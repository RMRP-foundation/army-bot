"""Слой данных админ-панели для Beanie/MongoDB: универсальный CRUD и досье бойца."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel

from database.counters import Counter
from database.models import (
    BottomMessage,
    DismissalRequest,
    Division,
    LeaveRequest,
    LogisticsRequest,
    MaterialsReport,
    PromotionRequest,
    ReinstatementRequest,
    RoleRequest,
    SSOPatrolRequest,
    SupplyRequest,
    TimeoffRequest,
    TransferRequest,
    User,
)

ALL_MODELS: list[type] = [
    User,
    Division,
    ReinstatementRequest,
    RoleRequest,
    TimeoffRequest,
    SupplyRequest,
    DismissalRequest,
    TransferRequest,
    SSOPatrolRequest,
    MaterialsReport,
    LogisticsRequest,
    LeaveRequest,
    PromotionRequest,
    BottomMessage,
    Counter,
]

_USER_FK = ("user_id", "user")
_TRUE = {"true", "1", "yes", "да"}
_FALSE = {"false", "0", "no", "нет"}


def get_models() -> dict[str, type]:
    """Возвращает словарь {collection_name: ModelClass}."""
    result = {}
    for m in ALL_MODELS:
        name = getattr(m.Settings, "name", m.__name__.lower())
        result[name] = m
    return dict(sorted(result.items(), key=lambda kv: kv[0]))


def pk_name(model: type) -> str:
    if model is User:
        return "discord_id"
    if model is Division:
        return "division_id"
    if model is BottomMessage:
        return "channel_id"
    if model is Counter:
        return "name"
    return "id"


def _get_model_fields(model: type) -> dict:
    if hasattr(model, "model_fields"):
        return model.model_fields
    if hasattr(model, "__fields__"):
        return model.__fields__
    return {}


def fields(model: type) -> list[str]:
    return list(_get_model_fields(model).keys())


def parse_value(model: type, field_name: str, raw: str) -> Any:
    s = raw.strip()
    if s.lower() in ("null", "none") or s == "":
        return None

    field_info = _get_model_fields(model).get(field_name)
    annotation = getattr(field_info, "annotation", getattr(field_info, "outer_type_", str))

    # Списки и словари / Pydantic JSON
    if s.startswith("{") or s.startswith("["):
        return json.loads(s)

    # Булевы значения
    if annotation is bool or "bool" in str(annotation).lower():
        if s.lower() in _TRUE:
            return True
        if s.lower() in _FALSE:
            return False
        raise ValueError("Ожидалось: true/false/1/0")

    # Числовые значения
    if annotation is int or "int" in str(annotation).lower():
        if s.lstrip("-").isdigit():
            return int(s)
        raise ValueError("Ожидалось целое число")

    # Дата и время
    if "datetime" in str(annotation).lower():
        if s.lstrip("-").isdigit():
            return dt.datetime.fromtimestamp(int(s), dt.timezone.utc)
        val = dt.datetime.fromisoformat(s)
        return val if val.tzinfo else val.replace(tzinfo=dt.timezone.utc)

    # Enums
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(s)
        except ValueError:
            return annotation[s]

    return s


def format_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if isinstance(value, BaseModel):
        if hasattr(value, "model_dump_json"):
            return value.model_dump_json()
        return value.json()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def render_record(model: type, record: Any, limit: int = 3000, value_limit: int = 150) -> str:
    lines = []
    pk = pk_name(model)
    for f in fields(model):
        val = getattr(record, f, None)
        text = format_value(val).replace("\n", " ⏎ ").replace("`", "'")
        if len(text) > value_limit:
            text = text[:value_limit] + "…"
        prefix = "🔑 " if f == pk else ""
        lines.append(f"{prefix}`{f}` = {text}")
    res = "\n".join(lines)
    return res if len(res) <= limit else res[: limit - 1] + "…"


async def find_records(model: type, field: str, raw: str, limit: int = 10) -> list:
    val = parse_value(model, field, raw)
    query = {field: val}
    res = await model.find(query).limit(limit).to_list()
    if not res and field == "division_id":
        res = await model.find({"id": val}).limit(limit).to_list()
    return res


async def reload(model: type, record: Any):
    pk = pk_name(model)
    val = getattr(record, pk, None)
    res = await model.find_one({pk: val})
    if not res and pk == "division_id":
        res = await model.find_one({"id": val})
    return res


async def update_fields(model: type, record: Any, changes: dict[str, str]):
    target = await reload(model, record)
    if not target:
        raise ValueError("Запись уже удалена")

    pk = pk_name(model)
    diff = []
    for f, raw in changes.items():
        if f == pk:
            raise ValueError("Первичный ключ менять нельзя")
        if f not in fields(model):
            raise ValueError(f"Поле '{f}' отсутствует в модели")

        old_val = getattr(target, f, None)
        new_val = parse_value(model, f, raw)
        diff.append((f, format_value(old_val), format_value(new_val)))
        setattr(target, f, new_val)

    await target.save()
    return diff, await reload(model, target)


@dataclass
class DossierSection:
    table: str
    model: type
    total: int
    rows: list


async def find_user(raw: str) -> User | None:
    digits = raw.strip().replace("-", "").replace(" ", "")
    if not digits.isdigit():
        raise ValueError("Нужен числовой Discord ID или статик")
    if len(digits) >= 15:
        return await User.find_one(User.discord_id == int(digits))
    return await User.find_one(User.static == int(digits))


async def dossier_sections(discord_id: int, per_model: int = 2) -> list[DossierSection]:
    sections: list[DossierSection] = []
    for name, model in get_models().items():
        if model in (User, Division, BottomMessage, Counter):
            continue
        model_fields = fields(model)
        fk = next((k for k in _USER_FK if k in model_fields), None)
        if not fk:
            continue

        query = {fk: discord_id}
        total = await model.find(query).count()
        if not total:
            continue

        pk = pk_name(model)
        rows = await model.find(query).sort(f"-{pk}").limit(per_model).to_list()
        sections.append(DossierSection(name, model, total, rows))
    return sections


def row_summary(model: type, row: Any) -> str:
    pk = pk_name(model)
    val = getattr(row, pk, "?")
    status = getattr(row, "status", None)
    return f"#{val} ({status})" if status else f"#{val}"


async def load_row(model: type, pk_value: Any):
    pk = pk_name(model)
    if isinstance(pk_value, str) and pk_value.isdigit() and pk != "name":
        pk_value = int(pk_value)
    res = await model.find_one({pk: pk_value})
    if not res and pk == "division_id":
        res = await model.find_one({"id": pk_value})
    return res