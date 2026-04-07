import datetime
from enum import Enum
from typing import Dict

import discord
from beanie import Document, Indexed
from pydantic import BaseModel, Field

import config
from utils.user_data import format_game_id, display_rank, transliterate_abbreviation


class Privilege(Enum):
    COMMANDER = 4
    DEPUTY_COMMANDER = 3
    OFFICER = 2
    DEFAULT = 1


class Position(BaseModel):
    name: str
    role_id: int
    privilege: Privilege = Privilege.DEFAULT


class Division(Document):
    division_id: int = Field(alias="id")
    name: str
    abbreviation: str
    role_id: int
    transfer_channel: int | None = None
    description: str | None = None
    emoji: str | None = None
    positions: list[Position] | None = None

    def get_position_by_name(self, name: str) -> Position | None:
        if not self.positions:
            return None
        for pos in self.positions:
            if pos.name.lower() == name.lower():
                return pos
        return None

    class Settings:
        name = "divisions"


class Blacklist(BaseModel):
    initiator: int
    reason: str
    evidence: str
    ends_at: datetime.datetime | None = None

    def __bool__(self):
        if self.ends_at is None:
            return True
        return datetime.datetime.now() < self.ends_at


class User(Document):
    discord_id: Indexed(int, unique=True)
    static: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    rank: int | None = None
    position: str | None = None
    division: int | None = None
    leave_status: str | None = None
    invited_at: datetime.datetime | None = None
    blacklist: Blacklist | None = None
    last_supply_at: datetime.datetime | None = None
    pre_inited: bool = False

    @property
    def full_name(self) -> str | None:
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.last_name

    @property
    def short_name(self) -> str | None:
        if self.first_name and self.last_name:
            return f"{self.first_name[0]}. {self.last_name}"
        return None

    @property
    def discord_nick(self) -> str:
        from database import divisions

        parts = []
        if self.leave_status:
            parts.append(self.leave_status)

        if self.division is not None:
            div = divisions.get_division(self.division)
            if div:
                if div.abbreviation in ["ВА", "КМБ"]:
                    parts.append(div.abbreviation)
                else:
                    parts.append(transliterate_abbreviation(div.abbreviation))
        if self.rank is not None:
            parts.append(config.RANKS_SHORT[self.rank])
        if self.full_name:
            if len(" | ".join(parts + [self.full_name])) > 32:
                parts.append(self.short_name or self.full_name)
            else:
                parts.append(self.full_name)
        return " | ".join(parts)[:32]

    class Settings:
        name = "users"


class ReinstatementData(BaseModel):
    full_name: str
    all_documents: str
    army_pass: str


class ReinstatementRequest(Document):
    id: int
    user: int
    data: ReinstatementData
    approved: bool = False
    checked: bool = False
    rank: int | None = None
    sent_at: datetime.datetime = Field(default_factory=datetime.datetime.now)

    async def to_embed(self):
        user = await User.find_one(User.discord_id == self.user)

        status = (
            "одобрено"
            if self.approved
            else "отклонено"
            if self.checked
            else "на рассмотрении"
        )
        emoji = "✅" if self.approved else "❌" if self.checked else "⏳"
        colour = (
            discord.Colour.dark_green()
            if self.approved
            else discord.Colour.dark_red()
            if self.checked
            else discord.Colour.gold()
        )

        e = discord.Embed(
            title=f"{emoji} Заявление {status}", colour=colour, timestamp=self.sent_at
        )
        e.add_field(name="Заявитель", value=f"{self.data.full_name}")
        e.add_field(name="Статик", value=format_game_id(user.static))
        e.add_field(name="Все документы", value=self.data.all_documents, inline=False)
        e.add_field(name="Военный билет", value=self.data.army_pass, inline=False)
        e.set_footer(text="Отправлено")

        if self.rank is not None:
            e.add_field(
                name="Полученное звание", value=display_rank(self.rank), inline=False
            )

        return e

    class Settings:
        name = "reinstatement_requests"


class RoleType(str, Enum):
    ARMY = "army"  # ВС РФ
    KMB = "kmb" # КМБ
    SUPPLY_ACCESS = "supply_access"  # Доступ к поставке
    GOV_EMPLOYEE = "gov_employee"  # Гос. сотрудник


class RoleData(BaseModel):
    full_name: str
    static_id: int


