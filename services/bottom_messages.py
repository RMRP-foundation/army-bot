"""Реестр закреплённых нижних сообщений подачи заявок."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import discord

from core import config
from database import divisions
from utils.bottom_message import update_bottom_message, update_bottom_message_now

logger = logging.getLogger(__name__)

Build = Callable[[], tuple[discord.ui.View | discord.ui.LayoutView, discord.Embed | None]]


@dataclass(frozen=True, slots=True)
class Target:
    label: str
    channel_id: int
    build: Build


def _dismissal():
    from ui.views.dismissal import DismissalApplyView
    return DismissalApplyView(), None


def _role_getting():
    from ui.views.role_getting import RoleApplyView
    return RoleApplyView(), None


def _storage_requests():
    from ui.views.supplies import SupplyCreateView
    description = (
        "Нажмите кнопку ниже, чтобы сформировать заявку "
        "на получение амуниции и материалов.\n\n"
        "**Требования:** Звание Старший Сержант и выше.\n"
        f"**Лимиты:** Соблюдайте установленные лимиты: {config.SUPPLY_INFO_LINK}"
    )
    embed = discord.Embed(
        title="📦 Склад",
        description=description,
        color=discord.Color.blue(),
    )
    return SupplyCreateView(), embed


def _storage_audit():
    from ui.views.supplies_audit import SupplyAuditView
    return SupplyAuditView(), None


def _sso_patrol():
    from ui.views.sso_patrol import SSOPatrolApplyView
    return SSOPatrolApplyView(), None


def _materials():
    from ui.views.materials import MaterialsReportView
    return MaterialsReportView(), None


def _logistics():
    from ui.views.logistics import LogisticsApplyView
    return LogisticsApplyView(), None


def _timeoff():
    from ui.views.timeoff import TimeoffApplyView
    return TimeoffApplyView(), None


def _reinstatement():
    from ui.views import ReinstatementApplyView
    return ReinstatementApplyView(), None


def _ic_leave():
    from ui.views.leave import ICLeaveApplyView
    return ICLeaveApplyView(), None


def _ooc_leave():
    from ui.views.leave import OOCLeaveApplyView
    return OOCLeaveApplyView(), None


def _promotion():
    from ui.views.promotion import PromotionApplyView
    return PromotionApplyView(), None


def _transfer(division) -> Build:
    def build():
        from ui.views.transfers import TransferApplyView
        return TransferApplyView(division), None
    return build


_STATIC: dict[str, tuple[str, Build]] = {
    "dismissal": ("Увольнения", _dismissal),
    "role_getting": ("Роли", _role_getting),
    "storage_requests": ("Склад · заявки", _storage_requests),
    "storage_audit": ("Склад · аудит", _storage_audit),
    "sso_patrol": ("Патруль ССО", _sso_patrol),
    "materials": ("Материалы", _materials),
    "logistics": ("Логистика / Поставки", _logistics),
    "timeoff": ("Отгулы", _timeoff),
    "reinstatement": ("Восстановление", _reinstatement),
    "ic_leave": ("IC отпуск", _ic_leave),
    "ooc_leave": ("OOC отпуск", _ooc_leave),
}


def get_targets() -> dict[int, Target]:
    targets: dict[int, Target] = {}
    for key, (label, build) in _STATIC.items():
        channel_id = config.CHANNELS.get(key)
        if channel_id:
            targets.setdefault(channel_id, Target(label, channel_id, build))
    for div in divisions.divisions:
        if div.transfer_channel:
            targets.setdefault(
                div.transfer_channel,
                Target(f"Переводы · {div.abbreviation}", div.transfer_channel, _transfer(div)),
            )
        if div.promotion_channel:
            targets.setdefault(
                div.promotion_channel,
                Target(f"Повышения · {div.abbreviation}", div.promotion_channel, _promotion),
            )
    return targets


async def request_refresh(bot, channel_id: int) -> None:
    """Обновление с дебаунсом для сервисов после отправки заявки."""
    target = get_targets().get(channel_id)
    if target:
        view, embed = target.build()
        await update_bottom_message(bot, channel_id, view, embed)


async def refresh_now(bot, target: Target) -> None:
    """Мгновенное обновление для админ-панели."""
    view, embed = target.build()
    if not await update_bottom_message_now(bot, target.channel_id, view, embed):
        raise RuntimeError(f"канал {target.channel_id} не найден")


async def refresh_all(bot) -> list[tuple[Target, Exception | None]]:
    results: list[tuple[Target, Exception | None]] = []
    for target in get_targets().values():
        try:
            await refresh_now(bot, target)
            results.append((target, None))
        except Exception as error:
            logger.error(f"Refresh failed for {target.label}: {error}", exc_info=True)
            results.append((target, error))
    return results