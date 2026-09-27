import datetime

import discord

from database import divisions
from database.models import User, LeaveRequest
from database.status import get_status_display
from utils.user_data import format_static, format_rank


def leave_embed(request: LeaveRequest, requester: User | None) -> discord.Embed:
    status_display = get_status_display(request.status)

    e = discord.Embed(
        title=f"{status_display.emoji} Заявление #{request.id} — {status_display.text}",
        color=status_display.color,
        timestamp=request.created_at,
    )

    div_name = (
        divisions.get_division_name(requester.division)
        if (requester and requester.division is not None)
        else "Нет"
    )

    e.add_field(name="Имя Фамилия", value=requester.full_name, inline=True)
    e.add_field(name="Статик", value=format_static(requester.static), inline=True)
    e.add_field(name="Звание", value=format_rank(requester.rank), inline=False)
    e.add_field(name="Подразделение", value=div_name, inline=False)

    e.add_field(name="Дата начала", value=discord.utils.format_dt(request.starts_at, "d"), inline=True)
    e.add_field(name="Дата выхода", value=discord.utils.format_dt(request.ends_at, "d"), inline=True)

    e.add_field(name="Причина", value=request.reason, inline=False)

    if request.reviewer_id and request.approved_at:
        e.add_field(
            name="Рассмотрел",
            value=(f"<@{request.reviewer_id}> "
                   f"{discord.utils.format_dt(request.approved_at, 'R')}"),
            inline=True
        )

    if request.annuller_id and request.annulled_at:
        e.add_field(
            name="Аннулировал",
            value=(
                f"<@{request.annuller_id}> "
                f"{discord.utils.format_dt(request.annulled_at, 'R')}"
            ),
            inline=False,
        )

    e.set_footer(text="Отправлено")
    return e