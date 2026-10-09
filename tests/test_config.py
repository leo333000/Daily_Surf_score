"""Config loading and validation (FR-1, spec 6.1): TC-10 to TC-43 and TC-202 to TC-205."""

from __future__ import annotations

import pytest

from surf_alert.config import load_config, parse_config
from surf_alert.errors import ConfigError
from surf_alert.models import Rule, Settings
from tests.scenario import (
    PIPELINE,
    REF_CONFIG_TOML,
    REF_RULES,
    REF_SETTINGS,
    SUNSET,
    ref_config_dict,
)

METRIC_MESSAGE = (
    "rules[{i}].metric must be one of: wave_height_min, wave_height_max, surf_rating, "
    "swell_height, swell_period, wind_speed, wind_gust, wind_direction_type, tide_height, "
    "temperature, weather_condition"
)
OP_MESSAGE = "rules[{i}].op must be one of: >, >=, <, <=, ==, !=, in, not_in"
SPOT_ID_MESSAGE = "spots[{i}].id must be a 24-character hexadecimal Surfline spot id"


def changed(mutate):
    """A fresh reference config dict with `mutate(d)` applied."""
    d = ref_config_dict()
    mutate(d)
    return d


def config_error(data):
    """Run parse_config(data), expect ConfigError and return its message."""
    with pytest.raises(ConfigError) as ei:
        parse_config(data)
    return str(ei.value)


def set_path(d, path, value):
    """Set d[path[0]][path[1]]... = value."""
    target = d
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def del_path(d, path):
    target = d
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]


def many_spots(n):
    return [{"name": f"Spot {i}", "id": f"{i:024x}"} for i in range(n)]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("as_str", [True, False], ids=["str", "Path"])
def test_tc010_load_reference_config(ref_config_path, as_str):
    """TC-10: AC-1.1, the reference config loads to the expected AppConfig."""
    cfg = load_config(str(ref_config_path) if as_str else ref_config_path)
    assert cfg.spots == (PIPELINE, SUNSET)
    assert cfg.rules == REF_RULES
    assert cfg.settings == REF_SETTINGS
    assert isinstance(cfg.spots, tuple)
    assert isinstance(cfg.rules, tuple)


def test_tc011_missing_file(tmp_path):
    """TC-11: AC-1.2, a missing file gives 'config file not found: <path>'."""
    path = tmp_path / "nope.toml"
    with pytest.raises(ConfigError) as ei:
        load_config(path)
    assert str(ei.value) == "config file not found: " + str(path)


def test_tc012_invalid_toml(write_config):
    """TC-12: AC-1.3, invalid TOML gives 'invalid TOML in <path>: <detail>'."""
    path = write_config("spots = [")
    with pytest.raises(ConfigError) as ei:
        load_config(path)
    prefix = f"invalid TOML in {path}: "
    assert str(ei.value).startswith(prefix)
    assert len(str(ei.value)) > len(prefix)


def test_tc013_invalid_value_through_file(write_config):
    """TC-13: AC-1.4 via a file, height_unit "yd" is rejected by load_config."""
    text = REF_CONFIG_TOML.replace('height_unit = "ft"', 'height_unit = "yd"')
    assert text != REF_CONFIG_TOML
    path = write_config(text)
    with pytest.raises(ConfigError) as ei:
        load_config(path)
    assert str(ei.value) == "settings.height_unit must be one of: ft, m"


def test_tc014_unit_case_normalized():
    """TC-14: AC-1.5, unit settings are case-insensitive and normalized."""

    def mutate(d):
        d["settings"].update(height_unit="FT", wind_unit="KPH", temperature_unit="f")

    settings = parse_config(changed(mutate)).settings
    assert settings.height_unit == "ft"
    assert settings.wind_unit == "kph"
    assert settings.temperature_unit == "F"


def test_tc015_rule_value_types():
    """TC-15: AC-1.6, numeric values become float, in-lists become tuples, strings stay strings."""

    def mutate(d):
        d["rules"] = [
            {"metric": "swell_period", "op": ">", "value": 7},
            {"metric": "wind_direction_type", "op": "in", "value": ["Offshore", "Cross-shore"]},
            {"metric": "wind_direction_type", "op": "==", "value": "Offshore"},
        ]

    rules = parse_config(changed(mutate)).rules
    assert rules[0].value == 7.0
    assert isinstance(rules[0].value, float)
    assert rules[1].value == ("Offshore", "Cross-shore")
    assert isinstance(rules[1].value, tuple)
    assert rules[2].value == "Offshore"


