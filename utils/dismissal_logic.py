import datetime
import discord
import config
from database.models import User, Blacklist, LeaveRequest
from utils.user_data import format_game_id


async def check_and_apply_penalty(
        interaction: discord.Interaction,
        target_user_db: User,
        initiator_db: User,
        audit_msg_url: str
) -> bool:

    days_in_organization = (
        (datetime.datetime.now() - target_user_db.invited_at).days
        if target_user_db.invited_at
        else None
    )

    if days_in_organization is not None and days_in_organization < config.PENALTY_THRESHOLD:
        blacklist = Blacklist(
            initiator=initiator_db.discord_id,
            reason="Неустойка",
            evidence=audit_msg_url,
            ends_at=datetime.datetime.now() + datetime.timedelta(days=14),
        )
        target_user_db.blacklist = blacklist

        blacklist_channel = interaction.client.get_channel(config.CHANNELS["blacklist"])
        if blacklist_channel:
            bl_embed = discord.Embed(
                title="📋 Автоматический ЧС",
                color=discord.Color.dark_red(),
                timestamp=datetime.datetime.now(),
            )
            author_name = f"Составитель: {initiator_db.full_name} | {format_game_id(initiator_db.static)}"
            bl_embed.set_author(name=author_name)

            citizen_value = f"<@{target_user_db.discord_id}> {target_user_db.full_name} | {format_game_id(target_user_db.static)}"
            bl_embed.add_field(name="Гражданин", value=citizen_value, inline=False)
            bl_embed.add_field(name="Причина", value="Неустойка", inline=False)
            bl_embed.add_field(name="Доказательства", value=f"[Перейти к логу]({audit_msg_url})", inline=False)

            ends_at_fmt = discord.utils.format_dt(blacklist.ends_at, style="d")
            bl_embed.add_field(name="Срок", value=f"14 дней (до {ends_at_fmt})", inline=False)

            mentions_list = [f"<@{target_user_db.discord_id}>", f"<@{initiator_db.discord_id}>"]
            mentions_list.extend([f"<@&{m}>" for m in config.BLACKLIST_MENTIONS])

            await blacklist_channel.send(
                content=f"-# ||{' '.join(mentions_list)}||",
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

        from cogs.leave import cancel_leave_timer
        cancel_leave_timer(req.id)

        req.status = "ANNULLED"
        req.annuller_id = bot.user.id
        req.annulled_at = discord.utils.utcnow()
        await req.save()
        await _update_leave_message(bot, req)


async def _update_leave_message(bot, req: LeaveRequest):
    """Функция для обновления сообщения в канале отпусков."""
    try:
        channel_id = config.CHANNELS["ic_leave"] if req.leave_type.value == "IC" else config.CHANNELS["ooc_leave"]
        channel = bot.get_channel(channel_id)
        if channel and req.message_id:
            msg = await channel.fetch_message(req.message_id)
            await msg.edit(view=None, embed=await req.to_embed())
    except Exception:
        pass