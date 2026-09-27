import discord

from database.models import DismissalRequest, DismissalType
from database import divisions
from database.status import get_status_display
from utils.user_data import format_static, format_rank


def dismissal_embed(request: DismissalRequest) -> discord.Embed:
    status_display = get_status_display(request.status)

    title_text = "Авторапорт" if request.type == DismissalType.AUTO else "Рапорт"

    embed = discord.Embed(
        title=f"{status_display.emoji} {title_text} #{request.id} — {status_display.text}",
        color=status_display.color,
        timestamp=request.created_at,
    )

    embed.add_field(name="Имя Фамилия", value=request.full_name, inline=True)
    embed.add_field(name="Номер паспорта", value=format_static(request.static), inline=True)
    embed.add_field(name="Звание", value=format_rank(request.rank_index), inline=False)

    div_name = divisions.get_division_name(request.division_id) if request.division_id else "Нет"
    embed.add_field(name="Подразделение", value=div_name, inline=True)

    if request.position:
        embed.add_field(name="Должность", value=request.position, inline=True)
    embed.add_field(name="Причина", value=request.type.value, inline=False)

    if request.status == "REJECTED" and request.reject_reason:
        embed.add_field(name="Причина отказа", value=request.reject_reason, inline=False)

    if request.reviewer_id:
        reviewed = f" в {discord.utils.format_dt(request.reviewed_at)}" if request.reviewed_at else ""
        embed.add_field(name="Рассмотрел", value=f"<@{request.reviewer_id}>{reviewed}", inline=False)

    return embed