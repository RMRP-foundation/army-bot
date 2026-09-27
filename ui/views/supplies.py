import logging
import re

import discord
from discord import Interaction

from core import config
from core.constants import SUPPLY_ITEMS
from core.exceptions import ServiceError
from database.models import SupplyRequest, User
from services.authorization import AuthorizationService
from services.supplies import SupplyService
from ui.embeds.supplies import supplies_embed
from ui.modals.supplies import ItemAmountModal
from utils.helpers import safe_respond, random_loading_message, safe_edit_message
from utils.permissions import is_senior_officer

logger = logging.getLogger(__name__)


class ItemSelectView(discord.ui.View):
    """Меню выбора предмета из категории."""

    def __init__(
        self,
        category: str,
        request: SupplyRequest,
        parent_view: "SupplyBuilderView",
    ):
        super().__init__(timeout=60)
        self.request = request
        self.parent_view = parent_view

        options = []
        items = SUPPLY_ITEMS[category]
        for item in items:
            current_qty = self.request.items.get(item, 0)
            desc = f"В корзине: {current_qty}" if current_qty > 0 else "Нет в корзине"
            options.append(discord.SelectOption(label=item, description=desc))

        select = discord.ui.Select(
            placeholder="Выберите предмет...",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        item_name = interaction.data["values"][0]  # type: ignore
        current_qty = self.request.items.get(item_name, 0)

        modal = ItemAmountModal(item_name, current_qty)
        await interaction.response.send_modal(modal)
        await modal.wait()

        if modal.result is not None:
            new_qty = modal.result
            temp_items = self.request.items.copy()
            if new_qty == 0:
                temp_items.pop(item_name, None)
            else:
                temp_items[item_name] = new_qty

            try:
                SupplyService.validate_limits(temp_items)
            except ServiceError as error:
                await safe_respond(interaction, error.message)
                return

            self.request.items = temp_items
            await self.request.set({"items": self.request.items})
            await self.parent_view.refresh_embed(self.parent_view.original_interaction)
            try:
                await interaction.delete_original_response()
            except discord.NotFound:
                pass


class CategorySelectButton(discord.ui.Button):
    def __init__(self, category: str, request: SupplyRequest):
        super().__init__(label=category, style=discord.ButtonStyle.secondary)
        self.category = category
        self.request = request

    async def callback(self, interaction: discord.Interaction):
        await safe_respond(interaction, f"⌛ Открываем категорию: {self.category}...", ephemeral=True)
        view = ItemSelectView(self.category, self.request, self.view)  # type: ignore
        await interaction.edit_original_response(content=f"📂 Категория: **{self.category}**", view=view)


class SupplyBuilderView(discord.ui.View):
    def __init__(
        self,
        request: SupplyRequest,
        original_interaction: discord.Interaction,
        is_edit_mode: bool = False,
    ):
        super().__init__(timeout=900)
        self.request = request
        self.original_interaction = original_interaction
        self.is_edit_mode = is_edit_mode
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()

        for cat_name in SUPPLY_ITEMS.keys():
            self.add_item(CategorySelectButton(cat_name, self.request))

        if self.request.items:
            clear_btn = discord.ui.Button(
                label="Очистить всё", style=discord.ButtonStyle.grey, emoji="🗑", row=2
            )
            clear_btn.callback = self.clear_cart_callback
            self.add_item(clear_btn)

        cancel_btn = discord.ui.Button(
            label="Отмена", style=discord.ButtonStyle.danger, row=2
        )
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)

        label = "Сохранить изменения" if self.is_edit_mode else "Отправить заявку"
        style = discord.ButtonStyle.success
        submit_btn = discord.ui.Button(label=label, style=style, row=2)
        submit_btn.callback = self.submit_callback
        self.add_item(submit_btn)

    async def refresh_embed(self, interaction: discord.Interaction):
        self.update_buttons()
        target_user = await User.get_by_discord_id(self.request.user_id)
        embed = supplies_embed(self.request, target_user)

        if self.is_edit_mode:
            embed.title = f"🛠 Редактирование заявки #{self.request.id}"
        elif self.request.status == "DRAFT":
            embed.title = "🛠 Создание заявки на склад"
            embed.set_footer(text="Выберите категорию, чтобы добавить предметы.")

        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
        except discord.HTTPException as e:
            logger.debug(f"Failed to refresh supply embed: {e}")

    async def clear_cart_callback(self, interaction: discord.Interaction):
        self.request.items = {}
        await self.request.set({"items": {}})
        await self.refresh_embed(interaction)

    async def cancel_callback(self, interaction: discord.Interaction):
        if not self.is_edit_mode and self.request.status == "DRAFT":
            await self.request.delete()
        await interaction.response.edit_message(
            content="❌ Действие отменено.", embed=None, view=None
        )

    async def submit_callback(self, interaction: discord.Interaction):
        try:
            user_db = await AuthorizationService.require_active_soldier(interaction)
            await safe_respond(interaction, random_loading_message(), ephemeral=True)

            if self.is_edit_mode:
                await SupplyService.update_supply_items(interaction=interaction, request=self.request,
                                                        items=self.request.items)
                await safe_respond(interaction, "✅ Изменения сохранены.")
            else:
                await SupplyService.submit_supply(
                    interaction=interaction, request=self.request, items=self.request.items,
                    user_db=user_db, view_factory=SupplyManagementView,
                )
                try:
                    await self.original_interaction.delete_original_response()
                except discord.NotFound:
                    pass
                await safe_respond(interaction, "✅ Заявка успешно отправлена!")

        except ServiceError as error:
            await safe_respond(interaction, error.message)


class SupplyManageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"supply_(?P<action>\w+):(?P<id>\d+)",
):
    def __init__(self, action: str, request_id: int):
        labels = {"approve": "Выдать", "reject": "Отклонить", "edit": "Редактировать"}
        styles = {
            "approve": discord.ButtonStyle.success,
            "reject": discord.ButtonStyle.danger,
            "edit": discord.ButtonStyle.primary,
        }
        emojis = {"approve": "✅", "reject": "❌", "edit": "✏️"}

        super().__init__(
            discord.ui.Button(
                label=labels.get(action, action),
                style=styles.get(action, discord.ButtonStyle.secondary),
                emoji=emojis.get(action),
                custom_id=f"supply_{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: Interaction) -> None:
        try:
            officer = await AuthorizationService.require_active_soldier(interaction)

            req = await SupplyRequest.find_one(SupplyRequest.id == self.request_id)
            if not req or req.status != "PENDING":
                raise ServiceError(f"❌ Заявка #{self.request_id} не найдена или уже обработана.")

            if self.action == "edit":
                is_author = officer.discord_id == req.user_id
                is_staff = is_senior_officer(officer)
                if not (is_author or is_staff):
                    raise ServiceError(f"❌ Редактирование доступно только автору или со звания {config.RANKS[config.RankIndex.MAJOR]}+.")

                view = SupplyBuilderView(req, interaction, is_edit_mode=True)
                target_user = await User.get_by_discord_id(req.user_id)
                embed = supplies_embed(req, target_user)
                embed.title = f"🛠 Редактирование заявки #{req.id}"
                embed.set_footer(text="Режим редактирования (Майор+)")
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                return

            await safe_respond(interaction, random_loading_message())

            if self.action == "approve":
                result = await SupplyService.approve_supply(interaction, self.request_id, officer)
            else:
                result = await SupplyService.reject_supply(interaction, self.request_id, officer)

            await safe_edit_message(message=interaction.message, embed=result.embed, view=None)
            await safe_respond(interaction, result.message)

        except ServiceError as error:
            await safe_respond(interaction, error.message)


class SupplyManagementView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(SupplyManageButton("approve", request_id))
        self.add_item(SupplyManageButton("reject", request_id))
        self.add_item(SupplyManageButton("edit", request_id))


class SupplyCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Запросить склад",
        style=discord.ButtonStyle.primary,
        emoji="📦",
        custom_id="create_supply_request",
    )
    async def create_request(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            user_db = await AuthorizationService.require_active_soldier(interaction)
            if isinstance(interaction.user, discord.Member):
                await SupplyService.validate_can_apply(user_db, interaction.user)

            draft = await SupplyService.create_draft(interaction.user.id)
            view = SupplyBuilderView(draft, interaction)
            embed = supplies_embed(draft, user_db)
            embed.title = "🛠 Создание заявки на склад"
            embed.set_footer(text="Используйте кнопки категорий ниже для добавления предметов.")

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except ServiceError as error:
            await safe_respond(interaction, error.message)