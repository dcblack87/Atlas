"""Secret redaction, in two strengths.

``scrub_secrets`` removes what is recognisably a credential and nothing else.
It is safe to run over text Atlas stores and shows back to you (cron command
lines, deploy output): paths, shas, and ordinary words survive.

``scrub`` adds a catch-all for long high-entropy strings and takes out public
IP addresses. It will eat the occasional innocent identifier, which is the
right trade for text that leaves the machine (AI context, context bundles).
"""

from __future__ import annotations

import ipaddress
import re

REDACTED = "[redacted]"

# Each pattern keeps its first group (the label) and drops the credential, so
# "Authorization: Bearer abc123" becomes "Authorization: Bearer [redacted]".
_LABELLED: tuple[re.Pattern[str], ...] = (
    re.compile(r"(\bbearer\s+)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(\bauthorization\s*:\s*(?:basic|token)\s+)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|token|secret|password|passwd)\s*[=:]\s*)[^\s'\"]+", re.IGNORECASE),
    # --password x, --pass=x, -p x is too ambiguous to touch
    re.compile(r"(--(?:pass(?:word)?|token|secret|api-?key|auth)[= ])[^\s'\"]+", re.IGNORECASE),
    # X-Auth-Token: x, X-Api-Key: x, and similar custom headers
    re.compile(r"(\bx-[a-z-]*(?:auth|token|key|secret)[a-z-]*\s*:\s*)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s@/]+:[^\s@/]+(?=@)", re.IGNORECASE),  # user:pass@
)
_BARE = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}"  # Anthropic, OpenAI
    r"|\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"  # Stripe
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}"  # GitHub
    r"|\bglpat-[A-Za-z0-9_-]{20,}"  # GitLab
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"  # Slack
    r"|\bAKIA[0-9A-Z]{16}\b"  # AWS access key id
    r"|\bAIza[0-9A-Za-z_-]{35}\b"  # Google API key
    r"|\b\d{8,10}:[A-Za-z0-9_-]{35}\b"  # Telegram bot token
    r"|\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r"|-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"
)
_HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9+/_-]{40,}\b")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
# A 40-hex string is let through only where it is plainly a commit, not a secret
_SHA_CONTEXT = re.compile(
    r"(?:sha|commit|head|rev|ref|deploy|origin|rollback|→|\.\.)\W{0,4}$", re.IGNORECASE
)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def scrub_secrets(text: str) -> str:
    """Redact recognisable credentials; leave everything else untouched."""
    for pattern in _LABELLED:
        text = pattern.sub(rf"\g<1>{REDACTED}", text)
    return _BARE.sub(REDACTED, text)


def scrub(text: str) -> str:
    """``scrub_secrets`` plus long token-shaped strings (commit shas excepted) and public IPs."""
    text = _HIGH_ENTROPY.sub(_redact_unless_sha, scrub_secrets(text))
    return _IPV4.sub(_redact_public_ip, text)


def _redact_unless_sha(match: re.Match[str]) -> str:
    token = match.group(0)
    before = match.string[max(0, match.start() - 24) : match.start()]
    if _GIT_SHA.fullmatch(token) and _SHA_CONTEXT.search(before):
        return token
    return REDACTED


def _redact_public_ip(match: re.Match[str]) -> str:
    try:
        address = ipaddress.ip_address(match.group(0))
    except ValueError:
        return match.group(0)  # not an address, e.g. a version number like 300.1.2.3
    return "[ip]" if address.is_global else match.group(0)
