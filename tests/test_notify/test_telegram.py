"""Severity routing for Telegram alerts: critical-only by default."""

from atlas.bus import IncidentEvent
from atlas.config import TelegramSection
from atlas.notify.telegram import TelegramNotifier


def _notifier(min_severity: str) -> tuple[TelegramNotifier, list[str]]:
    config = TelegramSection(enabled=True, bot_token="x", chat_id="1", min_severity=min_severity)
    notifier = TelegramNotifier(config, db=None, bus=None)  # type: ignore[arg-type]
    sent: list[str] = []

    async def record(text: str, incident_id: int | None) -> None:
        sent.append(text)

    notifier._send = record  # type: ignore[method-assign]
    return notifier, sent


def _event(kind: str, severity: str) -> IncidentEvent:
    return IncidentEvent(1, kind, severity, "backup for app:x is 60h old", "app:x")


async def test_default_forwards_critical_only() -> None:
    notifier, sent = _notifier("critical")
    await notifier._on_incident(_event("opened", "warning"))
    assert sent == []
    await notifier._on_incident(_event("opened", "critical"))
    assert len(sent) == 1 and sent[0].startswith("🔴 ATLAS:")
    notifier._last_sent = 0  # step past the resolved-event rate limit
    await notifier._on_incident(_event("resolved", "critical"))
    assert len(sent) == 2 and sent[1].startswith("✅ ATLAS resolved:")


async def test_warning_opt_in() -> None:
    notifier, sent = _notifier("warning")
    await notifier._on_incident(_event("opened", "warning"))
    assert len(sent) == 1 and sent[0].startswith("⚠️ ATLAS:")
    notifier._last_sent = 0  # step past the escalated-event rate limit
    await notifier._on_incident(_event("escalated", "critical"))
    assert len(sent) == 2 and sent[1].startswith("🔴 ATLAS escalated:")


async def test_dedupe_window() -> None:
    notifier, sent = _notifier("critical")
    await notifier._on_incident(_event("opened", "critical"))
    await notifier._on_incident(_event("opened", "critical"))  # same key, within window
    assert len(sent) == 1
