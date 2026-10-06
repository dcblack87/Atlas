"""Secret redaction, in two strengths.

``scrub_secrets`` removes what is recognisably a credential and nothing else.
It is safe to run over text Atlas stores and shows back to you (cron command
lines, deploy output): paths, shas, and ordinary words survive.

``scrub`` adds a catch-all for long high-entropy strings. It will eat the
occasional innocent identifier, which is the right trade for text that leaves
the machine (AI context, context bundles).
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

# Each pattern keeps its first group (the label) and drops the credential, so
# "Authorization: Bearer abc123" becomes "Authorization: Bearer [redacted]".
_LABELLED: tuple[re.Pattern[str], ...] = (
    re.compile(r"(\bbearer\s+)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(\bauthorization\s*:\s*(?:basic|token)\s+)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|token|secret|password|passwd)\s*[=:]\s*)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s@/]+:[^\s@/]+(?=@)", re.IGNORECASE),  # user:pass@
)
_BARE = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"
)
_HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9+/_-]{40,}\b")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


def scrub_secrets(text: str) -> str:
    """Redact recognisable credentials; leave everything else untouched."""
    for pattern in _LABELLED:
        text = pattern.sub(rf"\g<1>{REDACTED}", text)
    return _BARE.sub(REDACTED, text)


def scrub(text: str) -> str:
    """``scrub_secrets`` plus any long token-shaped string (git shas excepted)."""
    return _HIGH_ENTROPY.sub(_redact_unless_sha, scrub_secrets(text))


def _redact_unless_sha(match: re.Match[str]) -> str:
    token = match.group(0)
    return token if _GIT_SHA.fullmatch(token) else REDACTED
