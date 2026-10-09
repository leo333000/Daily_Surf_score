"""Surfline fetching and parsing (FR-4, FR-5, spec 4.5, 6.2): TC-48 to TC-74 and TC-214 to TC-217."""

from __future__ import annotations

import copy
import logging
import sys
import types
from datetime import date

import pytest
import requests

from surf_alert.errors import SurflineError
from surf_alert.models import DaylightWindow, Settings, Spot, Swell
from surf_alert.surfline import SurflineClient, parse_forecast
from tests.fakes import FakeResponse, RoutingSession
from tests.scenario import (
    DAWN,
    DUSK,
    PIPELINE,
    REF_SUNLIGHT,
    SUNSET,
    REF_UNITS,
    forecast_bodies,
    local_ts,
    make_raw,
    pipeline_forecast,
    pipeline_raw,
    pysurfline_raw,
    rating_entry,
    sunset_forecast,
    sunset_raw,
    tide_entry,
    wave_entry,
    weather_entry,
    wind_entry,
    with_,
    without,
)

T = local_ts(7)
_MISSING = object()
FORECAST_URL = "https://services.surfline.com/kbyg/spots/forecasts/{}"
PARAMS = {"spotId": PIPELINE.spot_id, "days": 1, "intervalHours": 1}


def parse(raw, settings=None):
    return parse_forecast(raw, PIPELINE, settings or Settings())


def surfline_error(raw, settings=None):
    with pytest.raises(SurflineError) as ei:
        parse(raw, settings)
    return str(ei.value)


def recording_factory(result):
    calls = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    return factory, calls


def install_pysurfline(monkeypatch, raw):
    """Make `pysurfline.SpotForecast` record `(args, kwargs)` and return `raw`."""
    calls = []

    def spot_forecast(*args, **kwargs):
        calls.append((args, kwargs))
        return raw

    module = types.ModuleType("pysurfline")
    module.SpotForecast = spot_forecast
    monkeypatch.setitem(sys.modules, "pysurfline", module)
    return calls


def forecast_session(bodies):
    """A RoutingSession answering each forecast kind with its body (or FakeResponse or exception)."""
    return RoutingSession(
        {
            FORECAST_URL.format(kind): (
                body if isinstance(body, (FakeResponse, BaseException)) else FakeResponse(200, body)
            )
            for kind, body in bodies.items()
        }
    )


def default_fetch(monkeypatch, **bodies):
    """Fetch PIPELINE through the default path from `make_raw()`-based data.

    pysurfline returns `make_raw()` without its wave data; the session answers `surf`, `swells`
    and `rating` from `make_raw(rating=[rating_entry(T)])`, with `bodies` replacing any kind.
    """
    install_pysurfline(monkeypatch, pysurfline_raw(make_raw()))
    responses = forecast_bodies(make_raw(rating=[rating_entry(T)]))
    responses.update(bodies)
    return SurflineClient(Settings(), session=forecast_session(responses)).fetch(PIPELINE)


def default_fetch_error(monkeypatch, **bodies):
    with pytest.raises(SurflineError) as ei:
        default_fetch(monkeypatch, **bodies)
    return str(ei.value)


def surf_body(entries, units=_MISSING):
    units = {"waveHeight": "FT"} if units is _MISSING else units
    return {"associated": {"units": units}, "data": {"surf": entries}}


def swells_body(entries, units=_MISSING):
    units = {"swellHeight": "FT"} if units is _MISSING else units
    return {"associated": {"units": units}, "data": {"swells": entries}}


def surf_item(ts=T, surf_min=2.0, surf_max=3.0):
    return {"timestamp": ts, "utcOffset": -10, "surf": {"min": surf_min, "max": surf_max}}


def swells_item(ts=T, swells=None):
    return {"timestamp": ts, "utcOffset": -10, "swells": swells or [_s(4.2, 11, 315.0)]}


# ---------------------------------------------------------------------------
# SurflineClient.fetch
# ---------------------------------------------------------------------------


def test_tc048_factory_called_with_params():
    """TC-48: AC-4.1, the factory is called once with the single positional params dict."""
    factory, calls = recording_factory(pipeline_raw())
    SurflineClient(Settings(), forecast_factory=factory).fetch(
        Spot("Pipeline", "5842041f4e65fad6a7708890")
    )
    assert calls == [(({"spotId": "5842041f4e65fad6a7708890", "days": 1, "intervalHours": 1},), {})]


