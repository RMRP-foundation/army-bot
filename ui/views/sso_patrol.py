import re

import discord

from core.exceptions import ServiceError
from services.authorization import AuthorizationService
from services.sso_patrol import SSOPatrolService
from texts import patrol_rules, patrol_title
from ui.modals.sso_patrol import SSOPatrolModal
from utils.helpers import safe_respond, safe_edit_message, build_mentions, random_loading_message


class SSOPatrolApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(patrol_title))
        container.add_item(discord.ui.TextDisplay(patrol_rules))
        container.add_item(discord.ui.Separator())

        btn = discord.ui.Button(
            label="Подать заявление",
            style=discord.ButtonStyle.primary,
            custom_id="sso_apply_btn",
            emoji="📨",
        )
        btn.callback = self.on_apply

        row = discord.ui.ActionRow()
        row.add_item(btn)
        container.add_item(row)
        self.add_item(container)

    async def on_apply(self, interaction: discord.Interaction):
        try:
            user_db = await AuthorizationService.require_active_soldier(interaction)
            await SSOPatrolService.validate_can_apply(user_db)
            await interaction.response.send_modal(SSOPatrolModal(user_db))
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class SSOPatrolManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"sso_mng:(?P<action>\w+):(?P<id>\d+)",
):
    def __init__(self, action: str, request_id: int):
        labels = {"approve": "Одобрить", "reject": "Отклонить"}
        styles = {"approve": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger}
        emojis = {"approve": "👍", "reject": "👎"}

        super().__init__(
            discord.ui.Button(
                label=labels.get(action, action),
                emoji=emojis.get(action),
                style=styles.get(action, discord.ButtonStyle.secondary),
                custom_id=f"sso_mng:{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message(), ephemeral=True)

            result = await SSOPatrolService.process_sso_patrol(
                interaction=interaction, request_id=self.request_id, action=self.action, officer=officer,
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


class SSOPatrolManagementView(discord.ui.View):
    """Вьюха для управления заявкой на патруль ССО (Одобрить / Отклонить)."""

    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(SSOPatrolManagementButton("approve", request_id))
        self.add_item(SSOPatrolManagementButton("reject", request_id))