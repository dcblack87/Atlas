"""Collector registry — importing this package registers every collector."""

from atlas.collectors import discovery, docker_, http_health, system  # noqa: F401
from atlas.collectors.base import REGISTRY, Collector, register

__all__ = ["REGISTRY", "Collector", "register"]
