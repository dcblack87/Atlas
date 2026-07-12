"""Cron: fleet-wide job inventory with passive failure detection.

Captures the root crontab, ``/etc/cron.d`` entries, and (when configured)
celery-beat run metadata, then judges staleness from the journal's CRON
lines. Absence of an expected cron is a silent failure mode — inventory
makes it visible; the journal makes "installed but not firing" visible too.

Everything here is read-only and passive: no wrapper scripts, no crontab
rewriting. What we can't observe stays "unknown" rather than guessed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Entity, EntityKind, Finding, Observation, Sample, Severity
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

# Distro-managed cron.d files that are noise in an app-fleet view.
STOCK_CROND = {
    "certbot",
    "e2scrub_all",
    "sysstat",
    ".placeholder",
    "anacron",
    "popularity-contest",
    "mdadm",
    "php",
}

# Consecutive failing observations before a cron_failed finding escalates.
FAIL_STREAK_CRITICAL = 3

# How far back we ask the journal for CRON activity. Generous enough for a
# daily job plus slack; weekly/monthly jobs rely on remembered state instead.
JOURNAL_WINDOW = "-26h"

_SPECIALS = {
    "@hourly": 3600.0,
    "@daily": 86400.0,
    "@midnight": 86400.0,
    "@weekly": 604800.0,
    "@monthly": 2678400.0,
    "@yearly": 31536000.0,
    "@annually": 31536000.0,
}

_ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FIELD = re.compile(r"^[A-Za-z0-9*,/-]+$")
_JOURNAL_CMD = re.compile(r"^(\d+(?:\.\d+)?)\s+\S+\s+CRON\[\d+\]:\s+\((\S+)\)\s+CMD\s+\((.*)\)\s*$")
_API_CRON_PATH = re.compile(r"/api/v1/cron/([A-Za-z0-9_-]+)")
_LOG_REDIRECT = re.compile(r">>\s*([^\s;&]+)")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_SECTION = "===ATLAS-SECTION==="


@dataclass(slots=True)
class CronJob:
    name: str
    slug: str
    schedule: str
    command: str
    source: str  # "crontab" | cron.d basename | "celery"
    user: str


@register
class CronCollector(Collector):
    name = "cron"
    interval = 900
    owns_kinds = ("cron",)

    def __init__(self) -> None:
        # Journal only covers a window; remember the newest run we ever saw
        # so staleness keeps growing while a job stays dark. (Per-host state:
        # the scheduler builds one collector instance per host loop.)
        self._last_runs: dict[str, float] = {}
        self._fail_streaks: dict[str, int] = {}

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        script = (
            "crontab -l 2>/dev/null"
            f"; echo '{_SECTION}'"
            "; grep -H '' /etc/cron.d/* 2>/dev/null"
            f"; echo '{_SECTION}'"
            f"; journalctl SYSLOG_IDENTIFIER=CRON --since {JOURNAL_WINDOW} "
            "-o short-unix --no-pager 2>/dev/null | grep -F 'CMD (' | tail -400"
        )
        result = await transport.run(["sh", "-c", script], timeout=30)
        crontab_text, crond_text, journal_text = _split_sections(result.stdout, 3)

        jobs = parse_cron_sources(crontab_text, crond_text)
        runs = match_journal_runs(journal_text, jobs)

        # Second pass: tail the logs that backup-ish jobs write to.
        log_paths = _backup_logs(jobs)
        log_status: dict[str, str] = {}
        if log_paths:
            tail_script = f"; echo '{_SECTION}'".join(
                f"tail -30 {path} 2>/dev/null" for path in log_paths
            )
            tails = await transport.run(["sh", "-c", tail_script], timeout=20)
            chunks = _split_sections(tails.stdout, len(log_paths))
            for path, chunk in zip(log_paths, chunks, strict=False):
                status = parse_backup_log_status(chunk)
                if status is not None:
                    log_status[path] = status

        # Celery-beat tasks, for apps that expose run metadata in redis.
        celery_jobs: list[tuple[CronJob, float | None, str | None]] = []
        for app_name in host.apps:
            app = ctx.apps.get(app_name)
            if app is None or not app.celery_redis_container:
                continue
            cmd = (
                f"docker compose --project-directory {app.path} exec -T "
                f"{app.celery_redis_container} sh -c '"
                'redis-cli --scan --pattern "taskengine:last_run:*" | while read k; do '
                'printf "%s\\t" "$k"; redis-cli GET "$k"; done\' 2>/dev/null'
            )
            redis_result = await transport.run(["sh", "-c", cmd], timeout=25)
            celery_jobs.extend(parse_celery_runs(redis_result.stdout))

        return self._build_observation(host.name, jobs, runs, log_status, celery_jobs)

    def _build_observation(
        self,
        host_name: str,
        jobs: list[CronJob],
        runs: dict[str, float],
        log_status: dict[str, str],
        celery_jobs: list[tuple[CronJob, float | None, str | None]],
    ) -> Observation:
        obs = Observation()
        host_entity = f"host:{host_name}"
        now = time.time()

        all_jobs: list[tuple[CronJob, float | None, str | None]] = [
            (job, runs.get(job.slug), None) for job in jobs
        ]
        all_jobs.extend(celery_jobs)

        for job, last_run, error in all_jobs:
            entity = f"cron:{host_name}/{job.slug}"
            obs.entities.append(
                Entity(
                    EntityKind.CRON,
                    entity,
                    parent=host_entity,
                    attrs={"name": job.name, "schedule": job.schedule, "source": job.source},
                )
            )
            obs.facts[(entity, "cron.schedule")] = job.schedule
            obs.facts[(entity, "cron.command")] = job.command[:200]
            obs.facts[(entity, "cron.source")] = job.source
            obs.facts[(entity, "cron.user")] = job.user

            if last_run is not None:
                previous = self._last_runs.get(entity)
                if previous is None or last_run > previous:
                    self._last_runs[entity] = last_run
            remembered = self._last_runs.get(entity)
            if remembered is not None:
                obs.facts[(entity, "cron.last_run_ts")] = int(remembered)

            interval = 86400.0 if job.source == "celery" else expected_interval_s(job.schedule)
            if interval is not None:
                obs.facts[(entity, "cron.expected_interval_s")] = interval
                if remembered is not None:
                    ratio = round((now - remembered) / interval, 2)
                    obs.facts[(entity, "cron.overdue_ratio")] = ratio

            status: str | None = None
            if error:
                status = "failed"
                obs.facts[(entity, "cron.last_error")] = error[:300]
            else:
                for path, log_result in log_status.items():
                    if path in job.command:
                        status = log_result
                        break
            if status is not None:
                obs.facts[(entity, "cron.last_status")] = status

            if status == "failed":
                streak = self._fail_streaks.get(entity, 0) + 1
                self._fail_streaks[entity] = streak
                severity = Severity.CRITICAL if streak >= FAIL_STREAK_CRITICAL else Severity.WARNING
                detail = {"source": job.source, "streak": streak}
                if error:
                    detail["error"] = error[:300]
                obs.findings.append(
                    Finding(
                        "cron_failed",
                        entity,
                        severity,
                        f"cron job {job.name} on {host_name} is failing",
                        detail=detail,
                    )
                )
            else:
                self._fail_streaks.pop(entity, None)

        obs.samples.append(Sample("cron.entries", float(len(all_jobs)), host_entity))
        obs.facts[(host_entity, "cron.entries")] = [
            f"{job.schedule}  {job.command}"[:160] for job, _, _ in all_jobs[:50]
        ]
        return obs


# ── parsing (pure, fixture-tested) ──────────────────────────────────────


def _backup_logs(jobs: list[CronJob], cap: int = 3) -> list[str]:
    """Log files written by backup-ish jobs — the ones worth tailing."""
    paths: list[str] = []
    for job in jobs:
        if "backup" not in job.command.lower():
            continue
        match = _LOG_REDIRECT.search(job.command)
        if match and match.group(1) not in paths:
            paths.append(match.group(1))
        if len(paths) >= cap:
            break
    return paths


def _split_sections(stdout: str, expected: int) -> list[str]:
    parts = stdout.split(_SECTION)
    while len(parts) < expected:
        parts.append("")
    return [p.strip("\n") for p in parts[:expected]]


def parse_cron_sources(crontab_text: str, crond_text: str) -> list[CronJob]:
    jobs = _parse_lines(crontab_text.splitlines(), source="crontab", has_user=False)
    by_file: dict[str, list[str]] = {}
    for raw in crond_text.splitlines():
        path, sep, rest = raw.partition(":")
        if not sep:
            continue
        basename = path.rsplit("/", 1)[-1]
        if basename in STOCK_CROND:
            continue
        by_file.setdefault(basename, []).append(rest)
    for basename, lines in by_file.items():
        jobs.extend(_parse_lines(lines, source=basename, has_user=True))
    seen: dict[str, int] = {}
    for job in jobs:
        if job.slug in seen:
            seen[job.slug] += 1
            job.slug = f"{job.slug}-{seen[job.slug]}"
        else:
            seen[job.slug] = 1
    return jobs


def _parse_lines(lines: list[str], *, source: str, has_user: bool) -> list[CronJob]:
    jobs: list[CronJob] = []
    pending_comment: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            pending_comment = None
            continue
        if line.startswith("#"):
            pending_comment = line
            continue
        if _ENV_LINE.match(line):
            pending_comment = None
            continue
        parsed = _parse_job_line(line, has_user=has_user)
        if parsed is None:
            pending_comment = None
            continue
        schedule, user, command = parsed
        name = _name_from_comment(pending_comment) or _fallback_name(command)
        pending_comment = None
        jobs.append(
            CronJob(
                name=name,
                slug=_slugify(name),
                schedule=schedule,
                command=command,
                source=source,
                user=user,
            )
        )
    return jobs


def _parse_job_line(line: str, *, has_user: bool) -> tuple[str, str, str] | None:
    """Return (schedule, user, command) or None if this isn't a job line."""
    if line.startswith("@"):
        parts = line.split(None, 2 if has_user else 1)
        if len(parts) < (3 if has_user else 2) or (
            parts[0] != "@reboot" and parts[0] not in _SPECIALS
        ):
            return None
        if has_user:
            return parts[0], parts[1], parts[2]
        return parts[0], "root", parts[1]
    fields = line.split(None, 6 if has_user else 5)
    needed = 7 if has_user else 6
    if len(fields) < needed:
        return None
    schedule_fields = fields[:5]
    if not all(_FIELD.match(f) for f in schedule_fields):
        return None
    schedule = " ".join(schedule_fields)
    if has_user:
        return schedule, fields[5], fields[6]
    return schedule, "root", fields[5]


