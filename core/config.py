import logging
import os
from enum import Enum, IntEnum

from dotenv import load_dotenv

from core.constants import RANKS, RankIndex
from database.models import RoleType, Privilege
from utils.permissions import DivisionRule

load_dotenv()

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise Exception("TOKEN not found")

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    MONGO_URI = "mongodb://localhost:27017"
    logger.warning("MONGO_URI not found, using default value")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "TimohaBot")

ENVIRONMENT = os.getenv("ENVIRONMENT")
if not ENVIRONMENT:
    raise Exception("ENVIRONMENT not set")

SENTRY_DSN = os.getenv("SENTRY_DSN")

IS_PRODUCTION = ENVIRONMENT.lower() == "production"
GUILD_ID = 1245655012550512670 if IS_PRODUCTION else 1469687237351309444

SERVICE_ACCOUNT_IDS: frozenset[int] = frozenset({
    531744307103662080,
    538662196297859082

})


class DivisionId(IntEnum):
    VPB = 0
    VA = 1
    VK = 2
    ROIO = 3
    VP = 4
    SSO = 5
    MR = 6
    GENERAL_STAFF = 7
    KMB = 8


if IS_PRODUCTION:

    class RoleId(Enum):
        REINFORCEMENT = 1318305723637301268
        ATTESTATION = 1246115278992048262
        CONTRACT = 1246115199124111544
        MILITARY = 1246114676136218714  # Военнослужащий ВС РФ
        MILITARY_ACADEMY = 1246114673460252692  # Военная академия
        KMB = 1484589979492552724  # КМБ
        SUPPLY_ACCESS = 1246115197781934161  # Доступ к поставке
        GOV_EMPLOYEE = 1251432181381861477  # Гос. сотрудник
        GENERAL_HQ = 1411507570195173447  # Генеральный штаб
        BRIGADE_HQ = 1246113826953367714  # Штаб бригады
        UNIT_COMMANDER = 1246113827972583474  # Командир подразделения
        UNIT_DEPUTY_COMMANDER = 1246113944524034070  # Зам. командира подразделения
        SUPPLIER = 1262452148675809380  # Поставщик
        IC_LEAVE = 1246115364732141569  # IC Отпуск
        OOC_LEAVE = 1246114982995820634  # OOC отпуск

else:

    class RoleId(Enum):
        REINFORCEMENT = 1484560946947948674
        ATTESTATION = 1484560969219707072
        CONTRACT = 1484560993286750259
        MILITARY = 1484561070495633450  # Военнослужащий ВС РФ
        MILITARY_ACADEMY = 1484561095476908133  # Военная академия
        KMB = 1487473018576965864  # КМБ
        SUPPLY_ACCESS = 1484561120604979370  # Доступ к поставке
        GOV_EMPLOYEE = 1484561182722494484  # Гос. сотрудник
        GENERAL_HQ = 1484561198711308459  # Генеральный штаб
        BRIGADE_HQ = 1484561252553457735  # Штаб бригады
        UNIT_COMMANDER = 1484561277559902321  # Командир подразделения
        UNIT_DEPUTY_COMMANDER = 1484561300544552960  # Зам. командира подразделения
        SUPPLIER = 1484937657753800915  # Поставщик
        IC_LEAVE = 1490255314459824259  # IC Отпуск
        OOC_LEAVE = 1490255334265327616  # OOC отпуск


RANK_ROLES = dict(zip(RANKS, [
    1246114675574313021,  # Рядовой
    1246114674638983270,  # Ефрейтор
    1261982952275972187,  # Младший сержант
    1246114673997123595,  # Сержант
    1246114672352952403,  # Старший сержант
    1246114604958879754,  # Старшина
    1246114604329865327,  # Прапорщик
    1251045305793773648,  # Старший прапорщик
    1251045263062335590,  # Младший лейтенант
    1246115365746901094,  # Лейтенант
    1246114469340250214,  # Старший лейтенант
    1246114469336322169,  # Капитан
    1246114042821607424,  # Майор
    1246114038744875090,  # Подполковник
    1246113825791672431,  # Полковник
    1246113709093556337,  # Генерал-майор
    1262442013241118820,  # Генерал-лейтенант
    1262442305290371153,  # Генерал-полковник
    1246113708514476073,  # Генерал армии
]))

if not IS_PRODUCTION:
    RANK_ROLES["Рядовой"] = 1554454960345710592
    RANK_ROLES["Младший сержант"] = 1554454940368506971
    RANK_ROLES["Старший сержант"] = 1554454898928656425

