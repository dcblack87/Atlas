# Running Atlas on an e-ink tablet (BOOX + Termius)

Atlas runs 24/7 in a `tmux` session on your ops server; the tablet is a thin
client that attaches to it. Nothing is installed on the tablet but Termius
and Tailscale, and — because the server has Tailscale SSH enabled — the
tablet needs **no SSH key**. Your tailnet identity is the login.

This is the "always-on operations cockpit" setup: the BOOX sits on the desk
showing fleet health, and picking it up drops you straight into the console.

## Prerequisites (already true for this fleet)

- Atlas running on the server in a tmux session named `atlas`
  (`tmux new -d -s atlas 'cd /opt/atlas && exec bash scripts/atlas-tmux.sh'`).
- Tailscale SSH enabled on the server (`tailscale set --ssh`) — lets any of
  your own tailnet devices SSH in with no key exchange.
- Termius + Tailscale installed on the BOOX, signed into the **same**
  Tailscale account (`david.black@dcblack.co.uk`).

## One-time setup on the BOOX

1. **Tailscale** — open the app, sign in, toggle the VPN **on**. Confirm the
   server (e.g. `ballcourt-prod`, `100.81.79.24`) shows in the machine list.

2. **Termius → Hosts → +** (new host):
   - **Address**: the server's Tailscale IP (`100.81.79.24`) or its MagicDNS
     name (`ballcourt-prod`)
   - **Port**: `22`
   - **Username**: `root`
   - **Key**: leave as **None / password** — Tailscale SSH handles auth. (If
     Termius insists on something, pick "Keyboard-interactive"; you won't be
     asked for a password.)
   - **Label**: `Atlas`

3. **Auto-attach to the console** — in that host's settings find the startup
   command / "snippet on connect" field and set:
   ```
   tmux attach -t atlas
   ```
   Now connecting drops you straight into Atlas instead of a bare shell.

4. **First connect** — tap the host. Tailscale SSH may show a one-time
   approval link the first time; approve it. Atlas fills the screen.

## E-ink comfort settings (Termius)

- **Font size**: 14–16pt.
- **Theme**: a **light** theme (black text on white) reads best on e-ink.
- **Cursor**: disable blinking if the option exists.
- Enable the **extra keys row** (Esc, Ctrl, arrows, Tab) — Atlas is fully
  keyboard-driven and e-ink keyboards lack function keys.

## Using Atlas on e-ink

- Press **`p`** (the e-ink-friendly alias for F2) to cycle display profiles:
  `standard → eink → glance`. Use **glance** for across-the-desk viewing —
  a handful of huge tiles, refreshed slowly, no flicker.
- Menu: `1` Dashboard · `2` Incidents · `3` Apps · `4` Deploy · `5` Chat ·
  `6` Cost · `7` Security · `8` Reports · `h` Hosts · `l` Logs · `b` Bundle ·
  `c` Copy · `?` Help.
- **Copy** with `c` (uses OSC 52 → lands on the tablet's clipboard, ready to
  paste into any Android app).
- **Detach** without stopping Atlas: `Ctrl-b` then `d`. Atlas keeps running
  on the server; reconnect any time from the BOOX, phone, or Mac and you're
  back where you left off.

## Same recipe on a phone

Identical: Termius (or any SSH client) + Tailscale, same host entry, same
`tmux attach -t atlas` snippet. Phone is the "emergency" client — glance at
fleet health or run a deploy from anywhere on the tailnet.

## If it won't connect

- **Tailscale VPN off** on the tablet → turn it on; the server must be
  reachable in the Tailscale machine list.
- **`no server running` / `session not found`** → the tmux session isn't up.
  From the Mac: `ssh root@<server> 'tmux new -d -s atlas "cd /opt/atlas && exec bash scripts/atlas-tmux.sh"'`.
- **Auth prompt / permission denied** → confirm the tablet is signed into the
  same Tailscale account and Tailscale SSH is enabled on the server
  (`tailscale set --ssh`).
