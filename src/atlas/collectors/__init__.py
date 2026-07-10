"""Collector registry — importing this package registers every collector."""

from atlas.collectors import (  # noqa: F401
    backups,
    certs,
    cron,
    discovery,
    docker_,
    github_,
    http_health,
    nginx,
    postgres,
    security,
    system,
)
from atlas.collectors.base import REGISTRY, Collector, register

__all__ = ["REGISTRY", "Collector", "register"]
