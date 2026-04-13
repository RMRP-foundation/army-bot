import datetime
import re
from typing import Any

import discord
from discord import Interaction, InteractionResponse
from discord._types import ClientT

import config
from database.models import TimeoffRequest, User
from texts import timeoff_title, timeoff_submission, timeoff_description
from ui.views.indicators import indicator_view
from utils.exceptions import StaticInputRequired
from utils.notifications import notify_timeoff_approved, notify_timeoff_rejected
from utils.user_data import get_initiator, get_user_defaults

MSK = datetime.timezone(datetime.timedelta(hours=3))


async def _check_can_apply(interaction: discord.Interaction) -> bool:
    opened_request = await TimeoffRequest.find_one(
        TimeoffRequest.user_id == interaction.user.id,
        TimeoffRequest.status == "PENDING",
    )
    if opened_request is not None:
        await interaction.response.send_message(
            "### У вас уже есть открытое заявление на рассмотрении.\nОжидайте его рассмотрения.",
            ephemeral=True,
        )
        return False

    user = await get_initiator(interaction)
    if not user or user.rank is None:
        await interaction.response.send_message(
            "### Вы не состоите на службе и не можете подать заявление на отгул.",
            ephemeral=True,
        )
        return False
    if user.rank < config.RankIndex.SENIOR_SERGEANT:
        await interaction.response.send_message(
            "### Вы не можете подать заявление на отгул. Требуется звание: Старший сержант+",
            ephemeral=True,
        )
        return False

    today = datetime.datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    approved_request = await TimeoffRequest.find_one(
        TimeoffRequest.user_id == interaction.user.id,
        TimeoffRequest.status == "APPROVED",
        TimeoffRequest.reviewed_at >= today,
    )
    if approved_request:
        await interaction.response.send_message(
            "### Вы уже подавали заявление на отгул сегодня.\nПовторная подача возможна только на следующий день.",
            ephemeral=True,
        )
        return False
    return True


async def timeoff_button_callback(interaction: discord.Interaction):
    if not await _check_can_apply(interaction):
        return
    _, user_name, static_id = await get_user_defaults(interaction)
    from ui.modals.timeoff import TimeoffRequestModal
    await interaction.response.send_modal(TimeoffRequestModal(user_name=user_name))


class TimeoffApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(timeoff_title))
    container.add_item(discord.ui.TextDisplay(timeoff_submission))
    container.add_item(discord.ui.TextDisplay(timeoff_description))
    container.add_item(discord.ui.Separator(visible=True))

    timeoff_button = discord.ui.Button(
        label="Заявление на отгул",
        emoji="⏰",
        style=discord.ButtonStyle.primary,
        custom_id="timeoff_apply_button",
    )
    timeoff_button.callback = timeoff_button_callback

    action_row = discord.ui.ActionRow()
    action_row.add_item(timeoff_button)
    container.add_item(action_row)


async def check_approve_permission(interaction: Interaction[ClientT], request: TimeoffRequest) -> tuple[bool, str]:
    try:
        approver = await get_initiator(interaction)
    except StaticInputRequired:
        return False, ""

    if not approver:
        return False, "Вы не найдены в базе данных."

    if (approver.rank or 0) < config.RankIndex.MAJOR:
        return False, "Для рассмотрения заявок на отгул требуется звание Майор и выше."

    requester = await User.find_one(User.discord_id == request.user_id)
    if not requester:
        return False, "Заявитель не найден в базе данных."

    if (approver.rank or 0) <= (requester.rank or 0):
        return False, "Вы не можете рассматривать заявку человека, чье звание равно вашему или выше."

    return True, ""


class TimeoffManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"timeoff_(?P<action>approve|reject):(?P<id>\d+)",
):
    def __init__(self, action: str, request_id: int):
        labels = {"approve": "Одобрить", "reject": "Отклонить"}
        styles = {"approve": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger}
        emojis = {"approve": "👍", "reject": "👎"}

        super().__init__(
            discord.ui.Button(
                label=labels[action],
                emoji=emojis[action],
                style=styles[action],
                custom_id=f"timeoff_{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        from utils.mongo_lock import try_lock

        if not await try_lock(TimeoffRequest, self.request_id, "status", "PROCESSING", "PENDING"):
            await interaction.response.send_message("❌ Заявка не найдена или уже обработана.", ephemeral=True)
            return

        request = await TimeoffRequest.find_one(TimeoffRequest.id == self.request_id)

        is_allowed, error_msg = await check_approve_permission(interaction, request)
        if not is_allowed:
            await TimeoffRequest.get_pymongo_collection().update_one(
                {"_id": self.request_id}, {"$set": {"status": "PENDING"}}
            )
            if error_msg:
                await interaction.response.send_message(error_msg, ephemeral=True)
            return

        request.status = "APPROVED" if self.action == "approve" else "REJECTED"
        request.reviewed_at = datetime.datetime.now(MSK)
        await request.save()

        assert isinstance(interaction.response, InteractionResponse)
        prefix = "Одобрил" if self.action == "approve" else "Отклонил"
        emoji = "👍" if self.action == "approve" else "👎"
        await interaction.response.edit_message(
            content=f"-# ||<@{request.user_id}> {interaction.user.mention}||",
            embed=await request.to_embed(),
            view=indicator_view(f"{prefix} {interaction.user.display_name}", emoji=emoji),
        )

        if self.action == "approve":
            await notify_timeoff_approved(interaction.client, request.user_id)
        else:
            await notify_timeoff_rejected(interaction.client, request.user_id)


class TimeoffCancelButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"timeoff:cancel:(?P<id>\d+)"
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Отменить",
                style=discord.ButtonStyle.grey,
                custom_id=f"timeoff:cancel:{request_id}",
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        req = await TimeoffRequest.find_one(
            TimeoffRequest.id == self.request_id,
            TimeoffRequest.user_id == interaction.user.id,
        )
        if not req or req.status != "PENDING":
            await interaction.response.send_message("❌ Заявка не найдена или уже обработана.", ephemeral=True)
            return

        req.status = "REJECTED"
        req.reviewed_at = datetime.datetime.now(MSK)
        await req.save()

        await interaction.response.send_message(content="✅ Ваша заявка была отменена.", ephemeral=True)
        await interaction.message.delete()


class TimeoffManagementView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(TimeoffManagementButton("approve", request_id))
        self.add_item(TimeoffManagementButton("reject", request_id))
        self.add_item(TimeoffCancelButton(request_id))