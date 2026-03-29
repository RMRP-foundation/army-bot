import datetime
import logging
import re
from typing import Dict, Tuple

import discord
from discord import Interaction

import config
from config import PENALTY_ROLES
from database.counters import get_next_id
from database.models import SupplyRequest, User
from ui.modals.supplies import ItemAmountModal
from utils.user_data import get_initiator

logger = logging.getLogger(__name__)


def check_limits(items: Dict[str, int]) -> Tuple[bool, str]:
    cat_counts = {cat: 0 for cat in config.SUPPLY_ITEMS}

    for item_name, qty in items.items():
        if item_name in config.SUPPLY_LIMITS:
            if qty > config.SUPPLY_LIMITS[item_name]:
                limit = config.SUPPLY_LIMITS[item_name]
                return (
                    False,
                    f"Лимит на '{item_name}': максимум {limit} шт.",
                )

        found_cat = False
        for cat, cat_items in config.SUPPLY_ITEMS.items():
            if item_name in cat_items:
                cat_counts[cat] += qty
                found_cat = True
                break

        if not found_cat and "Misc" in cat_counts:
            cat_counts["Misc"] += qty

    if cat_counts["Оружие"] > config.SUPPLY_LIMITS.get("Оружие", 999):
        return False, f"Лимит на Оружие: максимум {config.SUPPLY_LIMITS['Оружие']} ед."

    if cat_counts["Броня"] > config.SUPPLY_LIMITS.get("Броня", 999):
        return (
            False,
            f"Лимит на Бронежилеты: максимум {config.SUPPLY_LIMITS['Броня']} шт.",
        )

    mats_qty = items.get("Материалы", 0)
    if mats_qty > config.SUPPLY_LIMITS.get("Материалы", 9999):
        return (
            False,
            f"Лимит на Материалы: максимум {config.SUPPLY_LIMITS['Материалы']} ед.",
        )

    # Медикаменты (общий лимит на категорию, если есть)
    med_limit = config.SUPPLY_LIMITS.get("Медикаменты", 999)
    if cat_counts["Медикаменты"] > med_limit:
        return (
            False,
            f"Лимит на Медикаменты (всего): максимум {med_limit} шт.",
        )

    return True, ""


async def handle_approve(interaction: discord.Interaction, req: SupplyRequest):
    target_user = await User.find_one(User.discord_id == req.user_id)

    if target_user.last_supply_at:
        cooldown_time = target_user.last_supply_at + datetime.timedelta(hours=3)
        if datetime.datetime.now() < cooldown_time:
            remaining = cooldown_time - datetime.datetime.now()
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            await interaction.response.send_message(
                f"❌ У пользователя КД на получение склада. "
                f"Осталось: {hours}ч {minutes}м.",
                ephemeral=True,
            )
            return

    req.status = "APPROVED"
    req.reviewer_id = interaction.user.id
    req.reviewed_at = datetime.datetime.now()
    await req.save()

    target_user.last_supply_at = datetime.datetime.now()
    await target_user.save()

    other_requests = await SupplyRequest.find(
        SupplyRequest.user_id == req.user_id,
        SupplyRequest.status == "PENDING",
        SupplyRequest.id != req.id,
    ).to_list()

    for other in other_requests:
        other.status = "REJECTED"
        other.reviewer_id = interaction.client.user.id
        await other.save()

    embed = await req.to_embed(interaction.client)
    await interaction.response.edit_message(embed=embed, view=None)
    await interaction.followup.send(
        f"✅ Заявка #{req.id} одобрена. КД установлено.", ephemeral=True
    )

    try:
        embed_audit = discord.Embed(
            title="📦 Выдача склада",
            color=discord.Color.dark_green(),
            timestamp=datetime.datetime.now(),
        )
        embed_audit.add_field(name="Выдал", value=interaction.user.mention, inline=True)
        embed_audit.add_field(name="Получил", value=f"<@{req.user_id}>", inline=True)

        items_str = "\n".join([f"• {k}: {v} шт." for k, v in req.items.items()])
        embed_audit.add_field(name="Предметы", value=items_str, inline=False)
        embed_audit.add_field(
            name="Причина",
            value=f"[Заявка #{req.id}]({interaction.message.jump_url})",
            inline=False,
        )

        audit_channel = interaction.client.get_channel(config.CHANNELS["storage_audit"])
        if audit_channel:
            await audit_channel.send(
                content=f"-# ||<@{req.user_id}>||", embed=embed_audit
            )

            from cogs.supplies_audit import update_bottom_message

            await update_bottom_message(interaction.client)
    except Exception as e:
        logger.error(f"Error logging supply: {e}")


