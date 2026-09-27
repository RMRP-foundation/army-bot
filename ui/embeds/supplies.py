import discord

from database.models import SupplyRequest, User
from database.status import get_status_display
from utils.user_data import format_static


def supplies_embed(request: SupplyRequest, requester: User) -> discord.Embed:
    requester_name = requester.full_name if requester else f"<@{request.user_id}>"

    status_display = get_status_display(request.status)

    embed = discord.Embed(
        title=f"Заявка на склад #{request.id}", color=status_display.color, timestamp=request.created_at
    )
    embed.add_field(
        name="Запросил",
        value=f"{requester_name} ({format_static(requester.static)})",
        inline=False,
    )

    items_str = ""
    if request.items:
        for item, amount in request.items.items():
            items_str += f"• **{item}**: {amount} шт.\n"
    else:
        items_str = "Список пуст"

    embed.add_field(name="Список предметов", value=items_str, inline=False)

    if request.reviewer_id:
        embed.add_field(
            name="Рассмотрел", value=f"<@{request.reviewer_id}>", inline=False
        )

    return embed
