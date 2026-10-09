"""Today's daylight window, the current slot, and the deterministic rule engine."""

from __future__ import annotations

import logging
import operator
from datetime import date, datetime, timezone
from typing import Sequence

from surf_alert.config import METRICS
from surf_alert.models import ForecastData, Rule, Settings, Slot, SpotEvaluation

logger = logging.getLogger(__name__)

FALLBACK_DAYLIGHT_HOURS: tuple[int, int] = (6, 19)  # inclusive local start hours

_NUMERIC_COMPARE = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

_SUFFIXES = {
    "height": lambda s: f" {s.height_unit}",
    "period": lambda s: " s",
    "speed": lambda s: f" {s.wind_unit}",
    "temperature": lambda s: f"°{s.temperature_unit}",
    "score": lambda s: "",
}


def local_date_of(timestamp: int, utc_offset: float) -> date:
    return datetime.fromtimestamp(timestamp + round(utc_offset * 3600), tz=timezone.utc).date()


def current_slot(slots: Sequence[Slot], now: datetime) -> Slot:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if len(slots) == 0:
        raise ValueError("no slots")
    now_ts = now.timestamp()
    best: Slot | None = None
    for slot in slots:
        if slot.timestamp <= now_ts and (best is None or slot.timestamp > best.timestamp):
            best = slot
    return best if best is not None else slots[0]


def evaluation_window(forecast: ForecastData, now: datetime) -> tuple[date, tuple[Slot, ...]]:
    cur = current_slot(forecast.slots, now)
    today = local_date_of(int(now.timestamp()), cur.utc_offset)
    daylight = next((d for d in forecast.daylight if d.local_date == today), None)

    def in_daylight(slot: Slot) -> bool:
        if daylight is not None:
            return daylight.dawn <= slot.timestamp <= daylight.dusk
        first_hour, last_hour = FALLBACK_DAYLIGHT_HOURS
        return first_hour <= slot.local_datetime().hour <= last_hour

    window = [
        s
        for s in forecast.slots
        if local_date_of(s.timestamp, s.utc_offset) == today
        and s.timestamp >= cur.timestamp
        and in_daylight(s)
    ]
    window.sort(key=lambda s: s.timestamp)
    return today, tuple(window)


def metric_value(slot: Slot, metric: str) -> float | int | str | None:
    if metric == "wave_height_min":
        return slot.surf_min
    if metric == "wave_height_max":
        return slot.surf_max
    if metric == "surf_rating":
        return slot.surf_rating
    if metric == "swell_height":
        return slot.primary_swell.height if slot.primary_swell is not None else None
    if metric == "swell_period":
        return slot.primary_swell.period if slot.primary_swell is not None else None
    if metric == "wind_speed":
        return slot.wind_speed
    if metric == "wind_gust":
        return slot.wind_gust
    if metric == "wind_direction_type":
        return slot.wind_direction_type
    if metric == "tide_height":
        return slot.tide_height
    if metric == "temperature":
        return slot.temperature
    if metric == "weather_condition":
        return slot.weather_condition
    raise ValueError(f"unknown metric: {metric}")


def rule_passes(rule: Rule, slot: Slot) -> bool:
    value = metric_value(slot, rule.metric)
    if value is None:
        return False
    if METRICS[rule.metric] == "text":
        text = str(value).casefold()
        if rule.op == "==":
            return text == str(rule.value).casefold()
        if rule.op == "!=":
            return text != str(rule.value).casefold()
        if rule.op == "in":
            return text in {str(v).casefold() for v in rule.value}
        if rule.op == "not_in":
            return text not in {str(v).casefold() for v in rule.value}
    elif rule.op in _NUMERIC_COMPARE:
        return _NUMERIC_COMPARE[rule.op](value, float(rule.value))
    raise ValueError(f"op '{rule.op}' is not valid for metric '{rule.metric}'")


def slot_passes(rules: Sequence[Rule], slot: Slot) -> bool:
    return all(rule_passes(rule, slot) for rule in rules)


def evaluate_spot(
    forecast: ForecastData, rules: Sequence[Rule], settings: Settings, now: datetime
) -> SpotEvaluation:
    current = current_slot(forecast.slots, now)
    local_date, window = evaluation_window(forecast, now)
    good_slots = tuple(s for s in window if slot_passes(rules, s))
    passes = len(good_slots) >= settings.min_good_hours
    logger.debug(
        "%s: %d window slots, %d good, passes_rules=%s",
        forecast.spot.name,
        len(window),
        len(good_slots),
        passes,
    )
    return SpotEvaluation(
        spot=forecast.spot,
        local_date=local_date,
        current=current,
        window=window,
        good_slots=good_slots,
        passes_rules=passes,
    )


def describe_rule(rule: Rule, settings: Settings) -> str:
    kind = METRICS.get(rule.metric)
    if kind is None:
        raise ValueError(f"unknown metric: {rule.metric}")
    if kind == "text":
        if rule.op in ("in", "not_in"):
            return f"{rule.metric} {rule.op} [{', '.join(rule.value)}]"
        return f"{rule.metric} {rule.op} {rule.value}"
    return f"{rule.metric} {rule.op} {rule.value:g}{_SUFFIXES[kind](settings)}"
