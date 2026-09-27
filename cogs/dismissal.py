from discord.ext import commands

from core import config
from bot import Bot
from ui.views.dismissal import DismissalApplyView
from utils.bottom_message import update_bottom_message as _update_bottom_message
from utils.permissions import is_service

channel_id = config.CHANNELS["dismissal"]


async def update_bottom_message(bot: Bot):
    await _update_bottom_message(bot, channel_id, DismissalApplyView())


class Dismissal(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.command(name="refresh_dismissal")
    @is_service()
    async def update_command(self, ctx: commands.Context):
        if ctx.channel.id != channel_id:
            return
        await update_bottom_message(self.bot)


async def setup(bot: Bot):
    await bot.add_cog(Dismissal(bot))
