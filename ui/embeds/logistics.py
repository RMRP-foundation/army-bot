import discord

from database.models import LogisticsRequest
from database.status import get_status_display


def logistics_embed(request: LogisticsRequest):
    status_display = get_status_display(request.status)

    embed = discord.Embed(
        title=f"Поставка: {request.supply_type.value}",
        color=status_display.color,
        timestamp=request.created_at
    )
    embed.add_field(name="Заявитель", value=request.nickname, inline=True)
    embed.add_field(name="Организация", value=request.faction, inline=True)

    return embed