import logging
from dataclasses import dataclass
from typing import Callable

import discord
from beanie.odm.operators.find.comparison import In

from core import config
from core.config import RankIndex
from core.exceptions import ServiceError
from database import divisions
from database.counters import get_next_id
from database.models import Division, PromotionRequest, User
from services.audit import AuditAction, audit_logger
from services.member import MemberService
from services.notifications import notify_promoted, notify_promotion_approved, notify_promotion_rejected
from ui.embeds.promotion import promotion_embed
from utils.helpers import safe_publish_request, build_mentions
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import has_disciplinary_restrictions, is_high_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DivisionRule:
    review_rank: int
    promote_rank: int = RankIndex.MAJOR
    review_positions: tuple[str, ...] = ()
    promote_positions: tuple[str, ...] = ()
    reviewer_division_id: int | None = None

    def can_review(self, user: User) -> bool:
        if (user.rank or 0) >= self.review_rank:
            return True
        if user.position and self.review_positions:
            return user.position.strip().lower() in {p.lower() for p in self.review_positions}
        return False

    def can_promote(self, user: User) -> bool:
        if (user.rank or 0) >= self.promote_rank:
            return True
        if user.position and self.promote_positions:
            return user.position.strip().lower() in {p.lower() for p in self.promote_positions}
        return False




@dataclass(frozen=True)
class PromotionActionResult:
    """Результат approve/reject/promote: данные для отрисовки, без discord-вызовов внутри сервиса."""
    request: PromotionRequest
    embed: discord.Embed
    view: discord.ui.View
    mention_ids: list[int]
    message: str