class ExtendedRoleData(BaseModel):
    full_name: str
    static_id: int
    faction: str
    rank_position: str
    purpose: str | None = None  # Цель и удостоверение для гос. сотрудника
    certificate_link: str | None = None  # Только для доступа к поставке


class RoleRequest(Document):
    id: int
    user: int
    role_type: RoleType = RoleType.ARMY
    data: RoleData | None = None
    extended_data: ExtendedRoleData | None = None
    approved: bool = False
    checked: bool = False
    sent_at: datetime.datetime = Field(default_factory=datetime.datetime.now)

    def _get_role_type_name(self) -> str:
        names = {
            RoleType.ARMY: "ВС РФ",
            RoleType.KMB: "КМБ",
            RoleType.SUPPLY_ACCESS: "Доступ к поставке",
            RoleType.GOV_EMPLOYEE: "Гос. сотрудник",
        }
        return names.get(self.role_type, "Неизвестно")

    async def to_embed(self):
        status = (
            "одобрено"
            if self.approved
            else "отклонено"
            if self.checked
            else "на рассмотрении"
        )
        emoji = "✅" if self.approved else "❌" if self.checked else "⏳"
        colour = (
            discord.Colour.dark_green()
            if self.approved
            else discord.Colour.dark_red()
            if self.checked
            else discord.Colour.gold()
        )

        role_name = self._get_role_type_name()
        e = discord.Embed(
            title=f"{emoji} Заявление на роль «{role_name}» {status}",
            colour=colour,
            timestamp=self.sent_at,
        )

        if self.role_type in [RoleType.ARMY, RoleType.KMB] and self.data:
            e.add_field(name="Заявитель", value=self.data.full_name)
            e.add_field(name="Статик", value=format_game_id(self.data.static_id))
        elif self.extended_data:
            e.add_field(name="Имя Фамилия", value=self.extended_data.full_name)
            e.add_field(
                name="Статик", value=format_game_id(self.extended_data.static_id)
            )
            e.add_field(name="Фракция", value=self.extended_data.faction, inline=False)
            e.add_field(
                name="Звание, должность",
                value=self.extended_data.rank_position,
                inline=False,
            )
            if self.extended_data.purpose:
                e.add_field(
                    name="Цель и удостоверение",
                    value=self.extended_data.purpose,
                    inline=False,
                )
            if self.extended_data.certificate_link:
                e.add_field(
                    name="Удостоверение",
                    value=self.extended_data.certificate_link,
                    inline=False,
                )

        e.set_footer(text="Отправлено")
        return e

    class Settings:
        name = "role_requests"

class TimeoffRequest(Document):
    id: int
    user_id: int
    data: RoleData
    approved: bool = False
    checked: bool = False
    period: str | None = None
    sent_at: datetime.datetime = Field(default_factory=datetime.datetime.now)
    reviewed_at: datetime.datetime | None = None

    async def to_embed(self):
        emoji = "✅" if self.approved else "❌" if self.checked else "⏳"
        colour = (
            discord.Colour.dark_green()
            if self.approved
            else discord.Colour.dark_red()
            if self.checked
            else discord.Colour.gold()
        )

        e = discord.Embed(
            title=f"{emoji} Заявление на отгул #{self.id}",
            colour=colour,
            timestamp=self.sent_at,
        )

        e.add_field(name="Заявитель", value=self.data.full_name)
        e.add_field(name="Статик", value=format_game_id(self.data.static_id))

        requester = await User.find_one(User.discord_id == self.user_id)
        from database import divisions

        # Safely determine rank and division name in case requester or division is missing
        rank_value = "Неизвестно"
        division_name = "Неизвестно"

        if requester is not None:
            rank_value = display_rank(requester.rank)

            division = divisions.get_division(requester.division)
            if division is not None:
                division_name = division.name

        e.add_field(name="Звание", value=rank_value)
        e.add_field(name="Подразделение", value=division_name, inline=False)
        e.add_field(name="Время", value=self.period)


        e.set_footer(text="Отправлено")
        return e

    class Settings:
        name = "timeoff_requests"


