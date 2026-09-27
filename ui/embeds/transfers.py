import discord

from database import divisions
from database.models import TransferRequest, User
from database.status import get_status_display
from utils.user_data import format_static, format_rank


def transfer_embed(request: TransferRequest, user: User) -> discord.Embed:
    """Чистая синхронная функция сборки эмбеда заявления на перевод."""
    status_display = get_status_display(request.status)

    old_div = divisions.get_division(request.old_division_id) if request.old_division_id else None
    new_div = divisions.get_division(request.new_division_id)

    old_abbr = old_div.abbreviation if old_div else "Нет"
    new_abbr = new_div.abbreviation if new_div else "Нет"

    if request.status == "OLD_DIVISION_REVIEW":
        context = f" в {old_abbr}"
    elif request.status == "NEW_DIVISION_REVIEW":
        context = f" в {new_abbr}"
    else:
        context = ""

    embed = discord.Embed(
        title = f"{status_display.emoji} Рапорт{context} #{request.id} — {status_display.text}",
        color=status_display.color,
        timestamp=request.created_at,
    )

    embed.add_field(name="Имя Фамилия", value=request.full_name, inline=True)
    embed.add_field(name="Номер паспорта", value=format_static(request.static), inline=True)
    embed.add_field(name="Звание", value=format_rank(user.rank), inline=False)
    embed.add_field(name="Возраст и имя в реальной жизни", value=request.name_age, inline=False)
    embed.add_field(name="Часовой пояс", value=request.timezone, inline=True)
    embed.add_field(name="Онлайн и прайм тайм", value=request.online_prime, inline=True)
    embed.add_field(name="Мотивация", value=request.motivation, inline=False)

    if old_div and old_div.positions:
        embed.add_field(name="Старое подразделение", value=old_div.name, inline=True)

    if request.old_reviewer_id:
        reviewed = f" в {discord.utils.format_dt(request.old_reviewed_at)}" if request.old_reviewed_at else ""
        embed.add_field(name=f"Рассматривающий (с {old_abbr})", value=f"<@{request.old_reviewer_id}>{reviewed}", inline=False)

    if request.new_reviewer_id:
        reviewed = f" в {discord.utils.format_dt(request.new_reviewed_at)}" if request.new_reviewed_at else ""
        embed.add_field(name=f"Рассматривающий (в {new_abbr})", value=f"<@{request.new_reviewer_id}>{reviewed}", inline=False)

    if request.reject_reason:
        embed.add_field(name="Причина отклонения", value=request.reject_reason, inline=False)

    return embed