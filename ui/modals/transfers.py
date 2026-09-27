import discord
from discord import Interaction

from core.exceptions import ServiceError
from database.models import Division, User
from services.authorization import AuthorizationService
from services.transfers import TransferService
from utils.helpers import safe_respond


class TransferModal(discord.ui.Modal):
    def __init__(self, destination: Division, user_db: User):
        super().__init__(title=f"Перевод в {destination.abbreviation}")
        self.destination = destination
        self.user_db = user_db

    name_age = discord.ui.TextInput(
        label="[OOC] Ваше имя и возраст",
        placeholder="Имя и возраст в реальной жизни",
        max_length=50,
    )

    timezone = discord.ui.TextInput(
        label="[OOC] Ваш часовой пояс",
        placeholder="Например: МСК, МСК+3, МСК-1",
        max_length=30,
    )

    online_prime = discord.ui.TextInput(
        label="[OOC] Ваш средний онлайн и прайм-тайм",
        placeholder="4-5 часов в день, прайм-тайм с 18:00 до 22:00 МСК",
        max_length=100,
    )

    motivation = discord.ui.TextInput(
        label="Причина выбора подразделения",
        placeholder="Почему вы хотите перевестись именно в это подразделение?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            await TransferService.validate_no_active_request(self.user_db.discord_id)
            await TransferService.validate_transfer_rules(self.user_db, self.destination)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.transfers import TransferManagementView

        try:
            await safe_respond(interaction, "⏳ Заявление отправляется...", ephemeral=True)

            await TransferService.submit_transfer(
                interaction=interaction,
                user_db=self.user_db,
                destination=self.destination,
                name_age=self.name_age.value.strip(),
                timezone=self.timezone.value.strip(),
                online_prime=self.online_prime.value.strip(),
                motivation=self.motivation.value.strip(),
                view_factory=TransferManagementView,
            )

            await safe_respond(interaction, "✅ Заявление на перевод успешно подано.")

        except ServiceError as error:
            await safe_respond(interaction, error.message)