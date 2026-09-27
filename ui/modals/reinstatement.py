import discord.ui

from core.exceptions import ServiceError
from database.models import User
from services.authorization import AuthorizationService
from services.reinstatement import ReinstatementService
from ui.modals.labels import screenshot_label, static_reminder
from utils.helpers import safe_respond


class ReinstatementModal(discord.ui.Modal, title="Заявление на восстановление"):
    all_documents = screenshot_label("всех документов")
    army_pass = screenshot_label("военного билета")
    footer = static_reminder()

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
        from ui.views.reinstatement import ReinstatementManagementView

        try:
            await safe_respond(interaction, "⏳ Заявление отправляется...")
            await ReinstatementService.submit_reinstatement(
                interaction=interaction,
                user_db=self.user_db,
                all_documents=self.all_documents.component.value.strip(),
                army_pass=self.army_pass.component.value.strip(),
                view_factory=ReinstatementManagementView,
            )
            await safe_respond(interaction, "### Заявление отправлено на рассмотрение.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)