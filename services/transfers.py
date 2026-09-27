import logging
from dataclasses import dataclass
from typing import Callable

import discord
from beanie.odm.operators.find.comparison import NotIn

from core import config
from core.config import RankIndex
from core.exceptions import ServiceError
from database import divisions
from database.counters import get_next_id
from database.models import Division, TransferRequest, User
from services.audit import AuditAction, audit_logger
from services.member import MemberService
from services.notifications import notify_transfer_approved, notify_transfer_rejected
from ui.embeds.transfers import transfer_embed
from utils.helpers import build_request_mentions, safe_publish_request
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import is_high_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferActionResult:
    """Результат approve/reject: данные для отрисовки, без discord-вызовов внутри сервиса.

    Attributes:
        reply_content: Если задано, вызывающий должен отдельно ответить этим текстом на
            сообщение заявки (доп. пинг руководства нового подразделения на этапе 1 -> 2).
    """
    request: TransferRequest
    embed: discord.Embed
    view: discord.ui.View
    mention_content: str | None = None
    reply_content: str | None = None


class TransferService:

    @staticmethod
    def validate_reviewer_permissions(
            officer: User, division_ids: list[int], error_message: str = "❌ У вас нет прав для этого действия.",
    ) -> None:
        """Проверяет право офицера обрабатывать заявку по одному из перечисленных подразделений."""
        if is_high_command(officer):
            return
        if officer.division not in division_ids:
            raise ServiceError(error_message)
        division = divisions.get_division(officer.division)
        if not division or not division.positions:
            raise ServiceError(error_message)
        if not any(p.name == officer.position and p.privilege.value >= 2 for p in division.positions):
            raise ServiceError(error_message)

    @staticmethod
    async def validate_no_active_request(user_id: int) -> None:
        """Проверяет, что у пользователя нет открытых заявлений на перевод."""
        opened_request = await TransferRequest.find_one(
            TransferRequest.user_id == user_id, NotIn(TransferRequest.status, ["APPROVED", "REJECTED"]),
        )
        if opened_request is not None:
            raise ServiceError(
                f"### У вас уже есть открытое заявление #{opened_request.id} на рассмотрении.\nОжидайте его рассмотрения."
            )

    @staticmethod
    async def validate_transfer_rules(user_db: User, destination: Division) -> None:
        """Проверяет соответствие ранга требованиям подразделения и запрет перевода в своё же подразделение.

        Raises:
            ServiceError: Если звание ниже требуемого, либо боец уже состоит в этом подразделении.
        """
        min_rank = RankIndex.JUNIOR_SERGEANT if destination.abbreviation != "ССО" else RankIndex.SENIOR_SERGEANT

        if (user_db.rank or 0) < min_rank:
            raise ServiceError(
                f"### ❌ Отказано в подаче\nВ подразделение **{destination.abbreviation}** "
                f"можно вступить только со звания **{config.RANKS[min_rank]}** и выше."
            )

        if user_db.division == destination.division_id:
            raise ServiceError(f"### Вы уже состоите в подразделении {destination.abbreviation}. Подача заявления невозможна.")

    @staticmethod
    async def create_request(
        user_db: User, destination: Division, name_age: str, timezone: str, online_prime: str, motivation: str,
    ) -> TransferRequest:
        """Создаёт и сохраняет запрос на перевод в базе данных.

        Если старое подразделение не имеет формальной структуры должностей (`positions` пуст),
        этап согласования старым подразделением пропускается — заявка стартует сразу с NEW_DIVISION_REVIEW.
        """
        division = divisions.get_division(user_db.division)
        status = "OLD_DIVISION_REVIEW" if (division and division.positions) else "NEW_DIVISION_REVIEW"

        new_id = await get_next_id("transfer_requests")
        request = TransferRequest(
            id=new_id, user_id=user_db.discord_id, static=user_db.static or 0,
            new_division_id=destination.division_id, old_division_id=user_db.division or 0,
            full_name=user_db.full_name or "Не указано", name_age=name_age, timezone=timezone,
            online_prime=online_prime, motivation=motivation, status=status,
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(
        interaction: discord.Interaction, request: TransferRequest, user_db: User, destination: Division, view: discord.ui.View,
    ) -> None:
        """Публикует заявку на перевод в текущий канал."""
        first_division = destination if request.status == "NEW_DIVISION_REVIEW" else divisions.get_division(request.old_division_id)
        div_id = first_division.division_id if first_division else destination.division_id
        mentions = build_request_mentions(interaction.user.id, div_id)

        await safe_publish_request(
            channel=interaction.channel, request=request, embed=transfer_embed(request, user_db),
            content=mentions, view=view, error_message="❌ Не удалось отправить заявление в канал. Попробуйте ещё раз.",
        )
        from cogs.transfers import update_bottom_message
        await update_bottom_message(interaction.client, interaction.channel.id)

    @classmethod
    async def submit_transfer(
        cls, interaction: discord.Interaction, user_db: User, destination: Division, name_age: str,
        timezone: str, online_prime: str, motivation: str, view_factory: Callable[[TransferRequest, Division], discord.ui.View],
    ) -> None:
        """Оформляет и публикует заявление на перевод от лица бойца.

        Raises:
            ServiceError: Если у бойца уже есть открытое заявление, либо не пройдены правила перевода.
        """
        await cls.validate_no_active_request(interaction.user.id)
        await cls.validate_transfer_rules(user_db, destination)

        request = await cls.create_request(
            user_db=user_db, destination=destination, name_age=name_age, timezone=timezone,
            online_prime=online_prime, motivation=motivation,
        )
        await cls.publish_request(interaction=interaction, request=request, user_db=user_db, destination=destination, view=view_factory(request, destination))

    @classmethod
    async def approve_old_division(cls, interaction: discord.Interaction, request_id: int, officer: User) -> TransferActionResult:
        """Согласовывает перевод руководством старого подразделения (этап OLD_DIVISION_REVIEW -> NEW_DIVISION_REVIEW).

        Raises:
            ServiceError: Если запрос не найден/уже обработан, офицер — автор заявления,
                либо не имеет прав на согласование от старого подразделения.
        """
        req = await TransferRequest.find_one(TransferRequest.id == request_id)
        if not req or req.status != "OLD_DIVISION_REVIEW":
            raise ServiceError(f"❌ Запрос #{request_id} не найден или уже обработан.")

        if officer.discord_id == req.user_id:
            raise ServiceError("❌ Вы не можете рассматривать собственное заявление.")

        cls.validate_reviewer_permissions(officer, [req.old_division_id],
                                           "❌ У вас нет прав согласовывать перевод от старого подразделения.")

        updated_dict = await atomic_status_transition(
            TransferRequest.get_pymongo_collection(), request_id, "OLD_DIVISION_REVIEW", "NEW_DIVISION_REVIEW",
            extra_fields={"old_reviewer_id": interaction.user.id, "old_reviewed_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Запрос #{request_id} уже обработан.")

        request = TransferRequest(**updated_dict)
        target_user = await User.get_by_discord_id(request.user_id) or User(discord_id=request.user_id, pre_inited=True)
        new_division = divisions.get_division(request.new_division_id)
        if not new_division:
            raise ServiceError("❌ Целевое подразделение не найдено.")

        from cogs.transfers import update_bottom_message
        await update_bottom_message(interaction.client, interaction.channel.id)

        from ui.views.transfers import TransferManagementView
        mentions = build_request_mentions(request.user_id, new_division.division_id)
        return TransferActionResult(
            request=request,
            embed=transfer_embed(request, target_user),
            view=TransferManagementView(request, new_division),
            mention_content=mentions,
            reply_content=mentions or None,
        )

    @classmethod
    async def approve_new_division(cls, interaction: discord.Interaction, request_id: int, officer: User) -> TransferActionResult:
        """Окончательно одобряет перевод руководством нового подразделения: назначает подразделение и должность.

        Raises:
            ServiceError: Если запрос не найден/уже обработан, офицер — автор заявления,
                либо не имеет прав одобрять перевод в целевое подразделение.
        """
        req = await TransferRequest.find_one(TransferRequest.id == request_id)
        if not req or req.status != "NEW_DIVISION_REVIEW":
            raise ServiceError(f"❌ Запрос #{request_id} не найден или уже обработан.")

        if officer.discord_id == req.user_id:
            raise ServiceError("❌ Вы не можете рассматривать собственное заявление.")

        cls.validate_reviewer_permissions(officer, [req.new_division_id],
                                           "❌ У вас нет прав одобрять перевод в данное подразделение.")

        updated_dict = await atomic_status_transition(
            TransferRequest.get_pymongo_collection(), request_id, "NEW_DIVISION_REVIEW", "APPROVED",
            extra_fields={"new_reviewer_id": interaction.user.id, "new_reviewed_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Запрос #{request_id} уже обработан.")

        request = TransferRequest(**updated_dict)
        destination = divisions.get_division(request.new_division_id)

        target_user = await User.get_by_discord_id(request.user_id) or User(discord_id=request.user_id, pre_inited=True)
        target_user.division = request.new_division_id
        target_user.position = destination.positions[-1].name if destination and destination.positions else None
        await target_user.save()

        target_member = await interaction.client.getch_member(request.user_id)
        if target_member:
            await MemberService.sync_member_discord(member=target_member, user_db=target_user, reason=f"Одобрен перевод by {interaction.user.id}")

        old_div = divisions.get_division(request.old_division_id)
        action = AuditAction.DIVISION_ASSIGNED if not (old_div and old_div.positions) else AuditAction.DIVISION_CHANGED
        await audit_logger.log_action(action=action, initiator=interaction.user, target=target_user.discord_id)

        div_name = destination.name if destination else "подразделение"
        await notify_transfer_approved(interaction.client, request.user_id, div_name)

        from ui.views.indicators import indicator_view
        return TransferActionResult(
            request=request, embed=transfer_embed(request, target_user), view=indicator_view("Одобрено", emoji="👍"),
        )

    @classmethod
    async def reject_transfer(cls, interaction: discord.Interaction, request_id: int, officer: User, reason: str) -> TransferActionResult:
        """Отклоняет заявление на перевод на любом из этапов рассмотрения.

        Raises:
            ServiceError: Если запрос не найден/уже обработан, офицер — автор заявления,
                либо не имеет прав отклонить данное заявление.
        """
        req = await TransferRequest.find_one(TransferRequest.id == request_id)
        if not req or req.status not in ("OLD_DIVISION_REVIEW", "NEW_DIVISION_REVIEW"):
            raise ServiceError(f"❌ Запрос #{request_id} не найден или уже обработан.")

        if officer.discord_id == req.user_id:
            raise ServiceError("❌ Вы не можете рассматривать собственное заявление.")

        cls.validate_reviewer_permissions(officer, [req.old_division_id, req.new_division_id],
                                           "❌ У вас нет прав отклонять данное заявление.")

        now = discord.utils.utcnow()
        update_fields: dict = {"status": "REJECTED", "reject_reason": reason}
        if officer.division == req.old_division_id:
            update_fields["old_reviewer_id"] = interaction.user.id
            update_fields["old_reviewed_at"] = now
        else:
            update_fields["new_reviewer_id"] = interaction.user.id
            update_fields["new_reviewed_at"] = now

        updated_dict = await atomic_status_transition(
            TransferRequest.get_pymongo_collection(), request_id, ["OLD_DIVISION_REVIEW", "NEW_DIVISION_REVIEW"], "REJECTED",
            extra_fields=update_fields,
        )
        if not updated_dict:
            raise ServiceError(f"❌ Запрос #{request_id} уже обработан.")

        request = TransferRequest(**updated_dict)
        target_user = await User.get_by_discord_id(request.user_id) or User(discord_id=request.user_id, pre_inited=True)
        await notify_transfer_rejected(interaction.client, request.user_id, reason)

        from ui.views.indicators import indicator_view
        return TransferActionResult(
            request=request, embed=transfer_embed(request, target_user), view=indicator_view("Отклонено", emoji="👎"),
        )

    @staticmethod
    async def cancel_transfer(request_id: int, user_id: int) -> None:
        """Атомарно отменяет собственную заявку на перевод, пока она на первом этапе."""
        updated_dict = await atomic_status_transition(
            TransferRequest.get_pymongo_collection(), request_id, ["OLD_DIVISION_REVIEW", "NEW_DIVISION_REVIEW"], "REJECTED",
            extra_fields={"reject_reason": "Отменено автором"},
            extra_filter={"user_id": user_id},
        )
        if not updated_dict:
            raise ServiceError("❌ Заявку можно отменить только на этапе рассмотрения старым подразделением.")