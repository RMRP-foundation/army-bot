import logging
import os

import discord
import sentry_sdk
from discord.ext import commands

from core.config import GUILD_ID, SENTRY_DSN
from database import divisions
from database.connection import establish_db_connection
from error_handling import _custom_view_on_error, on_tree_error, on_command_error
from ui.views import load_buttons
from services.audit import audit_logger

logger = logging.getLogger(__name__)

sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0, send_default_pii=False)


discord.ui.View.on_error = _custom_view_on_error
discord.ui.LayoutView.on_error = _custom_view_on_error


class Bot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print("done")
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info("------")
        from cogs.leave import restore_leave_timers
        await restore_leave_timers(self)

    async def _load_cogs(self):
        for file in os.listdir("./cogs"):
            if file.endswith(".py"):
                await self.load_extension(f"cogs.{file[:-3]}")
                logger.info(f"Loaded cog {file[:-3]}")

    async def setup_hook(self):
        await establish_db_connection()
        await divisions.load()

        from core import config
        from database.models import Setting
        if setting := await Setting.find_one(Setting.key == "supply_info_link"):
            config.SUPPLY_INFO_LINK = setting.value

        audit_logger.set_bot(self)

        load_buttons(self)
        await self._load_cogs()

        self.tree.on_error = on_tree_error

        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info(f"Slash commands synced to guild {GUILD_ID}")

    async def getch_user(self, discord_id: int):
        if user := self.get_user(discord_id):
            return user
        return await self.fetch_user(discord_id)

    async def getch_member(self, discord_id: int):
        guild = self.get_guild(GUILD_ID)
        if member := guild.get_member(discord_id):
            return member

        try:
            return await guild.fetch_member(discord_id)
        except discord.NotFound:
            return None

    async def on_command_error(self, ctx, error):
        await on_command_error(ctx, error)