def _printing_factory(params):
    print("Error : 500")
    return pipeline_raw()


def test_tc049_factory_stdout_suppressed(capsys):
    """TC-49: AC-4.2, text the factory prints never reaches stdout."""
    result = SurflineClient(Settings(), forecast_factory=_printing_factory).fetch(PIPELINE)
    assert capsys.readouterr().out == ""
    assert result == pipeline_forecast()


def test_tc050_factory_stdout_logged_at_debug(caplog):
    """TC-50: AC-4.2 and spec 4.12, captured text is logged at DEBUG by surf_alert.surfline."""
    caplog.set_level(logging.DEBUG, logger="surf_alert")
    SurflineClient(Settings(), forecast_factory=_printing_factory).fetch(PIPELINE)
    assert any(
        r.name == "surf_alert.surfline"
        and r.levelno == logging.DEBUG
        and "Error : 500" in r.getMessage()
        for r in caplog.records
    )
    assert not any(
        r.levelno >= logging.WARNING and "Error : 500" in r.getMessage() for r in caplog.records
    )


@pytest.mark.parametrize(
    "exc",
    [
        requests.ConnectionError("boom"),
        ValueError("boom"),
        ImportError("boom"),
        RuntimeError("boom"),
    ],
    ids=["connection", "value", "import", "runtime"],
)
def test_tc051_factory_exception_wrapped(exc):
    """TC-51: AC-4.3, any factory exception becomes SurflineError with the spot name."""

    def factory(params):
        raise exc

    with pytest.raises(SurflineError) as ei:
        SurflineClient(Settings(), forecast_factory=factory).fetch(PIPELINE)
    assert str(ei.value) == "failed to fetch forecast for Pipeline: boom"


def test_tc052_parse_errors_not_wrapped():
    """TC-52: parse errors from fetch propagate unchanged (not wrapped as fetch failures)."""
    factory, _ = recording_factory(make_raw(wave=[]))
    with pytest.raises(SurflineError) as ei:
        SurflineClient(Settings(), forecast_factory=factory).fetch(PIPELINE)
    assert str(ei.value) == "no wave data returned for Pipeline"


def test_tc053_default_factory_without_pysurfline():
    """TC-53: (a) AC-4.6, with pysurfline unavailable fetch raises a wrapped SurflineError."""
    with pytest.raises(SurflineError) as ei:
        SurflineClient(Settings()).fetch(PIPELINE)
    assert str(ei.value).startswith("failed to fetch forecast for Pipeline: ")


def test_tc053_default_factory_uses_spot_forecast(monkeypatch):
    """TC-53: (b) the default path calls pysurfline.SpotForecast(params, verbose=False), imported
    lazily, then GETs the surf, swells and rating forecasts through the session."""
    calls = install_pysurfline(monkeypatch, pysurfline_raw(pipeline_raw()))
    session = forecast_session(forecast_bodies(pipeline_raw()))

    result = SurflineClient(Settings(), session=session).fetch(PIPELINE)
    assert calls == [((PARAMS,), {"verbose": False})]
    assert session.calls == [
        {"method": "get", "url": FORECAST_URL.format(kind), "params": PARAMS, "timeout": 30.0}
        for kind in ("surf", "swells", "rating")
    ]
    assert result == pipeline_forecast()


def test_tc214_default_path_reference_and_timeout(monkeypatch):
    """TC-214: the default path rebuilds Sunset Beach's reference forecast and uses the given timeout."""
    install_pysurfline(monkeypatch, pysurfline_raw(sunset_raw()))
    session = forecast_session(forecast_bodies(sunset_raw()))

    result = SurflineClient(Settings(), session=session, timeout=5.0).fetch(SUNSET)
    assert result == sunset_forecast()
    assert [c["timeout"] for c in session.calls] == [5.0, 5.0, 5.0]


