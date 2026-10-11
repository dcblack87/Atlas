# Atlas in Telegram

Atlas pages you in Telegram when something is on fire, and answers from a menu
when you ask. It has no public address, so the bot asks Telegram for new
messages itself (long polling) from inside the one Atlas process.

## Setting it up

```toml
[telegram]
enabled = true
# bot_token = "..."     # or $ATLAS_TELEGRAM_TOKEN
# chat_id = "..."       # leave unset, send the bot /start, it replies with the id
# min_severity = "critical"
# bot_commands = true   # the menu; false leaves alerts only
# daily_brief = true    # the 07:00 morning brief
# weekly_brief = true   # the Sunday 08:00 weekly brief
```

On every start Atlas tells Telegram the command list and shows the Menu
button. The list is for the configured chat only, so a stranger who finds the
bot sees no commands. The Menu button is the one beside the message box in a
one-to-one chat with the bot; a group shows a `/` button instead.

Only one process may read a bot's messages. A second Atlas on the same token
(or a webhook set on it) makes Telegram refuse both; the log says so plainly
and the bot retries every minute.

## The menu

`/start`, `/menu` and `/help` all open the home card, which carries the
headline (health, open incidents, apps with commits waiting) before you tap
anything.

| Command      | What it shows                                                       |
| ------------ | ------------------------------------------------------------------- |
| `/status`    | Fleet overview: health, hosts up, apps, incidents, pending deploys  |
| `/digest`    | The last 24 hours: incidents, backups, crons                        |
| `/incidents` | Open incidents, numbered; tap a number to open one                  |
| `/crons`     | Cron jobs, failing first                                            |
| `/backups`   | Backup age per app                                                  |
| `/hosts`     | Load, memory and disk per host                                      |
| `/apps`      | App health, latency, sites                                          |
| `/deploys`   | Apps with commits waiting                                           |

A tap changes the card it was pressed on rather than adding a message. Every
card says what time it was read and ends with Refresh and Menu. Long lists
have pages.

## What it can change

One thing: **acknowledging an incident**, from the incident's card or straight
from its alert. It asks first, then stops the daily reminder; the incident
stays on the list until the condition clears. It is the same act as `a` on the
Incidents screen.

Deploys stay in the TUI, where they are audited.

## Alerts and briefs

Alerts are critical incidents only by default (`min_severity = "warning"` for
more): opened, escalated, still open after a day, resolved. They are deduped
for an hour. An alert for an incident that is still open carries an
Acknowledge button. A button under an alert or a brief answers in a new
message below it, so the alert stays in the chat as it was sent; only the
menu's own cards change in place.

The morning brief (07:00) and the weekly brief (Sunday 08:00) are sent to the
same chat. Each has its own switch above.

## Where the code is

`src/atlas/notify/bot/`: `commands.py` is the one registry behind typed
commands, buttons and the command list; `format.py` is the house style;
`transport.py` is every call to Telegram; `poller.py` is the loop.
`src/atlas/notify/telegram.py` sends the alerts and briefs.
