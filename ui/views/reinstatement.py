import discord
from discord import SelectOption

from core import constants
import texts
from core.exceptions import ServiceError
from database.models import User, ReinstatementRequest
from services.authorization import AuthorizationService
from services.reinstatement import ReinstatementService
from ui.modals.reinstatement import ReinstatementModal
from utils.helpers import safe_respond, random_loading_message, safe_edit_message, build_mentions


async def _open_reinstatement_modal(interaction: discord.Interaction) -> None:
    """Проверяет возможность подачи заявления на восстановление и открывает модалку."""
    try:
        user_db = await AuthorizationService.require_active_soldier(interaction)
        await ReinstatementService.validate_no_active_request(user_db.discord_id)
        await interaction.response.send_modal(ReinstatementModal(user_db))
    except ServiceError as error:
        await safe_respond(interaction, error.message)


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
    button.callback = _open_reinstatement_modal

    action_row = discord.ui.ActionRow()
    action_row.add_item(button)
    container.add_item(action_row)


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
                    for index, rank in enumerate(constants.AVAILABLE_FOR_REINSTATEMENT)
                ],
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message(), ephemeral=True)
            selected_rank = int(self.item.values[0])
            result = await ReinstatementService.approve_reinstatement(
                interaction=interaction, request_id=self.request_id, rank_index=selected_rank, officer=officer,
            )
            await safe_edit_message(message=interaction.message, embed=result.embed, view=result.view)
            await safe_respond(interaction, "✅ Заявление на восстановление одобрено.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class ReinstatementManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"reinst_(?P<action>\w+):(?P<id>\d+)",
):
    _config = {
        "approve": ("Принять", discord.ButtonStyle.success, "👍"),
        "reject": ("Отклонить", discord.ButtonStyle.danger, "👎"),
    }

    def __init__(self, action: str, request_id: int):
        label, style, emoji = self._config.get(action, (action, discord.ButtonStyle.secondary, None))
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"reinst_{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match.group("action"), int(match.group("id")))

    async def _handle_reject_modal(self, interaction: discord.Interaction, officer: User) -> None:
        req = await ReinstatementRequest.find_one(ReinstatementRequest.id == self.request_id)
        if not req or req.status not in ("PENDING", "ATTESTATION"):
            await safe_respond(interaction, f"❌ Заявление #{self.request_id} не найдено или уже обработано.")
            return

        try:
            ReinstatementService.validate_reviewer_permissions(officer, req.user)
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return

        modal = discord.ui.Modal(title="Отклонение заявления")
        reason_input = discord.ui.TextInput(
            label="Причина отклонения", style=discord.TextStyle.paragraph,
            placeholder="Введите причину отказа...", required=True, max_length=500,
        )
        modal.add_item(reason_input)

        async def on_submit(modal_inter: discord.Interaction):
            try:
                result = await ReinstatementService.reject_reinstatement(
                    interaction=modal_inter, request_id=self.request_id, officer=officer,
                    reason=reason_input.value.strip(),
                )
            except ServiceError as error:
                await safe_respond(modal_inter, error.message)
                return

            await modal_inter.response.edit_message(embed=result.embed, view=result.view)

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)

            if self.action == "reject":
                await self._handle_reject_modal(interaction, officer)
            elif self.action == "approve":
                await safe_respond(interaction, random_loading_message())
                result = await ReinstatementService.start_attestation(interaction=interaction,
                                                                      request_id=self.request_id, officer=officer)
                await safe_edit_message(
                    message=interaction.message,
                    content=build_mentions(result.mention_ids),
                    embed=result.embed,
                    view=result.view,
                )
                await safe_respond(interaction, "✅ Кандидат переведён на этап переаттестации.")

        except ServiceError as error:
            await safe_respond(interaction, error.message)


class ReinstatementManagementView(discord.ui.View):
    """Вьюха для первичной заявки на восстановление (Принять / Отклонить)."""

    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(ReinstatementManagementButton("approve", request_id))
        self.add_item(ReinstatementManagementButton("reject", request_id))


class ReinstatementAttestationView(discord.ui.View):
    """Вьюха для этапа переаттестации (Выбор звания + Отклонить)."""

    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(ReinstatementRankSelect(request_id=request_id))
        self.add_item(ReinstatementManagementButton("reject", request_id))


class LegacyApproveReinstatementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"approve_reinstatement:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Принять",
                emoji="👍",
                style=discord.ButtonStyle.success,
                custom_id=f"approve_reinstatement:{request_id}",
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message())
            result = await ReinstatementService.start_attestation(
                interaction=interaction, request_id=self.request_id, officer=officer,
            )
            await safe_edit_message(
                message=interaction.message, content=build_mentions(result.mention_ids),
                embed=result.embed, view=result.view,
            )
            await safe_respond(interaction, "✅ Кандидат переведён на этап переаттестации.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class LegacyRejectReinstatementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"reject_reinstatement:(?P<id>\d+)",
):
    def __init__(self, request_id: int):
        super().__init__(
            discord.ui.Button(
                label="Отклонить",
                emoji="👎",
                style=discord.ButtonStyle.danger,
                custom_id=f"reject_reinstatement:{request_id}",
            )
        )
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)
            btn = ReinstatementManagementButton("reject", self.request_id)
            await btn._handle_reject_modal(interaction, officer)
        except ServiceError as error:
            await safe_respond(interaction, error.message)