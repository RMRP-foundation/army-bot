import discord
from database.models import LogisticsRequest, LogisticsType, User
from database.counters import get_next_id
import config
from utils.user_data import update_user_name_if_changed


class LogisticsModal(discord.ui.Modal):
    def __init__(self, supply_type: LogisticsType, user_db: User | None):
        super().__init__(title=f"Запрос: {supply_type.value}")
        self.supply_type = supply_type

        self.nickname = discord.ui.TextInput(
            label="Ваше имя и фамилия",
            default=getattr(user_db, "full_name", "") or "",
            placeholder="Иван Иванов",
            max_length=32
        )
        self.faction = discord.ui.TextInput(
            label="Ваша организация",
            placeholder="Например: ФСВНГ",
            max_length=20
        )
        self.add_item(self.nickname)
        self.add_item(self.faction)

    async def on_submit(self, interaction: discord.Interaction):
        user_db = await User.find_one(User.discord_id == interaction.user.id)
        if not user_db:
            user_db = User(discord_id=interaction.user.id, pre_inited=True)
        await update_user_name_if_changed(user_db, self.nickname.value)

        new_id = await get_next_id("logistics_requests")
        req = LogisticsRequest(
            id=new_id,
            user_id=interaction.user.id,
            nickname=self.nickname.value,
            faction=self.faction.value,
            supply_type=self.supply_type
        )
        await req.create()

        from ui.views.logistics import LogisticsManagementView
        channel = interaction.client.get_channel(config.CHANNELS["logistics"])

        supplier = config.RoleId.SUPPLIER.value
        msg = await channel.send(
            content=f"-# ||<@&{supplier}> <@{interaction.user.id}>||",
            embed=await req.to_embed(),
            view=LogisticsManagementView(new_id)
        )

        req.message_id = msg.id
        await req.save()

        await interaction.response.send_message("✅ Ваш запрос на поставку отправлен.", ephemeral=True)

        from cogs.logistics import update_bottom_message
        await update_bottom_message(interaction.client)