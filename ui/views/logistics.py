import discord

from discord.ui import Separator

import config
from database.models import LogisticsRequest, LogisticsType, User, SupplyRequest
from texts import logistics_description
from ui.views.indicators import indicator_view
from utils.permissions import is_high_command
from utils.user_data import get_initiator


class LogisticsApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay("# 🚚 Запрос поставок"))
        container.add_item(discord.ui.TextDisplay(logistics_description))
        container.add_item(Separator())

        row = discord.ui.ActionRow()
        types = [LogisticsType.ORBITA, LogisticsType.OBJECT7, LogisticsType.WAREHOUSE]

        for t in types:
            btn = discord.ui.Button(label=t.value, custom_id=f"log_apply_{t.name}", style=discord.ButtonStyle.secondary)
            btn.callback = self.create_callback(t)
            row.add_item(btn)

        container.add_item(row)
        self.add_item(container)

    def create_callback(self, supply_type):
        async def callback(interaction: discord.Interaction):
            user_db = await User.find_one(User.discord_id == interaction.user.id)
            from ui.modals.logistics import LogisticsModal
            await interaction.response.send_modal(LogisticsModal(supply_type, user_db))

        return callback


class LogisticsManagementButton(discord.ui.DynamicItem[discord.ui.Button],
                                template=r"log_mng:(?P<act>\w+):(?P<id>\d+)"):
    status_map = {
        "approve": ("Завершить", "👍", discord.ButtonStyle.success, "Завершил"),
        "reject":  ("Отклонить", "👎", discord.ButtonStyle.danger,  "Отклонил")
    }
    def __init__(self, action: str, request_id: int):
        label, emoji, style, _ = self.status_map.get(action, ("?", None, discord.ButtonStyle.secondary))
        super().__init__(discord.ui.Button(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"log_mng:{action}:{request_id}"
        ))
        self.action, self.request_id = action, request_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match.group("act"), int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        from utils.mongo_lock import try_lock
        if not await try_lock(LogisticsRequest, self.request_id, "status", "PROCESSING", "PENDING"):
            return await interaction.response.send_message("❌ Запрос уже обработан.", ephemeral=True)

        is_supplier =any(r.id == config.RoleId.SUPPLIER.value for r in interaction.user.roles)
        is_staff = await is_high_command(interaction.user.id)
        if not is_supplier and not is_staff:
            await LogisticsRequest.get_pymongo_collection().update_one(
                {"_id": self.request_id}, {"$set": {"status": "PENDING"}}
            )
            return await interaction.response.send_message(
                "❌ У вас нет прав поставщика для этого действия.",
                ephemeral=True
            )

        req = await LogisticsRequest.find_one(LogisticsRequest.id == self.request_id)

        req.status = "APPROVED" if self.action == "approve" else "REJECTED"
        req.reviewer_name = interaction.user.display_name
        await req.save()

        _, emoji, _, prefix = self.status_map[self.action]
        await interaction.response.edit_message(
            content=f"<@{req.user_id}> <@{interaction.user.id}>",
            embed=await req.to_embed(),
            view=indicator_view(f"{prefix} {req.reviewer_name}", emoji)
        )


class LogisticsManagementView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(LogisticsManagementButton("approve", request_id))
        self.add_item(LogisticsManagementButton("reject", request_id))