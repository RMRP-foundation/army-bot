import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core import config
from core.exceptions import ServiceError
from database.counters import get_next_id
from database.models import LogisticsRequest, LogisticsType, User
from ui.embeds.logistics import logistics_embed
from utils.helpers import safe_publish_request, build_mentions
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import is_high_command
from utils.user_data import set_name_if_changed

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "approve": ("APPROVED", "Завершил", "👍"),
    "reject": ("REJECTED", "Отклонил", "👎"),
}


@dataclass(frozen=True)
class LogisticsActionResult:
    """Результат process_logistics: данные для отрисовки, без discord-вызовов внутри сервиса."""
    request: LogisticsRequest
    embed: discord.Embed
    view: discord.ui.View
    message: str


class LogisticsService:

    @staticmethod
    async def create_request(interaction: discord.Interaction, nickname: str,
                             faction: str, supply_type: LogisticsType) -> LogisticsRequest:
        """Создаёт и сохраняет запрос на поставку в базе данных."""
        new_id = await get_next_id("logistics_requests")
        request = LogisticsRequest(
            id=new_id, user_id=interaction.user.id, nickname=nickname, faction=faction, supply_type=supply_type,
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(interaction: discord.Interaction, request: LogisticsRequest, view: discord.ui.View) -> None:
        """Публикует запрос в канал поставок и обновляет статус-бар."""
        channel = interaction.client.get_channel(config.CHANNELS["logistics"])
        supplier = config.RoleId.SUPPLIER.value

        msg_id = await safe_publish_request(
            channel=channel, request=request, embed=logistics_embed(request),
            content=build_mentions(interaction.user.id, supplier),
            view=view,
        )
        request.message_id = msg_id
        await request.save()

        from cogs.logistics import update_bottom_message
        await update_bottom_message(interaction.client)

    @staticmethod
    async def submit_logistics(interaction: discord.Interaction, nickname: str, faction: str,
                               supply_type: LogisticsType, view_factory: Callable[[int], discord.ui.View]) -> None:
        """Оформляет и публикует запрос на поставку от лица представителя фракции."""
        user_db = await User.find_one(User.discord_id == interaction.user.id)
        if not user_db:
            user_db = User(discord_id=interaction.user.id, pre_inited=True)

        if set_name_if_changed(user_db, nickname):
            await user_db.save()

        request = await LogisticsService.create_request(
            interaction=interaction, nickname=nickname, faction=faction, supply_type=supply_type,
        )
        await LogisticsService.publish_request(interaction=interaction, request=request, view=view_factory(request.id))

    @staticmethod
    def _validate_reviewer_permissions(member: discord.Member, user_db: User | None) -> None:
        """Проверяет наличие роли поставщика или звания высшего командования."""
        is_supplier = any(r.id == config.RoleId.SUPPLIER.value for r in member.roles)
        is_staff = is_high_command(user_db) if user_db else False

        if not is_supplier and not is_staff:
            raise ServiceError("❌ У вас нет прав поставщика для этого действия.")

    @classmethod
    async def process_logistics(cls, interaction: discord.Interaction, request_id: int, action: str) -> LogisticsActionResult:
        """Обрабатывает запрос на поставку (approve/reject) поставщиком либо высшим командованием.

        Raises:
            ServiceError: Если недостаточно прав, либо запрос уже обработан.
        """
        user_db = await User.get_by_discord_id(interaction.user.id)
        if isinstance(interaction.user, discord.Member):
            cls._validate_reviewer_permissions(interaction.user, user_db)

        status, prefix, emoji = STATUS_MAP[action]
        updated_dict = await atomic_status_transition(
            LogisticsRequest.get_pymongo_collection(), request_id, "PENDING", status,
            extra_fields={"reviewer_name": interaction.user.display_name},
        )
        if not updated_dict:
            raise ServiceError(f"❌ Запрос #{request_id} уже обработан.")

        request = LogisticsRequest(**updated_dict)
        message = "✅ Поставка успешно завершена." if action == "approve" else "✅ Запрос на поставку отклонён."

        from ui.views.indicators import indicator_view
        return LogisticsActionResult(
            request=request,
            embed=logistics_embed(request),
            view=indicator_view(f"{prefix} {request.reviewer_name}", emoji=emoji),
            message=message,
        )