def _name_from_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    text = comment.lstrip("#").strip()
    # Drop trailing inline markers ("... # bookingmachine-cron").
    text = text.split(" # ")[0].strip()
    # "Recurring Bookings — daily at 6:00 AM UTC" -> "Recurring Bookings"
    text = text.split(" — ")[0].strip()
    # Section headers / dividers never name a job.
    if not text or set(text) <= set("-=─━ ") or text.endswith(":") or "──" in text:
        return None
    return text


def _fallback_name(command: str) -> str:
    match = _API_CRON_PATH.search(command)
    if match:
        return match.group(1).replace("-", " ")
    for token in command.split():
        if token in {"bash", "sh", "curl", "cd", "for", "flock", "nice", "timeout"}:
            continue
        if token.startswith("-"):
            continue
        return token.rsplit("/", 1)[-1]
    return command[:32]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "job"


def expected_interval_s(schedule: str) -> float | None:
    """Coarse upper bound on seconds between fires. None = exempt/unknown.

    Deliberately bucketed (minutely/hourly/daily/weekly/monthly): the
    staleness rule only needs an order of magnitude, and coarse errors
    only ever delay an alert, never invent one.
    """
    s = schedule.strip()
    if s.startswith("@"):
        return _SPECIALS.get(s)  # @reboot -> None
    fields = s.split()
    if len(fields) != 5:
        return None
    minute, hour, dom, _mon, dow = fields
    if hour == "*":
        slots = _slots_per_span(minute, 60)
        if slots is None:
            return 3600.0
        return max(60.0, 3600.0 / slots)
    if dow != "*":
        return 604800.0
    if dom != "*":
        return 2678400.0
    slots = _slots_per_span(hour, 24)
    if slots is None:
        return 86400.0
    return max(3600.0, 86400.0 / slots)


