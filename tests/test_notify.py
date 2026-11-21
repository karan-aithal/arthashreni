"""Desktop notifications (G3): only new alerts, capped, local only, and never an error when unavailable."""

import subprocess
from datetime import date
from typing import Any

import pytest

from arthashreni import notify
from arthashreni.core.config import get_settings
from arthashreni.risk.alerts import Alert
from arthashreni.risk.thresholds import Level


def alert(key: str, level: Level, what: str = "something fell") -> Alert:
    return Alert(
        key=key, level=level, what=what, when=date(2026, 11, 10), assets=(), evidence=(), sources=(),
        impact_inr=None, classification="uncertain", scenarios=(), responses=(), if_nothing="", watch="",
    )  # fmt: skip


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    monkeypatch.setenv("ARTHA_NOTIFY", "desktop")
    get_settings.cache_clear()
    return seen


def test_no_new_alerts_means_no_notification(calls: list[list[str]]) -> None:
    assert notify.notify_alerts("demo", []) == {"channel": "desktop", "sent": 0}
    assert calls == []


def test_a_new_alert_is_sent_with_its_dataset_and_level(calls: list[list[str]]) -> None:
    out = notify.notify_alerts("demo", [alert("a", Level.HIGH_RISK, "top-3 weight is high")])
    assert out["sent"] == 1 and len(calls) == 1
    cmd = calls[0]
    assert "--urgency=critical" in cmd and cmd[-3:] == [
        "--",
        "ArthaShreni DEMO: High Risk alert",
        "top-3 weight is high",
    ]


def test_caution_is_not_urgent(calls: list[list[str]]) -> None:
    notify.notify_alerts("real", [alert("a", Level.CAUTION)])
    assert "--urgency=normal" in calls[0] and calls[0][-2].startswith("ArthaShreni REAL")


def test_a_bad_day_is_capped_highest_level_first(calls: list[list[str]]) -> None:
    alerts = [alert(f"k{i}", Level.CAUTION) for i in range(4)] + [
        alert("worst", Level.SEVERE_STRESS, "worst one")
    ]
    out = notify.notify_alerts("demo", alerts)
    assert out["sent"] == 4 and len(calls) == 4  # three alerts and one summary line
    assert calls[0][-1] == "worst one" and "2 more new alert(s)" in calls[3][-1]


def test_off_sends_nothing(calls: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTHA_NOTIFY", "off")
    get_settings.cache_clear()
    assert notify.notify_alerts("demo", [alert("a", Level.SEVERE_STRESS)]) == {"channel": "off", "sent": 0}
    assert calls == []


def test_missing_notify_send_degrades_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    monkeypatch.setenv("ARTHA_NOTIFY", "desktop")
    get_settings.cache_clear()
    out = notify.notify_alerts("demo", [alert("a", Level.HIGH_RISK)])
    assert out["sent"] == 0 and "notify-send not installed" in str(out["not_delivered"])


def test_a_failing_or_hanging_command_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setenv("ARTHA_NOTIFY", "desktop")
    get_settings.cache_clear()

    def boom(cmd: list[str], **kw: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(notify.subprocess, "run", boom)
    assert "failed: TimeoutExpired" in str(notify.notify_alerts("demo", [alert("a", Level.HIGH_RISK)]))
    monkeypatch.setattr(notify.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    assert "failed: exit 1" in str(notify.notify_alerts("demo", [alert("a", Level.HIGH_RISK)]))


def test_alert_text_cannot_become_an_option(calls: list[list[str]]) -> None:
    notify.notify_alerts("demo", [alert("a", Level.CAUTION, "--hint=string:x:y")])
    assert calls[0].index("--") < calls[0].index("--hint=string:x:y")
