# Surf Alert: specification

Status: ready for implementation
Language: Python 3.13 (project venv at `.venv/`)

## 1. Overview

Surf Alert is a command-line app that runs once a day (from cron or launchd). It reads a list of surf spots and a list of rules from a config file the user edits by hand. It downloads each spot's Surfline forecast with the `pysurfline` library and finds the spots where today's remaining daylight hours meet every rule (for example "wave height above 2 ft" and "swell period above 7 s"). It then asks TypeSafe's Jev decision model whether those spots are actually worth surfing today and which one is best. If Jev approves at least one spot, the app sends the user one WhatsApp message naming the beach to go to, plus the current conditions there. If no spot is approved, it sends nothing.

The user can also list the configured spots and rules, and print current conditions for each spot without sending anything.

### Glossary

| Term | Meaning |
|---|---|
| Slot | One hourly forecast entry for one spot (wave, wind, tide and weather values for that hour). |
| Current slot | The slot that is in effect right now (see FR-6). Its values are the "current conditions". |
| Window | Today's remaining daylight slots for a spot (see FR-6). |
| Good slot | A window slot where every rule passes. |
| Candidate | A spot whose window contains at least `min_good_hours` good slots. Only candidates are sent to Jev. |
| Recommendation | A candidate that Jev approved, or every candidate when the rules-only fallback is used (FR-11). |

## 2. Scope

### In scope

- A TOML config file (`surf_alert.toml`) holding the spot list, the rule list and settings, plus an example file `surf_alert.example.toml`.
- A CLI run as `.venv/bin/python -m surf_alert` with the commands `spots`, `rules`, `conditions`, `check` (with `--dry-run`) and `test-notification`.
- Downloading forecasts through `pysurfline.SpotForecast`, and converting units to the units the user configures.
- A deterministic rule engine that works on hourly slots.
- A Jev integration that calls the TypeSafe HTTP API directly with `requests`.
- WhatsApp delivery through the CallMeBot free personal API, behind a one-method `Notifier` interface.
- Secrets read from environment variables, with optional loading from a `.env` file. Also an `.env.example` file.
- `requirements.txt`.
- Instructions for scheduling a daily run (section 4.13). This is documentation only; the app installs nothing.

### Out of scope

- Adding or removing spots through the CLI. The user edits the TOML file.
- Searching Surfline for spot IDs. The user copies the ID from the Surfline URL.
- Per-spot rules. One rule list applies to every spot.
- Alerts about future days (for example tomorrow). Only "today" is evaluated.
- Remembering what was already sent. Running `check` twice in one day can send two messages.
- A long-running daemon or built-in scheduler, and installing cron or launchd entries.
- Other WhatsApp providers (Twilio, Meta Cloud API). The `Notifier` interface leaves room for them.
- Retries for any network call. Surfline premium access tokens.
- Sending error reports over WhatsApp. Errors go to stderr only.
- pandas DataFrames, charts, and `typesafe-sdk`.

## 3. Functional requirements

All examples use the reference config (section 5.1) and the reference scenario (section 5.6) unless they say otherwise.

### FR-1: Load and validate the config file

`load_config(path)` reads a TOML file (format in section 5.1) and returns an `AppConfig`. Any problem raises `ConfigError` with the exact message from section 6.1.

- AC-1.1: Given the reference config file, when `load_config(path)` is called, then it returns an `AppConfig` where `spots == (Spot("Pipeline", "5842041f4e65fad6a7708890", "Only surf it when it is under 6 ft."), Spot("Sunset Beach", "0123456789abcdef01234567", ""))`, `rules == (Rule("wave_height_min", ">", 2.0), Rule("swell_period", ">", 7.0))`, and `settings == Settings(notes="Intermediate surfer; prefer offshore or light winds.")`.
- AC-1.2: Given a path that does not exist, when `load_config(path)` is called, then it raises `ConfigError` with the message `config file not found: <path>`, where `<path>` is `str(path)`.
- AC-1.3: Given a file with invalid TOML syntax, when `load_config(path)` is called, then it raises `ConfigError` whose message starts with `invalid TOML in <path>: `.
- AC-1.4: Given a config that breaks any one constraint in section 6.1, when `load_config` or `parse_config` is called, then it raises `ConfigError` with exactly the message the table gives.
- AC-1.5: Given `height_unit = "FT"`, `wind_unit = "KPH"` and `temperature_unit = "f"`, when the config is parsed, then `settings.height_unit == "ft"`, `settings.wind_unit == "kph"` and `settings.temperature_unit == "F"`.
- AC-1.6: Given a rule `{metric = "swell_period", op = ">", value = 7}`, when the config is parsed, then `rule.value == 7.0` and `isinstance(rule.value, float)`. Given a rule `{metric = "wind_direction_type", op = "in", value = ["Offshore", "Cross-shore"]}`, then `rule.value == ("Offshore", "Cross-shore")` (a tuple).
- AC-1.7: Given a config with no `[settings]` table, when it is parsed, then `settings == Settings()`.
- AC-1.8: Given a spot with `name = "  Pipeline  "`, when it is parsed, then `spot.name == "Pipeline"`.

### FR-2: List the configured spots (`spots` command)

- AC-2.1: Given the reference config, when `main(["--config", path, "--env-file", missing_path, "spots"])` runs, then stdout is exactly `1. Pipeline (5842041f4e65fad6a7708890)\n2. Sunset Beach (0123456789abcdef01234567)\n`, the return value is `0`, and none of `make_surfline_client`, `make_jev_client` or `make_notifier` is called.
- AC-2.2: Given a config file that does not exist, when `spots` runs, then stderr is exactly `error: config file not found: <path>\n` and the return value is `1`.

### FR-3: List the configured rules (`rules` command) and describe rules

`describe_rule(rule, settings)` returns the text form of a rule given in section 4.6.

- AC-3.1: Given the reference config, when `main([... , "rules"])` runs, then stdout is exactly `1. wave_height_min > 2 ft\n2. swell_period > 7 s\n` and the return value is `0`.
- AC-3.2: Given these rules and default settings, `describe_rule` returns:
  - `Rule("wave_height_max", ">=", 2.5)` returns `wave_height_max >= 2.5 ft`
  - `Rule("wind_speed", "<", 15.0)` returns `wind_speed < 15 kts`
  - `Rule("temperature", ">=", 18.0)` returns `temperature >= 18°C`
  - `Rule("surf_rating", ">=", 1.0)` returns `surf_rating >= 1`
  - `Rule("wind_direction_type", "==", "Offshore")` returns `wind_direction_type == Offshore`
  - `Rule("wind_direction_type", "in", ("Offshore", "Cross-shore"))` returns `wind_direction_type in [Offshore, Cross-shore]`
- AC-3.3: Given `settings.height_unit == "m"` and `Rule("swell_height", ">", 1.0)`, then `describe_rule` returns `swell_height > 1 m`.

### FR-4: Fetch Surfline data

`SurflineClient.fetch(spot)` builds a forecast object with the forecast factory and turns it into a `ForecastData` with `parse_forecast`. The default factory is `pysurfline.SpotForecast` plus three direct `requests` GETs (`surf`, `swells` and `rating`), because Surfline retired the `wave` forecast that pysurfline requests (section 5.3).

- AC-4.1: Given a `SurflineClient` built with a fake `forecast_factory`, when `fetch(Spot("Pipeline", "5842041f4e65fad6a7708890"))` is called, then the factory is called exactly once, with the single positional argument `{"spotId": "5842041f4e65fad6a7708890", "days": 1, "intervalHours": 1}`.
- AC-4.2: Given a factory that prints `Error : 500` to stdout and then returns a valid raw object, when `fetch` is called, then nothing reaches the process's real stdout (pytest `capsys.readouterr().out == ""`). The output is captured with `contextlib.redirect_stdout` and only logged at DEBUG level.
- AC-4.3: Given a factory that raises any exception `exc` (for example `requests.ConnectionError("boom")`), when `fetch` is called, then it raises `SurflineError` with the message `failed to fetch forecast for Pipeline: boom`, which is `f"failed to fetch forecast for {spot.name}: {exc}"`.
- AC-4.4: Given a raw object with no `wave` attribute, or with `wave == []`, when `fetch` (or `parse_forecast`) runs, then it raises `SurflineError("no wave data returned for Pipeline")`. The same applies to `wind`, with the message `no wind data returned for Pipeline`.
- AC-4.5: Given a raw object with valid `wave`, `wind`, `units_wave` and `units_wind`, but no `tides`, `weather`, `rating`, `sunlightTimes`, `units_tides` or `units_weather` attributes, when it is parsed, then parsing succeeds, every slot has `tide_height`, `temperature`, `weather_condition` and `surf_rating` set to `None`, and `ForecastData.daylight == ()`.
- AC-4.6: Given that `pysurfline` is not installed and no factory is injected, when `fetch` is called, then it raises `SurflineError` whose message starts with `failed to fetch forecast for Pipeline: `. Importing any `surf_alert` module must not import `pysurfline` (the import is lazy).
- AC-4.7: Given no factory, a `pysurfline.SpotForecast` that returns an object without `wave`, `units_wave` or `rating`, and a session that answers the `surf`, `swells` and `rating` forecasts, when `fetch` is called, then the session gets exactly three GETs, in that order, to `KBYG_URL.format(kind)` with the same `params` and `timeout`, and the result equals what `parse_forecast` makes of the rebuilt `wave`, `units_wave` and `rating` (section 4.5).
- AC-4.8: Given no factory and a session whose `surf`, `swells` or `rating` GET answers a non-200 status (for example 404), when `fetch` is called, then it raises `SurflineError("failed to fetch forecast for Pipeline: Surfline returned HTTP 404 for the surf forecast")` (with the matching kind). A request exception or a body that is not JSON is wrapped the same way as in AC-4.3.

### FR-5: Normalize forecast data

`parse_forecast(raw, spot, settings)` converts the raw pysurfline attributes into slots in the configured units. The rules are in section 4.5.

- AC-5.1: Given `units_wave == {"waveHeight": "M", "swellHeight": "M"}`, `settings.height_unit == "ft"` and a wave entry with `surf_min = 1.0`, when it is parsed, then `slot.surf_min == pytest.approx(3.28084, abs=1e-6)`.
- AC-5.2: Given `units_wind == {"windSpeed": "KPH"}`, `settings.wind_unit == "kts"`, `speed = 18.52` and `gust = 37.04`, then `wind_speed == approx(10.0, abs=1e-6)` and `wind_gust == approx(20.0, abs=1e-6)`. Given `"MPH"` and `speed = 11.50779`, then `wind_speed == approx(10.0, abs=1e-4)`.
- AC-5.3: Given `units_weather == {"temperature": "F"}`, `settings.temperature_unit == "C"` and `temperature = 68.0`, then `slot.temperature == approx(20.0, abs=1e-9)`.
- AC-5.4: Given swells `[{height 1.1, period 7, direction 20}, {height 4.2, period 11, direction 315}, {height 0, period 0, direction 0}]` in FT with `height_unit "ft"`, then `slot.primary_swell == Swell(height=4.2, period=11.0, direction=315.0)`. Given swells that all have height `0`, or an empty or missing `swells` list, then `slot.primary_swell is None`. If two swells share the largest height, the first one in list order wins.
- AC-5.5: Given a wave slot at timestamp `T` and wind entries at `T - 3600` and `T + 1800`, then the slot uses the `T + 1800` entry, because it is nearest. Given wind entries only at `T - 3601` and `T + 3700`, then the slot's wind fields (`wind_speed`, `wind_gust`, `wind_direction`, `wind_direction_type`) are all `None`. When two entries are equally near, the earlier one wins. The same rule applies to tides, weather and ratings.
- AC-5.6: Given `units_wave == {"waveHeight": "FATHOMS"}`, then `SurflineError("unsupported waveHeight unit for Pipeline: 'FATHOMS'")` is raised. Given a `units_wave` without `waveHeight`, the message ends with `: None`. The same applies to `windSpeed`.
- AC-5.7: Given `units_tides == {"tideHeight": "XX"}`, then parsing succeeds and every slot has `tide_height is None`. Given `units_weather == {"temperature": "XX"}`, then parsing succeeds, every slot has `temperature is None`, and `weather_condition` is still filled in.
- AC-5.8: Given wave entries whose timestamps are out of order, then `ForecastData.slots` is sorted by ascending timestamp.
- AC-5.9: Given a wave entry missing `surf_max`, then `SurflineError` is raised and its message starts with `malformed forecast data for Pipeline: `.
- AC-5.10: Given `units_wave == {"waveHeight": "FT"}` with no `swellHeight` key, then swell heights are treated as FT.

### FR-6: Current conditions and today's window

