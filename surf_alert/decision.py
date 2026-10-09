"""Build the Jev request from the rule candidates, and turn Jev's answers into a Decision."""

from __future__ import annotations

import logging
import math
from typing import Any, Sequence

from surf_alert.errors import JevError
from surf_alert.evaluate import describe_rule, slot_passes
from surf_alert.jev import JevLike
from surf_alert.models import Decision, Recommendation, Rule, Settings, Slot, SpotEvaluation

logger = logging.getLogger(__name__)

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


def candidate_ref(index: int) -> str:
    """1-based: candidate_ref(1) == "spot_1"."""
    return f"spot_{index}"


def _round1(value: float | None) -> float | None:
    return None if value is None else round(float(value), 1)


def _slot_state(slot: Slot, rules: Sequence[Rule]) -> dict:
    swell = slot.primary_swell
    return {
        "time": f"{slot.local_datetime():%H:%M}",
        "surf_min": _round1(slot.surf_min),
        "surf_max": _round1(slot.surf_max),
        "surf_rating": slot.surf_rating,
        "swell_height": _round1(swell.height) if swell is not None else None,
        "swell_period": _round1(swell.period) if swell is not None else None,
        "swell_direction": _round1(swell.direction) if swell is not None else None,
        "wind_speed": _round1(slot.wind_speed),
        "wind_gust": _round1(slot.wind_gust),
        "wind_direction": _round1(slot.wind_direction),
        "wind_direction_type": slot.wind_direction_type,
        "tide_height": _round1(slot.tide_height),
        "temperature": _round1(slot.temperature),
        "weather_condition": slot.weather_condition,
        "passes_rules": slot_passes(rules, slot),
    }


def build_jev_request(
    candidates: Sequence[SpotEvaluation], rules: Sequence[Rule], settings: Settings
) -> tuple[dict, dict[str, dict]]:
    if len(candidates) == 0:
        raise ValueError("no candidate spots")

    spots = []
    for i, ev in enumerate(candidates, start=1):
        spots.append(
            {
                "ref": candidate_ref(i),
                "name": ev.spot.name,
                "notes": ev.spot.notes,
                "local_date": ev.local_date.isoformat(),
                "good_hours": [f"{s.local_datetime():%H:%M}" for s in ev.good_slots],
                "current": _slot_state(ev.current, rules),
                "today": [_slot_state(s, rules) for s in ev.window],
            }
        )
    state = {
        "units": {
            "height": settings.height_unit,
            "period": "s",
            "wind": settings.wind_unit,
            "temperature": settings.temperature_unit,
        },
        "rules": [describe_rule(r, settings) for r in rules],
        "notes": settings.notes,
        "min_good_hours": settings.min_good_hours,
        "spots": spots,
    }

    questions: dict[str, dict] = {}
    for i, ev in enumerate(candidates, start=1):
        questions[f"good_spot_{i}"] = {
            "type": "noul",
            "instructions": NOUL_TEMPLATE.format(name=ev.spot.name, ref=candidate_ref(i)),
        }
    if len(candidates) >= 2:
        questions["best_spot"] = {
            "type": "choice",
            "instructions": BEST_SPOT_INSTRUCTIONS,
            "criteria": {candidate_ref(i): ev.spot.name for i, ev in enumerate(candidates, start=1)},
        }
    return state, questions


def _valid_probability(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and not math.isnan(value)
        and 0 <= value <= 1
    )


def interpret_jev_answers(
    answers: dict, candidates: Sequence[SpotEvaluation], settings: Settings
) -> Decision:
    if not isinstance(answers, dict):
        answers = {}

    probabilities: list[float] = []
    for i in range(1, len(candidates) + 1):
        answer = answers.get(f"good_spot_{i}")
        noul = answer.get("noul") if isinstance(answer, dict) else None
        if not _valid_probability(noul):
            raise JevError(f"malformed response: missing or invalid answer for good_spot_{i}")
        probabilities.append(float(noul))

    # (position, evaluation, probability) for every approved candidate.
    approved = [
        (pos, ev, p)
        for pos, (ev, p) in enumerate(zip(candidates, probabilities))
        if p >= settings.jev_threshold
    ]
    if not approved:
        return Decision(recommendations=(), jev_used=True, jev_error=None)

    ordered = sorted(approved, key=lambda item: (-item[2], item[0]))

    best_position: int | None = None
    best = answers.get("best_spot")
    if isinstance(best, dict):
        choice = best.get("choice")
        if isinstance(choice, str):
            refs = {candidate_ref(i): i - 1 for i in range(1, len(candidates) + 1)}
            best_position = refs.get(choice)
    if best_position is not None:
        for index, item in enumerate(ordered):
            if item[0] == best_position:
                ordered.insert(0, ordered.pop(index))
                break

    return Decision(
        recommendations=tuple(Recommendation(ev, p) for _, ev, p in ordered),
        jev_used=True,
        jev_error=None,
    )


def decide(
    candidates: Sequence[SpotEvaluation],
    rules: Sequence[Rule],
    settings: Settings,
    jev: JevLike,
) -> Decision:
    state, questions = build_jev_request(candidates, rules, settings)
    try:
        answers = jev.ask(state, questions)
        return interpret_jev_answers(answers, candidates, settings)
    except JevError as exc:
        error = str(exc)
        logger.debug("Jev failed (%s); on_jev_error=%s", error, settings.on_jev_error)
        if settings.on_jev_error == "skip":
            return Decision(recommendations=(), jev_used=False, jev_error=error)
        ordered = sorted(
            enumerate(candidates), key=lambda item: (-len(item[1].good_slots), item[0])
        )
        return Decision(
            recommendations=tuple(Recommendation(ev, None) for _, ev in ordered),
            jev_used=False,
            jev_error=error,
        )
