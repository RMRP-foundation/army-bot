import discord
from discord import Interaction

from core.exceptions import ServiceError
from database.models import User
from services.authorization import AuthorizationService
from services.supplies_audit import SupplyAuditService
from utils.helpers import safe_respond, random_loading_message


class GiveSupplyModal(discord.ui.Modal, title="Выдача снабжения"):
    to_whom = discord.ui.Label(
        text="Кому выдаете снабжение?",
        description="Выберите военнослужащего, которому вы хотите выдать снабжение.",
        component=discord.ui.UserSelect(
            placeholder="Выберите пользователя",
            min_values=1,
            max_values=1,
            required=True,
        ),
    )
    items = discord.ui.TextInput(
        label="Снабжение",
        placeholder="Перечислите, что вы выдаете",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000,
        required=True,
    )
    reason = discord.ui.TextInput(
        label="Причина выдачи",
        placeholder="Укажите причину выдачи снабжения",
        required=False,
    )

    def __init__(self, user_db: User):
        super().__init__()
        self.user_db = user_db

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            SupplyAuditService.validate_officer(self.user_db)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: Interaction, /) -> None:
        try:
            await safe_respond(interaction, random_loading_message())

            selected_user = self.to_whom.component.values[0]

            await SupplyAuditService.submit_give_supply(
                interaction=interaction,
                user_db=self.user_db,
                recipient_id=selected_user.id,
                items_raw=self.items.value,
                reason=self.reason.value,
            )

            await safe_respond(interaction, "✅ Снабжение успешно выдано.")

        except ServiceError as error:
            await safe_respond(interaction, error.message)


class ClearSupplyModal(discord.ui.Modal, title="Чистка склада"):
    job = discord.ui.TextInput(
        label="Действие",
        placeholder="Перечислите выполненные на складе действия",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000,
        required=True,
    )

    def __init__(self, user_db: User):
        super().__init__()
        self.user_db = user_db

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            SupplyAuditService.validate_officer(self.user_db)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: Interaction, /) -> None:
        try:
            await safe_respond(interaction, random_loading_message())

            await SupplyAuditService.submit_clear_supply(
                interaction=interaction,
                user_db=self.user_db,
                job_raw=self.job.value,
            )

            await safe_respond(interaction, "✅ Запись успешно отправлена.")

        except ServiceError as error:
            await safe_respond(interaction, error.message)