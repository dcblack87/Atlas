"""The architectural invariant: only atlas.deploy mutates servers.

A denylist grep over the source tree. Crude by design — it catches the
honest mistake (someone adds a restart to a collector) rather than the
determined adversary, and it fails loudly in CI.
"""

import re
from pathlib import Path

SRC = Path(__file__).parent.parent.parent / "src" / "atlas"

# Command fragments that mutate a server. (deploy.sh is deliberately absent:
# the *string* legitimately lives in config defaults; only executing it is a
# mutation, and execution requires a Transport.)
MUTATING = re.compile(
    r"docker (restart|rm|stop|kill|prune|system prune|builder prune)"
    r"|systemctl (restart|stop|start|reboot|poweroff)"
    r"|git (checkout|pull|reset|push)"
    r"|rm -rf"
    r"|shutdown -"
)

ALLOWED_DIRS = {"deploy"}
# config.py declares the *default deploy command string*; the deploy screen
# *displays* commands in its confirmation dialog. Neither holds a Transport;
# execution still only happens inside atlas/deploy/.
ALLOWED_FILES = {Path("config.py"), Path("tui/screens/deploy.py")}


def test_only_deploy_module_constructs_mutations() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC)
        if relative.parts[0] in ALLOWED_DIRS or relative in ALLOWED_FILES:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if MUTATING.search(stripped):
                offenders.append(f"{relative}:{number}: {stripped}")
    assert not offenders, (
        "Mutating commands outside atlas/deploy/ — read-only by default is "
        "an architectural invariant:\n" + "\n".join(offenders)
    )
