import asyncio
import datetime
import logging

import discord
from discord.ext import commands, tasks

from bot import Bot
from core import config
from database.models import LogisticsRequest
from ui.embeds.logistics import logistics_embed
from ui.views.logistics import LogisticsApplyView
from utils.bottom_message import update_bottom_message as _update_bottom_message
from utils.helpers import safe_edit_message
from utils.permissions import is_service

logger = logging.getLogger(__name__)

# 03:10 MSK = 00:10 UTC
RESTART_TIME = datetime.time(hour=0, minute=10, tzinfo=datetime.timezone.utc)
channel_id = config.CHANNELS["logistics"]


async def update_bottom_message(bot: Bot):
    await _update_bottom_message(bot, channel_id, LogisticsApplyView())


class Logistics(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.cleanup_task.start()

    def cog_unload(self):
        self.cleanup_task.cancel()

    @tasks.loop(time=RESTART_TIME)
    async def cleanup_task(self):
        """Автоматически отклоняет все PENDING заявки за предыдущие дни при рестарте."""
        now = discord.utils.utcnow()
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(hours=3)
        pending = await LogisticsRequest.find(
            LogisticsRequest.status == "PENDING",
            LogisticsRequest.created_at < cutoff,
        ).to_list()
        if not pending:
            return

        channel = self.bot.get_channel(config.CHANNELS["logistics"])
        if not channel:
            return

        for req in pending:
            req.status = "EXPIRED"
            await req.save()
            if req.message_id:
                await safe_edit_message(
                    channel.get_partial_message(req.message_id),
                    embed=logistics_embed(req),
                    view=None,
                )
            await asyncio.sleep(1)

    @commands.command(name="refresh_logistics")
    @is_service()
    async def update_command(self, ctx: commands.Context):
        if ctx.channel.id != channel_id:
            return
        await update_bottom_message(self.bot)


async def setup(bot: Bot):
    await bot.add_cog(Logistics(bot))