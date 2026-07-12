# Running Atlas

Atlas is a terminal app. It runs anywhere a modern terminal runs: locally on
a laptop for development, or (the intended setup) 24/7 inside tmux on one of
your servers, with every other device attaching as a thin client.

## In a terminal, locally

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and any
reasonably modern terminal. Textual renders fine in Terminal.app, iTerm2,
Ghostty, WezTerm, Alacritty, Windows Terminal, and over plain SSH. Truecolor
looks best; the e-ink profile survives 16 colours and even greyscale.

```bash
git clone https://github.com/dcblack87/Atlas && cd Atlas
uv sync

# a complete fake fleet, zero configuration:
uv run atlas run --demo

# your real fleet:
cp atlas.example.toml atlas.toml && $EDITOR atlas.toml
uv run atlas check     # validates config and prints the host/app map
uv run atlas run
```

`atlas run --headless` runs the collectors, rules engine, and Telegram
alerting with no TUI at all. Useful for a server you only ever want paging
you, or for testing.

## Keyboard reference

Atlas is fully keyboard-driven. There is no mouse anywhere.

| Key      | Goes to                                            |
| -------- | -------------------------------------------------- |
| `1`      | Dashboard (fleet at a glance)                      |
| `2`      | Incidents (open + 24h timeline)                    |
| `3`      | Apps (drill-down: git, drift, sites, containers)   |
| `4`      | Deploy (preflight, typed confirm, stream, verify)  |
| `5`      | Chat (AI, grounded in live fleet SQL)              |
| `6`      | Cost (Hetzner + Claude spend)                      |
| `7`      | Security (updates, ports, ssh failures, certs)     |
| `8`      | Reports (morning / weekly briefs)                  |
| `h`      | Hosts detail                                       |
| `l`      | Logs                                               |
| `b`      | Write an AI context bundle                         |
| `c`      | Copy the current screen (OSC 52, works over SSH)   |
| `e`      | Explain incident with AI (on the incidents screen) |
| `F2`/`p` | Cycle display profile: standard, eink, glance      |
| `?`      | Help                                               |
| `q`      | Quit                                               |

## Always-on: tmux on a server

The intended installation. Atlas lives in a tmux session on one server and
never stops; you attach to the same live session from a Mac, a tablet, or a
phone, and detach without killing anything.

```bash
# on the server (as the user whose SSH key reaches the fleet):
cd /opt && git clone https://github.com/dcblack87/Atlas atlas && cd atlas
uv sync
cp atlas.example.toml atlas.toml && $EDITOR atlas.toml
uv run atlas check

# start the always-on console:
tmux new -d -s atlas 'cd /opt/atlas && exec bash scripts/atlas-tmux.sh'
```

`scripts/atlas-tmux.sh` wraps Atlas in a respawn loop: a crash (or a stray
`q` from a tablet) restarts it in seconds, so the console is genuinely
always-on. It also sets the tmux options that matter for shared sessions:
clipboard passthrough for the `c` key, and `window-size latest` so a second
attached client never shrinks the layout under the first.

Attach from anywhere that can reach the server (a tailnet makes "anywhere"
literal):

```bash
ssh root@<atlas-host> -t 'tmux attach -t atlas'
```

Leave with detach, not quit: `Ctrl-b` then `d`. Atlas keeps collecting,
alerting, and rendering on the server. To actually stop it:
`tmux kill-session -t atlas`.

For attaching from e-ink tablets and phones (BOOX, reMarkable, Supernote,
iOS, Android), see [device-setup.md](device-setup.md).