def test_tc215_wave_rebuilt_from_surf_and_swells(monkeypatch):
    """TC-215: each surf entry takes the nearest swells entry within 3600 s, and units come from
    the surf (waveHeight) and swells (swellHeight) responses."""
    nearest = default_fetch(
        monkeypatch,
        surf=surf_body([surf_item(T)]),
        swells=swells_body(
            [swells_item(T - 3600, [_s(1.0, 8, 90)]), swells_item(T + 1800, [_s(2.0, 12, 300)])]
        ),
    )
    assert nearest.slots[0].primary_swell == Swell(2.0, 12.0, 300.0)

    far = default_fetch(
        monkeypatch, surf=surf_body([surf_item(T)]), swells=swells_body([swells_item(T + 3601)])
    )
    assert far.slots[0].primary_swell is None
    assert far.slots[0].surf_min == 2.0

    meters = default_fetch(
        monkeypatch,
        surf=surf_body([surf_item(T, 1.0, 2.0)], {"waveHeight": "M"}),
        swells=swells_body([swells_item(T, [_s(1.0, 10, 270)])], {}),
    )
    assert meters.slots[0].surf_min == pytest.approx(3.28084, abs=1e-6)
    assert meters.slots[0].primary_swell.height == pytest.approx(3.28084, abs=1e-6)

    mixed = default_fetch(
        monkeypatch,
        surf=surf_body([surf_item(T, 2.0, 3.0)]),
        swells=swells_body([swells_item(T, [_s(1.0, 10, 270)])], {"swellHeight": "M"}),
    )
    assert mixed.slots[0].surf_min == 2.0
    assert mixed.slots[0].primary_swell.height == pytest.approx(3.28084, abs=1e-6)


@pytest.mark.parametrize("kind", ["surf", "swells", "rating"])
def test_tc216_http_error_wrapped(monkeypatch, kind):
    """TC-216: (a) a non-200 answer for any of the three forecasts fails the spot."""
    msg = default_fetch_error(monkeypatch, **{kind: FakeResponse(404, text="Not Found")})
    assert msg == f"failed to fetch forecast for Pipeline: Surfline returned HTTP 404 for the {kind} forecast"


@pytest.mark.parametrize(
    "bodies, msg",
    [
        (dict(surf=requests.ConnectionError("boom")), "failed to fetch forecast for Pipeline: boom"),
        (dict(swells=FakeResponse(200, text="<html>")), "failed to fetch forecast for Pipeline: not JSON"),
        (dict(surf={}), "no wave data returned for Pipeline"),
        (dict(surf=surf_body([])), "no wave data returned for Pipeline"),
        (dict(surf=surf_body([surf_item()], {})), "unsupported waveHeight unit for Pipeline: None"),
        (dict(surf={"data": {"surf": [surf_item()]}}), "unsupported waveHeight unit for Pipeline: None"),
    ],
    ids=["connection", "not_json", "no_data", "empty", "no_wave_unit", "no_associated"],
)
def test_tc216_bad_responses(monkeypatch, bodies, msg):
    """TC-216: (b) request errors are wrapped; missing data or units fail like a bad raw object."""
    assert default_fetch_error(monkeypatch, **bodies) == msg


@pytest.mark.parametrize(
    "entry",
    [{"timestamp": T, "utcOffset": -10}, {"timestamp": T, "utcOffset": -10, "surf": 2.0}, "x",
     {"timestamp": T, "utcOffset": -10, "surf": {"min": 2.0}}],
    ids=["no_surf", "surf_not_dict", "not_dict", "no_max"],
)
def test_tc216_malformed_surf_entry(monkeypatch, entry):
    """TC-216: (c) a surf entry without usable heights is a malformed forecast."""
    msg = default_fetch_error(monkeypatch, surf=surf_body([surf_item(), entry]))
    assert msg.startswith("malformed forecast data for Pipeline: ")


# ---------------------------------------------------------------------------
# parse_forecast: reference data and required data
# ---------------------------------------------------------------------------


def test_tc054_reference_parse():
    """TC-54: the reference raw objects parse to the reference forecasts."""
    pipe = parse_forecast(pipeline_raw(), PIPELINE, Settings())
    sun = parse_forecast(sunset_raw(), SUNSET, Settings())
    factory, _ = recording_factory(pipeline_raw())
    fetched = SurflineClient(Settings(), forecast_factory=factory).fetch(PIPELINE)

    assert pipe == pipeline_forecast()
    assert sun == sunset_forecast()
    assert fetched == pipeline_forecast()
    for forecast in (pipe, sun, fetched):
        assert isinstance(forecast.slots, tuple)
        assert isinstance(forecast.daylight, tuple)
        assert all(type(s.utc_offset) is float for s in forecast.slots)


