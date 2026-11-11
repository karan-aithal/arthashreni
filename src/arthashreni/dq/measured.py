"""`QualityStatus` and `Measured` (E1, E2, ADR-0007). Decimal only; no float."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from enum import StrEnum


class QualityStatus(StrEnum):
    OK = "ok"
    ESTIMATED = "estimated"
    STALE = "stale"
    CONFLICTING = "conflicting"
    MISSING = "missing"
    QUARANTINED = "quarantined"

    @property
    def severity(self) -> int:
        return _ORDER.index(self)


_ORDER = list(QualityStatus)  # best to worst (ADR-0007)


def worst(*statuses: QualityStatus) -> QualityStatus:
    return max(statuses, key=lambda s: s.severity, default=QualityStatus.OK)


@dataclass(frozen=True)
class Measured[T]:
    value: T | None
    status: QualityStatus
    as_of: datetime
    source_ids: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """A value that may feed a calculation: present and not quarantined."""
        return self.value is not None and self.status is not QualityStatus.QUARANTINED


def day_stamp(d: date) -> datetime:
    """Midnight UTC of a business date: the `as_of` of a value that is true for that day."""
    return datetime.combine(d, time.min, tzinfo=UTC)


def missing[T](as_of: datetime | None = None, note: str = "no data") -> "Measured[T]":
    return Measured(None, QualityStatus.MISSING, as_of or datetime.now(UTC), (), (note,))


def with_status[T](m: Measured[T], status: QualityStatus, note: str) -> Measured[T]:
    """Degrade (never improve) a status and record why."""
    return replace(m, status=worst(m.status, status), notes=(*m.notes, note))


def _combine(
    a: Measured[Decimal], b: Measured[Decimal], fn: Callable[[Decimal, Decimal], Decimal]
) -> Measured[Decimal]:
    status = worst(a.status, b.status)
    notes = (*a.notes, *b.notes)
    as_of = min(a.as_of, b.as_of)
    sources = tuple(dict.fromkeys((*a.source_ids, *b.source_ids)))
    if status is QualityStatus.QUARANTINED or a.value is None or b.value is None:
        out = status if status.severity >= QualityStatus.MISSING.severity else QualityStatus.MISSING
        return Measured(None, out, as_of, sources, (*notes, "an input has no usable value"))
    try:
        return Measured(fn(a.value, b.value), status, as_of, sources, notes)
    except (DivisionByZero, InvalidOperation, Overflow):
        return Measured(None, QualityStatus.MISSING, as_of, sources, (*notes, "arithmetic error"))


def m_add(a: Measured[Decimal], b: Measured[Decimal]) -> Measured[Decimal]:
    return _combine(a, b, lambda x, y: x + y)


def m_sub(a: Measured[Decimal], b: Measured[Decimal]) -> Measured[Decimal]:
    return _combine(a, b, lambda x, y: x - y)


def m_mul(a: Measured[Decimal], b: Measured[Decimal]) -> Measured[Decimal]:
    return _combine(a, b, lambda x, y: x * y)


def m_div(a: Measured[Decimal], b: Measured[Decimal]) -> Measured[Decimal]:
    """Division by zero returns `missing` with a note; it never raises into a report."""
    if b.value == 0:
        return Measured(
            None,
            QualityStatus.MISSING,
            min(a.as_of, b.as_of),
            tuple(dict.fromkeys((*a.source_ids, *b.source_ids))),
            (*a.notes, *b.notes, "division by zero"),
        )
    return _combine(a, b, lambda x, y: x / y)


def m_sum(items: Iterable[Measured[Decimal]], as_of: datetime | None = None) -> Measured[Decimal]:
    acc = Measured(Decimal(0), QualityStatus.OK, as_of or datetime.max.replace(tzinfo=UTC), ())
    for m in items:
        acc = m_add(acc, m)
    return acc
