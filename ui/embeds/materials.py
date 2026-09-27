import discord

from core import constants
from database.models import MaterialsReport, User
from utils.user_data import format_static, format_rank


def materials_embed(request: MaterialsReport, user: User) -> discord.Embed:
    price = f"{request.quantity * constants.MATERIAL_PRICE:,}".replace(',', '.')

    embed = discord.Embed(
        title="Отчет о продаже материалов",
        color=discord.Color.gold(),
        timestamp=request.created_at
    )
    embed.add_field(name="Имя Фамилия", value=request.full_name)
    embed.add_field(name="Статик", value=format_static(user.static))
    embed.add_field(name="Звание", value=format_rank(user.rank), inline=False)
    embed.add_field(name="Количество", value=f"{request.quantity:,} ед.".replace(",", "."), inline=True)
    embed.add_field(name="Сумма", value=f"{price} ₽", inline=True)
    embed.add_field(name="Доказательства", value=request.evidence, inline=False)
    return embed