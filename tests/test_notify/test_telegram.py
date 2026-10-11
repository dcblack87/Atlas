"""Severity routing for Telegram alerts: critical-only by default."""

from atlas.bus import IncidentEvent
from atlas.config import TelegramSection
from atlas.notify.bot.transport import Result
from atlas.notify.telegram import TelegramNotifier


def _notifier(min_severity: str) -> tuple[TelegramNotifier, list[str]]:
    config = TelegramSection(enabled=True, bot_token="x", chat_id="1", min_severity=min_severity)
    notifier = TelegramNotifier(config, db=None, bus=None)  # type: ignore[arg-type]
    sent: list[str] = []

    async def record(text: str, incident_id: int | None, *, ack: bool = False) -> None:
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


async def test_reminder_is_sent_and_not_rate_limited() -> None:
    notifier, sent = _notifier("critical")
    await notifier._on_incident(_event("opened", "critical"))
    await notifier._on_incident(_event("reminder", "critical"))  # same second
    assert len(sent) == 2 and sent[1].startswith("🔴 ATLAS still open:")


class _FakeAPI:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, list | None]] = []

    async def send_message(self, chat_id: str, text: str, keyboard: list | None = None) -> Result:
        self.sent.append((chat_id, text, keyboard))
        return Result(True)


class _FakeDB:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    async def execute(self, sql: str, params: tuple = ()) -> int:
        self.rows.append(params)
        return 1


def _wired(**overrides: object) -> tuple[TelegramNotifier, _FakeAPI, _FakeDB]:
    config = TelegramSection(enabled=True, bot_token="x", chat_id="1", **overrides)  # type: ignore[arg-type]
    db = _FakeDB()
    notifier = TelegramNotifier(config, db=db, bus=None)  # type: ignore[arg-type]
    api = _FakeAPI()
    notifier._api = lambda: api  # type: ignore[method-assign]
    return notifier, api, db


def _data(keyboard: list | None) -> list[str]:
    return [b["callback_data"] for row in keyboard or [] for b in row]


async def test_an_open_incident_can_be_acknowledged_from_its_alert() -> None:
    notifier, api, db = _wired()
    await notifier._on_incident(
        IncidentEvent(42, "opened", "critical", "disk <full> & hot", "host:a")
    )
    chat_id, text, keyboard = api.sent[0]
    assert chat_id == "1"
    assert "disk &lt;full&gt; &amp; hot" in text  # escaped: a title cannot break the message
    # "open:" buttons answer below the alert, so the alert itself is never written over
    assert _data(keyboard) == ["open:ack:42", "open:incidents", "open:status"]
    assert db.rows[0][-2:] == (1, None)  # logged as delivered


async def test_a_resolved_alert_offers_nothing_to_acknowledge() -> None:
    notifier, api, _ = _wired()
    await notifier._on_incident(IncidentEvent(42, "resolved", "critical", "disk full", "host:a"))
    assert _data(api.sent[0][2]) == ["open:incidents", "open:status"]


async def test_alerts_carry_no_buttons_when_the_bot_is_not_listening() -> None:
    notifier, api, _ = _wired(bot_commands=False)
    await notifier._on_incident(IncidentEvent(42, "opened", "critical", "disk full", "host:a"))
    assert api.sent[0][2] is None


async def test_briefs_are_sent_and_each_has_a_switch() -> None:
    notifier, api, _ = _wired(weekly_brief=False)
    assert await notifier.send_brief("daily", "MORNING BRIEF\n\nfleet health 97/100 <ok>") is True
    assert "MORNING BRIEF" in api.sent[0][1] and "&lt;ok&gt;" in api.sent[0][1]
    assert _data(api.sent[0][2]) == ["open:status", "open:incidents", "open:help"]
    assert await notifier.send_brief("weekly", "WEEKLY BRIEF") is False
    assert len(api.sent) == 1


async def test_briefs_are_not_sent_when_telegram_is_off() -> None:
    config = TelegramSection(enabled=False, bot_token="x", chat_id="1")
    notifier = TelegramNotifier(config, db=None, bus=None)  # type: ignore[arg-type]
    assert await notifier.send_brief("daily", "MORNING BRIEF") is False
