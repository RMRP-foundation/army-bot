import logging
from typing import TYPE_CHECKING

import discord

from core import config
from core.config import EXCLUDED_ROLES, RoleId
from core.exceptions import ServiceError
from database import divisions
from database.models import User
from utils.roles import to_division, to_position, to_rank
from utils.user_data import format_static, set_name_if_changed, format_rank

if TYPE_CHECKING:
    from bot import Bot

logger = logging.getLogger(__name__)


class MemberService:
    """Сервис для управления и синхронизации участников в Discord."""

    @staticmethod
    def _build_dismissed_nickname(user_db: User) -> str:
        """Формирует корректный никнейм для уволенного сотрудника (до 32 символов)."""
        prefix = "Уволен | "
        nick_full = user_db.full_name
        nick_short = user_db.short_name

        if nick_full and len(prefix + nick_full) <= 32:
            return prefix + nick_full
        if nick_short and len(prefix + nick_short) <= 32:
            return prefix + nick_short
        return (prefix + (nick_full or nick_short or "Неизвестный"))[:32]

    @staticmethod
    def _build_active_nickname(
        member: discord.Member, user_db: User, original_nick: str | None = None
    ) -> str:
        """Формирует актуальный никнейм для действующего военнослужащего."""
        div = divisions.get_division(user_db.division) if user_db.division else None

        # Для подразделения ССО используется оригинальный ник (позывной)
        if div and div.abbreviation == "ССО":
            if user_db.leave_status:
                base_name = original_nick or member.display_name
                return f"{user_db.leave_status} | {base_name}"[:32]
            return (original_nick or member.display_name)[:32]

        return user_db.discord_nick[:32]

    @classmethod
    async def sync_member_discord(
        cls,
        member: discord.Member,
        user_db: User,
        reason: str | None = None,
        original_nick: str | None = None,
    ) -> bool:
        """Синхронизирует никнейм и роли пользователя в Discord в соответствии с БД.

        Args:
            member: Объект участника Discord.
            user_db: Объект пользователя из базы данных.
            reason: Причина изменения в Discord Audit Log.
            original_nick: Исходный никнейм (применяется для ССО и завершения отпусков).

        Returns:
            bool: True, если синхронизация прошла успешно (или изменений не требовалось),
                  False при ошибках прав доступа или сбоях API.
        """
        if not isinstance(member, discord.Member):
            return False

        try:
            current_roles = member.roles

            if user_db.rank is None:
                # 1. Уволен: оставляем только базовые и исключенные роли
                excluded = set(EXCLUDED_ROLES)
                target_roles = [
                    role
                    for role in current_roles
                    if role.is_default()
                    or role.id in excluded
                    or not role.is_assignable()
                ]
                target_nick = cls._build_dismissed_nickname(user_db)
            else:
                # 2. На службе: пересчитываем роли подразделения, звания и должности
                roles_div = to_division(current_roles, user_db.division)
                roles_rank = to_rank(roles_div, user_db.rank)
                target_roles = to_position(
                    roles_rank, user_db.division, user_db.position
                )

                # Синхронизация ролей отпуска
                leave_roles = {
                    RoleId.IC_LEAVE.value,
                    RoleId.OOC_LEAVE.value,
                }
                target_roles = [r for r in target_roles if r.id not in leave_roles]

                if user_db.leave_status == "IC":
                    if role_ic := member.guild.get_role(RoleId.IC_LEAVE.value):
                        target_roles.append(role_ic)
                elif user_db.leave_status == "OOC":
                    if role_ooc := member.guild.get_role(RoleId.OOC_LEAVE.value):
                        target_roles.append(role_ooc)

                target_nick = cls._build_active_nickname(
                    member, user_db, original_nick
                )

            roles_changed = set(r.id for r in target_roles) != set(
                r.id for r in current_roles
            )
            nick_changed = member.display_name != target_nick

            if not roles_changed and not nick_changed:
                return True

            edit_kwargs = {"reason": reason or "Синхронизация профиля военнослужащего"}
            if roles_changed:
                edit_kwargs["roles"] = target_roles
            if nick_changed:
                edit_kwargs["nick"] = target_nick

            await member.edit(**edit_kwargs)
            return True

        except discord.Forbidden:
            logger.warning(
                f"Missing permissions to sync member {member.id} ({member.display_name})."
            )
            return False
        except discord.HTTPException as error:
            logger.error(
                f"HTTP error syncing member {member.id}: {error}", exc_info=True
            )
            return False
        except Exception as error:
            logger.error(
                f"Unexpected error syncing member {member.id}: {error}",
                exc_info=True,
            )
            return False


    @staticmethod
    async def _send_profile_log(
        interaction: discord.Interaction, user: User, action_title: str, value_name: str, value_str: str
    ) -> None:
        channel = interaction.client.get_channel(config.CHANNELS["static_log"])
        if not channel:
            return

        embed = discord.Embed(
            title=f"Самостоятельный ввод {action_title}",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Пользователь",
            value=f"{interaction.user.mention} ({interaction.user.display_name})",
            inline=False,
        )
        embed.add_field(name="Имя в системе", value=user.full_name or "Не указано", inline=False)
        embed.add_field(name="Звание", value=format_rank(user.rank), inline=False)
        embed.add_field(name=value_name, value=value_str, inline=False)
        embed.set_footer(text="Проверьте корректность данных")

        await channel.send(
            content="-# Требуется проверка. При несовпадении — измените через команду редактирования.",
            embed=embed,
        )

    @classmethod
    async def update_self_static(cls, interaction: discord.Interaction, static_id: int) -> None:
        user = await User.get_by_discord_id(interaction.user.id)
        if not user:
            raise ServiceError("❌ Пользователь не найден в базе данных.")

        user.static = static_id
        await user.save()

        await cls._send_profile_log(
            interaction=interaction,
            user=user,
            action_title="статика",
            value_name="Введенный статик",
            value_str=f"`{format_static(static_id)}`",
        )

    @classmethod
    async def update_self_name(cls, interaction: discord.Interaction, full_name: str) -> None:
        user = await User.get_by_discord_id(interaction.user.id)
        if not user:
            raise ServiceError("❌ Пользователь не найден в базе данных.")

        if set_name_if_changed(user, full_name):
            await user.save()

        await MemberService.sync_member_discord(
            member=interaction.user,
            user_db=user,
            reason="Самостоятельный ввод имени",
        )

        await cls._send_profile_log(
            interaction=interaction,
            user=user,
            action_title="имени",
            value_name="Введенное имя",
            value_str=f"`{user.full_name}`",
        )