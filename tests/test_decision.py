"""Jev request building and decisions (FR-8, FR-10, FR-11): TC-105 to TC-123."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from surf_alert.decision import build_jev_request, candidate_ref, decide, interpret_jev_answers
from surf_alert.errors import JevError
from surf_alert.models import Decision, Recommendation, Rule, Settings, Swell
from tests.fakes import FakeJev
from tests.scenario import (
    OPTIONAL_NONE,
    REF_ANSWERS,
    REF_DECISION,
    REF_QUESTIONS,
    REF_RULES,
    REF_SETTINGS,
    REF_STATE,
    SPEC_NOUL_TEMPLATE,
    make_slot,
    pipeline_evaluation,
    rocky_evaluation,
    sunset_evaluation,
)

A = pipeline_evaluation()
B = sunset_evaluation()
C = rocky_evaluation()
_NO_BEST = object()

INVALID_1 = "malformed response: missing or invalid answer for good_spot_1"
INVALID_2 = "malformed response: missing or invalid answer for good_spot_2"


def ans(*probabilities, best=_NO_BEST):
    answers = {
        f"good_spot_{i}": {"type": "noul", "noul": p} for i, p in enumerate(probabilities, start=1)
    }
    if best is not _NO_BEST:
        answers["best_spot"] = {"type": "choice", "choice": best}
    return answers


def recs(*pairs):
    return tuple(Recommendation(ev, p) for ev, p in pairs)


def assert_same_key_order(actual, expected, path="state"):
    """Dict key order matches at every nesting level."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert list(actual) == list(expected), path
        for key in expected:
            assert_same_key_order(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), path
        assert len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_same_key_order(a, e, f"{path}[{i}]")


def test_tc105_candidate_ref():
    """TC-105: candidate refs are 1-based."""
    assert candidate_ref(1) == "spot_1"
    assert candidate_ref(12) == "spot_12"


def test_tc106_single_candidate():
    """TC-106: AC-8.1, one candidate gets one noul question and no best_spot."""
    state, questions = build_jev_request([B], REF_RULES, REF_SETTINGS)
    assert questions == {
        "good_spot_1": {
            "type": "noul",
            "instructions": SPEC_NOUL_TEMPLATE.format(name="Sunset Beach", ref="spot_1"),
        }
    }
    assert len(state["spots"]) == 1
    assert state["spots"][0]["ref"] == "spot_1"
    assert state["spots"][0]["name"] == "Sunset Beach"


def test_tc107_several_candidates():
    """TC-107: AC-8.2, noul questions in order, then best_spot with criteria."""
    _, questions = build_jev_request([A, B], REF_RULES, REF_SETTINGS)
    assert questions == REF_QUESTIONS
    assert list(questions) == ["good_spot_1", "good_spot_2", "best_spot"]
    assert_same_key_order(questions, REF_QUESTIONS, "questions")

    _, three = build_jev_request([A, B, C], REF_RULES, REF_SETTINGS)
    assert list(three) == ["good_spot_1", "good_spot_2", "good_spot_3", "best_spot"]
    assert three["best_spot"]["type"] == "choice"
    assert three["best_spot"]["criteria"] == {
        "spot_1": "Pipeline",
        "spot_2": "Sunset Beach",
        "spot_3": "Rocky Point",
    }
    assert three["good_spot_3"]["instructions"] == SPEC_NOUL_TEMPLATE.format(
        name="Rocky Point", ref="spot_3"
    )


def test_tc108_reference_state():
    """TC-108: AC-8.3, the state matches spec 5.4 exactly, key order included."""
    state, _ = build_jev_request([A, B], REF_RULES, REF_SETTINGS)
    assert state == REF_STATE
    assert_same_key_order(state, REF_STATE)
    json.dumps(state)


def test_tc109_rounding_none_and_units():
    """TC-109: AC-8.3, rounding to 1 decimal, None for missing values, unit labels from settings."""
    slot = make_slot(7, **{**OPTIONAL_NONE, "surf_min": 1.23456, "surf_max": 2.98765})
    ev = replace(A, current=slot, window=(slot,), good_slots=(slot,))
    settings = Settings(height_unit="m", wind_unit="kph", temperature_unit="F")
    state, _ = build_jev_request([ev], (Rule("wave_height_min", ">", 1.0),), settings)

    expected_slot = {
        "time": "07:00",
        "surf_min": 1.2,
        "surf_max": 3.0,
        "surf_rating": None,
        "swell_height": None,
        "swell_period": None,
        "swell_direction": None,
        "wind_speed": None,
        "wind_gust": None,
        "wind_direction": None,
        "wind_direction_type": None,
        "tide_height": None,
        "temperature": None,
        "weather_condition": None,
        "passes_rules": True,
    }
    expected = {
        "units": {"height": "m", "period": "s", "wind": "kph", "temperature": "F"},
        "rules": ["wave_height_min > 1 m"],
        "notes": "",
        "min_good_hours": 1,
        "spots": [
            {
                "ref": "spot_1",
                "name": "Pipeline",
                "notes": "Only surf it when it is under 6 ft.",
                "local_date": "2026-10-07",
                "good_hours": ["07:00"],
                "current": expected_slot,
                "today": [expected_slot],
            }
        ],
    }
    assert state == expected
    assert_same_key_order(state, expected)

    slot2 = make_slot(
        7,
        primary_swell=Swell(4.24, 11, 315.04),
        wind_speed=8.44,
        wind_gust=12.36,
        wind_direction=45.04,
        tide_height=1.26,
        temperature=23.96,
    )
    ev2 = replace(A, current=slot2, window=(slot2,), good_slots=(slot2,))
    state2, _ = build_jev_request([ev2], REF_RULES, Settings())
    current = state2["spots"][0]["current"]
    assert [
        current["swell_height"],
        current["swell_period"],
        current["swell_direction"],
        current["wind_speed"],
        current["wind_gust"],
        current["wind_direction"],
        current["tide_height"],
        current["temperature"],
    ] == [4.2, 11.0, 315.0, 8.4, 12.4, 45.0, 1.3, 24.0]


