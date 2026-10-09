"""Reference config, scenario and expected outputs (spec 5.1, 5.4, 5.5, 5.6).

Data and builders only. Every parsed or evaluated object here is built
directly from the spec, never by calling production code.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

from surf_alert.models import (
    AppConfig,
    DaylightWindow,
    Decision,
    ForecastData,
    Recommendation,
    Rule,
    Settings,
    Slot,
    Spot,
    SpotEvaluation,
    Swell,
)

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 7, 17, 30, tzinfo=timezone.utc)  # epoch 1791394200, 07:30 local at -10
LOCAL_MIDNIGHT = 1791367200  # 2026-10-07 00:00 local at utcOffset -10 (10:00Z)


def local_ts(h):
    """Epoch of local hour `h` on 2026-10-07 at utcOffset -10 (`h` may fall outside 0..23)."""
    return LOCAL_MIDNIGHT + 3600 * h


def utc_dt(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc)


DAWN = 1791389400  # 06:10 local
SUNRISE = 1791391200  # 06:40 local
SUNSET_TS = 1791432600  # 18:10 local
DUSK = 1791434400  # 18:40 local

# ---------------------------------------------------------------------------
# Units and daylight
# ---------------------------------------------------------------------------

REF_UNITS = {
    "temperature": "C",
    "tideHeight": "FT",
    "swellHeight": "FT",
    "waveHeight": "FT",
    "windSpeed": "KTS",
}

REF_SUNLIGHT = [
    {
        "midnight": 1791367200,
        "midnightUTCOffset": -10,
        "dawn": 1791389400,
        "dawnUTCOffset": -10,
        "sunrise": 1791391200,
        "sunriseUTCOffset": -10,
        "sunset": 1791432600,
        "sunsetUTCOffset": -10,
        "dusk": 1791434400,
        "duskUTCOffset": -10,
    }
]

REF_DAYLIGHT = (DaylightWindow(date(2026, 10, 7), DAWN, DUSK),)

# ---------------------------------------------------------------------------
# Config objects
# ---------------------------------------------------------------------------

PIPELINE = Spot("Pipeline", "5842041f4e65fad6a7708890", "Only surf it when it is under 6 ft.")
SUNSET = Spot("Sunset Beach", "0123456789abcdef01234567", "")
ROCKY = Spot("Rocky Point", "aaaaaaaaaaaaaaaaaaaaaaaa", "")

REF_RULES = (Rule("wave_height_min", ">", 2.0), Rule("swell_period", ">", 7.0))

REF_SETTINGS = Settings(notes="Intermediate surfer; prefer offshore or light winds.")


def ref_app_config(**settings_overrides):
    return AppConfig(replace(REF_SETTINGS, **settings_overrides), (PIPELINE, SUNSET), REF_RULES)


REF_CONFIG_TOML = """\
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
"""


def ref_config_dict():
    """A fresh parsed copy of the reference config."""
    return tomllib.loads(REF_CONFIG_TOML)


# ---------------------------------------------------------------------------
# Raw pysurfline-shaped data (spec 5.6)
# ---------------------------------------------------------------------------

PIPELINE_SWELLS = [
    {"height": 4.2, "period": 11, "direction": 315.0, "directionMin": 305.0, "optimalScore": 1},
    {"height": 1.1, "period": 7, "direction": 20.0, "directionMin": 10.0, "optimalScore": 0},
    {"height": 0, "period": 0, "direction": 0, "directionMin": 0, "optimalScore": 0},
]

SUNSET_SWELLS = [
    {"height": 3.9, "period": 10, "direction": 310.0, "directionMin": 300.0, "optimalScore": 1},
]

SUNSET_GOOD_HOURS = (8, 9, 10, 15, 16)


def pipeline_raw():
    hours = range(24)
    return SimpleNamespace(
        wave=[
            {
                "timestamp": local_ts(h),
                "utcOffset": -10,
                "surf_min": 2.4255 if 7 <= h <= 11 else 1.2,
                "surf_max": 3.3482 if 7 <= h <= 11 else 1.9,
                "swells": copy.deepcopy(PIPELINE_SWELLS),
            }
            for h in hours
        ],
        wind=[
            {
                "timestamp": local_ts(h),
                "utcOffset": -10,
                "speed": 8.4,
                "direction": 45.0,
                "directionType": "Offshore",
                "gust": 12.3,
                "optimalScore": 1,
            }
            for h in hours
        ],
        tides=[
            {"timestamp": local_ts(h), "utcOffset": -10, "type": "NORMAL", "height": 1.3}
            for h in hours
        ],
        weather=[
            {"timestamp": local_ts(h), "utcOffset": -10, "temperature": 24.0, "condition": "MOSTLY_CLEAR"}
            for h in hours
        ],
        rating=[rating_entry(local_ts(h), 4, "FAIR_TO_GOOD") for h in hours],
        sunlightTimes=copy.deepcopy(REF_SUNLIGHT),
        units_wave=dict(REF_UNITS),
        units_wind=dict(REF_UNITS),
        units_tides=dict(REF_UNITS),
        units_weather=dict(REF_UNITS),
    )


def sunset_raw():
    hours = range(24)
    return SimpleNamespace(
        wave=[
            {
                "timestamp": local_ts(h),
                "utcOffset": -10,
                "surf_min": 2.5 if h in SUNSET_GOOD_HOURS else 1.9,
                "surf_max": 2.8,
                "swells": copy.deepcopy(SUNSET_SWELLS),
            }
            for h in hours
        ],
        wind=[
            {
                "timestamp": local_ts(h),
                "utcOffset": -10,
                "speed": 11.0,
                "direction": 90.0,
                "directionType": "Cross-shore",
                "gust": 15.0,
            }
            for h in hours
        ],
        tides=[
            {"timestamp": local_ts(h), "utcOffset": -10, "type": "NORMAL", "height": 1.3}
            for h in hours
        ],
        weather=[
            {"timestamp": local_ts(h), "utcOffset": -10, "temperature": 24.0, "condition": "MOSTLY_CLEAR"}
            for h in hours
        ],
        rating=[rating_entry(local_ts(h), 3, "FAIR") for h in hours],
        sunlightTimes=copy.deepcopy(REF_SUNLIGHT),
        units_wave=dict(REF_UNITS),
        units_wind=dict(REF_UNITS),
        units_tides=dict(REF_UNITS),
        units_weather=dict(REF_UNITS),
    )


def wave_entry(ts=None, utc_offset=-10, surf_min=2.0, surf_max=3.0, swells=None):
    """One raw wave entry. `swells=None` means the reference Pipeline swells."""
    return {
        "timestamp": local_ts(7) if ts is None else ts,
        "utcOffset": utc_offset,
        "surf_min": surf_min,
        "surf_max": surf_max,
        "swells": copy.deepcopy(PIPELINE_SWELLS) if swells is None else swells,
    }


def wind_entry(ts, speed=8.4, gust=12.3, direction=45.0, direction_type="Offshore"):
    return {
        "timestamp": ts,
        "utcOffset": -10,
        "speed": speed,
        "gust": gust,
        "direction": direction,
        "directionType": direction_type,
    }


def tide_entry(ts, height=1.3):
    return {"timestamp": ts, "utcOffset": -10, "type": "NORMAL", "height": height}


def weather_entry(ts, temperature=24.0, condition="MOSTLY_CLEAR"):
    return {"timestamp": ts, "utcOffset": -10, "temperature": temperature, "condition": condition}


def rating_entry(ts, value=4, key="FAIR_TO_GOOD"):
    return {"timestamp": ts, "utcOffset": -10, "rating": {"key": key, "value": value}}


def make_raw(omit=(), **attrs):
    """A raw forecast object with exactly the given attributes.

    Defaults: `wave=[wave_entry()]`, `wind=[wind_entry(local_ts(7))]` and copies
    of `REF_UNITS` for `units_wave` and `units_wind`. Names in `omit` are left unset.
    """
    values = {
        "wave": [wave_entry()],
        "wind": [wind_entry(local_ts(7))],
        "units_wave": dict(REF_UNITS),
        "units_wind": dict(REF_UNITS),
    }
    values.update(attrs)
    for name in omit:
        values.pop(name, None)
    return SimpleNamespace(**values)


def pysurfline_raw(raw):
    """What pysurfline now returns for `raw`: no `wave`, `units_wave` or `rating`
    (Surfline answers 404 for the `wave` forecast, and pysurfline never asks for ratings)."""
    values = dict(vars(raw))
    for name in ("wave", "units_wave", "rating"):
        values.pop(name, None)
    return SimpleNamespace(**values)


def forecast_bodies(raw):
    """Surfline `surf`, `swells` and `rating` response bodies carrying `raw`'s wave and rating data."""
    return {
        "surf": {
            "associated": {"units": {"waveHeight": raw.units_wave["waveHeight"]}},
            "data": {
                "surf": [
                    {
                        "timestamp": e["timestamp"],
                        "utcOffset": e["utcOffset"],
                        "surf": {"min": e["surf_min"], "max": e["surf_max"], "plus": False},
                    }
                    for e in raw.wave
                ]
            },
        },
        "swells": {
            "associated": {"units": {"swellHeight": raw.units_wave["swellHeight"]}},
            "data": {
                "swells": [
                    {"timestamp": e["timestamp"], "utcOffset": e["utcOffset"], "swells": copy.deepcopy(e["swells"])}
                    for e in raw.wave
                ]
            },
        },
        "rating": {"associated": {}, "data": {"rating": copy.deepcopy(raw.rating)}},
    }


