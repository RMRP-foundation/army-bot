import discord

from core.exceptions import ServiceError
from services.authorization import AuthorizationService
from services.supplies_audit import SupplyAuditService
import texts
from ui.modals.supplies_audit import ClearSupplyModal, GiveSupplyModal
from utils.helpers import safe_respond


async def _open_give_modal(interaction: discord.Interaction) -> None:
    """Проверяет права и открывает модалку выдачи склада."""
    try:
        user_db = await AuthorizationService.require_active_soldier(interaction)
        SupplyAuditService.validate_officer(user_db)
        await interaction.response.send_modal(GiveSupplyModal(user_db))
    except ServiceError as error:
        await safe_respond(interaction, error.message)


async def _open_clear_modal(interaction: discord.Interaction) -> None:
    """Проверяет права и открывает модалку чистки склада."""
    try:
        user_db = await AuthorizationService.require_active_soldier(interaction)
        SupplyAuditService.validate_officer(user_db)
        await interaction.response.send_modal(ClearSupplyModal(user_db))
    except ServiceError as error:
        await safe_respond(interaction, error.message)


class SupplyAuditView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(texts.supply_audit_title))
    container.add_item(
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large)
    )

    give_button = discord.ui.Button(
        label="Выдача склада",
        emoji="📝",
        style=discord.ButtonStyle.gray,
        custom_id="supply_audit_give",
    )
    give_button.callback = _open_give_modal

    clear_button = discord.ui.Button(
        label="Очистка склада",
        emoji="🧹",
        style=discord.ButtonStyle.gray,
        custom_id="supply_audit_clear",
    )
    clear_button.callback = _open_clear_modal

    action_row = discord.ui.ActionRow()
    action_row.add_item(give_button)
    action_row.add_item(clear_button)
    container.add_item(action_row)