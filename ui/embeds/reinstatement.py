import discord

from database.models import ReinstatementRequest, User
from database.status import get_status_display
from utils.user_data import format_static, format_rank


def reinstatement_embed(request: ReinstatementRequest, requester: User) -> discord.Embed:
    status_display = get_status_display(request.status)

    e = discord.Embed(
        title=f"{status_display.emoji} Заявление #{request.id} — {status_display.text}",
        colour=status_display.color,
        timestamp=request.sent_at
    )
    e.add_field(name="Заявитель", value=f"{request.data.full_name}")
    e.add_field(name="Статик", value=format_static(requester.static))
    e.add_field(name="Все документы", value=request.data.all_documents, inline=False)
    e.add_field(name="Военный билет", value=request.data.army_pass, inline=False)
    if request.reject_reason:
        e.add_field(
            name="Причина отклонения", value=request.reject_reason, inline=False
        )
    e.set_footer(text="Отправлено")

    if request.rank is not None:
        e.add_field(
            name="Полученное звание", value=format_rank(request.rank), inline=False
        )

    return e