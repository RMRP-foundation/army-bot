import discord

from core.exceptions import ServiceError
from database.models import User
from services.authorization import AuthorizationService
from services.materials import MaterialsService
from utils.helpers import safe_respond


class MaterialsReportModal(discord.ui.Modal, title="Отчет о продаже материалов"):
    quantity = discord.ui.TextInput(
        label="Количество материалов",
        placeholder="Например: 200.000",
        max_length=15,
    )
    evidence = discord.ui.TextInput(
        label="Доказательства",
        placeholder="Ссылка на доказательства",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, user_db: User):
        super().__init__()
        self.user_db = user_db

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: discord.Interaction):
        clean_quantity = "".join(c for c in self.quantity.value if c.isdigit())

        if not clean_quantity or int(clean_quantity) <= 0:
            await safe_respond(interaction, "❌ Введите корректное количество материалов.")
            return

        try:
            await safe_respond(interaction, "✅ Отчет отправляется...")
            await MaterialsService.submit_materials(
                interaction=interaction,
                user_db=self.user_db,
                quantity=int(clean_quantity),
                evidence=self.evidence.value.strip(),
            )
        except ServiceError as error:
            await safe_respond(interaction, error.message)