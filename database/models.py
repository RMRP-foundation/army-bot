import datetime
from enum import Enum
from typing import Dict

import discord
from beanie import Document, Indexed
from pydantic import BaseModel, Field
from pymongo import IndexModel

from core import constants


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
    promotion_channel: int | None = None

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
        return discord.utils.utcnow() < self.ends_at


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
        from utils.user_data import transliterate_abbreviation

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
            parts.append(constants.RANKS_SHORT[self.rank])
        if self.full_name:
            if len(" | ".join(parts + [self.full_name])) > 32:
                parts.append(self.short_name or self.full_name)
            else:
                parts.append(self.full_name)
        return " | ".join(parts)[:32]

    @staticmethod
    async def get_active_soldier(discord_id: int) -> "User | None":
        """Возвращает пользователя, если он состоит на службе (rank задан), иначе None.

        Args:
            discord_id: Discord ID пользователя.

        Returns:
            User или None.
        """
        user = await User.get_by_discord_id(discord_id)
        if not user or user.rank is None:
            return None
        return user

    @classmethod
    async def get_by_discord_id(cls, discord_id: int) -> "User | None":
        """Находит пользователя в базе данных по его Discord ID.

        Args:
            discord_id: Discord ID пользователя.

        Returns:
            Объект User, если пользователь найден; в противном случае None.
        """
        return await cls.find_one(cls.discord_id == discord_id)

    class Settings:
        name = "users"
        indexes = [IndexModel([("division", 1), ("rank", -1)])]


class ReinstatementData(BaseModel):
    full_name: str
    all_documents: str
    army_pass: str


class ReinstatementRequest(Document):
    id: int
    user: int
    data: ReinstatementData
    status: str = "PENDING"
    rank: int | None = None
    reject_reason: str | None = None
    sent_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)

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
    status: str = "PENDING"
    sent_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    message_id: int | None = None

    class Settings:
        name = "role_requests"

class TimeoffRequest(Document):
    id: int
    user_id: int
    data: RoleData
    status: str = "PENDING"
    period: str | None = None
    sent_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    reviewed_at: datetime.datetime | None = None

    class Settings:
        name = "timeoff_requests"


class SupplyRequest(Document):
    id: int
    user_id: int
    items: Dict[str, int] = Field(default_factory=dict)
    status: str = "PENDING"
    reviewer_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    reviewed_at: datetime.datetime | None = None
    message_id: int | None = None  # ID сообщения в канале

    class Settings:
        name = "supply_requests"
        indexes = [
            IndexModel([("user_id", 1), ("status", 1), ("created_at", -1)])
        ]


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
    reject_reason: str | None = None

    status: str = "PENDING"  # PENDING, APPROVED, REJECTED
    reviewer_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    reviewed_at: datetime.datetime | None = None

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
    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    old_reviewed_at: datetime.datetime | None = None
    new_reviewed_at: datetime.datetime | None = None
    reject_reason: str | None = None

    class Settings:
        name = "transfer_requests"

class SSOPatrolRequest(Document):
    id: int
    user_id: int
    full_name: str
    reason: str
    date: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    status: str = "PENDING"
    reviewer_id: int | None = None

    class Settings:
        name = "sso_patrol_requests"


class MaterialsReport(Document):
    id: int
    user_id: int
    full_name: str
    quantity: int
    evidence: str
    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)

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
    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)

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
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    original_nick: str | None = None  # Ник до отпуска для ССО

    status: str = "PENDING"
    reviewer_id: int | None = None
    annuller_id: int | None = None
    annulled_at: datetime.datetime | None = None

    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    approved_at: datetime.datetime | None = None
    message_id: int | None = None

    class Settings:
        name = "leave_requests"
        indexes = [IndexModel([("status", 1), ("ends_at", 1)])]


class PromotionRequest(Document):
    id: int
    user_id: int
    division_id: int
    current_rank: int
    target_rank: int
    evidence: Dict[str, str] = Field(default_factory=dict)
    score: str | None = None
    reject_reason: str | None = None
    status: str = "PENDING"  # PENDING, APPROVED, PROMOTED, REJECTED, CANCELLED
    reviewer_id: int | None = None
    promoted_by: int | None = None
    created_at: datetime.datetime = Field(default_factory=discord.utils.utcnow)
    message_id: int | None = None

    class Settings:
        name = "promotion_reports"
        indexes = [IndexModel([("user_id", 1), ("status", 1)])]


class BottomMessage(Document):
    channel_id: Indexed(int, unique=True)
    message_id: int

    class Settings:
        name = "bottom_messages"
