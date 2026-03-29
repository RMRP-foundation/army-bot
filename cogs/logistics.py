import datetime
from discord.ext import commands, tasks
import config
from bot import Bot
from database.models import LogisticsRequest
from ui.views.logistics import LogisticsApplyView
from utils.bottom_message import update_bottom_message as _update_bottom_message

# 03:00 MSK = 00:00 UTC
RESTART_TIME = datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc)
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
        """Автоматически отклоняет все PENDING заявки при рестарте."""
        pending = await LogisticsRequest.find(LogisticsRequest.status == "PENDING").to_list()
        if not pending: return

        channel = self.bot.get_channel(config.CHANNELS["logistics"])
        if not channel: return

        for req in pending:
            req.status = "EXPIRED"
            await req.save()
            if req.message_id:
                try:
                    msg = await channel.fetch_message(req.message_id)
                    await msg.edit(embed=await req.to_embed(), view=None)
                except:
                    continue

    @commands.command(name="refresh_logistics")
    @commands.has_permissions(administrator=True)
    async def update_command(self, ctx: commands.Context):
        if ctx.channel.id != channel_id:
            return
        await update_bottom_message(self.bot)

async def setup(bot: Bot):
    await bot.add_cog(Logistics(bot))