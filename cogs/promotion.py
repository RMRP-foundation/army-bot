from discord.ext import commands

from bot import Bot
from database import divisions
from ui.views.promotion import PromotionApplyView
from utils.bottom_message import update_bottom_message as _update_bottom_message


async def update_bottom_message(bot: Bot, channel_id: int):
    div = next(
        (d for d in divisions.divisions if d.promotion_channel == channel_id),
        None,
    )
    if not div:
        return
    await _update_bottom_message(bot, channel_id, PromotionApplyView())


class Promotion(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.command(name="refresh_promotion")
    @commands.is_owner()
    async def update_command(self, ctx: commands.Context):
        await update_bottom_message(self.bot, ctx.channel.id)


async def setup(bot: Bot):
    await bot.add_cog(Promotion(bot))