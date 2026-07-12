"""Collector parsers against recorded fixtures — the bulk of test value."""

from pathlib import Path

import pytest

from atlas.collectors.backups import parse_newest_backup
from atlas.collectors.cron import (
    expected_interval_s,
    match_journal_runs,
    parse_backup_log_status,
    parse_celery_runs,
    parse_cron_sources,
)
from atlas.collectors.discovery import parse_sites
from atlas.collectors.docker_ import parse_docker_ps, parse_docker_stats
from atlas.collectors.system import parse_system
from atlas.model import Severity

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestSystemParser:
    def test_ubuntu_cpx(self) -> None:
        obs = parse_system(_fixture("system/ubuntu-cpx.txt"), "web-1")
        metrics = {(s.metric, s.entity): s.value for s in obs.samples}
        assert metrics[("load.1m", "host:web-1")] == pytest.approx(0.42)
        assert metrics[("cpu.load_per_core", "host:web-1")] == pytest.approx(0.14)
        assert metrics[("mem.used_pct", "host:web-1")] == pytest.approx(60.9, abs=0.1)
        assert metrics[("disk.used_pct", "host:web-1")] == pytest.approx(48.0, abs=0.1)
        assert obs.facts[("host:web-1", "cpu.cores")] == 3
        assert obs.facts[("host:web-1", "boot_time")] == "2026-05-14 03:12:44"
        # no swap configured -> no swap sample
        assert ("swap.used_pct", "host:web-1") not in metrics

    def test_garbage_input_is_harmless(self) -> None:
        obs = parse_system("not the output you expected", "web-1")
        assert obs.samples == []


class TestDockerParser:
    def test_compose_stack(self) -> None:
        obs = parse_docker_ps(_fixture("docker/ps-compose.txt"), "web-1")
        keys = {e.key for e in obs.entities}
        assert "container:web-1/exampleapp-backend" in keys
        assert len(keys) == 6

        metrics = {(s.metric, s.entity): s.value for s in obs.samples}
        assert metrics[("container.up", "container:web-1/exampleapp-backend")] == 1.0
        assert metrics[("container.up", "container:web-1/exampleapp-celery-beat")] == 0.0
        assert metrics[("container.restarts", "container:web-1/exampleapp-celery-beat")] == 17
        assert metrics[("docker.running", "host:web-1")] == 5.0

        # the restarting container is a critical finding
        crit = [f for f in obs.findings if f.severity == Severity.CRITICAL]
        assert len(crit) == 1
        assert crit[0].rule_id == "container_restarting"
        assert "celery-beat" in crit[0].title

    def test_stats(self) -> None:
        samples = parse_docker_stats(_fixture("docker/stats.txt"), "web-1")
        by_key = {(s.metric, s.entity): s.value for s in samples}
        assert by_key[("container.cpu_pct", "container:web-1/exampleapp-backend")] == pytest.approx(
            3.42
        )
        mem = by_key[("container.mem_bytes", "container:web-1/exampleapp-backend")]
        assert mem == pytest.approx(412.3 * 2**20, rel=0.01)


class TestBackupParser:
    def test_newest_backup(self) -> None:
        mtime, size = parse_newest_backup("1783917600.123 1857312\n")
        assert mtime == pytest.approx(1783917600.123)
        assert size == pytest.approx(1857312)

    def test_empty_and_garbage(self) -> None:
        assert parse_newest_backup("") is None
        assert parse_newest_backup("find: no such directory") is None


class TestCronParser:
    def _jobs(self):
        return parse_cron_sources(_fixture("cron/quotelab-crontab.txt"), _fixture("cron/crond.txt"))

    def test_crontab_jobs_and_names(self) -> None:
        jobs = {j.slug: j for j in self._jobs() if j.source == "crontab"}
        assert len(jobs) == 6
        assert jobs["database-backups"].schedule == "0 3 * * *"
        assert jobs["recurring-bookings"].name == "Recurring Bookings"
        assert jobs["admin-weekly-digest"].schedule == "30 7 * * 1"
        # the trigger-file sweep gets its comment name, not the for-loop text
        assert "check-for-manual-trigger-files-every-5-minutes" in jobs

    def test_divider_comments_never_bind(self) -> None:
        # "--- enable as each ship lands ---" and the ── header sit between
        # comments/jobs; no job may inherit them as a name
        names = {j.name for j in self._jobs()}
        assert not any("enable as" in n or "──" in n for n in names)

    def test_crond_user_column_and_stock_skip(self) -> None:
        jobs = [j for j in self._jobs() if j.source == "marketing-agent"]
        assert len(jobs) == 2
        tick = next(j for j in jobs if j.slug == "scheduler-tick")
        assert tick.user == "root"
        assert tick.command.startswith("curl")  # user column consumed, not glued to command
        assert tick.schedule == "* * * * *"
        # stock certbot file skipped entirely
        assert not any(j.source == "certbot" for j in self._jobs())

    def test_env_and_garbage_lines_skipped(self) -> None:
        jobs = parse_cron_sources("SHELL=/bin/bash\nnot a cron line\n", "")
        assert jobs == []

    def test_api_path_fallback_name(self) -> None:
        jobs = parse_cron_sources(
            "*/5 * * * * curl -sf http://127.0.0.1:3000/api/v1/cron/blog-publish\n", ""
        )
        assert jobs[0].name == "blog publish"


class TestBackupLogDiscovery:
    def test_backup_jobs_yield_their_logs(self) -> None:
        from atlas.collectors.cron import _backup_logs

        jobs = parse_cron_sources(_fixture("cron/quotelab-crontab.txt"), _fixture("cron/crond.txt"))
        logs = _backup_logs(jobs)
        assert "/var/log/bookingmachine-backup.log" in logs
        assert "/var/log/marketing-agent-backup.log" in logs
        assert len(logs) <= 3  # capped


