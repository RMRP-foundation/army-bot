import datetime
import logging

import discord
from core.config import DivisionId, BLACKLIST_MENTIONS, PENALTY_THRESHOLD, CHANNELS
from database.models import User, Blacklist, LeaveRequest
from ui.embeds.leave import leave_embed
from utils.helpers import safe_edit_message, build_mentions
from utils.user_data import format_static

logger = logging.getLogger(__name__)


async def check_and_apply_penalty(
        interaction: discord.Interaction,
        target_user_db: User,
        initiator_db: User,
        audit_msg_url: str
) -> bool:

    # КМБ не выдаем ЧС за неустойку
    if target_user_db.division == DivisionId.KMB:
        return False

    days_in_organization = (
        (discord.utils.utcnow() - target_user_db.invited_at).days
        if target_user_db.invited_at
        else None
    )

    if days_in_organization is not None and days_in_organization < PENALTY_THRESHOLD:
        blacklist = Blacklist(
            initiator=initiator_db.discord_id,
            reason="Неустойка",
            evidence=audit_msg_url,
            ends_at=discord.utils.utcnow() + datetime.timedelta(days=14),
        )
        target_user_db.blacklist = blacklist

        blacklist_channel = interaction.client.get_channel(CHANNELS["blacklist"])
        if blacklist_channel:
            bl_embed = discord.Embed(
                title="📋 Автоматический ЧС",
                color=discord.Color.dark_red(),
                timestamp=discord.utils.utcnow(),
            )
            author_name = f"Составитель: {initiator_db.full_name} | {format_static(initiator_db.static)}"
            bl_embed.set_author(name=author_name)

            citizen_value = f"<@{target_user_db.discord_id}> {target_user_db.full_name} | {format_static(target_user_db.static)}"
            bl_embed.add_field(name="Гражданин", value=citizen_value, inline=False)
            bl_embed.add_field(name="Причина", value="Неустойка", inline=False)
            bl_embed.add_field(name="Доказательства", value=f"[Перейти к логу]({audit_msg_url})", inline=False)

            ends_at_fmt = discord.utils.format_dt(blacklist.ends_at, style="d")
            bl_embed.add_field(name="Срок", value=f"14 дней (до {ends_at_fmt})", inline=False)

            await blacklist_channel.send(
                content=build_mentions(
                [target_user_db.discord_id, initiator_db.discord_id],
                BLACKLIST_MENTIONS
                ),
                embed=bl_embed,
            )

        return True

    return False


async def cleanup_user_leaves(bot, user_id: int):
    """Аннулирует или отклоняет отпуска пользователя при увольнении."""

    pending_reqs = await LeaveRequest.find(
        LeaveRequest.user_id == user_id,
        LeaveRequest.status == "PENDING"
    ).to_list()

    for req in pending_reqs:
        req.status = "REJECTED"
        await req.save()
        await _update_leave_message(bot, req)

    active_reqs = await LeaveRequest.find(
        LeaveRequest.user_id == user_id,
        LeaveRequest.status == "APPROVED"
    ).to_list()

    for req in active_reqs:
        user_db = await User.find_one(User.discord_id == user_id)
        user_db.leave_status = None
        await user_db.save()

        from cogs.leave import cancel_leave_timer, cancel_activation_timer
        cancel_leave_timer(req.id)
        cancel_activation_timer(req.id)

        req.status = "ANNULLED"
        req.annuller_id = bot.user.id
        req.annulled_at = discord.utils.utcnow()
        await req.save()
        await _update_leave_message(bot, req)


async def _update_leave_message(bot, req: LeaveRequest):
    """Функция для обновления сообщения в канале отпусков."""
    try:
        channel_id = CHANNELS["ic_leave"] if req.leave_type.value == "IC" else CHANNELS["ooc_leave"]
        channel = bot.get_channel(channel_id)
        if channel and req.message_id:
            user_db = await User.get_by_discord_id(req.user_id)
            await safe_edit_message(
                channel.get_partial_message(req.message_id),
                embed=leave_embed(req, user_db),
                view=None,
            )
    except (discord.NotFound, discord.Forbidden):
        logger.warning("Cannot update leave message for request #%s", req.id)
    except Exception:
        logger.exception("Failed to update leave message for request #%s", req.id)
