"""Public interface checks (spec section 4): TC-1 to TC-9."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
from datetime import datetime

import pytest

from surf_alert import app, cli, config, decision, evaluate, jev, message, notify, surfline, units
from surf_alert.errors import ConfigError, JevError, NotificationError, SurfAlertError, SurflineError
from surf_alert.models import (
    AppConfig,
    DaylightWindow,
    Decision,
    ForecastData,
    Recommendation,
    Rule,
    RunResult,
    Settings,
    Slot,
    Spot,
    SpotEvaluation,
    Swell,
)
from tests.scenario import (
    PIPELINE,
    REF_DAYLIGHT,
    REF_RULES,
    REF_SETTINGS,
    SPEC_BEST_SPOT_INSTRUCTIONS,
    SPEC_NOUL_TEMPLATE,
    make_slot,
    pipeline_evaluation,
    pipeline_forecast,
    ref_app_config,
)

P = inspect.Parameter
EMPTY = inspect.Parameter.empty
POK = P.POSITIONAL_OR_KEYWORD
KWO = P.KEYWORD_ONLY

SUBMODULES = (
    "errors",
    "models",
    "config",
    "units",
    "surfline",
    "evaluate",
    "jev",
    "decision",
    "message",
    "notify",
    "app",
    "cli",
)


def test_tc001_modules_import_and_version():
    """TC-1: every surf_alert module imports and __version__ is "0.1.0"."""
    package = importlib.import_module("surf_alert")
    for name in SUBMODULES:
        importlib.import_module(f"surf_alert.{name}")
    assert package.__version__ == "0.1.0"


@pytest.mark.parametrize("cls", [ConfigError, SurflineError, JevError, NotificationError])
def test_tc002_error_hierarchy(cls):
    """TC-2: every error subclasses SurfAlertError (an Exception) and keeps its message."""
    exc = cls("msg")
    assert issubclass(cls, SurfAlertError)
    assert issubclass(SurfAlertError, Exception)
    assert isinstance(exc, SurfAlertError)
    assert str(exc) == "msg"


def test_tc003_jev_error_status_code():
    """TC-3: JevError.status_code defaults to None and accepts a positional or keyword value."""
    plain = JevError("m")
    positional = JevError("m", 429)
    keyword = JevError("m", status_code=401)
    assert plain.status_code is None
    assert positional.status_code == 429
    assert keyword.status_code == 401
    assert str(plain) == str(positional) == str(keyword) == "m"


@pytest.mark.parametrize(
    "cls, names",
    [
        (Spot, ["name", "spot_id", "notes"]),
        (Rule, ["metric", "op", "value"]),
        (
            Settings,
            [
                "height_unit",
                "wind_unit",
                "temperature_unit",
                "min_good_hours",
                "jev_threshold",
                "jev_model",
                "on_jev_error",
                "max_spots_in_message",
                "notes",
            ],
        ),
        (AppConfig, ["settings", "spots", "rules"]),
        (Swell, ["height", "period", "direction"]),
        (
            Slot,
            [
                "timestamp",
                "utc_offset",
                "surf_min",
                "surf_max",
                "surf_rating",
                "primary_swell",
                "wind_speed",
                "wind_gust",
                "wind_direction",
                "wind_direction_type",
                "tide_height",
                "temperature",
                "weather_condition",
            ],
        ),
        (DaylightWindow, ["local_date", "dawn", "dusk"]),
        (ForecastData, ["spot", "slots", "daylight"]),
        (SpotEvaluation, ["spot", "local_date", "current", "window", "good_slots", "passes_rules"]),
        (Recommendation, ["evaluation", "probability"]),
        (Decision, ["recommendations", "jev_used", "jev_error"]),
        (
            RunResult,
            ["evaluations", "failures", "decision", "message", "sent", "send_error", "exit_code"],
        ),
    ],
    ids=lambda v: v.__name__ if isinstance(v, type) else None,
)
def test_tc004_dataclass_field_order(cls, names):
    """TC-4: each model is a dataclass whose fields appear in the spec order."""
    assert dataclasses.is_dataclass(cls)
    assert [f.name for f in dataclasses.fields(cls)] == names


def _instances():
    evaluation = pipeline_evaluation()
    return [
        PIPELINE,
        REF_RULES[0],
        REF_SETTINGS,
        ref_app_config(),
        Swell(4.2, 11.0, 315.0),
        make_slot(7),
        REF_DAYLIGHT[0],
        pipeline_forecast(),
        evaluation,
        Recommendation(evaluation, 0.9),
        Decision((), True),
        RunResult((), {}, None, None, False, None, 0),
    ]


@pytest.mark.parametrize("obj", _instances(), ids=lambda o: type(o).__name__)
def test_tc005_dataclasses_are_frozen(obj):
    """TC-5: assigning an attribute on any model instance raises FrozenInstanceError."""
    first = dataclasses.fields(obj)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, first, getattr(obj, first))


def test_tc006_defaults():
    """TC-6: Settings, Spot and Decision defaults."""
    assert Settings() == Settings("ft", "kts", "C", 1, 0.7, "jev-latest", "rules_only", 3, "")
    assert Spot("a", "b").notes == ""
    assert Decision((), True).jev_error is None


@pytest.mark.parametrize(
    "slot, expected",
    [
        (make_slot(7), datetime(2026, 10, 7, 7, 0)),
        (make_slot(timestamp=1791331200 + 7200, utc_offset=5.5), datetime(2026, 10, 7, 7, 30)),
        (make_slot(timestamp=1791392400, utc_offset=-9.5), datetime(2026, 10, 7, 7, 30)),
    ],
    ids=["minus10", "plus5_5", "minus9_5"],
)
def test_tc007_slot_local_datetime(slot, expected):
    """TC-7: Slot.local_datetime() is the naive local wall-clock time, fractional offsets included."""
    result = slot.local_datetime()
    assert result == expected
    assert result.tzinfo is None


def test_tc008_constants():
    """TC-8: module constants match spec 4.4 to 4.10."""
    assert list(config.METRICS.items()) == [
        ("wave_height_min", "height"),
        ("wave_height_max", "height"),
        ("surf_rating", "score"),
        ("swell_height", "height"),
        ("swell_period", "period"),
        ("wind_speed", "speed"),
        ("wind_gust", "speed"),
        ("wind_direction_type", "text"),
        ("tide_height", "height"),
        ("temperature", "temperature"),
        ("weather_condition", "text"),
    ]
    assert config.NUMERIC_OPS == (">", ">=", "<", "<=", "==", "!=")
    assert config.TEXT_OPS == ("==", "!=", "in", "not_in")
    assert config.ALL_OPS == (">", ">=", "<", "<=", "==", "!=", "in", "not_in")
    assert config.MAX_SPOTS == 50

    assert units.FT_PER_M == 3.28084
    assert units.KPH_PER_KT == 1.852
    assert units.MPH_PER_KT == 1.150779

    assert surfline.FORECAST_DAYS == 1
    assert surfline.FORECAST_INTERVAL_HOURS == 1
    assert surfline.MATCH_TOLERANCE_SECONDS == 3600
    assert surfline.KBYG_URL == "https://services.surfline.com/kbyg/spots/forecasts/{}"
    assert surfline.DEFAULT_TIMEOUT == 30.0
    assert evaluate.FALLBACK_DAYLIGHT_HOURS == (6, 19)

    assert jev.JEV_URL == "https://api.typesafe.ai/v1/systemone"
    assert jev.DEFAULT_TIMEOUT == 30.0

    assert decision.NOUL_TEMPLATE == SPEC_NOUL_TEMPLATE
    assert decision.BEST_SPOT_INSTRUCTIONS == SPEC_BEST_SPOT_INSTRUCTIONS

    assert notify.CALLMEBOT_URL == "https://api.callmebot.com/whatsapp.php"
    assert notify.PHONE_PATTERN == r"^\+[1-9][0-9]{6,14}$"
    assert notify.TEST_MESSAGE == "Surf alert test message: WhatsApp notifications are working."

    assert hasattr(surfline, "ForecastSource")
    assert hasattr(jev, "JevLike")
    assert hasattr(notify, "Notifier")


def _p(name, kind=POK, default=EMPTY):
    return (name, kind, default)


SIGNATURES = [
    ("config.load_config", lambda: config.load_config, [_p("path")]),
    ("config.parse_config", lambda: config.parse_config, [_p("data")]),
    (
        "units.convert_height",
        lambda: units.convert_height,
        [_p("value"), _p("from_unit"), _p("to_unit")],
    ),
    (
        "units.convert_speed",
        lambda: units.convert_speed,
        [_p("value"), _p("from_unit"), _p("to_unit")],
    ),
    (
        "units.convert_temperature",
        lambda: units.convert_temperature,
        [_p("value"), _p("from_unit"), _p("to_unit")],
    ),
    (
        "surfline.SurflineClient",
        lambda: surfline.SurflineClient,
        [
            _p("settings"),
            _p("forecast_factory", default=None),
            _p("session", KWO, None),
            _p("timeout", KWO, 30.0),
        ],
    ),
    ("surfline.SurflineClient.fetch", lambda: surfline.SurflineClient.fetch, [_p("spot")]),
    (
        "surfline.parse_forecast",
        lambda: surfline.parse_forecast,
        [_p("raw"), _p("spot"), _p("settings")],
    ),
    ("evaluate.local_date_of", lambda: evaluate.local_date_of, [_p("timestamp"), _p("utc_offset")]),
    ("evaluate.current_slot", lambda: evaluate.current_slot, [_p("slots"), _p("now")]),
    (
        "evaluate.evaluation_window",
        lambda: evaluate.evaluation_window,
        [_p("forecast"), _p("now")],
    ),
    ("evaluate.metric_value", lambda: evaluate.metric_value, [_p("slot"), _p("metric")]),
    ("evaluate.rule_passes", lambda: evaluate.rule_passes, [_p("rule"), _p("slot")]),
    ("evaluate.slot_passes", lambda: evaluate.slot_passes, [_p("rules"), _p("slot")]),
    (
        "evaluate.evaluate_spot",
        lambda: evaluate.evaluate_spot,
        [_p("forecast"), _p("rules"), _p("settings"), _p("now")],
    ),
    ("evaluate.describe_rule", lambda: evaluate.describe_rule, [_p("rule"), _p("settings")]),
    (
        "jev.JevClient",
        lambda: jev.JevClient,
        [
            _p("api_key"),
            _p("model", KWO, "jev-latest"),
            _p("session", KWO, None),
            _p("timeout", KWO, 30.0),
        ],
    ),
    ("jev.JevClient.ask", lambda: jev.JevClient.ask, [_p("state"), _p("questions")]),
    ("decision.candidate_ref", lambda: decision.candidate_ref, [_p("index")]),
    (
        "decision.build_jev_request",
        lambda: decision.build_jev_request,
        [_p("candidates"), _p("rules"), _p("settings")],
    ),
    (
        "decision.interpret_jev_answers",
        lambda: decision.interpret_jev_answers,
        [_p("answers"), _p("candidates"), _p("settings")],
    ),
    (
        "decision.decide",
        lambda: decision.decide,
        [_p("candidates"), _p("rules"), _p("settings"), _p("jev")],
    ),
    ("message.compass", lambda: message.compass, [_p("degrees")]),
    ("message.format_hours", lambda: message.format_hours, [_p("slots")]),
    (
        "message.format_spot_block",
        lambda: message.format_spot_block,
        [_p("evaluation"), _p("settings"), _p("probability", default=None)],
    ),
    (
        "message.format_rules_status",
        lambda: message.format_rules_status,
        [_p("evaluation"), _p("settings")],
    ),
    ("message.format_message", lambda: message.format_message, [_p("decision"), _p("settings")]),
    (
        "notify.CallMeBotNotifier",
        lambda: notify.CallMeBotNotifier,
        [_p("phone"), _p("api_key"), _p("session", KWO, None), _p("timeout", KWO, 30.0)],
    ),
    ("notify.CallMeBotNotifier.send", lambda: notify.CallMeBotNotifier.send, [_p("text")]),
    (
        "app.evaluate_spots",
        lambda: app.evaluate_spots,
        [_p("config"), _p("surfline"), _p("now"), _p("spots", default=None)],
    ),
    (
        "app.run_check",
        lambda: app.run_check,
        [
            _p("config"),
            _p("surfline", KWO),
            _p("jev", KWO),
            _p("notifier", KWO),
            _p("now", KWO),
        ],
    ),
    ("cli.make_surfline_client", lambda: cli.make_surfline_client, [_p("settings")]),
    ("cli.make_jev_client", lambda: cli.make_jev_client, [_p("api_key"), _p("settings")]),
    ("cli.make_notifier", lambda: cli.make_notifier, [_p("phone"), _p("api_key")]),
    ("cli.utc_now", lambda: cli.utc_now, []),
    ("cli.main", lambda: cli.main, [_p("argv", default=None)]),
]


@pytest.mark.parametrize(
    "getter, expected",
    [(getter, expected) for _, getter, expected in SIGNATURES],
    ids=[name for name, _, _ in SIGNATURES],
)
def test_tc009_signatures(getter, expected):
    """TC-9: parameter names, kinds and defaults match spec section 4 (self excluded)."""
    params = [
        (p.name, p.kind, p.default)
        for p in inspect.signature(getter()).parameters.values()
        if p.name != "self"
    ]
    assert params == expected
