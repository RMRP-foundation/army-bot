import discord

from core import config
from database.models import RoleType, RoleRequest
from database.status import get_status_display
from utils.user_data import format_static


def _add_basic_role_fields(embed: discord.Embed, request: RoleRequest) -> None:
    """Добавляет поля для базовых ролей (ВС РФ, КМБ)."""
    if request.data:
        embed.add_field(name="Заявитель", value=request.data.full_name)
        embed.add_field(name="Статик", value=format_static(request.data.static_id))

def _add_extended_role_fields(embed: discord.Embed, request: RoleRequest) -> None:
    """Добавляет поля для расширенных ролей (Доступ к поставке, гос. сотрудник)."""
    ext = request.extended_data
    if not ext:
        return

    embed.add_field(name="Имя Фамилия", value=ext.full_name)
    embed.add_field(name="Статик", value=format_static(ext.static_id))
    embed.add_field(name="Фракция", value=ext.faction, inline=False)
    embed.add_field(name="Звание, должность", value=ext.rank_position, inline=False)

    if ext.purpose:
        embed.add_field(name="Цель и удостоверение", value=ext.purpose, inline=False)
    if ext.certificate_link:
        embed.add_field(name="Удостоверение", value=ext.certificate_link, inline=False)

def _get_role_type_name(role_type: RoleType) -> str:
    return config.ROLE_DISPLAY_NAMES.get(role_type, "Неизвестно")


def role_embed(request: RoleRequest) -> discord.Embed:
    status_display = get_status_display(request.status)

    role_name = _get_role_type_name(request.role_type)
    embed = discord.Embed(
        title=f"{status_display.emoji} Заявление на роль «{role_name}» {status_display.text}",
        colour=status_display.color,
        timestamp=request.sent_at,
    )

    if request.role_type in [RoleType.ARMY, RoleType.KMB] and request.data:
        _add_basic_role_fields(embed, request)
    elif request.extended_data:
        _add_extended_role_fields(embed, request)

    embed.set_footer(text="Отправлено")
    return embed