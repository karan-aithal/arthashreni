"""Structured JSON logging with redaction of personal identifiers (security model)."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"postgresql(\+\w+)?://[^:/\s]+:[^@\s]+@"), "postgresql://***:***@"),
    (
        re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)(\"?\s*[:=]\s*\"?)[^\s\",&]+"),
        r"\1\2***",
    ),
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "[PAN]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b\d{10,}\b"), "[NUMBER]"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            entry["data"] = json.loads(redact(json.dumps(extra, default=str)))
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines carry full URLs (tickers)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
