import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core.config import RANKS, RoleId, DivisionId
from core.exceptions import ServiceError
from database import divisions
from database.counters import get_next_id
from database.models import ReinstatementData, ReinstatementRequest, User
from services.audit import AuditAction, audit_logger
from services.member import MemberService
from services.notifications import notify_reinstatement_approved, notify_reinstatement_rejected, \
    notify_reinstatement_attestation
from ui.embeds.reinstatement import reinstatement_embed
from utils.helpers import safe_publish_request, build_mentions
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import is_high_command

logger = logging.getLogger(__name__)

BASIC_ATTESTATION_ROLES = (
    RoleId.ATTESTATION.value,
    RoleId.REINFORCEMENT.value,
)


@dataclass(frozen=True)
class ReinstatementActionResult:
    """Результат start_attestation/approve/reject: данные для отрисовки, без discord-вызовов внутри сервиса."""
    request: ReinstatementRequest
    embed: discord.Embed
    view: discord.ui.View
    mention_ids: list[int] | None = None


class ReinstatementService:

    @staticmethod
    def validate_reviewer_permissions(officer: User, target_user_id: int) -> None:
        """Проверяет права на обработку заявки (Полковник+ или УВП, не автор).

        Raises:
            ServiceError: Если офицер — автор заявления, либо не проходит по званию/подразделению.
        """
        if officer.discord_id == target_user_id:
            raise ServiceError("❌ Вы не можете обрабатывать собственное заявление.")

        has_perm = is_high_command(officer)
        if not has_perm and officer.division:
            div = divisions.get_division(officer.division)
            if div and div.abbreviation == "УВП":
                has_perm = True

        if not has_perm:
            raise ServiceError("❌ У вас нет прав взаимодействовать с этой заявкой (требуется Полковник+ или УВП).")

    @staticmethod
    async def validate_no_active_request(user_id: int) -> None:
        """Проверяет, что у пользователя нет заявления в статусе PENDING или ATTESTATION."""
        opened_request = await ReinstatementRequest.find_one(
            ReinstatementRequest.user == user_id,
            {"status": {"$in": ["PENDING", "ATTESTATION"]}},
        )
        if opened_request is not None:
            raise ServiceError("### У вас уже есть открытое заявление на рассмотрении.\nОжидайте его рассмотрения.")

    @staticmethod
    async def create_request(user_id: int, full_name: str, all_documents: str, army_pass: str) -> ReinstatementRequest:
        """Создаёт и сохраняет заявление на восстановление в базе данных."""
        new_id = await get_next_id("reinstatement_requests")
        request = ReinstatementRequest(
            id=new_id, user=user_id,
            data=ReinstatementData(full_name=full_name, all_documents=all_documents, army_pass=army_pass),
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(interaction: discord.Interaction, request: ReinstatementRequest, user_db: User, view: discord.ui.View) -> None:
        """Публикует заявление в канал УВП."""
        division = divisions.get_division_by_abbreviation("УВП")

        await safe_publish_request(
            channel=interaction.channel,
            request=request,
            embed=reinstatement_embed(request, user_db),
            content=build_mentions(user_db.discord_id, division.role_id if division else ()),
            view=view,
        )

        from cogs.reinstatement import update_bottom_message
        await update_bottom_message(interaction.client)

    @classmethod
    async def submit_reinstatement(
        cls, interaction: discord.Interaction, user_db: User, all_documents: str, army_pass: str,
        view_factory: Callable[[int], discord.ui.View],
    ) -> None:
        """Оформляет и публикует заявление на восстановление от лица кандидата.

        Raises:
            ServiceError: Если у кандидата уже есть открытое заявление.
        """
        await cls.validate_no_active_request(interaction.user.id)
        request = await cls.create_request(
            user_id=interaction.user.id, full_name=user_db.full_name or interaction.user.display_name,
            all_documents=all_documents, army_pass=army_pass,
        )
        await cls.publish_request(interaction=interaction, request=request, user_db=user_db, view=view_factory(request.id))

    @staticmethod
    async def _grant_attestation_roles(guild: discord.Guild | None, user_id: int) -> None:
        """Выдаёт временные роли переаттестации, игнорируя отдельные сбои Discord API."""
        if not guild:
            return
        for role_id in BASIC_ATTESTATION_ROLES:
            try:
                await guild._state.http.add_role(guild.id, user_id, role_id)
            except discord.HTTPException:
                logger.warning(f"Не удалось выдать роль {role_id} пользователю {user_id} при переаттестации.")

    @staticmethod
    async def _revoke_attestation_roles(guild: discord.Guild | None, user_id: int) -> None:
        """Снимает временные роли переаттестации, игнорируя отдельные сбои Discord API."""
        if not guild:
            return
        for role_id in BASIC_ATTESTATION_ROLES:
            try:
                await guild._state.http.remove_role(guild.id, user_id, role_id)
            except discord.HTTPException:
                logger.warning(f"Не удалось снять роль {role_id} с пользователя {user_id} при переаттестации.")

    @classmethod
    async def start_attestation(cls, interaction: discord.Interaction, request_id: int, officer: User) -> ReinstatementActionResult:
        """Переводит заявку в статус переаттестации (ATTESTATION) и выдаёт временные роли.

        Raises:
            ServiceError: Если заявление не найдено/уже обработано, либо нарушены права доступа.
        """
        req = await ReinstatementRequest.find_one(ReinstatementRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или уже обработано.")

        cls.validate_reviewer_permissions(officer, req.user)

        updated_dict = await atomic_status_transition(
            ReinstatementRequest.get_pymongo_collection(), request_id, "PENDING", "ATTESTATION",
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявление #{request_id} уже обработано.")

        request = ReinstatementRequest(**updated_dict)
        await cls._grant_attestation_roles(interaction.guild, request.user)
        await notify_reinstatement_attestation(interaction.client, request.user)

        target_user_db = await User.get_by_discord_id(request.user)
        from ui.views.reinstatement import ReinstatementAttestationView
        return ReinstatementActionResult(
            request=request,
            embed=reinstatement_embed(request, target_user_db),
            view=ReinstatementAttestationView(request_id),
            mention_ids=[request.user, interaction.user.id],
        )

    @classmethod
    async def approve_reinstatement(cls, interaction: discord.Interaction, request_id: int, rank_index: int, officer: User) -> ReinstatementActionResult:
        """Окончательно одобряет восстановление с присвоением звания и зачислением в ВБП.

        Raises:
            ServiceError: Если заявление не найдено/не на этапе переаттестации, либо нарушены права доступа.
        """
        req = await ReinstatementRequest.find_one(ReinstatementRequest.id == request_id)
        if not req or req.status != "ATTESTATION":
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или не на этапе переаттестации.")

        cls.validate_reviewer_permissions(officer, req.user)

        updated_dict = await atomic_status_transition(
            ReinstatementRequest.get_pymongo_collection(), request_id, "ATTESTATION", "APPROVED",
            extra_fields={"rank": rank_index},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявление #{request_id} уже обработано.")

        request = ReinstatementRequest(**updated_dict)

        target_user_db = await User.get_by_discord_id(request.user)
        if not target_user_db:
            target_user_db = User(discord_id=request.user, pre_inited=True)
        target_user_db.rank = rank_index
        target_user_db.division = DivisionId.VPB
        await target_user_db.save()

        member = await interaction.client.getch_member(request.user)
        if member:
            await cls._revoke_attestation_roles(interaction.guild, request.user)
            await MemberService.sync_member_discord(member=member, user_db=target_user_db, reason=f"Восстановление #{request.id}")

        await audit_logger.log_action(AuditAction.REINSTATEMENT, interaction.user, request.user)
        await notify_reinstatement_approved(interaction.client, request.user, RANKS[rank_index])

        from ui.views.indicators import indicator_view
        return ReinstatementActionResult(
            request=request,
            embed=reinstatement_embed(request, target_user_db),
            view=indicator_view(f"Одобрил {interaction.user.display_name}", emoji="👍"),
        )

    @classmethod
    async def reject_reinstatement(cls, interaction: discord.Interaction, request_id: int, officer: User, reason: str) -> ReinstatementActionResult:
        """Отклоняет заявление на восстановление на этапе PENDING или ATTESTATION.

        Raises:
            ServiceError: Если заявление не найдено/уже обработано, либо нарушены права доступа.
        """
        req = await ReinstatementRequest.find_one(ReinstatementRequest.id == request_id)
        if not req or req.status not in ("PENDING", "ATTESTATION"):
            raise ServiceError(f"❌ Заявление #{request_id} не найдено или уже обработано.")

        cls.validate_reviewer_permissions(officer, req.user)

        updated_dict = await atomic_status_transition(
            ReinstatementRequest.get_pymongo_collection(), request_id, ["PENDING", "ATTESTATION"], "REJECTED",
            extra_fields={"reject_reason": reason},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Заявление #{request_id} уже обработано.")

        request = ReinstatementRequest(**updated_dict)
        await cls._revoke_attestation_roles(interaction.guild, request.user)

        target_user_db = await User.get_by_discord_id(request.user)
        await notify_reinstatement_rejected(interaction.client, request.user, reason=reason)

        from ui.views.indicators import indicator_view
        return ReinstatementActionResult(
            request=request,
            embed=reinstatement_embed(request, target_user_db),
            view=indicator_view(f"Отклонил {interaction.user.display_name}", emoji="👎"),
        )