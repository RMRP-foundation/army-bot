import discord

from database import divisions
from database.models import TimeoffRequest, User
from database.status import get_status_display
from utils.user_data import format_static, format_rank


def timeoff_embed(request: TimeoffRequest, requester: User | None) -> discord.Embed:
    """Чистая синхронная функция сборки эмбеда заявления на отгул."""
    status_display = get_status_display(request.status)

    embed = discord.Embed(
        title=f"{status_display.emoji} Заявление #{request.id} — {status_display.text}",
        color=status_display.color,
        timestamp=request.sent_at,
    )

    full_name = requester.full_name if requester else request.data.full_name
    static_id = requester.static if (requester and requester.static) else request.data.static_id

    embed.add_field(name="Заявитель", value=full_name, inline=True)
    embed.add_field(name="Статик", value=format_static(static_id), inline=True)

    rank_value = format_rank(requester.rank) if requester else "Неизвестно"
    division_name = "Неизвестно"

    if requester and requester.division is not None:
        div = divisions.get_division(requester.division)
        if div:
            division_name = div.name

    embed.add_field(name="Звание", value=rank_value, inline=False)
    embed.add_field(name="Подразделение", value=division_name, inline=False)
    embed.add_field(name="Время", value=request.period or "Не указано", inline=False)

    embed.set_footer(text="Отправлено")
    return embed