- AC-6.1: Given slots at 16:00Z, 17:00Z and 18:00Z, and `now = 17:30Z`, when `current_slot(slots, now)` is called, then it returns the 17:00Z slot. Given `now` earlier than the first slot, it returns the first slot. Given `now` later than the last slot, it returns the last slot.
- AC-6.2: Given an empty slot list, `current_slot` raises `ValueError("no slots")`. Given a naive `now` (no `tzinfo`), `current_slot` and `evaluate_spot` raise `ValueError("now must be timezone-aware")`.
- AC-6.3: Given the reference Pipeline forecast (utcOffset -10, dawn 06:10 local, dusk 18:40 local on 2026-10-07, hourly slots from 00:00 to 23:00 local) and `now = 2026-10-07T17:30:00Z` (07:30 local), when `evaluation_window(forecast, now)` is called, then it returns `(date(2026, 10, 7), slots)`, where `slots` holds exactly the slots starting at 07:00 through 18:00 local (12 slots).
- AC-6.4: Given the same forecast but `ForecastData.daylight == ()`, then the window holds the slots starting at 07:00 through 19:00 local (13 slots), because the fallback daylight hours are 06 to 19 inclusive.
- AC-6.5: Given `now = 2026-10-08T05:00:00Z` (19:00 local on 2026-10-07, after dusk), then the window is empty, `good_slots == ()` and `passes_rules is False`.
- AC-6.6: "Today" is the spot's local date of `now`, using the current slot's `utc_offset`. Slots whose local date is a different day are never in the window.

### FR-7: Rule evaluation

- AC-7.1: For a numeric metric, `rule_passes(Rule(m, op, v), slot)` returns `metric_value(slot, m) <op> v` for `op` in `>`, `>=`, `<`, `<=`, `==` and `!=`. Example: `swell_period` 7.0 with `> 7` gives `False`; with `>= 7` it gives `True`.
- AC-7.2: For a text metric, comparisons ignore case (`str.casefold`). `wind_direction_type == "offshore"` passes when the slot holds `"Offshore"`. `in ("offshore", "cross-shore")` passes for `"Cross-shore"`. `not_in ("onshore",)` passes for `"Offshore"`.
- AC-7.3: When `metric_value(slot, m)` is `None`, every operator returns `False`, including `!=` and `not_in`.
- AC-7.4: `slot_passes(rules, slot)` is `True` only when every rule passes. For an empty rule list it is `True`.
- AC-7.5: `evaluate_spot` sets `good_slots` to the window slots for which `slot_passes` is `True`, in timestamp order, and sets `passes_rules = len(good_slots) >= settings.min_good_hours`. For the reference Pipeline forecast: `current` is the 07:00 local slot, `window` has 12 slots, `good_slots` holds the 07:00–11:00 slots (5), and `passes_rules is True`.
- AC-7.6: `metric_value(slot, "nope")` raises `ValueError("unknown metric: nope")`.

### FR-8: Build the Jev request

The rules act as a hard pre-filter: only candidates go to Jev, and Jev can never approve a spot that failed the rules. Jev also receives the rules as text, the full window for each candidate, and the user's notes. It decides whether each candidate is worth going to, and which one is best.

- AC-8.1: Given one candidate, when `build_jev_request([ev], rules, settings)` is called, then `questions` has exactly one key, `good_spot_1`, with `type == "noul"` and `instructions == NOUL_TEMPLATE.format(name=<spot name>, ref="spot_1")`. There is no `best_spot` key.
- AC-8.2: Given 2 or more candidates, then `questions` has the keys `good_spot_1` through `good_spot_n` in that order, followed by `best_spot` with `type == "choice"`, `instructions == BEST_SPOT_INSTRUCTIONS`, and `criteria == {"spot_1": <name 1>, ..., "spot_n": <name n>}`.
- AC-8.3: The returned `state` matches the schema in section 5.4 exactly: same keys, rounding, `None` for missing values, and local `HH:MM` times.
- AC-8.4: Given an empty candidate list, `build_jev_request` raises `ValueError("no candidate spots")`.

### FR-9: Jev HTTP client

- AC-9.1: Given `JevClient("k", model="jev-latest", session=fake)`, when `ask(state, questions)` is called, then it calls `fake.post` exactly once, as `fake.post("https://api.typesafe.ai/v1/systemone", headers={"Authorization": "Bearer k", "Content-Type": "application/json"}, json={"model": "jev-latest", "state": state, "questions": questions}, timeout=30.0)`.
- AC-9.2: Given a 200 response whose JSON body is `{"model": "...", "answers": A, "usage": {...}}`, then `ask` returns `A`.
- AC-9.3: Given an HTTP status of 401, 422, 429, 529 or anything else outside 2xx, then `ask` raises `JevError` with the message from section 6.3, and `exc.status_code` set to that status.
- AC-9.4: Given `fake.post` raising `requests.Timeout("t")`, then `ask` raises `JevError("request failed: t")` with `status_code is None`.
- AC-9.5: Given a 200 response whose body is not JSON, or is JSON without an `answers` object, then `ask` raises `JevError`. The message is `malformed response: body is not valid JSON` or `malformed response: missing 'answers' object`, and `status_code` is `200`.
- AC-9.6: `JevClient("")` raises `ValueError("api_key must not be empty")`. `ask(state, {})` raises `ValueError("questions must not be empty")` without making an HTTP call.

### FR-10: Decide from Jev's answers

- AC-10.1: Given 2 candidates (Pipeline first, Sunset Beach second), `jev_threshold = 0.7`, and answers `good_spot_1.noul = 0.92`, `good_spot_2.noul = 0.81` and `best_spot.choice = "spot_1"`, when `interpret_jev_answers` runs, then the `Decision` has `jev_used=True`, `jev_error=None`, and recommendations `[(Pipeline, 0.92), (Sunset Beach, 0.81)]`.
- AC-10.2: A candidate is approved only when its probability is `>= jev_threshold`. With threshold 0.7, a probability of exactly 0.7 is approved and 0.69 is not.
- AC-10.3: Given that `best_spot.choice` names an approved candidate, that candidate comes first, even if another approved candidate has a higher probability. The other approved candidates follow, by descending probability, with ties broken by config order.
- AC-10.4: Given that `best_spot` is missing, is not a dict, has no `choice`, names an unknown ref, or names a candidate that was not approved, then the approved candidate with the highest probability comes first (ties go to the earlier one in config order). This is not an error.
- AC-10.5: Given that no candidate reaches the threshold, then the result is `Decision(recommendations=(), jev_used=True, jev_error=None)`.
- AC-10.6: Given that any `good_spot_i` answer is missing, is not a dict, or has a `noul` that is not a real number in `[0, 1]` (an `int` or `float`, not a `bool`, not NaN; an `int` 0 or 1 is valid and stored as `float`), then `interpret_jev_answers` raises `JevError("malformed response: missing or invalid answer for good_spot_<i>")`, using the first bad `i`.

### FR-11: Jev failure handling

`decide(candidates, rules, settings, jev)` calls `build_jev_request`, then `jev.ask`, then `interpret_jev_answers`. It catches only `JevError`.

- AC-11.1: Given `on_jev_error == "rules_only"` and `jev.ask` (or `interpret_jev_answers`) raising `JevError("rate limited (HTTP 429)")`, then `decide` returns `Decision(recommendations=<every candidate, probability None>, jev_used=False, jev_error="rate limited (HTTP 429)")`. The recommendations are ordered by number of good slots (most first), with ties broken by config order.
- AC-11.2: Given `on_jev_error == "skip"` and a `JevError`, then `decide` returns `Decision(recommendations=(), jev_used=False, jev_error=<message>)`.
- AC-11.3: Exceptions that are not `JevError` propagate out of `decide`.

### FR-12: Format the notification message

- AC-12.1: Given the reference decision (section 5.6), `format_message(decision, Settings())` returns exactly the reference message in section 5.5.
- AC-12.2: Given 5 recommendations and `max_spots_in_message == 3`, the message has the header, 3 spot blocks (the 2nd and 3rd start with `Also good: `), and then the paragraph `More good spots: <name 4>, <name 5>`.
- AC-12.3: Given `decision.jev_used is False`, the message ends with the paragraph `Note: Jev was unavailable, so this recommendation is based on your rules only.`, and no block has a `Jev confidence:` line.
- AC-12.4: Given a current slot with `tide_height None`, the block has no `Tide:` line. With `primary_swell None` it has no `Swell:` line. With `temperature None` and `weather_condition None` it has no `Weather:` line. With `wind_speed None` it has no `Wind:` line. With only one of temperature and condition present, the line is `Weather: 24°C` or `Weather: Mostly clear`.
- AC-12.5: Given good slots at local 08:00, 09:00, 10:00, 15:00 and 16:00, then `format_hours` returns `08:00-11:00, 15:00-17:00`. Given no good slots, it returns `none`.
- AC-12.6: `compass(0) == "N"`, `compass(11.24) == "N"`, `compass(11.25) == "NNE"`, `compass(315) == "NW"`, `compass(359) == "N"`, `compass(360) == "N"`.
- AC-12.7: `format_message` with an empty `recommendations` raises `ValueError("no recommendations")`.

### FR-13: Send WhatsApp messages through CallMeBot

- AC-13.1: Given `CallMeBotNotifier("+34612345678", "SECRET", session=fake)`, when `send("hi")` is called, then `fake.get` is called exactly once, as `fake.get("https://api.callmebot.com/whatsapp.php", params={"phone": "+34612345678", "text": "hi", "apikey": "SECRET"}, timeout=30.0)`.
- AC-13.2: Given a response with `status_code == 200`, `send` returns `None`.
- AC-13.3: Given `status_code == 203` and `text == "APIKey is invalid"`, `send` raises `NotificationError("CallMeBot returned HTTP 203: APIKey is invalid")`.
- AC-13.4: Given `fake.get` raising `requests.ConnectionError("failed url=/whatsapp.php?apikey=SECRET")`, `send` raises `NotificationError("request to CallMeBot failed: failed url=/whatsapp.php?apikey=***")`. The API key never appears in any `NotificationError` message.
- AC-13.5: `send("")` and `send("   ")` raise `ValueError("message text must not be empty")` without making an HTTP call.
- AC-13.6: A phone value that does not match `^\+[1-9][0-9]{6,14}$` (for example `"0612345678"`) makes the constructor raise `ValueError("WHATSAPP_PHONE must be in international format, e.g. +34612345678")`. An empty or whitespace-only `api_key` raises `ValueError("CALLMEBOT_API_KEY must not be empty")`.

### FR-14: Daily check (`check` command and `run_check`)

- AC-14.1 (no candidates): Given that every spot fetches but none passes the rules, when `run_check` runs, then `jev.ask` is not called, `notifier.send` is not called, and the result has `decision=None`, `message=None`, `sent=False`, `send_error=None` and `exit_code=0`. The CLI prints `No good spots today; no notification sent.\n` to stdout and returns `0`.
- AC-14.2 (send): Given that Pipeline is the only candidate and Jev returns `good_spot_1.noul = 0.9`, then `notifier.send` is called exactly once, with `format_message(result.decision, config.settings)`. The result has `sent=True` and `exit_code=0`. The CLI prints `Notification sent: 1 spot(s) recommended.\n` and returns `0`.
- AC-14.3 (Jev rejects everything): Given candidates and Jev probabilities all below the threshold, then nothing is sent, `message=None` and `exit_code=0`. The CLI prints `No good spots today; no notification sent.\n`.
- AC-14.4 (one spot fails): Given that Pipeline's fetch raises `SurflineError("no wave data returned for Pipeline")` and Sunset Beach is approved, then a message about Sunset Beach only is sent, `failures == {"Pipeline": "no wave data returned for Pipeline"}` and `exit_code == 3`. The CLI prints `warning: Pipeline: no wave data returned for Pipeline\n` to stderr and returns `3`.
- AC-14.5 (all spots fail): Given that every fetch fails, then Jev is not called, nothing is sent, `exit_code == 3`, and stdout is `No good spots today; no notification sent.\n`.
- AC-14.6 (Jev fails, rules-only fallback): Given candidates, `on_jev_error == "rules_only"` and `jev.ask` raising `JevError("service overloaded (HTTP 529)")`, then a message ending with the rules-only note is sent and `exit_code == 3`. The CLI prints `warning: Jev request failed: service overloaded (HTTP 529)\n` to stderr.
- AC-14.7 (send fails): Given that `notifier.send` raises `NotificationError("CallMeBot returned HTTP 500: x")`, then the result has `sent=False`, `send_error="CallMeBot returned HTTP 500: x"` and `exit_code=4`. The CLI prints `error: failed to send WhatsApp message: CallMeBot returned HTTP 500: x\n` to stderr and returns `4`. Exit code 4 takes priority over 3.
- AC-14.8 (dry run): Given `check --dry-run` and an approved spot, then `make_notifier` is not called, `WHATSAPP_PHONE` and `CALLMEBOT_API_KEY` are not required, and stdout is the message text followed by `\n`. The return value is `0` (or `3` under the conditions of AC-14.4 and AC-14.6).
- AC-14.9 (missing secrets): Given `WHATSAPP_PHONE=+34612345678` set, and both `TYPESAFE_API_KEY` and `CALLMEBOT_API_KEY` unset (or set to empty strings), when `check` runs without `--dry-run`, then stderr starts with the line `error: missing environment variable(s): TYPESAFE_API_KEY, CALLMEBOT_API_KEY`, followed by one line per missing variable and a closing line as described in 4.12 (`check` step 2), the return value is `1`, and no factory is called. Variables are listed in the fixed order `TYPESAFE_API_KEY`, `WHATSAPP_PHONE`, `CALLMEBOT_API_KEY`, and only missing ones appear.
- AC-14.10 (bad phone): Given `WHATSAPP_PHONE=0612345678`, the other variables set, and the real `make_notifier`, then stderr is `error: WHATSAPP_PHONE must be in international format, e.g. +34612345678\n`, the return value is `1`, and no Surfline fetch happens.
- AC-14.11: `run_check` passes the same `now` to the evaluation of every spot.