def test_tc110_no_candidates():
    """TC-110: AC-8.4, an empty candidate list is rejected."""
    with pytest.raises(ValueError) as ei:
        build_jev_request([], REF_RULES, Settings())
    assert str(ei.value) == "no candidate spots"


def test_tc111_reference_answers():
    """TC-111: AC-10.1, the reference answers give the reference decision."""
    assert interpret_jev_answers(copy.deepcopy(REF_ANSWERS), [A, B], Settings()) == REF_DECISION


def test_tc112_threshold_inclusive():
    """TC-112: AC-10.2, approval is probability >= jev_threshold."""
    assert interpret_jev_answers(ans(0.7, 0.69), [A, B], Settings()) == Decision(
        recs((A, 0.7)), True, None
    )
    assert interpret_jev_answers(ans(0.7, 0.69), [A, B], Settings(jev_threshold=0.5)) == Decision(
        recs((A, 0.7), (B, 0.69)), True, None
    )


@pytest.mark.parametrize(
    "answers, order",
    [
        (ans(0.75, 0.95, 0.85, best="spot_1"), [(A, 0.75), (B, 0.95), (C, 0.85)]),
        (ans(0.8, 0.9, 0.9, best="spot_1"), [(A, 0.8), (B, 0.9), (C, 0.9)]),
        (ans(0.9, 0.9, 0.8, best="spot_3"), [(C, 0.8), (A, 0.9), (B, 0.9)]),
        (ans(0.95, 0.75, 0.85, best="spot_2"), [(B, 0.75), (A, 0.95), (C, 0.85)]),
    ],
    ids=["best_lower_prob", "tie_config_order", "best_last", "best_middle"],
)
def test_tc113_best_spot_first(answers, order):
    """TC-113: AC-10.3, the chosen approved spot comes first, then by probability, ties by config order."""
    result = interpret_jev_answers(answers, [A, B, C], Settings())
    assert result == Decision(recs(*order), True, None)


def _with_best(probabilities, best_value):
    answers = ans(*probabilities)
    if best_value is not _NO_BEST:
        answers["best_spot"] = best_value
    return answers


@pytest.mark.parametrize(
    "answers, order",
    [
        (_with_best((0.8, 0.9, 0.75), _NO_BEST), [(B, 0.9), (A, 0.8), (C, 0.75)]),
        (_with_best((0.8, 0.9, 0.75), "spot_1"), [(B, 0.9), (A, 0.8), (C, 0.75)]),
        (_with_best((0.8, 0.9, 0.75), {"type": "choice"}), [(B, 0.9), (A, 0.8), (C, 0.75)]),
        (_with_best((0.8, 0.9, 0.75), {"choice": "spot_9"}), [(B, 0.9), (A, 0.8), (C, 0.75)]),
        (_with_best((0.8, 0.9, 0.75), {"choice": "Pipeline"}), [(B, 0.9), (A, 0.8), (C, 0.75)]),
        (_with_best((0.8, 0.9, 0.75), {"choice": None}), [(B, 0.9), (A, 0.8), (C, 0.75)]),
        (_with_best((0.8, 0.9, 0.6), {"choice": "spot_3"}), [(B, 0.9), (A, 0.8)]),
        (_with_best((0.9, 0.9, 0.9), _NO_BEST), [(A, 0.9), (B, 0.9), (C, 0.9)]),
    ],
    ids=[
        "missing",
        "not_a_dict",
        "no_choice",
        "unknown_ref",
        "name_not_ref",
        "choice_none",
        "choice_not_approved",
        "all_tied",
    ],
)
def test_tc114_best_spot_unusable(answers, order):
    """TC-114: AC-10.4, an unusable best_spot falls back to highest probability (not an error)."""
    result = interpret_jev_answers(answers, [A, B, C], Settings())
    assert result == Decision(recs(*order), True, None)


def test_tc115_one_candidate():
    """TC-115: spec 6.5, one candidate is first if approved."""
    assert interpret_jev_answers(ans(0.81), [B], Settings()) == Decision(recs((B, 0.81)), True, None)
    assert interpret_jev_answers(ans(0.5), [B], Settings()) == Decision((), True, None)


