"""Unit conversion (spec 4.5, units.py): TC-44 to TC-47 and TC-206."""

from __future__ import annotations

import pytest

from surf_alert.units import convert_height, convert_speed, convert_temperature


@pytest.mark.parametrize(
    "args, expected",
    [
        ((1.0, "m", "ft"), pytest.approx(3.28084, abs=1e-9)),
        ((3.28084, "ft", "m"), pytest.approx(1.0, abs=1e-9)),
        ((1.0, "M", "FT"), pytest.approx(3.28084, abs=1e-9)),
    ],
    ids=["m_to_ft", "ft_to_m", "upper_case"],
)
def test_tc044_convert_height(args, expected):
    """TC-44: convert_height converts between ft and m in any case."""
    assert convert_height(*args) == expected


def test_tc044_convert_height_same_unit_unchanged():
    """TC-44: the same unit (ignoring case) returns the value unchanged."""
    assert convert_height(2.4255, "FT", "ft") == 2.4255


@pytest.mark.parametrize(
    "args, expected",
    [
        ((18.52, "kph", "kts"), pytest.approx(10.0, abs=1e-6)),
        ((11.50779, "mph", "kts"), pytest.approx(10.0, abs=1e-4)),
        ((10, "kts", "kph"), pytest.approx(18.52, abs=1e-9)),
        ((10, "kts", "mph"), pytest.approx(11.50779, abs=1e-9)),
        ((18.52, "KPH", "MPH"), pytest.approx(11.50779, abs=1e-6)),
    ],
    ids=["kph_to_kts", "mph_to_kts", "kts_to_kph", "kts_to_mph", "kph_to_mph_upper"],
)
def test_tc045_convert_speed(args, expected):
    """TC-45: convert_speed converts through knots, in any case."""
    assert convert_speed(*args) == expected


def test_tc045_convert_speed_same_unit_unchanged():
    """TC-45: the same unit (ignoring case) returns the value unchanged."""
    assert convert_speed(8.4, "KTS", "kts") == 8.4


@pytest.mark.parametrize(
    "args, expected",
    [
        ((68, "F", "C"), pytest.approx(20.0, abs=1e-9)),
        ((20, "C", "F"), pytest.approx(68.0, abs=1e-9)),
        ((-40, "f", "c"), pytest.approx(-40.0, abs=1e-9)),
    ],
    ids=["f_to_c", "c_to_f", "lower_case"],
)
def test_tc046_convert_temperature(args, expected):
    """TC-46: convert_temperature uses (v - 32) * 5 / 9 and v * 9 / 5 + 32."""
    assert convert_temperature(*args) == expected


def test_tc046_convert_temperature_same_unit_unchanged():
    """TC-46: the same unit (ignoring case) returns the value unchanged."""
    assert convert_temperature(24.0, "c", "C") == 24.0


@pytest.mark.parametrize(
    "func, args, msg",
    [
        (convert_height, (1.0, "yd", "ft"), "unsupported height unit: 'yd'"),
        (convert_height, (1.0, "ft", "YD"), "unsupported height unit: 'YD'"),
        (convert_speed, (1.0, "m/s", "kts"), "unsupported speed unit: 'm/s'"),
        (convert_temperature, (1.0, "C", "K"), "unsupported temperature unit: 'K'"),
    ],
    ids=["height_from", "height_to", "speed", "temperature"],
)
def test_tc047_unsupported_units(func, args, msg):
    """TC-47: an unsupported unit raises ValueError naming it exactly as given."""
    with pytest.raises(ValueError) as ei:
        func(*args)
    assert str(ei.value) == msg


@pytest.mark.parametrize(
    "func, args, msg",
    [
        (convert_height, (1, "yd", "yd"), "unsupported height unit: 'yd'"),
        (convert_height, (1, "YD", "yd"), "unsupported height unit: 'YD'"),
        (convert_speed, (5, "x", "X"), "unsupported speed unit: 'x'"),
        (convert_temperature, (1, "K", "K"), "unsupported temperature unit: 'K'"),
    ],
    ids=["height_same", "height_same_ignoring_case", "speed_same", "temperature_same"],
)
def test_tc206_units_validated_before_shortcut(func, args, msg):
    """TC-206: both units are validated before the same-unit shortcut."""
    with pytest.raises(ValueError) as ei:
        func(*args)
    assert str(ei.value) == msg
