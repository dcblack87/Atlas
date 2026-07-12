# Attaching from tablets and phones (BOOX, reMarkable, Supernote, iOS, Android)

Atlas runs 24/7 in a tmux session on your ops server (see
[running.md](running.md)). Every device in this guide is a thin client: it
carries an SSH client and nothing else. Nothing about your fleet is stored on
the tablet, and losing it costs you nothing but the hardware.

The recipe is always the same three steps:

1. Get the device onto your tailnet (or otherwise able to reach the server).
2. Get an SSH client onto the device.
3. Connect with a startup command of `tmux attach -t atlas`.

With Tailscale SSH enabled on the server (`tailscale set --ssh`), step 3
needs no key on the device. Your tailnet identity is the login, which is
exactly what you want on a tablet that might get left on a train.

## Server-side prerequisites

- Atlas running in a tmux session named `atlas`:
  `tmux new -d -s atlas 'cd /opt/atlas && exec bash scripts/atlas-tmux.sh'`
- Tailscale SSH enabled on the server: `tailscale set --ssh`

## BOOX (and other Android e-ink tablets)

The smooth path. BOOX tablets run Android with the Play Store, so this is
ten minutes with no tinkering.

1. **Install Tailscale and Termius** from the Play Store. Sign Tailscale
   into the same tailnet as your servers and toggle the VPN on. Confirm the
   server shows in the machine list.
2. **Termius, Hosts, +** (new host):
   - Address: the server's Tailscale IP or MagicDNS name
   - Port: `22`, Username: `root` (or your deploy user)
   - Key: leave as none. Tailscale SSH handles auth. If Termius insists,
     pick "Keyboard-interactive"; you will not be asked for a password.
3. **Auto-attach**: in the host's settings, set the startup snippet to
   `tmux attach -t atlas`. Connecting now drops you straight into Atlas.
4. **First connect**: Tailscale SSH may show a one-time approval link.
   Approve it once and you are done.

E-ink comfort settings in Termius: font size 14 to 16pt, a light theme
(black on white reads best on paper displays), cursor blink off, and enable
the extra keys row (Esc, Ctrl, arrows, Tab). Atlas is fully keyboard-driven
and tablet keyboards lack function keys, which is why `p` mirrors `F2`.

Once attached, press `p` to cycle display profiles: `standard`, `eink`
(greyscale-safe glyphs, slow coalesced refresh), `glance` (huge tiles,
readable across a desk).

## reMarkable

The tinkerer path. reMarkable tablets run Linux, not Android: there is no
app store, no Termius, and no supported way to install apps. What they do
have is official SSH access to a root shell, which is enough to build a
serviceable Atlas client if you enjoy this sort of thing. If you just want
an e-ink cockpit that works this afternoon, buy a BOOX or a Supernote.

Getting a shell on the tablet:

- **reMarkable 1 and 2**: SSH is already there. Find the root password under
  Settings, Help, Copyrights and licenses (at the end of the GPLv3 section),
  then `ssh root@10.11.99.1` over the USB cable, or use the tablet's Wi-Fi
  address.
- **reMarkable Paper Pro**: SSH is behind developer mode (Settings, General,
  Software, Advanced). Enabling it factory-resets the tablet, so do it
  before you accumulate notes, not after.

Turning that shell into an Atlas client needs two more pieces, both from the
community rather than reMarkable:

- **A terminal emulator on the device.** [Toltec](https://toltec-dev.org/)
  (the community package manager for reMarkable 1 and 2) packages `yaft`
  and `fingerterm`. Check Toltec's supported-OS table before installing;
  it lags reMarkable's firmware releases, and it does not support the Paper
  Pro at all. No terminal emulator means no on-device Atlas, which currently
  rules the Paper Pro out.
- **A network path to the server.** Simplest is SSH from the terminal
  emulator to the server's LAN address. For roaming, community Tailscale
  setups for reMarkable exist (search "tailscale remarkable"); they work but
  are unofficial and survive OS updates imperfectly.

Then it is the same as everywhere else: `ssh` to the server, `tmux attach
-t atlas`, press `p` until the eink profile is active. Treat the whole
exercise as unsupported: reMarkable's updates can and do break community
packages, and you should be comfortable re-flashing via recovery before
depending on it.

## Supernote

Supernote devices run Android underneath but ship without a store. Newer
models (the A5 X2 "Manta" generation onwards) added official APK
sideloading in recent firmware, which makes them BOOX-equivalent with one
extra step:

1. Update to a firmware version with sideloading and enable it (under
   Settings, Apps on current firmware; the option's location has moved
   between releases).
2. Sideload the Termius and Tailscale APKs (download them from the vendors'
   official releases or a mirror you trust).
3. Follow the BOOX steps above from step 2.

Older Supernote models without sideloading have no practical path; there is
no shell access to build one by hand.

## Phones (iOS and Android)

The emergency client. Same recipe: Tailscale from the app store, an SSH
client (Termius on both platforms, or Blink Shell on iOS), the same host
entry, the same `tmux attach -t atlas` startup snippet. Glance at fleet
health from anywhere, or run a full audited deploy from a beach.

## Anything with a terminal

Any laptop or desktop needs nothing beyond what it already has:

```bash
ssh root@<atlas-host> -t 'tmux attach -t atlas'
```

## If it won't connect

- **Tailscale VPN off** on the device: turn it on. The server must appear
  in the Tailscale machine list.
- **`no server running` or `session not found`**: the tmux session is not
  up. From any machine:
  `ssh root@<server> 'tmux new -d -s atlas "cd /opt/atlas && exec bash scripts/atlas-tmux.sh"'`
- **Auth prompt or permission denied**: confirm the device is signed into
  the same tailnet and Tailscale SSH is enabled on the server
  (`tailscale set --ssh`).
- **Layout thrashes when a second device attaches**: you are not using
  `atlas-tmux.sh`, which sets `window-size latest`. Set it manually:
  `tmux set -g window-size latest`.