class TestJournalMatch:
    def test_last_run_and_truncation(self) -> None:
        jobs = parse_cron_sources(_fixture("cron/quotelab-crontab.txt"), _fixture("cron/crond.txt"))
        runs = match_journal_runs(_fixture("cron/journal-cron.txt"), jobs)
        assert runs["database-backups"] == pytest.approx(1783998001.4, abs=0.1)
        # journal CMD is truncated mid-token; prefix match still lands
        assert runs["recurring-bookings"] == pytest.approx(1784008801.1, abs=0.1)
        assert runs["scheduler-tick"] == pytest.approx(1784012402.0, abs=0.1)
        # weekly digest never fired in the window -> absent, never guessed
        assert "admin-weekly-digest" not in runs


class TestExpectedInterval:
    @pytest.mark.parametrize(
        ("schedule", "expected"),
        [
            ("*/5 * * * *", 300.0),
            ("* * * * *", 60.0),
            ("30 * * * *", 3600.0),
            ("15,45 * * * *", 1800.0),
            ("0 3 * * *", 86400.0),
            ("0 7,19 * * *", 43200.0),
            ("30 7 * * 1", 604800.0),
            ("0 4 1 * *", 2678400.0),
            ("@daily", 86400.0),
            ("@weekly", 604800.0),
        ],
    )
    def test_buckets(self, schedule: str, expected: float) -> None:
        assert expected_interval_s(schedule) == expected

    def test_reboot_and_garbage_are_exempt(self) -> None:
        assert expected_interval_s("@reboot") is None
        assert expected_interval_s("whenever") is None


class TestCeleryRuns:
    def test_parse(self) -> None:
        runs = parse_celery_runs(_fixture("cron/redis-taskengine.txt"))
        by_slug = {job.slug: (ts, err) for job, ts, err in runs}
        assert len(by_slug) == 2  # non-JSON and tab-less lines dropped
        ts, err = by_slug["celery-gmail-incremental-sync"]
        assert ts == pytest.approx(1784011920.51)
        assert err is None
        _, err = by_slug["celery-compute-signal-health"]
        assert err is not None and "IntegrityError" in err


class TestCronObservation:
    def test_facts_findings_and_streak_escalation(self) -> None:
        import time

        from atlas.collectors.cron import CronCollector, CronJob

        collector = CronCollector()
        job = CronJob(
            name="Nightly backup",
            slug="nightly-backup",
            schedule="0 3 * * *",
            command="/opt/x/backup.sh >> /var/log/x-backup.log 2>&1",
            source="crontab",
            user="root",
        )
        now = time.time()
        args = (
            "web-1",
            [job],
            {"nightly-backup": now - 3600},
            {"/var/log/x-backup.log": "failed"},
            [],
        )
        entity = "cron:web-1/nightly-backup"

        obs = collector._build_observation(*args)
        assert obs.entities[0].key == entity
        assert obs.facts[(entity, "cron.last_status")] == "failed"
        assert obs.facts[(entity, "cron.expected_interval_s")] == 86400.0
        assert obs.facts[(entity, "cron.overdue_ratio")] < 1
        assert obs.findings[0].severity is Severity.WARNING

        collector._build_observation(*args)
        obs3 = collector._build_observation(*args)
        assert obs3.findings[0].severity is Severity.CRITICAL  # 3rd consecutive failure

        # recovery resets the streak and stops asserting
        ok = ("web-1", [job], {"nightly-backup": now}, {"/var/log/x-backup.log": "ok"}, [])
        obs_ok = collector._build_observation(*ok)
        assert obs_ok.findings == []
        assert obs_ok.facts[(entity, "cron.last_status")] == "ok"

    def test_no_last_run_means_unknown_not_stale(self) -> None:
        from atlas.collectors.cron import CronCollector, CronJob

        collector = CronCollector()
        job = CronJob(
            name="Weekly digest",
            slug="weekly-digest",
            schedule="30 7 * * 1",
            command="curl -sf http://127.0.0.1:3000/api/v1/cron/weekly-digest",
            source="crontab",
            user="root",
        )
        obs = collector._build_observation("web-1", [job], {}, {}, [])
        entity = "cron:web-1/weekly-digest"
        assert (entity, "cron.overdue_ratio") not in obs.facts
        assert (entity, "cron.last_run_ts") not in obs.facts
        assert obs.findings == []


class TestBackupLogStatus:
    def test_ok(self) -> None:
        assert parse_backup_log_status(_fixture("cron/backup-log-ok.txt")) == "ok"

    def test_failed_after_success(self) -> None:
        assert parse_backup_log_status(_fixture("cron/backup-log-failed.txt")) == "failed"

    def test_no_signal(self) -> None:
        assert parse_backup_log_status("") is None
        assert parse_backup_log_status("rotating logs\n") is None


class TestSiteDiscovery:
    def test_port_files(self) -> None:
        sites = parse_sites(_fixture("discovery/sites.txt"), "sitefarm", "sitefarm-")
        assert [s.key for s in sites] == [
            "site:sitefarm/acmedetailing",
            "site:sitefarm/plumberspro",
            "site:sitefarm/roofersnearme",
        ]
        assert sites[0].attrs == {"port": 5001, "container": "sitefarm-acmedetailing"}
        assert sites[0].parent == "app:sitefarm"

    def test_junk_lines_skipped(self) -> None:
        assert parse_sites("badline\nname\tnotaport\n", "x", "") == []
