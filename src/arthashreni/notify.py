"""Notification channel (G3): a local desktop notification for new, de-duplicated risk alerts.

Only `notify-send` on this machine is used, so nothing leaves it (invariant 10). Email and Telegram would send
portfolio-derived text to an outside service and need the investor's explicit, logged consent; they are not
built. A missing `notify-send`, no desktop session or `ARTHA_NOTIFY=off` is not an error: the alert is still
stored and shown in the CLI, API and report (invariant 13). Finishing a job is never notified.
"""

import logging
import shutil
import subprocess
from collections.abc import Sequence

from arthashreni.core.config import get_settings
from arthashreni.risk.alerts import Alert
from arthashreni.risk.thresholds import Level

log = logging.getLogger(__name__)
MAX_PER_RUN = 3  # a bad day gives one summary line, not a flood
TIMEOUT_S = 5


def _send(title: str, body: str, *, urgent: bool) -> str:
    exe = shutil.which("notify-send")
    if exe is None:
        return "unavailable: notify-send not installed"
    cmd = [
        exe,
        "--app-name=ArthaShreni",
        f"--urgency={'critical' if urgent else 'normal'}",
        "--",
        title,
        body,
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed: {type(exc).__name__}"
    return "sent" if done.returncode == 0 else f"failed: exit {done.returncode}"


def notify_alerts(dataset: str, alerts: Sequence[Alert]) -> dict[str, object]:
    """Notify new alerts, highest level first, at most `MAX_PER_RUN` plus one line for the rest."""
    channel = get_settings().notify
    if channel == "off":
        return {"channel": "off", "sent": 0}
    if not alerts:
        return {"channel": channel, "sent": 0}
    ordered = sorted(alerts, key=lambda a: (-int(a.level), a.key))
    shown, rest = ordered[:MAX_PER_RUN], ordered[MAX_PER_RUN:]
    results = []
    for a in shown:
        title = f"ArthaShreni {dataset.upper()}: {a.level.name.replace('_', ' ').title()} alert"
        results.append(_send(title, a.what, urgent=a.level >= Level.HIGH_RISK))
    if rest:
        results.append(
            _send(
                f"ArthaShreni {dataset.upper()}",
                f"{len(rest)} more new alert(s); open the Risk Monitor.",
                urgent=False,
            )
        )
    sent = sum(r == "sent" for r in results)
    for r in results:
        if r != "sent":
            log.warning("desktop notification not delivered: %s", r)
    return {"channel": channel, "sent": sent, "not_delivered": [r for r in results if r != "sent"]}


def send_test(dataset: str) -> str:
    return _send(
        f"ArthaShreni {dataset.upper()}: test",
        "If you can read this, risk alerts will reach you.",
        urgent=False,
    )
