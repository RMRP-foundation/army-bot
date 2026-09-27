import discord
from discord.ui import Separator

from core.exceptions import ServiceError
from database.models import LogisticsType, User
from services.logistics import LogisticsService
from texts import logistics_description
from ui.modals.logistics import LogisticsModal
from utils.helpers import safe_respond, safe_edit_message, random_loading_message, build_mentions


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
            btn = discord.ui.Button(
                label=t.value,
                custom_id=f"log_apply_{t.name}",
                style=discord.ButtonStyle.secondary,
            )
            btn.callback = self._create_callback(t)
            row.add_item(btn)

        container.add_item(row)
        self.add_item(container)

    @staticmethod
    def _create_callback(supply_type: LogisticsType):
        async def callback(interaction: discord.Interaction):
            user_db = await User.get_by_discord_id(interaction.user.id)
            default_nick = user_db.full_name if user_db and user_db.full_name else ""
            await interaction.response.send_modal(LogisticsModal(supply_type, default_nickname=default_nick))

        return callback


class LogisticsManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"log_mng:(?P<act>\w+):(?P<id>\d+)",
):
    _config = {
        "approve": ("Завершить", "👍", discord.ButtonStyle.success),
        "reject": ("Отклонить", "👎", discord.ButtonStyle.danger),
    }

    def __init__(self, action: str, request_id: int):
        label, emoji, style = self._config.get(action, (action, None, discord.ButtonStyle.secondary))
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"log_mng:{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match.group("act"), int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            await safe_respond(interaction, random_loading_message())
            result = await LogisticsService.process_logistics(
                interaction=interaction, request_id=self.request_id, action=self.action,
            )
            await safe_edit_message(
                message=interaction.message,
                content=build_mentions([result.request.user_id, interaction.user.id]),
                embed=result.embed,
                view=result.view,
            )
            await safe_respond(interaction, result.message)
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class LogisticsManagementView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(LogisticsManagementButton("approve", request_id))
        self.add_item(LogisticsManagementButton("reject", request_id))