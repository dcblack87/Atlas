"""Context assembly and bundle scrubbing against the demo fleet."""

from pathlib import Path

from atlas.ai.bundles import scrub, write_bundle
from atlas.ai.context import ContextBuilder
from atlas.demo.dataset import seed_demo
from atlas.store.db import Database


async def _demo_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "demo.db")
    await db.open()
    await seed_demo(db)
    return db


async def test_entity_matching(tmp_path: Path) -> None:
    db = await _demo_db(tmp_path)
    context = ContextBuilder(db)
    matched = await context.match_entities("why is shopfront slow today?")
    assert any("shopfront" in key for key in matched)
    assert await context.match_entities("what colour is the sky") == []
    await db.close()


async def test_inventory_block_lists_fleet(tmp_path: Path) -> None:
    db = await _demo_db(tmp_path)
    block = await ContextBuilder(db).inventory_block()
    assert "web-1" in block
    assert "sitefarm" in block
    assert "acmedetailing" in block
    await db.close()


async def test_entity_block_contains_incident_and_metrics(tmp_path: Path) -> None:
    db = await _demo_db(tmp_path)
    context = ContextBuilder(db)
    block = await context.entity_block([])
    assert "OPEN INCIDENTS" in block
    assert "expires in 9 days" in block
    assert "METRICS host:web-1" in block
    await db.close()


def test_scrub_redacts_secrets() -> None:
    text = (
        "DATABASE_URL=postgres://u:hunter2@host/db\n"
        "api_key = sk-ant-abcdefghijklmnopqrstuvwx\n"
        "normal line stays"
    )
    scrubbed = scrub(text)
    assert "hunter2" not in scrubbed
    assert "sk-ant" not in scrubbed
    assert "normal line stays" in scrubbed


async def test_bundle_writes_markdown(tmp_path: Path, monkeypatch) -> None:
    db = await _demo_db(tmp_path)
    monkeypatch.setattr("atlas.ai.bundles.BUNDLES_DIR", tmp_path / "bundles")
    path = await write_bundle(ContextBuilder(db))
    body = path.read_text()
    assert "Atlas context bundle" in body
    assert "web-1" in body
    await db.close()
