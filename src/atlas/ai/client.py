"""The budget-gated Claude client. Every AI call in Atlas flows through here.

The gate is checked BEFORE each call against a spend ledger in SQLite; cost
is recorded AFTER from the API's actual usage numbers, never estimates.
Automatic (anomaly-triggered) calls have a separate sub-budget, a per-entity
cooldown, and a prompt-digest dedupe so a flapping incident can't spend
money in a loop.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from atlas.config import AISection
from atlas.store.db import Database

log = logging.getLogger(__name__)

# $/MTok — checked against the live price list when models change.
PRICING = {
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
}
FALLBACK_PRICING = {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75}

AUTO_COOLDOWN_S = 1800


class BudgetExhausted(Exception):
    pass


class AIDisabled(Exception):
    pass


class AIClient:
    def __init__(self, config: AISection, db: Database) -> None:
        self._config = config
        self._db = db
        self._client = None
        self._auto_last: dict[str, float] = {}  # entity -> last auto-insight ts

    def _sdk(self):
        if self._client is None:
            api_key = self._config.resolve_api_key()
            if not self._config.enabled or not api_key:
                raise AIDisabled("AI is disabled or no API key is configured")
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=api_key)
        return self._client

    # ── the gate ─────────────────────────────────────────────────────

    async def _check_budget(self, *, auto: bool) -> None:
        day = _today()
        row = await self._db.fetch_one("SELECT * FROM ai_spend WHERE day = ?", (day,))
        spent = row["cost_usd"] if row else 0.0
        auto_spent = row["auto_cost_usd"] if row else 0.0
        if spent >= self._config.daily_budget_usd:
            raise BudgetExhausted(
                f"daily AI budget exhausted (${spent:.2f} of ${self._config.daily_budget_usd:.2f})"
            )
        if auto and auto_spent >= self._config.auto_insight_budget_usd:
            raise BudgetExhausted("auto-insight sub-budget exhausted for today")

    def check_auto_cooldown(self, entity: str) -> bool:
        """True if an auto insight for this entity is allowed right now."""
        return time.time() - self._auto_last.get(entity, 0) > AUTO_COOLDOWN_S

    async def find_cached(self, digest: str) -> str | None:
        return await self._db.fetch_value(
            "SELECT response FROM ai_analyses WHERE prompt_digest = ? "
            "AND ts > ? ORDER BY ts DESC LIMIT 1",
            (digest, int(time.time()) - 86400),
        )

    # ── calls ────────────────────────────────────────────────────────

    async def complete(
        self,
        kind: str,
        system_blocks: list[str],
        user_content: str,
        *,
        auto: bool = False,
        entity_key: str | None = None,
        incident_id: int | None = None,
        max_tokens: int = 1500,
    ) -> str:
        """One-shot completion. Returns the text; records spend."""
        digest = _digest(system_blocks, user_content)
        if auto:
            cached = await self.find_cached(digest)
            if cached is not None:
                return cached
        await self._check_budget(auto=auto)
        client = self._sdk()

        response = await client.messages.create(
            model=self._config.model,
            max_tokens=max_tokens,
            system=_cacheable_system(system_blocks),
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        await self._record(
            kind,
            response.usage,
            digest,
            text,
            auto=auto,
            entity_key=entity_key,
            incident_id=incident_id,
        )
        if auto and entity_key:
            self._auto_last[entity_key] = time.time()
        return text

    async def stream(
        self,
        kind: str,
        system_blocks: list[str],
        messages: list[Any],
        *,
        max_tokens: int = 2000,
    ) -> AsyncIterator[str]:
        """Streaming completion for chat. Yields text deltas."""
        await self._check_budget(auto=False)
        client = self._sdk()
        digest = _digest(system_blocks, str(messages[-1]["content"]) if messages else "")

        async with client.messages.stream(
            model=self._config.model,
            max_tokens=max_tokens,
            system=_cacheable_system(system_blocks),
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
        full_text = "".join(block.text for block in final.content if block.type == "text")
        await self._record(kind, final.usage, digest, full_text, auto=False)

    # ── ledger ───────────────────────────────────────────────────────

    async def _record(
        self,
        kind: str,
        usage,
        digest: str,
        response_text: str,
        *,
        auto: bool,
        entity_key: str | None = None,
        incident_id: int | None = None,
    ) -> None:
        prices = PRICING.get(self._config.model, FALLBACK_PRICING)
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = (
            input_tokens * prices["input"]
            + output_tokens * prices["output"]
            + cache_read * prices["cache_read"]
            + cache_write * prices["cache_write"]
        ) / 1_000_000
        await self._db.execute(
            """
            INSERT INTO ai_analyses (ts, kind, incident_id, entity_key, model, input_tokens,
                                     output_tokens, cache_read_tokens, cost_usd, prompt_digest,
                                     response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                kind,
                incident_id,
                entity_key,
                self._config.model,
                input_tokens,
                output_tokens,
                cache_read,
                cost,
                digest,
                response_text,
            ),
        )
        await self._db.execute(
            """
            INSERT INTO ai_spend (day, cost_usd, auto_cost_usd, calls) VALUES (?, ?, ?, 1)
            ON CONFLICT (day) DO UPDATE SET
                cost_usd = cost_usd + excluded.cost_usd,
                auto_cost_usd = auto_cost_usd + excluded.auto_cost_usd,
                calls = calls + 1
            """,
            (_today(), cost, cost if auto else 0.0),
        )
        log.info("ai %s: %.4f USD (%d in / %d out)", kind, cost, input_tokens, output_tokens)

    async def spend_today(self) -> dict:
        row = await self._db.fetch_one("SELECT * FROM ai_spend WHERE day = ?", (_today(),))
        return {
            "day": _today(),
            "cost_usd": row["cost_usd"] if row else 0.0,
            "auto_cost_usd": row["auto_cost_usd"] if row else 0.0,
            "calls": row["calls"] if row else 0,
            "budget_usd": self._config.daily_budget_usd,
        }


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _digest(system_blocks: list[str], user_content: str) -> str:
    hasher = hashlib.sha256()
    for block in system_blocks:
        hasher.update(block.encode())
    hasher.update(user_content.encode())
    return hasher.hexdigest()


def _cacheable_system(blocks: list[str]) -> Any:
    """Stable blocks first; cache breakpoint on the last one so the shared
    prefix (role + fleet architecture + inventory) is reused across calls."""
    system: list[dict[str, Any]] = [{"type": "text", "text": text} for text in blocks]
    if system:
        system[-1]["cache_control"] = {"type": "ephemeral"}
    return system
