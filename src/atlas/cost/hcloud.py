"""Hetzner Cloud costs — read-only, straight from the API.

One token per project; Atlas only ever GETs. Server prices come from the
server response itself (price_monthly for its location), so estimates match
the console. Costs land as facts on matching host entities plus a per-project
total.
"""

from __future__ import annotations

import logging

import httpx

from atlas.config import HcloudSection
from atlas.store.db import Database
from atlas.store.inventory import Inventory

log = logging.getLogger(__name__)

API = "https://api.hetzner.cloud/v1"


async def collect_costs(config: HcloudSection, db: Database) -> None:
    if not config.enabled or not config.tokens:
        return
    inventory = Inventory(db)
    known_hosts = {e["key"].removeprefix("host:") for e in await inventory.entities(kind="host")}

    for project, token in config.tokens.items():
        try:
            servers = await _servers(token)
        except httpx.HTTPError as e:
            log.warning("hcloud fetch failed for project %s: %s", project, e)
            continue
        total = 0.0
        for server in servers:
            monthly = _monthly_price(server)
            total += monthly
            name = server.get("name", "")
            if name in known_hosts:
                await inventory.set_fact(f"host:{name}", "cost.monthly_eur", round(monthly, 2))
                await inventory.set_fact(
                    f"host:{name}",
                    "hcloud.server_type",
                    server.get("server_type", {}).get("name"),
                )
        # project totals live as facts on a virtual key; no entity needed
        await inventory.set_fact(f"project:{project}", "cost.monthly_eur", round(total, 2))


async def _servers(token: str) -> list[dict]:
    async with httpx.AsyncClient(
        timeout=20, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        response = await client.get(f"{API}/servers")
        response.raise_for_status()
        return response.json().get("servers", [])


def _monthly_price(server: dict) -> float:
    location = server.get("datacenter", {}).get("location", {}).get("name")
    for price in server.get("server_type", {}).get("prices", []):
        if price.get("location") == location:
            try:
                return float(price["price_monthly"]["gross"])
            except (KeyError, TypeError, ValueError):
                continue
    return 0.0