@pytest.mark.parametrize(
    "raw, msg",
    [
        (make_raw(omit=("wave",)), "no wave data returned for Pipeline"),
        (make_raw(wave=[]), "no wave data returned for Pipeline"),
        (make_raw(wave=None), "no wave data returned for Pipeline"),
        (make_raw(wave={"timestamp": 1}), "no wave data returned for Pipeline"),
        (make_raw(omit=("wind",)), "no wind data returned for Pipeline"),
        (make_raw(wind=[]), "no wind data returned for Pipeline"),
        (make_raw(omit=("wave", "wind")), "no wave data returned for Pipeline"),
    ],
    ids=["wave_omitted", "wave_empty", "wave_none", "wave_dict", "wind_omitted", "wind_empty", "both_omitted"],
)
def test_tc055_missing_wave_or_wind(raw, msg):
    """TC-55: AC-4.4, missing or empty wave/wind data."""
    assert surfline_error(raw) == msg


def test_tc056_optional_data_missing():
    """TC-56: AC-4.5, without tides, weather and sunlight the parse still succeeds."""
    full = pipeline_raw()
    raw = types.SimpleNamespace(
        wave=full.wave, wind=full.wind, units_wave=full.units_wave, units_wind=full.units_wind
    )
    forecast = parse(raw)
    assert len(forecast.slots) == 24
    for slot in forecast.slots:
        assert slot.tide_height is None
        assert slot.temperature is None
        assert slot.weather_condition is None
        assert slot.surf_rating is None
        assert slot.wind_speed == 8.4
    assert forecast.daylight == ()


TABLE_C = [
    ("wave_fathoms", dict(units_wave={"waveHeight": "FATHOMS"}),
     "unsupported waveHeight unit for Pipeline: 'FATHOMS'"),
    ("wave_empty_dict", dict(units_wave={}), "unsupported waveHeight unit for Pipeline: None"),
    ("wave_swell_only", dict(units_wave={"swellHeight": "FT"}),
     "unsupported waveHeight unit for Pipeline: None"),
    ("wave_omitted", dict(omit=("units_wave",)), "unsupported waveHeight unit for Pipeline: None"),
    ("wave_list", dict(units_wave=["FT"]), "unsupported waveHeight unit for Pipeline: None"),
    ("wave_int", dict(units_wave={"waveHeight": 3}), "unsupported waveHeight unit for Pipeline: 3"),
    ("swell_yd", dict(units_wave={"waveHeight": "FT", "swellHeight": "YD"}),
     "unsupported swellHeight unit for Pipeline: 'YD'"),
    ("wind_ms", dict(units_wind={"windSpeed": "M/S"}), "unsupported windSpeed unit for Pipeline: 'M/S'"),
    ("wind_empty_dict", dict(units_wind={}), "unsupported windSpeed unit for Pipeline: None"),
    ("wind_str", dict(units_wind="KTS"), "unsupported windSpeed unit for Pipeline: None"),
]


@pytest.mark.parametrize(
    "kwargs, msg", [(k, m) for _, k, m in TABLE_C], ids=[n for n, _, _ in TABLE_C]
)
def test_tc057_required_units(kwargs, msg):
    """TC-57: AC-5.6, table C, unknown or missing required units fail the spot."""
    assert surfline_error(make_raw(**kwargs)) == msg


def test_tc058_swell_unit_falls_back_to_wave_unit():
    """TC-58: AC-5.10, a missing swellHeight unit means the waveHeight unit."""
    swell = [{"height": 4.2, "period": 11, "direction": 315.0}]
    raw_a = make_raw(wave=[wave_entry(T, swells=swell)], units_wave={"waveHeight": "FT"})
    assert parse(raw_a).slots[0].primary_swell.height == 4.2

    raw_b = make_raw(wave=[wave_entry(T, swells=swell)], units_wave={"waveHeight": "FT"})
    height_m = parse(raw_b, Settings(height_unit="m")).slots[0].primary_swell.height
    assert height_m == pytest.approx(4.2 / 3.28084, abs=1e-6)

    raw_c = make_raw(
        wave=[wave_entry(T, surf_min=2.0, swells=[{"height": 1.0, "period": 11, "direction": 315.0}])],
        units_wave={"waveHeight": "FT", "swellHeight": "M"},
    )
    slot = parse(raw_c).slots[0]
    assert slot.surf_min == 2.0
    assert slot.primary_swell.height == pytest.approx(3.28084, abs=1e-6)