### FR-15: Print current conditions (`conditions` command)

- AC-15.1: Given the reference config and fake fetches that succeed for both spots, when `conditions` runs, then stdout is one section per spot in config order. Each section is `format_spot_block(evaluation, settings)` + `"\n"` + `format_rules_status(evaluation, settings)`. Sections are separated by one blank line, and the output ends with `\n`. The return value is `0`. `make_jev_client` and `make_notifier` are never called.
- AC-15.2: Given `conditions --spot "sunset beach"`, only Sunset Beach is fetched and printed (the name match ignores case).
- AC-15.3: Given `conditions --spot Nowhere`, stderr is exactly `error: unknown spot: Nowhere\n`, the return value is `1`, and nothing is fetched.
- AC-15.4: Given one failed fetch, its warning line goes to stderr (same format as AC-14.4), the other spots still print, and the return value is `3`.
- AC-15.5: `format_rules_status` returns `Rules: PASS (good hours today: 5, required: 1)` when `passes_rules` is true, and `Rules: FAIL (good hours today: 0, required: 1)` when it is false.

### FR-16: Test the WhatsApp setup (`test-notification` command)

- AC-16.1: Given valid `WHATSAPP_PHONE` and `CALLMEBOT_API_KEY`, when `test-notification` runs, then `make_notifier(phone, key)` is called, `send` is called once with `Surf alert test message: WhatsApp notifications are working.`, stdout is `Test notification sent.\n`, and the return value is `0`. The config file is not read, so it may be missing.
- AC-16.2: Given that `send` raises `NotificationError(m)`, stderr is `error: failed to send WhatsApp message: <m>\n` and the return value is `4`.
- AC-16.3: Given missing variables, the AC-14.9 message lists `WHATSAPP_PHONE` and/or `CALLMEBOT_API_KEY` (in that order), and the return value is `1`. `TYPESAFE_API_KEY` is not required.

### FR-17: Secrets and `.env`

- AC-17.1: Given an env file containing `TYPESAFE_API_KEY=fromfile`, and the process environment already holding `TYPESAFE_API_KEY=fromenv`, when `main([... "--env-file", f, "check", ...])` runs, then `make_jev_client` receives `"fromenv"` (real environment variables win).
- AC-17.2: Given an env file containing `TYPESAFE_API_KEY=fromfile`, and the variable not set in the process environment, then `make_jev_client` receives `"fromfile"`.
- AC-17.3: Given `--env-file` pointing to a missing file, no error is raised and only the process environment is used.
- AC-17.4: Secrets never appear on stdout or stderr, or in any exception message the app creates.

## 4. Public interface

Everything below is a contract. Names, signatures, return types, messages and exit codes must match exactly. All modules live in the package `surf_alert/` at the project root.

### 4.1 File layout

```
surf_alert/
  __init__.py        # __version__ = "0.1.0"; nothing else required
  __main__.py        # from surf_alert.cli import main; raise SystemExit(main())
  errors.py          # exception classes
  models.py          # all dataclasses
  config.py          # METRICS, ops, parse_config, load_config
  units.py           # unit conversion
  surfline.py        # SurflineClient, parse_forecast, ForecastSource
  evaluate.py        # windowing and rule engine, describe_rule
  jev.py             # JevClient, JevLike
  decision.py        # build_jev_request, interpret_jev_answers, decide
  message.py         # message formatting
  notify.py          # Notifier, CallMeBotNotifier
  app.py             # evaluate_spots, run_check
  report.py          # -v explanation and the per-spot CSV table (4.14)
  cli.py             # main + dependency factories
surf_alert.example.toml   # the reference config (section 5.1) with comments
.env.example              # see section 5.2
README.md                 # setup, CallMeBot activation, commands, daily scheduling (section 4.13)
requirements.txt          # see section 7
tests/                    # owned by the test agents
```

### 4.2 `surf_alert/errors.py`

```python
class SurfAlertError(Exception): ...            # base class
class ConfigError(SurfAlertError): ...
class SurflineError(SurfAlertError): ...
class JevError(SurfAlertError):
    def __init__(self, message: str, status_code: int | None = None) -> None: ...
    status_code: int | None                      # HTTP status, or None for transport and answer errors
class NotificationError(SurfAlertError): ...
```

For every class, `str(exc)` is exactly the message passed in.

### 4.3 `surf_alert/models.py`

All dataclasses are `@dataclass(frozen=True)`. Fields appear in the order shown and have no defaults unless shown. Equality is the default dataclass equality.

```python
@dataclass(frozen=True)
class Spot:
    name: str
    spot_id: str              # TOML key is `id`
    notes: str = ""

@dataclass(frozen=True)
class Rule:
    metric: str               # a key of config.METRICS
    op: str                   # one of config.ALL_OPS
    value: float | str | tuple[str, ...]
    # float for numeric metrics; str for text metrics with ==/!=; tuple[str, ...] for in/not_in

@dataclass(frozen=True)
class Settings:
    height_unit: str = "ft"            # "ft" | "m"
    wind_unit: str = "kts"             # "kts" | "kph" | "mph"
    temperature_unit: str = "C"        # "C" | "F"
    min_good_hours: int = 1            # 1..24
    jev_threshold: float = 0.7         # 0 < x <= 1
    jev_model: str = "jev-latest"
    on_jev_error: str = "rules_only"   # "rules_only" | "skip"
    max_spots_in_message: int = 3      # 1..10
    notes: str = ""

@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    spots: tuple[Spot, ...]
    rules: tuple[Rule, ...]

@dataclass(frozen=True)
class Swell:
    height: float             # configured height unit
    period: float             # seconds
    direction: float          # degrees, direction the swell comes FROM

@dataclass(frozen=True)
class Slot:
    timestamp: int            # epoch seconds, UTC, start of the hour
    utc_offset: float         # hours, e.g. -10.0 or 5.5
    surf_min: float           # configured height unit
    surf_max: float
    surf_rating: int | None   # Surfline rating, 0 (very poor) to 6 (epic)
    primary_swell: Swell | None
    wind_speed: float | None  # configured wind unit
    wind_gust: float | None
    wind_direction: float | None        # degrees
    wind_direction_type: str | None     # e.g. "Offshore", "Cross-shore", "Onshore"
    tide_height: float | None           # configured height unit
    temperature: float | None           # configured temperature unit
    weather_condition: str | None       # raw Surfline value, e.g. "MOSTLY_CLEAR"

    def local_datetime(self) -> datetime:
        """Naive datetime of the spot's local wall-clock time:
        datetime.fromtimestamp(self.timestamp + round(self.utc_offset * 3600), tz=timezone.utc).replace(tzinfo=None)"""

@dataclass(frozen=True)
class DaylightWindow:
    local_date: date
    dawn: int                 # epoch seconds UTC
    dusk: int                 # epoch seconds UTC

@dataclass(frozen=True)
class ForecastData:
    spot: Spot
    slots: tuple[Slot, ...]               # ascending timestamp; never empty
    daylight: tuple[DaylightWindow, ...]  # may be empty

@dataclass(frozen=True)
class SpotEvaluation:
    spot: Spot
    local_date: date              # the spot's "today"
    current: Slot
    window: tuple[Slot, ...]      # today's remaining daylight slots, ascending
    good_slots: tuple[Slot, ...]  # subset of window where every rule passes, ascending
    passes_rules: bool            # len(good_slots) >= settings.min_good_hours

@dataclass(frozen=True)
class Recommendation:
    evaluation: SpotEvaluation
    probability: float | None     # Jev "noul" value; None under the rules-only fallback

@dataclass(frozen=True)
class Decision:
    recommendations: tuple[Recommendation, ...]   # best first; may be empty
    jev_used: bool
    jev_error: str | None = None

@dataclass(frozen=True)
class RunResult:
    evaluations: tuple[SpotEvaluation, ...]   # successfully fetched spots, config order
    failures: dict[str, str]                  # spot name -> error message, config order
    decision: Decision | None                 # None when there were no candidates (Jev not called)
    message: str | None                       # None when there is nothing to notify
    sent: bool
    send_error: str | None
    exit_code: int                            # 0, 3 or 4 (see 4.11)
```

(`RunResult` is frozen, but `failures` is a plain dict. That is acceptable.)

### 4.4 `surf_alert/config.py`

```python
METRICS: dict[str, str]   # metric name -> kind, in exactly this insertion order:
# "wave_height_min": "height", "wave_height_max": "height", "surf_rating": "score",
# "swell_height": "height", "swell_period": "period", "wind_speed": "speed", "wind_gust": "speed",
# "wind_direction_type": "text", "tide_height": "height", "temperature": "temperature",
# "weather_condition": "text"

NUMERIC_OPS: tuple[str, ...] = (">", ">=", "<", "<=", "==", "!=")
TEXT_OPS: tuple[str, ...] = ("==", "!=", "in", "not_in")
ALL_OPS: tuple[str, ...] = (">", ">=", "<", "<=", "==", "!=", "in", "not_in")
MAX_SPOTS: int = 50

def parse_config(data: dict) -> AppConfig
    """Validate an already-parsed TOML document. Raises ConfigError (messages in 6.1)."""

def load_config(path: str | os.PathLike) -> AppConfig
    """Read the file with tomllib (binary mode), then call parse_config.
    Raises ConfigError for a missing file, invalid TOML, or invalid content."""
```

Metric kinds: `height`, `period`, `speed`, `temperature` and `score` are numeric and allow `NUMERIC_OPS`. `text` allows `TEXT_OPS`.

How each metric maps to a slot value (used by `evaluate.metric_value`):

| Metric | Value |
|---|---|
| `wave_height_min` | `slot.surf_min` |
| `wave_height_max` | `slot.surf_max` |
| `surf_rating` | `slot.surf_rating` |
| `swell_height` | `slot.primary_swell.height`, or `None` if there is no primary swell |
| `swell_period` | `slot.primary_swell.period`, or `None` |
| `wind_speed` | `slot.wind_speed` |
| `wind_gust` | `slot.wind_gust` |
| `wind_direction_type` | `slot.wind_direction_type` |
| `tide_height` | `slot.tide_height` |
| `temperature` | `slot.temperature` |
| `weather_condition` | `slot.weather_condition` |

### 4.5 `surf_alert/units.py` and `surf_alert/surfline.py`

`units.py`:

```python
FT_PER_M: float = 3.28084
KPH_PER_KT: float = 1.852
MPH_PER_KT: float = 1.150779

def convert_height(value: float, from_unit: str, to_unit: str) -> float
    # units "ft" | "m", case-insensitive; ValueError(f"unsupported height unit: {u!r}") otherwise
def convert_speed(value: float, from_unit: str, to_unit: str) -> float
    # units "kts" | "kph" | "mph", case-insensitive; converts through knots;
    # ValueError(f"unsupported speed unit: {u!r}")
def convert_temperature(value: float, from_unit: str, to_unit: str) -> float
    # units "c" | "f", case-insensitive; F->C: (v - 32) * 5 / 9; C->F: v * 9 / 5 + 32;
    # ValueError(f"unsupported temperature unit: {u!r}")
```

Both units are validated first (`from_unit`, then `to_unit`). Only after both are valid, when they are the same (ignoring case), the value is returned unchanged. So `convert_height(1, "yd", "yd")` raises `ValueError("unsupported height unit: 'yd'")`. `u` in the messages is the first unsupported argument, exactly as given.

`surfline.py`:

