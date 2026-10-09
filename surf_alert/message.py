"""Plain-text formatting of conditions and of the WhatsApp message."""

from __future__ import annotations

from datetime import timedelta
from typing import Sequence

from surf_alert.models import Decision, Settings, Slot, SpotEvaluation

_COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)  # fmt: skip

RULES_ONLY_NOTE = "Note: Jev was unavailable, so this recommendation is based on your rules only."


def compass(degrees: float) -> str:
    index = int((degrees % 360) / 22.5 + 0.5) % 16
    return _COMPASS_POINTS[index]


def format_hours(slots: Sequence[Slot]) -> str:
    if len(slots) == 0:
        return "none"
    runs: list[list[Slot]] = []
    for slot in slots:
        if runs and slot.timestamp == runs[-1][-1].timestamp + 3600:
            runs[-1].append(slot)
        else:
            runs.append([slot])
    parts = []
    for run in runs:
        first, last = run[0], run[-1]
        end = last.local_datetime() + timedelta(hours=1)
        parts.append(f"{first.local_datetime():%H:%M}-{end:%H:%M}")
    return ", ".join(parts)


def _humanize(condition: str) -> str:
    return condition.replace("_", " ").capitalize()


def format_spot_block(
    evaluation: SpotEvaluation, settings: Settings, probability: float | None = None
) -> str:
    c = evaluation.current
    h = settings.height_unit
    w = settings.wind_unit
    t = f"°{settings.temperature_unit}"

    lines = [
        f"{evaluation.spot.name} - now ({c.local_datetime():%H:%M} local)",
        f"Surf: {c.surf_min:.1f}-{c.surf_max:.1f} {h}",
    ]
    s = c.primary_swell
    if s is not None:
        lines.append(
            f"Swell: {s.height:.1f} {h} @ {s.period:.0f}s from {compass(s.direction)} "
            f"({s.direction:.0f}°)"
        )
    if c.wind_speed is not None:
        wind = f"Wind: {c.wind_speed:.0f} {w}"
        if c.wind_direction_type is not None:
            wind += f" {c.wind_direction_type}"
        if c.wind_gust is not None:
            wind += f" (gusts {c.wind_gust:.0f} {w})"
        lines.append(wind)
    if c.tide_height is not None:
        lines.append(f"Tide: {c.tide_height:.1f} {h}")
    weather_parts = []
    if c.temperature is not None:
        weather_parts.append(f"{c.temperature:.0f}{t}")
    if c.weather_condition is not None:
        weather_parts.append(_humanize(c.weather_condition))
    if weather_parts:
        lines.append("Weather: " + ", ".join(weather_parts))
    lines.append(f"Good hours today: {format_hours(evaluation.good_slots)}")
    if probability is not None:
        lines.append(f"Jev confidence: {probability * 100:.0f}%")
    return "\n".join(lines)


def format_rules_status(evaluation: SpotEvaluation, settings: Settings) -> str:
    return (
        f"Rules: {'PASS' if evaluation.passes_rules else 'FAIL'} "
        f"(good hours today: {len(evaluation.good_slots)}, required: {settings.min_good_hours})"
    )


def format_message(decision: Decision, settings: Settings) -> str:
    recommendations = decision.recommendations
    if len(recommendations) == 0:
        raise ValueError("no recommendations")
    first = recommendations[0]
    limit = max(1, settings.max_spots_in_message)

    paragraphs = [
        f"Surf alert {first.evaluation.local_date.isoformat()}\n"
        f"Go surf at {first.evaluation.spot.name}!",
        format_spot_block(first.evaluation, settings, first.probability),
    ]
    for rec in recommendations[1:limit]:
        paragraphs.append(
            "Also good: " + format_spot_block(rec.evaluation, settings, rec.probability)
        )
    extra = recommendations[limit:]
    if extra:
        paragraphs.append("More good spots: " + ", ".join(r.evaluation.spot.name for r in extra))
    if not decision.jev_used:
        paragraphs.append(RULES_ONLY_NOTE)
    return "\n\n".join(paragraphs)
