import discord
from beanie.odm.operators.find.comparison import In

import config
from database.counters import get_next_id
from database.models import PromotionRequest, User, Division
from ui.modals.labels import evidence, score
from ui.views.promotion import _promotion_view


class PromotionRequestModal(discord.ui.Modal, title="Рапорт на повышение"):
    def __init__(self, division: Division, user_db: User):
        super().__init__()
        self.division = division
        self.user_db = user_db
        self.evidence = self.mandatory = self.additional = self.score = None

        if division.division_id in config.PROMOTION_SIMPLE_EVIDENCE_DIVISIONS:
            self.evidence = evidence("Доказательства")
            self.add_item(self.evidence)
        else:
            self.mandatory = evidence("Доказательства балловой системы")
            self.additional = evidence("Обязательные условия вне балловой системы")
            self.score = score()

            for item in (self.mandatory, self.additional, self.score):
                self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        existing = await PromotionRequest.find_one(
            PromotionRequest.user_id == interaction.user.id,
            In(PromotionRequest.status, ["PENDING", "APPROVED"]),
        )
        if existing:
            return await interaction.response.send_message(
                f"❌ У вас уже есть активный рапорт #{existing.id}.", ephemeral=True
            )

        ev = {}

        if self.evidence:
            ev["Доказательства"] = self.evidence.value
        else:
            ev["Доказательства балловой системы"] = self.mandatory.value
            ev["Обязательные условия вне балловой системы"] = self.additional.value

        new_id = await get_next_id("promotion_reports")
        report = PromotionRequest(
            id=new_id,
            user_id=interaction.user.id,
            division_id=self.division.division_id,
            current_rank=self.user_db.rank,
            target_rank=self.user_db.rank + 1,
            evidence=ev,
            score=self.score.value if self.score and self.score.value else None,
        )
        await report.create()

        await interaction.response.send_message("✅ Рапорт отправлен.", ephemeral=True)

        channel = interaction.client.get_channel(self.division.promotion_channel)
        role_ids = config.PROMOTION_NOTIFY_ROLES.get(self.division.division_id, ())
        role_tags = " ".join(f"<@&{rid}>" for rid in role_ids)
        content = f"-# ||<@{interaction.user.id}>{f' {role_tags}' if role_tags else ''}||"

        sent = await channel.send(
            content=content,
            embed=await report.to_embed(interaction.client),
            view=_promotion_view(report.id, "approve", "reject", "cancel"),
        )

        report.message_id = sent.id
        await report.save()

        from cogs.promotion import update_bottom_message
        await update_bottom_message(interaction.client, self.division.promotion_channel)