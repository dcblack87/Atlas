#!/usr/bin/env bash
# Run Atlas inside tmux with a respawn loop — a crash self-heals in 5s.
#
#   tmux new -s atlas /opt/atlas/scripts/atlas-tmux.sh
#
# Attach from anywhere on the tailnet:
#   ssh root@<atlas-host> -t 'tmux attach -t atlas'
set -u

cd "$(dirname "$0")/.."

# Let Atlas's copy keys (OSC 52) pass through tmux to the local clipboard —
# this is what makes `c` work from an SSH session on a tablet.
if [ -n "${TMUX:-}" ]; then
    tmux set -g set-clipboard on 2>/dev/null || true
fi

while true; do
    uv run atlas run
    status=$?
    if [ "$status" -eq 0 ]; then
        break  # clean quit (q) — don't respawn
    fi
    echo "atlas exited with status $status — restarting in 5s (Ctrl-C to stop)"
    sleep 5
done
