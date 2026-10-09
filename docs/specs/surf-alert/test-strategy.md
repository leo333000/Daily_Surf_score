# Surf Alert: testing strategy

Source of truth: `docs/specs/surf-alert/spec.md` (the spec), including the clarifications listed in Assumption 21. Section numbers like §4.9 refer to the spec. The tests are written without seeing the production code, so every call uses only the public interface in spec §4.

Revision 2: updated for the spec clarifications (whitespace-only secrets, logger levels, redaction order, unit validation order, case and blank-string rules, unreadable config paths, `re.fullmatch` for the phone check, README). TC-1 to TC-201 keep their numbers; TC-202 to TC-213 are new.

Revision 3 (2026-10-07): Surfline retired the `wave` forecast, so the default factory rebuilds it from the `surf` and `swells` forecasts and also fetches `rating` (spec AC-4.7, AC-4.8, §4.5, §5.3). The `surf_optimal_score` metric became `surf_rating`. TC-53 and TC-72 changed; TC-214 to TC-217 are new.

Revision 5 (2026-10-09): the `-v` report and spot table (spec 4.14): TC-219 to TC-225 are new; the autouse `isolate_report` fixture points `cli.REPORT_PATH` at the test's tmp directory.

Revision 4 (2026-10-09): the missing-variable error now explains each variable and why it is missing (spec 4.12 `check` step 2). TC-179, TC-182 (a)–(c), TC-185 and TC-212 expect the full message; TC-218 is new.

## 1. Objectives and scope

The tests must prove that:
- every acceptance criterion AC-1.1 to AC-17.4 holds exactly as written (values, messages, exit codes, stdout/stderr text);
- every §6 table row, every §6.1 "further rule" and every §6.5 edge case behaves as specified;
- the §4 public interface exists exactly as specified: module paths, names, parameter names and kinds, defaults, constants, dataclass field order;
- the testable non-functional requirements hold: offline injection points, lazy `pysurfline` import, 30 s timeouts, 0 or 1 Jev request and at most 1 WhatsApp request per run, redaction and logger levels (Security), and ASCII-plus-`°` output (Output);
- the deliverable files (`surf_alert.example.toml`, `.env.example`, `README.md`, `requirements.txt`, `python -m surf_alert`) exist and work.

Out of scope (not tested): live calls to Surfline, Jev or CallMeBot; whether the real `pysurfline` works on 3.13 (`pysurfline` is never imported in tests); Surfline hangs; retries (there are none); installing cron or launchd; log text beyond the checks in TC-50, TC-190 and TC-210; config files unreadable for permission reasons (macOS root and ACLs make `chmod` unreliable, so the directory case stands in for every `OSError`); Windows.

## 2. Approach

| Level | What | Why |
|---|---|---|
| Unit (majority) | One module at a time: `config`, `units`, `surfline.parse_forecast`, `evaluate`, `jev`, `decision`, `message`, `notify`. Collaborators are faked through the spec's injection points. | Most of the spec is pure functions with exact outputs. |
| Integration | `app.evaluate_spots` and `app.run_check` with real evaluate/decision/message code and fake `ForecastSource`/`JevLike`/`Notifier`. `cli.main(argv)` in-process, with the four `cli` module globals monkeypatched and `capsys`. | Covers orchestration, exit-code priority, the stdout/stderr contract, `.env` handling and logging setup. |
| End-to-end (offline) | (a) `python -m surf_alert` in a subprocess for no-network commands and usage errors. (b) In-process full stack: real `SurflineClient`, `JevClient` and `CallMeBotNotifier`, with only the forecast factory and HTTP sessions faked. (c) A subprocess check of the lazy import. | Covers the entry point and wiring with only the transport faked. |

## 3. Tooling

- Framework: **pytest >= 8** on Python 3.13 in `.venv/`, plus the standard library (`types`, `subprocess`, `tomllib`, `json`, `copy`, `dataclasses`, `inspect`, `logging`, `socket`, `math`, `urllib.parse`). No test-only plugins (no `responses`, `pytest-mock` or `freezegun`).
- Dependencies: `pytest>=8`, `requests` and `python-dotenv` are already in `requirements.txt` per spec §7. So there is **no new test-only package and no `requirements-dev.txt`**, and the test-implementer does not edit `requirements.txt`. Any package added later must be installed with `.venv/bin/pip install <pkg>` and added to `requirements.txt` (CLAUDE.md).
- Venv setup for tests. Skip `-r requirements.txt`, because its `pysurfline` git line needs network and may not build on 3.13, and tests never need it:
  `.venv/bin/pip install "requests>=2.31" "python-dotenv>=1.0" "pytest>=8"`
- `pytest.ini` at the project root (owned by the test-implementer). It must **not** set `log_level` or `log_cli_level`: TC-210 relies on pytest leaving the root logger at Python's default WARNING.
  ```ini
  [pytest]
  testpaths = tests
  pythonpath = .
  addopts = -ra --strict-markers --strict-config
  xfail_strict = true
  markers =
      e2e: runs `python -m surf_alert` or a fresh interpreter in a subprocess
  ```
- **Run the whole suite** (from the project root): `.venv/bin/python -m pytest`
  - one file: `.venv/bin/python -m pytest tests/test_config.py`
  - one test case: `.venv/bin/python -m pytest -k tc023`
  - skip subprocess tests: `.venv/bin/python -m pytest -m "not e2e"`

Conventions:
- Each test function is named `test_tcNNN_<short_name>` (zero-padded TC number) and its docstring starts with `TC-N:`. Parametrized cases share one function.
- Exception messages are checked with `pytest.raises(T) as ei` and `assert str(ei.value) == msg`. Never use `match=`, because the messages contain regex metacharacters.
- Floats are compared exactly unless a case says `approx`.
- The tests enforce the container types the spec fixes (§4.3, §4.11): tuples in the dataclasses (`AppConfig.spots/rules`, `Rule.value` for `in`/`not_in`, `ForecastData.slots/daylight`, `SpotEvaluation.window/good_slots`, `Decision.recommendations`, `RunResult.evaluations`). `evaluate_spots` returns `(list, dict)`.

## 4. Test layout

```
pytest.ini
tests/
  __init__.py          # empty; makes `tests.scenario` / `tests.fakes` importable
  conftest.py          # autouse isolation fixtures + shared fixtures (section 5)
  scenario.py          # reference config/scenario/expected outputs (spec 5.1, 5.4, 5.5, 5.6), data and builders only
  fakes.py             # FakeResponse, FakeSession, RoutingSession, FakeForecastSource, FakeJev, FakeNotifier
  test_interface.py    # TC-1..9                 package, errors, models, constants, signatures
  test_config.py       # TC-10..43, 202..205     FR-1, spec 6.1
  test_units.py        # TC-44..47, 206          units.py
  test_surfline.py     # TC-48..74, 214..217     FR-4, FR-5, spec 6.2
  test_evaluate.py     # TC-75..95               FR-6, FR-7, FR-3 (describe_rule)
  test_jev.py          # TC-96..104, 207         FR-9, spec 6.3
  test_decision.py     # TC-105..123             FR-8, FR-10, FR-11
  test_message.py      # TC-124..136             FR-12, AC-15.5, Output NFR
  test_notify.py       # TC-137..144, 208, 209   FR-13, spec 6.4
  test_app.py          # TC-145..159             evaluate_spots, run_check (FR-14)
  test_cli.py          # TC-160..192, 210..212, 218, 223..225   FR-2, FR-3, FR-14..17, logging, missing-variable reasons, -v report, through cli.main
  test_report.py       # TC-219..222             spec 4.14
  test_e2e.py          # TC-193..198             subprocess + full-stack offline runs
  test_artifacts.py    # TC-199..201, 213        example files, requirements.txt, README.md
```

## 5. Fixtures and test data

### 5.1 Autouse fixtures (`tests/conftest.py`)