```python
FORECAST_DAYS: int = 1
FORECAST_INTERVAL_HOURS: int = 1
MATCH_TOLERANCE_SECONDS: int = 3600
KBYG_URL: str = "https://services.surfline.com/kbyg/spots/forecasts/{}"
DEFAULT_TIMEOUT: float = 30.0

class ForecastSource(Protocol):
    def fetch(self, spot: Spot) -> ForecastData: ...

class SurflineClient:
    def __init__(self, settings: Settings,
                 forecast_factory: Callable[[dict], object] | None = None, *,
                 session: requests.Session | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None
        # forecast_factory=None means the default factory, run inside fetch():
        #   1. lazily `from pysurfline import SpotForecast`; raw = SpotForecast(params, verbose=False)
        #   2. for kind in "surf", "swells", "rating" (in that order):
        #      session.get(KBYG_URL.format(kind), params=params, timeout=timeout).
        #      A status other than 200 raises
        #      SurflineError(f"Surfline returned HTTP {code} for the {kind} forecast").
        #      The entries are body["data"][kind] and the units are body["associated"]["units"]
        #      (a missing or wrong-typed level counts as [] or {}).
        #   3. raw.wave = the surf entries rebuilt in pysurfline's flattened shape: each surf
        #      entry without its "surf" dict, plus "surf_<key>" for each key of that dict
        #      ("surf_min", "surf_max", ...), plus "swells" from the swells entry nearest its
        #      timestamp (the step 6 matching rule; no match leaves "swells" out). A surf entry
        #      that is not a dict or has no "surf" dict is kept unchanged, so step 4 rejects it.
        #   4. raw.units_wave = {"waveHeight": surf units' waveHeight}, plus "swellHeight" when
        #      the swells units have it; raw.rating = the rating entries.
        # session=None means a new requests.Session(). session and timeout are only used by
        # the default factory.
    def fetch(self, spot: Spot) -> ForecastData
        # params = {"spotId": spot.spot_id, "days": FORECAST_DAYS, "intervalHours": FORECAST_INTERVAL_HOURS}
        # raw = factory(params), called inside contextlib.redirect_stdout(io.StringIO());
        #   captured text is logged at DEBUG.
        # Any exception raised by the factory (including ImportError and the default
        #   factory's SurflineError) ->
        #   SurflineError(f"failed to fetch forecast for {spot.name}: {exc}")
        # returns parse_forecast(raw, spot, settings); its SurflineErrors propagate unchanged.

def parse_forecast(raw: object, spot: Spot, settings: Settings) -> ForecastData
```

`parse_forecast` reads attributes with `getattr(raw, name, None)`, so it works on a real `pysurfline.SpotForecast` and on a `types.SimpleNamespace`. "Numeric" below means `int` or `float` but not `bool`, and "int" means `int` but not `bool`, everywhere in these steps. Steps, in order:

1. `wave = getattr(raw, "wave", None)`. If it is not a non-empty list, raise `SurflineError(f"no wave data returned for {spot.name}")`. Do the same for `wind`, with the message `no wind data returned for {spot.name}`.
2. Required units. Treat a `units_wave`/`units_wind` that is not a dict as `{}`.
   - `wave_unit = units_wave.get("waveHeight")`. It must be a str equal to `FT` or `M` (any case), otherwise raise `SurflineError(f"unsupported waveHeight unit for {spot.name}: {wave_unit!r}")`.
   - `swell_unit = units_wave.get("swellHeight", wave_unit)`, checked the same way (`unsupported swellHeight unit for ...`).
   - `wind_unit = units_wind.get("windSpeed")`. It must be `KTS`, `KPH` or `MPH` (any case), otherwise raise `SurflineError(f"unsupported windSpeed unit for {spot.name}: {wind_unit!r}")`.
3. Optional units. Treat a `units_tides`/`units_weather` that is not a dict as `{}`.
   - `units_tides.get("tideHeight")`: if it is missing or not `FT`/`M`, ignore all tide data (every `tide_height` is `None`).
   - `units_weather.get("temperature")`: if it is missing or not `C`/`F`, every `temperature` is `None`. `weather_condition` is still read.
4. Slots: one per wave entry. Required keys: `timestamp` (int, not bool), `utcOffset` (numeric), `surf_min` (numeric) and `surf_max` (numeric). If an entry is not a dict, or any of these is missing or the wrong type, raise `SurflineError(f"malformed forecast data for {spot.name}: <detail>")`. `<detail>` is free text, for example `wave entry 3 missing 'surf_max'`. `surf_min`/`surf_max` are converted from `wave_unit` to `settings.height_unit`, and `utc_offset` is `float(utcOffset)`.
5. Primary swell: among `entry.get("swells") or []`, ignore entries that are not dicts or that lack a numeric `height`, `period` or `direction`. Ignore entries with `height <= 0`. Pick the largest `height`; on a tie, the first one wins. Convert its height from `swell_unit` to `settings.height_unit`. Store `period` and `direction` as floats. If no swell is left, the primary swell is `None`.
6. Matching wind, tide, weather and rating: for each source list (a missing or non-list attribute counts as `[]`), ignore entries that are not dicts or have no int `timestamp`. For a slot at `t`, pick the entry with the smallest `abs(e["timestamp"] - t)`, but only if that distance is `<= MATCH_TOLERANCE_SECONDS`. On a tie, the earlier timestamp wins. With no match, the fields are `None`.
   - Wind: a numeric `speed` and `gust` are converted from `wind_unit` to `settings.wind_unit`; a numeric `direction` becomes a float; a str `directionType` is kept as is. Any key that is missing or the wrong type becomes `None`.
   - Tide: only entries with a numeric `height` are eligible (filter before matching). The height is converted from the tide unit to `settings.height_unit`.
   - Weather: a numeric `temperature` is converted to `settings.temperature_unit` (only when the temperature unit is valid); a str `condition` is kept as is. Each key independently becomes `None` when missing or the wrong type.
   - Rating (`getattr(raw, "rating", None)`): `surf_rating` is the entry's `rating["value"]` when `rating` is a dict and that value is an int (not bool), otherwise `None`.
7. Daylight: for each dict in `getattr(raw, "sunlightTimes", None) or []` that has int `midnight`, `dawn` and `dusk` and a numeric `midnightUTCOffset`, set `local_date = datetime.fromtimestamp(midnight + round(midnightUTCOffset * 3600), tz=timezone.utc).date()` and create `DaylightWindow(local_date, dawn, dusk)`. Skip other entries.
8. Return `ForecastData(spot, slots sorted by ascending timestamp, daylight tuple in source order)`.

### 4.6 `surf_alert/evaluate.py`

```python
FALLBACK_DAYLIGHT_HOURS: tuple[int, int] = (6, 19)   # inclusive local start hours

def local_date_of(timestamp: int, utc_offset: float) -> date
    # datetime.fromtimestamp(timestamp + round(utc_offset * 3600), tz=timezone.utc).date()

def current_slot(slots: Sequence[Slot], now: datetime) -> Slot
    # checked in this order:
    # ValueError("now must be timezone-aware") if now.tzinfo is None or now.utcoffset() is None
    # ValueError("no slots") if slots is empty
    # the slot with the greatest timestamp <= now.timestamp(); if none, the first slot

def evaluation_window(forecast: ForecastData, now: datetime) -> tuple[date, tuple[Slot, ...]]
    # cur = current_slot(forecast.slots, now)
    # today = local_date_of(int(now.timestamp()), cur.utc_offset)
    # window = slots s where local_date_of(s.timestamp, s.utc_offset) == today
    #          and s.timestamp >= cur.timestamp
    #          and daylight(s)
    # daylight(s): if a DaylightWindow with local_date == today exists (use the first),
    #              dawn <= s.timestamp <= dusk;
    #              otherwise 6 <= s.local_datetime().hour <= 19
    # returns (today, window in ascending order)

def metric_value(slot: Slot, metric: str) -> float | int | str | None
    # mapping in 4.4; ValueError(f"unknown metric: {metric}") for anything else

def rule_passes(rule: Rule, slot: Slot) -> bool
    # value None -> False for every op.
    # numeric: standard Python comparison against float(rule.value).
    # text ==, !=: compare casefolded strings; in / not_in: casefolded membership in rule.value.

def slot_passes(rules: Sequence[Rule], slot: Slot) -> bool    # all(...); True for empty rules

def evaluate_spot(forecast: ForecastData, rules: Sequence[Rule], settings: Settings,
                  now: datetime) -> SpotEvaluation
    # current = current_slot(...); (local_date, window) = evaluation_window(...);
    # good_slots = window slots passing slot_passes; passes_rules per AC-7.5

def describe_rule(rule: Rule, settings: Settings) -> str
```

`describe_rule` format:
- Numeric rule: `f"{metric} {op} {value:g}{suffix}"`. The suffix depends on the metric kind:
  - `height`: `" ft"` or `" m"`
  - `period`: `" s"`
  - `speed`: `" kts"`, `" kph"` or `" mph"`
  - `temperature`: `"°C"` or `"°F"` (no space)
  - `score`: `""`
- Text rule with `==` or `!=`: `f"{metric} {op} {value}"`.
- Text rule with `in` or `not_in`: `f"{metric} {op} [{', '.join(value)}]"`.

### 4.7 `surf_alert/jev.py`

```python
JEV_URL: str = "https://api.typesafe.ai/v1/systemone"
DEFAULT_TIMEOUT: float = 30.0

class JevLike(Protocol):
    def ask(self, state: dict | list | str, questions: dict[str, dict]) -> dict[str, dict]: ...

class JevClient:
    def __init__(self, api_key: str, *, model: str = "jev-latest",
                 session: requests.Session | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None
        # ValueError("api_key must not be empty") for "" or whitespace-only
        # session=None -> requests.Session()
    def ask(self, state: dict | list | str, questions: dict[str, dict]) -> dict[str, dict]
        # ValueError("questions must not be empty") for {}
        # session.post(JEV_URL,
        #              headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        #              json={"model": model, "state": state, "questions": questions},
        #              timeout=timeout)
        # returns response.json()["answers"] (a dict)
        # errors: section 6.3
```

The session can be any object with a `post(url, *, headers, json, timeout)` method that returns an object with `.status_code: int`, `.text: str` and `.json() -> Any`. The `.json()` method raises `ValueError` (which includes `requests.JSONDecodeError`) when the body is not JSON.

### 4.8 `surf_alert/decision.py`

```python
NOUL_TEMPLATE: str = (
    "Is the surf at {name} (the state.spots entry with ref \"{ref}\") good enough for the user "
    "to go surfing today? Every rule in state.rules is already met for at least "
    "state.min_good_hours of the hours in that entry's \"today\" list. Also weigh wind, tide, "
    "swell consistency, how many good hours remain, state.notes and the entry's notes. "
    "Answer yes only if a session there today is worth it."
)
BEST_SPOT_INSTRUCTIONS: str = (
    "Which spot in state.spots should the user go surfing at today for the best session?"
)

def candidate_ref(index: int) -> str          # 1-based: candidate_ref(1) == "spot_1"

def build_jev_request(candidates: Sequence[SpotEvaluation], rules: Sequence[Rule],
                      settings: Settings) -> tuple[dict, dict[str, dict]]
    # returns (state, questions); ValueError("no candidate spots") if empty
    # questions, in insertion order:
    #   f"good_spot_{i}": {"type": "noul", "instructions": NOUL_TEMPLATE.format(name=..., ref=f"spot_{i}")}
    #   for i = 1..n in candidate order; then, only if n >= 2:
    #   "best_spot": {"type": "choice", "instructions": BEST_SPOT_INSTRUCTIONS,
    #                 "criteria": {f"spot_{i}": candidate_i.spot.name for i in 1..n}}
    # state: section 5.4

def interpret_jev_answers(answers: dict, candidates: Sequence[SpotEvaluation],
                          settings: Settings) -> Decision
    # FR-10 rules; raises JevError for invalid good_spot answers

def decide(candidates: Sequence[SpotEvaluation], rules: Sequence[Rule], settings: Settings,
           jev: JevLike) -> Decision
    # FR-11 rules
```

The candidate order is the order passed in. `app.run_check` passes candidates in config order. Every tie-break that mentions "config order" means position in this sequence. A probability stored in a `Recommendation` is `float(noul)`.

### 4.9 `surf_alert/message.py`

