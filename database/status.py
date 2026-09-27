from dataclasses import dataclass
from enum import Enum

import discord


class RequestStatus(str, Enum):
    """Унифицированные статусы для всех типов заявок"""

    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ANNULLED = "annulled"
    EXPIRED = "expired"
    PROMOTED = "promoted"
    ATTESTATION = "attestation"
    # Специфичные статусы для переводов
    OLD_DIVISION_REVIEW = "old_division_review"
    NEW_DIVISION_REVIEW = "new_division_review"


@dataclass
class StatusDisplay:
    """Отображение статуса (emoji, текст, цвет)"""

    emoji: str
    text: str
    color: discord.Color


# Маппинг статусов на их отображение
STATUS_DISPLAY: dict[RequestStatus, StatusDisplay] = {
    RequestStatus.DRAFT: StatusDisplay("📝", "Черновик", discord.Color.light_grey()),
    RequestStatus.PENDING: StatusDisplay("⏳", "На рассмотрении", discord.Color.gold()),
    RequestStatus.APPROVED: StatusDisplay("✅", "Одобрено", discord.Color.green()),
    RequestStatus.REJECTED: StatusDisplay("❌", "Отклонено", discord.Color.red()),
    RequestStatus.ANNULLED: StatusDisplay("🚫", "Аннулировано", discord.Color.dark_grey()),
    RequestStatus.EXPIRED: StatusDisplay("🕐", "Истекло" , discord.Color.dark_grey()),
    RequestStatus.PROMOTED: StatusDisplay("⭐", "Повышен", discord.Color.dark_green()),
    RequestStatus.ATTESTATION: StatusDisplay("⏳","На переаттестации", discord.Colour.blue()),

    RequestStatus.OLD_DIVISION_REVIEW: StatusDisplay(
        "🔵", "На рассмотрении", discord.Color.blue()
    ),
    RequestStatus.NEW_DIVISION_REVIEW: StatusDisplay(
        "🟠", "На рассмотрении", discord.Color.orange()
    ),
}


def get_status_display(status: RequestStatus | str) -> StatusDisplay:
    """
    Получить отображение для статуса.

    Args:
        status: Статус (enum или строка)

    Returns:
        StatusDisplay с emoji, текстом и цветом
    """
    if isinstance(status, str):
        try:
            status = RequestStatus(status.lower())
        except ValueError:
            # Fallback для неизвестных статусов
            return StatusDisplay("❓", "Неизвестно", discord.Color.default())

    return STATUS_DISPLAY.get(
        status, StatusDisplay("❓", "Неизвестно", discord.Color.default())
    )