class PromotionService:

    @classmethod
    def validate_can_review(cls, approver: User, div: Division, report: PromotionRequest) -> None:
        """Валидирует полномочия офицера рассмотреть рапорт.

        Raises:
            ServiceError: Если звание ниже целевого, офицер не из нужного подразделения,
                либо не проходит по правилам DIVISION_REVIEW_RULES.
        """
        if is_high_command(approver):
            return

        if (approver.rank or 0) <= report.target_rank:
            needed = config.RANKS[report.target_rank + 1] if (report.target_rank + 1) < len(config.RANKS) else "выше"
            raise ServiceError(f"❌ Для проверки этого рапорта требуется звание {needed}+.")

        rule = config.PROMOTION_RULES.get(div.division_id)
        if not rule:
            raise ServiceError(f"❌ Правила повышения для {div.abbreviation} не настроены.")

        expected_div_id = rule.reviewer_division_id if rule.reviewer_division_id is not None else div.division_id
        if approver.division != expected_div_id:
            expected_div = divisions.get_division(expected_div_id)
            div_name = expected_div.name if expected_div else "нужное подразделение"
            raise ServiceError(f"❌ Рапорты этого подразделения проверяет {div_name}.")

        if not rule.can_review(approver):
            raise ServiceError(f"❌ Недостаточно звания или должности для проверки рапортов в {div.abbreviation}.")

    @staticmethod
    def validate_can_promote(promoter: User, div: Division) -> None:
        """Валидирует полномочия офицера выполнить повышение.

        Raises:
            ServiceError: Если не проходит по DIVISION_PROMOTE_RULES, либо офицер не из нужного подразделения.
        """
        if is_high_command(promoter):
            return

        rule = config.PROMOTION_RULES.get(div.division_id)
        if not rule:
            raise ServiceError(f"❌ Правила повышения для {div.abbreviation} не настроены.")

        expected_div_id = rule.reviewer_division_id if rule.reviewer_division_id is not None else div.division_id
        if promoter.division != expected_div_id:
            expected_div = divisions.get_division(expected_div_id)
            div_name = expected_div.name if expected_div else "нужное подразделение"
            raise ServiceError(f"❌ Рапорты этого подразделения проверяет {div_name}.")

        if not rule.can_promote(promoter):
            raise ServiceError(f"❌ Недостаточно звания или должности для повышения в {div.abbreviation}.")

    @staticmethod
    async def _get_request_and_division(request_id: int, allowed_statuses: tuple[str, ...]) -> tuple[
        PromotionRequest, Division]:
        """Получает рапорт и его подразделение, проверяя, что статус рапорта допустим для действия.

        Raises:
            ServiceError: Если рапорт не найден, статус не входит в allowed_statuses,
                либо подразделение рапорта не найдено.
        """
        req = await PromotionRequest.find_one(PromotionRequest.id == request_id)
        if not req or req.status not in allowed_statuses:
            raise ServiceError(f"❌ Рапорт #{request_id} не найден или уже обработан.")

        div = divisions.get_division(req.division_id)
        if not div:
            raise ServiceError("❌ Подразделение рапорта не найдено.")

        return req, div


    @staticmethod
    async def create_request(
        user_id: int, user_db: User, division_id: int, evidence: dict[str, str], score: str | None,
    ) -> PromotionRequest:
        """Создаёт и сохраняет рапорт на повышение в базе данных."""
        new_id = await get_next_id("promotion_reports")
        request = PromotionRequest(
            id=new_id, user_id=user_id, division_id=division_id,
            current_rank=user_db.rank, target_rank=user_db.rank + 1, evidence=evidence, score=score,
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(
        interaction: discord.Interaction, user: User, request: PromotionRequest, division: Division, view: discord.ui.View,
    ) -> None:
        """Публикует рапорт в канал подразделения и обновляет статус-бар."""
        role_ids = config.PROMOTION_NOTIFY_ROLES.get(division.division_id, ())
        msg_id = await safe_publish_request(
            channel=interaction.channel, request=request, embed=promotion_embed(request, user),
            content=build_mentions(interaction.user.id, role_ids), view=view,
        )
        request.message_id = msg_id
        await request.save()

        from cogs.promotion import update_bottom_message
        await update_bottom_message(interaction.client, interaction.channel.id)

    @classmethod
    async def submit_promotion(
        cls, interaction: discord.Interaction, user_db: User, division: Division,
        evidence: dict[str, str], score: str | None, view_factory: Callable[..., discord.ui.View],
    ) -> None:
        """Оформляет и публикует рапорт на повышение от лица бойца.

        Raises:
            ServiceError: Если у бойца уже есть активный рапорт.
        """
        existing = await PromotionRequest.find_one(
            PromotionRequest.user_id == interaction.user.id,
            In(PromotionRequest.status, ["PENDING", "APPROVED"]),
        )
        if existing:
            raise ServiceError(f"❌ У вас уже есть активный рапорт #{existing.id}.")

        request = await cls.create_request(
            user_id=interaction.user.id, user_db=user_db, division_id=division.division_id,
            evidence=evidence, score=score,
        )
        await cls.publish_request(
            interaction=interaction, user=user_db, request=request, division=division,
            view=view_factory(request.id, "approve", "reject", "cancel"),
        )

    @classmethod
    async def approve_promotion(cls, interaction: discord.Interaction, request_id: int, approver: User) -> PromotionActionResult:
        """Одобряет рапорт на повышение: субординация/подразделение, атомарный переход статуса.

        Raises:
            ServiceError: Если рапорт не найден/уже обработан, подразделение не найдено,
                либо нарушены правила проверки.
        """
        req, div = await cls._get_request_and_division(request_id, ("PENDING",))
        cls.validate_can_review(approver, div, req)

        updated_dict = await atomic_status_transition(
            PromotionRequest.get_pymongo_collection(), request_id, "PENDING", "APPROVED",
            extra_fields={"reviewer_id": interaction.user.id},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Рапорт #{request_id} не найден или уже обработан.")

        request = PromotionRequest(**updated_dict)
        target_user = await User.get_by_discord_id(request.user_id)
        await notify_promotion_approved(interaction.client, request.user_id)

        from ui.views.promotion import PromotionManagementView
        return PromotionActionResult(
            request=request,
            embed=promotion_embed(request, target_user),
            view=PromotionManagementView(request.id, "promote", "reject"),
            mention_ids=[request.user_id, interaction.user.id],
            message="✅ Рапорт одобрен.",
        )

    @classmethod
    async def reject_promotion(cls, interaction: discord.Interaction, request_id: int, reviewer: User, reason: str) -> PromotionActionResult:
        """Отклоняет рапорт на повышение из статуса PENDING или APPROVED.

        Raises:
            ServiceError: Если рапорт не найден/уже обработан, подразделение не найдено,
                либо нарушены правила проверки.
        """
        req, div = await cls._get_request_and_division(request_id, ("PENDING", "APPROVED"))
        cls.validate_can_review(reviewer, div, req)

        updated_dict = await atomic_status_transition(
            PromotionRequest.get_pymongo_collection(), request_id, ["PENDING", "APPROVED"], "REJECTED",
            extra_fields={"reviewer_id": interaction.user.id, "reject_reason": reason},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Рапорт #{request_id} не найден или уже обработан.")

        request = PromotionRequest(**updated_dict)
        target_user = await User.get_by_discord_id(request.user_id)
        await notify_promotion_rejected(interaction.client, request.user_id, reason)

        from ui.views.indicators import indicator_view
        return PromotionActionResult(
            request=request,
            embed=promotion_embed(request, target_user),
            view=indicator_view("Отклонён", emoji="👎"),
            mention_ids=[request.user_id, interaction.user.id],
            message="✅ Рапорт отклонён.",
        )

    @classmethod
    async def promote_soldier(cls, interaction: discord.Interaction, request_id: int, promoter: User) -> PromotionActionResult:
        """Применяет повышение звания в БД и синхронизирует Discord-профиль.

        Повышение бойца ВА до младшего сержанта автоматически переводит бойца в ВБП.

        Raises:
            ServiceError: Если рапорт не найден/не одобрен, подразделение не найдено, боец под
                дисциплинарным взысканием, либо больше не состоит на службе.
        """
        req, div = await cls._get_request_and_division(request_id, ("APPROVED",))
        cls.validate_can_promote(promoter, div)

        member = await interaction.client.getch_member(req.user_id)
        if member and has_disciplinary_restrictions(member):
            raise ServiceError("❌ Невозможно повысить военнослужащего с активными дисциплинарными взысканиями или под расследованием.")

        target_user_db = await User.get_by_discord_id(req.user_id)
        if not target_user_db or target_user_db.rank is None:
            await atomic_status_transition(
                PromotionRequest.get_pymongo_collection(), request_id, "APPROVED", "REJECTED",
                extra_fields={"reviewer_id": interaction.user.id, "reject_reason": "Военнослужащий уволен."},
            )
            raise ServiceError("❌ Военнослужащий больше не состоит на службе.")

        updated_dict = await atomic_status_transition(
            PromotionRequest.get_pymongo_collection(), request_id, "APPROVED", "PROMOTED",
            extra_fields={"promoted_by": interaction.user.id},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Рапорт #{request_id} не найден или уже обработан.")

        request = PromotionRequest(**updated_dict)

        new_division = target_user_db.division
        if div.abbreviation.lower() == "ва" and request.target_rank == RankIndex.JUNIOR_SERGEANT:
            if vbp := divisions.get_division_by_abbreviation("ВБП"):
                new_division = vbp.division_id

        await target_user_db.set({User.rank: request.target_rank, User.division: new_division})

        if member:
            await MemberService.sync_member_discord(
                member=member, user_db=target_user_db, reason=f"Повышение по рапорту #{request.id} by {interaction.user.id}",
            )

        await audit_logger.log_action(action=AuditAction.PROMOTED, initiator=interaction.user, target=request.user_id)
        await notify_promoted(interaction.client, request.user_id, config.RANKS[request.target_rank])

        from ui.views.indicators import indicator_view
        return PromotionActionResult(
            request=request,
            embed=promotion_embed(request, target_user_db),
            view=indicator_view("Повышен", emoji="⭐"),
            mention_ids=[request.user_id, request.reviewer_id, interaction.user.id],
            message="✅ Военнослужащий повышен.",
        )

    @staticmethod
    async def cancel_promotion(request_id: int, user_id: int) -> None:
        """Атомарно отменяет собственный рапорт автором. Удаление сообщения — на вызывающем.

        Raises:
            ServiceError: Если рапорт не найден или уже обработан.
        """
        updated_dict = await atomic_status_transition(
            PromotionRequest.get_pymongo_collection(), request_id, "PENDING", "CANCELLED",
            extra_filter={"user_id": user_id},
        )
        if not updated_dict:
            raise ServiceError("❌ Рапорт не найден или уже обработан.")