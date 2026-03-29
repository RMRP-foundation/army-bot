import discord

from utils.user_data import get_initiator
from ui.modals.materials import MaterialsReportModal


async def open_report_modal(interaction: discord.Interaction):
    user_db = await get_initiator(interaction)
    if not user_db:
        return await interaction.response.send_message("❌ Профиль не найден.", ephemeral=True)

    await interaction.response.send_modal(MaterialsReportModal(user_db))


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
            custom_id="btn_materials_report"
        )
        btn.callback = open_report_modal

        row = discord.ui.ActionRow()
        row.add_item(btn)
        container.add_item(row)
        self.add_item(container)