"""Deterministic row fingerprints for duplicate detection."""

import hashlib
from collections.abc import Sequence
from datetime import date
from decimal import Decimal


def _norm(part: object) -> str:
    if part is None:
        return ""
    if isinstance(part, Decimal):
        return format(part.normalize(), "f")
    if isinstance(part, date):
        return part.isoformat()
    return str(part).strip().upper()


def fingerprint(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(_norm(p) for p in parts).encode()).hexdigest()


def with_occurrence(keys: Sequence[tuple[object, ...]]) -> list[str]:
    """Fingerprint each key plus its occurrence number, so two genuinely identical trades in one
    file stay distinct while the same file (or an overlapping one) maps to the same fingerprints."""
    seen: dict[tuple[str, ...], int] = {}
    out: list[str] = []
    for key in keys:
        norm = tuple(_norm(p) for p in key)
        n = seen.get(norm, 0) + 1
        seen[norm] = n
        out.append(fingerprint(*key, n))
    return out
