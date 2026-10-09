"""Verbose report and spot table (spec 4.14): TC-219 to TC-222."""

from __future__ import annotations

import csv
from dataclasses import replace

import pytest

from surf_alert.errors import JevError
from surf_alert.models import Decision, Recommendation
from surf_alert.report import RecordingJev, build_spot_reports, format_report, write_csv
from tests.fakes import FakeJev
from tests.scenario import (
    REF_ANSWERS,
    REF_DECISION,
    REF_QUESTIONS,
    REF_STATE,
    pipeline_evaluation,
    ref_app_config,
    sunset_evaluation,
)

ANSWERS_REJECTING_SUNSET = {
    **REF_ANSWERS,
    "good_spot_2": {"type": "noul", "noul": 0.55},
}
PIPELINE_ONLY = Decision((Recommendation(pipeline_evaluation(), 0.92),), jev_used=True, jev_error=None)


def recorded(answers):
    jev = RecordingJev(FakeJev(answers=answers))
    jev.ask(REF_STATE, REF_QUESTIONS)
    return jev


def reports_for(decision, jev, evaluations=None, failures=None, config=None):
    return build_spot_reports(
        config or ref_app_config(),
        [pipeline_evaluation(), sunset_evaluation()] if evaluations is None else evaluations,
        failures or {},
        decision,
        jev,
    )


# ---------------------------------------------------------------------------
# RecordingJev
# ---------------------------------------------------------------------------


def test_tc219_passes_through_and_records():
    """TC-219: the wrapper forwards the request once and keeps the state and answers."""
    inner = FakeJev(answers=REF_ANSWERS)
    jev = RecordingJev(inner)
    assert jev.ask(REF_STATE, REF_QUESTIONS) == REF_ANSWERS
    assert inner.calls == [(REF_STATE, REF_QUESTIONS)]
    assert jev.state == REF_STATE
    assert jev.answers == REF_ANSWERS
    assert jev.confidences() == {"Pipeline": 0.92, "Sunset Beach": 0.81}
    assert jev.best_spot() == ("Pipeline", {"Pipeline": 0.88, "Sunset Beach": 0.12})


def test_tc219_error_reraised_and_answers_cleared():
    """TC-219: an exception from Jev propagates; no answers are kept."""
    jev = RecordingJev(FakeJev(exc=JevError("rate limited (HTTP 429)")))
    with pytest.raises(JevError):
        jev.ask(REF_STATE, REF_QUESTIONS)
    assert jev.state == REF_STATE
    assert jev.answers is None
    assert jev.confidences() == {}
    assert jev.best_spot() == (None, {})


@pytest.mark.parametrize(
    "answers, confidences, best",
    [
        ({}, {}, (None, {})),
        ("junk", {}, (None, {})),
        ({"good_spot_1": {"noul": True}, "good_spot_2": {"noul": 1.5}}, {}, (None, {})),
        ({"good_spot_1": {"noul": 1}, "good_spot_2": "x"}, {"Pipeline": 1.0}, (None, {})),
        (
            {"best_spot": {"choice": "spot_9", "probabilities": {"spot_1": 0.7, "spot_9": 0.3, "spot_2": "x"}}},
            {},
            (None, {"Pipeline": 0.7}),
        ),
    ],
    ids=["empty", "not_a_dict", "invalid_values", "partial", "unknown_refs"],
)
def test_tc219_malformed_answers(answers, confidences, best):
    """TC-219: only valid probabilities for known spots are reported."""
    jev = recorded(answers)
    assert jev.confidences() == confidences
    assert jev.best_spot() == best


# ---------------------------------------------------------------------------
# build_spot_reports
# ---------------------------------------------------------------------------


def test_tc220_reference_reports():
    """TC-220: per-rule hours, Jev scores, ranks and outcomes for the reference run."""
    pipe, sun = reports_for(REF_DECISION, recorded(REF_ANSWERS))
    assert (pipe.name, pipe.rule_hours, pipe.sent_to_jev, pipe.rank) == ("Pipeline", (5, 12), True, 1)
    assert (pipe.jev_confidence, pipe.best_spot_probability) == (0.92, 0.88)
    assert pipe.outcome == "recommended #1: Jev confidence 0.92 >= threshold 0.70"
    assert (sun.rule_hours, sun.rank, sun.jev_confidence, sun.best_spot_probability) == ((5, 12), 2, 0.81, 0.12)
    assert sun.outcome == "recommended #2: Jev confidence 0.81 >= threshold 0.70"


