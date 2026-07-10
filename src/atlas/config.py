"""Configuration loading and validation.

Real inventory lives in a gitignored ``atlas.toml`` (see ``atlas.example.toml``
for the documented template). Secrets may come from the environment instead of
the file; the environment always wins.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

LOCAL_ADDRESS = "local"


class ConfigError(Exception):
    """Raised for a missing or invalid config file, with a human-friendly message."""


class AtlasSection(BaseModel):
    db_path: Path = Path("atlas.db")
    display_profile: Literal["standard", "eink", "glance"] = "standard"


class SSHSection(BaseModel):
    key_file: Path = Path("~/.ssh/atlas_ed25519")
    user: str = "root"
    connect_timeout: float = 10
    keepalive: int = 30

    @field_validator("key_file")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()


class HostConfig(BaseModel):
    name: str
    address: str  # "local" or an SSH-reachable address (Tailscale IP)
    apps: list[str] = Field(default_factory=list)

    @property
    def is_local(self) -> bool:
        return self.address == LOCAL_ADDRESS


AppKind = Literal["compose", "single-container", "multi-site"]


class AppConfig(BaseModel):
    kind: AppKind
    path: str
    deploy_command: str = "./scripts/deploy.sh update"
    health_url: str | None = None  # JSON health endpoint, if the app has one
    liveness_url: str | None = None  # plain URL; 2xx/3xx means alive
    container: str | None = None  # single-container: the container name
    sites_dir: str | None = None  # multi-site: where sites/<name>/.port live
    container_prefix: str | None = None  # multi-site: e.g. "sitefarm-"
    github_repo: str | None = None  # "owner/repo" for CI status + drift

    @model_validator(mode="after")
    def _kind_requirements(self) -> AppConfig:
        if self.kind == "multi-site" and not self.sites_dir:
            raise ValueError("multi-site apps need `sites_dir`")
        if self.kind == "single-container" and not self.container:
            raise ValueError("single-container apps need `container`")
        return self


class AISection(BaseModel):
    enabled: bool = True
    model: str = "claude-opus-4-8"
    api_key: str | None = None  # $ANTHROPIC_API_KEY wins over this
    daily_budget_usd: float = 2.00
    auto_insight_budget_usd: float = 0.75
    max_auto_insights_per_day: int = 10

    def resolve_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY") or self.api_key


class TelegramSection(BaseModel):
    enabled: bool = False
    bot_token: str | None = None
    chat_id: str | None = None

    def resolve_token(self) -> str | None:
        return os.environ.get("ATLAS_TELEGRAM_TOKEN") or self.bot_token


class HcloudSection(BaseModel):
    enabled: bool = False
    tokens: dict[str, str] = Field(default_factory=dict)


class GithubSection(BaseModel):
    enabled: bool = False
    token: str | None = None

    def resolve_token(self) -> str | None:
        return os.environ.get("ATLAS_GITHUB_TOKEN") or self.token


class DeploySection(BaseModel):
    enabled: bool = True
    timeout_seconds: float = 900
    remediations: list[str] = Field(default_factory=list)


class Config(BaseModel):
    atlas: AtlasSection = Field(default_factory=AtlasSection)
    ssh: SSHSection = Field(default_factory=SSHSection)
    hosts: list[HostConfig] = Field(default_factory=list)
    apps: dict[str, AppConfig] = Field(default_factory=dict)
    ai: AISection = Field(default_factory=AISection)
    telegram: TelegramSection = Field(default_factory=TelegramSection)
    hcloud: HcloudSection = Field(default_factory=HcloudSection)
    github: GithubSection = Field(default_factory=GithubSection)
    deploy: DeploySection = Field(default_factory=DeploySection)

    @model_validator(mode="after")
    def _cross_check(self) -> Config:
        names = {h.name for h in self.hosts}
        if len(names) != len(self.hosts):
            raise ValueError("duplicate host names")
        for host in self.hosts:
            for app in host.apps:
                if app not in self.apps:
                    raise ValueError(f"host {host.name!r} references unknown app {app!r}")
        return self

    def host_for_app(self, app_name: str) -> HostConfig | None:
        for host in self.hosts:
            if app_name in host.apps:
                return host
        return None


def config_path() -> Path:
    return Path(os.environ.get("ATLAS_CONFIG", "atlas.toml"))


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        raise ConfigError(
            f"No config found at {path}.\n"
            f"Copy atlas.example.toml to atlas.toml and edit it, "
            f"or point $ATLAS_CONFIG at your config.\n"
            f"To explore without any setup: atlas run --demo"
        )
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from e
    try:
        return Config.model_validate(data)
    except ValueError as e:
        raise ConfigError(f"{path} is invalid: {e}") from e
