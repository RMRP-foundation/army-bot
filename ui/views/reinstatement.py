import logging
import re
from typing import Any

import discord
from discord import Interaction, InteractionResponse, SelectOption
from discord._types import ClientT

import config
import texts
from config import RANKS
from database import divisions
from database.models import ReinstatementRequest, User
from ui.views.indicators import indicator_view
from utils.audit import AuditAction, audit_logger
from utils.notifications import (
    notify_reinstatement_approved,
    notify_reinstatement_rejected,
)
from utils.roles import to_division, to_position, to_rank
from utils.user_data import (
    get_full_name,
    get_initiator,
    update_user_name_if_changed,
)

logger = logging.getLogger(__name__)


async def button_callback(interaction: discord.Interaction):
    user = await get_initiator(interaction)
    if not user or user.rank is None:
        await interaction.response.send_message(
            "### Вы не состоите на службе "
            "и не можете подать заявление на восстановление.",
            ephemeral=True,
        )
        return

    from ui.modals.reinstatement import ReinstatementModal

    modal = ReinstatementModal(await get_full_name(interaction))
    await interaction.response.send_modal(modal)


class ReinstatementApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(texts.reinstatement_title))
    container.add_item(discord.ui.TextDisplay(texts.reinstatement_submission))
    container.add_item(discord.ui.TextDisplay(texts.reinstatement_requirements))
    container.add_item(discord.ui.TextDisplay(texts.reinstatement_system))

    container.add_item(discord.ui.Separator(visible=True))

    button = discord.ui.Button(
        label="Подать заявление на восстановление",
        emoji="📨",
        style=discord.ButtonStyle.primary,
        custom_id="reinstatement_apply_button",
    )
    button.callback = button_callback

    action_row = discord.ui.ActionRow()
    action_row.add_item(button)
    container.add_item(action_row)


async def interaction_check(interaction: Interaction[ClientT], /) -> bool:
    user = await get_initiator(interaction)
    if (user.rank or 0) >= 14:
        return True
    div = divisions.get_division(user.division) if user.division else None
    if div and div.abbreviation == "УВП":
        return True
    return False


class ReinstatementRankSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"select_reinstatement_rank:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Select(
                placeholder="👍 Одобрить на звание...",
                custom_id=f"select_reinstatement_rank:{request_id}",
                options=[
                    SelectOption(label=rank, value=str(index + 4))
                    for index, rank in enumerate(config.AVAILABLE_FOR_REINSTATEMENT)
                ],
            )
        )
        self.request_id = request_id
        self.interaction_check = interaction_check

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match: re.Match[str],
    ):
        request_id = int(match.group("id"))
        return cls(request_id)

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await ReinstatementRequest.find_one(
            ReinstatementRequest.id == self.request_id
        )
        request.approved = True
        request.checked = True
        request.rank = int(self.item.values[0])
        await request.save()
        assert isinstance(interaction.response, InteractionResponse)
        await interaction.response.edit_message(
            embed=await request.to_embed(),
            view=indicator_view(f"Одобрил {interaction.user.display_name}", emoji="👍"),
        )

        user = await User.find_one(User.discord_id == request.user)

        user.rank = request.rank
        user.division = 0
        await update_user_name_if_changed(
            user, request.data.full_name, interaction.user
        )
        await user.save()

        user_discord = await interaction.client.getch_member(request.user)

        remove_roles = [
            config.RoleId.ATTESTATION.value,
            config.RoleId.REINFORCEMENT.value,
        ]
        new_user_roles = [
            role for role in user_discord.roles if role.id not in remove_roles
        ]
        new_user_roles = to_division(new_user_roles, user.division)
        new_user_roles = to_rank(new_user_roles, user.rank)
        new_user_roles = to_position(new_user_roles, user.division, user.position)

        await user_discord.edit(
            nick=user.discord_nick,
            roles=new_user_roles,
            reason=f"Одобрено восстановление by {interaction.user.id}",
        )

        await audit_logger.log_action(
            action=AuditAction.REINSTATEMENT,
            initiator=interaction.user,
            target=user.discord_id,
        )

        # Уведомление в ЛС
        rank_name = RANKS[request.rank] if request.rank is not None else "Неизвестно"
        await notify_reinstatement_approved(interaction.client, request.user, rank_name)


basic_roles = config.RoleId.ATTESTATION, config.RoleId.REINFORCEMENT


class ApproveReinstatementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"approve_reinstatement:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Принять",
                emoji="👍",
                custom_id=f"approve_reinstatement:{request_id}",
                style=discord.ButtonStyle.primary,
            )
        )
        self.request_id = request_id
        self.interaction_check = interaction_check

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        request_id = int(match.group("id"))
        return cls(request_id)

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await ReinstatementRequest.find_one(
            ReinstatementRequest.id == self.request_id
        )
        if not request:
            await interaction.response.send_message("Запрос не найден.", ephemeral=True)
            return

        request.approved = True
        request.checked = False

        try:
            for role in basic_roles:
                await interaction.client.http.add_role(
                    guild_id=interaction.guild.id,
                    user_id=request.user,
                    role_id=role.value,
                )
        except Exception as e:
            await interaction.response.send_message(
                f"Не удалось выдать роли: {e}", ephemeral=True
            )
            return

        await request.save()

        assert isinstance(interaction.response, InteractionResponse)

        view = discord.ui.View(timeout=None)
        view.add_item(ReinstatementRankSelect(request_id=self.request_id))
        view.add_item(RejectReinstatementButton(request_id=self.request_id))

        await interaction.response.edit_message(
            content=f"-# ||<@{request.user}> <@{interaction.user.id}>||",
            embed=await request.to_embed(),
            view=view,
        )


class RejectReinstatementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"reject_reinstatement:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Отклонить",
                emoji="👎",
                custom_id=f"reject_reinstatement:{request_id}",
                style=discord.ButtonStyle.danger,
            )
        )
        self.request_id = request_id
        self.interaction_check = interaction_check

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        request_id = int(match.group("id"))
        return cls(request_id)

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await ReinstatementRequest.find_one(
            ReinstatementRequest.id == self.request_id
        )
        if not request:
            await interaction.response.send_message("Запрос не найден.", ephemeral=True)
            return

        request.approved = False
        request.checked = True
        await request.save()

        try:
            for role in basic_roles:
                await interaction.client.http.remove_role(
                    guild_id=interaction.guild.id,
                    user_id=request.user,
                    role_id=role.value,
                )
        except discord.HTTPException as e:
            logger.warning(
                f"Failed to remove roles from rejected user {request.user}: {e}"
            )

        assert isinstance(interaction.response, InteractionResponse)
        await interaction.response.edit_message(
            embed=await request.to_embed(),
            view=indicator_view(
                f"Отклонил {interaction.user.display_name}", emoji="👎"
            ),
        )

        # Уведомление в ЛС
        await notify_reinstatement_rejected(interaction.client, request.user)
