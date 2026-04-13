import discord

from config import OOC_MIN_DAYS, OOC_MAX_DAYS, IC_MAX_DAYS
from database.counters import get_next_id
from database.models import LeaveRequest, LeaveType
from utils.user_data import get_initiator


class LeaveRequestModal(discord.ui.Modal):
    def __init__(self, leave_type: LeaveType):
        super().__init__(title=f"Заявление на {leave_type.value} отпуск")
        self.leave_type = leave_type

        days_hint = (
            f"1–{IC_MAX_DAYS}" if leave_type == LeaveType.IC
            else f"{OOC_MIN_DAYS}–{OOC_MAX_DAYS}"
        )

        self.days_input = discord.ui.TextInput(
            label="Количество дней",
            placeholder=days_hint,
            max_length=2,
            required=True,
        )
        self.reason_input = discord.ui.TextInput(
            label="Причина",
            style=discord.TextStyle.paragraph,
            placeholder="Укажите причину отпуска",
            max_length=500,
            required=True,
        )
        self.add_item(self.days_input)
        self.add_item(self.reason_input)


    async def on_submit(self, interaction: discord.Interaction):
        user_db = await get_initiator(interaction)

        try:
            days = int(self.days_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Введите корректное число дней.", ephemeral=True
            )
            return

        if self.leave_type == LeaveType.IC:
            if not (1 <= days <= IC_MAX_DAYS):
                await interaction.response.send_message(
                    f"❌ IC отпуск можно взять на промежуток от 1 до {IC_MAX_DAYS} дней.", ephemeral=True
                )
                return
        else:
            if not (OOC_MIN_DAYS <= days <= OOC_MAX_DAYS):
                await interaction.response.send_message(
                    f"❌ OOC отпуск можно взять на промежуток от {OOC_MIN_DAYS} до {OOC_MAX_DAYS} дней.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            "✅ Заявление подаётся...", ephemeral=True
        )

        new_id = await get_next_id("leave_requests")
        request = LeaveRequest(
            id=new_id,
            user_id=interaction.user.id,
            leave_type=self.leave_type,
            reason=self.reason_input.value.strip(),
            days=days,
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