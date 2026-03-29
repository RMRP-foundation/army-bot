import datetime
import logging
from pathlib import Path

import discord
from discord.ext import commands, tasks

import config
from bot import Bot

logger = logging.getLogger(__name__)

# 21:00 по МСК
DAILY_TIME = datetime.time(hour=18, minute=0, tzinfo=datetime.timezone.utc)
PICS_PATH = Path("./daily_pics").resolve()


class DailyAnnounce(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.daily_task.start()

    def cog_unload(self):
        self.daily_task.cancel()

    @tasks.loop(time=DAILY_TIME)
    async def daily_task(self):
        logger.info("Daily task started")
        if not PICS_PATH.is_dir():
            logger.warning(f"Directory '{PICS_PATH}' does not exist.")
            return

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            logger.error(f"Guild with ID {config.GUILD_ID} not found.")
            return

        for pic in PICS_PATH.iterdir():
            try:
                channel_id = int(pic.stem)
            except ValueError:
                logger.warning(f"Invalid file name '{pic.name}', skipping.")
                continue

            file_path = pic.resolve()
            if not file_path.is_relative_to(PICS_PATH):
                logger.warning(f"Path traversal detected for '{pic.name}', skipping.")
                continue

            channel = guild.get_channel(channel_id)

            if channel is None:
                logger.warning(f"Channel with ID {channel_id} not found in guild.")
                continue

            try:
                await channel.send(
                    file=discord.File(fp=str(file_path), filename="daily_announce.png")
                )
            except discord.Forbidden:
                logger.warning(
                    f"Permission denied to send message in channel ID {channel_id}."
                )
            except Exception as e:
                logger.error(f"Failed to send message in channel ID {channel_id}: {e}")

    @daily_task.before_loop
    async def before_daily_task(self):
        await self.bot.wait_until_ready()


async def setup(bot: Bot):
    await bot.add_cog(DailyAnnounce(bot))
