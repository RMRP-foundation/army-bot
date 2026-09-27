from discord.ext import commands

from database.models import User
from utils.permissions import is_service


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reset_division")
    @is_service()
    async def reset_division_cmd(self, ctx, discord_id: int):
        user = await User.get_by_discord_id(discord_id)
        if user:
            user.division = None
            user.position = None
            await user.save()
            await ctx.message.add_reaction("✅")

    @commands.command(name="resync")
    @is_service()
    async def resync_command(self, ctx: commands.Context):
        self.bot.tree.clear_commands(guild=ctx.guild)
        await self.bot.tree.sync(guild=ctx.guild)

        self.bot.tree.copy_global_to(guild=ctx.guild)
        await self.bot.tree.sync(guild=ctx.guild)

        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync()

        await ctx.message.add_reaction("✅")


async def setup(bot):
    await bot.add_cog(Admin(bot))