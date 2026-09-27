from enum import StrEnum
from typing import TYPE_CHECKING

import discord

from core import config
from utils.helpers import build_mentions

if TYPE_CHECKING:
    from bot import Bot
from database import divisions
from database.models import User
from utils.user_data import format_static, format_rank


class AuditAction(StrEnum):
    INVITED = "Принятие на службу"
    DISMISSED = "Увольнение"
    PROMOTED = "Повышение в звании"
    DEMOTED = "Понижение в звании"
    POSITION_CHANGED = "Назначение на должность"
    POSITION_REMOVED = "Разжалование с должности"
    NICKNAME_CHANGED = "Изменение имени"
    DIVISION_ASSIGNED = "Вступление в подразделение"
    DIVISION_CHANGED = "Смена подразделения"
    DIVISION_LEFT = "Выход из подразделения"
    REINSTATEMENT = "Восстановление в звании"


action_colors = {
    AuditAction.INVITED: discord.Color.green(),
    AuditAction.DISMISSED: discord.Color.red(),
    AuditAction.PROMOTED: discord.Color.blue(),
    AuditAction.DEMOTED: discord.Color.dark_orange(),
    AuditAction.POSITION_CHANGED: discord.Color.purple(),
    AuditAction.POSITION_REMOVED: discord.Color.dark_grey(),
    AuditAction.NICKNAME_CHANGED: discord.Color.teal(),
    AuditAction.DIVISION_ASSIGNED: discord.Color.gold(),
    AuditAction.DIVISION_CHANGED: discord.Color.orange(),
    AuditAction.DIVISION_LEFT: discord.Color.light_grey(),
    AuditAction.REINSTATEMENT: discord.Color.blue(),
}

action_emojis = {
    AuditAction.INVITED: "✅",
    AuditAction.DISMISSED: "❌",
    AuditAction.PROMOTED: "⬆️",
    AuditAction.DEMOTED: "⬇️",
    AuditAction.POSITION_CHANGED: "📌",
    AuditAction.POSITION_REMOVED: "📍",
    AuditAction.NICKNAME_CHANGED: "✏️",
    AuditAction.DIVISION_ASSIGNED: "🏢",
    AuditAction.DIVISION_CHANGED: "🔄",
    AuditAction.DIVISION_LEFT: "🚪",
    AuditAction.REINSTATEMENT: "↩️",
}

channel_id = config.CHANNELS["audit"]


class AuditLogger:
    def __init__(self):
        self.bot: Bot | None = None

    def set_bot(self, bot: "Bot"):
        self.bot = bot

    async def log_action(
        self,
        action: AuditAction,
        initiator: discord.Member,
        target: discord.Member | discord.User | int | str,
        display_info: User | None = None,
        additional_info: dict[str, str] | None = None,
    ):
        mention_ids: list[int] = []
        if isinstance(target, (discord.Member, discord.User)):
            mention_ids.append(target.id)
        elif isinstance(target, int):
            mention_ids.append(target)
        mention_ids.append(initiator.id)

        mention_text = build_mentions(mention_ids)

        initiator_info = await User.find_one(User.discord_id == initiator.id)

        target_info = None
        if display_info is not None:
            target_info = display_info
        elif isinstance(target, (discord.Member, discord.User)):
            target_info = await User.find_one(User.discord_id == target.id)
        elif isinstance(target, int):
            target_info = await User.find_one(User.discord_id == target)

        embed = discord.Embed(
            title=f"{action_emojis[action]} {action.value}",
            colour=action_colors[action],
            timestamp=discord.utils.utcnow(),
        )
        author_name = (
            f"Составитель: {initiator_info.full_name if initiator_info else initiator.display_name} | "
            f"{format_static(initiator_info.static)}"
        )

        embed.set_author(name=author_name)
        embed.add_field(
            name="Военнослужащий",
            value=f"{target_info.full_name} `{format_static(target_info.static)}`"
            if target_info
            else str(target),
            inline=False,
        )
        if target_info and target_info.rank is not None:
            embed.add_field(
                name="Звание",
                value=format_rank(target_info.rank),
                inline=False,
            )
        if target_info and target_info.division is not None:
            embed.add_field(
                name="Подразделение",
                value=divisions.get_division_name(target_info.division),
                inline=False,
            )
        if target_info and target_info.position is not None:
            embed.add_field(name="Должность", value=target_info.position, inline=False)
        embed.set_footer(text="Записано в журнал аудита")

        for key, value in (additional_info or {}).items():
            embed.add_field(name=key, value=value, inline=False)

        return await self.bot.get_channel(channel_id).send(
            content=mention_text, embed=embed
        )


audit_logger = AuditLogger()
