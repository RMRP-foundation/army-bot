import discord

from database.counters import get_next_id
from database.models import SSOPatrolRequest
from ui.modals.labels import name_component, patrol_reminder, sso_quiz_field
from utils.sso_questions import get_random_quiz

class SSOPatrolModal(discord.ui.Modal, title="Заявление на совместный патруль"):
    name = name_component()

    def __init__(self, default_name: str):
        super().__init__()
        self.name.default = default_name
        self.quiz_data = get_random_quiz(3)
        self.selects = []

        for i, data in enumerate(self.quiz_data, 1):
            label, select = sso_quiz_field(data, i)
            self.add_item(label)
            self.selects.append(select)
        self.add_item(patrol_reminder())

    async def on_submit(self, interaction: discord.Interaction):
        from cogs.sso_patrol import update_bottom_message
        from ui.views.sso_patrol import SSOPatrolManagementButton

        failed_list = [
            self.quiz_data[i]['q']
            for i, s in enumerate(self.selects)
            if not s.values or s.values[0] != self.quiz_data[i]['a']
        ]

        if failed_list:
            failed_text = "\n".join(failed_list)

            new_id = await get_next_id("sso_patrol_requests")
            req = SSOPatrolRequest(
                id=new_id, user_id=interaction.user.id, full_name=self.name.value,
                reason="Провал теста", status="REJECTED"
            )
            await req.create()

            await interaction.response.send_message("### ❌ Тест не пройден", ephemeral=True)
            await interaction.channel.send(content=f"-# ||<@{interaction.user.id}>||",
                                           embed=await req.to_embed(interaction.client, failed_text))
            await update_bottom_message(interaction.client)
            return

        # Успешная подача
        new_id = await get_next_id("sso_patrol_requests")
        req = SSOPatrolRequest(id=new_id, user_id=interaction.user.id, full_name=self.name.value,
                               reason="Совместный патруль")
        await req.create()

        view = discord.ui.View(timeout=None)
        view.add_item(SSOPatrolManagementButton("approve", new_id))
        view.add_item(SSOPatrolManagementButton("reject", new_id))

        await interaction.response.send_message("### ✅ Заявление отправлено!", ephemeral=True)
        await interaction.channel.send(content=f"-# ||<@{interaction.user.id}>||",
                        embed=await req.to_embed(interaction.client), view=view)

        await update_bottom_message(interaction.client)
