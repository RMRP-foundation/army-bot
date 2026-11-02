import asyncio
import logging
from dataclasses import dataclass

import discord

from core.config import GUILD_ID, SERVICE_ACCOUNT_IDS
from database import divisions
from database.models import User
from utils.roles import get_rank_from_roles

logger = logging.getLogger(__name__)

_PAUSE = 0.25

absent_ids: set[int] = set()


@dataclass(frozen=True)
class PresenceResult:
    checked: int
    absent: set[int]


async def init_user(member: discord.Member) -> tuple[User, bool]:
    """Инициализирует профиль бойца по ролям Discord при первом взаимодействии."""
    user = await User.get_by_discord_id(member.id)
    if user is not None and user.pre_inited:
        return user, False

    div, pos = divisions.get_user_data(member)
    rank = get_rank_from_roles(member.roles)

    if user is None:
        user = User(
            discord_id=member.id,
            division=div.division_id if div else None,
            position=pos.name if pos else None,
            rank=rank,
            pre_inited=True,
        )
        await user.create()
        return user, True

    user.division = div.division_id if div else None
    user.position = pos.name if pos else None
    user.rank = rank
    user.pre_inited = True
    await user.save()
    return user, True


async def check(bot: discord.Client) -> PresenceResult:
    """Опрашивает сервер по REST для выявления ушедших бойцов с рангом."""
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        raise RuntimeError(f"Гильдия {GUILD_ID} не найдена в кэше бота")

    users = await User.find({"rank": {"$ne": None}}).to_list()
    ids = [u.discord_id for u in users if u.discord_id not in SERVICE_ACCOUNT_IDS]

    absent: set[int] = set()
    unknown: set[int] = set()

    for discord_id in ids:
        try:
            await guild.fetch_member(discord_id)
        except discord.NotFound:
            absent.add(discord_id)
        except discord.HTTPException:
            unknown.add(discord_id)
        await asyncio.sleep(_PAUSE)

    absent |= unknown & absent_ids
    absent_ids.clear()
    absent_ids.update(absent)
    return PresenceResult(checked=len(ids), absent=set(absent))