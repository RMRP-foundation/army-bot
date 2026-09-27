import discord
from discord import Interaction

from core.exceptions import ServiceError
from database.models import DismissalType
from services.authorization import AuthorizationService
from services.dismissal import DismissalService
from ui.modals.labels import name_input
from utils.helpers import safe_respond
from utils.user_data import clean_name


class DismissalModal(discord.ui.Modal):
    name = name_input()

    def __init__(self, dismissal_type: DismissalType, default_name: str = ""):
        super().__init__(title=f"Увольнение: {dismissal_type.value}")
        self.dismissal_type = dismissal_type
        self.name.default = default_name
        self.user_db = None

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            return True

        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.dismissal import DismissalManagementView

        try:
            await safe_respond(interaction, "✅ Рапорт подается...")

            await DismissalService.submit_dismissal(
                bot=interaction.client,
                user=self.user_db,
                full_name=clean_name(self.name.value),
                dismissal_type=self.dismissal_type,
                view_factory=DismissalManagementView
            )

        except ServiceError as error:
            await safe_respond(interaction, error.message)