def test_tc059_units_any_case():
    """TC-59: unit values are accepted in any case."""
    lower = {"temperature": "c", "tideHeight": "ft", "swellHeight": "ft", "waveHeight": "ft", "windSpeed": "kts"}
    raw = make_raw(
        tides=[tide_entry(T)],
        weather=[weather_entry(T)],
        units_wave=dict(lower),
        units_wind=dict(lower),
        units_tides=dict(lower),
        units_weather=dict(lower),
    )
    slot = parse(raw).slots[0]
    assert slot.tide_height == 1.3
    assert slot.temperature == 24.0


def test_tc060_wave_height_conversion():
    """TC-60: AC-5.1, surf heights are converted to the configured height unit."""
    raw = make_raw(
        wave=[wave_entry(T, surf_min=1.0, surf_max=2.0)],
        units_wave={"waveHeight": "M", "swellHeight": "M"},
    )
    slot = parse(raw).slots[0]
    assert slot.surf_min == pytest.approx(3.28084, abs=1e-6)
    assert slot.surf_max == pytest.approx(6.56168, abs=1e-6)

    raw_ft = make_raw(wave=[wave_entry(T, surf_min=3.28084)])
    assert parse(raw_ft, Settings(height_unit="m")).slots[0].surf_min == pytest.approx(1.0, abs=1e-6)


def test_tc061_wind_speed_conversion():
    """TC-61: AC-5.2, wind speed and gust are converted to the configured wind unit."""
    kph = make_raw(wind=[wind_entry(T, speed=18.52, gust=37.04)], units_wind={"windSpeed": "KPH"})
    slot = parse(kph).slots[0]
    assert slot.wind_speed == pytest.approx(10.0, abs=1e-6)
    assert slot.wind_gust == pytest.approx(20.0, abs=1e-6)

    mph = make_raw(wind=[wind_entry(T, speed=11.50779)], units_wind={"windSpeed": "MPH"})
    assert parse(mph).slots[0].wind_speed == pytest.approx(10.0, abs=1e-4)

    kts = make_raw(wind=[wind_entry(T, speed=10)], units_wind={"windSpeed": "KTS"})
    assert parse(kts, Settings(wind_unit="kph")).slots[0].wind_speed == pytest.approx(18.52, abs=1e-6)


def test_tc062_temperature_conversion():
    """TC-62: AC-5.3, temperatures are converted to the configured unit."""
    f_raw = make_raw(weather=[weather_entry(T, temperature=68.0)], units_weather={"temperature": "F"})
    assert parse(f_raw).slots[0].temperature == pytest.approx(20.0, abs=1e-9)

    c_raw = make_raw(weather=[weather_entry(T, temperature=20.0)], units_weather={"temperature": "C"})
    assert parse(c_raw, Settings(temperature_unit="F")).slots[0].temperature == pytest.approx(68.0, abs=1e-9)


def _s(h, p, d):
    return {"height": h, "period": p, "direction": d}


TABLE_D = [
    ("largest_wins", [_s(1.1, 7, 20), _s(4.2, 11, 315), _s(0, 0, 0)], Swell(4.2, 11.0, 315.0)),
    ("all_zero", [_s(0, 0, 0), _s(0, 9, 10)], None),
    ("empty", [], None),
    ("key_missing", _MISSING, None),
    ("none", None, None),
    ("tie_first_wins", [_s(3.0, 9, 10), _s(3.0, 12, 200)], Swell(3.0, 9.0, 10.0)),
    ("non_dict_ignored", ["x", _s(2.0, 8, 90)], Swell(2.0, 8.0, 90.0)),
    ("missing_period_ignored", [{"height": 5.0, "direction": 10}, _s(2.0, 8, 90)], Swell(2.0, 8.0, 90.0)),
    ("bool_height_ignored", [{"height": True, "period": 9, "direction": 10}, _s(0.5, 6, 100)],
     Swell(0.5, 6.0, 100.0)),
    ("str_period_ignored", [{"height": 5.0, "period": "9", "direction": 10}], None),
    ("negative_height", [_s(-1.0, 9, 10)], None),
]


@pytest.mark.parametrize(
    "swells, expected", [(s, e) for _, s, e in TABLE_D], ids=[n for n, _, _ in TABLE_D]
)
def test_tc063_primary_swell(swells, expected):
    """TC-63: AC-5.4, table D, primary swell selection."""
    entry = wave_entry(T)
    if swells is _MISSING:
        del entry["swells"]
    else:
        entry["swells"] = swells
    swell = parse(make_raw(wave=[entry])).slots[0].primary_swell
    assert swell == expected
    if swell is not None:
        assert type(swell.period) is float
        assert type(swell.direction) is float


