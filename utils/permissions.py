import discord
from discord.ext import commands

from core import config, rules, constants
from core.constants import Privilege
from database.models import User


def is_service_account(discord_id: int) -> bool:
    return discord_id in config.SERVICE_ACCOUNT_IDS

def is_service():
    async def predicate(ctx: commands.Context) -> bool:
        return is_service_account(ctx.author.id)
    return commands.check(predicate)

def is_higher_rank(officer: User, target: User) -> bool:
    """Возвращает True, если звание офицера строго выше звания целевого бойца."""
    if is_service_account(officer.discord_id):
        return True
    return (officer.rank or 0) > (target.rank or 0)

def check_rank_silent(user: User, min_rank: int) -> bool:
    """
    Проверяет ранг без отправки сообщения об ошибке.

    Args:
        user: Объект пользователя из базы данных
        min_rank: Минимальный индекс ранга

    Returns:
        True если пользователь имеет достаточный ранг
    """
    if user is not None and is_service_account(user.discord_id):
        return True
    return user is not None and (user.rank or 0) >= min_rank

def is_officer(user: User) -> bool:
    """Проверка на офицера (Капитан+)"""
    return check_rank_silent(user, constants.RankIndex.CAPTAIN)

def is_senior_officer(user: User) -> bool:
    """Проверка на старшего офицера (Майор+)"""
    return check_rank_silent(user, constants.RankIndex.MAJOR)

def is_high_command(user: User) -> bool:
    """Проверка на высшее командование (Полковник+)"""
    return check_rank_silent(user, constants.RankIndex.COLONEL)

def is_general(user: User) -> bool:
    """Проверка на генерала (Генерал-майор+)"""
    return check_rank_silent(user, constants.RankIndex.MAJOR_GENERAL)


def has_penalty_roles(member: discord.Member) -> bool:
    """Проверяет наличие ролей предупреждений/выговоров (PENALTY_ROLES)."""
    if not member:
        return False
    return any(r.id in config.PENALTY_ROLES for r in member.roles)

def is_under_investigation(member: discord.Member) -> bool:
    """Проверяет роль 'Ведется расследование'."""
    if not member:
        return False
    return any(r.id == config.INVESTIGATION_ROLE for r in member.roles)

def has_disciplinary_restrictions(member: discord.Member, check_investigation: bool = True) -> bool:
    """Общая проверка: выговоры + (опционально) расследование."""
    if has_penalty_roles(member):
        return True
    return check_investigation and is_under_investigation(member)

def can_assign_position(
    editor_discord_id: int,
    editor_division_id: int,
    editor_privilege: Privilege | None,
    target_division_id: int,
    target_privilege: Privilege,
) -> bool:
    if is_service_account(editor_discord_id):
        return True

    if editor_privilege is None:
        return False

    if editor_division_id == target_division_id:
        return editor_privilege.value > target_privilege.value

    ceiling = rules.DIVISION_EXTERNAL_GRANT_CEILING.get(editor_division_id)
    if ceiling is None:
        return False

    return target_privilege.value <= ceiling.value
