import discord
from discord import Interaction

from core.exceptions import ServiceError
from database.models import User
from services.authorization import AuthorizationService
from services.timeoff import TimeoffService
from ui.modals.labels import period_input
from utils.helpers import safe_respond


class TimeoffRequestModal(discord.ui.Modal, title="Заявление на отгул"):
    period = period_input()

    def __init__(self, user_db: User):
        super().__init__()
        self.user_db = user_db

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            await TimeoffService.validate_can_apply(self.user_db)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.timeoff import TimeoffManagementView

        try:
            await safe_respond(interaction, "⏳ Заявление отправляется...", ephemeral=True)

            await TimeoffService.submit_timeoff(
                interaction=interaction,
                user_db=self.user_db,
                period=self.period.value.strip(),
                view_factory=TimeoffManagementView,
            )

            await safe_respond(interaction, "### Заявление отправлено на рассмотрение.", ephemeral=True)

        except ServiceError as error:
            await safe_respond(interaction, error.message)