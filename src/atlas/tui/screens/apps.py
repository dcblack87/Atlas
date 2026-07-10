"""Apps — per-app drill-down: the screen behind menu key 3.

Everything Atlas knows about one application in one place: health, git
state vs origin, containers, sites, backups, and its deploy history.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, ListItem, ListView, Static

from atlas.model import DisplayProfile
from atlas.tui.widgets.tiles import GLYPH_CRIT, GLYPH_OK

if TYPE_CHECKING:
    from atlas.app import AtlasApp


class AppsScreen(Screen):
    DEFAULT_CSS = """
    AppsScreen #picker { width: 26; border: round $primary 60%; }
    AppsScreen #detail-scroll { border: round $primary 60%; }
    AppsScreen #detail { padding: 0 1; }
    """

    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("c", "copy_detail", "Copy"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="picker")
            with VerticalScroll(id="detail-scroll"):
                yield Static("select an app", id="detail")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    async def on_mount(self) -> None:
        picker = self.query_one("#picker", ListView)
        rt = self.atlas.runtime
        names: list[str] = []
        if self.atlas.config:
            names = list(self.atlas.config.apps)
        elif rt is not None:  # demo mode: no config, read inventory
            names = [a["key"].removeprefix("app:") for a in await rt.inventory.entities(kind="app")]
        for name in names:
            picker.append(ListItem(Static(name), name=name))
        self._start_clock(self.atlas.profile)

    def on_profile_changed(self, profile: DisplayProfile) -> None:
        self._start_clock(profile)

    def _start_clock(self, profile: DisplayProfile) -> None:
        if self._timer is not None:
            self._timer.stop()
        self.refresh_data()
        self._timer = self.set_interval(max(profile.refresh_period, 5), self.refresh_data)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="apps-refresh")

    def _selected_app(self) -> str | None:
        item = self.query_one("#picker", ListView).highlighted_child
        return item.name if item else None

    async def _refresh(self) -> None:
        rt = self.atlas.runtime
        app_name = self._selected_app()
        if rt is None or app_name is None:
            return
        entity = f"app:{app_name}"
        facts = await rt.inventory.facts_for(entity)
        snap = await rt.metrics.latest_snapshot(entity)
        apps = {a["key"]: a for a in await rt.inventory.entities(kind="app")}
        host = (apps.get(entity, {}).get("parent") or "").removeprefix("host:")

        lines = [f"[b]{app_name}[/b]   host {host or '?'}"]

        # health
        up = snap.get("http.up")
        ms = snap.get("http.response_ms")
        if up is not None:
            glyph = GLYPH_OK if up > 0 else GLYPH_CRIT
            latency = f"{ms:.0f}ms" if ms is not None else ""
            lines.append(f"{glyph} health: {'up' if up > 0 else 'DOWN'} {latency}")
        health = facts.get("health")
        if isinstance(health, dict):
            summary = ", ".join(f"{k}={v}" for k, v in list(health.items())[:6])
            lines.append(f"  endpoint: {summary}")

        # git / drift
        sha = str(facts.get("git.sha", ""))[:7]
        branch = facts.get("git.branch", "?")
        lines.append(f"git: {sha or '?'} on {branch}")
        behind = facts.get("drift.commits_behind")
        if isinstance(behind, int | float):
            drift = (
                "in sync with origin/main" if behind == 0 else f"▲ {behind:.0f} commits behind main"
            )
            lines.append(f"  {drift}")
        ci = facts.get("github.ci_status")
        prs = facts.get("github.open_prs")
        if ci or prs is not None:
            lines.append(f"  ci: {ci or '?'}   open PRs: {prs if prs is not None else '?'}")

        # backups
        backup_age = facts.get("backup.age_hours")
        if isinstance(backup_age, int | float):
            glyph = GLYPH_OK if backup_age < 30 else GLYPH_CRIT
            lines.append(f"{glyph} newest backup: {backup_age:.1f}h ago")

        # sites (multi-tenant)
        sites = await rt.inventory.entities(kind="site", parent=entity)
        if sites:
            lines.append(f"\nSITES ({len(sites)})")
            for site in sites:
                site_snap = await rt.metrics.latest_snapshot(site["key"])
                site_up = site_snap.get("http.up")
                glyph = GLYPH_CRIT if site_up is not None and site_up < 1 else GLYPH_OK
                site_ms = site_snap.get("http.response_ms")
                latency = f"{site_ms:>4.0f}ms" if site_ms is not None else "   —"
                port = site["attrs"].get("port", "?")
                lines.append(f"  {glyph} {site['key'].split('/')[-1]:<22} :{port}  {latency}")

            # capacity: how many more sites will this host hold?
            from atlas.engine.capacity import site_capacity

            if host:
                cap = await site_capacity(rt.inventory, rt.metrics, entity, host)
                if cap is not None and cap.known:
                    verb = "more site fits" if cap.additional_sites == 1 else "more sites fit"
                    lines.append(
                        f"\nCAPACITY  room for ~{cap.additional_sites} {verb} "
                        f"({cap.bound_by}-bound)"
                    )
                    lines.append(
                        f"  ~{cap.avg_site_mb:.0f} MB/site, "
                        f"{cap.ram_free_mb / 1024:.1f} GB RAM free"
                    )

        # containers on the app's host that belong to it (name-prefix heuristic
        # covers the compose and single-container conventions)
        if host:
            containers = await rt.inventory.entities(kind="container", parent=f"host:{host}")
            config = self.atlas.config
            app_config = config.apps.get(app_name) if config else None
            prefix = app_config.container if app_config and app_config.container else app_name
            mine = [
                c for c in containers if c["key"].split("/")[-1].startswith((app_name, str(prefix)))
            ]
            if mine:
                lines.append(f"\nCONTAINERS ({len(mine)})")
                for container in mine:
                    attrs = container["attrs"]
                    state = attrs.get("state", "?")
                    glyph = GLYPH_OK if state == "running" else GLYPH_CRIT
                    health_note = attrs.get("health") or ""
                    lines.append(
                        f"  {glyph} {container['key'].split('/')[-1]:<28} {state} {health_note}"
                    )

        # deploy history
        deploys = await rt.db.fetch_all(
            "SELECT * FROM deployments WHERE app = ? ORDER BY started_at DESC LIMIT 5",
            (app_name,),
        )
        if deploys:
            lines.append("\nRECENT DEPLOYS")
            for d in deploys:
                when = datetime.fromtimestamp(d["started_at"]).strftime("%m-%d %H:%M")
                lines.append(
                    f"  {when}  {(d['git_sha_before'] or '?')[:7]} → "
                    f"{(d['git_sha_after'] or '?')[:7]}  verify={d['verify_status'] or '…'}"
                )
        else:
            lines.append("\nno deploys through Atlas yet — press 4 to run one")

        text = "\n".join(lines)
        widget = self.query_one("#detail", Static)
        if getattr(widget, "_atlas_last", None) != text:
            widget._atlas_last = text  # type: ignore[attr-defined]
            widget.update(text)

    def action_copy_detail(self) -> None:
        from atlas.tui.clipboard import copy_text

        body = getattr(self.query_one("#detail", Static), "_atlas_last", "") or ""
        copy_text(self, body.replace("[b]", "").replace("[/b]", ""), "app detail")
