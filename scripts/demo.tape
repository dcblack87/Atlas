# VHS tape for the README demo GIF.
#
#   brew install vhs        (pulls ttyd + ffmpeg)
#   vhs scripts/demo.tape   (run from the repo root)
#
# Records the demo fleet only. Nothing real, same rule as the screenshots.

Output docs/screenshots/demo.gif

Set FontSize 15
Set Width 1200
Set Height 680
Set Padding 12
Set WindowBar Colorful
Set TypingSpeed 60ms

Type "uv run atlas run --demo"
Enter
# demo runtime seeds a three-host fleet with 24h of history
Sleep 6s

# the dashboard: tiles, hosts, apps, a pending deploy
Sleep 4s

# incidents: open warning + a 24h timeline
Type "2"
Sleep 4s
Escape

# apps: drill into shopfront (3 commits behind origin, CI green)
Type "3"
Sleep 1s
Down
Down
Sleep 4s
Escape

# cost: real monthly figures
Type "6"
Sleep 3s
Escape

# security posture
Type "7"
Sleep 3s
Escape

# cycle display profiles: standard -> eink -> glance
Type "p"
Sleep 3.5s
Type "p"
Sleep 3.5s
Type "p"
Sleep 2s

Type "q"
Sleep 1s
