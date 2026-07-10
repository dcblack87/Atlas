"""Cost — what the fleet and its AI actually cost."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Static

from atlas.model import DisplayProfile

if TYPE_CHECKING:
    from atlas.app import AtlasApp


class CostScreen(Screen):
    DEFAULT_CSS = """
    CostScreen #ai, CostScreen #infra { border: round $primary 60%; padding: 0 1; height: auto; }
    """

    BINDINGS: ClassVar = [("escape", "app.pop_screen", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("collecting…", id="infra")
            yield Static("", id="ai")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self._start_clock(self.atlas.profile)

    def on_profile_changed(self, profile: DisplayProfile) -> None:
        self._start_clock(profile)

    def _start_clock(self, profile: DisplayProfile) -> None:
        if self._timer is not None:
            self._timer.stop()
        self.refresh_data()
        self._timer = self.set_interval(max(profile.refresh_period, 30), self.refresh_data)

    def refresh_data(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="cost-refresh")

    async def _refresh(self) -> None:
        rt = self.atlas.runtime
        if rt is None:
            return

        infra_lines = ["INFRASTRUCTURE (monthly estimate)"]
        rows = await rt.db.fetch_all(
            "SELECT entity_key, value FROM facts WHERE name = 'cost.monthly_eur'"
        )
        if rows:
            total = 0.0
            for row in rows:
                value = float(row["value"])
                total += value
                infra_lines.append(f"  {row['entity_key'].removeprefix('host:'):<20} €{value:.2f}")
            infra_lines.append(f"  {'total':<20} €{total:.2f}")
        else:
            infra_lines.append("  hcloud tokens not configured — see [hcloud] in atlas.toml")
        self._update("infra", "\n".join(infra_lines))

        ai_lines = ["CLAUDE SPEND"]
        if rt.ai is not None:
            spend = await rt.ai.spend_today()
            ai_lines.append(
                f"  today      ${spend['cost_usd']:.3f} of ${spend['budget_usd']:.2f} budget"
                f"   ({spend['calls']} calls, ${spend['auto_cost_usd']:.3f} auto)"
            )
            history = await rt.db.fetch_all("SELECT * FROM ai_spend ORDER BY day DESC LIMIT 7")
            for row in history:
                ai_lines.append(f"  {row['day']}  ${row['cost_usd']:.3f} ({row['calls']} calls)")
        else:
            ai_lines.append("  AI not configured")
        self._update("ai", "\n".join(ai_lines))

    def _update(self, widget_id: str, text: str) -> None:
        widget = self.query_one(f"#{widget_id}", Static)
        if getattr(widget, "_atlas_last", None) != text:
            widget._atlas_last = text  # type: ignore[attr-defined]
            widget.update(text)
