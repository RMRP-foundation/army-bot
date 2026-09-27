import datetime
import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core import config
from core.config import RankIndex
from core.exceptions import ServiceError
from database.counters import get_next_id
from database.models import RoleData, TimeoffRequest, User
from services.notifications import notify_timeoff_approved, notify_timeoff_rejected
from ui.embeds.timeoff import timeoff_embed
from utils.helpers import build_request_mentions, safe_publish_request
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import is_senior_officer, is_higher_rank

logger = logging.getLogger(__name__)

MSK = datetime.timezone(datetime.timedelta(hours=3))


@dataclass(frozen=True)
class TimeoffActionResult:
    """Результат approve/reject: данные для отрисовки, без discord-вызовов внутри сервиса."""
    request: TimeoffRequest
    embed: discord.Embed
    view: discord.ui.View
    mention_ids: list[int]
    message: str


class TimeoffService:

    @staticmethod
    def _get_msk_day_start_utc() -> datetime.datetime:
        """Начало текущих суток по МСК, возвращённое в UTC для сравнения с БД."""
        now_utc = discord.utils.utcnow()
        today_msk = now_utc.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
        return today_msk.astimezone(datetime.timezone.utc)

    @classmethod
    async def validate_can_apply(cls, user_db: User) -> None:
        """Проверяет минимальный ранг, отсутствие открытых и уже одобренных заявок за сегодня.

        Raises:
            ServiceError: Если не выполнено любое из условий.
        """
        min_rank = RankIndex.JUNIOR_SERGEANT
        if (user_db.rank or 0) < min_rank:
            raise ServiceError(f"### Вы не можете подать заявление на отгул. Требуется звание: {config.RANKS[min_rank]}+")

        cutoff = cls._get_msk_day_start_utc()

        opened_request = await TimeoffRequest.find_one(
            TimeoffRequest.user_id == user_db.discord_id, TimeoffRequest.status == "PENDING", TimeoffRequest.sent_at >= cutoff,
        )
        if opened_request is not None:
            raise ServiceError(f"### У вас уже есть открытое заявление #{opened_request.id} на рассмотрении.\nОжидайте его рассмотрения.")

        approved_request = await TimeoffRequest.find_one(
            TimeoffRequest.user_id == user_db.discord_id, TimeoffRequest.status == "APPROVED", TimeoffRequest.reviewed_at >= cutoff,
        )
        if approved_request:
            raise ServiceError("### Вы уже подавали заявление на отгул сегодня.\nПовторная подача возможна только на следующий день.")

    @staticmethod
    def _validate_reviewer_permissions(officer: User, requester: User | None) -> None:
        """Проверяет права офицера на рассмотрение отгула (Майор+, выше заявителя по званию).

        Raises:
            ServiceError: Если звание ниже Майора, заявитель не найден, либо нарушена субординация.
        """
        if not is_senior_officer(officer):
            raise ServiceError(f"❌ Для рассмотрения заявок на отгул требуется звание {config.RANKS[RankIndex.MAJOR]} и выше.")

        if not requester:
            raise ServiceError("❌ Заявитель не найден в базе данных.")

        if officer.discord_id == requester.discord_id:
            raise ServiceError("❌ Вы не можете рассматривать собственную заявку.")

        if not is_higher_rank(officer, requester):
            raise ServiceError("❌ Вы не можете рассматривать заявку человека, чье звание равно вашему или выше.")

    @staticmethod
    async def create_request(user_db: User, period: str) -> TimeoffRequest:
        """Создаёт и сохраняет запрос на отгул в базе данных."""
        new_id = await get_next_id("timeoff_requests")
        request = TimeoffRequest(
            id=new_id, user_id=user_db.discord_id,
            data=RoleData(full_name=user_db.full_name or "Не указано", static_id=user_db.static or 0),
            period=period, status="PENDING",
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(interaction: discord.Interaction, request: TimeoffRequest, user_db: User, view: discord.ui.View) -> None:
        """Публикует заявку на отгул в служебный канал."""
        content_mentions = build_request_mentions(user_id=user_db.discord_id, user_division_id=user_db.division)
        await safe_publish_request(
            channel=interaction.channel, request=request, embed=timeoff_embed(request, user_db), content=content_mentions, view=view,
        )

        from cogs.timeoff import update_bottom_message
        await update_bottom_message(interaction.client)

    @classmethod
    async def submit_timeoff(cls, interaction: discord.Interaction, user_db: User, period: str, view_factory: Callable[[int], discord.ui.View]) -> None:
        """Оформляет и публикует заявление на отгул от лица бойца.

        Raises:
            ServiceError: Если боец не может подать заявку сегодня.
        """
        await cls.validate_can_apply(user_db)
        request = await cls.create_request(user_db=user_db, period=period)
        await cls.publish_request(interaction=interaction, request=request, user_db=user_db, view=view_factory(request.id))

    @classmethod
    async def approve_timeoff(cls, interaction: discord.Interaction, request_id: int, officer: User) -> TimeoffActionResult:
        """Одобряет заявку на отгул атомарным переходом статуса.

        Raises:
            ServiceError: Если заявка не найдена/уже обработана, либо нарушены права доступа.
        """
        req = await TimeoffRequest.find_one(TimeoffRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявка #{request_id} не найдена или уже обработана.")

        target_user = await User.get_by_discord_id(req.user_id)
        cls._validate_reviewer_permissions(officer, target_user)

        updated_dict = await atomic_status_transition(
            TimeoffRequest.get_pymongo_collection(), request_id, "PENDING", "APPROVED",
            extra_fields={"reviewed_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявка #{request_id} уже обработана.")

        request = TimeoffRequest(**updated_dict)
        await notify_timeoff_approved(interaction.client, request.user_id)

        from ui.views.indicators import indicator_view
        return TimeoffActionResult(
            request=request, embed=timeoff_embed(request, target_user),
            view=indicator_view(f"Одобрил {interaction.user.display_name}", emoji="👍"),
            mention_ids=[request.user_id, interaction.user.id],
            message="✅ Заявление на отгул одобрено.",
        )

    @classmethod
    async def reject_timeoff(cls, interaction: discord.Interaction, request_id: int, officer: User) -> TimeoffActionResult:
        """Отклоняет заявку на отгул атомарным переходом статуса.

        Raises:
            ServiceError: Если заявка не найдена/уже обработана, либо нарушены права доступа.
        """
        req = await TimeoffRequest.find_one(TimeoffRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявка #{request_id} не найдена или уже обработана.")

        target_user = await User.get_by_discord_id(req.user_id)
        cls._validate_reviewer_permissions(officer, target_user)

        updated_dict = await atomic_status_transition(
            TimeoffRequest.get_pymongo_collection(), request_id, "PENDING", "REJECTED",
            extra_fields={"reviewed_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявка #{request_id} уже обработана.")

        request = TimeoffRequest(**updated_dict)
        await notify_timeoff_rejected(interaction.client, request.user_id)

        from ui.views.indicators import indicator_view
        return TimeoffActionResult(
            request=request, embed=timeoff_embed(request, target_user),
            view=indicator_view(f"Отклонил {interaction.user.display_name}", emoji="👎"),
            mention_ids=[request.user_id, interaction.user.id],
            message="✅ Заявление на отгул отклонено.",
        )

    @staticmethod
    async def cancel_timeoff(request_id: int, user_id: int) -> None:
        """Атомарно отменяет собственную нерассмотренную заявку автором. Удаление сообщения — на вызывающем.

        Raises:
            ServiceError: Если заявка не найдена или уже обработана офицером.
        """
        updated_dict = await atomic_status_transition(
            TimeoffRequest.get_pymongo_collection(), request_id, "PENDING", "REJECTED",
            extra_fields={"reviewed_at": discord.utils.utcnow()}, extra_filter={"user_id": user_id},
        )
        if not updated_dict:
            raise ServiceError("❌ Заявка не найдена или уже обработана офицером.")