"""evaluate_spots and run_check (spec 4.11, FR-14; integration): TC-145 to TC-159."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from surf_alert.app import evaluate_spots, run_check
from surf_alert.errors import JevError, NotificationError, SurflineError
from surf_alert.message import format_message
from surf_alert.models import Decision, Recommendation, RunResult
from tests.fakes import FakeForecastSource, FakeJev, FakeNotifier
from tests.scenario import (
    NOW,
    PIPELINE,
    PIPELINE_ONLY_MESSAGE_90,
    REF_ANSWERS,
    REF_DECISION,
    REF_MESSAGE,
    REF_MESSAGE_RULES_ONLY,
    REF_QUESTIONS,
    REF_STATE,
    SUNSET,
    SUNSET_ONLY_MESSAGE_81,
    local_ts,
    pipeline_evaluation,
    pipeline_forecast,
    ref_app_config,
    sunset_evaluation,
    sunset_forecast,
    utc_dt,
)

A = pipeline_evaluation()
B = sunset_evaluation()
NO_WAVE = "no wave data returned for Pipeline"


def source(pipeline=None, sunset=None):
    return FakeForecastSource(
        {
            "Pipeline": pipeline if pipeline is not None else pipeline_forecast(),
            "Sunset Beach": sunset if sunset is not None else sunset_forecast(),
        }
    )


def noul(*probabilities):
    return {f"good_spot_{i}": {"type": "noul", "noul": p} for i, p in enumerate(probabilities, start=1)}


def check(cfg=None, src=None, jev=None, notifier=None, now=NOW, dry_run=False):
    cfg = cfg or ref_app_config()
    src = src or source()
    jev = jev or FakeJev(answers=REF_ANSWERS)
    if notifier is None and not dry_run:
        notifier = FakeNotifier()
    result = run_check(cfg, surfline=src, jev=jev, notifier=None if dry_run else notifier, now=now)
    return result, src, jev, notifier


def test_tc145_evaluate_spots():
    """TC-145: evaluate_spots returns (list of evaluations, dict of failures) in config order."""
    src = source()
    result = evaluate_spots(ref_app_config(), src, NOW)
    evaluations, failures = result
    assert type(evaluations) is list
    assert type(failures) is dict
    assert evaluations == [A, B]
    assert failures == {}
    assert src.fetched == [PIPELINE, SUNSET]


def test_tc146_evaluate_spots_subset():
    """TC-146: the spots parameter restricts which spots are fetched."""
    src = source()
    evaluations, failures = evaluate_spots(ref_app_config(), src, NOW, spots=[SUNSET])
    assert evaluations == [B]
    assert failures == {}
    assert src.fetched == [SUNSET]


def test_tc147_surfline_failures_recorded():
    """TC-147: spec 6.2, a SurflineError is recorded and the other spots still run."""
    src = source(pipeline=SurflineError(NO_WAVE))
    evaluations, failures = evaluate_spots(ref_app_config(), src, NOW)
    assert evaluations == [B]
    assert failures == {"Pipeline": NO_WAVE}
    assert src.fetched == [PIPELINE, SUNSET]

    both = source(
        pipeline=SurflineError(NO_WAVE),
        sunset=SurflineError("no wind data returned for Sunset Beach"),
    )
    evaluations, failures = evaluate_spots(ref_app_config(), both, NOW)
    assert evaluations == []
    assert failures == {"Pipeline": NO_WAVE, "Sunset Beach": "no wind data returned for Sunset Beach"}
    assert list(failures) == ["Pipeline", "Sunset Beach"]


def test_tc148_other_exceptions_propagate():
    """TC-148: spec 6.5, exceptions other than SurflineError propagate."""
    with pytest.raises(RuntimeError):
        evaluate_spots(ref_app_config(), source(pipeline=RuntimeError("bug")), NOW)


def test_tc149_no_candidates():
    """TC-149: AC-14.1, no candidates means no Jev call, no message and exit 0."""
    src = source(pipeline=pipeline_forecast(good_hours=()), sunset=sunset_forecast(good_hours=()))
    result, _, jev, notifier = check(src=src)
    assert jev.calls == []
    assert notifier.sent == []
    assert result.decision is None
    assert result.message is None
    assert result.sent is False
    assert result.send_error is None
    assert result.exit_code == 0
    assert len(result.evaluations) == 2


def test_tc150_single_candidate_sent():
    """TC-150: AC-14.2, one approved candidate is sent with format_message."""
    cfg = ref_app_config()
    src = source(sunset=sunset_forecast(good_hours=()))
    result, _, jev, notifier = check(cfg=cfg, src=src, jev=FakeJev(answers=noul(0.9)))
    assert notifier.sent == [result.message]
    assert result.message == format_message(result.decision, cfg.settings) == PIPELINE_ONLY_MESSAGE_90
    assert result.decision == Decision((Recommendation(A, 0.9),), True, None)
    assert result.sent is True
    assert result.exit_code == 0
    assert list(jev.calls[0][1]) == ["good_spot_1"]


def test_tc151_reference_run():
    """TC-151: the reference run, one Jev request and one WhatsApp message."""
    result, _, jev, notifier = check(jev=FakeJev(REF_ANSWERS))
    assert result == RunResult((A, B), {}, REF_DECISION, REF_MESSAGE, True, None, 0)
    assert jev.calls == [(REF_STATE, REF_QUESTIONS)]
    assert notifier.sent == [REF_MESSAGE]


def test_tc152_jev_rejects_everything():
    """TC-152: AC-14.3, nothing approved means nothing sent and exit 0."""
    result, _, _, notifier = check(jev=FakeJev(answers=noul(0.5, 0.3)))
    assert result.decision == Decision((), True, None)
    assert result.message is None
    assert result.sent is False
    assert result.exit_code == 0
    assert notifier.sent == []


def test_tc153_one_spot_fails():
    """TC-153: AC-14.4, a fetch failure still sends for the other spot, exit 3."""
    src = source(pipeline=SurflineError(NO_WAVE))
    result, _, _, notifier = check(src=src, jev=FakeJev(answers=noul(0.81)))
    assert notifier.sent == [SUNSET_ONLY_MESSAGE_81]
    assert result.failures == {"Pipeline": NO_WAVE}
    assert result.evaluations == (B,)
    assert result.exit_code == 3


def test_tc154_all_spots_fail():
    """TC-154: AC-14.5, every fetch fails: no Jev call, nothing sent, exit 3."""
    src = source(pipeline=SurflineError(NO_WAVE), sunset=SurflineError("no wave data returned for Sunset Beach"))
    result, _, jev, notifier = check(src=src)
    assert jev.calls == []
    assert notifier.sent == []
    assert result.evaluations == ()
    assert result.decision is None
    assert result.message is None
    assert result.exit_code == 3


def jev_529():
    return FakeJev(exc=JevError("service overloaded (HTTP 529)", 529))


def test_tc155_jev_fails_rules_only():
    """TC-155: AC-14.6, a Jev failure sends the rules-only message and exits 3."""
    result, _, _, notifier = check(jev=jev_529())
    assert result.message == REF_MESSAGE_RULES_ONLY == notifier.sent[0]
    assert result.decision == Decision(
        (Recommendation(A, None), Recommendation(B, None)), False, "service overloaded (HTTP 529)"
    )
    assert result.exit_code == 3


def test_tc156_jev_fails_skip():
    """TC-156: AC-11.2 in a run, on_jev_error 'skip' sends nothing and exits 3."""
    result, _, _, notifier = check(cfg=ref_app_config(on_jev_error="skip"), jev=jev_529())
    assert result.decision == Decision((), False, "service overloaded (HTTP 529)")
    assert result.message is None
    assert notifier.sent == []
    assert result.exit_code == 3


def test_tc157_send_fails():
    """TC-157: AC-14.7, a send failure gives exit 4, which beats 3."""
    error = "CallMeBot returned HTTP 500: x"

    result, _, _, _ = check(notifier=FakeNotifier(exc=NotificationError(error)))
    assert result.sent is False
    assert result.send_error == error
    assert result.message == REF_MESSAGE
    assert result.exit_code == 4

    with_failure, _, _, _ = check(
        src=source(pipeline=SurflineError(NO_WAVE)),
        jev=FakeJev(answers=noul(0.81)),
        notifier=FakeNotifier(exc=NotificationError(error)),
    )
    assert with_failure.send_error == error
    assert with_failure.exit_code == 4

    with_jev_error, _, _, _ = check(
        src=source(pipeline=SurflineError(NO_WAVE)),
        jev=jev_529(),
        notifier=FakeNotifier(exc=NotificationError(error)),
    )
    assert with_jev_error.send_error == error
    assert with_jev_error.exit_code == 4


def test_tc158_dry_run():
    """TC-158: with notifier None the message is built but not sent."""
    result, _, _, _ = check(dry_run=True)
    assert result.message == REF_MESSAGE
    assert result.sent is False
    assert result.send_error is None
    assert result.exit_code == 0

    degraded, _, _, _ = check(dry_run=True, jev=jev_529())
    assert degraded.exit_code == 3


def test_tc159_same_now_for_every_spot():
    """TC-159: AC-14.11, every spot is evaluated with the same `now`; a naive `now` raises."""
    result, _, _, _ = check(now=utc_dt(local_ts(11) + 1800))
    assert len(result.evaluations) == 2
    for ev in result.evaluations:
        assert ev.current.timestamp == local_ts(11)
        assert ev.local_date == date(2026, 10, 7)

    with pytest.raises(ValueError) as ei:
        check(now=datetime(2026, 10, 7, 21, 30))
    assert str(ei.value) == "now must be timezone-aware"
