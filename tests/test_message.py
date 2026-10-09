"""Message formatting (FR-12, AC-15.5, Output NFR): TC-124 to TC-136."""

from __future__ import annotations

from dataclasses import replace

import pytest

from surf_alert.message import (
    compass,
    format_hours,
    format_message,
    format_rules_status,
    format_spot_block,
)
from surf_alert.models import Decision, Recommendation, Settings, Spot, Swell
from tests.scenario import (
    OPTIONAL_NONE,
    PIPELINE_BLOCK,
    PIPELINE_BLOCK_LINES,
    REF_DECISION,
    REF_MESSAGE,
    REF_MESSAGE_RULES_ONLY,
    REF_SETTINGS,
    RULES_ONLY_NOTE,
    SUNSET_BLOCK,
    make_slot,
    pipeline_evaluation,
    sunset_evaluation,
)

A = pipeline_evaluation()
B = sunset_evaluation()
L1, L2, L3, L4, L5, L6, L7 = PIPELINE_BLOCK_LINES


def block_named(name):
    """PIPELINE_BLOCK with the spot name in line 1 replaced."""
    return PIPELINE_BLOCK.replace("Pipeline - now", f"{name} - now", 1)


@pytest.mark.parametrize(
    "degrees, expected",
    [
        (0, "N"),
        (11.24, "N"),
        (11.25, "NNE"),
        (22.5, "NNE"),
        (45, "NE"),
        (90, "E"),
        (180, "S"),
        (270, "W"),
        (310, "NW"),
        (315, "NW"),
        (348.74, "NNW"),
        (348.75, "N"),
        (359, "N"),
        (360, "N"),
        (-45, "NW"),
        (720, "N"),
    ],
)
def test_tc124_compass(degrees, expected):
    """TC-124: AC-12.6, 16-point compass with wrap-around."""
    assert compass(degrees) == expected


@pytest.mark.parametrize(
    "slots, expected",
    [
        ([make_slot(h) for h in (8, 9, 10, 15, 16)], "08:00-11:00, 15:00-17:00"),
        ([], "none"),
        ([make_slot(7)], "07:00-08:00"),
        ([make_slot(h) for h in range(7, 12)], "07:00-12:00"),
        ([make_slot(23)], "23:00-00:00"),
        ([make_slot(7), make_slot(9)], "07:00-08:00, 09:00-10:00"),
        ([make_slot(timestamp=1791331200 + 7200, utc_offset=5.5)], "07:30-08:30"),
    ],
    ids=["two_runs", "empty", "single", "one_run", "midnight_wrap", "gap", "fractional_offset"],
)
def test_tc125_format_hours(slots, expected):
    """TC-125: AC-12.5, consecutive hours are grouped into ranges."""
    assert format_hours(slots) == expected


def test_tc126_spot_blocks():
    """TC-126: spec 4.9, the reference spot blocks with and without probability."""
    assert format_spot_block(A, Settings()) == PIPELINE_BLOCK
    assert format_spot_block(A, Settings(), 0.92) == PIPELINE_BLOCK + "\nJev confidence: 92%"
    assert format_spot_block(B, Settings(), 0.81) == SUNSET_BLOCK + "\nJev confidence: 81%"


def test_tc127_unit_labels():
    """TC-127: height, wind and temperature labels follow the settings."""
    slot = make_slot(
        7,
        surf_min=0.74,
        surf_max=1.02,
        primary_swell=Swell(1.28, 11.0, 315.0),
        wind_speed=15.6,
        wind_gust=22.8,
        wind_direction_type="Onshore",
        tide_height=0.4,
        temperature=75.2,
        weather_condition="CLEAR",
    )
    block = format_spot_block(replace(A, current=slot), Settings("m", "kph", "F"))
    assert block == (
        "Pipeline - now (07:00 local)\n"
        "Surf: 0.7-1.0 m\n"
        "Swell: 1.3 m @ 11s from NW (315°)\n"
        "Wind: 16 kph Onshore (gusts 23 kph)\n"
        "Tide: 0.4 m\n"
        "Weather: 75°F, Clear\n"
        "Good hours today: 07:00-12:00"
    )

    mph_slot = make_slot(7, wind_speed=9.2, wind_gust=13.9)
    mph_block = format_spot_block(replace(A, current=mph_slot), Settings(wind_unit="mph"))
    assert mph_block.split("\n")[3] == "Wind: 9 mph Offshore (gusts 14 mph)"


TABLE_H = [
    ("no_tide", dict(tide_height=None), [L1, L2, L3, L4, L6, L7]),
    ("no_swell", dict(primary_swell=None), [L1, L2, L4, L5, L6, L7]),
    ("no_weather", dict(temperature=None, weather_condition=None), [L1, L2, L3, L4, L5, L7]),
    ("no_wind", dict(wind_speed=None), [L1, L2, L3, L5, L6, L7]),
    ("no_temperature", dict(temperature=None), [L1, L2, L3, L4, L5, "Weather: Mostly clear", L7]),
    ("no_condition", dict(weather_condition=None), [L1, L2, L3, L4, L5, "Weather: 24°C", L7]),
    ("night_clear", dict(weather_condition="NIGHT_CLEAR"),
     [L1, L2, L3, L4, L5, "Weather: 24°C, Night clear", L7]),
    ("no_direction_type", dict(wind_direction_type=None),
     [L1, L2, L3, "Wind: 8 kts (gusts 12 kts)", L5, L6, L7]),
    ("no_gust", dict(wind_gust=None), [L1, L2, L3, "Wind: 8 kts Offshore", L5, L6, L7]),
    ("no_type_no_gust", dict(wind_direction_type=None, wind_gust=None),
     [L1, L2, L3, "Wind: 8 kts", L5, L6, L7]),
    ("all_optional_none", dict(OPTIONAL_NONE), [L1, L2, L7]),
]