@pytest.mark.parametrize("variant", ["deleted", "empty"])
def test_tc016_settings_optional(variant):
    """TC-16: AC-1.7, a missing or empty [settings] table gives Settings()."""

    def mutate(d):
        if variant == "deleted":
            del d["settings"]
        else:
            d["settings"] = {}

    assert parse_config(changed(mutate)).settings == Settings()


def test_tc017_spot_name_stripped():
    """TC-17: AC-1.8, spot names are stripped."""
    cfg = parse_config(changed(lambda d: set_path(d, ["spots", 0, "name"], "  Pipeline  ")))
    assert cfg.spots[0].name == "Pipeline"


def test_tc018_valid_ranges_and_spot_ids():
    """TC-18: valid edge values round-trip; spot ids are stored as written."""
    full = {
        "height_unit": "m",
        "wind_unit": "mph",
        "temperature_unit": "F",
        "min_good_hours": 24,
        "jev_threshold": 1,
        "jev_model": "jev-1.13.0",
        "on_jev_error": "skip",
        "max_spots_in_message": 10,
        "notes": "",
    }
    cfg = parse_config(changed(lambda d: d.__setitem__("settings", full)))
    assert cfg.settings == Settings("m", "mph", "F", 24, 1.0, "jev-1.13.0", "skip", 10, "")

    low = parse_config(
        changed(
            lambda d: d["settings"].update(
                min_good_hours=1, max_spots_in_message=1, jev_threshold=0.001
            )
        )
    ).settings
    assert low.min_good_hours == 1
    assert low.max_spots_in_message == 1
    assert low.jev_threshold == 0.001

    kph = parse_config(changed(lambda d: d["settings"].update(wind_unit="kph"))).settings
    assert kph.wind_unit == "kph"

    upper = parse_config(
        changed(lambda d: set_path(d, ["spots", 0, "id"], "5842041F4E65FAD6A7708890"))
    )
    assert upper.spots[0].spot_id == "5842041F4E65FAD6A7708890"


def test_tc019_utf8_binary_read(write_config):
    """TC-19: the file is read as UTF-8 (binary mode), so non-ASCII notes survive."""
    text = REF_CONFIG_TOML.replace(
        'notes = "Only surf it when it is under 6 ft."', 'notes = "Café °"'
    )
    assert text != REF_CONFIG_TOML
    cfg = load_config(write_config(text))
    assert cfg.spots[0].notes == "Café °"


# ---------------------------------------------------------------------------
# Top level and settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["foo", "Settings"])
def test_tc020_unknown_top_level_key(key):
    """TC-20: an unknown top-level key is rejected (names are case-sensitive)."""
    d = changed(lambda d: d.__setitem__(key, 1 if key == "foo" else {}))
    assert config_error(d) == f"unknown key '{key}' in top level"


@pytest.mark.parametrize("value", [5, "ft", [1]], ids=["int", "str", "list"])
def test_tc021_settings_not_table(value):
    """TC-21: settings must be a table."""
    d = changed(lambda d: d.__setitem__("settings", value))
    assert config_error(d) == "settings must be a table"


def test_tc022_settings_unknown_key():
    """TC-22: an unknown key in settings is rejected."""
    d = changed(lambda d: d["settings"].__setitem__("height", "ft"))
    assert config_error(d) == "unknown key 'height' in settings"


TABLE_A = [
    ("height_unit", ["yd", "", 5], "settings.height_unit must be one of: ft, m"),
    ("wind_unit", ["knots", "m/s", 10], "settings.wind_unit must be one of: kts, kph, mph"),
    ("temperature_unit", ["K", "celsius", 1], "settings.temperature_unit must be one of: C, F"),
    (
        "min_good_hours",
        [0, 25, -1, 1.5, 2.0, True, "3"],
        "settings.min_good_hours must be an integer between 1 and 24",
    ),
    (
        "jev_threshold",
        [0, 0.0, -0.1, 1.01, 2, True, "0.7"],
        "settings.jev_threshold must be a number greater than 0 and at most 1",
    ),
    ("jev_model", ["", 5, ["jev-latest"]], "settings.jev_model must be a non-empty string"),
    ("on_jev_error", ["abort", "", 1], "settings.on_jev_error must be one of: rules_only, skip"),
    (
        "max_spots_in_message",
        [0, 11, 3.0, True, "3"],
        "settings.max_spots_in_message must be an integer between 1 and 10",
    ),
    ("notes", [5, True, ["x"]], "settings.notes must be a string"),
]

