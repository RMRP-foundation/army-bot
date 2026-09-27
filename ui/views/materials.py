import discord

from core.exceptions import ServiceError
from services.authorization import AuthorizationService
from ui.modals.materials import MaterialsReportModal
from utils.helpers import safe_respond


async def _open_report_modal(interaction: discord.Interaction) -> None:
    """Проверяет службу пользователя и открывает модалку подачи отчета."""
    try:
        user_db = await AuthorizationService.require_active_soldier(interaction)
        await interaction.response.send_modal(MaterialsReportModal(user_db))
    except ServiceError as error:
        await safe_respond(interaction, error.message)


class MaterialsReportView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay("# 💸 Продажа материалов"))
        container.add_item(discord.ui.Separator())

        btn = discord.ui.Button(
            label="Подать отчет",
            emoji="📨",
            style=discord.ButtonStyle.primary,
            custom_id="btn_materials_report",
        )
        btn.callback = _open_report_modal

        row = discord.ui.ActionRow()
        row.add_item(btn)
        container.add_item(row)
        self.add_item(container)