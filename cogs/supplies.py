import discord
from discord.ext import commands

import config
from bot import Bot
from ui.views.supplies import SupplyCreateView
from utils.bottom_message import update_bottom_message as _update_bottom_message
from utils.permissions import has_update_permission

channel_id = config.CHANNELS["storage_requests"]


async def update_bottom_message(bot: Bot):
    description = (
        "Нажмите кнопку ниже, чтобы сформировать заявку "
        "на получение амуниции и материалов.\n\n"
        "**Требования:** Звание Старший Сержант и выше.\n"
        f"**Лимиты:** Соблюдайте установленные лимиты: {config.SUPPLY_INFO_LINK}"
    )
    embed = discord.Embed(
        title="📦 Склад",
        description=description,
        color=discord.Color.blue(),
    )
    await _update_bottom_message(bot, channel_id, SupplyCreateView(), embed)


class Supplies(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.command(name="refresh_supplies")
    @has_update_permission()
    async def update_command(self, ctx: commands.Context):
        if ctx.channel.id != channel_id:
            return
        await update_bottom_message(self.bot)


async def setup(bot: Bot):
    await bot.add_cog(Supplies(bot))
