import logging

import discord

from core import config
from core.config import RankIndex
from core.exceptions import ServiceError
from database.models import User
from ui.embeds.supplies_audit import clear_supply_embed, give_supply_embed
from utils.helpers import build_mentions
from utils.permissions import is_senior_officer

logger = logging.getLogger(__name__)


class SupplyAuditService:

    @staticmethod
    def validate_officer(user_db: User) -> None:
        """Проверяет, имеет ли пользователь ранг Майор+ для работы с аудитом склада."""
        if not is_senior_officer(user_db):
            raise ServiceError(f"❌ Доступно со звания {config.RANKS[RankIndex.MAJOR]}.")

    @staticmethod
    def _parse_lines(raw: str, empty_error: str) -> list[str]:
        """Разбивает многострочный ввод на непустые строки.

        Raises:
            ServiceError: Если после фильтрации не осталось ни одной строки.
        """
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            raise ServiceError(empty_error)
        return lines

    @staticmethod
    async def _send_audit_entry(interaction: discord.Interaction, embed: discord.Embed, content: str | None = None) -> None:
        """Отправляет запись аудита в канал и обновляет статус-бар.

        Raises:
            ServiceError: Если отправка сообщения не удалась.
        """
        try:
            await interaction.channel.send(content=content, embed=embed)
        except Exception as error:
            logger.error(f"Failed to send supplies audit entry: {error}", exc_info=True)
            raise ServiceError("❌ Не удалось отправить отчёт. Попробуйте ещё раз.")

        from cogs.supplies_audit import update_bottom_message
        await update_bottom_message(interaction.client)

    @classmethod
    async def submit_give_supply(cls, interaction: discord.Interaction, user_db: User, recipient_id: int, items_raw: str, reason: str) -> None:
        """Оформляет запись о выдаче снабжения в канал аудита.

        Raises:
            ServiceError: Если недостаточно прав, список предметов пуст, либо отправка не удалась.
        """
        cls.validate_officer(user_db)
        items_list = cls._parse_lines(items_raw, "❌ Перечислите выданные предметы.")
        reason_text = reason.strip() if reason and reason.strip() else "Не указана"

        embed = give_supply_embed(
            issuer=interaction.user, recipient_id=recipient_id, items=items_list, reason=reason_text, created_at=interaction.created_at,
        )
        await cls._send_audit_entry(interaction, embed)

    @classmethod
    async def submit_clear_supply(cls, interaction: discord.Interaction, user_db: User, job_raw: str) -> None:
        """Оформляет запись о чистке склада в канал аудита.

        Raises:
            ServiceError: Если недостаточно прав, список действий пуст, либо отправка не удалась.
        """
        cls.validate_officer(user_db)
        job_list = cls._parse_lines(job_raw, "❌ Перечислите выполненные действия.")

        embed = clear_supply_embed(responsible=interaction.user, jobs=job_list, created_at=interaction.created_at)
        await cls._send_audit_entry(
            interaction, embed, content=build_mentions(interaction.user.id, config.SUPPLIES_AUDIT_MENTIONS),
        )