"""Orchestration: evaluate every spot, ask Jev about the candidates, and notify."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from surf_alert.decision import decide
from surf_alert.errors import NotificationError, SurflineError
from surf_alert.evaluate import evaluate_spot
from surf_alert.jev import JevLike
from surf_alert.message import format_message
from surf_alert.models import AppConfig, Decision, RunResult, Spot, SpotEvaluation
from surf_alert.notify import Notifier
from surf_alert.surfline import ForecastSource

logger = logging.getLogger(__name__)


def evaluate_spots(
    config: AppConfig,
    surfline: ForecastSource,
    now: datetime,
    spots: Sequence[Spot] | None = None,
) -> tuple[list[SpotEvaluation], dict[str, str]]:
    if spots is None:
        spots = config.spots
    evaluations: list[SpotEvaluation] = []
    failures: dict[str, str] = {}
    for spot in spots:
        try:
            forecast = surfline.fetch(spot)
        except SurflineError as exc:
            logger.debug("fetch failed for %s: %s", spot.name, exc)
            failures[spot.name] = str(exc)
            continue
        evaluations.append(evaluate_spot(forecast, config.rules, config.settings, now))
    return evaluations, failures


def run_check(
    config: AppConfig,
    *,
    surfline: ForecastSource,
    jev: JevLike,
    notifier: Notifier | None,
    now: datetime,
) -> RunResult:
    evaluations, failures = evaluate_spots(config, surfline, now)
    candidates = [e for e in evaluations if e.passes_rules]
    logger.debug("%d candidate spot(s) out of %d evaluated", len(candidates), len(evaluations))

    decision: Decision | None = None
    message: str | None = None
    if candidates:
        decision = decide(candidates, config.rules, config.settings, jev)
        if decision.recommendations:
            message = format_message(decision, config.settings)

    sent = False
    send_error: str | None = None
    if message is not None and notifier is not None:
        try:
            notifier.send(message)
        except NotificationError as exc:
            send_error = str(exc)
        else:
            sent = True

    if send_error is not None:
        exit_code = 4
    elif failures or (decision is not None and decision.jev_error is not None):
        exit_code = 3
    else:
        exit_code = 0

    return RunResult(
        evaluations=tuple(evaluations),
        failures=failures,
        decision=decision,
        message=message,
        sent=sent,
        send_error=send_error,
        exit_code=exit_code,
    )
