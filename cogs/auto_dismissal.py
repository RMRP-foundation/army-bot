import discord
from discord.ext import commands

from bot import Bot
from core.exceptions import ServiceError
from database.models import DismissalType, User
from services.dismissal import DismissalService


class AutoDismissal(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        user_db = await User.get_active_soldier(member.id)
        if user_db is None:
            return

        from ui.views.dismissal import DismissalManagementView

        try:
            await DismissalService.submit_dismissal(
                bot=self.bot,
                user=user_db,
                full_name=user_db.full_name or member.display_name,
                dismissal_type=DismissalType.AUTO,
                view_factory=DismissalManagementView
            )
        except ServiceError:
            pass


async def setup(bot: Bot):
    await bot.add_cog(AutoDismissal(bot))