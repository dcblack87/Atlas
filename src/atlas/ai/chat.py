"""Chat grounded in live data.

Each question re-matches entities and injects fresh scoped context as part
of the user turn; the system prefix (role + inventory) stays byte-stable so
prompt caching makes follow-ups cheap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from atlas.ai.client import AIClient
from atlas.ai.context import ContextBuilder, freshness_note

MAX_TURNS = 12


class ChatSession:
    def __init__(self, client: AIClient, context: ContextBuilder) -> None:
        self._client = client
        self._context = context
        self._history: list[dict] = []

    async def ask(self, question: str) -> AsyncIterator[str]:
        entities = await self._context.match_entities(question)
        detail = await self._context.entity_block(entities)
        user_content = f"{detail}\n\n{freshness_note()}\n\nQuestion: {question}"
        self._history.append({"role": "user", "content": user_content})
        self._history = self._history[-MAX_TURNS:]

        chunks: list[str] = []
        async for delta in self._client.stream(
            "chat", await self._context.system_blocks(), list(self._history)
        ):
            chunks.append(delta)
            yield delta
        self._history.append({"role": "assistant", "content": "".join(chunks)})

    def reset(self) -> None:
        self._history.clear()
