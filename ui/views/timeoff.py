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

closed_requests = set()

MSK = datetime.timezone(datetime.timedelta(hours=3))


async def _check_can_apply(interaction: discord.Interaction) -> bool:
    """Проверить, может ли пользователь подать заявку."""
    opened_request = await TimeoffRequest.find_one(
        TimeoffRequest.user_id == interaction.user.id,
        TimeoffRequest.checked == False,  # noqa: E712
    )
    if opened_request is not None:
        await interaction.response.send_message(
            "### У вас уже есть открытое заявление на рассмотрении.\n"
            "Ожидайте его рассмотрения.",
            ephemeral=True,
        )
        return False

    user = await get_initiator(interaction)
    if not user or user.rank is None:
        await interaction.response.send_message(
            "### Вы не состоите на службе "
            "и не можете подать заявление на отгул.",
            ephemeral=True,
        )
        return False
    if user.rank < config.RankIndex.SENIOR_SERGEANT:
        await interaction.response.send_message(
            f"### Вы не можете подать заявление на отгул. "
            f"Требуется звание: Старший сержант+",
            ephemeral=True,
        )
        return False

    today = datetime.datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    approved_request = await TimeoffRequest.find_one(
        TimeoffRequest.user_id == interaction.user.id,
        TimeoffRequest.approved == True,
        TimeoffRequest.reviewed_at >= today
    )
    if approved_request:
        await interaction.response.send_message(
            "### Вы уже подавали заявление на отгул сегодня.\n"
            "Повторная подача возможна только на следующий день.",
            ephemeral=True
        )
        return False
    return True


async def timeoff_button_callback(interaction: discord.Interaction):
    """Callback для кнопки запроса отгула."""
    if not await _check_can_apply(interaction):
        return

    _, user_name, static_id = await get_user_defaults(interaction)

    from ui.modals.timeoff import TimeoffRequestModal
    await interaction.response.send_modal(
        TimeoffRequestModal(user_name=user_name)
    )


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


async def check_approve_permission(
    interaction: Interaction[ClientT], request: TimeoffRequest
) -> tuple[bool, str]:
    """
    Проверяет права на одобрение/отклонение заявки.
    Возвращает (Результат, Сообщение об ошибке)
    """
    try:
        approver = await get_initiator(interaction)
    except StaticInputRequired:
        return False, ""

    if not approver:
        return False, "Вы не найдены в базе данных."

    # Проверка минимального звания (Майор)
    if (approver.rank or 0) < config.RankIndex.MAJOR:
        return False, "Для рассмотрения заявок на отгул требуется звание Майор и выше."

    requester = await User.find_one(User.discord_id == request.user_id)
    if not requester:
        return False, "Заявитель не найден в базе данных."

    # Проверка иерархии: ранг одобряющего должен быть СТРОГО БОЛЬШЕ ранга заявителя
    if (approver.rank or 0) <= (requester.rank or 0):
        return False, "Вы не можете рассматривать заявку человека, чье звание равно вашему или выше."

    return True, ""


class ApproveTimeoffButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"approve_timeoff:(?P<id>\d+)"
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Одобрить",
                emoji="👍",
                custom_id=f"approve_timeoff:{request_id}",
                style=discord.ButtonStyle.success,
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        request_id = int(match.group("id"))
        return cls(request_id)

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await TimeoffRequest.find_one(TimeoffRequest.id == self.request_id)
        if not request:
            await interaction.response.send_message("Запрос не найден.", ephemeral=True)
            return

        if request.checked or self.request_id in closed_requests:
            await interaction.response.send_message(
                "Этот запрос уже был обработан.", ephemeral=True
            )
            return
        closed_requests.add(self.request_id)

        is_allowed, error_msg = await check_approve_permission(interaction, request)
        if not is_allowed:
            closed_requests.discard(self.request_id)
            if error_msg:
                await interaction.response.send_message(error_msg, ephemeral=True)
            return

        request.approved = True
        request.checked = True
        request.reviewed_at = datetime.datetime.now(MSK)
        await request.save()
        assert isinstance(interaction.response, InteractionResponse)
        await interaction.response.edit_message(
            content=f"-# ||<@{request.user_id}> {interaction.user.mention}||",
            embed=await request.to_embed(),
            view=indicator_view(f"Одобрил {interaction.user.display_name}", emoji="👍"),
        )

        await notify_timeoff_approved(
            interaction.client, request.user_id
        )


class RejectTimeoffButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"reject_timeoff:(?P<id>\d+)"
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Отклонить",
                emoji="👎",
                custom_id=f"reject_timeoff:{request_id}",
                style=discord.ButtonStyle.danger,
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        request_id = int(match.group("id"))
        return cls(request_id)

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await TimeoffRequest.find_one(TimeoffRequest.id == self.request_id)
        if not request:
            await interaction.response.send_message("Запрос не найден.", ephemeral=True)
            return

        # Двойная проверка: БД (персистентная) + in-memory (быстрая)
        if request.checked or self.request_id in closed_requests:
            await interaction.response.send_message(
                "Этот запрос уже был обработан.", ephemeral=True
            )
            return
        closed_requests.add(self.request_id)

        # Проверка прав
        is_allowed, error_msg = await check_approve_permission(interaction, request)
        if not is_allowed:
            closed_requests.discard(self.request_id)
            if error_msg:
                await interaction.response.send_message(error_msg, ephemeral=True)
            return

        request.approved = False
        request.checked = True
        request.reviewed_at = datetime.datetime.now(MSK)
        await request.save()
        assert isinstance(interaction.response, InteractionResponse)
        await interaction.response.edit_message(
            content=f"-# ||<@{request.user_id}> {interaction.user.mention}||",
            embed=await request.to_embed(),
            view=indicator_view(
                f"Отклонил {interaction.user.display_name}", emoji="👎"
            ),
        )

        await notify_timeoff_rejected(
            interaction.client, request.user_id
        )

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
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        req = await TimeoffRequest.find_one(
            TimeoffRequest.id == self.request_id,
            TimeoffRequest.user_id == interaction.user.id,
        )
        if not req or req.checked == True:
            await interaction.response.send_message(
                "❌ Заявка не найдена или уже обработана.", ephemeral=True
            )
            return

        req.checked = True
        req.approved = False
        req.reviewed_at = datetime.datetime.now(MSK)
        await req.save()

        await interaction.response.send_message(
            content="✅ Ваша заявка была отменена.", ephemeral=True
        )
        await interaction.message.delete()