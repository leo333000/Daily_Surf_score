"""Windowing, rule engine and describe_rule (FR-6, FR-7, FR-3): TC-75 to TC-95."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo

import pytest

from surf_alert.config import NUMERIC_OPS
from surf_alert.evaluate import (
    current_slot,
    describe_rule,
    evaluate_spot,
    evaluation_window,
    local_date_of,
    metric_value,
    rule_passes,
    slot_passes,
)
from surf_alert.models import DaylightWindow, ForecastData, Rule, Settings, SpotEvaluation, Swell
from tests.scenario import (
    DAWN,
    DUSK,
    NOW,
    OPTIONAL_NONE,
    PIPELINE,
    REF_DAYLIGHT,
    REF_RULES,
    local_ts,
    make_slot,
    pipeline_evaluation,
    pipeline_forecast,
    pipeline_slot,
    sunset_evaluation,
    sunset_forecast,
    utc_dt,
)

NEXT_DAY_WINDOW = DaylightWindow(date(2026, 10, 8), DAWN + 86400, DUSK + 86400)


def hours_of(slots):
    """Local hours (relative to 2026-10-07 at -10) of the given slots."""
    return [(s.timestamp - local_ts(0)) // 3600 for s in slots]


class _NoOffset(tzinfo):
    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


@pytest.mark.parametrize(
    "timestamp, offset, expected",
    [
        (1791392400, -10.0, date(2026, 10, 7)),
        (1791331200, -10.0, date(2026, 10, 6)),
        (1791367200, -10, date(2026, 10, 7)),
        (1791367199, -10, date(2026, 10, 6)),
        (1791331200 - 19800, 5.5, date(2026, 10, 7)),
        (1791331200 - 19801, 5.5, date(2026, 10, 6)),
    ],
)
def test_tc075_local_date_of(timestamp, offset, expected):
    """TC-75: local_date_of handles negative and fractional offsets."""
    assert local_date_of(timestamp, offset) == expected


SLOTS_16_17_18 = [
    make_slot(timestamp=1791388800),
    make_slot(timestamp=1791392400),
    make_slot(timestamp=1791396000),
]


@pytest.mark.parametrize(
    "now, index",
    [
        (datetime(2026, 10, 7, 17, 30, tzinfo=timezone.utc), 1),
        (datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc), 0),
        (datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc), 2),
        (datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc), 2),
    ],
    ids=["between", "before_first", "after_last", "exact"],
)
def test_tc076_current_slot(now, index):
    """TC-76: AC-6.1, the slot in effect now; the first before the start; the last after the end."""
    assert current_slot(SLOTS_16_17_18, now) == SLOTS_16_17_18[index]


def test_tc077_current_slot_other_timezone():
    """TC-77: AC-6.1, an aware `now` in another time zone is compared as an instant."""
    now = datetime(2026, 10, 7, 19, 30, tzinfo=timezone(timedelta(hours=2)))
    assert current_slot(SLOTS_16_17_18, now) == SLOTS_16_17_18[1]


def test_tc078_current_slot_errors():
    """TC-78: AC-6.2, empty slots and naive `now`; the timezone check runs first."""
    with pytest.raises(ValueError) as ei:
        current_slot([], NOW)
    assert str(ei.value) == "no slots"

    naive = datetime(2026, 10, 7, 17, 30)
    no_offset = datetime(2026, 10, 7, 17, 30, tzinfo=_NoOffset())
    for slots, now in [(SLOTS_16_17_18, naive), (SLOTS_16_17_18, no_offset), ([], naive)]:
        with pytest.raises(ValueError) as ei:
            current_slot(slots, now)
        assert str(ei.value) == "now must be timezone-aware"


def test_tc079_reference_window():
    """TC-79: AC-6.3, the reference window is 07:00 to 18:00 local (12 slots)."""
    forecast = pipeline_forecast()
    today, window = evaluation_window(forecast, NOW)
    assert today == date(2026, 10, 7)
    assert isinstance(window, tuple)
    assert [s.timestamp for s in window] == [local_ts(h) for h in range(7, 19)]
    assert window == forecast.slots[7:19]


def test_tc080_fallback_daylight():
    """TC-80: AC-6.4, without daylight data the window runs to 19:00 local (13 slots)."""
    today, window = evaluation_window(pipeline_forecast(daylight=()), NOW)
    assert today == date(2026, 10, 7)
    assert hours_of(window) == list(range(7, 20))


def test_tc081_daylight_choice():
    """TC-81: daylight for another date means fallback; the first matching window is used."""
    _, other_day = evaluation_window(pipeline_forecast(daylight=(NEXT_DAY_WINDOW,)), NOW)
    assert hours_of(other_day) == list(range(7, 20))

    two = (REF_DAYLIGHT[0], DaylightWindow(date(2026, 10, 7), local_ts(9), local_ts(10)))
    _, first_used = evaluation_window(pipeline_forecast(daylight=two), NOW)
    assert hours_of(first_used) == list(range(7, 19))


def test_tc082_daylight_bounds_inclusive():
    """TC-82: dawn <= timestamp <= dusk is inclusive at both ends."""
    daylight = (DaylightWindow(date(2026, 10, 7), local_ts(7), local_ts(10)),)
    _, window = evaluation_window(pipeline_forecast(daylight=daylight), utc_dt(local_ts(6) + 1800))
    assert hours_of(window) == [7, 8, 9, 10]


def test_tc083_fallback_bounds():
    """TC-83: the fallback window covers local start hours 6 to 19 inclusive."""
    _, window = evaluation_window(pipeline_forecast(daylight=()), utc_dt(local_ts(5) + 1800))
    assert hours_of(window) == list(range(6, 20))
    assert len(window) == 14


def test_tc084_after_dusk():
    """TC-84: AC-6.5, after dusk the window is empty."""
    assert evaluation_window(pipeline_forecast(), utc_dt(local_ts(19))) == (date(2026, 10, 7), ())


def test_tc085_today_only():
    """TC-85: AC-6.6, slots on another local date are never in the window."""
    slots = tuple(make_slot(h) for h in range(48))
    with_daylight = ForecastData(PIPELINE, slots, (REF_DAYLIGHT[0], NEXT_DAY_WINDOW))
    _, window = evaluation_window(with_daylight, NOW)
    assert hours_of(window) == list(range(7, 19))

    no_daylight = ForecastData(PIPELINE, slots, ())
    _, window = evaluation_window(no_daylight, NOW)
    assert hours_of(window) == list(range(7, 20))

    forecast = pipeline_forecast()
    early = utc_dt(1791363600)  # 23:00 local on 2026-10-06, before the first slot
    assert current_slot(forecast.slots, early) == forecast.slots[0]
    assert evaluation_window(forecast, early) == (date(2026, 10, 6), ())


@pytest.mark.parametrize(
    "metric, expected",
    [
        ("wave_height_min", 2.4255),
        ("wave_height_max", 3.3482),
        ("surf_rating", 4),
        ("swell_height", 4.2),
        ("swell_period", 11.0),
        ("wind_speed", 8.4),
        ("wind_gust", 12.3),
        ("wind_direction_type", "Offshore"),
        ("tide_height", 1.3),
        ("temperature", 24.0),
        ("weather_condition", "MOSTLY_CLEAR"),
    ],
)
def test_tc086_metric_value(metric, expected):
    """TC-86: spec 4.4, each metric maps to its slot value."""
    assert metric_value(make_slot(7), metric) == expected


def test_tc086_metric_value_no_swell_and_unknown():
    """TC-86: AC-7.6, swell metrics are None without a primary swell; unknown metrics raise."""
    slot = make_slot(primary_swell=None)
    assert metric_value(slot, "swell_height") is None
    assert metric_value(slot, "swell_period") is None
    with pytest.raises(ValueError) as ei:
        metric_value(make_slot(7), "nope")
    assert str(ei.value) == "unknown metric: nope"


PERIOD_7 = make_slot(primary_swell=Swell(4.2, 7.0, 315.0))


@pytest.mark.parametrize(
    "rule, slot, expected",
    [
        (Rule("swell_period", ">", 7.0), PERIOD_7, False),
        (Rule("swell_period", ">=", 7.0), PERIOD_7, True),
        (Rule("swell_period", "<", 7.0), PERIOD_7, False),
        (Rule("swell_period", "<=", 7.0), PERIOD_7, True),
        (Rule("swell_period", "==", 7.0), PERIOD_7, True),
        (Rule("swell_period", "!=", 7.0), PERIOD_7, False),
        (Rule("swell_period", ">", 6.5), PERIOD_7, True),
        (Rule("swell_period", "<", 7.5), PERIOD_7, True),
        (Rule("swell_period", "==", 7.5), PERIOD_7, False),
        (Rule("swell_period", "!=", 7.5), PERIOD_7, True),
        (Rule("surf_rating", ">=", 4.0), PERIOD_7, True),
        (Rule("wave_height_min", ">", 2.0), PERIOD_7, True),
    ],
    ids=lambda v: f"{v.metric}{v.op}{v.value}" if isinstance(v, Rule) else None,
)
def test_tc087_numeric_rules(rule, slot, expected):
    """TC-87: AC-7.1, numeric comparisons."""
    assert rule_passes(rule, slot) is expected


OFFSHORE = make_slot(wind_direction_type="Offshore")
CROSS = make_slot(wind_direction_type="Cross-shore")


@pytest.mark.parametrize(
    "rule, slot, expected",
    [
        (Rule("wind_direction_type", "==", "offshore"), OFFSHORE, True),
        (Rule("wind_direction_type", "==", "OFFSHORE"), OFFSHORE, True),
        (Rule("wind_direction_type", "==", "Onshore"), OFFSHORE, False),
        (Rule("wind_direction_type", "!=", "offshore"), OFFSHORE, False),
        (Rule("wind_direction_type", "!=", "onshore"), OFFSHORE, True),
        (Rule("wind_direction_type", "not_in", ("onshore",)), OFFSHORE, True),
        (Rule("wind_direction_type", "not_in", ("OFFSHORE", "onshore")), OFFSHORE, False),
        (Rule("wind_direction_type", "in", ("offshore", "cross-shore")), OFFSHORE, True),
        (Rule("wind_direction_type", "in", ("offshore", "cross-shore")), CROSS, True),
        (Rule("wind_direction_type", "in", ("Onshore",)), CROSS, False),
        (Rule("weather_condition", "==", "STRASSE"), make_slot(weather_condition="straße"), True),
        (Rule("weather_condition", "==", "mostly_clear"), make_slot(weather_condition="MOSTLY_CLEAR"), True),
    ],
    ids=[
        "eq_lower",
        "eq_upper",
        "eq_other",
        "ne_same",
        "ne_other",
        "not_in_other",
        "not_in_same",
        "in_offshore",
        "in_cross",
        "in_other",
        "casefold_sharp_s",
        "weather_lower",
    ],
)
def test_tc088_text_rules(rule, slot, expected):
    """TC-88: AC-7.2, text comparisons ignore case with str.casefold."""
    assert rule_passes(rule, slot) is expected


NONE_SLOT = make_slot(**OPTIONAL_NONE)
NULLABLE_NUMERIC = [
    "surf_rating",
    "swell_height",
    "swell_period",
    "wind_speed",
    "wind_gust",
    "tide_height",
    "temperature",
]
NONE_RULES = [Rule(m, op, 0.0) for m in NULLABLE_NUMERIC for op in NUMERIC_OPS] + [
    Rule(m, op, value)
    for m in ("wind_direction_type", "weather_condition")
    for op, value in (("==", "x"), ("!=", "x"), ("in", ("x",)), ("not_in", ("x",)))
]


@pytest.mark.parametrize("rule", NONE_RULES, ids=lambda r: f"{r.metric}{r.op}")
def test_tc089_none_value_always_fails(rule):
    """TC-89: AC-7.3, a None metric value fails every operator, including != and not_in."""
    assert rule_passes(rule, NONE_SLOT) is False


def test_tc090_slot_passes():
    """TC-90: AC-7.4, every rule must pass; an empty rule list passes."""
    assert slot_passes(REF_RULES, pipeline_slot(7, True)) is True
    assert slot_passes(REF_RULES, pipeline_slot(12, False)) is False
    assert slot_passes([], pipeline_slot(12, False)) is True
    mixed = [Rule("wave_height_min", ">", 2.0), Rule("swell_period", ">", 20.0)]
    assert slot_passes(mixed, pipeline_slot(7, True)) is False


def test_tc091_reference_evaluations():
    """TC-91: AC-7.5, the reference forecasts evaluate to the reference evaluations."""
    pipe = evaluate_spot(pipeline_forecast(), REF_RULES, Settings(), NOW)
    sun = evaluate_spot(sunset_forecast(), REF_RULES, Settings(), NOW)
    assert pipe == pipeline_evaluation()
    assert sun == sunset_evaluation()
    for ev in (pipe, sun):
        assert isinstance(ev.window, tuple)
        assert isinstance(ev.good_slots, tuple)


@pytest.mark.parametrize("min_hours, expected", [(5, True), (6, False)])
def test_tc092_min_good_hours(min_hours, expected):
    """TC-92: AC-7.5, passes_rules compares the good-slot count with min_good_hours."""
    ev = evaluate_spot(pipeline_forecast(), REF_RULES, Settings(min_good_hours=min_hours), NOW)
    assert len(ev.good_slots) == 5
    assert ev.passes_rules is expected


def test_tc093_after_dusk_and_naive_now():
    """TC-93: AC-6.5 and AC-6.2, empty window after dusk; naive now raises."""
    ev = evaluate_spot(pipeline_forecast(), REF_RULES, Settings(), utc_dt(local_ts(19)))
    assert ev == SpotEvaluation(PIPELINE, date(2026, 10, 7), pipeline_slot(19, False), (), (), False)
    with pytest.raises(ValueError) as ei:
        evaluate_spot(pipeline_forecast(), REF_RULES, Settings(), datetime(2026, 10, 7, 17, 30))
    assert str(ei.value) == "now must be timezone-aware"


@pytest.mark.parametrize(
    "rule, expected",
    [
        (Rule("wave_height_max", ">=", 2.5), "wave_height_max >= 2.5 ft"),
        (Rule("wind_speed", "<", 15.0), "wind_speed < 15 kts"),
        (Rule("temperature", ">=", 18.0), "temperature >= 18°C"),
        (Rule("surf_rating", ">=", 1.0), "surf_rating >= 1"),
        (Rule("wind_direction_type", "==", "Offshore"), "wind_direction_type == Offshore"),
        (
            Rule("wind_direction_type", "in", ("Offshore", "Cross-shore")),
            "wind_direction_type in [Offshore, Cross-shore]",
        ),
    ],
)
def test_tc094_describe_rule_defaults(rule, expected):
    """TC-94: AC-3.2, describe_rule with default settings."""
    assert describe_rule(rule, Settings()) == expected


@pytest.mark.parametrize(
    "rule, settings, expected",
    [
        (Rule("swell_height", ">", 1.0), Settings(height_unit="m"), "swell_height > 1 m"),
        (Rule("tide_height", "<=", 0.5), Settings(height_unit="m"), "tide_height <= 0.5 m"),
        (Rule("wind_gust", "<", 30.0), Settings(wind_unit="kph"), "wind_gust < 30 kph"),
        (Rule("wind_speed", "<=", 12.5), Settings(wind_unit="mph"), "wind_speed <= 12.5 mph"),
        (Rule("temperature", ">=", 64.0), Settings(temperature_unit="F"), "temperature >= 64°F"),
        (Rule("swell_period", ">", 7.0), Settings(), "swell_period > 7 s"),
        (Rule("wind_direction_type", "!=", "Onshore"), Settings(), "wind_direction_type != Onshore"),
        (
            Rule("weather_condition", "not_in", ("RAIN", "THUNDERSTORM")),
            Settings(),
            "weather_condition not_in [RAIN, THUNDERSTORM]",
        ),
    ],
)
def test_tc095_describe_rule_suffixes(rule, settings, expected):
    """TC-95: AC-3.3, unit suffixes follow the settings and the metric kind."""
    assert describe_rule(rule, settings) == expected

