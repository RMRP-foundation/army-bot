import re

import discord

from core.exceptions import ServiceError
from database.models import LeaveType
from services.authorization import AuthorizationService
from services.leave import LeaveService
from texts import (
    ic_leave_description,
    ic_leave_title,
    ooc_leave_description,
    ooc_leave_title,
)
from ui.modals.leave import LeaveRequestModal
from utils.helpers import safe_respond, safe_edit_message, safe_delete_message, build_mentions, random_loading_message


async def _open_leave_modal(interaction: discord.Interaction, leave_type: LeaveType) -> None:
    """Проверяет возможность подачи и открывает модалку."""
    try:
        user_db = await AuthorizationService.require_active_soldier(interaction)
        await LeaveService.validate_can_apply(user_db, leave_type)
        await interaction.response.send_modal(LeaveRequestModal(leave_type))
    except ServiceError as error:
        await safe_respond(interaction, error.message)


class ICLeaveApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(ic_leave_title))
    container.add_item(discord.ui.TextDisplay(ic_leave_description))
    container.add_item(discord.ui.Separator())

    ic_button = discord.ui.Button(
        label="Подать заявление",
        style=discord.ButtonStyle.primary,
        custom_id="ic_leave_apply_button",
    )
    ic_button.callback = lambda inter: _open_leave_modal(inter, LeaveType.IC)

    action_row = discord.ui.ActionRow()
    action_row.add_item(ic_button)
    container.add_item(action_row)


class OOCLeaveApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(ooc_leave_title))
    container.add_item(discord.ui.TextDisplay(ooc_leave_description))
    container.add_item(discord.ui.Separator())

    ooc_button = discord.ui.Button(
        label="Подать заявление",
        style=discord.ButtonStyle.primary,
        custom_id="ooc_leave_apply_button",
    )
    ooc_button.callback = lambda inter: _open_leave_modal(inter, LeaveType.OOC)

    action_row = discord.ui.ActionRow()
    action_row.add_item(ooc_button)
    container.add_item(action_row)


class LeaveManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"leave_(?P<action>approve|reject|annul|cancel):(?P<id>\d+)",
):
    _config = {
        "approve": ("Одобрить", discord.ButtonStyle.success, "👍"),
        "reject": ("Отклонить", discord.ButtonStyle.danger, "👎"),
        "annul": ("Аннулировать", discord.ButtonStyle.grey, None),
        "cancel": ("Отменить", discord.ButtonStyle.grey, None),
    }

    def __init__(self, action: str, request_id: int):
        label, style, emoji = self._config[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                emoji=emoji,
                custom_id=f"leave_{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            user_db = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message(), ephemeral=True)

            match self.action:
                case "approve":
                    result = await LeaveService.approve_leave(interaction, self.request_id, user_db)
                case "reject":
                    result = await LeaveService.reject_leave(interaction, self.request_id, user_db)
                case "annul":
                    result = await LeaveService.annul_leave(interaction, self.request_id, user_db)
                case "cancel":
                    await LeaveService.cancel_leave(self.request_id, interaction.user.id)
                    await safe_delete_message(interaction.message)
                    await safe_respond(interaction, "✅ Заявление отменено.")
                    return

            await safe_edit_message(
                message=interaction.message,
                content=build_mentions([result.request.user_id, interaction.user.id]),
                embed=result.embed,
                view=result.view,
            )
            await safe_respond(interaction, result.message)

        except ServiceError as error:
            await safe_respond(interaction, error.message)


class LeaveManagementView(discord.ui.View):
    def __init__(self, request_id: int, status: str = "PENDING"):
        super().__init__(timeout=None)
        if status == "PENDING":
            self.add_item(LeaveManagementButton("approve", request_id))
            self.add_item(LeaveManagementButton("reject", request_id))
            self.add_item(LeaveManagementButton("cancel", request_id))
        elif status == "APPROVED":
            self.add_item(LeaveManagementButton("annul", request_id))