import datetime
import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core import config
from core.config import IC_MAX_DAYS, OOC_MAX_DAYS, OOC_MIN_DAYS, IC_MIN_DAYS
from core.exceptions import ServiceError
from database.counters import get_next_id
from database.models import User, LeaveType, LeaveRequest
from services.member import MemberService
from services.notifications import notify_leave_rejected, notify_leave_approved, notify_leave_cancelled
from ui.embeds.leave import leave_embed
from utils.helpers import build_request_mentions, safe_publish_request
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import is_senior_officer, is_higher_rank

logger = logging.getLogger(__name__)

LEAVE_DAY_BOUNDS = {
    LeaveType.IC: (IC_MIN_DAYS, IC_MAX_DAYS),
    LeaveType.OOC: (OOC_MIN_DAYS, OOC_MAX_DAYS),
}


@dataclass(frozen=True)
class LeaveActionResult:
    """Результат approve/reject/annul: данные для отрисовки, без discord-вызовов внутри сервиса.

    Attributes:
        request: Обновлённая заявка после атомарного перехода статуса.
        embed: Готовый эмбед для сообщения заявки.
        view: Готовая View для сообщения (индикатор статуса или панель управления).
        message: Текст итогового ответа автору действия.
    """
    request: LeaveRequest
    embed: discord.Embed
    view: discord.ui.View
    message: str


