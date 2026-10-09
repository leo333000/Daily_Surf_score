"""Load and validate the TOML config file (spots, rules and settings)."""

from __future__ import annotations

import logging
import os
import re
import tomllib
from typing import Any

from surf_alert.errors import ConfigError
from surf_alert.models import AppConfig, Rule, Settings, Spot

logger = logging.getLogger(__name__)

METRICS: dict[str, str] = {
    "wave_height_min": "height",
    "wave_height_max": "height",
    "surf_rating": "score",
    "swell_height": "height",
    "swell_period": "period",
    "wind_speed": "speed",
    "wind_gust": "speed",
    "wind_direction_type": "text",
    "tide_height": "height",
    "temperature": "temperature",
    "weather_condition": "text",
}

NUMERIC_OPS: tuple[str, ...] = (">", ">=", "<", "<=", "==", "!=")
TEXT_OPS: tuple[str, ...] = ("==", "!=", "in", "not_in")
ALL_OPS: tuple[str, ...] = (">", ">=", "<", "<=", "==", "!=", "in", "not_in")
MAX_SPOTS: int = 50

_TOP_LEVEL_KEYS = ("settings", "spots", "rules")
_SETTINGS_KEYS = (
    "height_unit",
    "wind_unit",
    "temperature_unit",
    "min_good_hours",
    "jev_threshold",
    "jev_model",
    "on_jev_error",
    "max_spots_in_message",
    "notes",
)
_SPOT_KEYS = ("name", "id", "notes")
_RULE_KEYS = ("metric", "op", "value")
_SPOT_ID_PATTERN = r"[0-9a-fA-F]{24}"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_non_blank_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _check_unknown_keys(table: dict, allowed: tuple[str, ...], where: str) -> None:
    for key in table:
        if key not in allowed:
            raise ConfigError(f"unknown key '{key}' in {where}")


def _parse_settings(data: dict) -> Settings:
    if "settings" not in data:
        return Settings()
    table = data["settings"]
    if not isinstance(table, dict):
        raise ConfigError("settings must be a table")
    _check_unknown_keys(table, _SETTINGS_KEYS, "settings")

    values: dict[str, Any] = {}

    if "height_unit" in table:
        v = table["height_unit"]
        if not isinstance(v, str) or v.lower() not in ("ft", "m"):
            raise ConfigError("settings.height_unit must be one of: ft, m")
        values["height_unit"] = v.lower()

    if "wind_unit" in table:
        v = table["wind_unit"]
        if not isinstance(v, str) or v.lower() not in ("kts", "kph", "mph"):
            raise ConfigError("settings.wind_unit must be one of: kts, kph, mph")
        values["wind_unit"] = v.lower()

    if "temperature_unit" in table:
        v = table["temperature_unit"]
        if not isinstance(v, str) or v.upper() not in ("C", "F"):
            raise ConfigError("settings.temperature_unit must be one of: C, F")
        values["temperature_unit"] = v.upper()

    if "min_good_hours" in table:
        v = table["min_good_hours"]
        if not _is_int(v) or not 1 <= v <= 24:
            raise ConfigError("settings.min_good_hours must be an integer between 1 and 24")
        values["min_good_hours"] = v

    if "jev_threshold" in table:
        v = table["jev_threshold"]
        if not _is_number(v) or not 0 < v <= 1:
            raise ConfigError("settings.jev_threshold must be a number greater than 0 and at most 1")
        values["jev_threshold"] = float(v)

    if "jev_model" in table:
        v = table["jev_model"]
        if not _is_non_blank_str(v):
            raise ConfigError("settings.jev_model must be a non-empty string")
        values["jev_model"] = v

    if "on_jev_error" in table:
        v = table["on_jev_error"]
        if not isinstance(v, str) or v not in ("rules_only", "skip"):
            raise ConfigError("settings.on_jev_error must be one of: rules_only, skip")
        values["on_jev_error"] = v

    if "max_spots_in_message" in table:
        v = table["max_spots_in_message"]
        if not _is_int(v) or not 1 <= v <= 10:
            raise ConfigError("settings.max_spots_in_message must be an integer between 1 and 10")
        values["max_spots_in_message"] = v

    if "notes" in table:
        v = table["notes"]
        if not isinstance(v, str):
            raise ConfigError("settings.notes must be a string")
        values["notes"] = v

    return Settings(**values)