TABLE_A_CASES = [(key, bad, msg) for key, bads, msg in TABLE_A for bad in bads]


@pytest.mark.parametrize(
    "key, bad, msg",
    TABLE_A_CASES,
    ids=[f"{key}={bad!r}" for key, bad, _ in TABLE_A_CASES],
)
def test_tc023_settings_values(key, bad, msg):
    """TC-23: table A, each bad settings value gives its message."""
    d = changed(lambda d: d["settings"].__setitem__(key, bad))
    assert config_error(d) == msg


# ---------------------------------------------------------------------------
# Spots
# ---------------------------------------------------------------------------

_DELETE = object()


@pytest.mark.parametrize(
    "value",
    [_DELETE, [], "Pipeline", {"name": "x"}],
    ids=["missing", "empty", "str", "dict"],
)
def test_tc024_spots_list(value):
    """TC-24: spots missing, empty or not a list."""

    def mutate(d):
        if value is _DELETE:
            del d["spots"]
        else:
            d["spots"] = value

    assert config_error(changed(mutate)) == "config must define at least one [[spots]] entry"


def test_tc025_spot_not_table():
    """TC-25: a spot entry that is not a table."""
    d = changed(lambda d: set_path(d, ["spots", 1], "Sunset"))
    assert config_error(d) == "spots[1] must be a table"


def test_tc026_spot_unknown_key():
    """TC-26: an unknown key in a spot."""
    d = changed(lambda d: set_path(d, ["spots", 0, "url"], "x"))
    assert config_error(d) == "unknown key 'url' in spots[0]"


@pytest.mark.parametrize(
    "value",
    [_DELETE, 5, "", "   ", ["x"]],
    ids=["missing", "int", "empty", "blank", "list"],
)
def test_tc027_spot_name(value):
    """TC-27: spot name missing, not a str, or blank."""

    def mutate(d):
        if value is _DELETE:
            del_path(d, ["spots", 1, "name"])
        else:
            set_path(d, ["spots", 1, "name"], value)

    assert config_error(changed(mutate)) == "spots[1].name must be a non-empty string"


@pytest.mark.parametrize(
    "value",
    [
        _DELETE,
        5842041,
        "xyz",
        "5842041f4e65fad6a770889",
        "5842041f4e65fad6a77088900",
        "5842041f4e65fad6a770889g",
    ],
    ids=["missing", "int", "short_word", "23_chars", "25_chars", "non_hex"],
)
def test_tc028_spot_id(value):
    """TC-28: spot id missing, not a str, or not 24 hex characters."""

    def mutate(d):
        if value is _DELETE:
            del_path(d, ["spots", 0, "id"])
        else:
            set_path(d, ["spots", 0, "id"], value)

    assert config_error(changed(mutate)) == SPOT_ID_MESSAGE.format(i=0)


def test_tc029_spot_notes():
    """TC-29: spot notes must be a string."""
    d = changed(lambda d: set_path(d, ["spots", 1, "notes"], 5))
    assert config_error(d) == "spots[1].notes must be a string"


@pytest.mark.parametrize(
    "name, shown",
    [("  pipeline ", "pipeline"), ("PIPELINE", "PIPELINE")],
    ids=["stripped_lower", "upper"],
)
def test_tc030_duplicate_name(name, shown):
    """TC-30: duplicate names are case-insensitive; the later stripped name is shown."""
    d = changed(lambda d: set_path(d, ["spots", 1], {"name": name, "id": SUNSET.spot_id}))
    assert config_error(d) == f"duplicate spot name: {shown}"