class SupplyRequest(Document):
    id: int
    user_id: int
    items: Dict[str, int] = Field(default_factory=dict)
    status: str = "PENDING"
    reviewer_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now)
    reviewed_at: datetime.datetime | None = None
    message_id: int | None = None  # ID сообщения в канале

    async def to_embed(self, bot):
        requester = await User.find_one(User.discord_id == self.user_id)
        requester_game_id = (
            format_game_id(requester.static) if requester else "Неизвестно"
        )
        requester_name = requester.full_name if requester else f"<@{self.user_id}>"

        status_map = {
            "PENDING": ("⏳ На рассмотрении", discord.Color.gold()),
            "APPROVED": ("✅ Одобрено", discord.Color.green()),
            "REJECTED": ("❌ Отклонено", discord.Color.red()),
            "DRAFT": ("📝 Черновик", discord.Color.light_grey()),
        }
        title, color = status_map.get(
            self.status, ("❓ Неизвестно", discord.Color.default())
        )

        embed = discord.Embed(
            title=f"Заявка на склад #{self.id}", color=color, timestamp=self.created_at
        )
        embed.add_field(
            name="Запросил",
            value=f"{requester_name} ({requester_game_id})",
            inline=False,
        )

        items_str = ""
        if self.items:
            for item, amount in self.items.items():
                items_str += f"• **{item}**: {amount} шт.\n"
        else:
            items_str = "Список пуст"

        embed.add_field(name="Список предметов", value=items_str, inline=False)

        if self.reviewer_id:
            embed.add_field(
                name="Рассмотрел", value=f"<@{self.reviewer_id}>", inline=False
            )

        return embed

    class Settings:
        name = "supply_requests"


class DismissalType(str, Enum):
    PJS = "ПСЖ"
    TRANSFER = "Перевод"
    AUTO = "Потеря спец. связи"


class DismissalRequest(Document):
    id: int
    user_id: int
    type: DismissalType
    full_name: str
    static: int

    rank_index: int | None = None
    division_id: int | None = None
    position: str | None = None

    status: str = "PENDING"  # PENDING, APPROVED, REJECTED
    reviewer_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now)
    reviewed_at: datetime.datetime | None = None

    async def to_embed(self, bot):
        from database import divisions

        status_map = {
            "PENDING": ("⏳", discord.Color.gold()),
            "APPROVED": ("✅", discord.Color.green()),
            "REJECTED": ("❌", discord.Color.red()),
        }
        title_prefix, color = status_map.get(
            self.status, ("❓", discord.Color.default())
        )

        if self.type == DismissalType.AUTO:
            title_text = "Автоматический рапорт на увольнение"
        else:
            title_text = "Рапорт на увольнение"

        embed = discord.Embed(
            title=f"{title_prefix} {title_text} #{self.id}",
            color=color,
            timestamp=self.created_at,
        )

        embed.add_field(name="Имя Фамилия", value=self.full_name, inline=True)
        embed.add_field(
            name="Номер паспорта", value=format_game_id(self.static), inline=True
        )

        embed.add_field(name="Звание", value=display_rank(self.rank_index), inline=False)

        div_name = (
            divisions.get_division_name(self.division_id) if self.division_id else "Нет"
        )
        embed.add_field(name="Подразделение", value=div_name, inline=True)

        if self.position:
            embed.add_field(name="Должность", value=self.position, inline=True)
        embed.add_field(name="Причина", value=self.type.value, inline=False)

        if self.reviewer_id:
            embed.add_field(
                name="Рассмотрел",
                value=f"<@{self.reviewer_id}>"
                + (
                    f" в {discord.utils.format_dt(self.reviewed_at)}"
                    if self.reviewed_at
                    else ""
                ),
                inline=False,
            )

        return embed

    class Settings:
        name = "dismissal_requests"


