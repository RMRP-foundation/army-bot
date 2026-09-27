import discord

from core.exceptions import ServiceError
from database.models import LogisticsType
from services.logistics import LogisticsService
from ui.modals.labels import name_input
from utils.helpers import safe_respond
from utils.user_data import clean_name


class LogisticsModal(discord.ui.Modal):
    def __init__(self, supply_type: LogisticsType, default_nickname: str = ""):
        super().__init__(title=f"Запрос: {supply_type.value}")
        self.supply_type = supply_type

        self.nickname = name_input()
        if default_nickname:
            self.nickname.default = default_nickname

        self.faction = discord.ui.TextInput(
            label="Ваша организация",
            placeholder="Например: ФСВНГ",
            max_length=20,
        )
        self.add_item(self.nickname)
        self.add_item(self.faction)

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.logistics import LogisticsManagementView

        try:
            await safe_respond(interaction, "✅ Ваш запрос на поставку отправляется...")

            await LogisticsService.submit_logistics(
                interaction=interaction,
                nickname=clean_name(self.nickname.value),
                faction=self.faction.value.strip(),
                supply_type=self.supply_type,
                view_factory=LogisticsManagementView,
            )
        except ServiceError as error:
            await safe_respond(interaction, error.message)