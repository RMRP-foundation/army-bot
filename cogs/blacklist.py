import datetime

import discord
from discord import app_commands
from discord.ext import commands

from bot import Bot
from core import config
from core.exceptions import ServiceError
from database.models import Blacklist as BlacklistModel
from database.models import User
from services.authorization import AuthorizationService
from services.notifications import notify_blacklisted, notify_unblacklisted
from utils.helpers import build_mentions, safe_respond
from utils.permissions import is_officer, is_higher_rank
from utils.user_data import format_static

channel_id = config.CHANNELS["blacklist"]


def can_manage_blacklist(initiator: User, target: User) -> bool:
    """Проверяет права инициатора на управление ЧС (Капитан+, звание выше целевого бойца)."""
    if not is_officer(initiator):
        return False
    if target.rank is not None and not is_higher_rank(initiator, target):
        return False
    return True


class Blacklist(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @app_commands.command(
        name="blacklist", description="Добавить военнослужащего в общий черный список"
    )
    @app_commands.rename(
        user="военнослужащий", days="дни", reason="причина", evidence="доказательства"
    )
    @app_commands.describe(
        user="Военнослужащий для добавления в черный список",
        days="Количество дней в черном списке (-1 для бессрочного)",
        reason="Причина добавления в черный список",
        evidence="Доказательства (ссылки на скриншоты, сообщения и т.д.)",
    )
    async def blacklist(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        days: app_commands.Range[int, -1, 3650],
        reason: str,
        evidence: str,
    ):
        try:
            initiator = await AuthorizationService.require_active_soldier(interaction)
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return

        db_user = await User.get_by_discord_id(user.id)
        if not db_user:
            await safe_respond(interaction, f"Пользователь {user.mention} не найден в базе данных.")
            return

        if not can_manage_blacklist(initiator, db_user):
            await safe_respond(
                interaction,
                "❌ У вас нет прав для добавления этого пользователя в черный список (требуется Капитан+ и звание выше цели).",
            )
            return

        await safe_respond(interaction, f"Гражданин {user.mention} был добавлен в черный список.")

        blacklist_entry = BlacklistModel(
            initiator=interaction.user.id,
            ends_at=discord.utils.utcnow() + datetime.timedelta(days=days) if days > 0 else None,
            reason=reason.strip(),
            evidence=evidence.strip(),
        )

        db_user.blacklist = blacklist_entry
        await db_user.save()

        duration = f"{days} дней" if days > 0 else "Бессрочно"
        await notify_blacklisted(self.bot, user.id, reason, duration)

        embed = discord.Embed(
            title="📋 Новое дело",
            color=discord.Color.dark_red(),
            timestamp=discord.utils.utcnow(),
        )
        author_name = f"Составитель: {initiator.full_name} | {format_static(initiator.static)}"
        embed.set_author(name=author_name)
        embed.add_field(
            name="Гражданин",
            value=f"{db_user.full_name} | {format_static(db_user.static)}",
            inline=False,
        )
        embed.add_field(name="Причина", value=reason[:1000], inline=False)
        embed.add_field(name="Доказательства", value=evidence[:1000], inline=False)

        if days > 0 and blacklist_entry.ends_at:
            ends_at_fmt = discord.utils.format_dt(blacklist_entry.ends_at, style="d")
            embed.add_field(name="Срок", value=f"{days} дней (до {ends_at_fmt})", inline=False)
        else:
            embed.add_field(name="Срок", value="Бессрочно", inline=False)

        mention_text = build_mentions([user.id, interaction.user.id], config.BLACKLIST_MENTIONS)
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(content=mention_text, embed=embed)

    @app_commands.command(
        name="unblacklist", description="Снять военнослужащего с черного списка"
    )
    @app_commands.rename(user="военнослужащий", reason="причина")
    @app_commands.describe(
        user="Военнослужащий для снятия с черного списка",
        reason="Причина снятия с черного списка",
    )
    async def unblacklist(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
    ):
        try:
            initiator = await AuthorizationService.require_active_soldier(interaction)
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return

        db_user = await User.get_by_discord_id(user.id)
        if not db_user:
            await safe_respond(interaction, f"Пользователь {user.mention} не найден в базе данных.")
            return

        if not db_user.blacklist:
            await safe_respond(interaction, f"Пользователь {user.mention} не находится в черном списке.")
            return

        if not can_manage_blacklist(initiator, db_user):
            await safe_respond(interaction, "❌ У вас нет прав для снятия этого пользователя с черного списка.")
            return

        await safe_respond(interaction, f"Гражданин {user.mention} был вынесен из черного списка.")

        old_blacklist = db_user.blacklist
        db_user.blacklist = None
        await db_user.save()

        await notify_unblacklisted(self.bot, user.id)

        embed = discord.Embed(
            title="Дело закрыто",
            color=discord.Color.dark_green(),
            timestamp=discord.utils.utcnow(),
        )
        author_name = f"Составитель: {initiator.full_name} | {format_static(initiator.static)}"
        embed.set_author(name=author_name)
        embed.add_field(
            name="Гражданин",
            value=f"{db_user.full_name} | {format_static(db_user.static)}",
            inline=False,
        )
        embed.add_field(name="Изначальная причина ЧС", value=old_blacklist.reason[:1000], inline=False)
        embed.add_field(name="Причина снятия", value=reason[:1000], inline=False)

        if old_blacklist.ends_at:
            embed.add_field(
                name="Оставалось",
                value=f"до {discord.utils.format_dt(old_blacklist.ends_at, style='d')}",
                inline=False,
            )
        else:
            embed.add_field(name="Срок был", value="Бессрочно", inline=False)

        mention_text = build_mentions([user.id, interaction.user.id])
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(content=mention_text, embed=embed)


async def setup(bot: Bot):
    await bot.add_cog(Blacklist(bot))