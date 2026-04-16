import asyncio
import datetime
import logging

import discord
from discord.ext import commands

import config
from bot import Bot
from database.models import LeaveRequest, LeaveType, User
from ui.views.leave import (
    ICLeaveApplyView,
    OOCLeaveApplyView,
    _remove_leave_nick_and_role,
)
from utils.bottom_message import update_bottom_message as _update_bottom_message
from utils.notifications import notify_leave_expired
from utils.permissions import has_update_permission

logger = logging.getLogger(__name__)

_leave_timers: dict[int, asyncio.Task] = {}
_timers_restored = False

ic_channel_id = config.CHANNELS["ic_leave"]
ooc_channel_id = config.CHANNELS["ooc_leave"]


async def _activate_leave(bot: Bot, request_id: int):
    """Выдает роль и ник, когда наступает дата начала отпуска."""
    request = await LeaveRequest.find_one(LeaveRequest.id == request_id)
    if not request or request.status != "APPROVED":
        return

    member = await bot.getch_member(request.user_id)
    user_db = await User.find_one(User.discord_id == request.user_id)

    if member and user_db:
        user_db.leave_status = request.leave_type.value
        await user_db.save()

        if not request.original_nick:
            request.original_nick = member.display_name
            await request.save()

        from ui.views.leave import apply_leave_nick_and_role
        await apply_leave_nick_and_role(bot, member, user_db, request.leave_type)


async def schedule_leave_activation(bot: Bot, request: LeaveRequest):
    """Планирует выдачу роли в будущем."""
    now = discord.utils.utcnow()
    # starts_at в БД уже в UTC
    start_time = request.starts_at.replace(tzinfo=datetime.timezone.utc)
    delay = (start_time - now).total_seconds()

    if delay <= 0:
        await _activate_leave(bot, request.id)
        return

    async def _run():
        try:
            await asyncio.sleep(delay)
            await _activate_leave(bot, request.id)
        except asyncio.CancelledError:
            pass

    asyncio.create_task(_run())


async def _expire_leave(bot: Bot, request_id: int):
    """Завершает отпуск по истечении срока."""
    request = await LeaveRequest.find_one(LeaveRequest.id == request_id)
    if not request or request.status != "APPROVED":
        return

    request.status = "EXPIRED"
    await request.save()

    member = await bot.getch_member(request.user_id)
    if member:
        user_db = await User.find_one(User.discord_id == request.user_id)
        if user_db:
            user_db.leave_status = None
            await user_db.save()

            await _remove_leave_nick_and_role(
                bot, member, user_db, request.leave_type,
                original_nick=request.original_nick,
            )

    channel_key = "ic_leave" if request.leave_type == LeaveType.IC else "ooc_leave"
    channel = bot.get_channel(config.CHANNELS[channel_key])

    if channel and request.message_id:
        try:
            msg = await channel.fetch_message(request.message_id)
            embed = await request.to_embed()
            await msg.edit(
                content=f"-# ||<@{request.user_id}>||",
                embed=embed,
                view=None,
            )
        except (discord.NotFound, discord.HTTPException) as e:
            logger.warning(f"Не удалось обновить сообщение отпуска #{request_id}: {e}")

    await notify_leave_expired(bot, request.user_id, request)
    _leave_timers.pop(request_id, None)


async def schedule_leave_expiry(bot: Bot, request: LeaveRequest):
    """Планирует задачу завершения отпуска через оставшееся время."""
    if request.ends_at is None:
        return

    now = discord.utils.utcnow()
    delay = (request.ends_at.replace(tzinfo=datetime.timezone.utc) - now).total_seconds()

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
            logger.error(f"Ошибка в таймере отпуска #{request.id}: {e}")

    task = asyncio.create_task(_run())
    _leave_timers[request.id] = task


def cancel_leave_timer(request_id: int):
    """Отменяет таймер завершения отпуска."""
    task = _leave_timers.pop(request_id, None)
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
        start_t = req.starts_at.replace(tzinfo=datetime.timezone.utc)
        end_t = req.ends_at.replace(tzinfo=datetime.timezone.utc)

        if now >= end_t:
            await _expire_leave(bot, req.id)
        elif now >= start_t:
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
    @has_update_permission()
    async def refresh_leave(self, ctx: commands.Context):
        if ctx.channel.id == ic_channel_id:
            await update_bottom_message(self.bot, LeaveType.IC)
        elif ctx.channel.id == ooc_channel_id:
            await update_bottom_message(self.bot, LeaveType.OOC)


async def setup(bot: Bot):
    await bot.add_cog(Leave(bot))