@pytest.mark.parametrize(
    "spot_id",
    ["5842041f4e65fad6a7708890", "5842041F4E65FAD6A7708890"],
    ids=["same_case", "upper"],
)
def test_tc031_duplicate_id(spot_id):
    """TC-31: duplicate ids are case-insensitive; the later id is shown as written."""
    d = changed(lambda d: set_path(d, ["spots", 1, "id"], spot_id))
    assert config_error(d) == f"duplicate spot id: {spot_id}"


def test_tc032_spot_count_limit():
    """TC-32: 50 spots load; 51 spots are rejected."""
    cfg = parse_config(changed(lambda d: d.__setitem__("spots", many_spots(50))))
    assert len(cfg.spots) == 50
    d = changed(lambda d: d.__setitem__("spots", many_spots(51)))
    assert config_error(d) == "config may define at most 50 spots"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [_DELETE, [], "x", {}], ids=["missing", "empty", "str", "dict"]
)
def test_tc033_rules_list(value):
    """TC-33: rules missing, empty or not a list."""

    def mutate(d):
        if value is _DELETE:
            del d["rules"]
        else:
            d["rules"] = value

    assert config_error(changed(mutate)) == "config must define at least one [[rules]] entry"


def test_tc034_rule_not_table():
    """TC-34: a rule entry that is not a table."""
    d = changed(lambda d: set_path(d, ["rules", 1], 5))
    assert config_error(d) == "rules[1] must be a table"


def test_tc035_rule_unknown_key():
    """TC-35: an unknown key in a rule."""
    d = changed(lambda d: set_path(d, ["rules", 0, "unit"], "ft"))
    assert config_error(d) == "unknown key 'unit' in rules[0]"


@pytest.mark.parametrize(
    "value", [_DELETE, "wave_height", 5], ids=["missing", "unknown", "int"]
)
def test_tc036_metric(value):
    """TC-36: metric missing or unknown."""

    def mutate(d):
        if value is _DELETE:
            del_path(d, ["rules", 1, "metric"])
        else:
            set_path(d, ["rules", 1, "metric"], value)

    assert config_error(changed(mutate)) == METRIC_MESSAGE.format(i=1)


@pytest.mark.parametrize(
    "value",
    [_DELETE, "=>", "contains", "", 5],
    ids=["missing", "arrow", "contains", "empty", "int"],
)
def test_tc037_op(value):
    """TC-37: op missing or unknown."""

    def mutate(d):
        if value is _DELETE:
            del_path(d, ["rules", 0, "op"])
        else:
            set_path(d, ["rules", 0, "op"], value)

    assert config_error(changed(mutate)) == OP_MESSAGE.format(i=0)


@pytest.mark.parametrize(
    "metric, op, value",
    [
        ("wave_height_min", "in", ["2"]),
        ("swell_period", "not_in", ["7"]),
        ("wind_direction_type", ">", "Offshore"),
        ("weather_condition", "<=", "CLEAR"),
    ],
)
def test_tc038_op_kind(metric, op, value):
    """TC-38: an op that is not allowed for the metric kind."""
    d = changed(lambda d: set_path(d, ["rules", 0], {"metric": metric, "op": op, "value": value}))
    assert config_error(d) == f"rules[0].op '{op}' is not valid for metric '{metric}'"


@pytest.mark.parametrize(
    "value", [_DELETE, "2", True, [2]], ids=["missing", "str", "bool", "list"]
)
def test_tc039_numeric_value(value):
    """TC-39: a numeric metric needs a number (not bool)."""

    def mutate(d):
        if value is _DELETE:
            del_path(d, ["rules", 0, "value"])
        else:
            set_path(d, ["rules", 0, "value"], value)

    assert config_error(changed(mutate)) == "rules[0].value must be a number"


def _rule0(metric, op, value=_DELETE):
    rule = {"metric": metric, "op": op}
    if value is not _DELETE:
        rule["value"] = value
    return lambda d: set_path(d, ["rules", 0], rule)


@pytest.mark.parametrize(
    "mutate",
    [
        _rule0("wind_direction_type", "=="),
        _rule0("wind_direction_type", "==", ""),
        _rule0("wind_direction_type", "==", 5),
        _rule0("wind_direction_type", "==", ["Offshore"]),
        _rule0("wind_direction_type", "==", True),
        _rule0("weather_condition", "!=", 5),
    ],
    ids=["missing", "empty", "int", "list", "bool", "weather_ne_int"],
)
def test_tc040_text_eq_value(mutate):
    """TC-40: text ==/!= rules need a non-empty string."""
    assert config_error(changed(mutate)) == "rules[0].value must be a non-empty string"