def without(mapping, *keys):
    """A copy of `mapping` without `keys`."""
    result = copy.deepcopy(mapping)
    for key in keys:
        result.pop(key, None)
    return result


def with_(mapping, **changes):
    """A copy of `mapping` with `changes` applied."""
    result = copy.deepcopy(mapping)
    result.update(changes)
    return result


# ---------------------------------------------------------------------------
# Parsed and evaluated equivalents
# ---------------------------------------------------------------------------

OPTIONAL_NONE = dict(
    surf_rating=None,
    primary_swell=None,
    wind_speed=None,
    wind_gust=None,
    wind_direction=None,
    wind_direction_type=None,
    tide_height=None,
    temperature=None,
    weather_condition=None,
)


def make_slot(h=7, **overrides):
    fields = dict(
        timestamp=local_ts(h),
        utc_offset=-10.0,
        surf_min=2.4255,
        surf_max=3.3482,
        surf_rating=4,
        primary_swell=Swell(4.2, 11.0, 315.0),
        wind_speed=8.4,
        wind_gust=12.3,
        wind_direction=45.0,
        wind_direction_type="Offshore",
        tide_height=1.3,
        temperature=24.0,
        weather_condition="MOSTLY_CLEAR",
    )
    fields.update(overrides)
    return Slot(**fields)