class TransferRequest(Document):
    id: int
    user_id: int
    full_name: str
    static: int
    name_age: str
    timezone: str
    online_prime: str
    motivation: str
    new_division_id: int
    old_division_id: int = 0

    status: str  # OLD_DIVISION_REVIEW, NEW_DIVISION_REVIEW, APPROVED, REJECTED
    old_reviewer_id: int | None = None
    new_reviewer_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now)
    old_reviewed_at: datetime.datetime | None = None
    new_reviewed_at: datetime.datetime | None = None
    reject_reason: str | None = None

    async def to_embed(self, bot):
        from database import divisions

        user = await User.find_one(User.discord_id == self.user_id)

        old_div = (
            divisions.get_division(self.old_division_id)
            if self.old_division_id
            else None
        )
        new_div = divisions.get_division(self.new_division_id)

        old_abbr = old_div.abbreviation if old_div else "Нет"
        new_abbr = new_div.abbreviation if new_div else "Нет"

        status_map = {
            "OLD_DIVISION_REVIEW": (
                f"🔵 Рассматривается в {old_abbr}",
                discord.Color.blue(),
            ),
            "NEW_DIVISION_REVIEW": (
                f"🟠 Рассматривается в {new_abbr}",
                discord.Color.orange(),
            ),
            "APPROVED": ("✅ Одобрена", discord.Color.dark_green()),
            "REJECTED": ("❌ Отклонена", discord.Color.dark_red()),
        }
        title, color = status_map.get(
            self.status, ("❓ Неизвестно", discord.Color.default())
        )

        embed = discord.Embed(
            title=f"{title[0]} Заявка #{self.id} - {title[1:].strip()}",
            color=color,
            timestamp=self.created_at,
        )

        embed.add_field(name="Имя Фамилия", value=self.full_name, inline=True)
        embed.add_field(
            name="Номер паспорта", value=format_game_id(self.static), inline=True
        )
        embed.add_field(
            name="Звание",
            value=display_rank(user.rank),
            inline=False,
        )
        embed.add_field(
            name="Возраст и имя в реальной жизни", value=self.name_age, inline=False
        )
        embed.add_field(name="Часовой пояс", value=self.timezone, inline=True)
        embed.add_field(
            name="Онлайн и прайм тайм", value=self.online_prime, inline=True
        )
        embed.add_field(name="Мотивация", value=self.motivation, inline=False)

        if old_div and old_div.positions:
            embed.add_field(
                name="Старое подразделение", value=old_div.name, inline=True
            )

        if self.old_reviewer_id:
            embed.add_field(
                name=f"Рассматривающий (с {old_abbr})",
                value=f"<@{self.old_reviewer_id}>"
                + (
                    f" в {discord.utils.format_dt(self.old_reviewed_at)}"
                    if self.old_reviewed_at
                    else ""
                ),
                inline=False,
            )
        if self.new_reviewer_id:
            embed.add_field(
                name=f"Рассматривающий (в {new_abbr})",
                value=f"<@{self.new_reviewer_id}>"
                + (
                    f" в {discord.utils.format_dt(self.new_reviewed_at)}"
                    if self.new_reviewed_at
                    else ""
                ),
                inline=False,
            )
        if self.reject_reason:
            embed.add_field(
                name="Причина отклонения", value=self.reject_reason, inline=False
            )
        return embed

    class Settings:
        name = "transfer_requests"

class SSOPatrolRequest(Document):
    id: int
    user_id: int
    full_name: str
    reason: str
    date: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    status: str = "PENDING"
    reviewer_id: int | None = None

    async def to_embed(self, bot, failed_question=None):
        user = await User.find_one(User.discord_id == self.user_id)
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime('%d.%m.%Y')

        if failed_question:
            title = "❌ Провал проверки знаний"
            color = discord.Color.red()
        else:
            title = f"Запрос формы Сил Специальных Операций #{self.id}"
            status_colors = {
                "PENDING": discord.Color.gold(),
                "APPROVED": discord.Color.green(),
                "REJECTED": discord.Color.red(),
            }
            color = status_colors.get(self.status, discord.Color.blue())

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="Имя Фамилия", value=self.full_name, inline=True)
        embed.add_field(name="Статик", value=format_game_id(user.static), inline=True)
        embed.add_field(name="Звание", value=display_rank(user.rank), inline=False)

        if failed_question:
            embed.add_field(name="Вопрос", value=failed_question, inline=False)
        else:
            embed.add_field(name="Причина", value=self.reason, inline=False)

        embed.set_footer(text=f"Дата: {today}")

        return embed

    class Settings:
        name = "sso_patrol_requests"


class MaterialsReport(Document):
    user_id: int
    full_name: str
    quantity: int
    evidence: str
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    )

    async def to_embed(self, user: User):
        price = f"{self.quantity * config.MATERIAL_PRICE:,}".replace(',', '.')

        embed = discord.Embed(
            title="Отчет о продаже материалов",
            color=discord.Color.gold(),
            timestamp=self.created_at
        )
        embed.add_field(name="Имя Фамилия", value=self.full_name)
        embed.add_field(name="Статик", value=format_game_id(user.static))
        embed.add_field(name="Звание", value=display_rank(user.rank), inline=False)
        embed.add_field(name="Количество", value=f"{self.quantity:,} ед.".replace(",", "."), inline=True)
        embed.add_field(name="Сумма", value=f"{price} ₽", inline=True)
        embed.add_field(name="Доказательства", value=self.evidence, inline=False)
        return embed

    class Settings:
        name = "materials_reports"