@pytest.mark.parametrize(
    "mutate",
    [
        _rule0("wind_direction_type", "in"),
        _rule0("wind_direction_type", "in", []),
        _rule0("wind_direction_type", "in", "Offshore"),
        _rule0("wind_direction_type", "in", ["Offshore", 5]),
        _rule0("wind_direction_type", "not_in", ["Offshore", ""]),
    ],
    ids=["missing", "empty_list", "str", "mixed_types", "not_in_empty_item"],
)
def test_tc041_text_in_value(mutate):
    """TC-41: text in/not_in rules need a non-empty list of non-empty strings."""
    assert (
        config_error(changed(mutate))
        == "rules[0].value must be a non-empty list of non-empty strings"
    )


def test_tc042_valid_rules():
    """TC-42: a mix of valid rules parses to the expected Rule tuple."""

    def mutate(d):
        d["rules"] = [
            {"metric": "wind_direction_type", "op": "in", "value": ["Offshore", "Cross-shore"]},
            {"metric": "weather_condition", "op": "!=", "value": "RAIN"},
            {"metric": "wind_direction_type", "op": "not_in", "value": ["Onshore"]},
            {"metric": "wind_speed", "op": "<=", "value": 15},
            {"metric": "surf_rating", "op": "==", "value": 3},
            {"metric": "wave_height_max", "op": "!=", "value": 2.5},
        ]

    rules = parse_config(changed(mutate)).rules
    assert rules == (
        Rule("wind_direction_type", "in", ("Offshore", "Cross-shore")),
        Rule("weather_condition", "!=", "RAIN"),
        Rule("wind_direction_type", "not_in", ("Onshore",)),
        Rule("wind_speed", "<=", 15.0),
        Rule("surf_rating", "==", 3.0),
        Rule("wave_height_max", "!=", 2.5),
    )
    assert isinstance(rules, tuple)
    for index in (3, 4, 5):
        assert type(rules[index].value) is float


# ---------------------------------------------------------------------------
# Validation order (table B)
# ---------------------------------------------------------------------------


def _b_spot0(**fields):
    def mutate(d):
        d["spots"][0].update(fields)

    return mutate


def _b_both(*mutators):
    def mutate(d):
        for m in mutators:
            m(d)

    return mutate


def _b_51_spots_dup(d):
    spots = many_spots(51)
    spots[50]["name"] = "Spot 0"
    d["spots"] = spots


