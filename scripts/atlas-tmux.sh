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

# Always-on: respawn no matter how Atlas exits, including a clean `q`. This
# is a server desk-console — you leave it by DETACHING (Ctrl-b then d), not
# by quitting, so a stray `q` on a tablet must never take the console down.
# To actually stop it: tmux kill-session -t atlas.
while true; do
    uv run atlas run || true
    echo "atlas exited — restarting in 3s (tmux kill-session -t atlas to stop)"
    sleep 3
done