| Fixture | Behaviour |
|---|---|
| `isolate_env` | Snapshots `dict(os.environ)`, then runs `monkeypatch.delenv(..., raising=False)` for `TYPESAFE_API_KEY`, `WHATSAPP_PHONE` and `CALLMEBOT_API_KEY`. At teardown it restores `os.environ` exactly (`clear()` + `update(snapshot)`). Reason: `load_dotenv` writes to `os.environ` directly, and `monkeypatch` does not undo writes to keys that were absent. |
| `no_network` | Uses `monkeypatch.setattr` to replace `socket.socket.connect`, `socket.socket.connect_ex` and `socket.getaddrinfo` with functions raising `RuntimeError("network access is disabled in tests")`. |
| `block_pysurfline` | `monkeypatch.setitem(sys.modules, "pysurfline", None)`, so `import pysurfline` raises `ImportError` whether or not it is installed. TC-53 overrides it. |
| `reset_logging` | Records the level and handler list of the root logger and of `logging.getLogger("surf_alert")`. At teardown it restores both levels and removes any handler added during the test whose `type(h) is logging.StreamHandler` (exact type, so pytest's own handlers, which are subclasses, survive). This undoes `cli.main`'s `basicConfig` and logger-level changes between tests. |

### 5.2 Shared fixtures (`tests/conftest.py`)

| Fixture | Returns |
|---|---|
| `ref_config_path(tmp_path)` | `tmp_path / "surf_alert.toml"` containing `REF_CONFIG_TOML`. |
| `missing_env_path(tmp_path)` | `tmp_path / "missing.env"` (never created). |
| `write_config(tmp_path)` | A function `(text, name="cfg.toml") -> Path`. |
| `secrets_env(monkeypatch)` | Sets `TYPESAFE_API_KEY="ts-key"`, `WHATSAPP_PHONE="+34612345678"` and `CALLMEBOT_API_KEY="cmb-key"`. |
| `cli_fakes(monkeypatch)` | An object with mutable attributes `surfline` (default `FakeForecastSource({"Pipeline": pipeline_forecast(), "Sunset Beach": sunset_forecast()})`), `jev` (default `FakeJev(answers=REF_ANSWERS)`), `notifier` (default `FakeNotifier()`) and `now` (default `NOW`). It also has `originals`, which holds the real `surf_alert.cli.make_surfline_client`, `make_jev_client`, `make_notifier` and `utc_now` captured before patching. Call logs: `surfline_calls` (list of `settings`), `jev_calls` (list of `(api_key, settings)`) and `notifier_calls` (list of `(phone, api_key)`). It monkeypatches `surf_alert.cli.make_surfline_client(settings)`, `make_jev_client(api_key, settings)`, `make_notifier(phone, api_key)` and `utc_now()` with plain functions that record their arguments and return the current attribute. Arguments may be passed positionally or by keyword. |
| `run_cli(capsys)` | A function `(*args, config=<ref_config_path>, env_file=<missing_env_path>) -> CliResult(rc, out, err)`. It builds `argv = ["--config", str(config), "--env-file", str(env_file), *args]`, omitting a pair when its value is `None`. Then it calls `surf_alert.cli.main(argv)` and returns the return value plus `capsys.readouterr()`. |

### 5.3 Reference data (`tests/scenario.py`)

Constants:
- `NOW = datetime(2026, 10, 7, 17, 30, tzinfo=timezone.utc)` (epoch `1791394200`, 07:30 local at -10).
- `LOCAL_MIDNIGHT = 1791367200`. `local_ts(h) = LOCAL_MIDNIGHT + 3600 * h` (`h` may fall outside 0..23). `utc_dt(ts) = datetime.fromtimestamp(ts, tz=timezone.utc)`.
- `DAWN = 1791389400`, `SUNRISE = 1791391200`, `SUNSET_TS = 1791432600`, `DUSK = 1791434400`.
- `REF_UNITS = {"temperature": "C", "tideHeight": "FT", "swellHeight": "FT", "waveHeight": "FT", "windSpeed": "KTS"}`.
- `REF_SUNLIGHT`: the §5.6 one-element `sunlightTimes` list. `REF_DAYLIGHT = (DaylightWindow(date(2026, 10, 7), DAWN, DUSK),)`.
- `PIPELINE = Spot("Pipeline", "5842041f4e65fad6a7708890", "Only surf it when it is under 6 ft.")`, `SUNSET = Spot("Sunset Beach", "0123456789abcdef01234567", "")`, `ROCKY = Spot("Rocky Point", "aaaaaaaaaaaaaaaaaaaaaaaa", "")`.
- `REF_RULES = (Rule("wave_height_min", ">", 2.0), Rule("swell_period", ">", 7.0))`.
- `REF_SETTINGS = Settings(notes="Intermediate surfer; prefer offshore or light winds.")`.
- `ref_app_config(**settings_overrides)` returns `AppConfig(replace(REF_SETTINGS, **overrides), (PIPELINE, SUNSET), REF_RULES)`.
- `REF_CONFIG_TOML`: the §5.1 TOML block verbatim, comments included. `ref_config_dict()` returns a fresh `tomllib.loads(REF_CONFIG_TOML)` each call.

Raw pysurfline-shaped data (`types.SimpleNamespace`, exactly per §5.6):
- `pipeline_raw()`: 24 entries for h 0..23 in each of `wave`, `wind`, `tides` and `weather`, each with `timestamp=local_ts(h)` and `utcOffset=-10`.
  - `wave`: hours 7..11 have `surf_min 2.4255` and `surf_max 3.3482`; every other hour has `1.2` and `1.9`. Every hour has the three §5.6 swells.
  - `wind`: `speed 8.4`, `direction 45.0`, `directionType "Offshore"`, `gust 12.3`, `optimalScore 1`.
  - `tides`: `type "NORMAL"`, `height 1.3`.
  - `weather`: `temperature 24.0`, `condition "MOSTLY_CLEAR"`.
  - `rating`: `rating_entry(ts, 4, "FAIR_TO_GOOD")` for every hour.
  - `sunlightTimes` is a deep copy of `REF_SUNLIGHT`; `units_wave`, `units_wind`, `units_tides` and `units_weather` are each a copy of `REF_UNITS`.
- `sunset_raw()`: same structure.
  - `wave`: `surf_min 2.5` at hours 8, 9, 10, 15 and 16, otherwise `1.9`; `surf_max 2.8` everywhere; a single swell `{"height": 3.9, "period": 10, "direction": 310.0, "directionMin": 300.0, "optimalScore": 1}`.
  - `wind`: `11.0`, `90.0`, `"Cross-shore"`, gust `15.0`.
  - `tides`: `1.3`. `weather`: `24.0`, `"MOSTLY_CLEAR"`. `rating`: `rating_entry(ts, 3, "FAIR")`.
- Small builders:
  - `wave_entry(ts=local_ts(7), utc_offset=-10, surf_min=2.0, surf_max=3.0, swells=None)`
  - `wind_entry(ts, speed=8.4, gust=12.3, direction=45.0, direction_type="Offshore")`
  - `tide_entry(ts, height=1.3)`
  - `weather_entry(ts, temperature=24.0, condition="MOSTLY_CLEAR")`
  - `rating_entry(ts, value=4, key="FAIR_TO_GOOD")`
  - `make_raw(**attrs)`: defaults to `wave=[wave_entry()]`, `wind=[wind_entry()]` and copies of `REF_UNITS` for `units_wave` and `units_wind`. Pass `omit=("wave",)` to leave an attribute unset. It sets exactly the given attributes.
  - `pysurfline_raw(raw)`: a copy of `raw` without `wave`, `units_wave` and `rating`, which is what the real pysurfline now returns.
  - `forecast_bodies(raw)`: `{"surf": body, "swells": body, "rating": body}`, Surfline response bodies (§5.3 shapes) carrying `raw`'s wave entries, units and ratings.

Parsed and evaluated equivalents (built directly, never by calling production code):
- `make_slot(h=7, **overrides)`. Defaults: `timestamp=local_ts(h)`, `utc_offset=-10.0`, `surf_min=2.4255`, `surf_max=3.3482`, `surf_rating=4`, `primary_swell=Swell(4.2, 11.0, 315.0)`, `wind_speed=8.4`, `wind_gust=12.3`, `wind_direction=45.0`, `wind_direction_type="Offshore"`, `tide_height=1.3`, `temperature=24.0`, `weather_condition="MOSTLY_CLEAR"`.
- `pipeline_slot(h, good)`: `make_slot(h)` with `surf_min/surf_max` of `2.4255/3.3482` if `good`, else `1.2/1.9`.
- `sunset_slot(h, good)`: `surf_min` `2.5` if `good` else `1.9`, `surf_max 2.8`, score `1`, `Swell(3.9, 10.0, 310.0)`, wind `11.0`/`15.0`/`90.0`/`"Cross-shore"`, tide `1.3`, temperature `24.0`, `"MOSTLY_CLEAR"`, offset `-10.0`.
- `pipeline_forecast(good_hours=range(7, 12), daylight=REF_DAYLIGHT)` and `sunset_forecast(good_hours=(8, 9, 10, 15, 16), daylight=REF_DAYLIGHT)`: 24 slots each, for h 0..23.
- `pipeline_evaluation()`: `SpotEvaluation(PIPELINE, date(2026,10,7), current=pipeline_slot(7, True), window=<slots h 7..18>, good_slots=<slots h 7..11>, passes_rules=True)`.
- `sunset_evaluation()`: current is the h 7 slot (not good), window is h 7..18, good slots are h 8, 9, 10, 15 and 16, `passes_rules=True`.
- `rocky_evaluation()`: `replace(pipeline_evaluation(), spot=ROCKY, good_slots=pipeline_evaluation().good_slots[:3])`.
- `REF_DECISION = Decision((Recommendation(pipeline_evaluation(), 0.92), Recommendation(sunset_evaluation(), 0.81)), jev_used=True, jev_error=None)`.

Jev data:
- `REF_ANSWERS` and `REF_JEV_RESPONSE`: the §5.4 `answers` object and the full HTTP 200 body.
- `REF_QUESTIONS`: the §5.4 `questions` dict as a literal. `good_spot_2` uses the template text with `Sunset Beach` and `spot_2`.
- `REF_STATE`: a literal dict whose keys appear in this order: `units`, `rules`, `notes`, `min_good_hours`, `spots`.
  - `units`: `{"height": "ft", "period": "s", "wind": "kts", "temperature": "C"}`
  - `rules`: `["wave_height_min > 2 ft", "swell_period > 7 s"]`
  - `notes`: `REF_SETTINGS.notes`; `min_good_hours`: `1`
  - `spots`: two entries, each with keys in the order `ref`, `name`, `notes`, `local_date`, `good_hours`, `current`, `today`:
    - Pipeline: `"spot_1"`, `"Pipeline"`, its notes, `"2026-10-07"`, `["07:00","08:00","09:00","10:00","11:00"]`; `current` is `today[0]`; `today` holds 12 SLOT dicts for h 7..18.
    - Sunset Beach: `"spot_2"`, `"Sunset Beach"`, `""`, `"2026-10-07"`, `["08:00","09:00","10:00","15:00","16:00"]`; `current` is `today[0]`; 12 SLOT dicts.
  - Each SLOT dict has keys in this order: `time`, `surf_min`, `surf_max`, `surf_rating`, `swell_height`, `swell_period`, `swell_direction`, `wind_speed`, `wind_gust`, `wind_direction`, `wind_direction_type`, `tide_height`, `temperature`, `weather_condition`, `passes_rules`.
    - Pipeline hour h: `f"{h:02d}:00"`; `2.4`/`3.3` if 7≤h≤11, else `1.2`/`1.9`; `2`; `4.2, 11.0, 315.0`; `8.4, 12.3, 45.0, "Offshore"`; `1.3`; `24.0`; `"MOSTLY_CLEAR"`; `passes_rules = 7<=h<=11`.
    - Sunset hour h: `2.5` if h is in {8, 9, 10, 15, 16}, else `1.9`; `2.8`; `1`; `3.9, 10.0, 310.0`; `11.0, 15.0, 90.0, "Cross-shore"`; `1.3`; `24.0`; `"MOSTLY_CLEAR"`; `passes_rules` is true for those same hours.

Expected texts (literal strings with `\n` line breaks; no trailing newline unless shown):
- `PIPELINE_BLOCK` lines:
  - L1 `Pipeline - now (07:00 local)`
  - L2 `Surf: 2.4-3.3 ft`
  - L3 `Swell: 4.2 ft @ 11s from NW (315°)`
  - L4 `Wind: 8 kts Offshore (gusts 12 kts)`
  - L5 `Tide: 1.3 ft`
  - L6 `Weather: 24°C, Mostly clear`
  - L7 `Good hours today: 07:00-12:00`
- `SUNSET_BLOCK` lines:
  - `Sunset Beach - now (07:00 local)`
  - `Surf: 1.9-2.8 ft`
  - `Swell: 3.9 ft @ 10s from NW (310°)`
  - `Wind: 11 kts Cross-shore (gusts 15 kts)`
  - `Tide: 1.3 ft`
  - `Weather: 24°C, Mostly clear`
  - `Good hours today: 08:00-11:00, 15:00-17:00`
- `REF_MESSAGE`: the §5.5 text verbatim. A self-check in TC-131 asserts that it equals `"Surf alert 2026-10-07\nGo surf at Pipeline!\n\n" + PIPELINE_BLOCK + "\nJev confidence: 92%\n\nAlso good: " + SUNSET_BLOCK + "\nJev confidence: 81%"`.
- `REF_MESSAGE_RULES_ONLY = "Surf alert 2026-10-07\nGo surf at Pipeline!\n\n" + PIPELINE_BLOCK + "\n\nAlso good: " + SUNSET_BLOCK + "\n\nNote: Jev was unavailable, so this recommendation is based on your rules only."`
- `PIPELINE_ONLY_MESSAGE_90 = "Surf alert 2026-10-07\nGo surf at Pipeline!\n\n" + PIPELINE_BLOCK + "\nJev confidence: 90%"`
- `SUNSET_ONLY_MESSAGE_81 = "Surf alert 2026-10-07\nGo surf at Sunset Beach!\n\n" + SUNSET_BLOCK + "\nJev confidence: 81%"`
- `REF_CONDITIONS_OUTPUT = PIPELINE_BLOCK + "\nRules: PASS (good hours today: 5, required: 1)\n\n" + SUNSET_BLOCK + "\nRules: PASS (good hours today: 5, required: 1)\n"`

Files: all config and `.env` files go in `tmp_path`. Tests that rely on default paths call `monkeypatch.chdir(tmp_path)`. Only `test_artifacts.py` and the subprocess `PYTHONPATH` touch the real project root (`PROJECT_ROOT = Path(__file__).resolve().parents[1]`).

## 6. Mocking

| Dependency | How | Where |
|---|---|---|
| Surfline / pysurfline | `SurflineClient(settings, forecast_factory=fake)`: the fake records `(args, kwargs)` and returns a raw `SimpleNamespace`. `FakeForecastSource.fetch(spot)` uses a `{name: ForecastData or Exception}` map and records `fetched`. `cli.make_surfline_client` is patched. `pysurfline` is blocked in `sys.modules`; TC-53 and TC-214 to TC-216 inject a fake `types.ModuleType("pysurfline")` and pass `session=RoutingSession({url: FakeResponse or exception})`, which records each `get` like `FakeSession` and answers it by URL. | surfline, app, cli, e2e |
| Jev HTTP | `FakeSession.post(self, url, *, headers, json, timeout)` records `url`, `headers`, a deep copy of `json` and `timeout`, then returns a `FakeResponse` or raises. `FakeJev.ask(state, questions)` records deep copies and returns answers or raises. `cli.make_jev_client` is patched. Only TC-192 patches `requests.Session.post` on the class. | jev, decision, app, cli, e2e |
| CallMeBot | `FakeSession.get(self, url, *, params, timeout)` behaves the same way. `FakeNotifier.send(text)` appends to `sent` and optionally raises. `cli.make_notifier` is patched. TC-192 patches `requests.Session.get`. | notify, app, cli, e2e |
| HTTP response | `FakeResponse(status_code=200, json_data=<missing>, text=None, json_exc=None)`. `.text` defaults to `json.dumps(json_data)` when data is given, else `""`. `.json()` raises `json_exc` if set, raises `ValueError("not JSON")` when there is no data, and otherwise returns a deep copy. | jev, notify |
| Clock | The `now` parameters. `surf_alert.cli.utc_now` is patched to return `cli_fakes.now`. | all |
| Env / `.env` | `monkeypatch.setenv`/`delenv` plus the autouse snapshot. `.env` files are written in `tmp_path`. `--env-file` is always given. | cli, e2e |
| stdout, stderr, logs | `capsys`; `caplog` (always `caplog.set_level(..., logger="surf_alert")`, never on the root logger); `subprocess.run(capture_output=True, text=True, encoding="utf-8")`. | |

## 7. Test cases

"ref" means the section 5.3 data. `settings = Settings()` unless stated otherwise.

### 7.1 `tests/test_interface.py` (unit)

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-1 | §4.1 | `importlib.import_module` on `surf_alert` and `surf_alert.{errors,models,config,units,surfline,evaluate,jev,decision,message,notify,app,cli}` | All import. `surf_alert.__version__ == "0.1.0"`. |
| TC-2 | §4.2 | Construct `ConfigError`, `SurflineError`, `JevError` and `NotificationError` with `"msg"` | Each is a subclass of `SurfAlertError`, which subclasses `Exception`. `str(exc) == "msg"`. |
| TC-3 | §4.2 | `JevError("m")`, `JevError("m", 429)`, `JevError("m", status_code=401)` | `status_code` is `None`, `429` and `401`; `str` is `"m"` each time. |
| TC-4 | §4.3 | `[f.name for f in dataclasses.fields(C)]` | Spot: `name, spot_id, notes`.<br>Rule: `metric, op, value`.<br>Settings: `height_unit, wind_unit, temperature_unit, min_good_hours, jev_threshold, jev_model, on_jev_error, max_spots_in_message, notes`.<br>AppConfig: `settings, spots, rules`.<br>Swell: `height, period, direction`.<br>Slot: `timestamp, utc_offset, surf_min, surf_max, surf_rating, primary_swell, wind_speed, wind_gust, wind_direction, wind_direction_type, tide_height, temperature, weather_condition`.<br>DaylightWindow: `local_date, dawn, dusk`.<br>ForecastData: `spot, slots, daylight`.<br>SpotEvaluation: `spot, local_date, current, window, good_slots, passes_rules`.<br>Recommendation: `evaluation, probability`.<br>Decision: `recommendations, jev_used, jev_error`.<br>RunResult: `evaluations, failures, decision, message, sent, send_error, exit_code`. |
| TC-5 | §4.3 frozen | Assign an attribute on an instance of each dataclass | `dataclasses.FrozenInstanceError`. |
| TC-6 | §4.3 defaults | `Settings()`, `Spot("a", "b")`, `Decision((), True)` | `Settings() == Settings("ft","kts","C",1,0.7,"jev-latest","rules_only",3,"")`; `.notes == ""`; `.jev_error is None`. |
| TC-7 | `Slot.local_datetime`, §6.5 | (a) `make_slot(7)`<br>(b) `make_slot(timestamp=1791331200+7200, utc_offset=5.5)`<br>(c) `make_slot(timestamp=1791392400, utc_offset=-9.5)` | (a) `datetime(2026,10,7,7,0)`, naive<br>(b) `datetime(2026,10,7,7,30)`<br>(c) `datetime(2026,10,7,7,30)` |
| TC-8 | §4.4–4.10 constants | Read the constants | `config.METRICS` equals the §4.4 dict, in that order. `NUMERIC_OPS == (">",">=","<","<=","==","!=")`, `TEXT_OPS == ("==","!=","in","not_in")`, `ALL_OPS ==` all eight in §4.4 order, `MAX_SPOTS == 50`.<br>`FT_PER_M == 3.28084`, `KPH_PER_KT == 1.852`, `MPH_PER_KT == 1.150779`.<br>`FORECAST_DAYS == 1`, `FORECAST_INTERVAL_HOURS == 1`, `MATCH_TOLERANCE_SECONDS == 3600`, `KBYG_URL == "https://services.surfline.com/kbyg/spots/forecasts/{}"`, surfline `DEFAULT_TIMEOUT == 30.0`, `FALLBACK_DAYLIGHT_HOURS == (6, 19)`.<br>`JEV_URL == "https://api.typesafe.ai/v1/systemone"`, `DEFAULT_TIMEOUT == 30.0`.<br>`NOUL_TEMPLATE` and `BEST_SPOT_INSTRUCTIONS` equal the §4.8 strings.<br>`CALLMEBOT_URL == "https://api.callmebot.com/whatsapp.php"`, `PHONE_PATTERN == r"^\+[1-9][0-9]{6,14}$"`, `TEST_MESSAGE == "Surf alert test message: WhatsApp notifications are working."`.<br>`ForecastSource`, `JevLike` and `Notifier` exist. |
| TC-9 | §4 signatures | `inspect.signature`, comparing `(name, kind, default)` for each parameter | `load_config(path)`, `parse_config(data)`, `convert_*(value, from_unit, to_unit)`, `SurflineClient(settings, forecast_factory=None, *, session=None, timeout=30.0)`, `.fetch(spot)`, `parse_forecast(raw, spot, settings)`, `local_date_of(timestamp, utc_offset)`, `current_slot(slots, now)`, `evaluation_window(forecast, now)`, `metric_value(slot, metric)`, `rule_passes(rule, slot)`, `slot_passes(rules, slot)`, `evaluate_spot(forecast, rules, settings, now)`, `describe_rule(rule, settings)`, `JevClient(api_key, *, model="jev-latest", session=None, timeout=30.0)`, `.ask(state, questions)`, `candidate_ref(index)`, `build_jev_request(candidates, rules, settings)`, `interpret_jev_answers(answers, candidates, settings)`, `decide(candidates, rules, settings, jev)`, `compass(degrees)`, `format_hours(slots)`, `format_spot_block(evaluation, settings, probability=None)`, `format_rules_status(evaluation, settings)`, `format_message(decision, settings)`, `CallMeBotNotifier(phone, api_key, *, session=None, timeout=30.0)`, `.send(text)`, `evaluate_spots(config, surfline, now, spots=None)`, `run_check(config, *, surfline, jev, notifier, now)`, `make_surfline_client(settings)`, `make_jev_client(api_key, settings)`, `make_notifier(phone, api_key)`, `utc_now()`, `main(argv=None)`. Parameters after `*` are `KEYWORD_ONLY`; all others are `POSITIONAL_OR_KEYWORD`. |

### 7.2 `tests/test_config.py` (FR-1, §6.1; unit)

Validation tests call `parse_config(d)`, where `d = ref_config_dict()` with one change. Each expects `ConfigError` whose `str(exc) ==` the message shown.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-10 | AC-1.1 | `load_config(ref_config_path)`, once as `str` and once as `Path` | `spots == (PIPELINE, SUNSET)`, `rules == REF_RULES`, `settings == REF_SETTINGS`; `spots` and `rules` are tuples. |
| TC-11 | AC-1.2 | `load_config(tmp_path/"nope.toml")` | `ConfigError("config file not found: " + str(path))` |
| TC-12 | AC-1.3 | File content `spots = [` | The message starts with `f"invalid TOML in {path}: "` and is longer than that prefix. |
| TC-13 | AC-1.4 via a file | `REF_CONFIG_TOML` with `height_unit = "yd"` | `ConfigError("settings.height_unit must be one of: ft, m")` |
| TC-14 | AC-1.5 | Settings with `"FT"`, `"KPH"`, `"f"` | `"ft"`, `"kph"`, `"F"` |
| TC-15 | AC-1.6 | Rules `swell_period > 7`, `wind_direction_type in ["Offshore","Cross-shore"]`, `wind_direction_type == "Offshore"` | `value == 7.0` and is a `float`; `("Offshore","Cross-shore")` as a tuple; `"Offshore"` |
| TC-16 | AC-1.7 | Delete `settings`; or `settings = {}` | `settings == Settings()` |
| TC-17 | AC-1.8 | `spots[0].name = "  Pipeline  "` | `"Pipeline"` |
| TC-18 | §5.1 valid ranges, §6.1 spot ids | (a) Settings `{height_unit "m", wind_unit "mph", temperature_unit "F", min_good_hours 24, jev_threshold 1, jev_model "jev-1.13.0", on_jev_error "skip", max_spots_in_message 10, notes ""}`<br>(b) `min_good_hours 1`, `max_spots_in_message 1`, `jev_threshold 0.001`<br>(c) `wind_unit "kph"`<br>(d) `spots[0].id = "5842041F4E65FAD6A7708890"` | (a) `Settings("m","mph","F",24,1.0,"jev-1.13.0","skip",10,"")`, compared with `==`<br>(b), (c) the values round-trip<br>(d) `spots[0].spot_id == "5842041F4E65FAD6A7708890"` (stored as written) |
| TC-19 | binary read | Pipeline notes `"Café °"` | `spots[0].notes == "Café °"` |
| TC-20 | unknown top-level key | `d["foo"] = 1`; `d["Settings"] = {}` | `unknown key 'foo' in top level`; `unknown key 'Settings' in top level` |
| TC-21 | settings type | `settings` = `5`, `"ft"`, `[1]` | `settings must be a table` |
| TC-22 | settings unknown key | `settings["height"] = "ft"` | `unknown key 'height' in settings` |
| TC-23 | settings values | Table A | The table A message |
| TC-24 | spots list | Delete `spots`; `[]`; `"Pipeline"`; `{"name": "x"}` | `config must define at least one [[spots]] entry` |
| TC-25 | spot type | `spots[1] = "Sunset"` | `spots[1] must be a table` |
| TC-26 | spot unknown key | `spots[0]["url"] = "x"` | `unknown key 'url' in spots[0]` |
| TC-27 | spot name | `spots[1].name` missing, `5`, `""`, `"   "`, `["x"]` | `spots[1].name must be a non-empty string` |
| TC-28 | spot id | `spots[0].id` missing, `5842041`, `"xyz"`, `"5842041f4e65fad6a770889"` (23 chars), `"5842041f4e65fad6a77088900"` (25 chars), `"5842041f4e65fad6a770889g"` | `spots[0].id must be a 24-character hexadecimal Surfline spot id` |
| TC-29 | spot notes | `spots[1]["notes"] = 5` | `spots[1].notes must be a string` |
| TC-30 | duplicate name | `spots[1] = {"name": "  pipeline ", "id": SUNSET id}`; then `"PIPELINE"` | `duplicate spot name: pipeline`; `duplicate spot name: PIPELINE` |
| TC-31 | duplicate id, §6.1 spot ids | `spots[1].id` = `"5842041f4e65fad6a7708890"`; then `"5842041F4E65FAD6A7708890"` | `duplicate spot id: 5842041f4e65fad6a7708890`; `duplicate spot id: 5842041F4E65FAD6A7708890` (later id as written) |
| TC-32 | spot count, Volume NFR | 50 spots, then 51 spots, each `{"name": f"Spot {i}", "id": f"{i:024x}"}` | 50 spots load; 51 gives `config may define at most 50 spots` |
| TC-33 | rules list | Delete `rules`; `[]`; `"x"`; `{}` | `config must define at least one [[rules]] entry` |
| TC-34 | rule type | `rules[1] = 5` | `rules[1] must be a table` |
| TC-35 | rule unknown key | `rules[0]["unit"] = "ft"` | `unknown key 'unit' in rules[0]` |
| TC-36 | metric | `rules[1].metric` missing, `"wave_height"`, `5` | `rules[1].metric must be one of: wave_height_min, wave_height_max, surf_rating, swell_height, swell_period, wind_speed, wind_gust, wind_direction_type, tide_height, temperature, weather_condition` |
| TC-37 | op | `rules[0].op` missing, `"=>"`, `"contains"`, `""`, `5` | `rules[0].op must be one of: >, >=, <, <=, ==, !=, in, not_in` |
| TC-38 | op/kind | `(wave_height_min,"in",["2"])`, `(swell_period,"not_in",["7"])`, `(wind_direction_type,">","Offshore")`, `(weather_condition,"<=","CLEAR")` | `rules[0].op 'in' is not valid for metric 'wave_height_min'`; `... 'not_in' ... 'swell_period'`; `... '>' ... 'wind_direction_type'`; `... '<=' ... 'weather_condition'` |
| TC-39 | numeric value | value missing, `"2"`, `True`, `[2]` | `rules[0].value must be a number` |
| TC-40 | text `==`/`!=` value | `wind_direction_type ==` with value missing, `""`, `5`, `["Offshore"]`, `True`; `weather_condition != 5` | `rules[0].value must be a non-empty string` |
| TC-41 | text `in`/`not_in` value | `in` with value missing, `[]`, `"Offshore"`, `["Offshore", 5]`; `not_in ["Offshore", ""]` | `rules[0].value must be a non-empty list of non-empty strings` |
| TC-42 | valid rules | `wind_direction_type in ["Offshore","Cross-shore"]`, `weather_condition != "RAIN"`, `wind_direction_type not_in ["Onshore"]`, `wind_speed <= 15`, `surf_rating == 3`, `wave_height_max != 2.5` | `(Rule("wind_direction_type","in",("Offshore","Cross-shore")), Rule("weather_condition","!=","RAIN"), Rule("wind_direction_type","not_in",("Onshore",)), Rule("wind_speed","<=",15.0), Rule("surf_rating","==",3.0), Rule("wave_height_max","!=",2.5))` |
| TC-43 | validation order | Table B | The table B message |
| TC-202 | §6.1 unreadable path | (a) `p = tmp_path/"cfgdir"; p.mkdir(); load_config(p)`<br>(b) `run_cli("spots", config=p)` | (a) `ConfigError("cannot read config file: " + str(p))`<br>(b) stderr `f"error: cannot read config file: {p}\n"`, rc 1, stdout `""` |
| TC-203 | §6.1 Case | `on_jev_error` = `"SKIP"`, `"Rules_Only"`; `rules[0].metric` = `"Swell_Period"`, `"WAVE_HEIGHT_MIN"`; `rules[0]` = `{wind_direction_type, "IN", ["Offshore"]}` and `{.., "NOT_IN", ["Onshore"]}`. Contrast: `height_unit "Ft"`, `wind_unit "Kts"`, `temperature_unit "c"` | `settings.on_jev_error must be one of: rules_only, skip`; the TC-36 metric message with index 0; the TC-37 op message. The contrast case loads, giving `"ft"`, `"kts"` and `"C"`. |
| TC-204 | §6.1 Blank strings | Invalid: `jev_model "   "`; `{wind_direction_type, "==", "   "}`; `{weather_condition, "!=", "\t"}`; `{wind_direction_type, "in", ["Offshore", "  "]}`.<br>Valid, stored as written: `jev_model " jev-x "`; `{wind_direction_type, "==", " Offshore "}`; `in [" Offshore", "Cross-shore "]`; `settings.notes "  hi  "`; `spots[1].notes " n "` | `settings.jev_model must be a non-empty string`; `rules[0].value must be a non-empty string` (twice); `rules[0].value must be a non-empty list of non-empty strings`.<br>Stored values: `" jev-x "`, `" Offshore "`, `(" Offshore", "Cross-shore ")`, `"  hi  "`, `" n "` |
| TC-205 | §6.1 Unknown keys | `d["zzz"]=1; d["aaa"]=2`; `settings` gets `zeta` then `alpha`; `spots[0]` gets `zeta` then `alpha`; `rules[0]` gets `zeta` then `alpha`. File variant: `REF_CONFIG_TOML` with `beta = 1` then `alpha = 2` added under `[settings]`, loaded with `load_config` | `unknown key 'zzz' in top level`; `unknown key 'zeta' in settings`; `unknown key 'zeta' in spots[0]`; `unknown key 'zeta' in rules[0]`; `unknown key 'beta' in settings` |

Table A (TC-23):

| key | bad values | message |
|---|---|---|
| height_unit | `"yd"`, `""`, `5` | `settings.height_unit must be one of: ft, m` |
| wind_unit | `"knots"`, `"m/s"`, `10` | `settings.wind_unit must be one of: kts, kph, mph` |
| temperature_unit | `"K"`, `"celsius"`, `1` | `settings.temperature_unit must be one of: C, F` |
| min_good_hours | `0`, `25`, `-1`, `1.5`, `2.0`, `True`, `"3"` | `settings.min_good_hours must be an integer between 1 and 24` |
| jev_threshold | `0`, `0.0`, `-0.1`, `1.01`, `2`, `True`, `"0.7"` | `settings.jev_threshold must be a number greater than 0 and at most 1` |
| jev_model | `""`, `5`, `["jev-latest"]` | `settings.jev_model must be a non-empty string` |
| on_jev_error | `"abort"`, `""`, `1` | `settings.on_jev_error must be one of: rules_only, skip` |
| max_spots_in_message | `0`, `11`, `3.0`, `True`, `"3"` | `settings.max_spots_in_message must be an integer between 1 and 10` |
| notes | `5`, `True`, `["x"]` | `settings.notes must be a string` |

Table B (TC-43):

| Errors present | Expected |
|---|---|
| top-level `foo = 1` and `settings = 5` | `unknown key 'foo' in top level` |
| settings `{"bogus": 1, "height_unit": "yd"}` | `unknown key 'bogus' in settings` |
| settings `{"wind_unit": "x", "height_unit": "yd"}` (dict order reversed) | `settings.height_unit must be one of: ft, m` |
| settings `{"notes": 5, "min_good_hours": 0}` | `settings.min_good_hours must be an integer between 1 and 24` |
| settings `{"max_spots_in_message": 0, "jev_threshold": 0}` | `settings.jev_threshold must be a number greater than 0 and at most 1` |
| bad `height_unit` and `spots = []` | `settings.height_unit must be one of: ft, m` |
| `spots[0]` has unknown key `url` and `name ""` | `unknown key 'url' in spots[0]` |
| `spots[0]` has `name ""` and `id "x"` | `spots[0].name must be a non-empty string` |
| `spots[0]` has `id "x"` and `notes 5` | `spots[0].id must be a 24-character hexadecimal Surfline spot id` |
| `spots[1]` repeats both the name and the id of `spots[0]` | `duplicate spot name: Pipeline` |
| 51 valid spots, with spot 50 named `"Spot 0"` | `duplicate spot name: Spot 0` |
| 51 valid spots and `rules = []` | `config may define at most 50 spots` |
| `spots[0].id "x"` and `rules = []` | `spots[0].id must be a 24-character hexadecimal Surfline spot id` |
| `spots = []` and `rules = []` | `config must define at least one [[spots]] entry` |
| `rules[0]` has unknown key `x` and metric `"nope"` | `unknown key 'x' in rules[0]` |
| `rules[0]` has metric `"nope"` and op `"=>"` | the TC-36 metric message with index 0 |
| `rules[0]` has op `"=>"` and value `"x"` | `rules[0].op must be one of: >, >=, <, <=, ==, !=, in, not_in` |
| `rules[0]` = `{wave_height_min, "in", "x"}` | `rules[0].op 'in' is not valid for metric 'wave_height_min'` |
| `rules[0]` valid, `rules[1]` value `"2"`, `rules[2]` op `"=>"` | `rules[1].value must be a number` |

### 7.3 `tests/test_units.py` (unit)

| TC | Covers | Action | Expected |
|---|---|---|---|
| TC-44 | convert_height | `(1.0,"m","ft")`; `(3.28084,"ft","m")`; `(1.0,"M","FT")`; `(2.4255,"FT","ft")` | `approx(3.28084, abs=1e-9)`; `approx(1.0, abs=1e-9)`; `approx(3.28084, abs=1e-9)`; `== 2.4255` |
| TC-45 | convert_speed | `(18.52,"kph","kts")`; `(11.50779,"mph","kts")`; `(10,"kts","kph")`; `(10,"kts","mph")`; `(18.52,"KPH","MPH")`; `(8.4,"KTS","kts")` | `approx(10.0, 1e-6)`; `approx(10.0, 1e-4)`; `approx(18.52, 1e-9)`; `approx(11.50779, 1e-9)`; `approx(11.50779, 1e-6)`; `== 8.4` |
| TC-46 | convert_temperature | `(68,"F","C")`; `(20,"C","F")`; `(-40,"f","c")`; `(24.0,"c","C")` | `approx(20.0)`; `approx(68.0)`; `approx(-40.0)` (all `abs=1e-9`); `== 24.0` |
| TC-47 | unsupported units | `convert_height(1.0,"yd","ft")`; `(1.0,"ft","YD")`; `convert_speed(1.0,"m/s","kts")`; `convert_temperature(1.0,"C","K")` | `ValueError`: `unsupported height unit: 'yd'`; `unsupported height unit: 'YD'`; `unsupported speed unit: 'm/s'`; `unsupported temperature unit: 'K'` |
| TC-206 | §4.5: units validated before the same-unit shortcut | `convert_height(1,"yd","yd")`; `convert_height(1,"YD","yd")`; `convert_speed(5,"x","X")`; `convert_temperature(1,"K","K")` | `ValueError("unsupported height unit: 'yd'")`; `... 'YD'`; `unsupported speed unit: 'x'`; `unsupported temperature unit: 'K'` |

### 7.4 `tests/test_surfline.py` (FR-4, FR-5, §6.2; unit)

`T = local_ts(7)`. Parse tests call `parse_forecast(raw, PIPELINE, settings)`, so error messages name `Pipeline`. In §4.5, "int" means `int` but not `bool`, and "numeric" means `int` or `float` but not `bool`.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-48 | AC-4.1 | A factory that records `(args, kwargs)` and returns `pipeline_raw()`; `SurflineClient(Settings(), forecast_factory=f).fetch(Spot("Pipeline","5842041f4e65fad6a7708890"))` | Calls `== [(({"spotId": "5842041f4e65fad6a7708890", "days": 1, "intervalHours": 1},), {})]` |
| TC-49 | AC-4.2 | The factory does `print("Error : 500")`, then returns `pipeline_raw()` | `capsys.readouterr().out == ""`; the result equals `pipeline_forecast()` with spot `PIPELINE` |
| TC-50 | AC-4.2, §4.12 logging | As TC-49, with `caplog.set_level(logging.DEBUG, logger="surf_alert")` and no change to the root logger | A record has `name == "surf_alert.surfline"`, `levelno == logging.DEBUG` and `"Error : 500"` in `getMessage()`; no record at WARNING or above contains it |
| TC-51 | AC-4.3 | The factory raises `requests.ConnectionError("boom")`, `ValueError("boom")`, `ImportError("boom")` or `RuntimeError("boom")` | `SurflineError("failed to fetch forecast for Pipeline: boom")` |
| TC-52 | fetch does not wrap parse errors | The factory returns `make_raw(wave=[])` | Message is exactly `no wave data returned for Pipeline` |
| TC-53 | AC-4.6 | (a) No factory, `pysurfline` blocked; `SurflineClient(Settings()).fetch(PIPELINE)`<br>(b) `sys.modules["pysurfline"]` is a `ModuleType` whose `SpotForecast` records `(args, kwargs)` and returns `pysurfline_raw(pipeline_raw())`; `session` answers from `forecast_bodies(pipeline_raw())` | (a) `SurflineError` whose message starts with `failed to fetch forecast for Pipeline: `<br>(b) One call `(({"spotId": PIPELINE.spot_id, "days": 1, "intervalHours": 1},), {"verbose": False})`; the session gets three GETs, `surf`, `swells` and `rating`, each to `KBYG_URL.format(kind)` with those params and `timeout 30.0`; the result equals `pipeline_forecast()` |
| TC-54 | FR-5 reference parse | `parse_forecast(pipeline_raw(), PIPELINE, Settings())`; the same for Sunset; `fetch` with a factory returning `pipeline_raw()` | `== pipeline_forecast()` / `== sunset_forecast()`. `slots` and `daylight` are tuples; every `utc_offset` is a `float`. |
| TC-55 | AC-4.4 | `wave` omitted; `wave=[]`; `wave=None`; `wave={"timestamp":1}`; `wind` omitted; `wind=[]`; both omitted | `no wave data returned for Pipeline` for the wave cases; `no wind data returned for Pipeline` for the wind cases; the wave message when both are omitted |
| TC-56 | AC-4.5 | Only `wave`, `wind` (from `pipeline_raw()`), `units_wave` and `units_wind` | Parses. Every `tide_height`, `temperature`, `weather_condition` and `surf_rating` is `None`; `wind_speed == 8.4`; `daylight == ()`. |
| TC-57 | AC-5.6 | Table C | `SurflineError` with the table C message |
| TC-58 | AC-5.10 | (a) `{"waveHeight":"FT"}`, swell 4.2, unit `"ft"`<br>(b) the same with unit `"m"`<br>(c) `{"waveHeight":"FT","swellHeight":"M"}`, `surf_min 2.0`, swell 1.0, unit `"ft"` | (a) `4.2`<br>(b) `approx(4.2/3.28084, abs=1e-6)`<br>(c) `surf_min == 2.0`, swell `approx(3.28084, abs=1e-6)` |
| TC-59 | units in any case | Lowercase units in all four dicts (`"ft"`, `"kts"`, `"c"`), plus a tide and a weather entry at T | Parses; `tide_height == 1.3`, `temperature == 24.0` |
| TC-60 | AC-5.1 | `{"waveHeight":"M","swellHeight":"M"}`, `surf_min 1.0`, `surf_max 2.0`, unit `"ft"`; then FT with `surf_min 3.28084` and unit `"m"` | `approx(3.28084, 1e-6)` and `approx(6.56168, 1e-6)`; then `approx(1.0, 1e-6)` |
| TC-61 | AC-5.2 | `KPH` speed 18.52 and gust 37.04 to `kts`; `MPH` 11.50779; `KTS` 10 to `kph` | `approx(10.0, 1e-6)` and `approx(20.0, 1e-6)`; `approx(10.0, abs=1e-4)`; `approx(18.52, 1e-6)` |
| TC-62 | AC-5.3 | `{"temperature":"F"}` 68.0 to `"C"`; `"C"` 20.0 to `"F"` | `approx(20.0, 1e-9)`; `approx(68.0, 1e-9)` |
| TC-63 | AC-5.4 | Table D (FT data, unit `"ft"`) | `primary_swell` per table D; `period` and `direction` are floats |
| TC-64 | AC-5.5 wind | Table E | `wind_speed` per table E; when it is `None`, gust, direction and type are also `None` |
| TC-65 | AC-5.5 tides and weather | tides `[T-3601, T+3601]`; tides `[(T-1800, 1.0), (T+1800, 2.0)]`; weather `[(T+3600, 20.0)]`; weather `[(T+3601, 20.0)]` | `tide_height None`; `1.0`; `temperature 20.0`; temperature and condition both `None` |
| TC-66 | tide filter, then match | tides `[{"timestamp":T,"height":None}, (T+1800, 1.0)]`; `[{"timestamp":T,"height":"1.3"}, (T+2000, 0.8)]`; `[{"timestamp":T,"height":True}]`; `{"tideHeight":"M"}` height 1.0 to `"ft"` | `1.0`; `0.8`; `None`; `approx(3.28084, 1e-6)` |
| TC-67 | wind field types | `{"timestamp":T,"speed":"fast","gust":12.3,"direction":45,"directionType":"Offshore"}`; no `gust`; `directionType: 5`; `speed: True` | `wind_speed None`, `wind_gust 12.3`, `wind_direction 45.0` (a float), `"Offshore"`; `wind_gust None`; type `None`; `wind_speed None` |
| TC-68 | weather field types | `{"timestamp":T,"temperature":24.0,"condition":7}`; `{"temperature":"hot","condition":"CLEAR"}`; no `temperature` | `24.0` and `None`; `None` and `"CLEAR"`; `None` |
| TC-69 | AC-5.7 | Tides present with `units_tides` `{"tideHeight":"XX"}`, omitted, or `"FT"` (not a dict); weather present with `units_weather` `{"temperature":"XX"}` or omitted | Every `tide_height is None`; every `temperature is None` and `weather_condition == "MOSTLY_CLEAR"` |
| TC-70 | AC-5.8 | Wave entries at `[T+7200, T, T+3600]` | Timestamps `== (T, T+3600, T+7200)` |
| TC-71 | AC-5.9 | `wave = [wave_entry(T), bad]` where `bad` is: missing `surf_max`; missing `surf_min`; missing `timestamp`; `timestamp` `True`, `1791392400.0` or `"1791392400"`; missing `utcOffset`; `utcOffset` `"-10"` or `True`; `surf_min` `"2"`; `surf_max` `None`; `"x"`; `None` | `SurflineError` whose message starts with `malformed forecast data for Pipeline: ` |
| TC-72 | rating | One `rating_entry(T)` whose `rating` is `{"key":"FAIR","value":3}`, has no `value`, has `value` `3.0`, `True` or `"3"`, is the str `"FAIR"`, or is missing | `surf_rating` is `3` (an int), then `None` for every other case |
| TC-73 | §4.5 step 7 | (a) `REF_SUNLIGHT`<br>(b) `[REF entry, no "dawn", "junk", midnight "x", no midnightUTCOffset, midnight True, midnightUTCOffset True, dawn 1791389400.0, REF entry +86400 on midnight, dawn and dusk]`<br>(c) `[{"midnight": 1791331200-19800, "midnightUTCOffset": 5.5, "dawn": 1, "dusk": 2}]`<br>(d) `None` | (a) `(DaylightWindow(date(2026,10,7), 1791389400, 1791434400),)`<br>(b) that window and `DaylightWindow(date(2026,10,8), 1791389400+86400, 1791434400+86400)`, in source order; every other entry skipped<br>(c) `(DaylightWindow(date(2026,10,7), 1, 2),)`<br>(d) `()` |
| TC-74 | utc_offset | `utcOffset -10`; `5.5` | `-10.0` (a float); `5.5` |
| TC-214 | AC-4.7, §4.5 | The TC-53(b) setup for Sunset Beach (`sunset_raw()`), with `SurflineClient(Settings(), session=s, timeout=5.0)` | The result equals `sunset_forecast()`; all three GETs use `timeout 5.0` |
| TC-215 | AC-4.7, §4.5 default factory steps 3–4 | Default path with hand-built bodies: (a) one surf entry at `T`, swells entries at `T-3600` and `T+1800`; (b) swells only at `T+3601`; (c) surf units `{"waveHeight":"M"}`, swells units `{}`, heights `1.0`; (d) surf units FT, swells units `{"swellHeight":"M"}`, swell height `1.0` | (a) the primary swell comes from the `T+1800` entry; (b) `primary_swell is None` and `surf_min == 2.0`; (c) `surf_min` and the swell height are both `approx(3.28084)`; (d) `surf_min == 2.0`, swell height `approx(3.28084)` |
| TC-216 | AC-4.8, §6.2 | Default path where: (a) `surf`, `swells` or `rating` answers `FakeResponse(404)`; (b) the `surf` GET raises `requests.ConnectionError("boom")`, `swells` answers non-JSON, the `surf` body is `{}` or has an empty list, or its units are `{}` or missing; (c) a surf entry has no `surf` dict, a non-dict `surf`, no `max`, or is not a dict | (a) `failed to fetch forecast for Pipeline: Surfline returned HTTP 404 for the <kind> forecast`; (b) `failed to fetch forecast for Pipeline: boom`, `...: not JSON`, `no wave data returned for Pipeline` (twice), `unsupported waveHeight unit for Pipeline: None` (twice); (c) a message starting `malformed forecast data for Pipeline: ` |
| TC-217 | AC-5.5 (ratings) | Ratings at `T-3600` (1) and `T+1800` (5); at `T+1800` (5) and `T-1800` (2); only at `T+3601`; no `rating`; `rating="junk"` | `5`; `2`; `None`; `None`; `None` |

Table C (TC-57):

| units_wave | units_wind | message |
|---|---|---|
| `{"waveHeight":"FATHOMS"}` | REF | `unsupported waveHeight unit for Pipeline: 'FATHOMS'` |
| `{}`; `{"swellHeight":"FT"}`; omitted; `["FT"]` | REF | `unsupported waveHeight unit for Pipeline: None` |
| `{"waveHeight": 3}` | REF | `unsupported waveHeight unit for Pipeline: 3` |
| `{"waveHeight":"FT","swellHeight":"YD"}` | REF | `unsupported swellHeight unit for Pipeline: 'YD'` |
| REF | `{"windSpeed":"M/S"}` | `unsupported windSpeed unit for Pipeline: 'M/S'` |
| REF | `{}`; `"KTS"` (not a dict) | `unsupported windSpeed unit for Pipeline: None` |

Table D (TC-63). `{h,p,d}` stands for `{"height":h,"period":p,"direction":d}`:

| swells | primary_swell |
|---|---|
| `[{1.1,7,20},{4.2,11,315},{0,0,0}]` | `Swell(4.2, 11.0, 315.0)` |
| `[{0,0,0},{0,9,10}]`; `[]`; key missing; `None` | `None` |
| `[{3.0,9,10},{3.0,12,200}]` | `Swell(3.0, 9.0, 10.0)` |
| `["x", {2.0,8,90}]`; `[{"height":5.0,"direction":10}, {2.0,8,90}]` | `Swell(2.0, 8.0, 90.0)` |
| `[{"height":True,"period":9,"direction":10}, {0.5,6,100}]` | `Swell(0.5, 6.0, 100.0)` |
| `[{"height":5.0,"period":"9","direction":10}]`; `[{-1.0,9,10}]` | `None` |
| `[{1.0,10,270}]` with `{"waveHeight":"M","swellHeight":"M"}` | height `approx(3.28084, 1e-6)`, `10.0`, `270.0` |

Table E (TC-64). Entries are `(timestamp, speed)` in list order:

| wind list | wind_speed |
|---|---|
| `[(T-3600, 5.0), (T+1800, 9.0)]` | `9.0` |
| `[(T-3601, 5.0), (T+3700, 9.0)]` | `None` |
| `[(T-1800, 5.0), (T+1800, 9.0)]`; `[(T+1800, 9.0), (T-1800, 5.0)]` | `5.0` (earlier timestamp wins, regardless of list order) |
| `[(T+3600, 9.0)]` | `9.0` (tolerance is inclusive) |
| `[(T, 5.0)]` | `5.0` |
| `["junk", {"speed":7.0}, {"timestamp":"x","speed":6.0}, (T+600, 9.0)]` | `9.0` |
| `["junk"]` | `None` (the list is non-empty, so no "no wind data" error) |
| wave `[wave_entry(ts=3600, utc_offset=0)]`, wind `[{"timestamp": True, "speed": 6.0, "gust": 7.0, "direction": 1.0, "directionType": "Offshore"}]` | `None` (a `bool` timestamp is ignored; read as int 1 it would match) |

### 7.5 `tests/test_evaluate.py` (FR-6, FR-7, FR-3; unit)

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-75 | local_date_of, §6.5 | `(1791392400,-10.0)`, `(1791331200,-10.0)`, `(1791367200,-10)`, `(1791367199,-10)`, `(1791331200-19800,5.5)`, `(1791331200-19801,5.5)` | 10-07, 10-06, 10-07, 10-06, 10-07, 10-06 (all 2026) |
| TC-76 | AC-6.1 | Slots at 1791388800 (16Z), 1791392400 (17Z) and 1791396000 (18Z); `now` = 17:30Z, 15:00Z, 20:00Z, 18:00Z | The 17Z slot, the first, the last, and the 18Z slot |
| TC-77 | AC-6.1 | `now = datetime(2026,10,7,19,30,tzinfo=timezone(timedelta(hours=2)))` | The 17Z slot |
| TC-78 | AC-6.2, §4.6 check order | `current_slot([], NOW)`; `current_slot(slots, naive)`; `now` with a custom `tzinfo` whose `utcoffset()` returns `None`; `current_slot([], naive)` | `ValueError("no slots")`; `ValueError("now must be timezone-aware")` for the other three (the naive check runs first) |
| TC-79 | AC-6.3 | `evaluation_window(pipeline_forecast(), NOW)` | `(date(2026,10,7), w)`, where `w` is a tuple of slots at `local_ts(7..18)` |
| TC-80 | AC-6.4 | `pipeline_forecast(daylight=())` | 13 slots, h 7..19 |
| TC-81 | daylight choice | (a) Daylight only for 10-08<br>(b) `(REF window, DaylightWindow(10-07, local_ts(9), local_ts(10)))` | (a) Fallback: h 7..19<br>(b) The first window is used: h 7..18 |
| TC-82 | inclusive bounds | `(DaylightWindow(10-07, local_ts(7), local_ts(10)),)`, `now = utc_dt(local_ts(6)+1800)` | h 7, 8, 9, 10 |
| TC-83 | fallback bounds | `daylight=()`, `now = utc_dt(local_ts(5)+1800)` | 14 slots, h 6..19 |
| TC-84 | AC-6.5 | `now = utc_dt(local_ts(19))` | `(date(2026,10,7), ())` |
| TC-85 | AC-6.6 | (a) 48 slots for h 0..47 with daylight for 10-07 and 10-08, `NOW`<br>(b) the same with `daylight=()`<br>(c) `pipeline_forecast()` with `now = utc_dt(1791363600)` | (a) h 7..18 only<br>(b) h 7..19 only<br>(c) `current_slot` returns the h 0 slot; the window is `(date(2026,10,6), ())` |
| TC-86 | §4.4 mapping, AC-7.6 | `metric_value(make_slot(7), m)` for all 11 metrics; `make_slot(primary_swell=None)`; `"nope"` | `2.4255, 3.3482, 4, 4.2, 11.0, 8.4, 12.3, "Offshore", 1.3, 24.0, "MOSTLY_CLEAR"`; `swell_height` and `swell_period` are `None`; `ValueError("unknown metric: nope")` |
| TC-87 | AC-7.1 | `make_slot(primary_swell=Swell(4.2,7.0,315.0))`, `swell_period` with: `(">",7)`, `(">=",7)`, `("<",7)`, `("<=",7)`, `("==",7)`, `("!=",7)`, `(">",6.5)`, `("<",7.5)`, `("==",7.5)`, `("!=",7.5)` (all values floats); `surf_rating >= 4.0`; `wave_height_min > 2.0` | `F,T,F,T,T,F,T,T,F,T`; `T`; `T` |
| TC-88 | AC-7.2 | `"Offshore"`: `("==","offshore")`, `("==","OFFSHORE")`, `("==","Onshore")`, `("!=","offshore")`, `("!=","onshore")`, `("not_in",("onshore",))`, `("not_in",("OFFSHORE","onshore"))`, `("in",("offshore","cross-shore"))`<br>`"Cross-shore"`: `("in",("offshore","cross-shore"))`, `("in",("Onshore",))`<br>`weather_condition` `"straße"`: `("==","STRASSE")`<br>`"MOSTLY_CLEAR"`: `("==","mostly_clear")` | `T,T,F,F,T,T,F,T`; `T,F`; `T` (casefold); `T` |
| TC-89 | AC-7.3 | All optional slot fields `None`. Every numeric metric except `wave_height_min`/`wave_height_max`, with every `NUMERIC_OPS` op against `0.0`; text metrics with `==`/`!=` `"x"` and `in`/`not_in` `("x",)` | Always `False` |
| TC-90 | AC-7.4 | `slot_passes(REF_RULES, pipeline_slot(7,True))`; `pipeline_slot(12,False)`; `[]`; one passing rule plus one failing rule | `T`, `F`, `T`, `F` |
| TC-91 | AC-7.5 | `evaluate_spot(pipeline_forecast(), REF_RULES, Settings(), NOW)`; Sunset | `== pipeline_evaluation()`; `== sunset_evaluation()` |
| TC-92 | AC-7.5 | Pipeline with `min_good_hours` 5, then 6 | `True`, then `False` (5 good slots both times) |
| TC-93 | AC-6.5, AC-6.2 | `evaluate_spot(..., utc_dt(local_ts(19)))`; a naive `now` | `SpotEvaluation(PIPELINE, date(2026,10,7), pipeline_slot(19,False), (), (), False)`; `ValueError("now must be timezone-aware")` |
| TC-94 | AC-3.2 | The six AC-3.2 rules with `Settings()` | The six AC-3.2 strings exactly |
| TC-95 | AC-3.3, suffixes | `swell_height > 1.0` (m); `tide_height <= 0.5` (m); `wind_gust < 30.0` (kph); `wind_speed <= 12.5` (mph); `temperature >= 64.0` (F); `swell_period > 7.0`; `wind_direction_type != "Onshore"`; `weather_condition not_in ("RAIN","THUNDERSTORM")` | `swell_height > 1 m`; `tide_height <= 0.5 m`; `wind_gust < 30 kph`; `wind_speed <= 12.5 mph`; `temperature >= 64°F`; `swell_period > 7 s`; `wind_direction_type != Onshore`; `weather_condition not_in [RAIN, THUNDERSTORM]` |

### 7.6 `tests/test_jev.py` (FR-9, §6.3; unit)

Setup: `fake = FakeSession(response=...)`, `client = JevClient("k", model="jev-latest", session=fake)`, `Q = {"good_spot_1": {"type":"noul","instructions":"x"}}`.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-96 | AC-9.1, Timeouts | 200 `REF_JEV_RESPONSE`; `ask(REF_STATE, REF_QUESTIONS)` | One call: url `https://api.typesafe.ai/v1/systemone`; headers `{"Authorization": "Bearer k", "Content-Type": "application/json"}`; json `{"model":"jev-latest","state":REF_STATE,"questions":REF_QUESTIONS}`; timeout `30.0` |
| TC-97 | options | `JevClient("k2", model="jev-x", session=fake, timeout=5.0).ask({"a":1}, Q)` | `"Bearer k2"`, model `"jev-x"`, timeout `5.0` |
| TC-98 | AC-9.2 | 200 `REF_JEV_RESPONSE`; 201 with the same body; 200 `{"answers": {}}` | `REF_ANSWERS`; `REF_ANSWERS`; `{}` |
| TC-99 | AC-9.3, no retries | Table F | `JevError` with the table F message; `status_code` equals the HTTP status; `post` called once |
| TC-100 | `{body}` truncation | 422 with `"a"*250`; 500 with `"b"*201` | `"invalid request (HTTP 422): " + "a"*200`; `"unexpected response (HTTP 500): " + "b"*200` |
| TC-101 | AC-9.4 | Raises `requests.Timeout("t")`, `requests.ConnectionError("conn refused")`, `requests.RequestException("x")` | `request failed: t`, `request failed: conn refused`, `request failed: x`; `status_code is None` |
| TC-102 | AC-9.5 | Table G | Message and `status_code` per table G |
| TC-103 | AC-9.6 | `JevClient("")`, `JevClient("   ")`; `ask({"a":1}, {})` | `ValueError("api_key must not be empty")` for both; `ValueError("questions must not be empty")` with `fake.calls == []` |
| TC-104 | §6.5 | `post` raises `RuntimeError("bug")` | The `RuntimeError` propagates |
| TC-207 | §6.3 redaction, AC-17.4 | Key `"SECRETKEY"` unless stated:<br>(a) 422 `"bad token SECRETKEY here"`<br>(b) 500 `"echo SECRETKEY SECRETKEY"`<br>(c) 422 `"a"*195 + "SECRETKEY" + "b"*10` (straddles character 200)<br>(d) raises `requests.ConnectionError("Authorization: Bearer SECRETKEY rejected")`<br>(e) key `"a b+c"`, 422 `"k=a+b%2Bc raw=a b+c"`<br>(f) key `"a b+c"`, raises `requests.Timeout("u?key=a+b%2Bc")` | (a) `invalid request (HTTP 422): bad token *** here`<br>(b) `unexpected response (HTTP 500): echo *** ***`<br>(c) `"invalid request (HTTP 422): " + "a"*195 + "***bb"` (redaction happens before truncation)<br>(d) `request failed: Authorization: Bearer *** rejected`<br>(e) `invalid request (HTTP 422): k=*** raw=***`<br>(f) `request failed: u?key=***`<br>No message contains the key or `quote_plus(key)` |

Table F (TC-99):

| status | text | message |
|---|---|---|
| 401 | `unauthorized` | `authentication failed (HTTP 401); check TYPESAFE_API_KEY` |
| 422 | `{"detail":"bad"}` | `invalid request (HTTP 422): {"detail":"bad"}` |
| 429 | `slow down` | `rate limited (HTTP 429)` |
| 529 | `busy` | `service overloaded (HTTP 529)` |
| 500 | `oops` | `unexpected response (HTTP 500): oops` |
| 503 | `` (empty) | `unexpected response (HTTP 503): ` (with a trailing space) |
| 302 | `moved` | `unexpected response (HTTP 302): moved` |
| 404 | `nf` | `unexpected response (HTTP 404): nf` |

Table G (TC-102):

| status | body | message | code |
|---|---|---|---|
| 200 | `.json()` raises `ValueError("x")` | `malformed response: body is not valid JSON` | 200 |
| 200 | `.json()` raises `requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)` | `malformed response: body is not valid JSON` | 200 |
| 201 | `.json()` raises `ValueError` | `malformed response: body is not valid JSON` | 201 |
| 200 | `[]`; `"answers"`; `{"model":"jev-1.13.0"}`; `{"answers": []}`; `{"answers": None}` | `malformed response: missing 'answers' object` | 200 |

### 7.7 `tests/test_decision.py` (FR-8, FR-10, FR-11; unit)

`A = pipeline_evaluation()`, `B = sunset_evaluation()`, `C = rocky_evaluation()`; the threshold is 0.7. `ans(*p, best=...)` builds `{"good_spot_i": {"type":"noul","noul":p_i}}` and, when `best` is given, `"best_spot": {"type":"choice","choice":best}`.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-105 | §4.8 | `candidate_ref(1)`, `candidate_ref(12)` | `"spot_1"`, `"spot_12"` |
| TC-106 | AC-8.1, §6.5 | `build_jev_request([B], REF_RULES, REF_SETTINGS)` | `questions == {"good_spot_1": {"type":"noul","instructions": NOUL_TEMPLATE.format(name="Sunset Beach", ref="spot_1")}}`; `state["spots"]` has one entry with ref `spot_1` |
| TC-107 | AC-8.2 | `[A, B]`; `[A, B, C]` | `== REF_QUESTIONS` with key order `good_spot_1, good_spot_2, best_spot`; for three spots, `criteria == {"spot_1":"Pipeline","spot_2":"Sunset Beach","spot_3":"Rocky Point"}` |
| TC-108 | AC-8.3 | `[A, B]` | `state == REF_STATE`; key order matches at every level; `json.dumps(state)` succeeds |
| TC-109 | AC-8.3 rounding, None and units | Variant 1: `E = replace(A, current=s, window=(s,), good_slots=(s,))`, where `s = make_slot(7, surf_min=1.23456, surf_max=2.98765, <every optional field None>)`; `Settings(height_unit="m", wind_unit="kph", temperature_unit="F")`; rules `(Rule("wave_height_min",">",1.0),)`<br>Variant 2: `make_slot(7, primary_swell=Swell(4.24,11,315.04), wind_speed=8.44, wind_gust=12.36, wind_direction=45.04, tide_height=1.26, temperature=23.96)` | Variant 1: units `{"height":"m","period":"s","wind":"kph","temperature":"F"}`; rules `["wave_height_min > 1 m"]`; notes `""`; `good_hours ["07:00"]`; SLOT has `time "07:00"`, `surf_min 1.2`, `surf_max 3.0`, every optional value `None`, `passes_rules True`<br>Variant 2: `4.2, 11.0, 315.0, 8.4, 12.4, 45.0, 1.3, 24.0` |
| TC-110 | AC-8.4 | `[]` | `ValueError("no candidate spots")` |
| TC-111 | AC-10.1 | `interpret_jev_answers(REF_ANSWERS, [A, B], settings)` | `== REF_DECISION` |
| TC-112 | AC-10.2 | `ans(0.7, 0.69)`; the same with `jev_threshold=0.5` | `Decision((Recommendation(A,0.7),), True, None)`; both recommendations |
| TC-113 | AC-10.3 | `[A,B,C]` with `ans(0.75,0.95,0.85,best="spot_1")`; `ans(0.8,0.9,0.9,"spot_1")`; `ans(0.9,0.9,0.8,"spot_3")`; `ans(0.95,0.75,0.85,"spot_2")` | Orders `A,B,C`; `A,B,C`; `C,A,B`; `B,A,C`, each with its own probability |
| TC-114 | AC-10.4 | `[A,B,C]`, `p=(0.8,0.9,0.75)`, with `best_spot`: missing; `"spot_1"` (a str); `{"type":"choice"}`; `{"choice":"spot_9"}`; `{"choice":"Pipeline"}`; `{"choice":None}`. Then `p=(0.8,0.9,0.6)` with `{"choice":"spot_3"}`. Then `p=(0.9,0.9,0.9)` with no `best_spot` | `B,A,C` for all six; `B,A`; `A,B,C`. Always `jev_used=True` and `jev_error=None` |
| TC-115 | §6.5 one candidate | `[B]` with noul 0.81; then 0.5 | `Decision((Recommendation(B,0.81),), True, None)`; `Decision((), True, None)` |
| TC-116 | AC-10.5 | `[A,B]`, `ans(0.5, 0.3, best="spot_1")` | `Decision((), True, None)` |
| TC-117 | AC-10.6 | `[A,B]` with: `good_spot_2` missing; `{}`; `good_spot_1 = "yes"`; `{"type":"noul"}`; noul `True`, `False`, `"0.9"`, `None`, `1.5`, `-0.01`, `float("nan")`; `good_spot_1` valid with `good_spot_2` noul `2`; both invalid | `JevError("malformed response: missing or invalid answer for good_spot_2")` for the first and the "good_spot_2 noul 2" cases; `...good_spot_1` for all others; `status_code is None` |
| TC-118 | AC-10.6 ints | `ans(1, 0)`; `ans(1.0, 0.0)` | `Decision((Recommendation(A, 1.0),), True, None)` with probability `isinstance float`; floats give the same result |
| TC-119 | FR-11 | `decide([A,B], REF_RULES, REF_SETTINGS, FakeJev(answers=REF_ANSWERS))` | `== REF_DECISION`; `jev.calls == [(REF_STATE, REF_QUESTIONS)]` |
| TC-120 | AC-11.1 | `FakeJev(exc=JevError("rate limited (HTTP 429)", 429))`; `decide([C,A,B], REF_RULES, Settings(), jev)` | `Decision((Rec(A,None), Rec(B,None), Rec(C,None)), False, "rate limited (HTTP 429)")` |
| TC-121 | AC-11.1 via interpret | Answers `{"good_spot_1":{"noul":"high"},"good_spot_2":{"noul":0.9}}`, `[A,B]` | `Decision((Rec(A,None), Rec(B,None)), False, "malformed response: missing or invalid answer for good_spot_1")` |
| TC-122 | AC-11.2 | `on_jev_error="skip"` with the TC-120 jev; with the TC-121 jev | `Decision((), False, "rate limited (HTTP 429)")`; `Decision((), False, "malformed response: missing or invalid answer for good_spot_1")` |
| TC-123 | AC-11.3, §6.5 | jev raises `RuntimeError("boom")`; jev raises `ValueError("bad")`; `decide([], ...)` | Propagates; propagates; `ValueError("no candidate spots")` with `jev.calls == []` |

### 7.8 `tests/test_message.py` (FR-12; unit)

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-124 | AC-12.6 | `compass(d)` for 0, 11.24, 11.25, 22.5, 45, 90, 180, 270, 310, 315, 348.74, 348.75, 359, 360, -45, 720 | `N,N,NNE,NNE,NE,E,S,W,NW,NW,NNW,N,N,N,NW,N` |
| TC-125 | AC-12.5 | `format_hours` for hours (8,9,10,15,16); (); (7,); (7..11); (23,); (7,9); and one slot `timestamp=1791331200+7200, utc_offset=5.5` | `08:00-11:00, 15:00-17:00`; `none`; `07:00-08:00`; `07:00-12:00`; `23:00-00:00`; `07:00-08:00, 09:00-10:00`; `07:30-08:30` |
| TC-126 | §4.9 block | `format_spot_block(A, Settings())`; `(A, Settings(), 0.92)`; `(B, Settings(), 0.81)` | `PIPELINE_BLOCK`; `PIPELINE_BLOCK + "\nJev confidence: 92%"`; `SUNSET_BLOCK + "\nJev confidence: 81%"` |
| TC-127 | unit labels | `Settings("m","kph","F")` with current `make_slot(7, surf_min=0.74, surf_max=1.02, primary_swell=Swell(1.28,11.0,315.0), wind_speed=15.6, wind_gust=22.8, wind_direction_type="Onshore", tide_height=0.4, temperature=75.2, weather_condition="CLEAR")`; then `Settings(wind_unit="mph")` with wind 9.2 / gust 13.9 | `"Pipeline - now (07:00 local)\nSurf: 0.7-1.0 m\nSwell: 1.3 m @ 11s from NW (315°)\nWind: 16 kph Onshore (gusts 23 kph)\nTide: 0.4 m\nWeather: 75°F, Clear\nGood hours today: 07:00-12:00"`; `Wind: 9 mph Offshore (gusts 14 mph)` |
| TC-128 | AC-12.4 | Table H | Lines per table H |
| TC-129 | line 8 | Probability `0.0`; `1.0` | `Jev confidence: 0%`; `Jev confidence: 100%` |
| TC-130 | AC-15.5 | `(A, Settings())`; `(replace(B, good_slots=(), passes_rules=False), Settings())`; `(replace(A, passes_rules=False), Settings(min_good_hours=6))` | `Rules: PASS (good hours today: 5, required: 1)`; `Rules: FAIL (good hours today: 0, required: 1)`; `Rules: FAIL (good hours today: 5, required: 6)` |
| TC-131 | AC-12.1 | `format_message(REF_DECISION, Settings())` and with `REF_SETTINGS`; also the `REF_MESSAGE` self-check | `== REF_MESSAGE` |
| TC-132 | AC-12.3 | `Decision((Rec(A,None), Rec(B,None)), False, "x")` | `== REF_MESSAGE_RULES_ONLY`; no `Jev confidence:` line |
| TC-133 | AC-12.2 | Five recommendations, each `replace(A, spot=Spot(n, id))` for Alpha, Bravo, Charlie, Delta, Echo, with p 0.9, 0.85, 0.8, 0.75, 0.72, `jev_used=True`; then all p `None` with `jev_used=False` | `split("\n\n")` gives 5 parts: the header `...Go surf at Alpha!`; the Alpha block (`PIPELINE_BLOCK` with the name replaced) + `\nJev confidence: 90%`; `Also good: ` + the Bravo block + `85%`; `Also good: ` + the Charlie block + `80%`; `More good spots: Delta, Echo`. The second run gives 6 parts with no confidence lines, ending with `More good spots: Delta, Echo` and then the note paragraph |
| TC-134 | paragraphs | 3 recommendations with max 3; 1 recommendation; `REF_DECISION` with `max_spots_in_message=1` | No `More good spots` paragraph; 2 paragraphs; `"Surf alert 2026-10-07\nGo surf at Pipeline!\n\n" + PIPELINE_BLOCK + "\nJev confidence: 92%\n\nMore good spots: Sunset Beach"` |
| TC-135 | AC-12.7 | `Decision((), True)` | `ValueError("no recommendations")` |
| TC-136 | Output NFR | The TC-131 and TC-132 messages | The non-ASCII characters are exactly `{"°"}`; `msg == msg.rstrip()` |

Table H (TC-128). Changes are applied to `A.current`. Lines L1..L7 are the `PIPELINE_BLOCK` lines:

| change | expected |
|---|---|
| `tide_height=None` | L1, L2, L3, L4, L6, L7 |
| `primary_swell=None` | L1, L2, L4, L5, L6, L7 |
| `temperature=None, weather_condition=None` | L1, L2, L3, L4, L5, L7 |
| `wind_speed=None` | L1, L2, L3, L5, L6, L7 |
| `temperature=None` | L6 is `Weather: Mostly clear` |
| `weather_condition=None` | L6 is `Weather: 24°C` |
| `weather_condition="NIGHT_CLEAR"` | L6 is `Weather: 24°C, Night clear` |
| `wind_direction_type=None` | L4 is `Wind: 8 kts (gusts 12 kts)` |
| `wind_gust=None` | L4 is `Wind: 8 kts Offshore` |
| `wind_direction_type=None, wind_gust=None` | L4 is `Wind: 8 kts` |
| every optional field `None` | L1, L2, L7 |
| evaluation `good_slots=()` | L7 is `Good hours today: none` |

### 7.9 `tests/test_notify.py` (FR-13, §6.4; unit)

Setup: `fake = FakeSession(response=FakeResponse(200, text="ok"))`, `n = CallMeBotNotifier("+34612345678", "SECRET", session=fake)`.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-137 | AC-13.1, Timeouts | `n.send("hi")` | One call: url `https://api.callmebot.com/whatsapp.php`; params `{"phone":"+34612345678","text":"hi","apikey":"SECRET"}`; timeout `30.0` |
| TC-138 | AC-13.2 | Status 200 | Returns `None` |
| TC-139 | AC-13.3, no retries | `(203,"APIKey is invalid")`, `(500,"x")`, `(201,"ok")` | `CallMeBot returned HTTP 203: APIKey is invalid`, `... 500: x`, `... 201: ok`; `get` called once; the error is a `SurfAlertError` |
| TC-140 | §6.4, AC-17.4 | `(500,"e"*300)`; `(403,"bad key SECRET, SECRET")` | `"CallMeBot returned HTTP 500: " + "e"*200`; `CallMeBot returned HTTP 403: bad key ***, ***` |
| TC-141 | AC-13.4 | Raises `requests.ConnectionError("failed url=/whatsapp.php?apikey=SECRET")`; `requests.Timeout("SECRET timed out SECRET")` | `request to CallMeBot failed: failed url=/whatsapp.php?apikey=***`; `request to CallMeBot failed: *** timed out ***` |
| TC-142 | AC-13.5 | `send("")`, `send("   ")`, `send("\n\t")` | `ValueError("message text must not be empty")`; `fake.calls == []` |
| TC-143 | AC-13.6 | Valid: `"+34612345678"`, `"+1234567"`, `"+123456789012345"`. Invalid: `"0612345678"`, `"34612345678"`, `"+0612345678"`, `"+123456"`, `"+1234567890123456"`, `"+34 612 345 678"`, `"+3461234567a"`, `""`. Keys `""` and `"   "`. Bad phone with key `""` | Valid ones construct. Invalid phone: `ValueError("WHATSAPP_PHONE must be in international format, e.g. +34612345678")`. Bad key: `ValueError("CALLMEBOT_API_KEY must not be empty")`. Both bad: the phone message |
| TC-144 | §6.5, timeout | `send("Pipeline & Sunset: 100% #1?")`; `timeout=5.0` | `params["text"]` unchanged; `timeout == 5.0` |
| TC-208 | §6.4 redaction order and `quote_plus` | (a) key `"SECRET"`, `(500, "e"*195 + "SECRET" + "f"*10)`<br>(b) key `"a b+c"`, `(203, "apikey=a+b%2Bc is invalid (a b+c)")`<br>(c) key `"a b+c"`, raises `requests.ConnectionError("failed url=/whatsapp.php?phone=%2B34612345678&apikey=a+b%2Bc")` | (a) `"CallMeBot returned HTTP 500: " + "e"*195 + "***ff"`<br>(b) `CallMeBot returned HTTP 203: apikey=*** is invalid (***)`<br>(c) `request to CallMeBot failed: failed url=/whatsapp.php?phone=%2B34612345678&apikey=***` |
| TC-209 | §4.10 `re.fullmatch` | `CallMeBotNotifier("+34612345678\n", "k")`; `("+34612345678 ", "k")` | `ValueError("WHATSAPP_PHONE must be in international format, e.g. +34612345678")` for both |

### 7.10 `tests/test_app.py` (§4.11, FR-14; integration)

Setup: `cfg = ref_app_config()`. `src` is a `FakeForecastSource` mapping Pipeline to `pipeline_forecast()` and Sunset Beach to `sunset_forecast()` unless changed. Calls are `run_check(cfg, surfline=src, jev=jev, notifier=notifier, now=NOW)`.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-145 | evaluate_spots | `evaluate_spots(cfg, src, NOW)` | `([A, B], {})` as a `list` and a `dict`; `src.fetched == [PIPELINE, SUNSET]` |
| TC-146 | `spots` parameter | `spots=[SUNSET]` | `([B], {})`; `fetched == [SUNSET]` |
| TC-147 | §6.2 | Pipeline raises `SurflineError("no wave data returned for Pipeline")`; then both raise | `([B], {"Pipeline": "no wave data returned for Pipeline"})`, and Sunset is still fetched; then `([], {...})` with keys in order `Pipeline`, `Sunset Beach` |
| TC-148 | §6.5 | Pipeline raises `RuntimeError("bug")` | Propagates |
| TC-149 | AC-14.1 | Both spots `good_hours=()` | `jev.calls == []`, `notifier.sent == []`, `decision None`, `message None`, `sent False`, `send_error None`, `exit_code 0`, 2 evaluations |
| TC-150 | AC-14.2 | Sunset `good_hours=()`; noul 0.9 | `notifier.sent == [result.message]`; `message == format_message(decision, cfg.settings) == PIPELINE_ONLY_MESSAGE_90`; `decision == Decision((Rec(A,0.9),), True, None)`; `sent True`; `exit_code 0`; questions have only `good_spot_1` |
| TC-151 | reference run, Volume | `FakeJev(REF_ANSWERS)` | `result == RunResult((A,B), {}, REF_DECISION, REF_MESSAGE, True, None, 0)`; `jev.calls == [(REF_STATE, REF_QUESTIONS)]`; `notifier.sent == [REF_MESSAGE]` |
| TC-152 | AC-14.3 | noul 0.5 and 0.3 | `decision == Decision((), True, None)`, `message None`, `sent False`, `exit_code 0` |
| TC-153 | AC-14.4 | Pipeline no-wave error; Sunset noul 0.81 | `sent == [SUNSET_ONLY_MESSAGE_81]`; `failures == {"Pipeline": "no wave data returned for Pipeline"}`; `evaluations == (B,)`; `exit_code 3` |
| TC-154 | AC-14.5 | Both fail | No Jev call and no send; `evaluations == ()`; `decision None`; `message None`; `exit_code 3` |
| TC-155 | AC-14.6 | `FakeJev(exc=JevError("service overloaded (HTTP 529)", 529))` | `message == REF_MESSAGE_RULES_ONLY == sent[0]`; `decision == Decision((Rec(A,None),Rec(B,None)), False, "service overloaded (HTTP 529)")`; `exit_code 3` |
| TC-156 | AC-11.2 in a run | `ref_app_config(on_jev_error="skip")` with the TC-155 jev | `Decision((), False, "service overloaded (HTTP 529)")`; `message None`; nothing sent; `exit_code 3` |
| TC-157 | AC-14.7 | Notifier raises `NotificationError("CallMeBot returned HTTP 500: x")`; then also a Pipeline fetch failure; then also a Jev 529 | `sent False`, `send_error "CallMeBot returned HTTP 500: x"`, `message == REF_MESSAGE`, exit 4 in every case (4 beats 3) |
| TC-158 | dry run | `notifier=None`; also with the TC-155 jev | `message REF_MESSAGE`, `sent False`, `send_error None`, `exit_code 0`; `exit_code 3` |
| TC-159 | AC-14.11, §6.5 | `now = utc_dt(local_ts(11)+1800)`; then a naive `now` | Every evaluation has `current.timestamp == local_ts(11)` and `local_date == date(2026,10,7)`; `ValueError("now must be timezone-aware")` |

### 7.11 `tests/test_cli.py` (FR-2, 3, 14–17, §4.12; integration via `cli.main`)

"No factory called" means `cli_fakes.surfline_calls == jev_calls == notifier_calls == []`. stderr is `""` on success, and stdout is `""` on error, unless a case says otherwise.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-160 | AC-2.1 | `run_cli("spots")` | stdout `"1. Pipeline (5842041f4e65fad6a7708890)\n2. Sunset Beach (0123456789abcdef01234567)\n"`; rc 0; no factory called |
| TC-161 | AC-2.2, §4.12 rule 5 | For each of `spots`, `rules`, `conditions`, `check` (with `secrets_env`) and `check --dry-run`, a config that is (a) missing, (b) `spots = [`, (c) has `height_unit = "yd"` | (a) `f"error: config file not found: {path}\n"`<br>(b) starts with `f"error: invalid TOML in {path}: "` and ends with `"\n"`<br>(c) `"error: settings.height_unit must be one of: ft, m\n"`<br>rc 1 and no factory called in all cases |
| TC-162 | AC-3.1 | `run_cli("rules")`; a config with rules `wind_direction_type in [...]` and `wind_speed <= 15` | `"1. wave_height_min > 2 ft\n2. swell_period > 7 s\n"`, rc 0, no factory called; `"1. wind_direction_type in [Offshore, Cross-shore]\n2. wind_speed <= 15 kts\n"` |
| TC-163 | default paths | `chdir(tmp_path)`; write `surf_alert.toml` and `.env` with `TYPESAFE_API_KEY=fromdefault`; `main(["spots"])`; `main(["check","--dry-run"])` | The TC-160 stdout; `jev_calls[0][0] == "fromdefault"`, rc 0 |
| TC-164 | exit code 2 | `main([])`; `main(["--config",p,"fly"])`; `main(["--config",p,"spots","--bogus"])`; `main(["--config",p,"check","--dry-run","extra"])` | `SystemExit` with `.code == 2`; stderr contains `usage:`; no factory called |
| TC-165 | `main(None)`, `-v` | `sys.argv = ["surf_alert","--config",p,"--env-file",m,"spots"]`, then `main()`; `run_cli("-v","spots")` | The TC-160 stdout and rc 0 both times |
| TC-166 | AC-15.1 | No secrets set; `run_cli("conditions")` | stdout `REF_CONDITIONS_OUTPUT`; rc 0; `surfline_calls == [REF_SETTINGS]`; `fetched == [PIPELINE, SUNSET]`; `jev_calls == notifier_calls == []` |
| TC-167 | AC-15.2 | `--spot "sunset beach"`; `"SUNSET BEACH"` | `fetched == [SUNSET]`; stdout `SUNSET_BLOCK + "\nRules: PASS (good hours today: 5, required: 1)\n"`; rc 0 |
| TC-168 | AC-15.3 | `--spot Nowhere` | stderr `"error: unknown spot: Nowhere\n"`; rc 1; `surfline_calls == []` |
| TC-169 | AC-15.4 | Pipeline raises `SurflineError("failed to fetch forecast for Pipeline: boom")`; then both fail | stderr `"warning: Pipeline: failed to fetch forecast for Pipeline: boom\n"`, stdout is the Sunset section + `"\n"`, rc 3; when both fail, stdout `""`, two warnings in order, rc 3 |
| TC-170 | AC-14.1, AC-14.3 | `secrets_env`; (a) no candidates; (b) noul 0.5 and 0.3 | stdout `"No good spots today; no notification sent.\n"`; rc 0; nothing sent; in (a), `jev.calls == []` |
| TC-171 | AC-14.2 | Sunset `good_hours=()`; noul 0.9 | stdout `"Notification sent: 1 spot(s) recommended.\n"`; rc 0; `sent == [PIPELINE_ONLY_MESSAGE_90]` |
| TC-172 | reference CLI run | `secrets_env`; `run_cli("check")` | stdout `"Notification sent: 2 spot(s) recommended.\n"`; rc 0; `sent == [REF_MESSAGE]`; `jev.calls == [(REF_STATE, REF_QUESTIONS)]`; `surfline_calls == [REF_SETTINGS]`; `jev_calls == [("ts-key", REF_SETTINGS)]`; `notifier_calls == [("+34612345678", "cmb-key")]` |
| TC-173 | AC-14.4 | Pipeline no-wave error; noul 0.81 | stderr `"warning: Pipeline: no wave data returned for Pipeline\n"`; stdout `"Notification sent: 1 spot(s) recommended.\n"`; rc 3 |
| TC-174 | AC-14.5 | Both fail with `failed to fetch forecast for <name>: boom` | stdout `"No good spots today; no notification sent.\n"`; two warnings; rc 3; no Jev call; no send |
| TC-175 | AC-14.6, step 5 order | (a) Jev raises `JevError("service overloaded (HTTP 529)")`<br>(b) Pipeline no-wave error plus Jev `JevError("rate limited (HTTP 429)")`<br>(c) (a) with `on_jev_error = "skip"` | (a) stderr `"warning: Jev request failed: service overloaded (HTTP 529)\n"`, stdout `"Notification sent: 2 spot(s) recommended.\n"`, `sent == [REF_MESSAGE_RULES_ONLY]`, rc 3<br>(b) stderr `"warning: Pipeline: no wave data returned for Pipeline\nwarning: Jev request failed: rate limited (HTTP 429)\n"`, stdout `"...1 spot(s)..."`, rc 3<br>(c) the (a) warning, stdout `"No good spots today; no notification sent.\n"`, rc 3 |
| TC-176 | AC-14.7 | Notifier raises `NotificationError("CallMeBot returned HTTP 500: x")`; then also the Pipeline failure | stderr `"error: failed to send WhatsApp message: CallMeBot returned HTTP 500: x\n"`, rc 4; then the warning line followed by that error line, rc 4 |
| TC-177 | AC-14.8 | Only `TYPESAFE_API_KEY` set; `check --dry-run` | stdout `REF_MESSAGE + "\n"`; rc 0; `notifier_calls == []` |
| TC-178 | AC-14.8 degraded | Dry run with (a) a Jev 529, (b) a Pipeline failure and noul 0.81, (c) no candidates | (a) stdout `REF_MESSAGE_RULES_ONLY + "\n"`, the Jev warning, rc 3<br>(b) stdout `SUNSET_ONLY_MESSAGE_81 + "\n"`, rc 3<br>(c) the "No good spots" line, rc 0 |
| TC-179 | AC-14.9 | Table I (the env file does not exist) | stderr exactly `missing_no_file(names, missing_env_path)`: `"error: missing environment variable(s): " + names`, one `"  NAME: <help>"` line per name, then `"No env file was found at <path>. Create it with: cp .env.example <path>"`; rc 1; no factory called |
| TC-180 | check order | Missing config and no env vars | The `config file not found` error; rc 1 |
| TC-181 | AC-14.10 | `TYPESAFE_API_KEY="ts-key"`, `WHATSAPP_PHONE="0612345678"`, `CALLMEBOT_API_KEY="cmb-key"`; real `make_notifier` restored from `cli_fakes.originals` | stderr `"error: WHATSAPP_PHONE must be in international format, e.g. +34612345678\n"`; stdout `""`; rc 1; no fetch; no Jev call |
| TC-182 | §4.12 whitespace-only means missing; values passed unstripped | (a) `TYPESAFE_API_KEY="   "`, `check --dry-run`<br>(b) `TYPESAFE_API_KEY="ts-key"`, phone valid, `CALLMEBOT_API_KEY="  \t"`, `check`<br>(c) `WHATSAPP_PHONE=" "`, `CALLMEBOT_API_KEY="cmb-key"`, `test-notification`<br>(d) `TYPESAFE_API_KEY=" ts-key "`, `check --dry-run`; and `WHATSAPP_PHONE=" +34612345678"`, `CALLMEBOT_API_KEY=" cmb-key "`, `TYPESAFE_API_KEY="ts-key"`, `check` (fake factories) | (a)–(c): stderr is `missing_no_file(names, missing_env_path)` for `TYPESAFE_API_KEY`, `CALLMEBOT_API_KEY` and `WHATSAPP_PHONE`; rc 1, no factory called<br>(d) `jev_calls[0][0] == " ts-key "`, rc 0; `notifier_calls == [(" +34612345678", " cmb-key ")]` |
| TC-183 | AC-16.1 | Phone and key set; `run_cli("test-notification", config=tmp_path/"absent.toml")` | `notifier_calls == [("+34612345678","cmb-key")]`; `sent == ["Surf alert test message: WhatsApp notifications are working."]`; stdout `"Test notification sent.\n"`; stderr `""`; rc 0; no Surfline or Jev factory call |
| TC-184 | AC-16.2 | `send` raises `NotificationError("CallMeBot returned HTTP 203: APIKey is invalid")` | stderr `"error: failed to send WhatsApp message: CallMeBot returned HTTP 203: APIKey is invalid\n"`; rc 4 |
| TC-185 | AC-16.3 | Env: none; phone only; key only (`TYPESAFE_API_KEY` never set) | stderr is `missing_no_file(...)` for `WHATSAPP_PHONE, CALLMEBOT_API_KEY`; `CALLMEBOT_API_KEY`; `WHATSAPP_PHONE`; rc 1; `notifier_calls == []` |
| TC-186 | test-notification step 3 | Phone `"0612345678"` with the real `make_notifier` | stderr `"error: WHATSAPP_PHONE must be in international format, e.g. +34612345678\n"`; rc 1 |
| TC-187 | AC-17.1 | Env file `f` with `TYPESAFE_API_KEY=fromfile`; `setenv("TYPESAFE_API_KEY","fromenv")`; `check --dry-run` | `jev_calls[0][0] == "fromenv"` |
| TC-188 | AC-17.2 | `f` with `TYPESAFE_API_KEY=fromfile` only; then `f` with all three (`CALLMEBOT_API_KEY=cmb-file`) and `check` | `"fromfile"`; `notifier_calls == [("+34612345678","cmb-file")]`, rc 0 |
| TC-189 | AC-17.3 | `secrets_env`; `env_file = tmp_path/"nodir"/"x.env"` | stdout `"Notification sent: 2 spot(s) recommended.\n"`; rc 0 |
| TC-190 | AC-17.4, Security | Secrets `TYPESAFE_API_KEY="ts-SECRET-7f3a"` and `CALLMEBOT_API_KEY="cmb-SECRET-9c1e"`. `make_jev_client` is patched to `JevClient(k, model=s.jev_model, session=FakeSession(...))` and `make_notifier` to `CallMeBotNotifier(p, k, session=FakeSession(...))`. Scenarios:<br>(a) Jev 200 `REF_JEV_RESPONSE`, CallMeBot 200<br>(b) CallMeBot `(500, "invalid apikey cmb-SECRET-9c1e")`<br>(c) CallMeBot raises `requests.ConnectionError("failed url=/whatsapp.php?apikey=cmb-SECRET-9c1e")`<br>(d) Jev `(401,"no")`<br>(e) Jev `(422, "token ts-SECRET-7f3a")`<br>(f) (a) with `-v` and `caplog.set_level(logging.DEBUG, logger="surf_alert")`<br>(g) `test-notification` with CallMeBot `(203, "cmb-SECRET-9c1e invalid")` | Neither secret appears in `out + err` in any scenario.<br>(a) rc 0<br>(b) `"error: failed to send WhatsApp message: CallMeBot returned HTTP 500: invalid apikey ***\n"`, rc 4<br>(c) `"error: failed to send WhatsApp message: request to CallMeBot failed: failed url=/whatsapp.php?apikey=***\n"`, rc 4<br>(d) `"warning: Jev request failed: authentication failed (HTTP 401); check TYPESAFE_API_KEY\n"`, rc 3<br>(e) `"warning: Jev request failed: invalid request (HTTP 422): token ***\n"`, rc 3<br>(f) neither secret in `caplog.text`, and no record message matches `https?://\S*\?`<br>(g) `"error: failed to send WhatsApp message: CallMeBot returned HTTP 203: *** invalid\n"`, rc 4 |
| TC-191 | §6.5 | `secrets_env`; Pipeline fetch raises `RuntimeError("bug")` | `main` raises `RuntimeError` |
| TC-192 | Offline NFR, factories | Use `cli_fakes.originals`:<br>(a) `make_surfline_client(Settings())`<br>(b) `make_jev_client("k", Settings(jev_model="jev-test"))`, with `requests.Session.post` patched to a recorder `(self, url, *, headers, json, timeout)` returning 200 `{"answers":{"q":{"type":"noul","noul":0.5}}}`; then `.ask({"a":1}, {"q": {...}})`<br>(c) `make_notifier("+34612345678","k")`, with `requests.Session.get` patched to return 200; then `.send("hi")`<br>(d) `utc_now()` | (a) a `SurflineClient` (pysurfline not imported)<br>(b) a `JevClient`; the recorder saw model `"jev-test"` and timeout `30.0`; returns `{"q": ...}`<br>(c) a `CallMeBotNotifier`; params as in TC-137 with `apikey "k"`<br>(d) `utcoffset() == timedelta(0)`, within 5 s of `datetime.now(timezone.utc)` |
| TC-210 | §4.12 logging, Security | Do not touch the root level or use `caplog` on root.<br>(a) `run_cli("-v","spots")`<br>(b) `run_cli("spots")` | (a) `logging.getLogger("surf_alert").level == logging.DEBUG`; `logging.getLogger().level == logging.WARNING`; `logging.getLogger("urllib3").getEffectiveLevel() >= logging.WARNING`<br>(b) `"surf_alert"` level `== logging.WARNING`; root `== logging.WARNING`<br>Both rc 0 |
| TC-211 | §4.12 global options position | `main(["spots","--config",str(p)])`; `main(["--env-file",m,"check","--config",str(p)])`; `main(["--config",str(p),"rules","-v"])` | `SystemExit` with `.code == 2`; stderr contains `usage:`; no factory called |
| TC-212 | §5.2 empty real var vs `.env` | `setenv("TYPESAFE_API_KEY", "")`; env file with `TYPESAFE_API_KEY=fromfile`; `check --dry-run` | stderr `"error: missing environment variable(s): TYPESAFE_API_KEY\n"`; rc 1; no factory called |
| TC-218 | §4.12 missing-variable reasons | (a) Env file with `TYPESAFE_API_KEY=` (empty), `WHATSAPP_PHONE=   ` and `CALLMEBOT_API_KEY` only in a `#` comment; `check`<br>(b) Env file with `TYPESAFE_API_KEY=ts-SECRET` and `WHATSAPP_PHONE`, shell `TYPESAFE_API_KEY=" "`; `test-notification`, then `check --dry-run` | (a) the three variables listed, with `it is empty in <file>`, `it is empty in <file>` and `it is not in <file>`, then `Add the missing values to <file> (see .env.example for the format).`; rc 1; no factory called<br>(b) `CALLMEBOT_API_KEY` `not in <file>` plus the Add line; then the shell-override reason with `run: unset TYPESAFE_API_KEY`, no Add line, and `ts-SECRET` never printed |

Table I (TC-179). Variables not listed are unset:

| env | command | names |
|---|---|---|
| none | `check` | `TYPESAFE_API_KEY, WHATSAPP_PHONE, CALLMEBOT_API_KEY` |
| `WHATSAPP_PHONE=+34612345678` | `check` | `TYPESAFE_API_KEY, CALLMEBOT_API_KEY` |
| phone set; `TYPESAFE_API_KEY=""`; `CALLMEBOT_API_KEY=""` | `check` | `TYPESAFE_API_KEY, CALLMEBOT_API_KEY` |
| all three set to `"  "` | `check` | `TYPESAFE_API_KEY, WHATSAPP_PHONE, CALLMEBOT_API_KEY` |
| `TYPESAFE_API_KEY=ts-key` | `check` | `WHATSAPP_PHONE, CALLMEBOT_API_KEY` |
| phone and `CALLMEBOT_API_KEY` set | `check` | `TYPESAFE_API_KEY` |
| none | `check --dry-run` | `TYPESAFE_API_KEY` |

### 7.12 `tests/test_e2e.py` (end-to-end, offline)

Subprocess tests (marker `e2e`) run `[sys.executable, "-m", "surf_alert", ...]` with `cwd=tmp_path`, `PYTHONPATH=str(PROJECT_ROOT)` and an env copy without the three secrets.

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-193 | `__main__`, AC-2.1 | `--config <ref> --env-file <missing> spots` | returncode 0; stdout as in TC-160 |
| TC-194 | AC-3.1 | `... rules` | returncode 0; `"1. wave_height_min > 2 ft\n2. swell_period > 7 s\n"` |
| TC-195 | exit code 2 | No arguments | returncode 2; `"usage:"` in stderr |
| TC-196 | AC-4.6, Offline NFR | `[sys.executable, "-c", code]`. The code puts a finder at `sys.meta_path[0]` that records and raises `ImportError` for any top-level `pysurfline`, `pandas` or `matplotlib`; imports `surf_alert` and its 13 submodules (not `__main__`); builds `SurflineClient(Settings())`; prints the recorded list and the subset of those names found in `sys.modules`, as JSON | returncode 0; both lists empty |
| TC-197 | Full stack (FR-4, 5, 8, 9, 12, 13, 14) | In-process with `secrets_env`:<br>`make_surfline_client = lambda s: SurflineClient(s, forecast_factory=fac)`, where `fac` returns `pipeline_raw()` or `sunset_raw()` by `spotId` and records params<br>`make_jev_client = lambda k, s: JevClient(k, model=s.jev_model, session=js)`, with `js` returning 200 `REF_JEV_RESPONSE`<br>`make_notifier = lambda p, k: CallMeBotNotifier(p, k, session=cs)`, with `cs` returning 200<br>`utc_now` returns `NOW`<br>Run `run_cli("check")` | rc 0; stdout `"Notification sent: 2 spot(s) recommended.\n"`; stderr `""`<br>`fac` params `== [{"spotId": PIPELINE.spot_id, "days":1, "intervalHours":1}, {"spotId": SUNSET.spot_id, ...}]`<br>One `js` call with `"Bearer ts-key"` and json `{"model":"jev-latest","state":REF_STATE,"questions":REF_QUESTIONS}`, timeout 30.0<br>One `cs` call with params `{"phone":"+34612345678","text":REF_MESSAGE,"apikey":"cmb-key"}` |
| TC-198 | Full stack, AC-14.6/14.8 | As TC-197, but `js` returns `(529,"busy")`; `run_cli("check","--dry-run")` | rc 3; stdout `REF_MESSAGE_RULES_ONLY + "\n"`; stderr `"warning: Jev request failed: service overloaded (HTTP 529)\n"`; `cs.calls == []` |

### 7.13 `tests/test_artifacts.py` (deliverables)

| TC | Covers | Action | Expected |
|---|---|---|---|
| TC-199 | §2, §5.1 | `load_config(PROJECT_ROOT/"surf_alert.example.toml")` | `spots == (PIPELINE,)`; `rules == REF_RULES`; `settings == REF_SETTINGS` |
| TC-200 | §5.2 | Non-blank, non-`#` stripped lines of `.env.example` | `["TYPESAFE_API_KEY=", "WHATSAPP_PHONE=+34612345678", "CALLMEBOT_API_KEY="]` |
| TC-201 | §7 | Non-comment lines of `requirements.txt` with all whitespace removed | Includes `requests>=2.31`, `python-dotenv>=1.0`, `pytest>=8` and `pysurfline@git+https://github.com/wolfinger/pysurfline@f0c98ea06ce9f26f5ba9e3b94ac98963150f7ddc` |
| TC-213 | §4.1, §4.13 | Read `PROJECT_ROOT/"README.md"` (UTF-8) | The file exists, and its text contains `check --dry-run` and `test-notification` |

### 7.14 `tests/test_report.py` and the `-v` CLI tests (spec 4.14)

| TC | Covers | Setup / action | Expected |
|---|---|---|---|
| TC-219 | `RecordingJev` | Wraps `FakeJev(REF_ANSWERS)`, a Jev that raises, and malformed answers (empty, not a dict, bool or out-of-range values, partial, unknown refs) | Forwards one call and records state and answers; confidences `{"Pipeline":0.92,"Sunset Beach":0.81}`, best `("Pipeline", {...0.88, 0.12})`; on an exception the answers are `None`; only valid probabilities for known spots appear |
| TC-220 | `build_spot_reports` | Reference run; Sunset rejected at 0.55; rules-only fallback; `on_jev_error="skip"`; no Jev; failed rules; empty window; fetch failure; one spot evaluated | Rule hours `(5, 12)`, ranks, confidences and the §4.14 outcome texts; config order; unevaluated spots left out |
| TC-221 | `format_report` | The rejected-Sunset run; endings with Jev not asked and Jev failed; a spot with no daylight left | The exact text in `REJECTED_REPORT`; the closing lines; no per-rule lines for an empty window |
| TC-222 | `write_csv` | Two evaluated spots and one fetch failure, over an existing file | Exact header and rows; the returned absolute path; the file is overwritten |
| TC-223 | §4.14 with `check` | `-v check --dry-run` with the CLI fakes; then without `-v`; then `-v spots` | stdout `REF_MESSAGE + "\n"`, rc 0, the report and `Spot table written to ...` on stderr, one Jev call, table ranks and confidences; no table without `-v` or for `spots` |
| TC-224 | §4.14 with `conditions` | `-v conditions`; `-v conditions --spot "sunset beach"` | stdout `REF_CONDITIONS_OUTPUT`; outcomes `passes rules (Jev not asked)`; empty Jev columns; one row with `--spot` |
| TC-225 | §4.14 write failure | `cli.REPORT_PATH` set to a directory | rc 0, stdout unchanged, `warning: could not write the spot table to ...` |

## 8. Traceability matrix

| Requirement | Test cases |
|---|---|
| AC-1.1 / 1.2 / 1.3 | TC-10 / TC-11, TC-161 / TC-12, TC-161 |
| AC-1.4 | TC-13, TC-20–TC-43, TC-161, TC-202–TC-205 |
| AC-1.5 / 1.6 / 1.7 / 1.8; FR-1 other | TC-14, TC-203 / TC-15, TC-42, TC-204 / TC-16 / TC-17; TC-18, TC-19 |
| AC-2.1 / 2.2 | TC-160, TC-193 / TC-161, TC-202 |
| AC-3.1 / 3.2 / 3.3 | TC-162, TC-194 / TC-94 / TC-95 |
| AC-4.1 / 4.2 / 4.3 | TC-48 / TC-49, TC-50 / TC-51 |
| AC-4.4 / 4.5 / 4.6 / 4.7 / 4.8; FR-4/5 reference | TC-52, TC-55 / TC-56 / TC-53, TC-196 / TC-53, TC-214, TC-215 / TC-216; TC-54, TC-197 |
| AC-5.1 / 5.2 / 5.3 / 5.4 / 5.5 | TC-60 / TC-61 / TC-62 / TC-63 / TC-64, TC-65, TC-66 |
| AC-5.6 / 5.7 / 5.8 / 5.9 / 5.10 | TC-57 / TC-69 / TC-70 / TC-71 / TC-58 |
| FR-5 other (§4.5, units) | TC-44–TC-47, TC-206, TC-59, TC-67, TC-68, TC-72, TC-73, TC-74, TC-217 |
| AC-6.1 / 6.2 / 6.3 / 6.4 / 6.5 / 6.6; FR-6 other | TC-76, TC-77 / TC-78, TC-93 / TC-79 / TC-80, TC-81 / TC-84, TC-93 / TC-85, TC-75; TC-7, TC-82, TC-83 |
| AC-7.1 / 7.2 / 7.3 / 7.4 / 7.5 / 7.6 | TC-87 / TC-88 / TC-89 / TC-90 / TC-91, TC-92 / TC-86 |
| AC-8.1 / 8.2 / 8.3 / 8.4 | TC-106 / TC-107 / TC-108, TC-109, TC-197 / TC-110; TC-105 |
| AC-9.1 / 9.2 / 9.3 / 9.4 / 9.5 / 9.6 | TC-96, TC-97 / TC-98 / TC-99, TC-100, TC-207 / TC-101, TC-207 / TC-102 / TC-103; TC-104 |
| AC-10.1 / 10.2 / 10.3 / 10.4 / 10.5 / 10.6 | TC-111 / TC-112 / TC-113 / TC-114 / TC-116 / TC-117, TC-118; TC-115 |
| AC-11.1 / 11.2 / 11.3 | TC-120, TC-121, TC-155 / TC-122, TC-156 / TC-123; TC-119 |
| AC-12.1 / 12.2 / 12.3 / 12.4 / 12.5 / 12.6 / 12.7 | TC-131 / TC-133, TC-134 / TC-132 / TC-128 / TC-125 / TC-124 / TC-135; TC-126, TC-127, TC-129 |
| AC-13.1 / 13.2 / 13.3 / 13.4 / 13.5 / 13.6 | TC-137 / TC-138 / TC-139, TC-140, TC-208 / TC-141, TC-208 / TC-142 / TC-143, TC-209; TC-144 |
| AC-14.1 / 14.2 / 14.3 / 14.4 / 14.5 | TC-149, TC-170 / TC-150, TC-171 / TC-152, TC-170 / TC-153, TC-173 / TC-154, TC-174 |
| AC-14.6 / 14.7 / 14.8 | TC-155, TC-175, TC-198 / TC-157, TC-176 / TC-158, TC-177, TC-178, TC-198 |
| AC-14.9 / 14.10 / 14.11; FR-14 other | TC-179, TC-180, TC-182, TC-212 / TC-181 / TC-159; TC-145–TC-148, TC-151, TC-156, TC-172, TC-197, TC-211 |
| AC-15.1 / 15.2 / 15.3 / 15.4 / 15.5 | TC-166 / TC-167 / TC-168 / TC-169 / TC-130 |
| AC-16.1 / 16.2 / 16.3 | TC-183 / TC-184 / TC-185, TC-182; TC-186 |
| AC-17.1 / 17.2 / 17.3 / 17.4 | TC-187 / TC-188, TC-163 / TC-189 / TC-190, TC-140, TC-141, TC-207, TC-208, TC-210 |
| §6.1 table rows | TC-11, TC-202, TC-12, TC-20–TC-41 (one TC per row) |
| §6.1 order, Unknown keys, Case, Blank strings, Spot ids | TC-43, TC-205, TC-203, TC-204, TC-18 + TC-31 |
| §6.2 rows | TC-51, TC-53, TC-147, TC-216 / TC-52, TC-55 / TC-56, TC-80 / TC-57 / TC-69 / TC-71 / TC-154, TC-174 / TC-84, TC-93; a Surfline hang is not testable |
| §6.3 rows, redaction, no retries, `on_jev_error` | TC-99–TC-102, TC-117, TC-207, TC-190; TC-99; TC-120–TC-122, TC-155, TC-156 |
| §6.4 rows, redaction, exit 4 | TC-139–TC-141, TC-208; TC-157, TC-176 |
| §6.5 items | TC-149, TC-152, TC-156 / TC-106, TC-115, TC-150 / TC-133 / TC-144 / TC-7, TC-73–TC-75, TC-125 / TC-78, TC-93, TC-159 / TC-104, TC-123, TC-148, TC-191 |
| §4 interface, §4.12 logging and usage | TC-1–TC-9; TC-210, TC-50; TC-164, TC-211 |
| NFRs | Runtime: section 3. Dependencies: TC-201. Offline: TC-48, TC-53, TC-96, TC-137, TC-192, TC-196 and the autouse guards. Timeouts: TC-96, TC-97, TC-137, TC-144, TC-192. Volume: TC-32, TC-149, TC-151, TC-154, TC-172. Security: TC-140, TC-141, TC-143, TC-190, TC-207, TC-208, TC-210. Output: TC-136 |
| Deliverables | TC-193, TC-199, TC-200, TC-201, TC-213 |

## 9. Exit criteria

1. `.venv/bin/python -m pytest` exits 0, with no failures, errors, skips or xfails.
2. All 225 test cases (TC-1 to TC-225) are implemented, and each test carries its TC id in its name and docstring.
3. Every row of the traceability matrix has at least one passing test.
4. The suite passes with no network access, whether or not `pysurfline`, pandas or matplotlib are installed.
5. The suite is deterministic: it passes three runs in a row, each file passes when run alone, and no test reads the developer's real `.env` or `surf_alert.toml`.
6. The full suite runs in under 30 s.
7. In fix rounds, a test is changed only when it contradicts the spec.

## 10. Risks and gaps

Spec gaps that change expected results: **none remaining**. All earlier gaps are resolved in the spec (Assumption 21, §4.5, §4.6, §4.10, §4.12, §4.13, §5.2, §6.1, §6.3, §6.4, AC-10.6). One edge is still unspecified, and no realistic key hits it: when the API key is a substring of its own `quote_plus` form (for example a key containing `%2`), the order of the two `redact` replacements is not defined. The tests use keys without this property.

Not testable offline: Surfline hangs; whether the real pysurfline `units_*` and CallMeBot response shapes match (spec open question 6); whether pysurfline installs on 3.13 (open question 7); config paths that are unreadable for permission reasons (the directory case stands in for them); README content beyond the two command names.

Test-design risks:
- TC-210 relies on the root logger being at Python's default WARNING when `main` runs. This holds because pytest changes the root level only when `log_level` is configured, and `pytest.ini` must not configure it. `basicConfig` is a no-op under pytest (the root already has pytest's handlers), so a spec-compliant `main` leaves the root at WARNING. Tests must use `caplog.set_level(..., logger="surf_alert")` and never set the root logger's level.
- The tests enforce the spec's container types (tuples in dataclasses, a list from `evaluate_spots`). Correct values in the wrong container fail.
- `requests.JSONDecodeError` is both a `RequestException` and a `ValueError`. TC-102 requires the "malformed response" message, so an implementation that wraps `.json()` inside its `except RequestException` fails.
- `load_dotenv` writes to `os.environ`, and `cli.main` changes logger levels. The autouse `isolate_env` and `reset_logging` fixtures keep tests independent.
