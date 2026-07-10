"""The budget gate: checked before, recorded after, never estimated."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.ai.client import AIClient, BudgetExhausted
from atlas.config import AISection
from atlas.store.db import Database


class FakeMessages:
    def __init__(self, usage=None) -> None:
        self.usage = usage or SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="analysis text")],
            usage=self.usage,
        )


@pytest.fixture
async def env(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    await db.open()
    config = AISection(api_key="test-key", daily_budget_usd=1.0, auto_insight_budget_usd=0.5)
    client = AIClient(config, db)
    fake = FakeMessages()
    client._client = SimpleNamespace(messages=fake)
    yield client, fake, db
    await db.close()


async def test_cost_recorded_from_usage(env) -> None:
    client, _fake, _db = env
    text = await client.complete("insight", ["system"], "question")
    assert text == "analysis text"
    spend = await client.spend_today()
    # 1000 in * $5/M + 200 out * $25/M = 0.005 + 0.005 = $0.01
    assert spend["cost_usd"] == pytest.approx(0.01)
    assert spend["calls"] == 1


async def test_budget_blocks_before_call(env) -> None:
    client, fake, db = env
    await db.execute("INSERT INTO ai_spend (day, cost_usd, calls) VALUES (date('now'), 1.5, 3)")
    with pytest.raises(BudgetExhausted, match="daily AI budget"):
        await client.complete("insight", ["system"], "question")
    assert fake.calls == 0  # gate fired BEFORE the API call


async def test_auto_subbudget(env) -> None:
    client, _fake, db = env
    await db.execute(
        "INSERT INTO ai_spend (day, cost_usd, auto_cost_usd, calls)"
        " VALUES (date('now'), 0.6, 0.6, 2)"
    )
    # manual calls still fine under the main budget…
    await client.complete("chat", ["system"], "q")
    # …but auto calls are blocked by the sub-budget
    with pytest.raises(BudgetExhausted, match="auto-insight"):
        await client.complete("insight", ["system"], "q2", auto=True)


async def test_auto_dedupe_returns_cached(env) -> None:
    client, fake, _db = env
    first = await client.complete("insight", ["system"], "same question", auto=True)
    second = await client.complete("insight", ["system"], "same question", auto=True)
    assert first == second
    assert fake.calls == 1  # second answer came from the digest cache


async def test_auto_cooldown(env) -> None:
    client, *_ = env
    assert client.check_auto_cooldown("app:x")
    await client.complete("insight", ["system"], "q", auto=True, entity_key="app:x")
    assert not client.check_auto_cooldown("app:x")
