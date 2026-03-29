import datetime
import logging
import re

import discord

import config
from config import INVESTIGATION_ROLE, PENALTY_ROLES, EXCLUDED_ROLES
from database.models import Blacklist, DismissalRequest, DismissalType, User
from ui.modals.dismissal import DismissalModal
from utils.audit import AuditAction, audit_logger
from utils.dismissal_logic import check_and_apply_penalty
from utils.notifications import notify_blacklisted, notify_dismissed
from utils.user_data import format_game_id, get_initiator

logger = logging.getLogger(__name__)

closed_requests = set()


async def open_modal(interaction: discord.Interaction, d_type: DismissalType):
    user_db = await get_initiator(interaction)
    if not user_db:
        await interaction.response.send_message(
            "❌ Вас нет в базе данных.", ephemeral=True
        )
        return

    user_roles = [role.id for role in interaction.user.roles]
    if (
        any(rid in PENALTY_ROLES for rid in user_roles)
        or INVESTIGATION_ROLE in user_roles
    ):
        await interaction.response.send_message(
            "❌ Вы не можете подать рапорт на увольнение, "
            "пока у вас есть активные дисциплинарные взыскания "
            "или в отношении вас ведётся расследование.",
            ephemeral=True,
        )
        return

    full_name = user_db.full_name or ""
    await interaction.response.send_modal(DismissalModal(d_type, full_name))


async def psj_button_callback(interaction: discord.Interaction):
    user = await get_initiator(interaction)
    if not user or user.rank is None:
        await interaction.response.send_message(
            "❌ Вы не состоите на службе и не можете подать рапорт на ПСЖ.",
            ephemeral=True,
        )
        return
    await open_modal(interaction, DismissalType.PJS)


class DismissalApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay("# Рапорт на увольнение"))
    container.add_item(
        discord.ui.TextDisplay(
            "### Подача рапорта\n"
            "Выберите тип увольнения, нажав соответствующую кнопку ниже.\n\n"
            "**Примечание:**\n"
            "- Если вы не отработали 5 дней во фракции, вы попадете в ЧС на 14 дней.\n"
            "- Заполняйте данные корректно, как в паспорте."
        )
    )

    container.add_item(discord.ui.Separator(visible=True))

    psj_button = discord.ui.Button(
        label="ПСЖ", style=discord.ButtonStyle.secondary, custom_id="dismissal_pjs"
    )

    psj_button.callback = psj_button_callback

    transfer_button = discord.ui.Button(
        label="Перевод",
        style=discord.ButtonStyle.primary,
        custom_id="dismissal_transfer",
    )
    transfer_button.callback = lambda interaction: open_modal(
        interaction, DismissalType.TRANSFER
    )

    action_row = discord.ui.ActionRow()
    action_row.add_item(psj_button)
    action_row.add_item(transfer_button)
    container.add_item(action_row)


class DismissalManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"dismiss_(?P<action>\w+):(?P<id>\d+)",
):
    def __init__(self, action: str, request_id: int):
        labels = {"approve": "Одобрить", "reject": "Отказать"}
        styles = {
            "approve": discord.ButtonStyle.success,
            "reject": discord.ButtonStyle.danger,
        }

        super().__init__(
            discord.ui.Button(
                label=labels.get(action, action),
                style=styles.get(action, discord.ButtonStyle.secondary),
                custom_id=f"dismiss_{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        officer = await get_initiator(interaction)
        if not officer or (officer.rank or 0) < config.CAPTAIN_RANK_INDEX:
            await interaction.response.send_message(
                "❌ Доступно со звания Капитан.", ephemeral=True
            )
            return

        req = await DismissalRequest.find_one(DismissalRequest.id == self.request_id)
        if not req or req.status != "PENDING" or self.request_id in closed_requests:
            await interaction.response.send_message(
                "❌ Заявка не найдена или уже обработана.", ephemeral=True
            )
            return
        closed_requests.add(self.request_id)

        if self.action == "reject":
            req.status = "REJECTED"
            req.reviewer_id = interaction.user.id
            req.reviewed_at = datetime.datetime.now()
            await req.save()

            embed = await req.to_embed(interaction.client)
            try:
                await interaction.response.edit_message(
                    content=f"<@{req.user_id}> {interaction.user.mention}",
                    embed=embed,
                    view=None,
                )
            except discord.NotFound:
                pass
            return

        if self.action == "approve":
            target_user_db = await User.find_one(User.discord_id == req.user_id)
            if not target_user_db:
                closed_requests.discard(self.request_id)
                await interaction.response.send_message(
                    "❌ Пользователь не найден в БД.", ephemeral=True
                )
                return

            if (officer.rank or 0) <= (target_user_db.rank or 0):
                closed_requests.discard(self.request_id)
                await interaction.response.send_message(
                    "❌ Вы не можете уволить этого пользователя, так как его "
                    "звание выше или равно вашему.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                "✅ Выполняются действия...", ephemeral=True
            )

            target_user_db.first_name, target_user_db.last_name = req.full_name.split(
                " ", 1
            )

            audit_msg = await audit_logger.log_action(
                AuditAction.DISMISSED,
                interaction.user,
                req.user_id,
                additional_info={
                    "Причина": f"[Рапорт на увольнение #{req.id}]"
                    f"({interaction.message.jump_url})"
                },
            )

            penalty_applied = await check_and_apply_penalty(
                interaction, target_user_db, officer, audit_msg.jump_url
            )

            target_user_db.rank = None
            target_user_db.division = None
            target_user_db.position = None
            await target_user_db.save()

            target_member = await interaction.client.getch_member(req.user_id)
            if target_member:
                try:
                    excluded = set(EXCLUDED_ROLES)
                    new_roles = [
                        role for role in target_member.roles
                        if role.is_default()
                        or role.id in excluded
                        or not role.is_assignable()
                    ]

                    prefix = "Уволен | "
                    nick_full = target_user_db.full_name
                    nick_short = target_user_db.short_name
                    if nick_full and len(prefix + nick_full) <= 32:
                        new_nick = prefix + nick_full
                    elif nick_short and len(prefix + nick_short) <= 32:
                        new_nick = prefix + nick_short
                    else:
                        new_nick = prefix + (nick_full or nick_short or "Неизвестный")
                    await target_member.edit(
                        nick=new_nick[:32],
                        roles=new_roles,
                        reason=f"Увольнение по рапорту #{req.id}",
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "⚠️ Не удалось обновить роли/ник в Discord (нет прав).",
                        ephemeral=True,
                    )
                except Exception as e:
                    logger.error(f"Error processing dismissal discord actions: {e}")

            req.status = "APPROVED"
            req.reviewer_id = interaction.user.id
            req.reviewed_at = datetime.datetime.now()
            await req.save()

            # Уведомление в ЛС об увольнении
            await notify_dismissed(
                interaction.client, req.user_id, f"Увольнение по рапорту #{req.id}", by_report=True
            )

            embed = await req.to_embed(interaction.client)
            if penalty_applied:
                embed.set_footer(text="Автоматически выдан ЧС за неустойку.")
                await notify_blacklisted(interaction.client, req.user_id, "Неустойка", "14 дней")

            await interaction.message.edit(
                content=f"<@{req.user_id}> {interaction.user.mention}",
                embed=embed,
                view=None,
            )


class DismissalCancelButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dismiss:cancel:(?P<id>\d+)"
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Отменить",
                style=discord.ButtonStyle.grey,
                custom_id=f"dismiss:cancel:{request_id}",
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        req = await DismissalRequest.find_one(
            DismissalRequest.id == self.request_id,
            DismissalRequest.user_id == interaction.user.id,
        )
        if not req or req.status != "PENDING":
            await interaction.response.send_message(
                "❌ Заявка не найдена или уже обработана.", ephemeral=True
            )
            return

        req.status = "REJECTED"
        req.reviewer_id = interaction.user.id
        req.reviewed_at = datetime.datetime.now()
        await req.save()

        await interaction.response.send_message(
            content="✅ Ваш рапорт был отменен.", ephemeral=True
        )
        await interaction.message.delete()


class DismissalManagementView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(DismissalManagementButton("approve", request_id))
        self.add_item(DismissalManagementButton("reject", request_id))
        self.add_item(DismissalCancelButton(request_id))
