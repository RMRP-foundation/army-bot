import re

import discord

from core.exceptions import ServiceError
from database import divisions
from database.models import Division, TransferRequest, User
from services.authorization import AuthorizationService
from services.transfers import TransferService
from ui.modals.transfers import TransferModal
from utils.helpers import safe_respond, safe_edit_message, random_loading_message, safe_delete_message


class TransferApplyView(discord.ui.LayoutView):
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
        a_row.add_item(TransferApplyButton(division))
        container.add_item(a_row)
        self.add_item(container)


class TransferApplyButton(
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
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        division_id = int(match.group("id"))
        return cls(divisions.get_division(division_id))

    async def callback(self, interaction: discord.Interaction):
        try:
            user_db = await AuthorizationService.require_active_soldier(interaction)
            await TransferService.validate_no_active_request(user_db.discord_id)
            await TransferService.validate_transfer_rules(user_db, self.division)
            await interaction.response.send_modal(TransferModal(destination=self.division, user_db=user_db))
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class ApproveOldDivisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"transfer:old_approve:(?P<id>\d+):(?P<div>\d+)",
):
    def __init__(self, request_id: int, division_id: int):
        self.division = divisions.get_division(division_id)
        abbr = self.division.abbreviation if self.division else "подразделения"
        super().__init__(
            discord.ui.Button(
                label=f"Одобрить (от {abbr})",
                emoji="👍",
                custom_id=f"transfer:old_approve:{request_id}:{division_id}",
                style=discord.ButtonStyle.success,
            )
        )
        self.request_id = request_id
        self.division_id = division_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match.group("id")), int(match.group("div")))

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message())
            result = await TransferService.approve_old_division(interaction, self.request_id,
                                                                officer)
            await safe_edit_message(message=interaction.message, content=result.mention_content, embed=result.embed,
                                    view=result.view)
            if result.reply_content:
                await interaction.message.reply(result.reply_content)

            await safe_respond(interaction, "✅ Перевод передан на рассмотрение новому подразделению.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class ApproveNewDivisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"transfer:new_approve:(?P<id>\d+):(?P<div>\d+)",
):
    def __init__(self, request_id: int, division_id: int):
        self.division = divisions.get_division(division_id)
        abbr = self.division.abbreviation if self.division else "подразделения"
        super().__init__(
            discord.ui.Button(
                label=f"Одобрить (от {abbr})",
                emoji="👍",
                custom_id=f"transfer:new_approve:{request_id}:{division_id}",
                style=discord.ButtonStyle.success,
            )
        )
        self.request_id = request_id
        self.division_id = division_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match.group("id")), int(match.group("div")))

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message())
            result = await TransferService.approve_new_division(interaction, self.request_id,
                                                                officer)
            await safe_edit_message(message=interaction.message, content=result.mention_content, embed=result.embed,
                                    view=result.view)
            if result.reply_content:
                await interaction.message.reply(result.reply_content)

            await safe_respond(interaction, "✅ Перевод в новое подразделение успешно одобрен.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class RejectTransferButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"transfer_reject:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(discord.ui.Button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id=f"transfer_reject:{request_id}"))
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return

        req = await TransferRequest.find_one(TransferRequest.id == self.request_id)
        if not req or req.status not in ("OLD_DIVISION_REVIEW", "NEW_DIVISION_REVIEW"):
            await safe_respond(interaction, f"❌ Запрос #{self.request_id} не найден или уже обработан.")
            return

        try:
            TransferService.validate_reviewer_permissions(
                officer, [req.old_division_id, req.new_division_id], "❌ У вас нет прав отклонять данное заявление.",
            )
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return

        modal = discord.ui.Modal(title="Отклонение заявления на перевод")
        reason_input = discord.ui.TextInput(
            label="Причина отклонения", style=discord.TextStyle.paragraph,
            placeholder="Введите причину отклонения заявления", required=True, max_length=500,
        )
        modal.add_item(reason_input)

        async def on_submit(modal_inter: discord.Interaction):
            try:
                result = await TransferService.reject_transfer(
                    interaction=modal_inter, request_id=self.request_id, officer=officer, reason=reason_input.value.strip(),
                )
            except ServiceError as error:
                await safe_respond(modal_inter, error.message)
                return

            await modal_inter.response.edit_message(embed=result.embed, view=result.view)

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)


class CancelTransferButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"transfer_cancel:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(discord.ui.Button(label="Отменить", style=discord.ButtonStyle.grey, custom_id=f"transfer_cancel:{request_id}"))
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            await TransferService.cancel_transfer(self.request_id, interaction.user.id)
            await safe_delete_message(interaction.message)
            await safe_respond(interaction, "✅ Заявка на перевод отменена.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class TransferManagementView(discord.ui.View):
    def __init__(self, request: TransferRequest, destination: Division):
        super().__init__(timeout=None)

        if request.status == "NEW_DIVISION_REVIEW":
            self.add_item(ApproveNewDivisionButton(request_id=request.id, division_id=destination.division_id))
        else:
            self.add_item(ApproveOldDivisionButton(request_id=request.id, division_id=request.old_division_id))

        self.add_item(RejectTransferButton(request_id=request.id))
        self.add_item(CancelTransferButton(request_id=request.id))