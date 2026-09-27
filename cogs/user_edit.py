import discord
from discord import app_commands
from discord.ext import commands

from bot import Bot
from core import config
from core.config import RANK_EMOJIS, RankIndex
from core.exceptions import ServiceError
from database import divisions
from database.models import User
from error_handling import on_tree_error
from services.audit import AuditAction, audit_logger
from services.authorization import AuthorizationService
from services.member import MemberService
from services.notifications import notify_blacklisted, notify_demoted, notify_dismissed, notify_position_changed, notify_promoted
from utils.dismissal_logic import check_and_apply_penalty, cleanup_user_leaves
from utils.helpers import random_loading_message, safe_respond
from utils.permissions import can_assign_position, has_disciplinary_restrictions, is_higher_rank, is_officer, is_service_account
from utils.user_data import format_rank, format_static, parse_name, parse_static, clean_name, clean_static, \
    set_name_if_changed


class UserEdit(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

        self.edit_user = app_commands.ContextMenu(name="Отредактировать", callback=self.edit_user_callback)
        self.bot.tree.add_command(self.edit_user)

        self.fast_promotion = app_commands.ContextMenu(name="Повысить (+1 зв.)", callback=self.fast_promotion_callback)
        self.bot.tree.add_command(self.fast_promotion)

        self.dismiss_user = app_commands.ContextMenu(name="Уволить", callback=self.ask_dismiss_user_callback)
        self.bot.tree.add_command(self.dismiss_user)

        self.edit_user.error(on_tree_error)
        self.fast_promotion.error(on_tree_error)
        self.dismiss_user.error(on_tree_error)

    async def _check_permissions(self, interaction: discord.Interaction, target_user_db: User) -> User | None:
        """Проверяет права инициатора на управление кадрами и субординацию."""
        try:
            editor_db = await AuthorizationService.require_active_soldier(interaction)
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return None

        if not is_officer(editor_db):
            await safe_respond(interaction, f"❌ Доступ к управлению кадрами разрешен со звания {format_rank(RankIndex.CAPTAIN)}.")
            return None

        if not is_higher_rank(editor_db, target_user_db):
            await safe_respond(interaction, "❌ Вы не можете редактировать пользователей равного или старшего звания.")
            return None

        return editor_db

    async def _finalize_change(
        self, interaction: discord.Interaction, user: discord.Member, user_info: User, reason: str,
        audit_action: AuditAction | None = None, notification=None,
    ) -> None:
        """Сохраняет изменение, логирует его и уведомляет (если задано), синхронизирует Discord-профиль и перерисовывает панель.

        Единая точка выхода для всех select/modal callback'ов панели редактирования —
        избавляет от копирования одного и того же хвоста (save → audit → notify → sync → redraw) в каждом.
        """
        await user_info.save()

        if audit_action:
            await audit_logger.log_action(audit_action, interaction.user, user)
        if notification:
            await notification

        await MemberService.sync_member_discord(member=user, user_db=user_info, reason=reason)

        await interaction.edit_original_response(view=self.build_view(user, user_info))

    async def ask_dismiss_user_callback(self, interaction: discord.Interaction, user: discord.Member):
        user_info = await User.get_by_discord_id(user.id)
        if not user_info or user_info.rank is None:
            await safe_respond(interaction, "❌ Пользователь не состоит на службе.")
            return

        if not await self._check_permissions(interaction, user_info):
            return

        confirm_modal = discord.ui.Modal(title="Причина увольнения", timeout=120)
        reason_input = discord.ui.TextInput(label="Причина увольнения", style=discord.TextStyle.paragraph, max_length=1000, required=True)

        async def on_submit(modal_interaction: discord.Interaction):
            current_user_info = await User.get_by_discord_id(user.id)
            if not current_user_info or current_user_info.rank is None:
                await safe_respond(modal_interaction, "❌ Военнослужащий уже не состоит на службе.")
                return

            initiator_db = await self._check_permissions(modal_interaction, current_user_info)
            if not initiator_db:
                return

            await safe_respond(modal_interaction, random_loading_message())

            audit_msg = await audit_logger.log_action(
                AuditAction.DISMISSED, modal_interaction.user, user, additional_info={"Причина": reason_input.value},
            )
            penalty_applied = await check_and_apply_penalty(modal_interaction, current_user_info, initiator_db, audit_msg.jump_url)

            current_user_info.rank = None
            current_user_info.division = None
            current_user_info.position = None
            await current_user_info.save()

            await cleanup_user_leaves(modal_interaction.client, user.id)
            await MemberService.sync_member_discord(member=user, user_db=current_user_info, reason=f"Уволил {modal_interaction.user.display_name}")
            await notify_dismissed(modal_interaction.client, user.id, reason_input.value, by_report=False)
            if penalty_applied:
                await notify_blacklisted(modal_interaction.client, user.id, "Неустойка", "14 дней")

            await safe_respond(modal_interaction, f"✅ {user.mention} уволен.")

        confirm_modal.add_item(reason_input)
        rank_name = config.RANKS[user_info.rank] if user_info.rank is not None else "Не указано"
        confirm_modal.add_item(discord.ui.TextDisplay(f"-# Вы собираетесь уволить {user.display_name} со звания {rank_name}"))
        confirm_modal.on_submit = on_submit
        await interaction.response.send_modal(confirm_modal)

    async def fast_promotion_callback(self, interaction: discord.Interaction, user: discord.Member):
        user_info = await User.get_by_discord_id(user.id)
        if not user_info or user_info.rank is None:
            await safe_respond(interaction, "❌ Пользователь не состоит на службе.")
            return

        editor = await self._check_permissions(interaction, user_info)
        if not editor:
            return

        if has_disciplinary_restrictions(user):
            await safe_respond(interaction, "❌ Вы не можете повысить военнослужащего с активными дисциплинарными взысканиями или под расследованием.")
            return

        if user_info.rank >= len(config.RANKS) - 1:
            await safe_respond(interaction, f"⚠️ {user.mention} уже имеет максимальное звание!")
            return

        target_rank = user_info.rank + 1
        if not is_service_account(editor.discord_id) and (editor.rank or 0) <= target_rank:
            await safe_respond(interaction, "❌ Вы не можете присвоить звание выше или равное вашему.")
            return

        user_info.rank = target_rank
        rank_name = config.RANKS[user_info.rank]
        await user_info.save()

        await MemberService.sync_member_discord(member=user, user_db=user_info, reason=f"Повысил {interaction.user.display_name}")
        await audit_logger.log_action(AuditAction.PROMOTED, interaction.user, user)
        await notify_promoted(interaction.client, user.id, rank_name)
        await safe_respond(interaction, f"📈 {user.mention} повышен до звания **{rank_name}**.")

    async def edit_user_callback(self, interaction: discord.Interaction, user: discord.Member):
        user_info = await User.get_by_discord_id(user.id)
        if not user_info:
            await safe_respond(interaction, "❌ Пользователь не найден в базе данных.")
            return

        if not await self._check_permissions(interaction, user_info):
            return

        await interaction.response.send_message(view=self.build_view(user, user_info), ephemeral=True)

    def _build_personal_data_section(self, user: discord.Member, user_info: User) -> discord.ui.Section:
        async def edit_data_callback(interaction: discord.Interaction):
            modal = discord.ui.Modal(title="Личные данные")
            name_input = discord.ui.TextInput(label="Имя Фамилия", default=user_info.full_name or "", max_length=50, required=False)
            static_input = discord.ui.TextInput(label="Статик", default=str(user_info.static) if user_info.static else "", max_length=10, required=False)
            modal.add_item(name_input)
            modal.add_item(static_input)

            async def data_submit(modal_inter: discord.Interaction):
                await modal_inter.response.defer(ephemeral=True)

                old_full_name, old_static = user_info.full_name, user_info.static

                try:
                    if name_input.value:
                        clean = clean_name(name_input.value)
                        set_name_if_changed(user_info, clean)

                    if static_input.value:
                        user_info.static = clean_static(static_input.value)

                except ServiceError as error:
                    await safe_respond(modal_inter, error.message)
                    return

                changed = user_info.full_name != old_full_name or user_info.static != old_static
                await self._finalize_change(
                    modal_inter, user, user_info, reason=f"Изменение данных {modal_inter.user.display_name}",
                    audit_action=AuditAction.NICKNAME_CHANGED if changed else None,
                )

            modal.on_submit = data_submit
            await interaction.response.send_modal(modal)

        change_user_data = discord.ui.Button(emoji="📝")
        change_user_data.callback = edit_data_callback

        section = discord.ui.Section(accessory=change_user_data)
        section.add_item(discord.ui.TextDisplay("### Личные данные"))
        section.add_item(discord.ui.TextDisplay(f"Имя Фамилия: **{user_info.full_name or 'Не установлено'}**"))
        section.add_item(discord.ui.TextDisplay(f"Статик: **`{format_static(user_info.static) or 'Не установлен'}`**"))
        return section

    def _build_rank_row(self, user: discord.Member, user_info: User) -> discord.ui.ActionRow:
        select_rank = discord.ui.Select(
            placeholder="Изменить звание",
            options=[
                discord.SelectOption(default=index == user_info.rank, emoji=RANK_EMOJIS[index], label=name, value=str(index))
                for index, name in enumerate(config.RANKS)
            ],
        )

        async def rank_callback(interaction: discord.Interaction):
            await interaction.response.defer()

            editor = await self._check_permissions(interaction, user_info)
            if not editor:
                return

            new_rank = int(select_rank.values[0])
            old_rank = user_info.rank if user_info.rank is not None else -1

            if new_rank > old_rank and has_disciplinary_restrictions(user):
                await safe_respond(interaction, "❌ Вы не можете повысить военнослужащего с активными дисциплинарными взысканиями или под расследованием.")
                await interaction.edit_original_response(view=self.build_view(user, user_info))
                return

            if not is_service_account(editor.discord_id) and (editor.rank or 0) <= new_rank:
                await safe_respond(interaction, "❌ Вы не можете присвоить звание выше или равное вашему.")
                await interaction.edit_original_response(view=self.build_view(user, user_info))
                return

            user_info.rank = new_rank

            audit_action, notification = None, None
            if old_rank != new_rank:
                if old_rank < new_rank:
                    audit_action = AuditAction.PROMOTED
                    notification = notify_promoted(interaction.client, user.id, config.RANKS[new_rank])
                else:
                    audit_action = AuditAction.DEMOTED
                    notification = notify_demoted(interaction.client, user.id, config.RANKS[new_rank])

            await self._finalize_change(
                interaction, user, user_info, reason=f"Изменение звания {interaction.user.display_name}",
                audit_action=audit_action, notification=notification,
            )

        select_rank.callback = rank_callback
        row = discord.ui.ActionRow()
        row.add_item(select_rank)
        return row

    def _build_division_row(self, user: discord.Member, user_info: User) -> discord.ui.ActionRow:
        select_division = discord.ui.Select(
            placeholder="Не в подразделении...",
            options=[
                discord.SelectOption(default=(user_info.division == div.division_id), emoji=div.emoji, label=div.name, value=str(div.division_id))
                for div in divisions.divisions
            ],
        )

        async def division_callback(interaction: discord.Interaction):
            await interaction.response.defer()

            editor = await self._check_permissions(interaction, user_info)
            if not editor:
                return

            new_div = int(select_division.values[0])
            old_div = user_info.division
            if old_div == new_div:
                await interaction.edit_original_response(content=None, view=self.build_view(user, user_info))
                await safe_respond(interaction, "Изменений не было.")
                return

            user_info.division = new_div
            user_info.position = None
            audit_action = AuditAction.DIVISION_ASSIGNED if old_div is None else AuditAction.DIVISION_CHANGED

            await self._finalize_change(
                interaction, user, user_info, reason=f"Смена подразделения {interaction.user.display_name}", audit_action=audit_action,
            )

        select_division.callback = division_callback
        row = discord.ui.ActionRow()
        row.add_item(select_division)
        return row

    def _build_position_block(self, user: discord.Member, user_info: User) -> tuple[discord.ui.Section, discord.ui.ActionRow | None]:
        async def manual_position_callback(interaction: discord.Interaction):
            change_modal = discord.ui.Modal(title="Изменение должности")
            position_input = discord.ui.TextInput(
                label="Должность", placeholder="Введите новую должность", style=discord.TextStyle.short,
                required=True, max_length=100, default=user_info.position or "",
            )
            change_modal.add_item(position_input)

            async def modal_callback(modal_interaction: discord.Interaction):
                await safe_respond(modal_interaction, random_loading_message())

                old_position = user_info.position
                user_info.position = position_input.value.strip()
                await self._finalize_change(
                    modal_interaction, user, user_info, reason=f"Смена должности {modal_interaction.user.display_name}",
                    audit_action=AuditAction.POSITION_CHANGED if old_position != user_info.position else None,
                    notification=notify_position_changed(modal_interaction.client, user.id, user_info.position) if old_position != user_info.position else None,
                )

            change_modal.on_submit = modal_callback
            await interaction.response.send_modal(change_modal)

        change_position = discord.ui.Button(emoji="📝")
        change_position.callback = manual_position_callback

        section = discord.ui.Section(accessory=change_position)
        section.add_item(discord.ui.TextDisplay("### Должность"))

        div_obj = divisions.get_division(user_info.division) if user_info.division else None
        if not (div_obj and div_obj.positions):
            section.add_item(discord.ui.TextDisplay(f"_{user_info.position or 'Не установлена'}_"))
            return section, None

        options = [
            discord.SelectOption(default=(user_info.position == pos.name), label=pos.name, value=pos.name)
            for pos in div_obj.positions
        ]
        if user_info.position and not any(opt.value == user_info.position for opt in options):
            options.insert(0, discord.SelectOption(label=user_info.position, value=user_info.position, default=True))

        position_select = discord.ui.Select(placeholder="Выберите должность", options=options[:25])

        async def position_select_callback(interaction: discord.Interaction):
            await interaction.response.defer()

            editor = await self._check_permissions(interaction, user_info)
            if not editor:
                return

            new_position_name = position_select.values[0]
            target_pos_obj = next((p for p in (div_obj.positions or []) if p.name == new_position_name), None)
            if not target_pos_obj:
                await safe_respond(interaction, "❌ Не удалось определить привилегию должности.")
                return

            editor_pos_obj = None
            if editor.division and editor.position:
                editor_div_obj = divisions.get_division(editor.division)
                if editor_div_obj:
                    editor_pos_obj = next((p for p in (editor_div_obj.positions or []) if p.name == editor.position),
                                          None)

            if not can_assign_position(
                    editor_discord_id=editor.discord_id,
                    editor_division_id=editor.division,
                    editor_privilege=editor_pos_obj.privilege if editor_pos_obj else None,
                    target_division_id=div_obj.division_id,
                    target_privilege=target_pos_obj.privilege,
            ):
                await safe_respond(interaction, "❌ Недостаточно привилегий для данного назначения.")
                await interaction.edit_original_response(view=self.build_view(user, user_info))
                return

            old_position = user_info.position
            user_info.position = new_position_name
            await self._finalize_change(
                interaction, user, user_info, reason=f"Смена должности {interaction.user.display_name}",
                audit_action=AuditAction.POSITION_CHANGED if old_position != user_info.position else None,
                notification=notify_position_changed(interaction.client, user.id,
                                                     user_info.position) if old_position != user_info.position else None,
            )

        position_select.callback = position_select_callback
        row = discord.ui.ActionRow()
        row.add_item(position_select)
        return section, row

    def build_view(self, user: discord.Member, user_info: User) -> discord.ui.LayoutView:
        """Собирает панель редактирования кадровой информации из независимых секций."""
        layout = discord.ui.LayoutView(timeout=300)
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(f"## Редактирование информации {user.mention}"))
        container.add_item(discord.ui.Separator())

        container.add_item(self._build_personal_data_section(user, user_info))
        container.add_item(discord.ui.Separator())

        container.add_item(discord.ui.TextDisplay("### Звание"))
        container.add_item(self._build_rank_row(user, user_info))
        container.add_item(discord.ui.Separator())

        container.add_item(discord.ui.TextDisplay("### Подразделение"))
        container.add_item(self._build_division_row(user, user_info))
        container.add_item(discord.ui.Separator())

        position_section, position_row = self._build_position_block(user, user_info)
        container.add_item(position_section)
        if position_row:
            container.add_item(position_row)

        layout.add_item(container)
        return layout


async def setup(bot: Bot):
    await bot.add_cog(UserEdit(bot))