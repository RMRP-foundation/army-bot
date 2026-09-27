import discord

from core import config
from core.exceptions import ServiceError
from database.models import Division, User
from services.authorization import AuthorizationService
from services.promotion import PromotionService
from ui.modals.labels import evidence_input, score_input
from utils.helpers import safe_respond


class PromotionRequestModal(discord.ui.Modal, title="Рапорт на повышение"):
    def __init__(self, division: Division, user_db: User):
        super().__init__()
        self.division = division
        self.user_db = user_db
        self.evidence_input = self.mandatory_input = self.additional_input = self.score_input = None

        if division.division_id in config.PROMOTION_SIMPLE_EVIDENCE_DIVISIONS:
            self.evidence_input = evidence_input("Доказательства")
            self.add_item(self.evidence_input)
        else:
            self.mandatory_input = evidence_input("Доказательства балловой системы")
            self.additional_input = evidence_input("Обязательные условия вне балловой системы")
            self.score_input = score_input()

            for item in (self.mandatory_input, self.additional_input, self.score_input):
                self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    def _get_evidence_dict(self) -> dict[str, str]:
        """Инкапсулирует логику упаковки полей ввода модалки в словарь."""
        if self.evidence_input:
            return {"Доказательства": self.evidence_input.value}

        return {
            "Доказательства балловой системы": self.mandatory_input.value if self.mandatory_input else "",
            "Обязательные условия вне балловой системы": self.additional_input.value if self.additional_input else "",
        }

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.promotion import PromotionManagementView

        try:
            await safe_respond(interaction, "✅ Рапорт подается...")
            await PromotionService.submit_promotion(
                interaction=interaction,
                user_db=self.user_db,
                division=self.division,
                evidence=self._get_evidence_dict(),
                score=self.score_input.value.strip() if self.score_input and self.score_input.value else None,
                view_factory=PromotionManagementView,
            )
        except ServiceError as error:
            await safe_respond(interaction, error.message)