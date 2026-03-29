import datetime
import re
from typing import Any

import discord
from beanie.odm.operators.find.comparison import NotIn
from discord import Interaction, InteractionResponse
from discord._types import ClientT

import config
from database import divisions
from database.models import Division, TransferRequest, User
from ui.views.indicators import indicator_view
from utils.audit import AuditAction, audit_logger
from utils.notifications import notify_transfer_approved, notify_transfer_rejected
from utils.roles import to_division, to_position
from utils.user_data import get_initiator


class TransferView(discord.ui.LayoutView):
    def __init__(self, division: Division):
        super().__init__(timeout=None)
        self.division = division

        container = discord.ui.Container()
        header_text = (
            f"## {self.division.emoji} {self.division.name} "
            f"({self.division.abbreviation})\n"
            f"{self.division.description}"
        )
        container.add_item(discord.ui.TextDisplay(header_text))
        info_text = (
            "### Важная информация:\n"
            "- Тщательно выбирайте подразделение. "
            "Не получится подать заявления в разные подразделения одновременно.\n"
            "- Заявление может рассматриватся до 72 часов."
        )
        container.add_item(discord.ui.TextDisplay(info_text))
        container.add_item(discord.ui.Separator())

        a_row = discord.ui.ActionRow()
        a_row.add_item(TransferApply(division))
        container.add_item(a_row)
        self.add_item(container)


def can_user_handle_transfer(user: User, division_ids: list[int]) -> bool:
    if (user.rank or 0) > config.RankIndex.COLONEL:
        return True

    if user.division not in division_ids:
        return False

    division = divisions.get_division(user.division)
    if not division or not division.positions:
        return False

    for position in division.positions:
        if position.name == user.position and position.privilege.value >= 2:
            return True

    return False


class TransferApply(
    discord.ui.DynamicItem[discord.ui.Button], template=r"transfer_apply:(?P<id>\d+)"
):
    def __init__(self, division: Division):
        super().__init__(
            discord.ui.Button(
                label=f"Подать заявление в {division.abbreviation}",
                emoji="📨",
                custom_id=f"transfer_apply:{division.division_id}",
                style=discord.ButtonStyle.primary,
            )
        )
        self.division = division

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        division_id = int(match.group("id"))
        return cls(divisions.get_division(division_id))

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        opened_request = await TransferRequest.find_one(
            TransferRequest.user_id == interaction.user.id,
            NotIn(TransferRequest.status, ["APPROVED", "REJECTED"]),
        )
        if opened_request is not None:
            await interaction.response.send_message(
                "### У вас уже есть открытое заявление на рассмотрении.\n"
                "Ожидайте его рассмотрения.",
                ephemeral=True,
            )
            return

        user = await get_initiator(interaction)
        if user and user.division == self.division.division_id:
            await interaction.response.send_message(
                f"### Вы уже состоите в подразделении "
                f"{self.division.abbreviation}. Подача заявления невозможна.",
                ephemeral=True,
            )
            return

        user_name = None
        if user and user.full_name:
            user_name = user.full_name

        from ui.modals.transfers import TransferModal

        await interaction.response.send_modal(
            TransferModal(destination=self.division, default_nickname=user_name)
        )


class OldApproveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"transfer:old_approve:(?P<id>\d+):(?P<div>\d+)",
):
    def __init__(self, request_id: int, division_id: int):
        self.division = divisions.get_division(division_id)
        super().__init__(
            discord.ui.Button(
                label=f"Одобрить (от {self.division.abbreviation})",
                emoji="👍",
                custom_id=f"transfer:old_approve:{request_id}:{division_id}",
                style=discord.ButtonStyle.success,
            )
        )
        self.request_id = request_id
        self.division_id = division_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        request_id = int(match.group("id"))
        division_id = int(match.group("div"))
        return cls(request_id, division_id)

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await TransferRequest.find_one(TransferRequest.id == self.request_id)
        if not request:
            await interaction.response.send_message("Запрос не найден.", ephemeral=True)
            return

        officer = await get_initiator(interaction)

        if not can_user_handle_transfer(officer, [request.old_division_id]):
            await interaction.response.send_message(
                "У вас нет прав взаимодействовать с этой кнопкой.", ephemeral=True
            )
            return

        request.status = "NEW_DIVISION_REVIEW"
        request.old_reviewer_id = interaction.user.id
        request.old_reviewed_at = datetime.datetime.now()
        await request.save()

        view = discord.ui.View(timeout=None)
        view.add_item(
            ApproveTransferButton(
                request_id=self.request_id, division_id=request.new_division_id
            )
        )
        view.add_item(RejectTransferButton(request_id=self.request_id))

        new_division = divisions.get_division(request.new_division_id)
        mentions = [f"<@{request.user_id}>"] + [
            f"<@&{pos.role_id}>"
            for pos in new_division.positions
            if pos.privilege.value >= 2
        ]

        assert isinstance(interaction.response, InteractionResponse)
        await interaction.response.edit_message(
            content="-# " + " ".join(mentions),
            embed=await request.to_embed(interaction.client),
            view=view,
        )
        await interaction.message.reply("-# ||" + " ".join(mentions) + "||")

        from cogs.transfers import update_bottom_message

        await update_bottom_message(interaction.client, interaction.channel.id)


class ApproveTransferButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"transfer:new_approve:(?P<id>\d+):(?P<div>\d+)",
):
    def __init__(self, request_id: int, division_id: int):
        self.division = divisions.get_division(division_id)
        super().__init__(
            discord.ui.Button(
                label=f"Одобрить (от {self.division.abbreviation})",
                emoji="👍",
                custom_id=f"transfer:new_approve:{request_id}:{division_id}",
                style=discord.ButtonStyle.success,
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        request_id = int(match.group("id"))
        div_id = int(match.group("div"))
        return cls(request_id, div_id)

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await TransferRequest.find_one(TransferRequest.id == self.request_id)
        if not request:
            await interaction.response.send_message("Запрос не найден.", ephemeral=True)
            return

        officer = await get_initiator(interaction)
        if not can_user_handle_transfer(officer, [request.new_division_id]):
            await interaction.response.send_message(
                "У вас нет прав взаимодействовать с этой кнопкой.", ephemeral=True
            )
            return

        request.status = "APPROVED"
        request.new_reviewer_id = interaction.user.id
        request.new_reviewed_at = datetime.datetime.now()
        await request.save()

        assert isinstance(interaction.response, InteractionResponse)
        await interaction.response.edit_message(
            embed=await request.to_embed(interaction.client),
            view=indicator_view("Одобрено", emoji="👍"),
        )

        user = await User.find_one(User.discord_id == request.user_id)
        user.division = request.new_division_id
        user.first_name, user.last_name = request.full_name.split(" ", 1)
        user.position = (
            self.division.positions[-1].name if self.division.positions else None
        )
        await user.save()

        user_discord = await interaction.client.getch_member(request.user_id)
        new_roles = to_division(user_discord.roles, self.division.division_id)
        new_roles = to_position(new_roles, user.division, user.position)
        await user_discord.edit(
            nick=user.discord_nick,
            roles=new_roles,
            reason=f"Одобрен перевод by {interaction.user.id}",
        )

        action = (
            AuditAction.DIVISION_ASSIGNED
            if not divisions.get_division(request.old_division_id).positions
            else AuditAction.DIVISION_CHANGED
        )
        await audit_logger.log_action(
            action=action, initiator=interaction.user, target=user.discord_id
        )

        # Уведомление в ЛС
        await notify_transfer_approved(
            interaction.client, request.user_id, self.division.name
        )


class RejectTransferButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"transfer:new_reject:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Отклонить",
                emoji="👎",
                custom_id=f"transfer:new_reject:{request_id}",
                style=discord.ButtonStyle.danger,
            )
        )
        self.request_id = request_id

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
        request = await TransferRequest.find_one(TransferRequest.id == self.request_id)
        if not request:
            await interaction.response.send_message("Запрос не найден.", ephemeral=True)
            return

        officer = await get_initiator(interaction)

        if not can_user_handle_transfer(
            officer, [request.old_division_id, request.new_division_id]
        ):
            await interaction.response.send_message(
                "У вас нет прав взаимодействовать с этой кнопкой.", ephemeral=True
            )
            return

        if officer.division == request.old_division_id:
            request.old_reviewer_id = interaction.user.id
            request.old_reviewed_at = datetime.datetime.now()
        else:
            request.new_reviewer_id = interaction.user.id
            request.new_reviewed_at = datetime.datetime.now()

        modal = discord.ui.Modal(title="Отклонение заявления на перевод")
        reason_input = discord.ui.TextInput(
            label="Причина отклонения",
            style=discord.TextStyle.paragraph,
            placeholder="Введите причину отклонения заявления",
            required=True,
            max_length=500,
        )
        modal.add_item(reason_input)

        async def on_modal_submit(modal_interaction: discord.Interaction):
            reason = reason_input.value
            request.reject_reason = reason
            request.status = "REJECTED"
            await request.save()

            assert isinstance(interaction.response, InteractionResponse)
            await modal_interaction.response.edit_message(
                embed=await request.to_embed(interaction.client),
                view=indicator_view("Отклонено", emoji="👎"),
            )

            # Уведомление в ЛС
            await notify_transfer_rejected(interaction.client, request.user_id, reason)

        modal.on_submit = on_modal_submit
        await interaction.response.send_modal(modal)