```python
def compass(degrees: float) -> str
    # 16-point: ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    # index = int((degrees % 360) / 22.5 + 0.5) % 16

def format_hours(slots: Sequence[Slot]) -> str
    # "none" if empty. Otherwise group runs where each next slot's timestamp == previous + 3600;
    # each run renders as f"{first.local_datetime():%H:%M}-{(last.local_datetime() + timedelta(hours=1)):%H:%M}";
    # runs joined with ", ".

def format_spot_block(evaluation: SpotEvaluation, settings: Settings,
                      probability: float | None = None) -> str

def format_rules_status(evaluation: SpotEvaluation, settings: Settings) -> str
    # f"Rules: {'PASS' if evaluation.passes_rules else 'FAIL'} "
    # f"(good hours today: {len(evaluation.good_slots)}, required: {settings.min_good_hours})"

def format_message(decision: Decision, settings: Settings) -> str
    # ValueError("no recommendations") if decision.recommendations is empty
```

`format_spot_block` lines, joined with `"\n"` with no trailing newline. `c` is `evaluation.current`, `H` is the height label (`ft` or `m`), `W` is the wind label (`kts`, `kph` or `mph`), and `T` is `°C` or `°F`.

| # | Line | Present when |
|---|---|---|
| 1 | `f"{spot.name} - now ({c.local_datetime():%H:%M} local)"` | always |
| 2 | `f"Surf: {c.surf_min:.1f}-{c.surf_max:.1f} {H}"` | always |
| 3 | `f"Swell: {s.height:.1f} {H} @ {s.period:.0f}s from {compass(s.direction)} ({s.direction:.0f}°)"`, where `s = c.primary_swell` | `s` is not None |
| 4 | `f"Wind: {c.wind_speed:.0f} {W}"`, plus `f" {c.wind_direction_type}"` if not None, plus `f" (gusts {c.wind_gust:.0f} {W})"` if not None | `wind_speed` is not None |
| 5 | `f"Tide: {c.tide_height:.1f} {H}"` | `tide_height` is not None |
| 6 | `"Weather: "` + the non-None parts of [`f"{c.temperature:.0f}{T}"`, `humanize(c.weather_condition)`] joined by `", "` | at least one part is not None |
| 7 | `f"Good hours today: {format_hours(evaluation.good_slots)}"` | always |
| 8 | `f"Jev confidence: {probability * 100:.0f}%"` | `probability` is not None |

`humanize(cond) = cond.replace("_", " ").capitalize()`. For example, `"MOSTLY_CLEAR"` becomes `"Mostly clear"`.

`format_message` builds a list of paragraphs and joins them with `"\n\n"`, with no trailing newline. `first` is `decision.recommendations[0]`.

1. `f"Surf alert {first.evaluation.local_date.isoformat()}\nGo surf at {first.evaluation.spot.name}!"`
2. `format_spot_block(first.evaluation, settings, first.probability)`
3. For each of the next recommendations, up to `settings.max_spots_in_message` blocks in total: `"Also good: " + format_spot_block(rec.evaluation, settings, rec.probability)`
4. If some recommendations did not fit: `"More good spots: " + ", ".join(their spot names, in order)`
5. If `decision.jev_used is False`: `"Note: Jev was unavailable, so this recommendation is based on your rules only."`

### 4.10 `surf_alert/notify.py`

```python
CALLMEBOT_URL: str = "https://api.callmebot.com/whatsapp.php"
PHONE_PATTERN: str = r"^\+[1-9][0-9]{6,14}$"
TEST_MESSAGE: str = "Surf alert test message: WhatsApp notifications are working."

class Notifier(Protocol):
    def send(self, text: str) -> None: ...

class CallMeBotNotifier:
    def __init__(self, phone: str, api_key: str, *,
                 session: requests.Session | None = None, timeout: float = 30.0) -> None
        # phone checked first with re.fullmatch(PHONE_PATTERN, phone) (so a trailing "\n" is rejected):
        #   ValueError("WHATSAPP_PHONE must be in international format, e.g. +34612345678")
        # then: ValueError("CALLMEBOT_API_KEY must not be empty")
        # session=None -> requests.Session()
    def send(self, text: str) -> None
        # ValueError("message text must not be empty") if text.strip() == ""
        # session.get(CALLMEBOT_URL, params={"phone": phone, "text": text, "apikey": api_key}, timeout=timeout)
        # success iff status_code == 200; errors in 6.4
```

### 4.11 `surf_alert/app.py`

```python
def evaluate_spots(config: AppConfig, surfline: ForecastSource, now: datetime,
                   spots: Sequence[Spot] | None = None
                   ) -> tuple[list[SpotEvaluation], dict[str, str]]
    # spots=None -> config.spots. One spot at a time, in order:
    #   evaluate_spot(surfline.fetch(spot), config.rules, config.settings, now)
    # SurflineError -> failures[spot.name] = str(exc); other exceptions propagate.
    # Returns (evaluations in order, failures in order).

def run_check(config: AppConfig, *, surfline: ForecastSource, jev: JevLike,
              notifier: Notifier | None, now: datetime) -> RunResult
```

`run_check` algorithm:
1. Call `evaluate_spots(config, surfline, now)`.
2. Set `candidates = [e for e in evaluations if e.passes_rules]` (config order).
3. If there are no candidates: `decision = None` and `message = None`. Jev is not called.
4. Otherwise: `decision = decide(candidates, config.rules, config.settings, jev)`. Set `message = format_message(decision, config.settings)` if `decision.recommendations` is not empty, else `None`.
5. If `message` is not None and `notifier` is not None: call `notifier.send(message)`. On `NotificationError` as `e`: `sent = False` and `send_error = str(e)`. On success: `sent = True`. In every other case (no message, or dry run with `notifier` None): `sent = False` and `send_error = None`.
6. `exit_code`: `4` if `send_error` is set; otherwise `3` if `failures` is non-empty or (`decision` is not None and `decision.jev_error` is not None); otherwise `0`.

### 4.12 `surf_alert/cli.py` and the command line

```python
def make_surfline_client(settings: Settings) -> ForecastSource     # SurflineClient(settings)
def make_jev_client(api_key: str, settings: Settings) -> JevLike   # JevClient(api_key, model=settings.jev_model)
def make_notifier(phone: str, api_key: str) -> Notifier            # CallMeBotNotifier(phone, api_key)
def utc_now() -> datetime                                          # datetime.now(timezone.utc)
def main(argv: list[str] | None = None) -> int                     # argv=None -> sys.argv[1:]
```

`main` must call these four helpers as module globals at call time, so that tests can replace them with `monkeypatch.setattr("surf_alert.cli.make_jev_client", fake)` and the like.

Usage. Global options come before the command (placing one after the command, e.g. `spots --config x`, is an argparse usage error, exit 2):

```
.venv/bin/python -m surf_alert [--config PATH] [--env-file PATH] [-v] COMMAND [options]

  --config PATH     TOML config file (default: surf_alert.toml, relative to the current directory)
  --env-file PATH   dotenv file (default: .env); a missing file is silently ignored
  -v, --verbose     log at DEBUG level to stderr (log text is not part of the contract); with
                    check or conditions, also print the 4.14 report and write the CSV table

Commands:
  spots                    list configured spots                                  (no network)
  rules                    list configured rules                                  (no network)
  conditions [--spot NAME] fetch and print current conditions and rule status     (Surfline only)
  check [--dry-run]        fetch, evaluate, ask Jev, send WhatsApp (or print with --dry-run)
  test-notification        send TEST_MESSAGE through WhatsApp                     (no config needed)
```

Common behaviour:
1. Parse the arguments with `argparse`. The subcommand is required. On a usage error (including no command), argparse prints usage to stderr and raises `SystemExit(2)`. `main` does not catch it.
2. Configure logging on stderr. Every app module logs through `logging.getLogger(__name__)` (so under the `surf_alert` logger). `main` calls `logging.basicConfig(level=logging.WARNING, stream=sys.stderr)` and then sets `logging.getLogger("surf_alert")` to DEBUG with `-v`, otherwise WARNING. The root logger stays at WARNING in both cases, so third-party loggers (`urllib3`, `requests`) never emit DEBUG records, because those would include the CallMeBot URL with `apikey=...`. The app itself logs only at DEBUG or INFO, so without `-v` stderr contains only the `error:`/`warning:` lines specified here. App log records never contain a secret or a URL with query parameters.
3. Call `dotenv.load_dotenv(dotenv_path=<--env-file>, override=False)`.
4. Every `error: ...` and `warning: ...` line goes to stderr through `print(..., file=sys.stderr)`. Normal output goes to stdout through `print(...)`.
5. Every `ConfigError` prints `error: <message>` and returns `1`.

`spots`
1. Load the config.
2. For each spot `i` (1-based), print `f"{i}. {spot.name} ({spot.spot_id})"`.
3. Return 0.

`rules`
1. Load the config.
2. For each rule `i` (1-based), print `f"{i}. {describe_rule(rule, settings)}"`.
3. Return 0.

`conditions [--spot NAME]`
1. Load the config.
2. If `--spot` is given, find the spot whose name equals NAME ignoring case (`casefold`). If there is none, print `error: unknown spot: <NAME as given>` and return 1.
3. `client = make_surfline_client(settings)`, then `evaluate_spots(config, client, utc_now(), spots=<[that spot]> or None)`.
4. Print `warning: {name}: {message}` for each failure, in order.
5. If there is at least one evaluation, print `"\n\n".join(format_spot_block(ev, settings) + "\n" + format_rules_status(ev, settings) for ev in evaluations)`.
6. Return 3 if there were failures, otherwise 0.

`check [--dry-run]`
1. Load the config.
2. Required variables: `TYPESAFE_API_KEY`, and also `WHATSAPP_PHONE` and `CALLMEBOT_API_KEY` unless `--dry-run` is given. A variable counts as missing when it is unset or `value.strip() == ""` (empty or whitespace-only). Present values are passed to the factories exactly as read (not stripped). If any are missing, print `error: missing environment variable(s): <missing names joined by ", ", in that order>`, then for each missing name a line `  <NAME>: <ENV_HELP[NAME]>`, and return 1. `ENV_HELP` (in `cli.py`) says what each variable is and where to get it. Values are never printed. The rest depends on the env file (`--env-file`, default `.env`):
   - The file does not exist (`os.path.isfile` is false): no reason is added, and the last line is `No env file was found at {os.path.abspath(env_file)}. Create it with: cp .env.example {env_file}`.
   - The file exists (read with `dotenv.dotenv_values`): each variable line ends with `; <reason>`. The reason is `{env_file} has a value, but {NAME} is set to an empty value in your shell, which takes precedence (run: unset {NAME})` when the file has a non-blank value; `it is empty in {env_file}` when the file has the key with a blank or missing value; otherwise `it is not in {env_file}`. Unless every reason is the shell one, the last line is `Add the missing values to {env_file} (see .env.example for the format).`
3. `surfline = make_surfline_client(settings)`; `jev = make_jev_client(TYPESAFE_API_KEY, settings)`; `notifier = None` with `--dry-run`, otherwise `make_notifier(WHATSAPP_PHONE, CALLMEBOT_API_KEY)`. A `ValueError` from any factory prints `error: <message>` and returns 1.
4. `result = run_check(config, surfline=surfline, jev=jev, notifier=notifier, now=utc_now())`.
5. On stderr: `warning: {name}: {message}` for each failure, in order. Then `warning: Jev request failed: {jev_error}` if `result.decision` is not None and its `jev_error` is not None.
6. Then exactly one of these:
   - `result.message is None`: stdout `No good spots today; no notification sent.`
   - dry run: stdout `result.message`
   - `result.sent`: stdout `Notification sent: {len(result.decision.recommendations)} spot(s) recommended.`
   - otherwise (the send failed): stderr `error: failed to send WhatsApp message: {result.send_error}`
7. Return `result.exit_code`.

`test-notification`
1. Do not load the config.
2. Require `WHATSAPP_PHONE` and `CALLMEBOT_API_KEY` (same missing-variable rule and message as `check`; return 1).
3. `notifier = make_notifier(phone, key)`. A `ValueError` prints `error: <message>` and returns 1.
4. `notifier.send(TEST_MESSAGE)`. On `NotificationError` as `e`, print `error: failed to send WhatsApp message: {e}` and return 4.
5. Print `Test notification sent.` and return 0.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success. Either a message was sent or printed, or there was nothing to send. |
| 1 | Config error, missing or invalid environment variable, or unknown `--spot`. No network calls were made. |
| 2 | CLI usage error (raised by argparse as `SystemExit(2)`). |
| 3 | Finished in a degraded state: at least one spot failed to fetch, or the Jev call failed. A message may still have been sent. |
| 4 | Sending the WhatsApp message failed. |

### 4.13 Running daily

This is documentation, not code, and it goes in `README.md` at the project root. The README also covers installing into `.venv/`, copying `surf_alert.example.toml` to `surf_alert.toml` and `.env.example` to `.env`, finding spot IDs, CallMeBot activation (5.2) and the five commands. The recommended setup is a cron entry that runs at 06:00 local time (`crontab -e`):