class LogisticsType(str, Enum):
    ORBITA = "РЛС \"Орбита\""
    OBJECT7 = "Объект 7"
    WAREHOUSE = "Военные склады"


class LogisticsRequest(Document):
    id: int
    user_id: int
    nickname: str
    faction: str
    supply_type: LogisticsType
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED, EXPIRED
    reviewer_name: str | None = None
    message_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))

    async def to_embed(self):
        status_map = {
            "PENDING": discord.Color.gold(),
            "APPROVED": discord.Color.green(),
            "REJECTED": discord.Color.red(),
            "EXPIRED": discord.Color.dark_grey(),
        }
        color = status_map.get(self.status, discord.Color.default())

        embed = discord.Embed(
            title=f"Поставка: {self.supply_type.value}",
            color=color,
            timestamp=self.created_at
        )
        embed.add_field(name="Заявитель", value=self.nickname, inline=True)
        embed.add_field(name="Организация", value=self.faction, inline=True)

        return embed

    class Settings:
        name = "logistics_requests"


class LeaveType(str, Enum):
    IC = "IC"
    OOC = "OOC"


class LeaveRequest(Document):
    id: int
    user_id: int
    leave_type: LeaveType
    reason: str
    days: int
    original_nick: str | None = None  # Ник до отпуска для ССО

    status: str = "PENDING"
    reviewer_id: int | None = None
    annuller_id: int | None = None
    annulled_at: datetime.datetime | None = None

    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    approved_at: datetime.datetime | None = None
    ends_at: datetime.datetime | None = None
    message_id: int | None = None

    async def to_embed(self) -> discord.Embed:
        from database import divisions

        status_map = {
            "PENDING": ("⏳", discord.Color.gold()),
            "APPROVED": ("✅", discord.Color.green()),
            "REJECTED": ("❌", discord.Color.red()),
            "EXPIRED": ("🕐", discord.Color.dark_grey()),
            "ANNULLED": ("🚫", discord.Color.dark_grey()),
        }
        emoji, color = status_map.get(
            self.status, ("❓", discord.Color.default())
        )

        def to_utc(dt: datetime.datetime | None):
            if dt is None: return None
            return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt

        ends_at = to_utc(self.ends_at)
        created_at = to_utc(self.created_at)
        annulled_at = to_utc(self.annulled_at)

        e = discord.Embed(
            title=f"{emoji} Заявление на {self.leave_type.value} отпуск #{self.id}",
            color=color,
            timestamp=created_at,
        )

        requester = await User.find_one(User.discord_id == self.user_id)
        e.add_field(name="Имя Фамилия", value=requester.full_name, inline=True)
        e.add_field(name="Статик", value=format_game_id(requester.static), inline=True)

        e.add_field(name="Звание", value=display_rank(requester.rank), inline=False)
        div_name = divisions.get_division_name(requester.division) or "Нет"
        e.add_field(name="Подразделение", value=div_name, inline=True)

        if requester.position:
            e.add_field(name="Должность", value=requester.position, inline=True)

        if self.ends_at:
            ends_fmt = (
                f"{discord.utils.format_dt(ends_at, 'd')} "
                f"({discord.utils.format_dt(ends_at, 'R')})"
            )
            e.add_field(name="Дата окончания", value=ends_fmt, inline=False)
        else:
            e.add_field(name="Продолжительность", value=f"{self.days} дн.", inline=False)

        e.add_field(name="Причина", value=self.reason, inline=False)

        if self.reviewer_id:
            e.add_field(name="Рассмотрел", value=f"<@{self.reviewer_id}>", inline=True)

        if self.annuller_id and annulled_at:
            e.add_field(
                name="Аннулировал",
                value=(
                    f"<@{self.annuller_id}> "
                    f"{discord.utils.format_dt(annulled_at, 'R')}"
                ),
                inline=False,
            )

        e.set_footer(text="Отправлено")
        return e

    class Settings:
        name = "leave_requests"

class BottomMessage(Document):
    channel_id: Indexed(int, unique=True)
    message_id: int

    class Settings:
        name = "bottom_messages"
