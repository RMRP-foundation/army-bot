from __future__ import annotations

from core import config
from core.constants import nickname_regex
from core.exceptions import ServiceError
from database.models import User


def format_static(static_id: int | None) -> str:
    """Форматирует ID паспорта в вид XXX-XXX (например, 537-328)."""
    if static_id is None:
        return "N/A"

    static_id_str = str(static_id).zfill(6)
    return f"{static_id_str[:3]}-{static_id_str[3:]}"

def transliterate_abbreviation(abbreviation: str) -> str:
    """Транслитерирует буквы кириллицы на похожие символы латиницы для никнейма."""
    translit_map = {
        "А": "A",
        "В": "B",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "Х": "X",
    }
    return "".join(translit_map.get(char, char) for char in abbreviation)

def parse_name(raw_name: str | None) -> tuple[str, str] | None:
    """
        Парсит полное имя в формате 'Имя Фамилия'.
        Возвращает (first_name, last_name) или None, если формат неверный.
    """
    if not raw_name:
        return None
    match = nickname_regex.match(raw_name.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def parse_static(raw_static: str | None) -> int | None:
    """Преобразует строку статика в int. Возвращает None при неверном формате."""
    if not raw_static:
        return None
    cleaned = "".join(filter(str.isdigit, raw_static))
    if not cleaned or len(cleaned) > 6:
        return None
    try:
        val = int(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None

def clean_name(value: str) -> str:
    """Проверяет имя, убирает пробелы по краям и возвращает строку 'Иван Иванов'."""
    parsed = parse_name(value)
    if not parsed:
        raise ServiceError("### Вы ввели некорректное имя и фамилию.\nПравильный формат: Иван Иванов.")
    return f"{parsed[0]} {parsed[1]}"

def clean_static(value: str) -> int:
    """Проверяет и конвертирует статик в число int."""
    val = parse_static(value)
    if val is None:
        raise ServiceError("### Вы ввели некорректный статик. Правильный формат: ХХХ-ХХХ. Пример: 537-328.")
    return val


def set_name_if_changed(user: User, full_name: str) -> bool:
    """
    Обновляет имя пользователя, если оно отличается от текущего.
    True — если что-то поменялось.
    """
    parsed = parse_name(full_name)
    if not parsed:
        return False

    first_name, last_name = parsed
    if user.first_name == first_name and user.last_name == last_name:
        return False

    user.first_name, user.last_name = first_name, last_name
    return True


def format_rank(rank_index: int | None) -> str:
    """Возвращает 'Эмодзи Название ранга' или 'Без звания', если ранг невалиден."""
    if rank_index is not None and 0 <= rank_index < len(config.RANKS):
        return f"{config.RANK_EMOJIS[rank_index]} {config.RANKS[rank_index]}"

    return "Без звания"