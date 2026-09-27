import datetime

import dateparser
import discord
from discord import Interaction

from core.exceptions import ServiceError
from database.models import LeaveType
from services.authorization import AuthorizationService
from services.leave import LeaveService
from utils.helpers import safe_respond

DATEPARSER_SETTINGS = {
    "RETURN_AS_TIMEZONE_AWARE": True,
    "DATE_ORDER": "DMY",
    "PREFER_DATES_FROM": "future",
    "TIMEZONE": "Europe/Moscow",
    "TO_TIMEZONE": "Europe/Moscow",
    "REQUIRE_PARTS": ["day", "month"],
}


def parse_date(raw: str) -> datetime.date | None:
    raw = raw.strip()
    result = dateparser.parse(
        raw,
        languages=["ru", "en"],
        date_formats=["%d.%m", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"],
        settings=DATEPARSER_SETTINGS,
    )
    return result.date() if result else None


class LeaveRequestModal(discord.ui.Modal):
    def __init__(self, leave_type: LeaveType):
        super().__init__(title=f"Заявление на {leave_type.value} отпуск")
        self.leave_type = leave_type

        self.start_input = discord.ui.TextInput(
            label="Дата начала",
            placeholder="например: 20.05.2026, 20 мая, завтра",
            max_length=30,
            required=True,
        )
        self.end_input = discord.ui.TextInput(
            label="Дата выхода",
            placeholder="например: 25.05.2026, 25 мая",
            max_length=30,
            required=True,
        )
        self.reason_input = discord.ui.TextInput(
            label="Причина",
            style=discord.TextStyle.paragraph,
            placeholder="Укажите причину отпуска",
            max_length=500,
            required=True,
        )
        self.user_db = None
        self.add_item(self.start_input)
        self.add_item(self.end_input)
        self.add_item(self.reason_input)

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        try:
            self.user_db = await AuthorizationService.require_active_soldier(interaction)
            return True
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return False

    async def on_submit(self, interaction: discord.Interaction):
        start_date = parse_date(self.start_input.value)
        end_date = parse_date(self.end_input.value)

        if not start_date or not end_date:
            await safe_respond(interaction, "❌ Не удалось распознать даты. Попробуйте формат: `20.05.2026`.")
            return

        from ui.views.leave import LeaveManagementView

        try:
            await safe_respond(interaction, "✅ Рапорт подается...")

            await LeaveService.submit_leave(
                interaction=interaction,
                user_db=self.user_db,
                leave_type=self.leave_type,
                start_date=start_date,
                end_date=end_date,
                reason=self.reason_input.value.strip(),
                view_factory=LeaveManagementView,
            )

        except ServiceError as error:
            await safe_respond(interaction, error.message)