def test_tc220_rejected_by_jev_keeps_confidence():
    """TC-220: a spot Jev rejects still shows its confidence."""
    _, sun = reports_for(PIPELINE_ONLY, recorded(ANSWERS_REJECTING_SUNSET))
    assert (sun.rank, sun.jev_confidence, sun.sent_to_jev) == (None, 0.55, True)
    assert sun.outcome == "rejected by Jev: confidence 0.55 < threshold 0.70"


@pytest.mark.parametrize(
    "decision, settings, outcome",
    [
        (
            Decision((Recommendation(sunset_evaluation(), None),), jev_used=False, jev_error="service overloaded (HTTP 529)"),
            {},
            "recommended #1 by rules only: Jev failed (service overloaded (HTTP 529))",
        ),
        (
            Decision((), jev_used=False, jev_error="service overloaded (HTTP 529)"),
            {"on_jev_error": "skip"},
            "not recommended: Jev failed (service overloaded (HTTP 529)) and on_jev_error is skip",
        ),
        (None, {}, "passes rules (Jev not asked)"),
    ],
    ids=["rules_only", "skip", "jev_not_asked"],
)
def test_tc220_jev_failure_and_not_asked(decision, settings, outcome):
    """TC-220: outcomes when Jev failed or was not asked; no confidence is shown."""
    (sun,) = reports_for(decision, None, evaluations=[sunset_evaluation()], config=ref_app_config(**settings))
    assert sun.outcome == outcome
    assert sun.jev_confidence is None
    assert sun.sent_to_jev is (decision is not None)


def test_tc220_rule_failures_fetch_failures_and_order():
    """TC-220: failed rules, an empty window and a failed fetch; reports follow config order."""
    failing = replace(sunset_evaluation(), good_slots=(), passes_rules=False)
    no_window = replace(pipeline_evaluation(), window=(), good_slots=(), passes_rules=False)
    reports = reports_for(None, None, evaluations=[failing, no_window])
    assert [r.name for r in reports] == ["Pipeline", "Sunset Beach"]
    assert reports[0].outcome == "not checked: no daylight hours left today"
    assert reports[0].rule_hours == (0, 0)
    assert reports[1].outcome == "failed rules: 0 good hour(s) today, needs 1"
    assert reports[1].sent_to_jev is False

    (pipe, sun) = reports_for(None, None, evaluations=[sunset_evaluation()], failures={"Pipeline": "boom"})
    assert (pipe.evaluation, pipe.error, pipe.rule_hours) == (None, "boom", ())
    assert pipe.outcome == "skipped: boom"
    assert sun.name == "Sunset Beach"


def test_tc220_unevaluated_spots_left_out():
    """TC-220: spots that were neither evaluated nor failed (conditions --spot) get no report."""
    reports = reports_for(None, None, evaluations=[sunset_evaluation()])
    assert [r.name for r in reports] == ["Sunset Beach"]


# ---------------------------------------------------------------------------
# format_report and write_csv
# ---------------------------------------------------------------------------

REJECTED_REPORT = """\
Verbose report
Rules (all must pass in the same hour; a spot needs 1 such hour(s) today):
  1. wave_height_min > 2 ft
  2. swell_period > 7 s

Pipeline: 2026-10-07 local, 12 daylight hour(s) left (07:00-19:00)
  now (07:00): surf 2.4-3.3 ft, Surfline rating 4, swell 4.2 ft @ 11s, wind 8 kts Offshore
  Surfline rating today: best 4, lowest 4
  rule 1 (wave_height_min > 2 ft): 5/12 hours
  rule 2 (swell_period > 7 s): 12/12 hours
  all rules: 5 hour(s) (07:00-12:00), needs 1 -> PASS
  Jev confidence 0.92 (threshold 0.70), best-spot probability 0.88
  outcome: recommended #1: Jev confidence 0.92 >= threshold 0.70

Sunset Beach: 2026-10-07 local, 12 daylight hour(s) left (07:00-19:00)
  now (07:00): surf 1.9-2.8 ft, Surfline rating 3, swell 3.9 ft @ 10s, wind 11 kts Cross-shore
  Surfline rating today: best 3, lowest 3
  rule 1 (wave_height_min > 2 ft): 5/12 hours
  rule 2 (swell_period > 7 s): 12/12 hours
  all rules: 5 hour(s) (08:00-11:00, 15:00-17:00), needs 1 -> PASS
  Jev confidence 0.55 (threshold 0.70), best-spot probability 0.12
  outcome: rejected by Jev: confidence 0.55 < threshold 0.70

Jev (jev-latest): asked about 2 spot(s).
Jev's best spot: Pipeline (probability 0.88)
Result: recommended Pipeline."""