async def handle_reject(interaction: discord.Interaction, req: SupplyRequest):
    req.status = "REJECTED"
    req.reviewer_id = interaction.user.id
    req.reviewed_at = datetime.datetime.now()
    await req.save()

    embed = await req.to_embed(interaction.client)
    await interaction.response.edit_message(embed=embed, view=None)
    await interaction.followup.send(f"❌ Заявка #{req.id} отклонена.", ephemeral=True)


async def handle_edit(interaction: discord.Interaction, req: SupplyRequest):
    view = SupplyBuilderView(req, interaction, is_edit_mode=True)
    embed = await req.to_embed(interaction.client)
    embed.title = f"🛠 Редактирование заявки #{req.id}"
    embed.set_footer(text="Режим редактирования (Майор+)")
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ItemSelectView(discord.ui.View):
    """Меню выбора предмета из категории"""

    def __init__(
        self, category: str, request: SupplyRequest, parent_view: "SupplyBuilderView"
    ):
        super().__init__(timeout=60)
        self.request = request
        self.parent_view = parent_view

        options = []
        items = config.SUPPLY_ITEMS[category]
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
        item_name = interaction.data["values"][0]
        current_qty = self.request.items.get(item_name, 0)

        modal = ItemAmountModal(item_name, current_qty)
        await interaction.response.send_modal(modal)
        await modal.wait()

        if modal.result is not None:
            new_qty = modal.result
            if new_qty == 0:
                if item_name in self.request.items:
                    del self.request.items[item_name]
            else:
                self.request.items[item_name] = new_qty

            await self.request.set({"items": self.request.items})

            await self.parent_view.refresh_embed(self.parent_view.original_interaction)
            await interaction.delete_original_response()


