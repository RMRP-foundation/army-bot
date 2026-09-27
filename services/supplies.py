import datetime
import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core import config
from core.config import RankIndex
from core.constants import SUPPLY_ITEMS, SUPPLY_LIMITS
from core.exceptions import ServiceError
from database.counters import get_next_id
from database.models import SupplyRequest, User
from ui.embeds.supplies import supplies_embed
from ui.embeds.supplies_audit import supply_issuance_embed
from utils.helpers import safe_edit_message, safe_publish_request, build_mentions
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import has_penalty_roles, is_senior_officer

logger = logging.getLogger(__name__)

SUPPLY_COOLDOWN_HOURS = 6


@dataclass(frozen=True)
class SupplyActionResult:
    """Результат approve/reject: данные для отрисовки, без discord-вызовов внутри сервиса."""
    request: SupplyRequest
    embed: discord.Embed
    message: str


class SupplyService:

    @staticmethod
    def validate_limits(items: dict[str, int]) -> None:
        """Проверяет поштучные и категориальные лимиты заказа.

        Raises:
            ServiceError: Если корзина пуста либо какой-то лимит превышен.
        """
        if not items:
            raise ServiceError("❌ Корзина пуста! Выберите хотя бы один предмет.")

        cat_counts: dict[str, int] = {cat: 0 for cat in SUPPLY_ITEMS}

        for item_name, qty in items.items():
            if item_name in SUPPLY_LIMITS:
                limit = SUPPLY_LIMITS[item_name]
                if qty > limit:
                    raise ServiceError(f"❌ Лимит на '{item_name}': максимум {limit} шт.")

            found_cat = False
            for cat, cat_items in SUPPLY_ITEMS.items():
                if item_name in cat_items:
                    cat_counts[cat] += qty
                    found_cat = True
                    break

            if not found_cat and "Misc" in cat_counts:
                cat_counts["Misc"] += qty

        if cat_counts["Оружие"] > SUPPLY_LIMITS.get("Оружие", 999):
            raise ServiceError(f"❌ Лимит на Оружие: максимум {SUPPLY_LIMITS['Оружие']} ед.")

        if cat_counts["Броня"] > SUPPLY_LIMITS.get("Броня", 999):
            raise ServiceError(f"❌ Лимит на Бронежилеты: максимум {SUPPLY_LIMITS['Броня']} шт.")

        mats_qty = items.get("Материалы", 0)
        if mats_qty > SUPPLY_LIMITS.get("Материалы", 9999):
            raise ServiceError(f"❌ Лимит на Материалы: максимум {SUPPLY_LIMITS['Материалы']} ед.")

        med_limit = SUPPLY_LIMITS.get("Медикаменты", 999)
        if cat_counts["Медикаменты"] > med_limit:
            raise ServiceError(f"❌ Лимит на Медикаменты (всего): максимум {med_limit} шт.")

    @staticmethod
    def _check_supply_cooldown(last_supply_at: datetime.datetime | None, subject: str = "У вас") -> None:
        """Проверяет, не действует ли ещё кулдаун после последнего получения склада.

        Raises:
            ServiceError: Если кулдаун ещё не истёк.
        """
        if not last_supply_at:
            return

        cooldown_time = last_supply_at + datetime.timedelta(hours=SUPPLY_COOLDOWN_HOURS)
        if discord.utils.utcnow() < cooldown_time:
            raise ServiceError(
                f"❌ {subject} КД на получение склада. "
                f"Следующий запрос будет доступен {discord.utils.format_dt(cooldown_time, 'R')}."
            )

    @staticmethod
    async def validate_can_apply(user_db: User, member: discord.Member) -> None:
        """Проверяет минимальное звание, отсутствие взысканий, активных заявок и кулдаун.

        Raises:
            ServiceError: Если не выполнено любое из условий.
        """
        min_rank = RankIndex.SENIOR_SERGEANT
        if (user_db.rank or 0) < min_rank:
            raise ServiceError(f"❌ Доступно со звания {config.RANKS[min_rank]}.")

        if has_penalty_roles(member):
            raise ServiceError("❌ Вы не можете создавать заявки на склад, пока у вас есть активные дисциплинарные взыскания.")

        cutoff = discord.utils.utcnow() - datetime.timedelta(hours=24)
        existing = await SupplyRequest.find_one(
            SupplyRequest.user_id == user_db.discord_id, SupplyRequest.status == "PENDING", SupplyRequest.created_at >= cutoff,
        )
        if existing:
            raise ServiceError(f"❌ У вас уже есть активная заявка #{existing.id}. Дождитесь её рассмотрения.")

        SupplyService._check_supply_cooldown(user_db.last_supply_at)

    @staticmethod
    def _validate_reviewer_permissions(officer: User) -> None:
        """Проверяет права офицера на рассмотрение склада (Майор+)."""
        if not is_senior_officer(officer):
            raise ServiceError(f"❌ Недостаточно прав для этого действия (требуется {config.RANKS[RankIndex.MAJOR]}+).")

    @staticmethod
    async def create_draft(user_id: int) -> SupplyRequest:
        """Создаёт пустой черновик заявки на склад."""
        new_id = await get_next_id("supply_requests")
        req = SupplyRequest(id=new_id, user_id=user_id, status="DRAFT")
        await req.create()
        return req

    @staticmethod
    async def publish_request(interaction: discord.Interaction, request: SupplyRequest, user_db: User, view: discord.ui.View) -> None:
        """Публикует готовую заявку на склад в канал и сохраняет message_id."""
        msg_id = await safe_publish_request(
            channel=interaction.channel, request=request, embed=supplies_embed(request, user_db),
            content=build_mentions(request.user_id), view=view,
            error_message="❌ Не удалось отправить заявку в канал. Попробуйте ещё раз.",
        )
        request.message_id = msg_id
        await request.save()

        from cogs.supplies import update_bottom_message
        await update_bottom_message(interaction.client)

    @classmethod
    async def submit_supply(
        cls, interaction: discord.Interaction, request: SupplyRequest, items: dict[str, int],
        user_db: User, view_factory: Callable[[int], discord.ui.View],
    ) -> None:
        """Проверяет лимиты и кулдаун, атомарно переводит заявку из DRAFT в PENDING, публикует.

        Raises:
            ServiceError: Если лимиты нарушены, действует кулдаун, либо заявка уже отправлена.
        """
        cls.validate_limits(items)
        cls._check_supply_cooldown(user_db.last_supply_at)

        updated_dict = await atomic_status_transition(
            SupplyRequest.get_pymongo_collection(), request.id, "DRAFT", "PENDING",
            extra_fields={"items": items, "created_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError("❌ Заявка уже отправлена.")

        request = SupplyRequest(**updated_dict)
        await cls.publish_request(interaction=interaction, request=request, user_db=user_db, view=view_factory(request.id))

    @classmethod
    async def update_supply_items(cls, interaction: discord.Interaction, request: SupplyRequest, items: dict[str, int]) -> None:
        """Сохраняет изменённые предметы в режиме редактирования и синхронизирует опубликованное сообщение.

        Raises:
            ServiceError: Если лимиты нарушены либо заявку уже обработал другой офицер.
        """
        cls.validate_limits(items)

        updated_dict = await atomic_status_transition(
            SupplyRequest.get_pymongo_collection(), request.id, "PENDING", "PENDING", extra_fields={"items": items},
        )
        if not updated_dict:
            raise ServiceError("❌ Заявка уже обработана другим офицером.")

        fresh = SupplyRequest(**updated_dict)
        target_user = await User.get_by_discord_id(fresh.user_id)

        if fresh.message_id:
            channel = interaction.client.get_channel(config.CHANNELS["storage_requests"])
            if channel:
                await safe_edit_message(channel.get_partial_message(fresh.message_id), embed=supplies_embed(fresh, target_user))

    @classmethod
    async def approve_supply(cls, interaction: discord.Interaction, request_id: int, officer: User) -> SupplyActionResult:
        """Одобряет заявку на склад, фиксирует КД, авто-отклоняет другие ожидающие заявки автора, шлёт аудит.

        Raises:
            ServiceError: Если заявка не найдена/уже обработана, заявитель не найден, либо действует КД.
        """
        req = await SupplyRequest.find_one(SupplyRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявка #{request_id} не найдена или уже обработана.")

        cls._validate_reviewer_permissions(officer)

        target_user = await User.get_by_discord_id(req.user_id)
        if not target_user:
            raise ServiceError("❌ Заявитель не найден в базе данных.")

        cls._check_supply_cooldown(target_user.last_supply_at, subject="У пользователя")

        now = discord.utils.utcnow()
        updated_dict = await atomic_status_transition(
            SupplyRequest.get_pymongo_collection(), request_id, "PENDING", "APPROVED",
            extra_fields={"reviewer_id": interaction.user.id, "reviewed_at": now},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявка #{request_id} уже обработана.")

        request = SupplyRequest(**updated_dict)
        target_user.last_supply_at = now
        await target_user.save()

        await SupplyRequest.get_pymongo_collection().update_many(
            {"user_id": request.user_id, "status": "PENDING", "_id": {"$ne": request.id}},
            {"$set": {
                "status": "REJECTED",
                "reviewer_id": interaction.client.user.id if interaction.client.user else None,
                "reviewed_at": now,
            }},
        )

        await cls._send_storage_audit(interaction, request)

        return SupplyActionResult(
            request=request, embed=supplies_embed(request, target_user),
            message=f"✅ Заявка #{request_id} одобрена. КД установлено.",
        )

    @staticmethod
    async def _send_storage_audit(interaction: discord.Interaction, request: SupplyRequest) -> None:
        audit_channel = interaction.client.get_channel(config.CHANNELS["storage_audit"])
        if not audit_channel:
            return

        embed_audit = supply_issuance_embed(interaction.user, request.user_id, request.items, request,
                                            interaction.message.jump_url)
        try:
            await audit_channel.send(content=build_mentions(request.user_id), embed=embed_audit)
        except discord.HTTPException as error:
            logger.error(f"Failed to send storage audit log for request #{request.id}: {error}")

        from cogs.supplies_audit import update_bottom_message as update_audit_bottom
        await update_audit_bottom(interaction.client)

    @classmethod
    async def reject_supply(cls, interaction: discord.Interaction, request_id: int, officer: User) -> SupplyActionResult:
        """Отклоняет заявку на склад.

        Raises:
            ServiceError: Если заявка не найдена/уже обработана.
        """
        req = await SupplyRequest.find_one(SupplyRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявка #{request_id} не найдена или уже обработана.")

        cls._validate_reviewer_permissions(officer)

        updated_dict = await atomic_status_transition(
            SupplyRequest.get_pymongo_collection(), request_id, "PENDING", "REJECTED",
            extra_fields={"reviewer_id": interaction.user.id, "reviewed_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявка #{request_id} уже обработана.")

        request = SupplyRequest(**updated_dict)
        target_user = await User.get_by_discord_id(request.user_id)

        return SupplyActionResult(
            request=request, embed=supplies_embed(request, target_user),
            message=f"❌ Заявка #{request_id} отклонена.",
        )