import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core import config
from core.config import RankIndex
from core.exceptions import ServiceError
from database import divisions
from database.counters import get_next_id
from database.models import SSOPatrolRequest, User
from ui.embeds.sso_patrol import sso_patrol_embed
from utils.helpers import safe_publish_request, build_mentions
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import is_high_command

logger = logging.getLogger(__name__)

STATUS_TEXT = {"approve": ("APPROVED", "Одобрил", "👍"), "reject": ("REJECTED", "Отклонил", "👎")}


@dataclass(frozen=True)
class SSOPatrolActionResult:
    """Результат process_sso_patrol: данные для отрисовки, без discord-вызовов внутри сервиса."""
    request: SSOPatrolRequest
    embed: discord.Embed
    view: discord.ui.View
    message: str


class SSOPatrolService:

    @staticmethod
    def _check_quiz(quiz_data: list[dict], selects: list[discord.ui.Select]) -> str | None:
        """Проверяет ответы теста. Возвращает строку с проваленными вопросами или None."""
        failed_list = [
            quiz_data[i]["q"] for i, s in enumerate(selects)
            if not s.values or s.values[0] != quiz_data[i]["a"]
        ]
        return "\n".join(failed_list) if failed_list else None

    @staticmethod
    async def validate_can_apply(user_db: User) -> None:
        """Проверяет минимальное звание, отсутствие активной заявки и кулдаун после провала теста.

        Raises:
            ServiceError: Если звание ниже требуемого, есть активная заявка, либо не истёк кулдаун.
        """
        min_rank = RankIndex.SENIOR_SERGEANT
        if (user_db.rank or 0) < min_rank:
            raise ServiceError(
                f"### ❌ Отказано в подаче\n"
                f"Подать заявление на совместную работу с ССО можно только со звания "
                f"**{config.RANKS[min_rank]}** и выше."
            )

        existing = await SSOPatrolRequest.find_one(
            SSOPatrolRequest.user_id == user_db.discord_id, SSOPatrolRequest.status == "PENDING",
        )
        if existing:
            raise ServiceError(f"### У вас уже есть активная заявка #{existing.id} на рассмотрении.")

        last_fail = (
            await SSOPatrolRequest.find(
                SSOPatrolRequest.user_id == user_db.discord_id,
                SSOPatrolRequest.status == "REJECTED",
                SSOPatrolRequest.reason == "Провал теста",
            ).sort("-date").first_or_none()
        )
        if last_fail:
            now = discord.utils.utcnow()
            last_date = last_fail.date
            if (now - last_date).total_seconds() < config.SSO_FAIL_COOLDOWN:
                retry_ts = int(last_date.timestamp() + config.SSO_FAIL_COOLDOWN)
                raise ServiceError(f"### ⏳ Тест провален\nВы сможете попробовать снова <t:{retry_ts}:R>.")

    @staticmethod
    def _validate_reviewer_permissions(officer: User, target_user_id: int) -> None:
        """Проверяет права на проверку заявки (боец ССО или Высшее командование, не автор)."""
        if officer.discord_id == target_user_id:
            raise ServiceError("❌ Вы не можете рассматривать собственное заявление.")

        div_info = divisions.get_division(officer.division) if officer.division else None
        is_sso = div_info and div_info.abbreviation == "ССО"

        if not (is_sso or is_high_command(officer)):
            raise ServiceError("### ❌ Ошибка доступа\nРассматривать заявления могут только сотрудники ССО или Высшее командование.")

    @staticmethod
    async def create_request(user_id: int, full_name: str, reason: str, status: str = "PENDING") -> SSOPatrolRequest:
        """Создаёт и сохраняет запрос на патруль ССО в базе данных."""
        new_id = await get_next_id("sso_patrol_requests")
        request = SSOPatrolRequest(id=new_id, user_id=user_id, full_name=full_name, reason=reason, status=status)
        await request.create()
        return request

    @staticmethod
    async def publish_request(
        interaction: discord.Interaction, request: SSOPatrolRequest, user_db: User,
        view: discord.ui.View | None = None, failed_question: str | None = None,
    ) -> None:
        """Публикует заявление на патруль (или отчёт о провале теста) в служебный канал."""
        await safe_publish_request(
            channel=interaction.channel, request=request, embed=sso_patrol_embed(request, user_db, failed_question),
            content=build_mentions(request.user_id), view=view,
            error_message="❌ Не удалось отправить заявление в канал. Попробуйте ещё раз.",
        )

        from cogs.sso_patrol import update_bottom_message
        await update_bottom_message(interaction.client)

    @classmethod
    async def submit_sso_patrol(
        cls, interaction: discord.Interaction, user_db: User, quiz_data: list[dict],
        selects: list[discord.ui.Select], view_factory: Callable[[int], discord.ui.View],
    ) -> bool:
        """Проверяет тест и создаёт заявку. Возвращает True при успехе, False при провале теста.

        Raises:
            ServiceError: Если кандидат не может подать заявку (звание, активная заявка, кулдаун).
        """
        await cls.validate_can_apply(user_db)
        failed_text = cls._check_quiz(quiz_data, selects)
        full_name = user_db.full_name or interaction.user.display_name

        if failed_text:
            request = await cls.create_request(user_id=interaction.user.id, full_name=full_name, reason="Провал теста", status="REJECTED")
            await cls.publish_request(interaction=interaction, request=request, user_db=user_db, view=None, failed_question=failed_text)
            return False

        request = await cls.create_request(user_id=interaction.user.id, full_name=full_name, reason="Совместный патруль")
        await cls.publish_request(interaction=interaction, request=request, user_db=user_db, view=view_factory(request.id))
        return True

    @classmethod
    async def process_sso_patrol(cls, interaction: discord.Interaction, request_id: int, action: str, officer: User) -> SSOPatrolActionResult:
        """Обрабатывает заявку на патруль ССО (approve/reject) атомарным переходом статуса.

        Raises:
            ServiceError: Если заявление не найдено/уже обработано, либо нарушены права доступа.
        """
        req = await SSOPatrolRequest.find_one(SSOPatrolRequest.id == request_id)
        if not req or req.status != "PENDING":
            raise ServiceError(f"### ❌ Заявление #{request_id} не найдено или уже обработано.")

        cls._validate_reviewer_permissions(officer, req.user_id)

        target_status, status_text, emoji = STATUS_TEXT[action]
        updated_dict = await atomic_status_transition(
            SSOPatrolRequest.get_pymongo_collection(), request_id, "PENDING", target_status,
            extra_fields={"reviewer_id": interaction.user.id},
        )
        if not updated_dict:
            raise ServiceError(f"### ❌ Заявление #{request_id} уже обработано.")

        request = SSOPatrolRequest(**updated_dict)
        target_user_db = await User.get_by_discord_id(request.user_id) or User(discord_id=request.user_id, pre_inited=True)

        from ui.views.indicators import indicator_view
        return SSOPatrolActionResult(
            request=request,
            embed=sso_patrol_embed(request, target_user_db),
            view=indicator_view(f"{status_text} {interaction.user.display_name}", emoji=emoji),
            message="✅ Заявление на патруль ССО одобрено." if action == "approve" else "✅ Заявление отклонено.",
        )