def _slots_per_span(field: str, span: int) -> float | None:
    if field == "*":
        return float(span)
    total = 0.0
    for part in field.split(","):
        if part.startswith("*/"):
            try:
                total += max(1, span // int(part[2:]))
            except ValueError:
                return None
        elif "-" in part:
            bounds = part.split("/")[0].split("-")
            try:
                total += int(bounds[1]) - int(bounds[0]) + 1
            except (ValueError, IndexError):
                return None
        else:
            total += 1
    return total or None


def _normalize_command(command: str) -> str:
    return " ".join(command.split())


def match_journal_runs(journal_text: str, jobs: list[CronJob]) -> dict[str, float]:
    """Map job slug -> newest run timestamp seen in the journal window.

    The journal may truncate long CMD lines, so a logged command that is a
    prefix of exactly one job's command still counts. Ambiguity (a truncated
    line that could be several jobs) means "unknown", never a wrong match.
    """
    normalized = [(job, _normalize_command(job.command)) for job in jobs]
    newest: dict[str, float] = {}
    for line in journal_text.splitlines():
        match = _JOURNAL_CMD.match(line.strip())
        if match is None:
            continue
        ts = float(match.group(1))
        logged = _normalize_command(match.group(3))
        if len(logged) < 12:  # too short to be a trustworthy match
            continue
        candidates = [
            job
            for job, norm in normalized
            if norm == logged or norm.startswith(logged) or logged.startswith(norm)
        ]
        if len(candidates) != 1:
            continue
        slug = candidates[0].slug
        if ts > newest.get(slug, 0):
            newest[slug] = ts
    return newest


def parse_celery_runs(stdout: str) -> list[tuple[CronJob, float | None, str | None]]:
    """Parse `key\\t{json}` lines from taskengine:last_run:* redis keys."""
    out: list[tuple[CronJob, float | None, str | None]] = []
    for line in stdout.splitlines():
        key, sep, payload = line.partition("\t")
        if not sep or "taskengine:last_run:" not in key:
            continue
        task_id = key.strip().rsplit(":", 1)[-1]
        if not task_id:
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        ts = data.get("timestamp")
        error = data.get("error")
        job = CronJob(
            name=task_id.replace("_", " ").replace("-", " "),
            slug=_slugify(f"celery-{task_id}"),
            schedule="celery",
            command=task_id,
            source="celery",
            user="celery",
        )
        out.append(
            (
                job,
                float(ts) if isinstance(ts, int | float) else None,
                str(error) if error else None,
            )
        )
    return out


def parse_backup_log_status(text: str) -> str | None:
    """Judge the tail of a backup log: "ok", "failed", or None (no signal)."""
    success_at = fail_at = -1
    lines = [_ANSI.sub("", line) for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        lowered = line.lower()
        if "backup complete" in lowered or "backup successful" in lowered:
            success_at = i
        if re.search(r"\b(error|failed|fatal|abort)", lowered):
            fail_at = i
    if fail_at > success_at:
        return "failed"
    if success_at >= 0:
        return "ok"
    return None