def _parse_spots(data: dict) -> tuple[Spot, ...]:
    entries = data.get("spots")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("config must define at least one [[spots]] entry")

    spots: list[Spot] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    for i, entry in enumerate(entries):
        where = f"spots[{i}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        _check_unknown_keys(entry, _SPOT_KEYS, where)

        name = entry.get("name")
        if not _is_non_blank_str(name):
            raise ConfigError(f"{where}.name must be a non-empty string")
        name = name.strip()

        spot_id = entry.get("id")
        if not isinstance(spot_id, str) or re.fullmatch(_SPOT_ID_PATTERN, spot_id) is None:
            raise ConfigError(f"{where}.id must be a 24-character hexadecimal Surfline spot id")

        notes = entry.get("notes", "")
        if not isinstance(notes, str):
            raise ConfigError(f"{where}.notes must be a string")

        name_key = name.casefold()
        if name_key in seen_names:
            raise ConfigError(f"duplicate spot name: {name}")
        id_key = spot_id.lower()
        if id_key in seen_ids:
            raise ConfigError(f"duplicate spot id: {spot_id}")
        seen_names.add(name_key)
        seen_ids.add(id_key)

        spots.append(Spot(name, spot_id, notes))

    if len(spots) > MAX_SPOTS:
        raise ConfigError(f"config may define at most {MAX_SPOTS} spots")
    return tuple(spots)


def _parse_rules(data: dict) -> tuple[Rule, ...]:
    entries = data.get("rules")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("config must define at least one [[rules]] entry")

    rules: list[Rule] = []
    for i, entry in enumerate(entries):
        where = f"rules[{i}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        _check_unknown_keys(entry, _RULE_KEYS, where)

        metric = entry.get("metric")
        if not isinstance(metric, str) or metric not in METRICS:
            raise ConfigError(f"{where}.metric must be one of: {', '.join(METRICS)}")

        op = entry.get("op")
        if not isinstance(op, str) or op not in ALL_OPS:
            raise ConfigError(f"{where}.op must be one of: {', '.join(ALL_OPS)}")

        kind = METRICS[metric]
        allowed_ops = TEXT_OPS if kind == "text" else NUMERIC_OPS
        if op not in allowed_ops:
            raise ConfigError(f"{where}.op '{op}' is not valid for metric '{metric}'")

        value = entry.get("value")
        if kind != "text":
            if not _is_number(value):
                raise ConfigError(f"{where}.value must be a number")
            parsed: float | str | tuple[str, ...] = float(value)
        elif op in ("==", "!="):
            if not _is_non_blank_str(value):
                raise ConfigError(f"{where}.value must be a non-empty string")
            parsed = value
        else:
            if (
                not isinstance(value, list)
                or not value
                or not all(_is_non_blank_str(item) for item in value)
            ):
                raise ConfigError(f"{where}.value must be a non-empty list of non-empty strings")
            parsed = tuple(value)

        rules.append(Rule(metric, op, parsed))
    return tuple(rules)


def parse_config(data: dict) -> AppConfig:
    """Validate an already-parsed TOML document. Raises ConfigError (messages in spec 6.1)."""
    for key in data:
        if key not in _TOP_LEVEL_KEYS:
            raise ConfigError(f"unknown key '{key}' in top level")
    settings = _parse_settings(data)
    spots = _parse_spots(data)
    rules = _parse_rules(data)
    return AppConfig(settings=settings, spots=spots, rules=rules)


def load_config(path: str | os.PathLike) -> AppConfig:
    """Read the file with tomllib (binary mode), then call parse_config.

    Raises ConfigError for a missing file, invalid TOML, or invalid content.
    """
    shown = str(path)
    try:
        with open(path, "rb") as fp:
            data = tomllib.load(fp)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {shown}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"invalid TOML in {shown}: {exc}") from None
    except OSError:
        raise ConfigError(f"cannot read config file: {shown}") from None
    logger.debug("loaded config file %s", shown)
    return parse_config(data)