TABLE_B = [
    (
        "top_level_before_settings",
        _b_both(lambda d: d.__setitem__("foo", 1), lambda d: d.__setitem__("settings", 5)),
        "unknown key 'foo' in top level",
    ),
    (
        "settings_unknown_before_values",
        lambda d: d.__setitem__("settings", {"bogus": 1, "height_unit": "yd"}),
        "unknown key 'bogus' in settings",
    ),
    (
        "settings_table_order_not_dict_order",
        lambda d: d.__setitem__("settings", {"wind_unit": "x", "height_unit": "yd"}),
        "settings.height_unit must be one of: ft, m",
    ),
    (
        "min_good_hours_before_notes",
        lambda d: d.__setitem__("settings", {"notes": 5, "min_good_hours": 0}),
        "settings.min_good_hours must be an integer between 1 and 24",
    ),
    (
        "jev_threshold_before_max_spots",
        lambda d: d.__setitem__("settings", {"max_spots_in_message": 0, "jev_threshold": 0}),
        "settings.jev_threshold must be a number greater than 0 and at most 1",
    ),
    (
        "settings_before_spots",
        _b_both(
            lambda d: d["settings"].__setitem__("height_unit", "yd"),
            lambda d: d.__setitem__("spots", []),
        ),
        "settings.height_unit must be one of: ft, m",
    ),
    (
        "spot_unknown_key_before_name",
        _b_spot0(url="x", name=""),
        "unknown key 'url' in spots[0]",
    ),
    ("spot_name_before_id", _b_spot0(name="", id="x"), "spots[0].name must be a non-empty string"),
    ("spot_id_before_notes", _b_spot0(id="x", notes=5), SPOT_ID_MESSAGE.format(i=0)),
    (
        "duplicate_name_before_duplicate_id",
        lambda d: set_path(d, ["spots", 1], {"name": "Pipeline", "id": "5842041f4e65fad6a7708890"}),
        "duplicate spot name: Pipeline",
    ),
    ("duplicate_before_count", _b_51_spots_dup, "duplicate spot name: Spot 0"),
    (
        "count_before_rules",
        _b_both(
            lambda d: d.__setitem__("spots", many_spots(51)),
            lambda d: d.__setitem__("rules", []),
        ),
        "config may define at most 50 spots",
    ),
    (
        "spot_id_before_rules",
        _b_both(_b_spot0(id="x"), lambda d: d.__setitem__("rules", [])),
        SPOT_ID_MESSAGE.format(i=0),
    ),
    (
        "spots_before_rules",
        _b_both(lambda d: d.__setitem__("spots", []), lambda d: d.__setitem__("rules", [])),
        "config must define at least one [[spots]] entry",
    ),
    (
        "rule_unknown_key_before_metric",
        lambda d: d["rules"][0].update(x=1, metric="nope"),
        "unknown key 'x' in rules[0]",
    ),
    (
        "metric_before_op",
        lambda d: d["rules"][0].update(metric="nope", op="=>"),
        METRIC_MESSAGE.format(i=0),
    ),
    (
        "op_before_value",
        lambda d: d["rules"][0].update(op="=>", value="x"),
        OP_MESSAGE.format(i=0),
    ),
    (
        "op_kind_before_value",
        lambda d: set_path(d, ["rules", 0], {"metric": "wave_height_min", "op": "in", "value": "x"}),
        "rules[0].op 'in' is not valid for metric 'wave_height_min'",
    ),
    (
        "rules_in_order",
        _b_both(
            lambda d: d["rules"][1].__setitem__("value", "2"),
            lambda d: d["rules"].append({"metric": "wind_speed", "op": "=>", "value": 1}),
        ),
        "rules[1].value must be a number",
    ),
]


@pytest.mark.parametrize(
    "mutate, msg",
    [(m, msg) for _, m, msg in TABLE_B],
    ids=[name for name, _, _ in TABLE_B],
)
def test_tc043_validation_order(mutate, msg):
    """TC-43: table B, with several errors present the first one in spec 6.1 order is reported."""
    assert config_error(changed(mutate)) == msg


# ---------------------------------------------------------------------------
# Spec 6.1 further rules
# ---------------------------------------------------------------------------


def test_tc202_unreadable_path(tmp_path, run_cli, cli_fakes):
    """TC-202: a path that exists but cannot be read gives 'cannot read config file: <path>'."""
    path = tmp_path / "cfgdir"
    path.mkdir()
    with pytest.raises(ConfigError) as ei:
        load_config(path)
    assert str(ei.value) == "cannot read config file: " + str(path)

    result = run_cli("spots", config=path)
    assert result.err == f"error: cannot read config file: {path}\n"
    assert result.rc == 1
    assert result.out == ""
    assert cli_fakes.no_factory_called()


CASE_ERRORS = [
    ("on_jev_error_upper", lambda d: d["settings"].update(on_jev_error="SKIP"),
     "settings.on_jev_error must be one of: rules_only, skip"),
    ("on_jev_error_mixed", lambda d: d["settings"].update(on_jev_error="Rules_Only"),
     "settings.on_jev_error must be one of: rules_only, skip"),
    ("metric_mixed", lambda d: d["rules"][0].update(metric="Swell_Period"), METRIC_MESSAGE.format(i=0)),
    ("metric_upper", lambda d: d["rules"][0].update(metric="WAVE_HEIGHT_MIN"), METRIC_MESSAGE.format(i=0)),
    ("op_IN", _rule0("wind_direction_type", "IN", ["Offshore"]), OP_MESSAGE.format(i=0)),
    ("op_NOT_IN", _rule0("wind_direction_type", "NOT_IN", ["Onshore"]), OP_MESSAGE.format(i=0)),
]


