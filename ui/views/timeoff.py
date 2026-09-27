import re

import discord

from core.exceptions import ServiceError
from services.authorization import AuthorizationService
from services.timeoff import TimeoffService
import texts
from ui.modals.timeoff import TimeoffRequestModal
from utils.helpers import safe_respond, build_mentions, safe_edit_message, random_loading_message, safe_delete_message


async def _timeoff_button_callback(interaction: discord.Interaction) -> None:
    """Проверяет возможность подачи заявления на отгул и открывает модалку."""
    try:
        user_db = await AuthorizationService.require_active_soldier(interaction)
        await TimeoffService.validate_can_apply(user_db)
        await interaction.response.send_modal(TimeoffRequestModal(user_db))
    except ServiceError as error:
        await safe_respond(interaction, error.message)


class TimeoffApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(texts.timeoff_title))
    container.add_item(discord.ui.TextDisplay(texts.timeoff_submission))
    container.add_item(discord.ui.TextDisplay(texts.timeoff_description))
    container.add_item(discord.ui.Separator(visible=True))

    timeoff_button = discord.ui.Button(
        label="Заявление на отгул",
        emoji="⏰",
        style=discord.ButtonStyle.primary,
        custom_id="timeoff_apply_button",
    )
    timeoff_button.callback = _timeoff_button_callback

    action_row = discord.ui.ActionRow()
    action_row.add_item(timeoff_button)
    container.add_item(action_row)


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

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message())

            if self.action == "approve":
                result = await TimeoffService.approve_timeoff(interaction, self.request_id, officer)
            else:
                result = await TimeoffService.reject_timeoff(interaction, self.request_id, officer)

            await safe_edit_message(
                message=interaction.message, content=build_mentions(result.mention_ids), embed=result.embed,
                view=result.view,
            )
            await safe_respond(interaction, result.message)

        except ServiceError as error:
            await safe_respond(interaction, error.message)


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
        try:
            await TimeoffService.cancel_timeoff(self.request_id, interaction.user.id)
            await safe_delete_message(interaction.message)
            await safe_respond(interaction, "✅ Ваша заявка на отгул была отменена.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class TimeoffManagementView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(TimeoffManagementButton("approve", request_id))
        self.add_item(TimeoffManagementButton("reject", request_id))
        self.add_item(TimeoffCancelButton(request_id))