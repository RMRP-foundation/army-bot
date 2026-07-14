import discord
from beanie.odm.operators.find.comparison import In

from database.counters import get_next_id
from database.models import PromotionRequest, User, Division
from ui.views.promotion import _promotion_view


class PromotionRequestModal(discord.ui.Modal, title="Рапорт на повышение"):
    evidence = discord.ui.TextInput(
        label="Доказательства",
        style=discord.TextStyle.paragraph,
        placeholder="Перечислите выполненную работу и прикрепите ссылки на доказательства",
        max_length=1024,
    )
    score = discord.ui.TextInput(
        label="Общее количество баллов",
        placeholder="Например: 300 (ВА и КМБ оставляет поле пустым)",
        max_length=100,
        required=False,
    )

    def __init__(self, division: Division, user_db: User):
        super().__init__()
        self.division = division
        self.user_db = user_db

    async def on_submit(self, interaction: discord.Interaction):
        existing = await PromotionRequest.find_one(
            PromotionRequest.user_id == interaction.user.id,
            In(PromotionRequest.status, ["PENDING", "APPROVED"]),
        )
        if existing:
            return await interaction.response.send_message(
                f"❌ У вас уже есть активный рапорт #{existing.id}.", ephemeral=True
            )

        new_id = await get_next_id("promotion_reports")
        report = PromotionRequest(
            id=new_id,
            user_id=interaction.user.id,
            division_id=self.division.division_id,
            current_rank=self.user_db.rank,
            target_rank=self.user_db.rank + 1,
            evidence=self.evidence.value,
            score=self.score.value or None,
        )
        await report.create()

        await interaction.response.send_message("✅ Рапорт отправлен.", ephemeral=True)

        channel = interaction.client.get_channel(self.division.promotion_channel)
        sent = await channel.send(
            content=f"-# ||<@{interaction.user.id}>||",
            embed=await report.to_embed(interaction.client),
            view=_promotion_view(report.id, "approve", "reject", "cancel"),
        )

        report.message_id = sent.id
        await report.save()

        from cogs.promotion import update_bottom_message
        await update_bottom_message(interaction.client, self.division.promotion_channel)