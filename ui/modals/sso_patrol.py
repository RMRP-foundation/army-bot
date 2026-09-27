import discord

from core.exceptions import ServiceError
from database.models import User
from services.authorization import AuthorizationService
from services.sso_patrol import SSOPatrolService
from ui.modals.labels import patrol_reminder, build_sso_quiz
from utils.helpers import safe_respond
from utils.sso_questions import get_random_quiz


class SSOPatrolModal(discord.ui.Modal, title="Заявление на совместный патруль"):
    def __init__(self, user_db: User):
        super().__init__()
        self.user_db = user_db
        self.quiz_data = get_random_quiz(3)
        self.selects = []

        for i, data in enumerate(self.quiz_data, 1):
            label, select = build_sso_quiz(data, i)
            self.add_item(label)
            self.selects.append(select)
        self.add_item(patrol_reminder())

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            await SSOPatrolService.validate_can_apply(self.user_db)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.sso_patrol import SSOPatrolManagementView

        try:
            await safe_respond(interaction, "⏳ Проверяем ответы...", ephemeral=True)
            success = await SSOPatrolService.submit_sso_patrol(
                interaction=interaction,
                user_db=self.user_db,
                quiz_data=self.quiz_data,
                selects=self.selects,
                view_factory=SSOPatrolManagementView,
            )

            if success:
                await safe_respond(interaction, "### ✅ Заявление отправлено!")
            else:
                await safe_respond(interaction, "### ❌ Тест не пройден.")

        except ServiceError as error:
            await safe_respond(interaction, error.message)