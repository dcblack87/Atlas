"""Redaction: credentials come out, everything else stays readable."""

from pathlib import Path

from atlas.redact import scrub, scrub_secrets

FIXTURES = Path(__file__).parent / "fixtures" / "cron"
SHA = "3f9a1c7e2b4d6f8091a2b3c4d5e6f708192a3b4c"


def test_bearer_token_in_a_cron_line_is_redacted_and_the_rest_survives() -> None:
    line = next(
        line
        for line in (FIXTURES / "crond.txt").read_text().splitlines()
        if "Authorization: Bearer" in line
    )
    cleaned = scrub_secrets(line)
    assert "1111111111" not in cleaned
    assert "Authorization: Bearer [redacted]" in cleaned
    assert "curl -sf -X POST" in cleaned  # still recognisable as the job it is
    assert "http://" in cleaned


def test_scrub_secrets_recognises_the_usual_shapes() -> None:
    text = (
        "DATABASE_URL=postgres://app:hunter2@db:5432/app\n"
        "api_key = sk-ant-abcdefghijklmnopqrstuvwx\n"
        "curl -H 'X-Cron-Secret: s3cr3tvalue' http://127.0.0.1:3000/tick\n"
        "Authorization: Basic dXNlcjpwYXNz\n"
    )
    cleaned = scrub_secrets(text)
    for secret in ("hunter2", "sk-ant", "s3cr3tvalue", "dXNlcjpwYXNz"):
        assert secret not in cleaned
    assert "postgres://[redacted]@db:5432/app" in cleaned
    assert "http://127.0.0.1:3000/tick" in cleaned


def test_scrub_secrets_leaves_paths_and_shas_alone() -> None:
    text = (
        f"deployed {SHA}\n"
        "/opt/shopfront/scripts/backup-database-and-uploads.sh auto >> /var/log/backup.log 2>&1"
    )
    assert scrub_secrets(text) == text


def test_scrub_also_takes_unlabelled_long_tokens_but_not_shas() -> None:
    token = "A1b2C3d4" * 8
    cleaned = scrub(f"sha {SHA} leaked {token}")
    assert token not in cleaned
    assert SHA in cleaned
