"""Collector parsers against recorded fixtures — the bulk of test value."""

from pathlib import Path

import pytest

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
