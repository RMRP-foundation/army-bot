import discord

from database.models import PromotionRequest, User
from database.status import get_status_display
from utils.user_data import format_rank, format_static


def promotion_embed(request: PromotionRequest, requester: User) -> discord.Embed:
    status_display = get_status_display(request.status)

    e = discord.Embed(
        title=f"{status_display.emoji} Рапорт #{request.id} — {status_display.text}",
        color=status_display.color,
        timestamp=request.created_at,
    )
    e.add_field(name="Имя Фамилия", value=requester.full_name, inline=True)
    e.add_field(name="Статик", value=format_static(requester.static), inline=True)
    e.add_field(name="Звание", value=f"{format_rank(request.current_rank)}  ⟶ {format_rank(request.target_rank)}",
                inline=False)

    if request.evidence:
        for title, content in request.evidence.items():
            if content and content.strip():
                e.add_field(name=title, value=content, inline=False)

    if request.score:
        e.add_field(name="Баллы", value=request.score, inline=False)
    if request.reject_reason:
        e.add_field(name="Причина отказа", value=request.reject_reason, inline=False)
    if request.reviewer_id:
        e.add_field(name="Проверил", value=f"<@{request.reviewer_id}>", inline=True)
    if request.promoted_by:
        e.add_field(name="Повысил", value=f"<@{request.promoted_by}>", inline=True)
    return e