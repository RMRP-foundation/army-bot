import asyncio
import logging

import discord
from discord.ext import commands

from bot import Bot
from core import config
from database.models import LeaveRequest, LeaveType, User
from services.member import MemberService
from services.notifications import notify_leave_expired
from ui.embeds.leave import leave_embed
from ui.views.leave import ICLeaveApplyView, OOCLeaveApplyView
from utils.bottom_message import update_bottom_message as _update_bottom_message
from utils.helpers import safe_edit_message, build_mentions
from utils.mongo_atomic import atomic_status_transition
from utils.permissions import is_service

logger = logging.getLogger(__name__)

_leave_timers: dict[int, asyncio.Task] = {}
_leave_activation_timers: dict[int, asyncio.Task] = {}
_timers_restored = False

ic_channel_id = config.CHANNELS["ic_leave"]
ooc_channel_id = config.CHANNELS["ooc_leave"]


async def _activate_leave(bot: Bot, request_id: int):
    """Выдает роль и ник, когда наступает дата начала отпуска."""
    request = await LeaveRequest.find_one(LeaveRequest.id == request_id)
    if not request or request.status != "APPROVED":
        return

    member = await bot.getch_member(request.user_id)
    user_db = await User.get_by_discord_id(request.user_id)

    if member and user_db and user_db.rank is not None:
        if user_db.leave_status == request.leave_type.value:
            return

        user_db.leave_status = request.leave_type.value
        await user_db.save()

        if not request.original_nick:
            request.original_nick = member.display_name
            await request.save()

        await MemberService.sync_member_discord(
            member=member,
            user_db=user_db,
            reason=f"{request.leave_type.value} отпуск активирован",
        )


async def schedule_leave_activation(bot: Bot, request: LeaveRequest):
    """Планирует выдачу роли в будущем."""
    now = discord.utils.utcnow()
    delay = (request.starts_at - now).total_seconds()

    if delay <= 0:
        await _activate_leave(bot, request.id)
        return

    prev = _leave_activation_timers.pop(request.id, None)
    if prev and not prev.done():
        prev.cancel()

    async def _run():
        try:
            await asyncio.sleep(delay)
            await _activate_leave(bot, request.id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in leave activation timer #{request.id}: {e}")
        finally:
            _leave_activation_timers.pop(request.id, None)

    task = asyncio.create_task(_run())
    _leave_activation_timers[request.id] = task


async def _expire_leave(bot: Bot, request_id: int):
    """Завершает отпуск по истечении срока."""
    updated_dict = await atomic_status_transition(
        LeaveRequest.get_pymongo_collection(), request_id, "APPROVED", "EXPIRED",
    )
    if not updated_dict:
        return

    request = LeaveRequest(**updated_dict)

    member = await bot.getch_member(request.user_id)
    user_db = await User.get_by_discord_id(request.user_id)
    if user_db:
        user_db.leave_status = None
        await user_db.save()

    if member and user_db:
        await MemberService.sync_member_discord(
            member=member,
            user_db=user_db,
            reason=f"{request.leave_type.value} отпуск завершён",
            original_nick=request.original_nick,
        )

    channel_key = "ic_leave" if request.leave_type == LeaveType.IC else "ooc_leave"
    channel = bot.get_channel(config.CHANNELS[channel_key])

    if channel and request.message_id:
        await safe_edit_message(
            channel.get_partial_message(request.message_id),
            embed=leave_embed(request, user_db),
            content=build_mentions(request.user_id),
            view=None,
        )

    await notify_leave_expired(bot, request.user_id, request)
    _leave_timers.pop(request_id, None)


async def schedule_leave_expiry(bot: Bot, request: LeaveRequest):
    """Планирует задачу завершения отпуска через оставшееся время."""
    now = discord.utils.utcnow()
    delay = (request.ends_at - now).total_seconds()

    if delay <= 0:
        await _expire_leave(bot, request.id)
        return

    cancel_leave_timer(request.id)

    async def _run():
        try:
            await asyncio.sleep(delay)
            await _expire_leave(bot, request.id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in leave expiry timer #{request.id}: {e}")

    task = asyncio.create_task(_run())
    _leave_timers[request.id] = task

    def _cleanup(completed: asyncio.Task) -> None:
        if _leave_timers.get(request.id) is completed:
            _leave_timers.pop(request.id, None)

    task.add_done_callback(_cleanup)


def cancel_leave_timer(request_id: int):
    """Отменяет таймер завершения отпуска."""
    task = _leave_timers.pop(request_id, None)
    if task and not task.done():
        task.cancel()


def cancel_activation_timer(request_id: int):
    """Отменяет таймер активации отпуска."""
    task = _leave_activation_timers.pop(request_id, None)
    if task and not task.done():
        task.cancel()


async def restore_leave_timers(bot: Bot):
    """Восстанавливает таймеры всех активных отпусков при запуске бота."""
    global _timers_restored
    if _timers_restored:
        return

    active = await LeaveRequest.find(LeaveRequest.status == "APPROVED").to_list()
    now = discord.utils.utcnow()

    for req in active:
        starts_at = getattr(req, "starts_at", None)
        ends_at = getattr(req, "ends_at", None)
        if starts_at is None or ends_at is None:
            logger.warning(
                f"Skipping leave timer restoration #{req.id}: missing starts_at/ends_at (legacy record)."
            )
            continue

        if now >= ends_at:
            await _expire_leave(bot, req.id)
        elif now >= starts_at:
            await _activate_leave(bot, req.id)
            await schedule_leave_expiry(bot, req)
        else:
            await schedule_leave_activation(bot, req)
            await schedule_leave_expiry(bot, req)

    _timers_restored = True


async def update_bottom_message(bot: Bot, leave_type: LeaveType):
    """Обновляет сообщение для подачи заявки в зависимости от типа отпуска."""
    if leave_type == LeaveType.IC:
        await _update_bottom_message(bot, ic_channel_id, ICLeaveApplyView())
    else:
        await _update_bottom_message(bot, ooc_channel_id, OOCLeaveApplyView())


class Leave(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.command(name="refresh_leave")
    @is_service()
    async def update_command(self, ctx: commands.Context):
        if ctx.channel.id == ic_channel_id:
            await update_bottom_message(self.bot, LeaveType.IC)
        elif ctx.channel.id == ooc_channel_id:
            await update_bottom_message(self.bot, LeaveType.OOC)


async def setup(bot: Bot):
    await bot.add_cog(Leave(bot))