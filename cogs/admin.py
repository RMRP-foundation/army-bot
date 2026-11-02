import discord
from discord import app_commands
from discord.ext import commands

from bot import Bot
from ui.views.admin import AdminPanel
from utils.helpers import safe_respond
from utils.permissions import is_service_account


class Admin(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @app_commands.command(name="admin", description="Сервисная панель управления ботом")
    @app_commands.guild_only()
    async def admin(self, interaction: discord.Interaction):
        if not is_service_account(interaction.user.id):
            await safe_respond(interaction, "⛔ Нет доступа")
            return
        await interaction.response.send_message(view=AdminPanel(self.bot), ephemeral=True)


async def setup(bot: Bot):
    await bot.add_cog(Admin(bot))