```
0 6 * * * cd /Users/leoxyde/Documents/Coding/Python/ClaudeAgentsScripts && .venv/bin/python -m surf_alert --config surf_alert.toml check >> "$HOME/Library/Logs/surf_alert.log" 2>&1
```

On macOS, cron jobs don't run while the Mac is asleep. A launchd agent (`~/Library/LaunchAgents/<label>.plist` with `StartCalendarInterval` Hour 6, Minute 0, and `ProgramArguments` running the same command with `WorkingDirectory` set to the project root) runs a missed job when the Mac wakes. The user can set that up; this feature doesn't deliver it. Before scheduling, run `check --dry-run` once by hand, and `test-notification` once.

### 4.14 `surf_alert/report.py` (the `-v` report)

Added 2026-10-09. With `-v`, `check` (also with `--dry-run`) and `conditions` run exactly as without it: same stdout, same exit code, same Jev request. After their normal output they print an explanation to stderr and write a per-spot table. Other commands only get the DEBUG logs.

```python
DEFAULT_REPORT_PATH: str = "surf_alert_report.csv"   # cli.REPORT_PATH, relative to the current directory

class RecordingJev:                     # a JevLike: ask() forwards once, then keeps .state and .answers
    def confidences(self) -> dict[str, float]          # spot name -> valid good_spot_N "noul"
    def best_spot(self) -> tuple[str | None, dict[str, float]]   # chosen name, name -> choice probability

@dataclass(frozen=True)
class SpotReport:  # name, spot_id, evaluation | None, error, rule_hours, sent_to_jev,
                   # jev_confidence, best_spot_probability, rank, outcome

def build_spot_reports(config, evaluations, failures, decision, jev) -> list[SpotReport]
def format_report(config, reports, decision, jev) -> str
def write_csv(path, config, reports) -> str            # returns the absolute path
```

- `check` wraps the Jev client in `RecordingJev`, because a `Decision` keeps only approved spots. Spot names come from the recorded `state.spots` refs, so a rejected spot still shows its confidence. `conditions` passes no decision and no recorder.
- Reports follow config order and cover every evaluated or failed spot (`conditions --spot` covers one). `rule_hours[i]` is the number of window slots where rule `i` passes. `rank` is the 1-based position in `decision.recommendations`.
- `outcome` is the first that applies: `skipped: {error}`; `not checked: no daylight hours left today`; `failed rules: {n} good hour(s) today, needs {min_good_hours}`; `passes rules (Jev not asked)`; with Jev used, `recommended #{rank}: Jev confidence {c:.2f} >= threshold {t:.2f}` or `rejected by Jev: confidence {c:.2f} < threshold {t:.2f}`; with Jev failed, `recommended #{rank} by rules only: Jev failed ({error})` or `not recommended: Jev failed ({error}) and on_jev_error is skip`.
- The stderr report lists the rules, then per spot: the window, the current conditions with the Surfline rating, the best and lowest rating today, each rule's `hours/window` count, the all-rules result, the Jev confidence and best-spot probability, and the outcome. It ends with a Jev summary and the result, then `Spot table written to {absolute path}`.
- The CSV (UTF-8, overwritten) has these columns: `spot, outcome, rank, jev_confidence, best_spot_probability, jev_threshold, passes_rules, good_hours, required_hours, surf_rating_now, surf_rating_best_today`, then `rule {i}: {describe_rule}` per rule, then `window_hours, good_hour_ranges, local_date, now_time, now_surf_min_{h}, now_surf_max_{h}, now_swell_height_{h}, now_swell_period_s, now_wind_speed_{w}, now_wind_direction_type, sent_to_jev, spot_id, error`. Missing values are empty; booleans are `yes`/`no`.
- If the file cannot be written, stderr gets `warning: could not write the spot table to {path}: {exc}` and the exit code is unchanged.

## 5. Data model

### 5.1 Config file: `surf_alert.toml` (reference config)

TOML, read with the standard-library `tomllib`. The top-level keys allowed are `settings` (a table, optional), `spots` (an array of tables, required) and `rules` (an array of tables, required).

```toml
[settings]
height_unit = "ft"            # "ft" | "m"           default "ft"
wind_unit = "kts"             # "kts" | "kph" | "mph" default "kts"
temperature_unit = "C"        # "C" | "F"            default "C"
min_good_hours = 1            # int 1..24            default 1
jev_threshold = 0.7           # 0 < x <= 1           default 0.7
jev_model = "jev-latest"      # non-empty string     default "jev-latest"
on_jev_error = "rules_only"   # "rules_only" | "skip" default "rules_only"
max_spots_in_message = 3      # int 1..10            default 3
notes = "Intermediate surfer; prefer offshore or light winds."   # default ""

[[spots]]
name = "Pipeline"                    # non-empty, unique (case-insensitive), stripped
id = "5842041f4e65fad6a7708890"      # 24 hex chars, unique (case-insensitive); from the Surfline URL
notes = "Only surf it when it is under 6 ft."    # optional, default ""

[[spots]]
name = "Sunset Beach"
id = "0123456789abcdef01234567"      # fictional id used in examples and tests

[[rules]]
metric = "wave_height_min"
op = ">"
value = 2

[[rules]]
metric = "swell_period"
op = ">"
value = 7
```

How to find a spot ID: open the spot on surfline.com. The ID is the last part of the URL, for example `https://www.surfline.com/surf-report/pipeline/5842041f4e65fad6a7708890`.

`surf_alert.example.toml` contains this content with explanatory comments (the comments may be reworded). The fictional Sunset Beach entry is replaced by a commented-out placeholder spot, so copying the example gives a working config. The real `surf_alert.toml` is created by the user (for example by copying the example).

Other rule examples (valid):

```toml
[[rules]]
metric = "wind_direction_type"
op = "in"
value = ["Offshore", "Cross-shore"]

[[rules]]
metric = "wind_speed"
op = "<="
value = 15
```

### 5.2 Environment variables and `.env`

| Variable | Needed by | Format |
|---|---|---|
| `TYPESAFE_API_KEY` | `check` | API key from console.typesafe.ai |
| `WHATSAPP_PHONE` | `check` (not `--dry-run`), `test-notification` | International format with `+`, matching `^\+[1-9][0-9]{6,14}$` |
| `CALLMEBOT_API_KEY` | `check` (not `--dry-run`), `test-notification` | Key that CallMeBot sends after activation |

`.env.example` (committed). `.env` itself is already in `.gitignore`:

```
TYPESAFE_API_KEY=
WHATSAPP_PHONE=+34612345678
CALLMEBOT_API_KEY=
```

Because `.env` never overrides the real environment, a variable that is set but empty in the real environment stays empty and counts as missing, even when `.env` has a value for it.

CallMeBot activation is a one-time manual step for the user. Follow https://www.callmebot.com/blog/free-api-whatsapp-messages/: add the CallMeBot number to your contacts, send it the activation phrase on WhatsApp, and you receive the API key.

### 5.3 External services reference (facts for implementers and testers; there is no web access)

**pysurfline** (the wolfinger fork, version 0.0.3, commit `f0c98ea06ce9f26f5ba9e3b94ac98963150f7ddc`, 2022)
- `pip install pysurfline` fails from PyPI in this venv. Install it from git: `.venv/bin/pip install "git+https://github.com/wolfinger/pysurfline@f0c98ea06ce9f26f5ba9e3b94ac98963150f7ddc"`. It depends on `requests`, `pandas` and `matplotlib`.
- `from pysurfline import SpotForecast`. `SpotForecast(params: dict, verbose=False)` makes 4 HTTP GETs as soon as it is constructed: `https://services.surfline.com/kbyg/spots/forecasts/{wave|wind|tides|weather}?{params}`. It uses `requests.get` with no timeout and no auth.
- **The `wave` forecast is retired.** Since at least 2026-10-07 Surfline answers HTTP 404 for `wave` (for every spot and `days` value), while `wind`, `tides` and `weather` still answer 200. So a real `SpotForecast` has no `wave` or `units_wave`. The wave data is now split across two forecasts at the same URL pattern, which the default factory requests itself (4.5), along with Surfline's rating:
  - `surf`: `{"associated": {"units": {"waveHeight": "M"}, ...}, "data": {"surf": [{"timestamp", "utcOffset", "surf": {"min": 0.9, "max": 1.4, "plus": false, "humanRelation": "Waist to shoulder", "raw": {...}}}, ...]}}`. There is no `optimalScore`.
  - `swells`: `{"associated": {"units": {"swellHeight": "M"}, ...}, "data": {"swells": [{"timestamp", "utcOffset", "probability", "power", "swells": [{"height", "period", "impact", "power", "spectralPower", "direction", "directionMin"}, ...]}, ...]}}`. There is no `optimalScore`.
  - `rating`: `{"associated": {...}, "data": {"rating": [{"timestamp", "utcOffset", "rating": {"key": "FAIR", "value": 3}}, ...]}}`. Observed pairs: 0 `VERY_POOR`, 1 `POOR`, 2 `POOR_TO_FAIR`, 3 `FAIR`, 4 `FAIR_TO_GOOD`. `GOOD` (5) and `EPIC` (6) are inferred from the pattern.
- params: `spotId` (str), `days` (int, at most 6 without a token), `intervalHours` (int, at least 1), and an optional `accesstoken`.
- On a non-200 response it does **not** raise. It prints `Error : <code>` and leaves the attribute unset, so `wave`, for example, may be missing.
- Attributes set on success:
  - `wave` (no longer returned; kept for reference): a list of flat dicts, for example `{'timestamp': 1644919200, 'utcOffset': -10, 'surf_min': 2.4255, 'surf_max': 3.3482, 'surf_optimalScore': 2, 'swells': [{'height': 1.49573, 'period': 9, 'direction': 27.57953, 'directionMin': 17.088825, 'optimalScore': 0}, ...]}`. There are 6 swells, and some are all zeros.
  - `wind`: a list of `{'timestamp', 'utcOffset', 'speed': 19.52, 'direction': 62.2, 'directionType': 'Cross-shore'|'Offshore'|'Onshore', 'gust': 20.89, 'optimalScore': 0}`
  - `tides`: a list of `{'timestamp', 'utcOffset', 'type': 'NORMAL'|'HIGH'|'LOW', 'height': 0.39}`
  - `weather`: a list of `{'timestamp', 'utcOffset', 'temperature': 22.05, 'condition': 'NIGHT_CLEAR'|'CLEAR'|...}`
  - `sunlightTimes`: a list of dicts with epoch-second keys `midnight`, `dawn`, `sunrise`, `sunset` and `dusk`, each with a matching `<key>UTCOffset` (hours).
  - `units_wave`, `units_wind`, `units_tides`, `units_weather`: unit dicts. These are assumed to look like `{"temperature": "C", "tideHeight": "FT", "swellHeight": "FT", "waveHeight": "FT", "windSpeed": "KTS"}`.
  - `location`, `forecastLocation`, `utcOffset` (not used).
  - `get_dataframe("wave")` and similar return pandas DataFrames (not used).
- Timestamps are epoch seconds in UTC. `utcOffset` is in hours.

**TypeSafe Jev** ("System One" probabilistic decision model: typed answers with calibrated probabilities)
- `POST https://api.typesafe.ai/v1/systemone`, with the header `Authorization: Bearer $TYPESAFE_API_KEY` and a JSON body. `GET https://api.typesafe.ai/v1/models` lists models (not used).
- Request: `{"model": "jev-latest", "state": <string | JSON object | JSON array>, "questions": {<id>: {"type": "noul", "instructions": "..."} | {"type": "choice", "instructions": "...", "criteria": {<option>: <description>}} | {"type": "score", "instructions": "...", "criteria": [<2..10 levels>]}}}`. `noul` is a yes/no question and returns a probability from 0 to 1. `choice` has at most 255 options.
- Response: `{"model": "jev-1.13.0", "answers": {<id>: {"type": "noul", "noul": 0.97} | {"type": "choice", "choice": "<option>", "confidence": 1.0, "probabilities": {...}} | {"type": "score", "score": 1.3, "legend": {}}}, "usage": {"input_tokens": 290, "output_tokens": 20}}`.
- Errors: 401 (bad or missing key), 422 (invalid body), 429 (rate limited, 1,200 requests per minute), 529 (overloaded).

**CallMeBot WhatsApp**
- `GET https://api.callmebot.com/whatsapp.php?phone=<+intl>&text=<urlencoded>&apikey=<key>`. It is free and meant for sending messages to yourself.

### 5.4 Jev `state` and `questions` schema

`state` is a dict (sent as a JSON object) with exactly these keys, in this order:

```json
{
  "units": {"height": "ft", "period": "s", "wind": "kts", "temperature": "C"},
  "rules": ["wave_height_min > 2 ft", "swell_period > 7 s"],
  "notes": "Intermediate surfer; prefer offshore or light winds.",
  "min_good_hours": 1,
  "spots": [
    {
      "ref": "spot_1",
      "name": "Pipeline",
      "notes": "Only surf it when it is under 6 ft.",
      "local_date": "2026-10-07",
      "good_hours": ["07:00", "08:00", "09:00", "10:00", "11:00"],
      "current": {"time": "07:00", "surf_min": 2.4, "surf_max": 3.3, "surf_rating": 4,
                  "swell_height": 4.2, "swell_period": 11.0, "swell_direction": 315.0,
                  "wind_speed": 8.4, "wind_gust": 12.3, "wind_direction": 45.0,
                  "wind_direction_type": "Offshore", "tide_height": 1.3, "temperature": 24.0,
                  "weather_condition": "MOSTLY_CLEAR", "passes_rules": true},
      "today": ["... one SLOT object per window slot, ascending; the first equals current here ..."]
    }
  ]
}
```

- `units` uses the settings values. `period` is always `"s"`.
- `rules` is `[describe_rule(r, settings) for r in rules]`.
- `notes` comes from `settings.notes`, and `min_good_hours` from `settings.min_good_hours`.
- `spots` follows candidate order, with `ref = candidate_ref(i)`, `notes = spot.notes` and `local_date = evaluation.local_date.isoformat()`.
- `good_hours` is the list of `f"{s.local_datetime():%H:%M}"` for each good slot.
- `current` is the SLOT for `evaluation.current`, and `today` is the list of SLOTs for `evaluation.window`.

A SLOT is a dict with exactly these keys, in this order:

| Key | Value |
|---|---|
| `time` | `f"{slot.local_datetime():%H:%M}"` |
| `surf_min`, `surf_max` | `round(x, 1)` |
| `surf_rating` | int or `None` |
| `swell_height`, `swell_period`, `swell_direction` | `round(x, 1)` from `primary_swell`, or `None` |
| `wind_speed`, `wind_gust`, `wind_direction` | `round(x, 1)` or `None` |
| `wind_direction_type` | str or `None` |
| `tide_height` | `round(x, 1)` or `None` |
| `temperature` | `round(x, 1)` or `None` |
| `weather_condition` | raw str or `None` |
| `passes_rules` | `slot_passes(rules, slot)` |

`questions` for the reference scenario (2 candidates):

```json
{
  "good_spot_1": {"type": "noul", "instructions": "Is the surf at Pipeline (the state.spots entry with ref \"spot_1\") good enough for the user to go surfing today? Every rule in state.rules is already met for at least state.min_good_hours of the hours in that entry's \"today\" list. Also weigh wind, tide, swell consistency, how many good hours remain, state.notes and the entry's notes. Answer yes only if a session there today is worth it."},
  "good_spot_2": {"type": "noul", "instructions": "<NOUL_TEMPLATE with name=Sunset Beach, ref=spot_2>"},
  "best_spot": {"type": "choice", "instructions": "Which spot in state.spots should the user go surfing at today for the best session?", "criteria": {"spot_1": "Pipeline", "spot_2": "Sunset Beach"}}
}
```

Reference answers (HTTP 200 body):

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "good_spot_1": {"type": "noul", "noul": 0.92},
    "good_spot_2": {"type": "noul", "noul": 0.81},
    "best_spot": {"type": "choice", "choice": "spot_1", "confidence": 0.88, "probabilities": {"spot_1": 0.88, "spot_2": 0.12}}
  },
  "usage": {"input_tokens": 1450, "output_tokens": 20}
}
```

### 5.5 Reference WhatsApp message

For the reference decision (Pipeline 0.92 first, then Sunset Beach 0.81; `jev_used=True`; `Settings()`):

```
Surf alert 2026-10-07
Go surf at Pipeline!

Pipeline - now (07:00 local)
Surf: 2.4-3.3 ft
Swell: 4.2 ft @ 11s from NW (315°)
Wind: 8 kts Offshore (gusts 12 kts)
Tide: 1.3 ft
Weather: 24°C, Mostly clear
Good hours today: 07:00-12:00
Jev confidence: 92%

Also good: Sunset Beach - now (07:00 local)
Surf: 1.9-2.8 ft
Swell: 3.9 ft @ 10s from NW (310°)
Wind: 11 kts Cross-shore (gusts 15 kts)
Tide: 1.3 ft
Weather: 24°C, Mostly clear
Good hours today: 08:00-11:00, 15:00-17:00
Jev confidence: 81%
```

(Sunset Beach's 07:00 "now" slot is shown even though it is not a good hour itself.)

The rules-only fallback version of the same decision has `jev_used=False` and both probabilities `None`. Both spots have 5 good slots, so config order keeps Pipeline first. It contains the same header and blocks without the `Jev confidence` lines, and ends with this extra paragraph:

```

