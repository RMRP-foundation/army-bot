from discord.ext import commands

from bot import Bot
from database import divisions
from ui.views.transfers import TransferApplyView
from utils.bottom_message import update_bottom_message as _update_bottom_message
from utils.permissions import is_service


async def update_bottom_message(bot: Bot, channel_id: int):
    target_division = None
    for division in divisions.divisions:
        if division.transfer_channel == channel_id:
            target_division = division
            break

    if not target_division:
        return

    await _update_bottom_message(bot, channel_id, TransferApplyView(target_division))


class Transfers(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.command(name="refresh_transfer")
    @is_service()
    async def update_command(self, ctx: commands.Context):
        await update_bottom_message(self.bot, ctx.channel.id)


async def setup(bot: Bot):
    await bot.add_cog(Transfers(bot))
