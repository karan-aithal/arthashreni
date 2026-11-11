import json
import logging

from arthashreni.core.logging import JsonFormatter, redact


def test_redacts_personal_identifiers_and_secrets() -> None:
    text = (
        "user ABCDE1234F mail a.b@example.com folio 12345678901 "
        # pragma: allowlist nextline secret
        "password=hunter2 url postgresql+psycopg://artha_demo:s3cret@127.0.0.1:5432/db"
    )
    out = redact(text)
    # pragma: allowlist nextline secret
    for leaked in ("ABCDE1234F", "a.b@example.com", "12345678901", "hunter2", "s3cret"):
        assert leaked not in out
    assert "[PAN]" in out and "[EMAIL]" in out and "password=***" in out


def test_json_formatter_redacts_extra_data() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "pan %s", ("ABCDE1234F",), None)
    record.data = {"email": "x@y.com"}
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "pan [PAN]"
    assert entry["data"] == {"email": "[EMAIL]"}
    assert entry["level"] == "INFO"
