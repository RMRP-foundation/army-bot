import datetime
import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core import constants, config
from core.config import ROLE_REQUIRED_RANK_INDICES, RoleId, DivisionId
from core.constants import RANKS
from core.exceptions import ServiceError
from database import divisions
from database.counters import get_next_id
from database.models import ExtendedRoleData, RoleData, RoleRequest, RoleType, User
from services.audit import AuditAction, audit_logger
from services.member import MemberService
from services.notifications import notify_role_approved, notify_role_rejected
from ui.embeds.role_getting import role_embed
from utils.helpers import build_officer_mentions, safe_edit_message, safe_publish_request, build_mentions
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import check_rank_silent
from utils.user_data import set_name_if_changed

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoleActionResult:
    """Результат approve/reject: данные для отрисовки, без discord-вызовов внутри сервиса."""
    request: RoleRequest
    embed: discord.Embed
    view: discord.ui.View
    message: str


class RoleService:

    @staticmethod
    def get_required_rank(role_type: RoleType) -> int:
        """Возвращает минимальный индекс ранга, требуемый для проверки запрошенной роли."""
        return ROLE_REQUIRED_RANK_INDICES.get(role_type, constants.RankIndex.COLONEL)

    @staticmethod
    def _validate_reviewer_permissions(officer: User, role_type: RoleType, applicant_id: int) -> None:
        """Проверяет права офицера на рассмотрение заявки на роль.

        Raises:
            ServiceError: Если офицер — автор заявки, либо не проходит по званию (кроме ВК для ARMY/KMB).
        """
        if officer.discord_id == applicant_id:
            raise ServiceError("❌ Вы не можете рассматривать собственную заявку.")

        required_rank = RoleService.get_required_rank(role_type)
        if check_rank_silent(officer, required_rank):
            return

        if role_type in (RoleType.ARMY, RoleType.KMB) and officer.division:
            div = divisions.get_division(officer.division)
            if div and div.abbreviation == "ВК":
                return

        raise ServiceError(f"❌ У вас нет прав. Требуется звание {RANKS[required_rank]}+.")

    @staticmethod
    async def _get_user_pending_request(user_id: int) -> RoleRequest | None:
        """Находит активную (PENDING) заявку пользователя."""
        return await RoleRequest.find_one(RoleRequest.user == user_id, RoleRequest.status == "PENDING")

    @staticmethod
    async def _expire_stale_request(request: RoleRequest, channel: discord.TextChannel | discord.Thread | None) -> None:
        """Атомарно отклоняет просроченную заявку и снимает кнопки с её сообщения.

        Raises:
            ServiceError: Если заявка уже обработана параллельно.
        """
        updated_dict = await atomic_status_transition(
            RoleRequest.get_pymongo_collection(), request.id, "PENDING", "REJECTED",
        )
        if not updated_dict:
            raise ServiceError(f"### ⏳ Заявка #{request.id} уже подана\nПодождите завершения.")

        if request.message_id and channel:
            request.status = "REJECTED"
            await safe_edit_message(channel.get_partial_message(request.message_id), embed=role_embed(request), view=None)

    @classmethod
    async def validate_can_apply(
            cls, user_id: int, channel: discord.TextChannel | discord.Thread | None, check_blacklist: bool = False,
    ) -> None:
        """Проверяет возможность подачи заявки на роль: отсутствие службы, кулдаун, черный список.

        Raises:
            ServiceError: Если пользователь уже на службе, есть недавняя заявка, либо действует ЧС.
        """
        user = await User.get_by_discord_id(user_id)
        if user and user.rank is not None:
            raise ServiceError("❌ Вы уже состоите на службе.")

        old_pending = await cls._get_user_pending_request(user_id)
        if old_pending is not None:
            now = discord.utils.utcnow()
            sent_at = old_pending.sent_at
            cooldown = datetime.timedelta(hours=config.ROLE_RESUBMIT_COOLDOWN_HOURS)

            if now - sent_at < cooldown:
                retry_at = sent_at + cooldown
                raise ServiceError(
                    f"### ⏳ Заявка #{old_pending.id} уже подана\n"
                    f"Повторно подать можно {discord.utils.format_dt(retry_at, 'R')}, "
                    f"если текущая не будет рассмотрена."
                )
            await cls._expire_stale_request(old_pending, channel)

        if check_blacklist and user and user.blacklist:
            ends_at_str = discord.utils.format_dt(user.blacklist.ends_at, 'd') if user.blacklist.ends_at else "Бессрочно"
            raise ServiceError(
                "### Вы не можете подать заявление на роль, так как на вас наложен черный список.\n"
                f"Дата окончания: {ends_at_str}."
            )

    @staticmethod
    async def create_request(
        user_id: int, role_type: RoleType, data: RoleData | None = None, extended_data: ExtendedRoleData | None = None,
    ) -> RoleRequest:
        """Создаёт и сохраняет запрос на роль в базе данных."""
        new_id = await get_next_id("role_requests")
        request = RoleRequest(
            id=new_id, user=user_id, role_type=role_type, data=data, extended_data=extended_data, status="PENDING",
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(
        interaction: discord.Interaction, request: RoleRequest, view: discord.ui.View, mention_content: str | None = None,
    ) -> None:
        """Публикует заявку на роль в текущий служебный канал."""
        msg_id = await safe_publish_request(
            channel=interaction.channel, request=request, embed=role_embed(request),
            content=mention_content or build_mentions(request.user), view=view,
        )
        request.message_id = msg_id
        await request.save()

        from cogs.role_getting import update_bottom_message
        await update_bottom_message(interaction.client)

    @classmethod
    async def submit_role(
        cls, interaction: discord.Interaction, role_type: RoleType, view_factory: Callable[[int], discord.ui.View],
        data: RoleData | None = None, extended_data: ExtendedRoleData | None = None,
    ) -> None:
        """Оформляет и публикует заявку на роль от лица кандидата.

        Raises:
            ServiceError: Если кандидат не может подать заявку (уже на службе, кулдаун, ЧС).
        """
        check_bl = role_type in (RoleType.ARMY, RoleType.KMB)
        await cls.validate_can_apply(interaction.user.id, interaction.channel, check_blacklist=check_bl)

        mention_content = None
        if role_type in (RoleType.SUPPLY_ACCESS, RoleType.GOV_EMPLOYEE):
            mention_content = build_officer_mentions(interaction.user.id, cls.get_required_rank(role_type))

        request = await cls.create_request(user_id=interaction.user.id, role_type=role_type, data=data, extended_data=extended_data)
        await cls.publish_request(interaction=interaction, request=request, view=view_factory(request.id), mention_content=mention_content)

    @classmethod
    async def _handle_military_role_approval(cls, interaction: discord.Interaction, request: RoleRequest) -> None:
        """Оформляет принятие на службу в ряды ВС РФ или КМБ."""
        if not request.data:
            return

        user = await User.get_by_discord_id(request.user) or User(discord_id=request.user)

        if user and user.blacklist:
            ends_at_str = discord.utils.format_dt(user.blacklist.ends_at, 'd') if user.blacklist.ends_at else "Бессрочно"
            raise ServiceError(
                "### Вы не можете одобрить данное заявление на роль, так как на автора наложен черный список.\n"
                f"Дата окончания: {ends_at_str}."
            )

        user.rank = 0
        user.division = DivisionId.VA if request.role_type == RoleType.ARMY else DivisionId.KMB
        set_name_if_changed(user, request.data.full_name)
        user.static = request.data.static_id
        user.invited_at = discord.utils.utcnow()
        user.pre_inited = True
        await user.save()

        target_member = await interaction.client.getch_member(request.user)
        if target_member:
            role_name = config.ROLE_DISPLAY_NAMES.get(request.role_type, "военнослужащего")
            await MemberService.sync_member_discord(
                member=target_member, user_db=user, reason=f"Одобрено получение роли {role_name} by {interaction.user.id}",
            )

        await audit_logger.log_action(action=AuditAction.INVITED, initiator=interaction.user, target=user.discord_id)

    @classmethod
    async def _handle_external_role_approval(cls, interaction: discord.Interaction, request: RoleRequest) -> None:
        """Выдаёт внешние роли (Доступ к поставке / Гос. сотрудник) без зачисления в БД армии."""
        if not request.extended_data:
            return

        user = await User.get_by_discord_id(request.user) or User(discord_id=request.user, pre_inited=True)
        set_name_if_changed(user, request.extended_data.full_name)
        user.static = request.extended_data.static_id
        await user.save()

        target_member = await interaction.client.getch_member(request.user)
        if target_member and interaction.guild:
            role_id = RoleId.SUPPLY_ACCESS.value if request.role_type == RoleType.SUPPLY_ACCESS else RoleId.GOV_EMPLOYEE.value
            role = interaction.guild.get_role(role_id)
            new_roles = list(target_member.roles)
            if role and role not in new_roles:
                new_roles.append(role)

            new_nick = f"{request.extended_data.faction} | {request.extended_data.full_name}"[:32]
            role_display = config.ROLE_DISPLAY_NAMES.get(request.role_type, "Роль")
            try:
                await target_member.edit(nick=new_nick, roles=new_roles, reason=f"Одобрена роль {role_display} by {interaction.user.id}")
            except (discord.Forbidden, discord.HTTPException) as err:
                logger.warning(f"Failed to edit external member {request.user}: {err}")

    @classmethod
    async def approve_role(cls, interaction: discord.Interaction, request_id: int, officer: User) -> RoleActionResult:
        """Одобряет заявку на роль: зачисляет в армию/КМБ либо выдаёт внешнюю роль.

        Raises:
            ServiceError: Если заявка не найдена/уже обработана, либо нарушены права доступа.
        """
        req = await RoleRequest.find_one(RoleRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявка #{request_id} не найдена или уже обработана.")

        cls._validate_reviewer_permissions(officer, req.role_type, req.user)

        updated_dict = await atomic_status_transition(
            RoleRequest.get_pymongo_collection(), request_id, "PENDING", "APPROVED",
            extra_fields={"reviewer_id": interaction.user.id, "sent_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявка #{request_id} уже обработана.")

        request = RoleRequest(**updated_dict)
        if request.role_type in (RoleType.ARMY, RoleType.KMB):
            await cls._handle_military_role_approval(interaction, request)
        else:
            await cls._handle_external_role_approval(interaction, request)

        role_label = config.ROLE_DISPLAY_NAMES.get(request.role_type, "Роль")
        await notify_role_approved(interaction.client, request.user, role_label)

        from ui.views.indicators import indicator_view
        return RoleActionResult(
            request=request,
            embed=role_embed(request),
            view=indicator_view(f"Одобрил {interaction.user.display_name}", emoji="👍"),
            message="✅ Заявка одобрена.",
        )

    @classmethod
    async def reject_role(cls, interaction: discord.Interaction, request_id: int, officer: User) -> RoleActionResult:
        """Отклоняет заявку на роль.

        Raises:
            ServiceError: Если заявка не найдена/уже обработана, либо нарушены права доступа.
        """
        req = await RoleRequest.find_one(RoleRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявка #{request_id} не найдена или уже обработана.")

        cls._validate_reviewer_permissions(officer, req.role_type, req.user)

        updated_dict = await atomic_status_transition(
            RoleRequest.get_pymongo_collection(), request_id, "PENDING", "REJECTED",
            extra_fields={"reviewer_id": interaction.user.id, "sent_at": discord.utils.utcnow()},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявка #{request_id} уже обработана.")

        request = RoleRequest(**updated_dict)
        role_label = config.ROLE_DISPLAY_NAMES.get(request.role_type, "Роль")
        await notify_role_rejected(interaction.client, request.user, role_label)

        from ui.views.indicators import indicator_view
        return RoleActionResult(
            request=request,
            embed=role_embed(request),
            view=indicator_view(f"Отклонил {interaction.user.display_name}", emoji="👎"),
            message="✅ Заявка отклонена.",
        )