def pipeline_slot(h, good):
    if good:
        return make_slot(h, surf_min=2.4255, surf_max=3.3482)
    return make_slot(h, surf_min=1.2, surf_max=1.9)


def sunset_slot(h, good):
    return Slot(
        timestamp=local_ts(h),
        utc_offset=-10.0,
        surf_min=2.5 if good else 1.9,
        surf_max=2.8,
        surf_rating=3,
        primary_swell=Swell(3.9, 10.0, 310.0),
        wind_speed=11.0,
        wind_gust=15.0,
        wind_direction=90.0,
        wind_direction_type="Cross-shore",
        tide_height=1.3,
        temperature=24.0,
        weather_condition="MOSTLY_CLEAR",
    )


def pipeline_forecast(good_hours=range(7, 12), daylight=REF_DAYLIGHT):
    good = set(good_hours)
    return ForecastData(PIPELINE, tuple(pipeline_slot(h, h in good) for h in range(24)), daylight)


def sunset_forecast(good_hours=SUNSET_GOOD_HOURS, daylight=REF_DAYLIGHT):
    good = set(good_hours)
    return ForecastData(SUNSET, tuple(sunset_slot(h, h in good) for h in range(24)), daylight)


def pipeline_evaluation():
    return SpotEvaluation(
        spot=PIPELINE,
        local_date=date(2026, 10, 7),
        current=pipeline_slot(7, True),
        window=tuple(pipeline_slot(h, 7 <= h <= 11) for h in range(7, 19)),
        good_slots=tuple(pipeline_slot(h, True) for h in range(7, 12)),
        passes_rules=True,
    )