def test_tc063_primary_swell_converted():
    """TC-63: table D, the primary swell height is converted from the swell unit."""
    entry = wave_entry(T, swells=[_s(1.0, 10, 270)])
    raw = make_raw(wave=[entry], units_wave={"waveHeight": "M", "swellHeight": "M"})
    swell = parse(raw).slots[0].primary_swell
    assert swell.height == pytest.approx(3.28084, abs=1e-6)
    assert swell.period == 10.0
    assert swell.direction == 270.0
    assert type(swell.period) is float
    assert type(swell.direction) is float


def _winds(*items):
    return [wind_entry(i[0], speed=i[1]) if isinstance(i, tuple) else i for i in items]


TABLE_E = [
    ("nearest", _winds((T - 3600, 5.0), (T + 1800, 9.0)), 9.0),
    ("outside_tolerance", _winds((T - 3601, 5.0), (T + 3700, 9.0)), None),
    ("tie_earlier_wins", _winds((T - 1800, 5.0), (T + 1800, 9.0)), 5.0),
    ("tie_earlier_wins_reversed", _winds((T + 1800, 9.0), (T - 1800, 5.0)), 5.0),
    ("tolerance_inclusive", _winds((T + 3600, 9.0)), 9.0),
    ("exact", _winds((T, 5.0)), 5.0),
    ("junk_ignored", _winds("junk", {"speed": 7.0}, {"timestamp": "x", "speed": 6.0}, (T + 600, 9.0)), 9.0),
    ("only_junk", _winds("junk"), None),
]


@pytest.mark.parametrize(
    "wind, expected", [(w, e) for _, w, e in TABLE_E], ids=[n for n, _, _ in TABLE_E]
)
def test_tc064_wind_matching(wind, expected):
    """TC-64: AC-5.5, table E, nearest wind entry within 3600 s, earlier wins ties."""
    slot = parse(make_raw(wave=[wave_entry(T)], wind=wind)).slots[0]
    assert slot.wind_speed == expected
    if expected is None:
        assert slot.wind_gust is None
        assert slot.wind_direction is None
        assert slot.wind_direction_type is None


def test_tc064_bool_timestamp_ignored():
    """TC-64: table E, a bool wind timestamp is ignored (as int 1 it would match)."""
    raw = make_raw(
        wave=[wave_entry(ts=3600, utc_offset=0)],
        wind=[{"timestamp": True, "speed": 6.0, "gust": 7.0, "direction": 1.0, "directionType": "Offshore"}],
    )
    slot = parse(raw).slots[0]
    assert slot.wind_speed is None
    assert slot.wind_gust is None
    assert slot.wind_direction is None
    assert slot.wind_direction_type is None


def _tide_weather_raw(**attrs):
    return make_raw(units_tides=dict(REF_UNITS), units_weather=dict(REF_UNITS), **attrs)


def test_tc065_tide_and_weather_matching():
    """TC-65: AC-5.5, the matching rule also applies to tides and weather."""
    far = parse(_tide_weather_raw(tides=[tide_entry(T - 3601), tide_entry(T + 3601)])).slots[0]
    assert far.tide_height is None

    tie = parse(_tide_weather_raw(tides=[tide_entry(T - 1800, 1.0), tide_entry(T + 1800, 2.0)])).slots[0]
    assert tie.tide_height == 1.0

    edge = parse(_tide_weather_raw(weather=[weather_entry(T + 3600, temperature=20.0)])).slots[0]
    assert edge.temperature == 20.0

    outside = parse(_tide_weather_raw(weather=[weather_entry(T + 3601, temperature=20.0)])).slots[0]
    assert outside.temperature is None
    assert outside.weather_condition is None


def test_tc066_tide_filter_then_match():
    """TC-66: only tide entries with a numeric height are eligible, filtered before matching."""
    none_height = _tide_weather_raw(tides=[{"timestamp": T, "height": None}, tide_entry(T + 1800, 1.0)])
    assert parse(none_height).slots[0].tide_height == 1.0

    str_height = _tide_weather_raw(tides=[{"timestamp": T, "height": "1.3"}, tide_entry(T + 2000, 0.8)])
    assert parse(str_height).slots[0].tide_height == 0.8

    bool_height = _tide_weather_raw(tides=[{"timestamp": T, "height": True}])
    assert parse(bool_height).slots[0].tide_height is None

    meters = make_raw(tides=[tide_entry(T, 1.0)], units_tides={"tideHeight": "M"})
    assert parse(meters).slots[0].tide_height == pytest.approx(3.28084, abs=1e-6)