class CategorySelectButton(discord.ui.Button):
    def __init__(self, category: str, request: SupplyRequest):
        super().__init__(label=category, style=discord.ButtonStyle.secondary)
        self.category = category
        self.request = request

    async def callback(self, interaction: discord.Interaction):
        view = ItemSelectView(self.category, self.request, self.view)
        await interaction.response.send_message(
            f"📂 Категория: **{self.category}**", view=view, ephemeral=True
        )


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

        for cat_name in config.SUPPLY_ITEMS.keys():
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

        embed = await self.request.to_embed(interaction.client)
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
        if not self.is_edit_mode:
            await self.request.delete()
        await interaction.response.edit_message(
            content="❌ Действие отменено.", embed=None, view=None
        )

    async def submit_callback(self, interaction: discord.Interaction):
        if not self.request.items:
            await interaction.response.send_message("❌ Корзина пуста!", ephemeral=True)
            return

        is_valid, error_msg = check_limits(self.request.items)
        if not is_valid:
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return

        if self.is_edit_mode:
            # Перечитываем из БД, чтобы не перезаписать статус, выставленный параллельным Approve
            fresh = await SupplyRequest.find_one(SupplyRequest.id == self.request.id)
            if not fresh or fresh.status != "PENDING":
                await interaction.response.send_message(
                    "❌ Заявка уже обработана другим администратором.", ephemeral=True
                )
                return
            await fresh.set({"items": self.request.items})

            # Обновляем оригинальное сообщение в канале
            if self.request.message_id:
                channel = interaction.client.get_channel(
                    config.CHANNELS["storage_requests"]
                )
                if channel:
                    try:
                        message = await channel.fetch_message(self.request.message_id)
                        embed = await self.request.to_embed(interaction.client)
                        await message.edit(embed=embed)
                    except discord.NotFound:
                        pass  # сообщение удалено

            await interaction.response.edit_message(
                content="✅ Изменения сохранены.", embed=None, view=None
            )
        else:
            target_user = await get_initiator(interaction)

            if target_user.last_supply_at:
                cooldown_time = target_user.last_supply_at + datetime.timedelta(hours=3)
                if datetime.datetime.now() < cooldown_time:
                    remaining = cooldown_time - datetime.datetime.now()
                    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    await interaction.response.send_message(
                        f"❌ У вас КД на получение склада. "
                        f"Осталось: {hours}ч {minutes}м.",
                        ephemeral=True,
                    )
                    return

            self.request.status = "PENDING"
            self.request.created_at = datetime.datetime.now()
            await self.request.save()

            channel = interaction.client.get_channel(
                config.CHANNELS["storage_requests"]
            )
            if channel:
                manage_view = SupplyManagementView(self.request.id)
                embed = await self.request.to_embed(interaction.client)
                message = await channel.send(
                    content=f"||<@{self.request.user_id}>||",
                    embed=embed,
                    view=manage_view,
                )
                self.request.message_id = message.id
                await self.request.save()

                from cogs.supplies import update_bottom_message

                await update_bottom_message(interaction.client)

            await interaction.response.edit_message(
                content="✅ Заявка успешно отправлена!", embed=None, view=None
            )


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
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: Interaction) -> None:
        req = await SupplyRequest.find_one(SupplyRequest.id == self.request_id)
        if not req:
            await interaction.response.send_message(
                "❌ Заявка не найдена в базе данных.", ephemeral=True
            )
            return

        if req.status != "PENDING":
            await interaction.response.send_message(
                f"❌ Эта заявка уже обработана (Статус: {req.status}).", ephemeral=True
            )
            return

        user = await get_initiator(interaction)

        if self.action == "edit":
            if user.discord_id != req.user_id and (user.rank or 0) < config.RankIndex.MAJOR:
                await interaction.response.send_message(
                    "❌ У вас недостаточно прав для этого действия (Требуется: Майор+).",
                    ephemeral=True,
                )
                return
            await handle_edit(interaction, req)
            return

        if (user.rank or 0) < config.RankIndex.MAJOR:
            await interaction.response.send_message(
                "❌ У вас недостаточно прав для этого действия (Требуется: Майор+).",
                ephemeral=True,
            )
            return

        if self.action == "approve":
            await handle_approve(interaction, req)
        elif self.action == "reject":
            await handle_reject(interaction, req)


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
        user = await get_initiator(interaction)

        if not user or (user.rank or 0) < 4:
            await interaction.response.send_message(
                "❌ Доступно со звания Старший Сержант.", ephemeral=True
            )
            return

        user_roles = [role.id for role in interaction.user.roles]
        if any(role_id in PENALTY_ROLES for role_id in user_roles):
            await interaction.response.send_message(
                "❌ Вы не можете создавать заявки на склад, "
                "пока у вас есть активные дисциплинарные взыскания.",
                ephemeral=True,
            )
            return

        cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)
        existing = await SupplyRequest.find_one(
            SupplyRequest.user_id == interaction.user.id,
            SupplyRequest.status == "PENDING",
            SupplyRequest.created_at >= cutoff,
        )
        if existing:
            await interaction.response.send_message(
                f"❌ У вас уже есть активная заявка #{existing.id}. "
                "Дождитесь её рассмотрения.",
                ephemeral=True,
            )
            return

        new_id = await get_next_id("supply_requests")
        req = SupplyRequest(id=new_id, user_id=interaction.user.id, status="DRAFT")
        await req.create()

        view = SupplyBuilderView(req, interaction)
        embed = await req.to_embed(interaction.client)
        embed.title = "🛠 Создание заявки на склад"
        embed.set_footer(
            text="Используйте кнопки категорий ниже для добавления предметов."
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