def sunset_evaluation():
    return SpotEvaluation(
        spot=SUNSET,
        local_date=date(2026, 10, 7),
        current=sunset_slot(7, False),
        window=tuple(sunset_slot(h, h in SUNSET_GOOD_HOURS) for h in range(7, 19)),
        good_slots=tuple(sunset_slot(h, True) for h in SUNSET_GOOD_HOURS),
        passes_rules=True,
    )


def rocky_evaluation():
    return replace(pipeline_evaluation(), spot=ROCKY, good_slots=pipeline_evaluation().good_slots[:3])


REF_DECISION = Decision(
    (Recommendation(pipeline_evaluation(), 0.92), Recommendation(sunset_evaluation(), 0.81)),
    jev_used=True,
    jev_error=None,
)

# ---------------------------------------------------------------------------
# Jev data (spec 4.8 and 5.4)
# ---------------------------------------------------------------------------

SPEC_NOUL_TEMPLATE = (
    "Is the surf at {name} (the state.spots entry with ref \"{ref}\") good enough for the user "
    "to go surfing today? Every rule in state.rules is already met for at least "
    "state.min_good_hours of the hours in that entry's \"today\" list. Also weigh wind, tide, "
    "swell consistency, how many good hours remain, state.notes and the entry's notes. "
    "Answer yes only if a session there today is worth it."
)

SPEC_BEST_SPOT_INSTRUCTIONS = (
    "Which spot in state.spots should the user go surfing at today for the best session?"
)

REF_ANSWERS = {
    "good_spot_1": {"type": "noul", "noul": 0.92},
    "good_spot_2": {"type": "noul", "noul": 0.81},
    "best_spot": {
        "type": "choice",
        "choice": "spot_1",
        "confidence": 0.88,
        "probabilities": {"spot_1": 0.88, "spot_2": 0.12},
    },
}

REF_JEV_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": copy.deepcopy(REF_ANSWERS),
    "usage": {"input_tokens": 1450, "output_tokens": 20},
}

REF_QUESTIONS = {
    "good_spot_1": {
        "type": "noul",
        "instructions": (
            "Is the surf at Pipeline (the state.spots entry with ref \"spot_1\") good enough for the "
            "user to go surfing today? Every rule in state.rules is already met for at least "
            "state.min_good_hours of the hours in that entry's \"today\" list. Also weigh wind, tide, "
            "swell consistency, how many good hours remain, state.notes and the entry's notes. "
            "Answer yes only if a session there today is worth it."
        ),
    },
    "good_spot_2": {
        "type": "noul",
        "instructions": (
            "Is the surf at Sunset Beach (the state.spots entry with ref \"spot_2\") good enough for "
            "the user to go surfing today? Every rule in state.rules is already met for at least "
            "state.min_good_hours of the hours in that entry's \"today\" list. Also weigh wind, tide, "
            "swell consistency, how many good hours remain, state.notes and the entry's notes. "
            "Answer yes only if a session there today is worth it."
        ),
    },
    "best_spot": {
        "type": "choice",
        "instructions": "Which spot in state.spots should the user go surfing at today for the best session?",
        "criteria": {"spot_1": "Pipeline", "spot_2": "Sunset Beach"},
    },
}


def _pipeline_state_slot(h):
    good = 7 <= h <= 11
    return {
        "time": f"{h:02d}:00",
        "surf_min": 2.4 if good else 1.2,
        "surf_max": 3.3 if good else 1.9,
        "surf_rating": 4,
        "swell_height": 4.2,
        "swell_period": 11.0,
        "swell_direction": 315.0,
        "wind_speed": 8.4,
        "wind_gust": 12.3,
        "wind_direction": 45.0,
        "wind_direction_type": "Offshore",
        "tide_height": 1.3,
        "temperature": 24.0,
        "weather_condition": "MOSTLY_CLEAR",
        "passes_rules": good,
    }


def _sunset_state_slot(h):
    good = h in SUNSET_GOOD_HOURS
    return {
        "time": f"{h:02d}:00",
        "surf_min": 2.5 if good else 1.9,
        "surf_max": 2.8,
        "surf_rating": 3,
        "swell_height": 3.9,
        "swell_period": 10.0,
        "swell_direction": 310.0,
        "wind_speed": 11.0,
        "wind_gust": 15.0,
        "wind_direction": 90.0,
        "wind_direction_type": "Cross-shore",
        "tide_height": 1.3,
        "temperature": 24.0,
        "weather_condition": "MOSTLY_CLEAR",
        "passes_rules": good,
    }


