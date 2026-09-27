import discord

from database.models import SupplyRequest


def give_supply_embed(issuer: discord.Member, recipient_id: int, items: list[str], reason: str, created_at) -> discord.Embed:
    embed = discord.Embed(
        title="📦 Выдача снабжения",
        color=discord.Color.dark_green(),
        timestamp=created_at,
    )
    embed.add_field(name="Выдал", value=issuer.mention, inline=True)
    embed.add_field(name="Получил", value=f"<@{recipient_id}>", inline=True)
    embed.add_field(name="Предметы", value="\n".join(f"- {item}" for item in items), inline=False)
    embed.add_field(name="Причина", value=reason, inline=False)

    return embed


def clear_supply_embed(responsible: discord.Member, jobs: list[str], created_at) -> discord.Embed:
    embed = discord.Embed(
        title="🧹 Чистка склада",
        color=discord.Color.gold(),
        timestamp=created_at,
    )
    embed.add_field(name="Ответственный", value=responsible.mention, inline=True)
    embed.add_field(name="Предметы", value="\n".join(f"- {item}" for item in jobs), inline=False)

    return embed


def supply_issuance_embed(issuer: discord.abc.User, recipient_id: int, items: dict[str, int], request: SupplyRequest, jump_url: str) -> discord.Embed:
    """Эмбед аудит-записи о выдаче склада по одобренной заявке."""
    embed = discord.Embed(title="📦 Выдача склада", color=discord.Color.dark_green(), timestamp=request.reviewed_at)
    embed.add_field(name="Выдал", value=issuer.mention, inline=True)
    embed.add_field(name="Получил", value=f"<@{recipient_id}>", inline=True)
    embed.add_field(name="Предметы", value="\n".join(f"• {k}: {v} шт." for k, v in items.items()), inline=False)
    embed.add_field(name="Причина", value=f"[Заявка #{request.id}]({jump_url})", inline=False)
    return embed