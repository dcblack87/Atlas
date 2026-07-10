"""Security collector parser."""

from atlas.collectors.security import parse_security

FIXTURE = """\
137
===ATLAS===
*** System restart required ***
===ATLAS===
14
3
===ATLAS===
22 80 443 """


def test_parse_security() -> None:
    obs = parse_security(FIXTURE, "web-1")
    metrics = {(s.metric, s.entity): s.value for s in obs.samples}
    assert metrics[("security.failed_auth_1h", "host:web-1")] == 137
    assert obs.facts[("host:web-1", "security.reboot_required")] is True
    assert obs.facts[("host:web-1", "security.pending_updates")] == 14
    assert obs.facts[("host:web-1", "security.pending_security_updates")] == 3
    assert obs.facts[("host:web-1", "security.public_ports")] == [22, 80, 443]
    assert obs.findings == []  # 137 < brute-force threshold


def test_bruteforce_finding() -> None:
    obs = parse_security(FIXTURE.replace("137", "9001"), "web-1")
    assert len(obs.findings) == 1
    assert obs.findings[0].rule_id == "ssh_bruteforce"


def test_garbage_is_harmless() -> None:
    obs = parse_security("nonsense", "web-1")
    assert obs.samples == []
    assert obs.facts == {}
