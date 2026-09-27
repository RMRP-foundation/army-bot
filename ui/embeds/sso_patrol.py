import datetime

import discord

from database.models import SSOPatrolRequest, User
from database.status import get_status_display
from utils.user_data import format_static, format_rank


def sso_patrol_embed(request: SSOPatrolRequest, user: User, failed_question: str | None = None) -> discord.Embed:
    today = discord.utils.utcnow().astimezone(datetime.timezone(datetime.timedelta(hours=3))).strftime('%d.%m.%Y')

    if failed_question:
        title = "❌ Провал проверки знаний"
        color = discord.Color.red()
    else:
        title = f"Запрос формы ССО #{request.id}"

        status_display = get_status_display(request.status)
        color = status_display.color

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Имя Фамилия", value=request.full_name, inline=True)
    embed.add_field(name="Статик", value=format_static(user.static), inline=True)
    embed.add_field(name="Звание", value=format_rank(user.rank), inline=False)

    if failed_question:
        embed.add_field(name="Вопрос", value=failed_question, inline=False)
    else:
        embed.add_field(name="Причина", value=request.reason, inline=False)

    embed.set_footer(text=f"Дата: {today}")

    return embed