class LeaveService:

    @staticmethod
    async def validate_can_apply(user_db: User, leave_type: LeaveType) -> None:
        """Проверяет отсутствие активной заявки и лимит IC-отпуска (раз в календарный месяц по МСК).

        Raises:
            ServiceError: Если есть заявление на рассмотрении, либо IC-отпуск уже брался в этом месяце.
        """
        user_requests = await LeaveRequest.find(
            LeaveRequest.user_id == user_db.discord_id,
            LeaveRequest.status != "REJECTED",
        ).to_list()

        pending_req = next((req for req in user_requests if req.status == "PENDING"), None)
        if pending_req:
            raise ServiceError(f"❌ Ваше предыдущее заявление #{pending_req.id} еще находится на рассмотрении.")

        if leave_type == LeaveType.IC:
            now_msk = discord.utils.utcnow() + datetime.timedelta(hours=3)
            msk_month_start_utc = now_msk.replace(day=1, hour=0, minute=0, second=0,
                                                  microsecond=0) - datetime.timedelta(hours=3)

            used_this_month = any(
                req.leave_type == LeaveType.IC and req.created_at >= msk_month_start_utc
                for req in user_requests
            )
            if used_this_month:
                raise ServiceError("❌ В этом месяце вы уже использовали свое право на IC отпуск.")

    @staticmethod
    def validate_bounds(leave_type: LeaveType, start_date: datetime.date, end_date: datetime.date) -> None:
        """Проверяет корректность дат и длительность отпуска относительно лимитов типа.

        Raises:
            ServiceError: Если дата в прошлом, конец раньше начала, либо срок вне допустимых границ.
        """
        today = (discord.utils.utcnow() + datetime.timedelta(hours=3)).date()
        if start_date < today:
            raise ServiceError("❌ Дата начала не может быть в прошлом.")

        if end_date <= start_date:
            raise ServiceError("❌ Дата выхода должна быть позже даты начала.")

        days = (end_date - start_date).days
        min_days, max_days = LEAVE_DAY_BOUNDS[leave_type]

        if not (min_days <= days <= max_days):
            raise ServiceError(
                f"❌ {leave_type.value} отпуск можно взять на {min_days}–{max_days} дней (у вас {days} дн.)."
            )

    @staticmethod
    async def create_request(
            user_db: User, leave_type: LeaveType, start_dt: datetime.datetime,
            end_dt: datetime.datetime, reason: str,
    ) -> LeaveRequest:
        """Создаёт и сохраняет заявление на отпуск в базе данных."""
        new_id = await get_next_id("leave_requests")
        request = LeaveRequest(
            id=new_id,
            user_id=user_db.discord_id,
            leave_type=leave_type,
            reason=reason,
            starts_at=start_dt.astimezone(datetime.timezone.utc),
            ends_at=end_dt.astimezone(datetime.timezone.utc),
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(interaction: discord.Interaction, request: LeaveRequest, user_db: User, view: discord.ui.View) -> None:
        """Публикует заявление в канал и обновляет статус-бар соответствующего типа отпусков.

        Raises:
            ServiceError: Если публикация не удалась.
        """
        embed = leave_embed(request, user_db)
        content_mentions = build_request_mentions(user_id=user_db.discord_id, user_division_id=user_db.division)

        msg_id = await safe_publish_request(
            channel=interaction.channel, request=request, embed=embed, content=content_mentions, view=view,
        )
        request.message_id = msg_id
        await request.save()

        from cogs.leave import update_bottom_message
        await update_bottom_message(interaction.client, request.leave_type)

    @staticmethod
    async def submit_leave(
            interaction: discord.Interaction, user_db: User, leave_type: LeaveType,
            start_date: datetime.date, end_date: datetime.date, reason: str,
            view_factory: Callable[[int], discord.ui.View],
    ) -> None:
        """Оформляет и публикует заявление на отпуск от имени бойца.

        Raises:
            ServiceError: Если даты не проходят валидацию либо публикация не удалась.
        """
        LeaveService.validate_bounds(leave_type, start_date, end_date)

        msk = datetime.timezone(datetime.timedelta(hours=3))
        start_dt = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=msk)
        end_dt = datetime.datetime.combine(end_date, datetime.time.min, tzinfo=msk)

        request = await LeaveService.create_request(
            user_db=user_db, leave_type=leave_type, start_dt=start_dt, end_dt=end_dt, reason=reason,
        )
        await LeaveService.publish_request(
            interaction=interaction, request=request, user_db=user_db, view=view_factory(request.id),
        )

    @staticmethod
    async def _validate_reviewer_permissions(officer: User, requester_id: int) -> None:
        """Проверяет звание и субординацию офицера, рассматривающего заявление."""
        if not is_senior_officer(officer):
            raise ServiceError(f"❌ Доступно со звания {config.RANKS[config.RankIndex.MAJOR]}.")

        requester = await User.get_by_discord_id(requester_id)
        if requester and not is_higher_rank(officer, requester):
            raise ServiceError("❌ Вы не можете рассматривать заявку военнослужащего равного или старшего звания.")

    @classmethod
    async def approve_leave(cls, interaction: discord.Interaction, request_id: int, officer: User) -> LeaveActionResult:
        """Одобряет отпуск: субординация, атомарный переход статуса, активация ролей или отложенный таймер.

        Raises:
            ServiceError: Если заявление не найдено, уже обработано, нарушена субординация,
                либо боец больше не состоит на службе.
        """
        req = await LeaveRequest.find_one(LeaveRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или уже обработано.")

        await cls._validate_reviewer_permissions(officer, req.user_id)

        target_user_db = await User.get_by_discord_id(req.user_id)
        if not target_user_db or target_user_db.rank is None:
            await atomic_status_transition(
                LeaveRequest.get_pymongo_collection(), request_id, "PENDING", "REJECTED",
                extra_fields={"reviewer_id": interaction.user.id, "reviewed_at": discord.utils.utcnow()},
            )
            raise ServiceError("❌ Пользователь не состоит на службе.")

        now = discord.utils.utcnow()
        member = await interaction.client.getch_member(req.user_id)

        updated_dict = await atomic_status_transition(
            LeaveRequest.get_pymongo_collection(), request_id, "PENDING", "APPROVED",
            extra_fields={
                "reviewer_id": interaction.user.id,
                "approved_at": now,
                "original_nick": member.display_name if member else None,
            },
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или уже обработано.")

        request = LeaveRequest(**updated_dict)
        start_t = request.starts_at

        from cogs.leave import schedule_leave_activation, schedule_leave_expiry

        if now >= start_t:
            target_user_db.leave_status = request.leave_type.value
            await target_user_db.save()
            if member:
                await MemberService.sync_member_discord(
                    member=member, user_db=target_user_db, reason=f"{request.leave_type.value} отпуск одобрен",
                )
            result_msg = "✅ Отпуск одобрен и активирован."
        else:
            await schedule_leave_activation(interaction.client, request)
            result_msg = f"✅ Отпуск одобрен. Роли будут выданы автоматически {discord.utils.format_dt(start_t, 'd')}."

        await schedule_leave_expiry(interaction.client, request)
        await notify_leave_approved(interaction.client, request.user_id, request)

        from ui.views.leave import LeaveManagementView
        return LeaveActionResult(
            request=request,
            embed=leave_embed(request, target_user_db),
            view=LeaveManagementView(request.id, status="APPROVED"),
            message=result_msg,
        )

    @classmethod
    async def reject_leave(cls, interaction: discord.Interaction, request_id: int, officer: User) -> LeaveActionResult:
        """Отклоняет заявление на отпуск атомарным переходом статуса.

        Raises:
            ServiceError: Если заявление не найдено, уже обработано, либо нарушена субординация.
        """
        req = await LeaveRequest.find_one(LeaveRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или уже обработано.")

        await cls._validate_reviewer_permissions(officer, req.user_id)

        updated_dict = await atomic_status_transition(
            LeaveRequest.get_pymongo_collection(), request_id, "PENDING", "REJECTED",
            extra_fields={"reviewer_id": interaction.user.id, "reviewed_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или уже обработано.")

        request = LeaveRequest(**updated_dict)
        target_user_db = await User.get_by_discord_id(request.user_id)
        await notify_leave_rejected(interaction.client, request.user_id, request)

        from ui.views.indicators import indicator_view
        return LeaveActionResult(
            request=request,
            embed=leave_embed(request, target_user_db),
            view=indicator_view(f"Отклонил {interaction.user.display_name}", emoji="👎"),
            message="✅ Заявка отклонена.",
        )

    @classmethod
    async def annul_leave(cls, interaction: discord.Interaction, request_id: int, initiator: User) -> LeaveActionResult:
        """Аннулирует одобренный отпуск: снимает статус отпуска, отменяет таймеры, синхронизирует роли.

        Доступно Майору+ либо самому бойцу.

        Raises:
            ServiceError: Если заявление не найдено, не одобрено, либо нарушена субординация.
        """
        req = await LeaveRequest.find_one(LeaveRequest.id == request_id)
        if not req or req.status != "APPROVED":
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или не одобрено.")

        is_own = interaction.user.id == req.user_id
        if not is_own:
            await cls._validate_reviewer_permissions(initiator, req.user_id)

        updated_dict = await atomic_status_transition(
            LeaveRequest.get_pymongo_collection(), request_id, "APPROVED", "ANNULLED",
            extra_fields={"annuller_id": interaction.user.id, "annulled_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявление #{request_id} уже обработано.")

        request = LeaveRequest(**updated_dict)
        target_user_db = await User.get_by_discord_id(request.user_id)
        if target_user_db:
            target_user_db.leave_status = None
            await target_user_db.save()

        member = await interaction.client.getch_member(request.user_id)
        if member and target_user_db:
            await MemberService.sync_member_discord(
                member=member, user_db=target_user_db, reason=f"{request.leave_type.value} отпуск аннулирован",
                original_nick=request.original_nick,
            )

        from cogs.leave import cancel_activation_timer, cancel_leave_timer
        cancel_leave_timer(request.id)
        cancel_activation_timer(request.id)

        await notify_leave_cancelled(interaction.client, request.user_id, request)

        from ui.views.indicators import indicator_view
        return LeaveActionResult(
            request=request,
            embed=leave_embed(request, target_user_db),
            view=indicator_view(f"Аннулировал {interaction.user.display_name}"),
            message="✅ Отпуск аннулирован.",
        )

    @staticmethod
    async def cancel_leave(request_id: int, user_id: int) -> None:
        """Атомарно отменяет нерассмотренное заявление самим автором. Удаление сообщения — на вызывающем.

        Raises:
            ServiceError: Если заявление уже обработано офицером или не найдено.
        """
        updated_dict = await atomic_status_transition(
            LeaveRequest.get_pymongo_collection(), request_id, "PENDING", "REJECTED",
            extra_fields={"annuller_id": user_id, "annulled_at": discord.utils.utcnow()},
            extra_filter={"user_id": user_id},
        )
        if not updated_dict:
            raise ServiceError("❌ Заявление уже обработано офицером или не найдено.")