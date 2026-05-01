from discord.ext import commands
from beanie import Document
import database.models
from database import divisions

from utils.permissions import has_update_permission

MODELS = {
    cls.__name__.lower().removesuffix("request"): cls
    for cls in Document.__subclasses__()
    if cls.__name__.endswith("Request")
}


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reset_processing")
    @has_update_permission()
    async def reset_processing_cmd(self, ctx, scope: str = None, request_id: int = None):
        if scope is None:
            await self.bot.reset_processing()
            return

        model = MODELS.get(scope.lower())
        if model is None or request_id is None:
            return

        if model is database.models.TransferRequest:
            div_with_positions = [d.division_id for d in divisions.divisions if d.positions]
            await model.get_pymongo_collection().update_one(
                {"_id": request_id, "status": "PROCESSING", "$or": [
                    {"old_reviewer_id": {"$ne": None}},
                    {"old_division_id": {"$nin": div_with_positions}},
                ]},
                {"$set": {"status": "NEW_DIVISION_REVIEW"}},
            )
            await model.get_pymongo_collection().update_one(
                {"_id": request_id, "status": "PROCESSING"},
                {"$set": {"status": "OLD_DIVISION_REVIEW"}},
            )
        else:
            await model.get_pymongo_collection().update_one(
                {"_id": request_id, "status": "PROCESSING"},
                {"$set": {"status": "PENDING"}},
            )


async def setup(bot):
    await bot.add_cog(Admin(bot))