import discord
import config
from database.models import MaterialsReport, User
from ui.modals.labels import name_component
from utils.user_data import get_initiator


class MaterialsReportModal(discord.ui.Modal, title="Отчет о продаже материалов"):
    name = name_component()
    quantity = discord.ui.TextInput(label="Количество материалов", placeholder="Например: 200.000", max_length=15)
    evidence = discord.ui.TextInput(label="Доказательства", placeholder="Ссылка на доказательства",
                                    style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, user_db: User):
        super().__init__()
        self.user_db = user_db
        self.name.default = user_db.full_name

    async def on_submit(self, interaction: discord.Interaction):
        clean_quantity = "".join(c for c in self.quantity.value if c.isdigit())

        if not clean_quantity or int(clean_quantity) <= 0:
            return await interaction.response.send_message(
                "❌ Введите корректное количество материалов.",
                ephemeral=True
            )

        report = MaterialsReport(
            user_id=interaction.user.id,
            full_name=self.name.value,
            quantity=int(clean_quantity),
            evidence=self.evidence.value
        )
        await report.create()

        channel = interaction.client.get_channel(config.CHANNELS["materials"])

        role_mentions = [f"<@&{rid}>" for rid in config.MATERIALS_MENTIONS]
        content = f"-# ||{interaction.user.mention} {' '.join(role_mentions)}||"

        embed = await report.to_embed(self.user_db)
        await channel.send(content=content, embed=embed)

        await interaction.response.send_message("✅ Отчет успешно отправлен.", ephemeral=True)

        from cogs.materials import update_bottom_message
        await update_bottom_message(interaction.client)