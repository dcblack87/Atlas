#!/usr/bin/env bash
# Run Atlas inside tmux with a respawn loop — a crash self-heals in 5s.
#
# Launch (robust to a missing +x bit after rsync/scp):
#   tmux new -d -s atlas 'cd /opt/atlas && exec bash scripts/atlas-tmux.sh'
#
# Attach from anywhere on the tailnet:
#   ssh root@<atlas-host> -t 'tmux attach -t atlas'
set -u

# tmux launches a bare shell — make sure uv (default install location) is
# reachable regardless of profile files.
export PATH="$HOME/.local/bin:$PATH"

cd "$(dirname "$0")/.."

# tmux tuning for a stable e-ink experience.
if [ -n "${TMUX:-}" ]; then
    # Copy keys (OSC 52) pass through to the local clipboard — makes `c` work
    # from an SSH session on a tablet.
    tmux set -g set-clipboard on 2>/dev/null || true
    # Follow the most recent client's size instead of shrinking to the
    # smallest attached client. Without this, a second attach (e.g. the Mac
    # still connected) makes the layout thrash on e-ink as tmux resizes.
    tmux set -g window-size latest 2>/dev/null || true
    tmux set -g aggressive-resize off 2>/dev/null || true
    # Don't hold a closed pane; keep the status bar quiet.
    tmux set -g status off 2>/dev/null || true
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
