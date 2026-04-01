from discord.ext import commands

import config
from bot import Bot
from ui.views.supplies_audit import SupplyAuditView
from utils.bottom_message import update_bottom_message as _update_bottom_message
from utils.permissions import has_update_permission

channel_id = config.CHANNELS["storage_audit"]


async def update_bottom_message(bot: Bot):
    await _update_bottom_message(bot, channel_id, SupplyAuditView())


class SuppliesAudit(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.command(name="refresh_audit")
    @has_update_permission()
    async def update_command(self, ctx: commands.Context):
        if ctx.channel.id != channel_id:
            return
        await update_bottom_message(self.bot)


async def setup(bot: Bot):
    await bot.add_cog(SuppliesAudit(bot))
