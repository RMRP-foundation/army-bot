from discord.ext import commands
from beanie import Document
import database.models

from utils.permissions import has_update_permission
from database.models import User


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reset_processing")
    @has_update_permission()
    async def reset_processing_cmd(self, ctx, scope: str = None, request_id: int = None):
        if scope is None:
            await self.bot.reset_processing()
            await ctx.message.add_reaction("✅")
            return

        models = {
            cls.__name__.lower().removesuffix("request"): cls
            for cls in Document.__subclasses__()
            if cls.__name__.endswith("Request")
        }
        model = models.get(scope.lower())
        if model is None or request_id is None:
            await ctx.message.add_reaction("❌")
            return

        if model is database.models.TransferRequest:
            from database import divisions
            div_with_positions = [d.division_id for d in divisions.divisions if d.positions]
            r1 = await model.get_pymongo_collection().update_one(
                {"_id": request_id, "status": "PROCESSING", "$or": [
                    {"old_reviewer_id": {"$ne": None}},
                    {"old_division_id": {"$nin": div_with_positions}},
                ]},
                {"$set": {"status": "NEW_DIVISION_REVIEW"}},
            )
            r2 = await model.get_pymongo_collection().update_one(
                {"_id": request_id, "status": "PROCESSING"},
                {"$set": {"status": "OLD_DIVISION_REVIEW"}},
            )
            modified = r1.modified_count + r2.modified_count
        else:
            r = await model.get_pymongo_collection().update_one(
                {"_id": request_id, "status": "PROCESSING"},
                {"$set": {"status": "PENDING"}},
            )
            modified = r.modified_count

        await ctx.message.add_reaction("✅" if modified else "⚠️")

    @commands.command(name="reset_division")
    @has_update_permission()
    async def reset_division_cmd(self, ctx, discord_id: int):
        user = await User.find_one(database.models.User.discord_id == discord_id)
        if user:
            user.division = None
            user.position = None
            await user.save()
            await ctx.message.add_reaction("✅")


async def setup(bot):
    await bot.add_cog(Admin(bot))