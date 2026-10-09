"""Download Surfline forecasts (through pysurfline and requests) and normalize them into hourly slots."""

from __future__ import annotations

import contextlib
import io
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

import requests

from surf_alert.errors import SurflineError
from surf_alert.models import DaylightWindow, ForecastData, Settings, Slot, Spot, Swell
from surf_alert.units import convert_height, convert_speed, convert_temperature

logger = logging.getLogger(__name__)

FORECAST_DAYS: int = 1
FORECAST_INTERVAL_HOURS: int = 1
MATCH_TOLERANCE_SECONDS: int = 3600
KBYG_URL: str = "https://services.surfline.com/kbyg/spots/forecasts/{}"
DEFAULT_TIMEOUT: float = 30.0


class ForecastSource(Protocol):
    def fetch(self, spot: Spot) -> ForecastData: ...


class SurflineClient:
    """Fetches one spot's forecast (by default with pysurfline plus requests) and parses it."""

    def __init__(
        self,
        settings: Settings,
        forecast_factory: Callable[[dict], object] | None = None,
        *,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings
        self._forecast_factory = forecast_factory
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    def fetch(self, spot: Spot) -> ForecastData:
        params = {
            "spotId": spot.spot_id,
            "days": FORECAST_DAYS,
            "intervalHours": FORECAST_INTERVAL_HOURS,
        }
        captured = io.StringIO()
        logger.debug("fetching Surfline forecast for %s", spot.name)
        try:
            with contextlib.redirect_stdout(captured):
                if self._forecast_factory is None:
                    raw = self._default_forecast(params)
                else:
                    raw = self._forecast_factory(params)
        except Exception as exc:
            raise SurflineError(f"failed to fetch forecast for {spot.name}: {exc}") from exc
        finally:
            output = captured.getvalue()
            if output:
                logger.debug("output while fetching %s: %s", spot.name, " ".join(output.split()))
        return parse_forecast(raw, spot, self._settings)

    def _default_forecast(self, params: dict) -> object:
        """pysurfline's SpotForecast, plus the wave data and ratings it cannot get.

        Surfline retired the `wave` forecast that pysurfline requests (it answers HTTP 404), so
        `wave` and `units_wave` are rebuilt from the `surf` and `swells` forecasts that replaced it.
        """
        from pysurfline import SpotForecast

        raw = SpotForecast(params, verbose=False)
        surf, surf_units = self._get_forecast("surf", params)
        swells, swell_units = self._get_forecast("swells", params)
        raw.wave = _wave_entries(surf, swells)
        raw.units_wave = {"waveHeight": surf_units.get("waveHeight")}
        if "swellHeight" in swell_units:
            raw.units_wave["swellHeight"] = swell_units["swellHeight"]
        raw.rating, _ = self._get_forecast("rating", params)
        return raw

    def _get_forecast(self, kind: str, params: dict) -> tuple[list, dict]:
        """One Surfline forecast type: its entries and its units dict."""
        response = self._session.get(KBYG_URL.format(kind), params=params, timeout=self._timeout)
        logger.debug("Surfline %s forecast responded with HTTP %s", kind, response.status_code)
        if response.status_code != 200:
            raise SurflineError(
                f"Surfline returned HTTP {response.status_code} for the {kind} forecast"
            )
        body = _as_dict(response.json())
        entries = _as_list(_as_dict(body.get("data")).get(kind))
        units = _as_dict(_as_dict(body.get("associated")).get("units"))
        return entries, units


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_unit(unit: Any, allowed: tuple[str, ...]) -> bool:
    return isinstance(unit, str) and unit.upper() in allowed


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _timed_entries(source: Any) -> list[dict]:
    """Entries that are dicts with an int (not bool) `timestamp`."""
    return [e for e in _as_list(source) if isinstance(e, dict) and _is_int(e.get("timestamp"))]


def _nearest(entries: list[dict], timestamp: int) -> dict | None:
    """The entry nearest to `timestamp` within the tolerance; ties go to the earlier timestamp."""
    best: dict | None = None
    best_key: tuple[int, int] | None = None
    for entry in entries:
        key = (abs(entry["timestamp"] - timestamp), entry["timestamp"])
        if best_key is None or key < best_key:
            best, best_key = entry, key
    if best_key is None or best_key[0] > MATCH_TOLERANCE_SECONDS:
        return None
    return best


def _wave_entries(surf: list, swells: list) -> list:
    """Entries shaped like pysurfline's flattened `wave` forecast.

    Each `surf` entry's heights become `surf_min`/`surf_max`, and the nearest `swells` entry
    supplies `swells`. Entries without a `surf` dict are passed on for parse_forecast to reject.
    """
    swell_entries = _timed_entries(swells)
    entries: list = []
    for entry in surf:
        heights = entry.get("surf") if isinstance(entry, dict) else None
        if not isinstance(heights, dict):
            entries.append(entry)
            continue
        wave = {key: value for key, value in entry.items() if key != "surf"}
        wave.update({f"surf_{key}": value for key, value in heights.items()})
        if _is_int(entry.get("timestamp")):
            match = _nearest(swell_entries, entry["timestamp"])
            if match is not None:
                wave["swells"] = match.get("swells")
        entries.append(wave)
    return entries


def _primary_swell(entry: dict, swell_unit: str, settings: Settings) -> Swell | None:
    swells = entry.get("swells") or []
    best: dict | None = None
    for swell in _as_list(swells):
        if not isinstance(swell, dict):
            continue
        height = swell.get("height")
        if not (
            _is_number(height)
            and _is_number(swell.get("period"))
            and _is_number(swell.get("direction"))
        ):
            continue
        if height <= 0:
            continue
        if best is None or height > best["height"]:
            best = swell
    if best is None:
        return None
    return Swell(
        height=float(convert_height(best["height"], swell_unit, settings.height_unit)),
        period=float(best["period"]),
        direction=float(best["direction"]),
    )


def _check_wave_entry(index: int, entry: Any, spot: Spot) -> None:
    def malformed(detail: str) -> SurflineError:
        return SurflineError(f"malformed forecast data for {spot.name}: {detail}")

    if not isinstance(entry, dict):
        raise malformed(f"wave entry {index} is not a table")
    checks = (
        ("timestamp", _is_int, "an integer"),
        ("utcOffset", _is_number, "a number"),
        ("surf_min", _is_number, "a number"),
        ("surf_max", _is_number, "a number"),
    )
    for key, check, expected in checks:
        if key not in entry:
            raise malformed(f"wave entry {index} missing '{key}'")
        if not check(entry[key]):
            raise malformed(f"wave entry {index} has a '{key}' that is not {expected}")


def parse_forecast(raw: object, spot: Spot, settings: Settings) -> ForecastData:
    """Convert raw pysurfline attributes into slots in the configured units (spec 4.5)."""
    # Step 1: required data.
    wave = getattr(raw, "wave", None)
    if not isinstance(wave, list) or not wave:
        raise SurflineError(f"no wave data returned for {spot.name}")
    wind = getattr(raw, "wind", None)
    if not isinstance(wind, list) or not wind:
        raise SurflineError(f"no wind data returned for {spot.name}")

    # Step 2: required units.
    units_wave = _as_dict(getattr(raw, "units_wave", None))
    units_wind = _as_dict(getattr(raw, "units_wind", None))
    wave_unit = units_wave.get("waveHeight")
    if not _valid_unit(wave_unit, ("FT", "M")):
        raise SurflineError(f"unsupported waveHeight unit for {spot.name}: {wave_unit!r}")
    swell_unit = units_wave.get("swellHeight", wave_unit)
    if not _valid_unit(swell_unit, ("FT", "M")):
        raise SurflineError(f"unsupported swellHeight unit for {spot.name}: {swell_unit!r}")
    wind_unit = units_wind.get("windSpeed")
    if not _valid_unit(wind_unit, ("KTS", "KPH", "MPH")):
        raise SurflineError(f"unsupported windSpeed unit for {spot.name}: {wind_unit!r}")

    # Step 3: optional units.
    tide_unit = _as_dict(getattr(raw, "units_tides", None)).get("tideHeight")
    if not _valid_unit(tide_unit, ("FT", "M")):
        tide_unit = None
    temperature_unit = _as_dict(getattr(raw, "units_weather", None)).get("temperature")
    if not _valid_unit(temperature_unit, ("C", "F")):
        temperature_unit = None

    # Step 6 (preparation): candidate entries for matching.
    wind_entries = _timed_entries(wind)
    if tide_unit is None:
        tide_entries: list[dict] = []
    else:
        tide_entries = [
            e for e in _timed_entries(getattr(raw, "tides", None)) if _is_number(e.get("height"))
        ]
    weather_entries = _timed_entries(getattr(raw, "weather", None))
    rating_entries = _timed_entries(getattr(raw, "rating", None))

    slots: list[Slot] = []
    for index, entry in enumerate(wave):
        # Step 4: the wave entry itself.
        _check_wave_entry(index, entry, spot)
        timestamp = entry["timestamp"]
        surf_min = float(convert_height(entry["surf_min"], wave_unit, settings.height_unit))
        surf_max = float(convert_height(entry["surf_max"], wave_unit, settings.height_unit))

        # Step 5: primary swell.
        primary_swell = _primary_swell(entry, swell_unit, settings)

        # Step 6: wind, tide, weather and rating.
        wind_speed = wind_gust = wind_direction = None
        wind_direction_type = None
        match = _nearest(wind_entries, timestamp)
        if match is not None:
            speed = match.get("speed")
            if _is_number(speed):
                wind_speed = float(convert_speed(speed, wind_unit, settings.wind_unit))
            gust = match.get("gust")
            if _is_number(gust):
                wind_gust = float(convert_speed(gust, wind_unit, settings.wind_unit))
            direction = match.get("direction")
            if _is_number(direction):
                wind_direction = float(direction)
            direction_type = match.get("directionType")
            if isinstance(direction_type, str):
                wind_direction_type = direction_type

        tide_height = None
        match = _nearest(tide_entries, timestamp)
        if match is not None and tide_unit is not None:
            tide_height = float(convert_height(match["height"], tide_unit, settings.height_unit))

        temperature = None
        weather_condition = None
        match = _nearest(weather_entries, timestamp)
        if match is not None:
            value = match.get("temperature")
            if temperature_unit is not None and _is_number(value):
                temperature = float(
                    convert_temperature(value, temperature_unit, settings.temperature_unit)
                )
            condition = match.get("condition")
            if isinstance(condition, str):
                weather_condition = condition

        surf_rating = None
        match = _nearest(rating_entries, timestamp)
        if match is not None:
            value = _as_dict(match.get("rating")).get("value")
            if _is_int(value):
                surf_rating = value

        slots.append(
            Slot(
                timestamp=timestamp,
                utc_offset=float(entry["utcOffset"]),
                surf_min=surf_min,
                surf_max=surf_max,
                surf_rating=surf_rating,
                primary_swell=primary_swell,
                wind_speed=wind_speed,
                wind_gust=wind_gust,
                wind_direction=wind_direction,
                wind_direction_type=wind_direction_type,
                tide_height=tide_height,
                temperature=temperature,
                weather_condition=weather_condition,
            )
        )

    # Step 7: daylight windows.
    daylight: list[DaylightWindow] = []
    for item in _as_list(getattr(raw, "sunlightTimes", None) or []):
        if not isinstance(item, dict):
            continue
        midnight = item.get("midnight")
        dawn = item.get("dawn")
        dusk = item.get("dusk")
        offset = item.get("midnightUTCOffset")
        if not (_is_int(midnight) and _is_int(dawn) and _is_int(dusk) and _is_number(offset)):
            continue
        local_date = datetime.fromtimestamp(
            midnight + round(offset * 3600), tz=timezone.utc
        ).date()
        daylight.append(DaylightWindow(local_date, dawn, dusk))

    # Step 8.
    slots.sort(key=lambda s: s.timestamp)
    logger.debug("parsed %d slots for %s", len(slots), spot.name)
    return ForecastData(spot=spot, slots=tuple(slots), daylight=tuple(daylight))