def test_tc221_format_report():
    """TC-221: the -v explanation for a run where Jev rejects one spot."""
    jev = recorded(ANSWERS_REJECTING_SUNSET)
    reports = reports_for(PIPELINE_ONLY, jev)
    assert format_report(ref_app_config(), reports, PIPELINE_ONLY, jev) == REJECTED_REPORT


@pytest.mark.parametrize(
    "decision, tail",
    [
        (None, "Jev: not asked (no spot passed the rules, or this command does not ask Jev)."),
        (
            Decision((), jev_used=False, jev_error="request failed: boom"),
            "Jev failed: request failed: boom; on_jev_error = rules_only.\nResult: no spot recommended, so no message.",
        ),
    ],
    ids=["not_asked", "jev_failed"],
)
def test_tc221_format_report_endings(decision, tail):
    """TC-221: the closing lines when Jev was not asked or failed; a failed fetch is explained."""
    reports = reports_for(decision, None, evaluations=[], failures={"Pipeline": "boom"})
    text = format_report(ref_app_config(), reports, decision, None)
    assert "\n\nPipeline: fetch failed: boom\n  outcome: skipped: boom\n\n" in text
    assert text.endswith(tail)


def test_tc222_write_csv(tmp_path):
    """TC-222: one row per spot, decision columns first, rule columns in config order."""
    config = ref_app_config()
    jev = recorded(ANSWERS_REJECTING_SUNSET)
    reports = reports_for(PIPELINE_ONLY, jev) + build_spot_reports(
        replace(config, spots=(config.spots[0],)), [], {"Pipeline": "boom"}, None, None
    )
    path = tmp_path / "out.csv"
    path.write_text("old content", encoding="utf-8")
    assert write_csv(str(path), config, reports) == str(path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "spot", "outcome", "rank", "jev_confidence", "best_spot_probability", "jev_threshold",
        "passes_rules", "good_hours", "required_hours", "surf_rating_now", "surf_rating_best_today",
        "rule 1: wave_height_min > 2 ft", "rule 2: swell_period > 7 s",
        "window_hours", "good_hour_ranges", "local_date", "now_time",
        "now_surf_min_ft", "now_surf_max_ft", "now_swell_height_ft", "now_swell_period_s",
        "now_wind_speed_kts", "now_wind_direction_type", "sent_to_jev", "spot_id", "error",
    ]  # fmt: skip
    assert rows[1] == [
        "Pipeline", "recommended #1: Jev confidence 0.92 >= threshold 0.70", "1", "0.92", "0.88", "0.70",
        "yes", "5", "1", "4", "4", "5", "12", "12", "07:00-12:00", "2026-10-07", "07:00",
        "2.4", "3.3", "4.2", "11", "8", "Offshore", "yes", "5842041f4e65fad6a7708890", "",
    ]  # fmt: skip
    assert rows[2] == [
        "Sunset Beach", "rejected by Jev: confidence 0.55 < threshold 0.70", "", "0.55", "0.12", "0.70",
        "yes", "5", "1", "3", "3", "5", "12", "12", "08:00-11:00, 15:00-17:00", "2026-10-07", "07:00",
        "1.9", "2.8", "3.9", "10", "11", "Cross-shore", "yes", "0123456789abcdef01234567", "",
    ]  # fmt: skip
    assert rows[3] == ["Pipeline", "skipped: boom", "", "", "", "0.70"] + [""] * 17 + [
        "no", "5842041f4e65fad6a7708890", "boom",
    ]
    assert len(rows) == 4


def test_tc221_no_daylight_left_is_one_line():
    """TC-221: a spot with no daylight hours left gets no per-rule lines."""
    no_window = replace(pipeline_evaluation(), window=(), good_slots=(), passes_rules=False)
    reports = reports_for(None, None, evaluations=[no_window])
    text = format_report(ref_app_config(), reports, None, None)
    assert (
        "Pipeline: 2026-10-07 local, 0 daylight hour(s) left (none)\n"
        "  now (07:00): surf 2.4-3.3 ft, Surfline rating 4, swell 4.2 ft @ 11s, wind 8 kts Offshore\n"
        "  outcome: not checked: no daylight hours left today\n"
    ) in text
    assert "rule 1" not in text.split("Pipeline:")[1]