REF_STATE = {
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
            "current": _pipeline_state_slot(7),
            "today": [_pipeline_state_slot(h) for h in range(7, 19)],
        },
        {
            "ref": "spot_2",
            "name": "Sunset Beach",
            "notes": "",
            "local_date": "2026-10-07",
            "good_hours": ["08:00", "09:00", "10:00", "15:00", "16:00"],
            "current": _sunset_state_slot(7),
            "today": [_sunset_state_slot(h) for h in range(7, 19)],
        },
    ],
}

# ---------------------------------------------------------------------------
# Expected texts (spec 4.9, 5.5)
# ---------------------------------------------------------------------------

PIPELINE_BLOCK_LINES = [
    "Pipeline - now (07:00 local)",
    "Surf: 2.4-3.3 ft",
    "Swell: 4.2 ft @ 11s from NW (315°)",
    "Wind: 8 kts Offshore (gusts 12 kts)",
    "Tide: 1.3 ft",
    "Weather: 24°C, Mostly clear",
    "Good hours today: 07:00-12:00",
]
PIPELINE_BLOCK = "\n".join(PIPELINE_BLOCK_LINES)

SUNSET_BLOCK_LINES = [
    "Sunset Beach - now (07:00 local)",
    "Surf: 1.9-2.8 ft",
    "Swell: 3.9 ft @ 10s from NW (310°)",
    "Wind: 11 kts Cross-shore (gusts 15 kts)",
    "Tide: 1.3 ft",
    "Weather: 24°C, Mostly clear",
    "Good hours today: 08:00-11:00, 15:00-17:00",
]
SUNSET_BLOCK = "\n".join(SUNSET_BLOCK_LINES)

RULES_ONLY_NOTE = "Note: Jev was unavailable, so this recommendation is based on your rules only."

# Spec 5.5, verbatim.
REF_MESSAGE = (
    "Surf alert 2026-10-07\n"
    "Go surf at Pipeline!\n"
    "\n"
    "Pipeline - now (07:00 local)\n"
    "Surf: 2.4-3.3 ft\n"
    "Swell: 4.2 ft @ 11s from NW (315°)\n"
    "Wind: 8 kts Offshore (gusts 12 kts)\n"
    "Tide: 1.3 ft\n"
    "Weather: 24°C, Mostly clear\n"
    "Good hours today: 07:00-12:00\n"
    "Jev confidence: 92%\n"
    "\n"
    "Also good: Sunset Beach - now (07:00 local)\n"
    "Surf: 1.9-2.8 ft\n"
    "Swell: 3.9 ft @ 10s from NW (310°)\n"
    "Wind: 11 kts Cross-shore (gusts 15 kts)\n"
    "Tide: 1.3 ft\n"
    "Weather: 24°C, Mostly clear\n"
    "Good hours today: 08:00-11:00, 15:00-17:00\n"
    "Jev confidence: 81%"
)

REF_MESSAGE_RULES_ONLY = (
    "Surf alert 2026-10-07\nGo surf at Pipeline!\n\n"
    + PIPELINE_BLOCK
    + "\n\nAlso good: "
    + SUNSET_BLOCK
    + "\n\n"
    + RULES_ONLY_NOTE
)

PIPELINE_ONLY_MESSAGE_90 = (
    "Surf alert 2026-10-07\nGo surf at Pipeline!\n\n" + PIPELINE_BLOCK + "\nJev confidence: 90%"
)

SUNSET_ONLY_MESSAGE_81 = (
    "Surf alert 2026-10-07\nGo surf at Sunset Beach!\n\n" + SUNSET_BLOCK + "\nJev confidence: 81%"
)

REF_CONDITIONS_OUTPUT = (
    PIPELINE_BLOCK
    + "\nRules: PASS (good hours today: 5, required: 1)\n\n"
    + SUNSET_BLOCK
    + "\nRules: PASS (good hours today: 5, required: 1)\n"
)

NO_GOOD_SPOTS_LINE = "No good spots today; no notification sent.\n"

TEST_MESSAGE_TEXT = "Surf alert test message: WhatsApp notifications are working."

PHONE_ERROR = "WHATSAPP_PHONE must be in international format, e.g. +34612345678"