RANK_EMOJIS = [
    "<:ryadovoy:1484957675136745482>",  # Рядовой
    "<:efreytor:1484957441086062702>",  # Ефрейтор
    "<:ml_serzhant:1484957607171981332>",  # Младший сержант
    "<:serzhant:1484957690286313592>",  # Сержант
    "<:st_serzhant:1484957735891239075>",  # Старший сержант
    "<:starshina:1484957752500682913>",  # Старшина
    "<:praporshchik:1484957660632580297>",  # Прапорщик
    "<:st_praporshchik:1484957718258389002>",  # Старший прапорщик
    "<:ml_leytenant:1484957589019299982>",  # Младший лейтенант
    "<:leytenant:1484957561328504963>",  # Лейтенант
    "<:st_leytenant:1484957705331540158>",  # Старший лейтенант
    "<:kapitan:1484957527945908275>",  # Капитан
    "<:mayor:1484957576188657854>",  # Майор
    "<:podpolkovnik:1484957628856664114>",  # Подполковник
    "<:polkovnik:1484957641712336976>",  # Полковник
    "<:generalmayor:1484957498589974650>",  # Генерал-майор
    "<:generalleytenant:1484957485898010696>",  # Генерал-лейтенант
    "<:generalpolkovnik:1484957509889294336>",  # Генерал-полковник
    "<:general_armii:1484957467979944147>",  # Генерал армии
]

CHANNELS = (
    {
        "audit": 1246119365607424050,
        "reinstatement": 1317830537724952626,
        "role_getting": 1246118891864723576,
        "blacklist": 1246119574357807246,
        "storage_requests": 1386780423098732567,
        "storage_audit": 1246119396225843261,
        "dismissal": 1246119825487564981,
        "static_log": 1246123219006787636,
        "timeoff": 1246119743602299022,
        "sso_patrol": 1291122702752420013,
        "materials": 1318570731457613844,
        "logistics": 1424686733319995432,
        "ic_leave": 1276358675396690024,
        "ooc_leave": 1246119775436931182,
    }
    if IS_PRODUCTION
    else {
        "audit": 1484560246348189717,
        "reinstatement": 1484560277981757621,
        "role_getting": 1484560302598127927,
        "blacklist": 1484560329949057084,
        "storage_requests": 1484560361645543517,
        "storage_audit": 1484560396496146594,
        "dismissal": 1484560450271182968,
        "static_log": 1484560489861091551,
        "timeoff": 1484560529937928293,
        "sso_patrol": 1484560560421998755,
        "materials": 1484560587101831288,
        "logistics": 1484936671169614005,
        "ic_leave": 1490255233345912913,
        "ooc_leave": 1490255253918974114,
    }
)


ROLE_DISPLAY_NAMES = {
        RoleType.ARMY: "ВС РФ",
        RoleType.KMB: "КМБ",
        RoleType.SUPPLY_ACCESS: "Доступ к поставке",
        RoleType.GOV_EMPLOYEE: "Гос. сотрудник",
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


IC_MIN_DAYS = 1
IC_MAX_DAYS = 5
OOC_MIN_DAYS = 7
OOC_MAX_DAYS = 30

ACADEMY_DAYS_LIMIT = 10

MATERIALS_MENTIONS = (1245655012760092707, 1246113710255374336)
BLACKLIST_MENTIONS = (1245655012760092707, 1245655012760092705, 1246113710255374336)
SUPPLIES_AUDIT_MENTIONS = (1245655012760092707, 1245655012760092705)
#                   Гражданин
EXCLUDED_ROLES = (1444484048629137579,)

PENALTY_THRESHOLD = 5
#                1 предупреждение     2 предупреждения     1 выговор             2 выговора
PENALTY_ROLES = (1246114985973645402, 1246114985508081804, 1246114984950239304, 1524056985677467678)
INVESTIGATION_ROLE = 1399081100927565945  # Ведется расследование

ROLE_RESUBMIT_COOLDOWN_HOURS = 24
SSO_FAIL_COOLDOWN = 300

DIVISION_EXTERNAL_GRANT_CEILING: dict[DivisionId, Privilege] = {
    DivisionId.GENERAL_STAFF: Privilege.COMMANDER,
}

PROMOTION_SIMPLE_EVIDENCE_DIVISIONS = {1, 8}
PROMOTION_NOTIFY_ROLES: dict[int, tuple[int, ...]] = {
    DivisionId.VA: (1246114382208040970, 1246113946872840214, 1477926048745128048),
    DivisionId.VK: (1246114382208040970, 1246113946872840214, 1477925799205015582,
        1524846478856818977, 1524846485907181668),
    DivisionId.ROIO: (1294219273031516261, 1246114041227640934, 1313584188116435044),
    DivisionId.VP: (1246113944897323150, 1246114040024010752),
    DivisionId.SSO: (1246113945887182848, 1246114040758013952),
    DivisionId.MR: (1246114039470227516, 1251045308729790556),
    DivisionId.KMB: (1246114382208040970, 1246113946872840214,
        1524846478856818977, 1524846485907181668),
}

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
        review_positions=("Командир отделения",),
    ),
    DivisionId.SSO: DivisionRule(
        review_rank=RankIndex.MAJOR,
        promote_rank=RankIndex.MAJOR,
    ),
}

SUPPLY_INFO_LINK = "https://discord.com/channels/1245655012550512670/1251166871064019015/1543582992948007024"