Note: Jev was unavailable, so this recommendation is based on your rules only.
```

### 5.6 Reference scenario (shared by the examples and tests)

- `now = datetime(2026, 10, 7, 17, 30, tzinfo=timezone.utc)`, which is epoch `1791394200` and 07:30 local at utcOffset -10.
- Useful epochs: 2026-10-07T00:00Z = `1791331200`. Local midnight at utcOffset -10 on 2026-10-07 (10:00Z) = `1791367200`. Local hour `h` (0..23) on 2026-10-07 starts at `1791367200 + 3600*h`; for example 07:00 local (17:00Z) = `1791392400`. Dawn 06:10 local = `1791389400`. Sunrise 06:40 local = `1791391200`. Sunset 18:10 local = `1791432600`. Dusk 18:40 local = `1791434400`.
- Units for both spots: `units_wave = units_wind = units_tides = units_weather = {"temperature": "C", "tideHeight": "FT", "swellHeight": "FT", "waveHeight": "FT", "windSpeed": "KTS"}`. With `Settings()` no conversion happens.
- `sunlightTimes` for both spots: `[{"midnight": 1791367200, "midnightUTCOffset": -10, "dawn": 1791389400, "dawnUTCOffset": -10, "sunrise": 1791391200, "sunriseUTCOffset": -10, "sunset": 1791432600, "sunsetUTCOffset": -10, "dusk": 1791434400, "duskUTCOffset": -10}]`
- **Pipeline** raw object (a `SimpleNamespace`). It has 24 hourly entries for `h = 0..23` in each of `wave`, `wind`, `tides`, `weather` and `rating`, all at the same timestamps with `utcOffset -10`.
  - `wave`: hours 07–11 have `surf_min 2.4255`, `surf_max 3.3482`. Every other hour has `surf_min 1.2`, `surf_max 1.9`. All hours have `swells [{"height": 4.2, "period": 11, "direction": 315.0, "directionMin": 305.0, "optimalScore": 1}, {"height": 1.1, "period": 7, "direction": 20.0, "directionMin": 10.0, "optimalScore": 0}, {"height": 0, "period": 0, "direction": 0, "directionMin": 0, "optimalScore": 0}]`.
  - `wind`: `speed 8.4`, `direction 45.0`, `directionType "Offshore"`, `gust 12.3`, `optimalScore 1`.
  - `tides`: `type "NORMAL"`, `height 1.3`.
  - `weather`: `temperature 24.0`, `condition "MOSTLY_CLEAR"`.
  - `rating`: `{"key": "FAIR_TO_GOOD", "value": 4}`.
  - Result: current slot 07:00, window 07:00–18:00 (12 slots), good slots 07:00–11:00 (5), `passes_rules = True`.
- **Sunset Beach** raw object: the same structure.
  - `wave`: hours 08, 09, 10, 15 and 16 have `surf_min 2.5`. Every other hour (including 07) has `surf_min 1.9`. All hours have `surf_max 2.8` and `swells [{"height": 3.9, "period": 10, "direction": 310.0, "directionMin": 300.0, "optimalScore": 1}]`.
  - `wind`: `speed 11.0`, `direction 90.0`, `directionType "Cross-shore"`, `gust 15.0`.
  - `tides`: `height 1.3`. `weather`: `temperature 24.0`, `condition "MOSTLY_CLEAR"`. `rating`: `{"key": "FAIR", "value": 3}`.
  - Result: current slot 07:00 (fails the rules because `surf_min` 1.9 is not > 2), window 07:00–18:00, good slots 08, 09, 10, 15 and 16 (5), `passes_rules = True`.
- Reference decision: `Decision(recommendations=(Recommendation(<Pipeline evaluation>, 0.92), Recommendation(<Sunset Beach evaluation>, 0.81)), jev_used=True, jev_error=None)`.

## 6. Error handling and edge cases

### 6.1 Config validation messages (`ConfigError`)

Validation stops at the first error. The order is: top-level keys, then `settings` (keys in the order of the table below), then each spot in order (table type, unknown keys, `name`, `id`, `notes`, duplicate name, duplicate id), then the spot-count limit, then each rule in order (table type, unknown keys, `metric`, `op`, op/kind compatibility, `value`). `{i}` is the 0-based index. Integers must be `int` and not `bool`. Numbers may be `int` or `float` but not `bool`.

Further rules:
- **Unknown keys**: when a table has several unknown keys, the first one in file order (the parsed dict's iteration order) is reported.
- **Case**: only `height_unit`, `wind_unit` and `temperature_unit` are case-insensitive (AC-1.5). `on_jev_error`, rule `metric` and rule `op` must match exactly, so `on_jev_error = "SKIP"` or `metric = "Swell_Period"` is an error with the message from the table.
- **Blank strings**: `jev_model` and text rule values that are empty or whitespace-only are invalid (same messages as the table). Valid values are stored exactly as written (not stripped). Spot `name` is the only value that is stripped.
- **Spot ids** are stored exactly as written (no case change). Duplicate detection compares them case-insensitively, and the `duplicate spot id: {id}` message shows the later spot's id exactly as written in the file.

| Condition | Message |
|---|---|
| File missing | `config file not found: {path}` |
| Path exists but cannot be read (a directory, no permission, any other `OSError`) | `cannot read config file: {path}` |
| TOML syntax error | `invalid TOML in {path}: {tomllib error text}` |
| Unknown top-level key | `unknown key '{key}' in top level` |
| `settings` is not a table | `settings must be a table` |
| Unknown key in settings | `unknown key '{key}' in settings` |
| Bad `height_unit` | `settings.height_unit must be one of: ft, m` |
| Bad `wind_unit` | `settings.wind_unit must be one of: kts, kph, mph` |
| Bad `temperature_unit` | `settings.temperature_unit must be one of: C, F` |
| Bad `min_good_hours` | `settings.min_good_hours must be an integer between 1 and 24` |
| Bad `jev_threshold` | `settings.jev_threshold must be a number greater than 0 and at most 1` |
| Bad `jev_model` | `settings.jev_model must be a non-empty string` |
| Bad `on_jev_error` | `settings.on_jev_error must be one of: rules_only, skip` |
| Bad `max_spots_in_message` | `settings.max_spots_in_message must be an integer between 1 and 10` |
| Bad `settings.notes` | `settings.notes must be a string` |
| `spots` missing, empty, or not a list | `config must define at least one [[spots]] entry` |
| Spot entry is not a table | `spots[{i}] must be a table` |
| Unknown key in a spot | `unknown key '{key}' in spots[{i}]` |
| Bad name (missing, not a str, or blank after strip) | `spots[{i}].name must be a non-empty string` |
| Bad id (missing, not a str, or not matching `^[0-9a-fA-F]{24}$`) | `spots[{i}].id must be a 24-character hexadecimal Surfline spot id` |
| Bad spot notes | `spots[{i}].notes must be a string` |
| Name repeats an earlier spot (case-insensitive, after strip) | `duplicate spot name: {name}` (the later spot's stripped name) |
| Id repeats an earlier spot (case-insensitive) | `duplicate spot id: {id}` (the later spot's id) |
| More than 50 spots | `config may define at most 50 spots` |
| `rules` missing, empty, or not a list | `config must define at least one [[rules]] entry` |
| Rule entry is not a table | `rules[{i}] must be a table` |
| Unknown key in a rule | `unknown key '{key}' in rules[{i}]` |
| Metric missing or unknown | `rules[{i}].metric must be one of: wave_height_min, wave_height_max, surf_rating, swell_height, swell_period, wind_speed, wind_gust, wind_direction_type, tide_height, temperature, weather_condition` |
| Op missing or unknown | `rules[{i}].op must be one of: >, >=, <, <=, ==, !=, in, not_in` |
| Op not allowed for the metric kind | `rules[{i}].op '{op}' is not valid for metric '{metric}'` |
| Numeric metric with a value that is missing or not a number | `rules[{i}].value must be a number` |
| Text metric with `==`/`!=` and a value that is not a non-empty str | `rules[{i}].value must be a non-empty string` |
| Text metric with `in`/`not_in` and a value that is not a non-empty list of non-empty strs | `rules[{i}].value must be a non-empty list of non-empty strings` |

### 6.2 Surfline (per spot; never fatal for the whole run)

| Situation | Behaviour |
|---|---|
| The factory raises (network error, JSON error, `ImportError`, a non-200 `surf`/`swells`/`rating` answer, anything else) | `SurflineError("failed to fetch forecast for {name}: {exc}")`. The spot is recorded in `failures`. Exit code 3. |
| A non-200 response made `wave` or `wind` missing or empty | `SurflineError("no wave data returned for {name}")` or `SurflineError("no wind data returned for {name}")`. |
| `tides`, `weather` or `sunlightTimes` missing | Not an error. The fields become `None`, and daylight falls back to local hours 06–19. |
| Unknown or missing required unit | `SurflineError("unsupported {waveHeight\|swellHeight\|windSpeed} unit for {name}: {value!r}")` |
| Unknown or missing optional unit | The related values become `None`. |
| Malformed wave entry | `SurflineError("malformed forecast data for {name}: ...")` |
| Surfline hangs on a pysurfline request (pysurfline sets no timeout) | Known limitation. See section 7. The `surf`, `swells` and `rating` GETs time out after 30 s and fail the spot. |
| Every spot fails | No Jev call, no message, stdout `No good spots today; no notification sent.`, exit 3. |
| `now` is after dusk, or no slots exist for today | The window is empty, the spot is not a candidate, and nothing is sent. |

### 6.3 Jev (`JevError` messages)

**Redaction** (shared by 6.3 and 6.4): `redact(s)` replaces every occurrence of the API key in `s` with `***`, and also every occurrence of `urllib.parse.quote_plus(api_key)` when that differs from the key. The order is fixed: redact first, then truncate.

`{body}` is `redact(response.text)[:200]`. `{exc}` is `redact(str(exc))`. The API key is never part of any `JevError` message.

| Situation | Message | `status_code` |
|---|---|---|
| `requests.RequestException` (connection, timeout, ...) | `request failed: {exc}` | `None` |
| HTTP 401 | `authentication failed (HTTP 401); check TYPESAFE_API_KEY` | 401 |
| HTTP 422 | `invalid request (HTTP 422): {body}` | 422 |
| HTTP 429 | `rate limited (HTTP 429)` | 429 |
| HTTP 529 | `service overloaded (HTTP 529)` | 529 |
| Any other status outside 200–299 | `unexpected response (HTTP {code}): {body}` | code |
| 2xx with a body that is not JSON | `malformed response: body is not valid JSON` | code |
| 2xx JSON that is not an object, or without a dict `answers` | `malformed response: missing 'answers' object` | code |
| Bad `good_spot_i` answer (raised by `interpret_jev_answers`) | `malformed response: missing or invalid answer for good_spot_{i}` | `None` |

There are no retries. In every case, `decide` applies `on_jev_error` (FR-11), and the run exits with 3.

### 6.4 WhatsApp (`NotificationError` messages from `CallMeBotNotifier.send`)

| Situation | Message |
|---|---|
| `requests.RequestException` | `request to CallMeBot failed: {redact(str(exc))}` |
| `status_code != 200` | `CallMeBot returned HTTP {code}: {redact(response.text)[:200]}` |

`redact` is defined in 6.3 (redact first, then truncate), using the CallMeBot API key.

There are no retries. `run_check` turns these into exit code 4.

### 6.5 Other edge cases

- Zero recommendations, for any reason: no message is sent, ever. The app never sends an "all bad" message.
- One candidate: no `best_spot` question is asked, and that candidate is first if approved.
- More approved spots than `max_spots_in_message`: the extras are listed by name only (FR-12).
- A spot name contains characters that are special in URLs or WhatsApp: no escaping is needed, because `requests` URL-encodes `params`.
- Fractional UTC offsets (for example +5.5): handled by `round(utc_offset * 3600)` everywhere.
- `now` is naive: `ValueError("now must be timezone-aware")`. The CLI always passes an aware UTC datetime.
- Unexpected exceptions that are not `SurfAlertError` and not handled above propagate, so Python prints a traceback and exits with status 1. No blanket catching.

## 7. Non-functional requirements

- **Runtime**: Python 3.13 in `.venv/`. Use `.venv/bin/python` and `.venv/bin/pip`. Never install into the system Python. Tests run with `.venv/bin/python -m pytest` from the project root.
- **Dependencies** (`requirements.txt`; test agents may add test-only packages there if their strategy requires it):
  ```
  requests>=2.31
  python-dotenv>=1.0
  pysurfline @ git+https://github.com/wolfinger/pysurfline@f0c98ea06ce9f26f5ba9e3b94ac98963150f7ddc
  pytest>=8
  ```
  `tomllib`, `dataclasses`, `argparse`, `contextlib`, `io` and `logging` come from the standard library.
- **Offline testability**: every network call can be replaced without patching third-party internals:
  - Surfline through `SurflineClient(forecast_factory=...)`, `SurflineClient(session=...)` (with a fake `pysurfline` module), any `ForecastSource`, or `cli.make_surfline_client`.
  - Jev through `JevClient(session=...)`, any `JevLike`, or `cli.make_jev_client`.
  - WhatsApp through `CallMeBotNotifier(session=...)`, any `Notifier`, or `cli.make_notifier`.
  - Time through the `now` parameters and `cli.utc_now`.

  No module imports `pysurfline` at import time, so the full test suite passes even if `pysurfline` (and pandas or matplotlib) cannot be installed. CLI tests should always pass `--env-file <nonexistent tmp path>` (or a temp file) and set or clear variables with `monkeypatch.setenv`/`delenv`, so the developer's real `.env` does not leak in.
- **Timeouts**: Jev, CallMeBot and the `surf`, `swells` and `rating` GETs use a 30 s timeout. pysurfline's GETs have no timeout because pysurfline offers no way to set one. This is a known limitation, and it is acceptable for a once-a-day job.
- **Volume**: at most 50 spots, which is 7 Surfline GETs per spot (4 from pysurfline, one of which is the retired `wave` forecast, plus `surf`, `swells` and `rating`), fetched one spot at a time. Exactly 0 or 1 Jev request per `check` run, and at most 1 WhatsApp request per run.
- **Security**: secrets come only from environment variables or `.env` (already gitignored). They are never printed, logged, or put in exception messages. The CallMeBot API key travels in the query string, so URLs, response bodies and request exceptions are redacted before being shown (6.3), and third-party DEBUG logging stays off even with `-v` (4.12).
- **Output**: the message is plain text with no Markdown and no emojis. The only non-ASCII character is `°`. The app keeps no persistent state.

## 8. Assumptions

1. **Language and layout**: Python 3.13 package `surf_alert/` at the project root, run with `.venv/bin/python -m surf_alert`. The project had no code, and CLAUDE.md fixes the venv.
2. **Config format**: TOML (`surf_alert.toml`, path set with `--config`). The stdlib `tomllib` reads it, so no extra dependency is needed, and it supports comments. The user edits the file by hand, and "a list of surf spots I can retrieve" is met by the `spots` command (list) and the `conditions` command (live data for one or all spots).
3. **WhatsApp provider**: CallMeBot. It is free, needs no business account, and is built for messaging yourself, which matches "notify me". It sits behind the one-method `Notifier` protocol, so Twilio or Meta can be added later.
4. **CallMeBot success** means HTTP 200 and nothing else. The response body is not inspected.
5. **"typesafe.jev api"** is TypeSafe's Jev endpoint `POST https://api.typesafe.ai/v1/systemone`, called directly with `requests` and not through `typesafe-sdk`.
6. **How rules and Jev combine**: rules are a deterministic hard pre-filter (Jev can never approve a spot that failed them). They are also passed to Jev as text in `state.rules`, along with each candidate's full window, the user's notes and per-spot notes. Jev makes the final yes/no for each candidate (`noul`, approved at `>= jev_threshold`, default 0.7) and picks the best spot (`choice`, asked only when there are at least 2 candidates). Everything goes in one Jev request per run.
7. **Jev failure default**: `on_jev_error = "rules_only"`. The app still notifies, based on rules alone, with a note in the message, and exits with 3. The user can switch to `"skip"`.
8. **"Good day"** means at least `min_good_hours` (default 1) of today's *remaining daylight* hourly slots pass every rule. Rules are checked hour by hour, so values from different hours are never mixed.
9. **"Today" and "now"** are in each spot's local time, from Surfline's `utcOffset`. "Current conditions" is the slot in effect now. Daylight is dawn to dusk from `sunlightTimes`, with local hours 06–19 as the fallback. The app is meant to run in the early morning of the spots' time zone (06:00 is recommended). A run after dusk sends nothing.
10. **Forecast request**: `days=1, intervalHours=1`, with no Surfline access token.
11. **Surfline units dict keys** are `waveHeight`, `swellHeight`, `windSpeed`, `tideHeight` and `temperature`, with values `FT`/`M`, `KTS`/`KPH`/`MPH` and `C`/`F`. A missing `swellHeight` falls back to `waveHeight`. An unknown required unit fails the spot instead of guessing.
12. **"Wave height"** maps to two metrics, `wave_height_min` and `wave_height_max` (Surfline's surf range). The user's example "above 2 ft" is written as `wave_height_min > 2` because that is the stricter reading. **"Period between waves"** is `swell_period`, the period of the largest non-zero swell.
13. **Display units** default to ft, kts and °C. They can be configured, and rule thresholds are read in the configured units.
14. **Message content**: the best spot first, with full current conditions, good-hour ranges and Jev confidence. Up to `max_spots_in_message` (default 3) spots appear in full, and the rest by name only. Dates are ISO and times are 24-hour local, so no locale-dependent formatting is used.
15. **No deduplication**: a single cron entry per day is expected, and running twice can send twice.
16. **Exit codes** 0/1/2/3/4 as in 4.12, so cron can show degraded runs.
17. **Secrets**: the variable names are `TYPESAFE_API_KEY`, `WHATSAPP_PHONE` and `CALLMEBOT_API_KEY`. `.env` is loaded with `python-dotenv` and never overrides real environment variables.
18. **Limits**: at most 50 spots, and at least 1 rule (an empty rule list is a config error).
19. **No retries** for any network call. The next daily run is the retry.
21. **Clarifications added after the test-strategy review**: whitespace-only secrets count as missing (4.12). `-v` raises only the `surf_alert` logger to DEBUG, never third-party loggers (4.12). Every Jev and CallMeBot error is redacted first and then truncated (6.3, 6.4). Units are validated before the same-unit shortcut (4.5). Config enum, metric and op names are case-sensitive, and blank `jev_model` or text values are invalid (6.1). Unreadable config paths get their own message (6.1). The phone check uses `re.fullmatch` (4.10). Setup and scheduling docs go in `README.md` (4.13).
20. **Example spot IDs**: only Pipeline's ID (`5842041f4e65fad6a7708890`) is real. `0123456789abcdef01234567` ("Sunset Beach") is fictional and used in examples and tests only.

## 9. Open questions

1. **Non-blocking**: Should the app alert about *tomorrow* (for example with an evening run) as well as today? The spec assumes today only (Assumptions 8 and 9).
2. **Non-blocking**: Should spots be added, removed, or searched (Surfline search) from the CLI? The spec assumes hand-editing the TOML file (Assumption 2).
3. **Non-blocking**: Should rules be configurable per spot (for example, offshore wind required only at certain beaches)? The spec assumes one global rule list. The per-spot `notes` field is passed to Jev as a softer alternative.
4. **Non-blocking**: When Jev is down, should the user still get a rules-only alert, or nothing? The default is a rules-only alert, configurable through `on_jev_error` (Assumption 7).
5. **Non-blocking**: Is CallMeBot acceptable, or does the user prefer Twilio or the Meta WhatsApp Cloud API? CallMeBot is assumed (Assumption 3). Only `notify.py` and `cli.make_notifier` would change.
6. **Non-blocking** (risk): The exact Surfline units-dict keys and CallMeBot's error responses could not be verified offline (Assumptions 4 and 11). Unknown required units fail loudly instead of guessing.
7. **Non-blocking** (risk): pysurfline is from 2022. Whether it installs on Python 3.13 (its pandas and matplotlib requirements), and whether Surfline's unauthenticated `kbyg` endpoints still answer, can only be confirmed by a live run. The design keeps `pysurfline` behind a lazy import, so the test suite does not depend on it. **Update (2026-10-07):** a live run found that `wave` answers HTTP 404, while the other forecasts still work. The default factory now rebuilds `wave` from `surf` and `swells` (4.5, 5.3), and the `surf_optimal_score` metric was replaced by `surf_rating`, because neither replacement forecast has an `optimalScore`.
