import discord

from core import config
from core.exceptions import ServiceError
from database import divisions
from database.models import User, PromotionRequest
from services.authorization import AuthorizationService
from services.promotion import PromotionService
from texts import promotion_description, promotion_title
from ui.modals.promotion import PromotionRequestModal
from utils.helpers import safe_respond, safe_edit_message, random_loading_message, build_mentions, safe_delete_message


class PromotionManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"promotion:(?P<action>approve|reject|cancel):(?P<id>\d+)",
):
    _config = {
        "approve": ("Одобрить", discord.ButtonStyle.success, "👍"),
        "reject": ("Отклонить", discord.ButtonStyle.danger, "👎"),
        "cancel": ("Отменить", discord.ButtonStyle.grey, None),
    }

    def __init__(self, action: str, report_id: int):
        label, style, emoji = self._config[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                emoji=emoji,
                custom_id=f"promotion:{action}:{report_id}",
            )
        )
        self.action = action
        self.report_id = report_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match.group("action"), int(match.group("id")))

    async def _handle_reject_modal(self, interaction: discord.Interaction, officer: User) -> None:
        req = await PromotionRequest.find_one(PromotionRequest.id == self.report_id)
        if not req or req.status not in ("PENDING", "APPROVED"):
            await safe_respond(interaction, f"❌ Рапорт #{self.report_id} не найден или уже обработан.")
            return

        div = divisions.get_division(req.division_id)
        if not div:
            await safe_respond(interaction, "❌ Подразделение рапорта не найдено.")
            return

        try:
            PromotionService.validate_can_review(officer, div, req)
        except ServiceError as error:
            await safe_respond(interaction, error.message)
            return

        modal = discord.ui.Modal(title="Отклонение рапорта на повышение")
        reason_input = discord.ui.TextInput(
            label="Причина отклонения", style=discord.TextStyle.paragraph,
            placeholder="Введите причину отклонения", required=True, max_length=500,
        )
        modal.add_item(reason_input)

        async def on_submit(modal_inter: discord.Interaction):
            await modal_inter.response.defer()
            try:
                result = await PromotionService.reject_promotion(
                    interaction=modal_inter, request_id=self.report_id, reviewer=officer,
                    reason=reason_input.value.strip(),
                )
            except ServiceError as error:
                await safe_respond(modal_inter, error.message)
                return

            await modal_inter.edit_original_response(
                content=build_mentions(result.mention_ids), embed=result.embed, view=result.view,
            )

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    async def callback(self, interaction: discord.Interaction):
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)

            if self.action == "reject":
                await self._handle_reject_modal(interaction, officer)
                return

            await safe_respond(interaction, random_loading_message())

            if self.action == "approve":
                result = await PromotionService.approve_promotion(interaction, self.report_id, officer)
                await safe_edit_message(
                    message=interaction.message, content=build_mentions(result.mention_ids),
                    embed=result.embed, view=result.view,
                )
                await safe_respond(interaction, result.message)
            elif self.action == "cancel":
                await PromotionService.cancel_promotion(self.report_id, interaction.user.id)
                await safe_delete_message(interaction.message)
                await safe_respond(interaction, "✅ Ваш рапорт был отменен.")

        except ServiceError as error:
            await safe_respond(interaction, error.message)


class PromoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"promotion:promote:(?P<id>\d+)",
):
    def __init__(self, report_id: int):
        super().__init__(
            discord.ui.Button(
                label="Повысить",
                emoji="⭐",
                style=discord.ButtonStyle.primary,
                custom_id=f"promotion:promote:{report_id}",
            )
        )
        self.report_id = report_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        try:
            promoter = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message())

            result = await PromotionService.promote_soldier(
                interaction=interaction, request_id=self.report_id, promoter=promoter,
            )
            await safe_edit_message(
                message=interaction.message,
                content=build_mentions(result.mention_ids),
                embed=result.embed, view=result.view,
            )
            await safe_respond(interaction, result.message)

        except ServiceError as error:
            await safe_respond(interaction, error.message)


async def _promotion_apply_callback(interaction: discord.Interaction) -> None:
    """Проверяет возможность подачи рапорта и открывает модалку."""
    try:
        user_db = await AuthorizationService.require_active_soldier(interaction)

        div = next(
            (d for d in divisions.divisions if d.promotion_channel == interaction.channel_id),
            None,
        )
        if not div:
            raise ServiceError("❌ Канал повышений для данного подразделения не настроен.")

        if (user_db.rank or 0) >= config.RankIndex.CAPTAIN:
            raise ServiceError("❌ Повышение через рапорт доступно только до звания Капитан.")

        if user_db.division != div.division_id:
            raise ServiceError("❌ Вы можете подавать рапорт только в своём подразделении.")

        await interaction.response.send_modal(PromotionRequestModal(div, user_db))

    except ServiceError as error:
        await safe_respond(interaction, error.message)


class PromotionApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(promotion_title))
        container.add_item(discord.ui.TextDisplay(promotion_description))
        container.add_item(discord.ui.Separator())

        btn = discord.ui.Button(
            label="Подать рапорт",
            emoji="📨",
            style=discord.ButtonStyle.primary,
            custom_id="promotion_apply_button",
        )
        btn.callback = _promotion_apply_callback

        row = discord.ui.ActionRow()
        row.add_item(btn)
        container.add_item(row)
        self.add_item(container)


class PromotionManagementView(discord.ui.View):
    """Панель управления рапортом на повышение. Набор кнопок собирается из action-строк."""
    def __init__(self, report_id: int, *actions: str):
        super().__init__(timeout=None)
        for action in actions:
            if action == "promote":
                self.add_item(PromoteButton(report_id))
            else:
                self.add_item(PromotionManagementButton(action, report_id))