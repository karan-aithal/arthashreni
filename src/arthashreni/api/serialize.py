"""Turn calculation results into JSON. A number never travels without its status and as-of (E2):
every `Measured` becomes {value, status, as_of, sources, notes}; Decimals stay strings (no float)."""

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from arthashreni.dq.measured import Measured


def jsonable(obj: Any) -> Any:
    if isinstance(obj, Measured):
        return {
            "value": None if obj.value is None else jsonable(obj.value),
            "status": obj.status.value,
            "as_of": obj.as_of.isoformat(),
            "sources": list(obj.source_ids),
            "notes": list(obj.notes),
        }
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [jsonable(v) for v in obj]
    return obj