def test_tc116_nothing_approved():
    """TC-116: AC-10.5, no candidate reaches the threshold."""
    result = interpret_jev_answers(ans(0.5, 0.3, best="spot_1"), [A, B], Settings())
    assert result == Decision((), True, None)


def _bad_answers():
    cases = []

    a = ans(0.9, 0.8)
    del a["good_spot_2"]
    cases.append(("good_spot_2_missing", a, INVALID_2))

    cases.append(("empty_answers", {}, INVALID_1))

    a = ans(0.9, 0.8)
    a["good_spot_1"] = "yes"
    cases.append(("not_a_dict", a, INVALID_1))

    a = ans(0.9, 0.8)
    a["good_spot_1"] = {"type": "noul"}
    cases.append(("no_noul", a, INVALID_1))

    for name, value in [
        ("true", True),
        ("false", False),
        ("str", "0.9"),
        ("none", None),
        ("above_one", 1.5),
        ("negative", -0.01),
        ("nan", float("nan")),
    ]:
        a = ans(0.9, 0.8)
        a["good_spot_1"]["noul"] = value
        cases.append((f"noul_{name}", a, INVALID_1))

    a = ans(0.9, 2)
    cases.append(("good_spot_2_noul_2", a, INVALID_2))

    a = ans(1.5, "x")
    cases.append(("both_invalid", a, INVALID_1))
    return cases


BAD_ANSWERS = _bad_answers()


@pytest.mark.parametrize(
    "answers, msg", [(a, m) for _, a, m in BAD_ANSWERS], ids=[n for n, _, _ in BAD_ANSWERS]
)
def test_tc117_invalid_answers(answers, msg):
    """TC-117: AC-10.6, a missing or invalid good_spot answer raises JevError for the first bad index."""
    with pytest.raises(JevError) as ei:
        interpret_jev_answers(copy.deepcopy(answers), [A, B], Settings())
    assert str(ei.value) == msg
    assert ei.value.status_code is None


@pytest.mark.parametrize("answers", [ans(1, 0), ans(1.0, 0.0)], ids=["ints", "floats"])
def test_tc118_integer_noul(answers):
    """TC-118: AC-10.6, int 0 and 1 are valid and stored as float."""
    result = interpret_jev_answers(answers, [A, B], Settings())
    assert result == Decision(recs((A, 1.0)), True, None)
    assert isinstance(result.recommendations[0].probability, float)


def test_tc119_decide_with_jev():
    """TC-119: FR-11, decide builds the request, asks Jev once and interprets the answers."""
    jev = FakeJev(answers=REF_ANSWERS)
    assert decide([A, B], REF_RULES, REF_SETTINGS, jev) == REF_DECISION
    assert jev.calls == [(REF_STATE, REF_QUESTIONS)]


def test_tc120_rules_only_fallback():
    """TC-120: AC-11.1, on JevError every candidate is recommended, most good slots first."""
    jev = FakeJev(exc=JevError("rate limited (HTTP 429)", 429))
    result = decide([C, A, B], REF_RULES, Settings(), jev)
    assert result == Decision(
        recs((A, None), (B, None), (C, None)), False, "rate limited (HTTP 429)"
    )


INVALID_ANSWERS_JEV = {"good_spot_1": {"noul": "high"}, "good_spot_2": {"noul": 0.9}}


def test_tc121_fallback_on_invalid_answers():
    """TC-121: AC-11.1, a JevError from interpret_jev_answers also triggers the fallback."""
    jev = FakeJev(answers=INVALID_ANSWERS_JEV)
    result = decide([A, B], REF_RULES, Settings(), jev)
    assert result == Decision(recs((A, None), (B, None)), False, INVALID_1)


@pytest.mark.parametrize(
    "jev, msg",
    [
        (lambda: FakeJev(exc=JevError("rate limited (HTTP 429)", 429)), "rate limited (HTTP 429)"),
        (lambda: FakeJev(answers=INVALID_ANSWERS_JEV), INVALID_1),
    ],
    ids=["ask_error", "answer_error"],
)
def test_tc122_skip(jev, msg):
    """TC-122: AC-11.2, with on_jev_error 'skip' a JevError gives no recommendations."""
    result = decide([A, B], REF_RULES, Settings(on_jev_error="skip"), jev())
    assert result == Decision((), False, msg)


@pytest.mark.parametrize(
    "exc", [RuntimeError("boom"), ValueError("bad")], ids=["runtime", "value"]
)
def test_tc123_other_exceptions_propagate(exc):
    """TC-123: AC-11.3, exceptions other than JevError propagate out of decide."""
    with pytest.raises(type(exc)) as ei:
        decide([A, B], REF_RULES, Settings(), FakeJev(exc=exc))
    assert ei.value is exc


def test_tc123_no_candidates_no_call():
    """TC-123: spec 6.5, decide([]) raises before asking Jev."""
    jev = FakeJev(answers=REF_ANSWERS)
    with pytest.raises(ValueError) as ei:
        decide([], REF_RULES, Settings(), jev)
    assert str(ei.value) == "no candidate spots"
    assert jev.calls == []
