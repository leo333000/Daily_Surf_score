"""Unit conversion for heights, speeds and temperatures."""

from __future__ import annotations

FT_PER_M: float = 3.28084
KPH_PER_KT: float = 1.852
MPH_PER_KT: float = 1.150779

_HEIGHT_UNITS = ("ft", "m")
_SPEED_PER_KT = {"kts": 1.0, "kph": KPH_PER_KT, "mph": MPH_PER_KT}
_TEMPERATURE_UNITS = ("c", "f")


def _validate(unit: object, allowed, kind: str) -> str:
    if not isinstance(unit, str) or unit.lower() not in allowed:
        raise ValueError(f"unsupported {kind} unit: {unit!r}")
    return unit.lower()


def convert_height(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between "ft" and "m" (case-insensitive)."""
    src = _validate(from_unit, _HEIGHT_UNITS, "height")
    dst = _validate(to_unit, _HEIGHT_UNITS, "height")
    if src == dst:
        return value
    if src == "m":
        return value * FT_PER_M
    return value / FT_PER_M


def convert_speed(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between "kts", "kph" and "mph" (case-insensitive), through knots."""
    src = _validate(from_unit, _SPEED_PER_KT, "speed")
    dst = _validate(to_unit, _SPEED_PER_KT, "speed")
    if src == dst:
        return value
    knots = value / _SPEED_PER_KT[src]
    return knots * _SPEED_PER_KT[dst]


def convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between "c" and "f" (case-insensitive)."""
    src = _validate(from_unit, _TEMPERATURE_UNITS, "temperature")
    dst = _validate(to_unit, _TEMPERATURE_UNITS, "temperature")
    if src == dst:
        return value
    if src == "f":
        return (value - 32) * 5 / 9
    return value * 9 / 5 + 32
