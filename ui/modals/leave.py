import datetime

import dateparser
import discord

from config import OOC_MIN_DAYS, OOC_MAX_DAYS, IC_MAX_DAYS
from database.counters import get_next_id
from database.models import LeaveRequest, LeaveType
from utils.user_data import get_initiator

MSK = datetime.timezone(datetime.timedelta(hours=3))

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
        settings=DATEPARSER_SETTINGS
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
            label="Дата окончания",
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
        self.add_item(self.start_input)
        self.add_item(self.end_input)
        self.add_item(self.reason_input)


    async def on_submit(self, interaction: discord.Interaction):
        user_db = await get_initiator(interaction)

        start_date = parse_date(self.start_input.value)
        end_date = parse_date(self.end_input.value)

        if not start_date or not end_date:
            await interaction.response.send_message(
                "❌ Не удалось распознать даты. Попробуйте формат: `20.05.2026`.",
                ephemeral=True,
            )
            return

        today = (discord.utils.utcnow() + datetime.timedelta(hours=3)).date()
        if start_date < today:
            await interaction.response.send_message(
                "❌ Дата начала не может быть в прошлом.", ephemeral=True
            )
            return

        if end_date <= start_date:
            await interaction.response.send_message(
                "❌ Дата окончания должна быть позже даты начала.", ephemeral=True
            )
            return

        days = (end_date - start_date).days + 1

        if self.leave_type == LeaveType.IC:
            if not (1 <= days <= IC_MAX_DAYS):
                await interaction.response.send_message(
                    f"❌ IC отпуск можно взять на 1–{IC_MAX_DAYS} дней (у вас {days} дн.).",
                    ephemeral=True,
                )
                return
        else:
            if not (OOC_MIN_DAYS <= days <= OOC_MAX_DAYS):
                await interaction.response.send_message(
                    f"❌ OOC отпуск можно взять на {OOC_MIN_DAYS}–{OOC_MAX_DAYS} дней (у вас {days} дн.).",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            "✅ Заявление подаётся...", ephemeral=True
        )

        start_dt = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=MSK)
        end_dt = datetime.datetime.combine(end_date, datetime.time.max, tzinfo=MSK)

        new_id = await get_next_id("leave_requests")
        request = LeaveRequest(
            id=new_id,
            user_id=interaction.user.id,
            leave_type=self.leave_type,
            reason=self.reason_input.value.strip(),
            starts_at=start_dt.astimezone(datetime.timezone.utc),
            ends_at=end_dt.astimezone(datetime.timezone.utc)
        )
        await request.create()

        from database import divisions

        div = divisions.get_division(user_db.division)
        if not div or not div.positions:
            div = divisions.get_division_by_abbreviation("ВК")

        mentions = [f"<@{interaction.user.id}>"]
        mentions += [f"<@&{pos.role_id}>" for pos in div.positions if pos.privilege.value >= 2 and pos.role_id]

        from ui.views.leave import LeaveManagementView

        embed = await request.to_embed()
        msg = await interaction.channel.send(
            content=f"||{' '.join(mentions)}||",
            embed=embed,
            view=LeaveManagementView(new_id, status="PENDING"),
        )
        request.message_id = msg.id
        await request.save()

        from cogs.leave import update_bottom_message
        await update_bottom_message(interaction.client, self.leave_type)