@pytest.mark.parametrize(
    "change, lines", [(c, l) for _, c, l in TABLE_H], ids=[n for n, _, _ in TABLE_H]
)
def test_tc128_optional_lines(change, lines):
    """TC-128: AC-12.4, table H, optional lines and parts are left out when values are None."""
    ev = replace(A, current=replace(A.current, **change))
    assert format_spot_block(ev, Settings()).split("\n") == lines


def test_tc128_no_good_hours():
    """TC-128: table H, no good slots gives 'Good hours today: none'."""
    ev = replace(A, good_slots=())
    assert format_spot_block(ev, Settings()).split("\n") == [L1, L2, L3, L4, L5, L6, "Good hours today: none"]


@pytest.mark.parametrize(
    "probability, line", [(0.0, "Jev confidence: 0%"), (1.0, "Jev confidence: 100%")]
)
def test_tc129_confidence_line(probability, line):
    """TC-129: line 8 shows the probability as a whole percentage."""
    assert format_spot_block(A, Settings(), probability).split("\n")[-1] == line


@pytest.mark.parametrize(
    "ev, settings, expected",
    [
        (A, Settings(), "Rules: PASS (good hours today: 5, required: 1)"),
        (replace(B, good_slots=(), passes_rules=False), Settings(), "Rules: FAIL (good hours today: 0, required: 1)"),
        (replace(A, passes_rules=False), Settings(min_good_hours=6), "Rules: FAIL (good hours today: 5, required: 6)"),
    ],
    ids=["pass", "fail_none", "fail_too_few"],
)
def test_tc130_rules_status(ev, settings, expected):
    """TC-130: AC-15.5, format_rules_status."""
    assert format_rules_status(ev, settings) == expected


def test_tc131_reference_message():
    """TC-131: AC-12.1, the reference decision gives the spec 5.5 message exactly."""
    assert REF_MESSAGE == (
        "Surf alert 2026-10-07\nGo surf at Pipeline!\n\n"
        + PIPELINE_BLOCK
        + "\nJev confidence: 92%\n\nAlso good: "
        + SUNSET_BLOCK
        + "\nJev confidence: 81%"
    )
    assert format_message(REF_DECISION, Settings()) == REF_MESSAGE
    assert format_message(REF_DECISION, REF_SETTINGS) == REF_MESSAGE


def test_tc132_rules_only_message():
    """TC-132: AC-12.3, without Jev there are no confidence lines and a final note."""
    decision = Decision((Recommendation(A, None), Recommendation(B, None)), False, "x")
    msg = format_message(decision, Settings())
    assert msg == REF_MESSAGE_RULES_ONLY
    assert "Jev confidence:" not in msg
    assert msg.endswith("\n\n" + RULES_ONLY_NOTE)


NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]


def _five(probabilities):
    return tuple(
        Recommendation(replace(A, spot=Spot(name, f"{i + 1:024x}")), p)
        for i, (name, p) in enumerate(zip(NAMES, probabilities))
    )


def test_tc133_more_spots_than_max():
    """TC-133: AC-12.2, extra recommendations are listed by name only."""
    msg = format_message(Decision(_five([0.9, 0.85, 0.8, 0.75, 0.72]), True, None), Settings())
    assert msg.split("\n\n") == [
        "Surf alert 2026-10-07\nGo surf at Alpha!",
        block_named("Alpha") + "\nJev confidence: 90%",
        "Also good: " + block_named("Bravo") + "\nJev confidence: 85%",
        "Also good: " + block_named("Charlie") + "\nJev confidence: 80%",
        "More good spots: Delta, Echo",
    ]

    rules_only = format_message(Decision(_five([None] * 5), False, "x"), Settings())
    assert rules_only.split("\n\n") == [
        "Surf alert 2026-10-07\nGo surf at Alpha!",
        block_named("Alpha"),
        "Also good: " + block_named("Bravo"),
        "Also good: " + block_named("Charlie"),
        "More good spots: Delta, Echo",
        RULES_ONLY_NOTE,
    ]


def test_tc134_paragraphs():
    """TC-134: paragraph structure for 3 spots, 1 spot, and max_spots_in_message=1."""
    three = format_message(Decision(_five([0.9, 0.85, 0.8])[:3], True, None), Settings())
    assert "More good spots" not in three
    assert len(three.split("\n\n")) == 4

    one = format_message(Decision((Recommendation(A, 0.9),), True, None), Settings())
    assert len(one.split("\n\n")) == 2

    limited = format_message(REF_DECISION, Settings(max_spots_in_message=1))
    assert limited == (
        "Surf alert 2026-10-07\nGo surf at Pipeline!\n\n"
        + PIPELINE_BLOCK
        + "\nJev confidence: 92%\n\nMore good spots: Sunset Beach"
    )


def test_tc135_no_recommendations():
    """TC-135: AC-12.7, an empty decision cannot be formatted."""
    with pytest.raises(ValueError) as ei:
        format_message(Decision((), True), Settings())
    assert str(ei.value) == "no recommendations"


def test_tc136_plain_text_output():
    """TC-136: Output NFR, the only non-ASCII character is the degree sign and there is no trailing whitespace."""
    messages = [
        format_message(REF_DECISION, Settings()),
        format_message(Decision((Recommendation(A, None), Recommendation(B, None)), False, "x"), Settings()),
    ]
    for msg in messages:
        assert {ch for ch in msg if ord(ch) > 127} == {"°"}
        assert msg == msg.rstrip()