def test_tc067_wind_field_types():
    """TC-67: wind fields with a missing or wrong type become None individually."""
    base = {"timestamp": T, "speed": "fast", "gust": 12.3, "direction": 45, "directionType": "Offshore"}
    slot = parse(make_raw(wind=[base])).slots[0]
    assert slot.wind_speed is None
    assert slot.wind_gust == 12.3
    assert slot.wind_direction == 45.0
    assert type(slot.wind_direction) is float
    assert slot.wind_direction_type == "Offshore"

    no_gust = without(wind_entry(T), "gust")
    assert parse(make_raw(wind=[no_gust])).slots[0].wind_gust is None

    int_type = with_(wind_entry(T), directionType=5)
    assert parse(make_raw(wind=[int_type])).slots[0].wind_direction_type is None

    bool_speed = with_(wind_entry(T), speed=True)
    assert parse(make_raw(wind=[bool_speed])).slots[0].wind_speed is None


def test_tc068_weather_field_types():
    """TC-68: weather fields with a missing or wrong type become None individually."""
    bad_condition = _tide_weather_raw(weather=[{"timestamp": T, "temperature": 24.0, "condition": 7}])
    slot = parse(bad_condition).slots[0]
    assert slot.temperature == 24.0
    assert slot.weather_condition is None

    bad_temp = _tide_weather_raw(weather=[{"timestamp": T, "temperature": "hot", "condition": "CLEAR"}])
    slot = parse(bad_temp).slots[0]
    assert slot.temperature is None
    assert slot.weather_condition == "CLEAR"

    no_temp = _tide_weather_raw(weather=[without(weather_entry(T), "temperature")])
    assert parse(no_temp).slots[0].temperature is None


@pytest.mark.parametrize(
    "units_tides",
    [{"tideHeight": "XX"}, _MISSING, "FT"],
    ids=["unknown", "omitted", "not_a_dict"],
)
def test_tc069_optional_tide_unit(units_tides):
    """TC-69: AC-5.7, an unknown or missing tide unit drops all tide data."""
    raw = pipeline_raw()
    if units_tides is _MISSING:
        del raw.units_tides
    else:
        raw.units_tides = units_tides
    forecast = parse(raw)
    assert all(s.tide_height is None for s in forecast.slots)


@pytest.mark.parametrize(
    "units_weather", [{"temperature": "XX"}, _MISSING], ids=["unknown", "omitted"]
)
def test_tc069_optional_temperature_unit(units_weather):
    """TC-69: AC-5.7, an unknown or missing temperature unit drops temperatures only."""
    raw = pipeline_raw()
    if units_weather is _MISSING:
        del raw.units_weather
    else:
        raw.units_weather = units_weather
    forecast = parse(raw)
    assert all(s.temperature is None for s in forecast.slots)
    assert all(s.weather_condition == "MOSTLY_CLEAR" for s in forecast.slots)


def test_tc070_slots_sorted():
    """TC-70: AC-5.8, slots are sorted by ascending timestamp."""
    raw = make_raw(wave=[wave_entry(T + 7200), wave_entry(T), wave_entry(T + 3600)])
    assert tuple(s.timestamp for s in parse(raw).slots) == (T, T + 3600, T + 7200)


_BASE_BAD = wave_entry(T + 3600)

BAD_WAVE_ENTRIES = [
    ("missing_surf_max", without(_BASE_BAD, "surf_max")),
    ("missing_surf_min", without(_BASE_BAD, "surf_min")),
    ("missing_timestamp", without(_BASE_BAD, "timestamp")),
    ("timestamp_bool", with_(_BASE_BAD, timestamp=True)),
    ("timestamp_float", with_(_BASE_BAD, timestamp=1791392400.0)),
    ("timestamp_str", with_(_BASE_BAD, timestamp="1791392400")),
    ("missing_utcOffset", without(_BASE_BAD, "utcOffset")),
    ("utcOffset_str", with_(_BASE_BAD, utcOffset="-10")),
    ("utcOffset_bool", with_(_BASE_BAD, utcOffset=True)),
    ("surf_min_str", with_(_BASE_BAD, surf_min="2")),
    ("surf_max_none", with_(_BASE_BAD, surf_max=None)),
    ("entry_str", "x"),
    ("entry_none", None),
]


