from discord.ext import commands

from utils.permissions import has_update_permission


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reset_processing")
    @has_update_permission()
    async def reset_processing(self, ctx):
        await self.bot.reset_processing()


async def setup(bot):
    await bot.add_cog(Admin(bot))