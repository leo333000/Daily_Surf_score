"""Immutable data classes shared by every Surf Alert module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class Spot:
    name: str
    spot_id: str  # TOML key is `id`
    notes: str = ""


@dataclass(frozen=True)
class Rule:
    metric: str  # a key of config.METRICS
    op: str  # one of config.ALL_OPS
    value: float | str | tuple[str, ...]
    # float for numeric metrics; str for text metrics with ==/!=; tuple[str, ...] for in/not_in


@dataclass(frozen=True)
class Settings:
    height_unit: str = "ft"  # "ft" | "m"
    wind_unit: str = "kts"  # "kts" | "kph" | "mph"
    temperature_unit: str = "C"  # "C" | "F"
    min_good_hours: int = 1  # 1..24
    jev_threshold: float = 0.7  # 0 < x <= 1
    jev_model: str = "jev-latest"
    on_jev_error: str = "rules_only"  # "rules_only" | "skip"
    max_spots_in_message: int = 3  # 1..10
    notes: str = ""


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    spots: tuple[Spot, ...]
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Swell:
    height: float  # configured height unit
    period: float  # seconds
    direction: float  # degrees, direction the swell comes FROM


@dataclass(frozen=True)
class Slot:
    timestamp: int  # epoch seconds, UTC, start of the hour
    utc_offset: float  # hours, e.g. -10.0 or 5.5
    surf_min: float  # configured height unit
    surf_max: float
    surf_rating: int | None  # Surfline rating, 0 (very poor) to 6 (epic)
    primary_swell: Swell | None
    wind_speed: float | None  # configured wind unit
    wind_gust: float | None
    wind_direction: float | None  # degrees
    wind_direction_type: str | None  # e.g. "Offshore", "Cross-shore", "Onshore"
    tide_height: float | None  # configured height unit
    temperature: float | None  # configured temperature unit
    weather_condition: str | None  # raw Surfline value, e.g. "MOSTLY_CLEAR"

    def local_datetime(self) -> datetime:
        """Naive datetime of the spot's local wall-clock time."""
        return datetime.fromtimestamp(
            self.timestamp + round(self.utc_offset * 3600), tz=timezone.utc
        ).replace(tzinfo=None)


@dataclass(frozen=True)
class DaylightWindow:
    local_date: date
    dawn: int  # epoch seconds UTC
    dusk: int  # epoch seconds UTC


@dataclass(frozen=True)
class ForecastData:
    spot: Spot
    slots: tuple[Slot, ...]  # ascending timestamp; never empty
    daylight: tuple[DaylightWindow, ...]  # may be empty


@dataclass(frozen=True)
class SpotEvaluation:
    spot: Spot
    local_date: date  # the spot's "today"
    current: Slot
    window: tuple[Slot, ...]  # today's remaining daylight slots, ascending
    good_slots: tuple[Slot, ...]  # subset of window where every rule passes, ascending
    passes_rules: bool  # len(good_slots) >= settings.min_good_hours


@dataclass(frozen=True)
class Recommendation:
    evaluation: SpotEvaluation
    probability: float | None  # Jev "noul" value; None under the rules-only fallback


@dataclass(frozen=True)
class Decision:
    recommendations: tuple[Recommendation, ...]  # best first; may be empty
    jev_used: bool
    jev_error: str | None = None


@dataclass(frozen=True)
class RunResult:
    evaluations: tuple[SpotEvaluation, ...]  # successfully fetched spots, config order
    failures: dict[str, str]  # spot name -> error message, config order
    decision: Decision | None  # None when there were no candidates (Jev not called)
    message: str | None  # None when there is nothing to notify
    sent: bool
    send_error: str | None
    exit_code: int  # 0, 3 or 4
