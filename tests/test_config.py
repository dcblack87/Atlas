"""Config loading and validation."""

from pathlib import Path

import pytest

from atlas.config import Config, ConfigError, load_config

REPO_ROOT = Path(__file__).parent.parent


def test_example_config_is_valid() -> None:
    """The committed example must always load — it's the user's template."""
    config = load_config(REPO_ROOT / "atlas.example.toml")
    assert len(config.hosts) == 3
    assert config.hosts[0].is_local
    assert config.ai.daily_budget_usd == pytest.approx(2.0)
    assert config.deploy.remediations  # allowlist is non-empty in the example


def test_missing_config_message_mentions_demo(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--demo"):
        load_config(tmp_path / "nope.toml")


def test_unknown_app_reference_rejected() -> None:
    with pytest.raises(ValueError, match="unknown app"):
        Config.model_validate({"hosts": [{"name": "a", "address": "local", "apps": ["ghost"]}]})


def test_multi_site_requires_sites_dir() -> None:
    with pytest.raises(ValueError, match="sites_dir"):
        Config.model_validate({"apps": {"x": {"kind": "multi-site", "path": "/opt/x"}}})


def test_host_for_app() -> None:
    config = load_config(REPO_ROOT / "atlas.example.toml")
    host = config.host_for_app("shopfront")
    assert host is not None and host.name == "web-2"
    assert config.host_for_app("ghost") is None