@pytest.mark.parametrize(
    "mutate, msg",
    [(m, msg) for _, m, msg in CASE_ERRORS],
    ids=[name for name, _, _ in CASE_ERRORS],
)
def test_tc203_case_sensitive_names(mutate, msg):
    """TC-203: on_jev_error, metric and op must match exactly (case-sensitive)."""
    assert config_error(changed(mutate)) == msg


def test_tc203_unit_case_contrast():
    """TC-203: contrast case, the three unit settings are case-insensitive."""
    cfg = parse_config(
        changed(lambda d: d["settings"].update(height_unit="Ft", wind_unit="Kts", temperature_unit="c"))
    )
    assert cfg.settings.height_unit == "ft"
    assert cfg.settings.wind_unit == "kts"
    assert cfg.settings.temperature_unit == "C"


BLANK_ERRORS = [
    ("jev_model_blank", lambda d: d["settings"].update(jev_model="   "),
     "settings.jev_model must be a non-empty string"),
    ("eq_blank", _rule0("wind_direction_type", "==", "   "), "rules[0].value must be a non-empty string"),
    ("ne_tab", _rule0("weather_condition", "!=", "\t"), "rules[0].value must be a non-empty string"),
    ("in_blank_item", _rule0("wind_direction_type", "in", ["Offshore", "  "]),
     "rules[0].value must be a non-empty list of non-empty strings"),
]


@pytest.mark.parametrize(
    "mutate, msg",
    [(m, msg) for _, m, msg in BLANK_ERRORS],
    ids=[name for name, _, _ in BLANK_ERRORS],
)
def test_tc204_blank_strings_invalid(mutate, msg):
    """TC-204: blank jev_model and blank text rule values are invalid."""
    assert config_error(changed(mutate)) == msg


def test_tc204_valid_values_stored_as_written():
    """TC-204: valid values keep surrounding whitespace (only spot names are stripped)."""

    def mutate(d):
        d["settings"]["jev_model"] = " jev-x "
        d["settings"]["notes"] = "  hi  "
        d["spots"][1]["notes"] = " n "
        d["rules"] = [
            {"metric": "wind_direction_type", "op": "==", "value": " Offshore "},
            {"metric": "wind_direction_type", "op": "in", "value": [" Offshore", "Cross-shore "]},
        ]

    cfg = parse_config(changed(mutate))
    assert cfg.settings.jev_model == " jev-x "
    assert cfg.rules[0].value == " Offshore "
    assert cfg.rules[1].value == (" Offshore", "Cross-shore ")
    assert cfg.settings.notes == "  hi  "
    assert cfg.spots[1].notes == " n "


def _add_keys(path_getter, first, second):
    def mutate(d):
        target = path_getter(d)
        target[first] = 1
        target[second] = 2

    return mutate


UNKNOWN_ORDER = [
    ("top_level", _add_keys(lambda d: d, "zzz", "aaa"), "unknown key 'zzz' in top level"),
    ("settings", _add_keys(lambda d: d["settings"], "zeta", "alpha"), "unknown key 'zeta' in settings"),
    ("spot", _add_keys(lambda d: d["spots"][0], "zeta", "alpha"), "unknown key 'zeta' in spots[0]"),
    ("rule", _add_keys(lambda d: d["rules"][0], "zeta", "alpha"), "unknown key 'zeta' in rules[0]"),
]


@pytest.mark.parametrize(
    "mutate, msg",
    [(m, msg) for _, m, msg in UNKNOWN_ORDER],
    ids=[name for name, _, _ in UNKNOWN_ORDER],
)
def test_tc205_first_unknown_key_in_file_order(mutate, msg):
    """TC-205: with several unknown keys, the first in file (dict) order is reported."""
    assert config_error(changed(mutate)) == msg


def test_tc205_first_unknown_key_from_file(write_config):
    """TC-205: file variant, `beta` then `alpha` under [settings] reports `beta`."""
    anchor = 'notes = "Intermediate surfer; prefer offshore or light winds."   # default ""\n'
    assert anchor in REF_CONFIG_TOML
    text = REF_CONFIG_TOML.replace(anchor, anchor + "beta = 1\nalpha = 2\n")
    with pytest.raises(ConfigError) as ei:
        load_config(write_config(text))
    assert str(ei.value) == "unknown key 'beta' in settings"
