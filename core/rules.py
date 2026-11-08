from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.constants import DivisionId, RankIndex, Privilege, RoleType

if TYPE_CHECKING:
    from database.models import User


@dataclass(frozen=True, slots=True)
class DivisionRule:
    review_rank: int
    promote_rank: int = RankIndex.MAJOR
    review_positions: tuple[str, ...] = ()
    promote_positions: tuple[str, ...] = ()
    reviewer_division_id: int | None = None

    def can_review(self, user: User) -> bool:
        if (user.rank or 0) >= self.review_rank:
            return True
        if user.position and self.review_positions:
            return user.position.strip().lower() in {p.lower() for p in self.review_positions}
        return False

    def can_promote(self, user: User) -> bool:
        if (user.rank or 0) >= self.promote_rank:
            return True
        if user.position and self.promote_positions:
            return user.position.strip().lower() in {p.lower() for p in self.promote_positions}
        return False


PROMOTION_RULES: dict[int, DivisionRule] = {
    DivisionId.VA: DivisionRule(
        review_rank=RankIndex.SENIOR_SERGEANT,
        promote_rank=RankIndex.MAJOR,
        promote_positions=("Заместитель начальника по кадровой работе",),
        reviewer_division_id=DivisionId.VK,
    ),
    DivisionId.KMB: DivisionRule(
        review_rank=RankIndex.SENIOR_SERGEANT,
        promote_rank=RankIndex.MAJOR,
        promote_positions=("Заместитель начальника по кадровой работе",),
        reviewer_division_id=DivisionId.VK,
    ),
    DivisionId.VK: DivisionRule(
        review_rank=RankIndex.JUNIOR_LIEUTENANT,
        promote_rank=RankIndex.MAJOR,
        promote_positions=("Заместитель начальника по кадровой работе",),
    ),
    DivisionId.MR: DivisionRule(
        review_rank=RankIndex.CAPTAIN,
        promote_rank=RankIndex.MAJOR,
        review_positions=("Ком. ПГ", "Старший Инструктор", "Санитарный инструктор"),
        promote_positions=("Ком. ПГ", "Старший Инструктор",),
    ),
    DivisionId.VP: DivisionRule(
        review_rank=RankIndex.MAJOR,
        promote_rank=RankIndex.MAJOR,
        review_positions=("Помощник начальника управления",),
        promote_positions=("Помощник начальника управления",),
    ),
    DivisionId.ROIO: DivisionRule(
        review_rank=RankIndex.MAJOR,
        promote_rank=RankIndex.MAJOR,
        review_positions=("Командир отделения", "Старший сотрудник"),
    ),
    DivisionId.SSO: DivisionRule(
        review_rank=RankIndex.MAJOR,
        promote_rank=RankIndex.MAJOR,
    ),
}
DIVISION_EXTERNAL_GRANT_CEILING: dict[DivisionId, Privilege] = {
    DivisionId.GENERAL_STAFF: Privilege.COMMANDER,
}
ROLE_REQUIRED_RANKS = {
    RoleType.ARMY: "Младший лейтенант",
    RoleType.KMB: "Младший лейтенант",
    RoleType.SUPPLY_ACCESS: "Подполковник",
    RoleType.GOV_EMPLOYEE: "Полковник",
}
ROLE_REQUIRED_RANK_INDICES = {
    RoleType.ARMY: RankIndex.JUNIOR_LIEUTENANT,
    RoleType.KMB: RankIndex.JUNIOR_LIEUTENANT,
    RoleType.SUPPLY_ACCESS: RankIndex.LIEUTENANT_COLONEL,
    RoleType.GOV_EMPLOYEE: RankIndex.LIEUTENANT_COLONEL,
}
