"""Rule hysteresis and judgment."""

from atlas.engine.rules import FactRule, MetricRule
from atlas.model import Severity

disk = MetricRule("disk_high", "disk.used_pct", warn=80, crit=90, for_samples=3, clear_samples=3)
http = MetricRule("http_down", "http.up", crit=1, below=True, for_samples=2, clear_samples=1)


class TestMetricRuleHysteresis:
    def test_needs_consecutive_breaches(self) -> None:
        assert disk.judge([70, 85, 85]) is None  # only 2 breaching
        assert disk.judge([85, 85, 85]) is Severity.WARNING
        assert disk.judge([91, 92, 95]) is Severity.CRITICAL

    def test_single_spike_does_not_open(self) -> None:
        assert disk.judge([70, 95, 70]) is None

    def test_not_enough_data(self) -> None:
        assert disk.judge([95]) is None

    def test_clear_requires_consecutive_normals(self) -> None:
        assert not disk.cleared([85, 70, 70])  # only 2 clear
        assert disk.cleared([70, 70, 70])

    def test_below_semantics(self) -> None:
        assert http.judge([0.0, 0.0]) is Severity.CRITICAL
        assert http.judge([1.0, 0.0]) is None  # one failed probe is not an outage
        assert http.cleared([1.0])


class TestFactRule:
    def test_cert_expiry(self) -> None:
        cert = FactRule("cert_expiry", "cert.days_remaining", warn=21, crit=7, below=True)
        assert cert.judge(30) is None
        assert cert.judge(14) is Severity.WARNING
        assert cert.judge(3) is Severity.CRITICAL

    def test_backup_age(self) -> None:
        backup = FactRule("backup_stale", "backup.age_hours", warn=30, crit=54)
        assert backup.judge(8) is None
        assert backup.judge(40) is Severity.WARNING
        assert backup.judge(60) is Severity.CRITICAL
