# Surf Alert

Surf Alert is a command-line app meant to run once a day. It reads your surf spots and rules
from `surf_alert.toml`, downloads each spot's Surfline forecast, and finds the spots where
today's remaining daylight hours meet every rule (for example "wave height above 2 ft" and
"swell period above 7 s"). It then asks TypeSafe's Jev decision model whether those spots are
worth surfing today and which one is best. If Jev approves at least one spot, you get one
WhatsApp message naming the beach to go to, with the current conditions there. If no spot is
approved, nothing is sent.

## Setup

1. Create the virtual environment (Python 3.13) and install the dependencies into it:

   ```sh
   python3.13 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

   `pysurfline` is installed from git (see `requirements.txt`) and pulls in pandas and
   matplotlib.

2. Create your config file from the example and edit it:

   ```sh
   cp surf_alert.example.toml surf_alert.toml
   ```

   The file holds `[settings]`, one `[[spots]]` table per spot and one `[[rules]]` table per
   rule. The comments in `surf_alert.example.toml` list every option, metric and operator.

3. Create your secrets file and fill it in:

   ```sh
   cp .env.example .env
   ```

   | Variable | Needed by | Value |
   |---|---|---|
   | `TYPESAFE_API_KEY` | `check` | API key from console.typesafe.ai |
   | `WHATSAPP_PHONE` | `check` (not `--dry-run`), `test-notification` | Your number in international format with `+`, e.g. `+34612345678` |
   | `CALLMEBOT_API_KEY` | `check` (not `--dry-run`), `test-notification` | The key CallMeBot sends you after activation |

   `.env` is git-ignored. Real environment variables always win over `.env`, so a variable
   that is set but empty in your shell counts as missing even if `.env` has a value.

### Finding a spot ID

Open the spot on surfline.com. The ID is the last part of the URL, for example
`https://www.surfline.com/surf-report/pipeline/5842041f4e65fad6a7708890` has the ID
`5842041f4e65fad6a7708890`. Put it in the spot's `id` key.

### Activating CallMeBot (one time)

WhatsApp messages go through the free CallMeBot personal API, which is meant for messaging
yourself. Follow https://www.callmebot.com/blog/free-api-whatsapp-messages/: add the CallMeBot
number to your phone's contacts, send it the activation phrase on WhatsApp, and you will receive
your API key. Put it in `CALLMEBOT_API_KEY`.

## Commands

Run everything from the project root. Global options go before the command:

```
.venv/bin/python -m surf_alert [--config PATH] [--env-file PATH] [-v] COMMAND [options]
```

| Command | What it does |
|---|---|
| `spots` | List the configured spots (no network). |
| `rules` | List the configured rules (no network). |
| `conditions [--spot NAME]` | Fetch and print the current conditions and rule status for every spot, or only one (Surfline only, nothing is sent). |
| `check` | Fetch, evaluate, ask Jev, and send the WhatsApp message. |
| `check --dry-run` | The same, but print the message instead of sending it (WhatsApp variables not needed). |
| `test-notification` | Send a test WhatsApp message (does not read the config). |

`--config` defaults to `surf_alert.toml` and `--env-file` to `.env`, both relative to the
current directory. `-v` logs debug information to stderr.

### Verbose report (`-v`)

Put `-v` before the command, for example:

```sh
.venv/bin/python -m surf_alert -v check --dry-run
.venv/bin/python -m surf_alert -v conditions
```

With `check` or `conditions`, `-v` adds two things after the normal output (which is
unchanged):

- **An explanation on stderr** of how the result was reached. For each spot it shows today's
  daylight window, the current conditions and Surfline rating, how many of today's hours pass
  each rule, whether the spot has enough good hours, Jev's confidence against `jev_threshold`
  (including spots Jev rejected) and its best-spot probability, and the outcome. A fetch
  failure is explained too.
- **A table, `surf_alert_report.csv`,** in the current directory (overwritten on each run), with
  one row per spot. It starts with the outcome, rank, Jev confidence, best-spot probability,
  threshold, rule result and Surfline ratings, then one column per rule (hours passing it
  today), then the current conditions. `conditions` leaves the Jev columns empty because it
  never asks Jev. The file is git-ignored.

Exit codes: `0` success (a message was sent or printed, or there was nothing to send), `1`
config error, missing or invalid environment variable, or unknown `--spot`, `2` usage error,
`3` degraded run (a spot failed to fetch or the Jev call failed; a message may still have been
sent), `4` sending the WhatsApp message failed.

## Running daily

Before scheduling, run these once by hand:

```sh
.venv/bin/python -m surf_alert check --dry-run
.venv/bin/python -m surf_alert test-notification
```

The recommended setup is a cron entry that runs at 06:00 local time. Open your crontab with
`crontab -e` and add:

```
0 6 * * * cd /Users/leoxyde/Documents/Coding/Python/ClaudeAgentsScripts && .venv/bin/python -m surf_alert --config surf_alert.toml check >> "$HOME/Library/Logs/surf_alert.log" 2>&1
```

On macOS, cron jobs don't run while the Mac is asleep. A launchd agent runs a missed job when
the Mac wakes: create `~/Library/LaunchAgents/<label>.plist` with `StartCalendarInterval`
(Hour 6, Minute 0), `ProgramArguments` running the same command, and `WorkingDirectory` set to
the project root, then load it with `launchctl`. The app does not install cron or launchd
entries itself.

Notes:
- Only "today" is evaluated, in each spot's local time, from now until dusk. A run after dusk
  sends nothing.
- The app keeps no state: running `check` twice in a day can send two messages.
- Network calls are not retried; the next daily run is the retry. The wind, tide and weather
  requests (made by pysurfline) have no timeout, because pysurfline does not support one; the
  surf, swell and rating requests time out after 30 seconds.
- Surfline retired the `wave` forecast that pysurfline asks for, so pysurfline's request for it
  fails with HTTP 404 on every run (visible with `-v`). That is expected: the app gets the same
  data from Surfline's `surf` and `swells` forecasts instead.
