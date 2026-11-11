"""Read CSV / XLSX files into lists of dicts with normalised header names."""

import csv
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

Row = dict[str, str]
MAX_FILE_BYTES = 20 * 1024 * 1024
HEADER_SCAN_ROWS = 40


def norm_header(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _raw_rows(path: Path) -> Iterable[Sequence[object]]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            yield from csv.reader(f)
    elif suffix == ".xlsx":
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            yield from wb.worksheets[0].iter_rows(values_only=True)
        finally:
            wb.close()
    else:
        raise ValueError(f"unsupported file type {suffix!r} (use .csv or .xlsx)")


def peek_headers(path: Path) -> set[str]:
    """Normalised column names from the first rows; XLSX files may have title rows above the header."""
    found: set[str] = set()
    for i, row in enumerate(_raw_rows(path)):
        if i >= HEADER_SCAN_ROWS:
            break
        found.update(norm_header(c) for c in row if c not in (None, ""))
    return found


def read_table(path: Path, required: set[str]) -> list[Row]:
    """Rows after the first line that contains all `required` headers. Empty lines are skipped."""
    header: list[str] | None = None
    out: list[Row] = []
    for i, row in enumerate(_raw_rows(path)):
        if header is None:
            if i >= HEADER_SCAN_ROWS:
                break
            candidate = [norm_header(c) if c is not None else "" for c in row]
            if required <= set(candidate):
                header = candidate
            continue
        cells = [_cell(c) for c in row]
        if not any(cells):
            continue
        out.append({h: cells[j] if j < len(cells) else "" for j, h in enumerate(header) if h})
    if header is None:
        raise ValueError(f"{path.name}: no header row containing {sorted(required)}")
    return out