@pytest.mark.parametrize(
    "bad", [b for _, b in BAD_WAVE_ENTRIES], ids=[n for n, _ in BAD_WAVE_ENTRIES]
)
def test_tc071_malformed_wave_entry(bad):
    """TC-71: AC-5.9, a malformed wave entry raises 'malformed forecast data for Pipeline: ...'."""
    raw = make_raw(wave=[wave_entry(T), bad])
    assert surfline_error(raw).startswith("malformed forecast data for Pipeline: ")


@pytest.mark.parametrize(
    "rating, expected",
    [
        ({"key": "FAIR", "value": 3}, 3),
        ({"key": "FAIR"}, None),
        ({"key": "FAIR", "value": 3.0}, None),
        ({"key": "FAIR", "value": True}, None),
        ({"key": "FAIR", "value": "3"}, None),
        ("FAIR", None),
        (_MISSING, None),
    ],
    ids=["int", "no_value", "float", "bool", "str", "not_dict", "missing"],
)
def test_tc072_surf_rating(rating, expected):
    """TC-72: a matched rating's `value` is kept only when it is an int (not bool)."""
    entry = rating_entry(T)
    if rating is _MISSING:
        del entry["rating"]
    else:
        entry["rating"] = rating
    value = parse(make_raw(rating=[entry])).slots[0].surf_rating
    assert value == expected
    if expected is not None:
        assert type(value) is int


def test_tc217_rating_matching():
    """TC-217: ratings use the nearest-entry rule; no rating data means surf_rating is None."""
    near = parse(make_raw(rating=[rating_entry(T - 3600, 1), rating_entry(T + 1800, 5)])).slots[0]
    assert near.surf_rating == 5

    tie = parse(make_raw(rating=[rating_entry(T + 1800, 5), rating_entry(T - 1800, 2)])).slots[0]
    assert tie.surf_rating == 2

    far = parse(make_raw(rating=[rating_entry(T + 3601, 5)])).slots[0]
    assert far.surf_rating is None

    assert parse(make_raw()).slots[0].surf_rating is None
    assert parse(make_raw(rating="junk")).slots[0].surf_rating is None


REF_WINDOW = DaylightWindow(date(2026, 10, 7), DAWN, DUSK)
_REF_SUN = REF_SUNLIGHT[0]


@pytest.mark.parametrize(
    "sunlight, expected",
    [
        (REF_SUNLIGHT, (REF_WINDOW,)),
        (
            [
                dict(_REF_SUN),
                without(_REF_SUN, "dawn"),
                "junk",
                with_(_REF_SUN, midnight="x"),
                without(_REF_SUN, "midnightUTCOffset"),
                with_(_REF_SUN, midnight=True),
                with_(_REF_SUN, midnightUTCOffset=True),
                with_(_REF_SUN, dawn=1791389400.0),
                with_(
                    _REF_SUN,
                    midnight=_REF_SUN["midnight"] + 86400,
                    dawn=_REF_SUN["dawn"] + 86400,
                    dusk=_REF_SUN["dusk"] + 86400,
                ),
            ],
            (REF_WINDOW, DaylightWindow(date(2026, 10, 8), DAWN + 86400, DUSK + 86400)),
        ),
        (
            [{"midnight": 1791331200 - 19800, "midnightUTCOffset": 5.5, "dawn": 1, "dusk": 2}],
            (DaylightWindow(date(2026, 10, 7), 1, 2),),
        ),
        (None, ()),
    ],
    ids=["reference", "mixed_valid_and_invalid", "fractional_offset", "none"],
)
def test_tc073_daylight(sunlight, expected):
    """TC-73: spec 4.5 step 7, daylight windows from sunlightTimes; invalid entries skipped."""
    raw = make_raw(sunlightTimes=copy.deepcopy(sunlight))
    daylight = parse(raw).daylight
    assert daylight == expected
    assert isinstance(daylight, tuple)


@pytest.mark.parametrize("offset, expected", [(-10, -10.0), (5.5, 5.5)], ids=["int", "fractional"])
def test_tc074_utc_offset_float(offset, expected):
    """TC-74: utc_offset is stored as a float."""
    slot = parse(make_raw(wave=[wave_entry(T, utc_offset=offset)])).slots[0]
    assert slot.utc_offset == expected
    assert type(slot.utc_offset) is float
