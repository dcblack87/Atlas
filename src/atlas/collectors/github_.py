"""GitHub: CI status, open PRs, and deployment drift.

Read-only API calls from wherever Atlas runs (not via the host transport).
Drift = deployed sha (discovery fact) vs origin/main — lets the AI answer
"why is production different from main?".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Observation
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

log = logging.getLogger(__name__)

API = "https://api.github.com"


@register
class GithubCollector(Collector):
    name = "github"
    interval = 1800

    def applies_to(self, host: HostConfig) -> bool:
        return bool(host.apps)

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        obs = Observation()
        github = ctx.github
        if github is None or not github.enabled:
            return obs
        token = github.resolve_token()
        if not token:
            return obs
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            for app_name in host.apps:
                app = ctx.apps.get(app_name)
                if app is None or not app.github_repo:
                    continue
                entity = f"app:{app_name}"
                deployed_sha = await ctx.fact(entity, "git.sha")
                await self._collect_repo(
                    client, app.github_repo, entity, str(deployed_sha or ""), obs
                )
        return obs

    async def _collect_repo(
        self,
        client: httpx.AsyncClient,
        repo: str,
        entity: str,
        deployed_sha: str,
        obs: Observation,
    ) -> None:
        try:
            branch = await _get(client, f"{API}/repos/{repo}/branches/HEAD")
            if branch is None:
                branch = await _get(client, f"{API}/repos/{repo}/branches/main")
            main_sha = branch["commit"]["sha"] if branch else None
            if main_sha:
                obs.facts[(entity, "github.main_sha")] = main_sha

            if deployed_sha and main_sha and deployed_sha != main_sha:
                compare = await _get(
                    client, f"{API}/repos/{repo}/compare/{deployed_sha}...{main_sha}"
                )
                if compare is not None:
                    obs.facts[(entity, "drift.commits_behind")] = compare.get("ahead_by", 0)
            elif deployed_sha and main_sha:
                obs.facts[(entity, "drift.commits_behind")] = 0

            runs = await _get(client, f"{API}/repos/{repo}/actions/runs?per_page=1&branch=main")
            if runs and runs.get("workflow_runs"):
                run = runs["workflow_runs"][0]
                obs.facts[(entity, "github.ci_status")] = (
                    run.get("conclusion") or run.get("status") or "unknown"
                )

            pulls = await _get(client, f"{API}/repos/{repo}/pulls?state=open&per_page=30")
            if pulls is not None:
                obs.facts[(entity, "github.open_prs")] = len(pulls)
        except httpx.HTTPError as e:
            log.warning("github collect failed for %s: %s", repo, e)


async def _get(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    if response.status_code != 200:
        return None
    return response.json()
