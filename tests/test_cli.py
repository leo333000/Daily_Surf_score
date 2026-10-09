"""Command line through cli.main (FR-2, FR-3, FR-14 to FR-17, spec 4.12): TC-160 to TC-192, TC-210 to TC-212."""

from __future__ import annotations

import copy
import csv
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest
import requests

import surf_alert.cli as cli
from surf_alert.errors import JevError, NotificationError, SurflineError
from surf_alert.jev import JevClient
from surf_alert.models import Settings
from surf_alert.notify import CallMeBotNotifier
from surf_alert.surfline import SurflineClient
from tests.fakes import FakeForecastSource, FakeJev, FakeNotifier, FakeResponse, FakeSession
from tests.scenario import (
    NO_GOOD_SPOTS_LINE,
    PHONE_ERROR,
    PIPELINE,
    PIPELINE_ONLY_MESSAGE_90,
    REF_CONDITIONS_OUTPUT,
    REF_CONFIG_TOML,
    REF_JEV_RESPONSE,
    REF_MESSAGE,
    REF_MESSAGE_RULES_ONLY,
    REF_QUESTIONS,
    REF_SETTINGS,
    REF_STATE,
    SUNSET,
    SUNSET_BLOCK,
    SUNSET_ONLY_MESSAGE_81,
    TEST_MESSAGE_TEXT,
    pipeline_forecast,
    sunset_forecast,
)

SPOTS_OUT = "1. Pipeline (5842041f4e65fad6a7708890)\n2. Sunset Beach (0123456789abcdef01234567)\n"
RULES_OUT = "1. wave_height_min > 2 ft\n2. swell_period > 7 s\n"
SUNSET_SECTION = SUNSET_BLOCK + "\nRules: PASS (good hours today: 5, required: 1)\n"
NO_WAVE = "no wave data returned for Pipeline"
NO_WAVE_WARNING = f"warning: Pipeline: {NO_WAVE}\n"
MISSING = "error: missing environment variable(s): "
CALLMEBOT_500 = "CallMeBot returned HTTP 500: x"


def source(pipeline=None, sunset=None):
    return FakeForecastSource(
        {
            "Pipeline": pipeline if pipeline is not None else pipeline_forecast(),
            "Sunset Beach": sunset if sunset is not None else sunset_forecast(),
        }
    )


def noul(*probabilities):
    return {f"good_spot_{i}": {"type": "noul", "noul": p} for i, p in enumerate(probabilities, start=1)}


ENV_HELP = {
    "TYPESAFE_API_KEY": "your Jev API key, from console.typesafe.ai",
    "WHATSAPP_PHONE": "your WhatsApp number in international format, e.g. +34612345678",
    "CALLMEBOT_API_KEY": "the key CallMeBot sends you after activation (see README)",
}


def missing_no_file(names, env_file):
    """The missing-variable error when `env_file` does not exist."""
    help_lines = "".join(f"  {name}: {ENV_HELP[name]}\n" for name in names.split(", "))
    return (
        MISSING + names + "\n" + help_lines
        + f"No env file was found at {env_file}. Create it with: cp .env.example {env_file}\n"
    )


def set_env(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


# ---------------------------------------------------------------------------
# spots, rules, defaults and usage
# ---------------------------------------------------------------------------


def test_tc160_spots(run_cli, cli_fakes):
    """TC-160: AC-2.1, `spots` lists the configured spots without calling any factory."""
    result = run_cli("spots")
    assert result.out == SPOTS_OUT
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.no_factory_called()


CONFIG_PROBLEMS = ["missing", "invalid_toml", "bad_value"]
COMMANDS = [("spots",), ("rules",), ("conditions",), ("check",), ("check", "--dry-run")]


@pytest.mark.parametrize("problem", CONFIG_PROBLEMS)
@pytest.mark.parametrize("command", COMMANDS, ids=[" ".join(c) for c in COMMANDS])
def test_tc161_config_errors(run_cli, cli_fakes, secrets_env, tmp_path, write_config, command, problem):
    """TC-161: AC-2.2 and spec 4.12 rule 5, every ConfigError prints `error: ...` and returns 1."""
    if problem == "missing":
        path = tmp_path / "absent.toml"
    elif problem == "invalid_toml":
        path = write_config("spots = [")
    else:
        path = write_config(REF_CONFIG_TOML.replace('height_unit = "ft"', 'height_unit = "yd"'))

    result = run_cli(*command, config=path)

    if problem == "missing":
        assert result.err == f"error: config file not found: {path}\n"
    elif problem == "invalid_toml":
        assert result.err.startswith(f"error: invalid TOML in {path}: ")
        assert result.err.endswith("\n")
    else:
        assert result.err == "error: settings.height_unit must be one of: ft, m\n"
    assert result.out == ""
    assert result.rc == 1
    assert cli_fakes.no_factory_called()


def test_tc162_rules(run_cli, cli_fakes, write_config):
    """TC-162: AC-3.1, `rules` lists the rules with describe_rule."""
    result = run_cli("rules")
    assert result.out == RULES_OUT
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.no_factory_called()

    head = REF_CONFIG_TOML.split("[[rules]]")[0]
    text = head + (
        "[[rules]]\n"
        'metric = "wind_direction_type"\n'
        'op = "in"\n'
        'value = ["Offshore", "Cross-shore"]\n'
        "\n"
        "[[rules]]\n"
        'metric = "wind_speed"\n'
        'op = "<="\n'
        "value = 15\n"
    )
    other = run_cli("rules", config=write_config(text))
    assert other.out == "1. wind_direction_type in [Offshore, Cross-shore]\n2. wind_speed <= 15 kts\n"
    assert other.rc == 0


def test_tc163_default_paths(tmp_path, monkeypatch, capsys, cli_fakes):
    """TC-163: the defaults are surf_alert.toml and .env in the current directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "surf_alert.toml").write_text(REF_CONFIG_TOML, encoding="utf-8")
    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=fromdefault\n", encoding="utf-8")

    assert cli.main(["spots"]) == 0
    assert capsys.readouterr().out == SPOTS_OUT

    assert cli.main(["check", "--dry-run"]) == 0
    assert cli_fakes.jev_calls[0][0] == "fromdefault"


def _usage_cases(p):
    return [
        [],
        ["--config", p, "fly"],
        ["--config", p, "spots", "--bogus"],
        ["--config", p, "check", "--dry-run", "extra"],
    ]


@pytest.mark.parametrize("case", range(4), ids=["no_command", "unknown_command", "unknown_option", "extra_arg"])
def test_tc164_usage_errors(tmp_path, monkeypatch, capsys, cli_fakes, ref_config_path, case):
    """TC-164: usage errors raise SystemExit(2) with a usage message on stderr."""
    monkeypatch.chdir(tmp_path)
    argv = _usage_cases(str(ref_config_path))[case]
    with pytest.raises(SystemExit) as ei:
        cli.main(argv)
    assert ei.value.code == 2
    assert "usage:" in capsys.readouterr().err
    assert cli_fakes.no_factory_called()


def test_tc165_argv_none_and_verbose(monkeypatch, capsys, run_cli, cli_fakes, ref_config_path, missing_env_path):
    """TC-165: main() reads sys.argv[1:]; -v does not change normal output."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["surf_alert", "--config", str(ref_config_path), "--env-file", str(missing_env_path), "spots"],
    )
    capsys.readouterr()
    assert cli.main() == 0
    assert capsys.readouterr().out == SPOTS_OUT

    verbose = run_cli("-v", "spots")
    assert verbose.out == SPOTS_OUT
    assert verbose.rc == 0


# ---------------------------------------------------------------------------
# conditions
# ---------------------------------------------------------------------------


def test_tc166_conditions(run_cli, cli_fakes):
    """TC-166: AC-15.1, `conditions` prints one section per spot and needs no secrets."""
    result = run_cli("conditions")
    assert result.out == REF_CONDITIONS_OUTPUT
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.surfline_calls == [REF_SETTINGS]
    assert cli_fakes.surfline.fetched == [PIPELINE, SUNSET]
    assert cli_fakes.jev_calls == []
    assert cli_fakes.notifier_calls == []


@pytest.mark.parametrize("name", ["sunset beach", "SUNSET BEACH"])
def test_tc167_conditions_one_spot(run_cli, cli_fakes, name):
    """TC-167: AC-15.2, --spot matches the name ignoring case."""
    result = run_cli("conditions", "--spot", name)
    assert cli_fakes.surfline.fetched == [SUNSET]
    assert result.out == SUNSET_SECTION
    assert result.rc == 0


def test_tc168_conditions_unknown_spot(run_cli, cli_fakes):
    """TC-168: AC-15.3, an unknown --spot is an error and nothing is fetched."""
    result = run_cli("conditions", "--spot", "Nowhere")
    assert result.err == "error: unknown spot: Nowhere\n"
    assert result.out == ""
    assert result.rc == 1
    assert cli_fakes.surfline_calls == []
    assert cli_fakes.surfline.fetched == []


def test_tc169_conditions_failures(run_cli, cli_fakes):
    """TC-169: AC-15.4, failed fetches print warnings; other spots still print; exit 3."""
    boom = "failed to fetch forecast for Pipeline: boom"
    cli_fakes.surfline = source(pipeline=SurflineError(boom))
    result = run_cli("conditions")
    assert result.err == f"warning: Pipeline: {boom}\n"
    assert result.out == SUNSET_SECTION
    assert result.rc == 3

    sunset_boom = "failed to fetch forecast for Sunset Beach: boom"
    cli_fakes.surfline = source(pipeline=SurflineError(boom), sunset=SurflineError(sunset_boom))
    both = run_cli("conditions")
    assert both.out == ""
    assert both.err == f"warning: Pipeline: {boom}\nwarning: Sunset Beach: {sunset_boom}\n"
    assert both.rc == 3


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["no_candidates", "all_rejected"])
def test_tc170_nothing_to_send(run_cli, cli_fakes, secrets_env, scenario):
    """TC-170: AC-14.1 and AC-14.3, nothing to send prints the 'No good spots' line and returns 0."""
    if scenario == "no_candidates":
        cli_fakes.surfline = source(
            pipeline=pipeline_forecast(good_hours=()), sunset=sunset_forecast(good_hours=())
        )
    else:
        cli_fakes.jev = FakeJev(answers=noul(0.5, 0.3))
    result = run_cli("check")
    assert result.out == NO_GOOD_SPOTS_LINE
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.notifier.sent == []
    if scenario == "no_candidates":
        assert cli_fakes.jev.calls == []


def test_tc171_one_spot_sent(run_cli, cli_fakes, secrets_env):
    """TC-171: AC-14.2, one approved spot is sent."""
    cli_fakes.surfline = source(sunset=sunset_forecast(good_hours=()))
    cli_fakes.jev = FakeJev(answers=noul(0.9))
    result = run_cli("check")
    assert result.out == "Notification sent: 1 spot(s) recommended.\n"
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.notifier.sent == [PIPELINE_ONLY_MESSAGE_90]


def test_tc172_reference_check(run_cli, cli_fakes, secrets_env):
    """TC-172: the reference `check` run wires settings and secrets into the factories."""
    result = run_cli("check")
    assert result.out == "Notification sent: 2 spot(s) recommended.\n"
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.notifier.sent == [REF_MESSAGE]
    assert cli_fakes.jev.calls == [(REF_STATE, REF_QUESTIONS)]
    assert cli_fakes.surfline_calls == [REF_SETTINGS]
    assert cli_fakes.jev_calls == [("ts-key", REF_SETTINGS)]
    assert cli_fakes.notifier_calls == [("+34612345678", "cmb-key")]


def test_tc173_one_spot_fails(run_cli, cli_fakes, secrets_env):
    """TC-173: AC-14.4, a failed spot is a warning and the run exits 3."""
    cli_fakes.surfline = source(pipeline=SurflineError(NO_WAVE))
    cli_fakes.jev = FakeJev(answers=noul(0.81))
    result = run_cli("check")
    assert result.err == NO_WAVE_WARNING
    assert result.out == "Notification sent: 1 spot(s) recommended.\n"
    assert result.rc == 3


def test_tc174_all_spots_fail(run_cli, cli_fakes, secrets_env):
    """TC-174: AC-14.5, every spot fails: two warnings, no Jev call, nothing sent, exit 3."""
    cli_fakes.surfline = source(
        pipeline=SurflineError("failed to fetch forecast for Pipeline: boom"),
        sunset=SurflineError("failed to fetch forecast for Sunset Beach: boom"),
    )
    result = run_cli("check")
    assert result.out == NO_GOOD_SPOTS_LINE
    assert result.err == (
        "warning: Pipeline: failed to fetch forecast for Pipeline: boom\n"
        "warning: Sunset Beach: failed to fetch forecast for Sunset Beach: boom\n"
    )
    assert result.rc == 3
    assert cli_fakes.jev.calls == []
    assert cli_fakes.notifier.sent == []


def test_tc175_jev_failure(run_cli, cli_fakes, secrets_env, write_config):
    """TC-175: AC-14.6, the Jev warning comes after spot warnings; skip mode sends nothing."""
    cli_fakes.jev = FakeJev(exc=JevError("service overloaded (HTTP 529)"))
    a = run_cli("check")
    assert a.err == "warning: Jev request failed: service overloaded (HTTP 529)\n"
    assert a.out == "Notification sent: 2 spot(s) recommended.\n"
    assert cli_fakes.notifier.sent == [REF_MESSAGE_RULES_ONLY]
    assert a.rc == 3

    cli_fakes.surfline = source(pipeline=SurflineError(NO_WAVE))
    cli_fakes.jev = FakeJev(exc=JevError("rate limited (HTTP 429)"))
    b = run_cli("check")
    assert b.err == NO_WAVE_WARNING + "warning: Jev request failed: rate limited (HTTP 429)\n"
    assert b.out == "Notification sent: 1 spot(s) recommended.\n"
    assert b.rc == 3

    cli_fakes.surfline = source()
    cli_fakes.jev = FakeJev(exc=JevError("service overloaded (HTTP 529)"))
    skip_config = write_config(
        REF_CONFIG_TOML.replace('on_jev_error = "rules_only"', 'on_jev_error = "skip"'), "skip.toml"
    )
    c = run_cli("check", config=skip_config)
    assert c.err == "warning: Jev request failed: service overloaded (HTTP 529)\n"
    assert c.out == NO_GOOD_SPOTS_LINE
    assert c.rc == 3


def test_tc176_send_fails(run_cli, cli_fakes, secrets_env):
    """TC-176: AC-14.7, a send failure prints an error and returns 4."""
    cli_fakes.notifier = FakeNotifier(exc=NotificationError(CALLMEBOT_500))
    result = run_cli("check")
    assert result.err == f"error: failed to send WhatsApp message: {CALLMEBOT_500}\n"
    assert result.out == ""
    assert result.rc == 4

    cli_fakes.surfline = source(pipeline=SurflineError(NO_WAVE))
    cli_fakes.jev = FakeJev(answers=noul(0.81))
    cli_fakes.notifier = FakeNotifier(exc=NotificationError(CALLMEBOT_500))
    degraded = run_cli("check")
    assert degraded.err == NO_WAVE_WARNING + f"error: failed to send WhatsApp message: {CALLMEBOT_500}\n"
    assert degraded.rc == 4


def test_tc177_dry_run(run_cli, cli_fakes, monkeypatch):
    """TC-177: AC-14.8, --dry-run prints the message and needs only TYPESAFE_API_KEY."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    result = run_cli("check", "--dry-run")
    assert result.out == REF_MESSAGE + "\n"
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.notifier_calls == []


@pytest.mark.parametrize("scenario", ["jev_529", "pipeline_failure", "no_candidates"])
def test_tc178_dry_run_degraded(run_cli, cli_fakes, monkeypatch, scenario):
    """TC-178: AC-14.8, dry runs report degraded states like normal runs."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    if scenario == "jev_529":
        cli_fakes.jev = FakeJev(exc=JevError("service overloaded (HTTP 529)", 529))
        result = run_cli("check", "--dry-run")
        assert result.out == REF_MESSAGE_RULES_ONLY + "\n"
        assert result.err == "warning: Jev request failed: service overloaded (HTTP 529)\n"
        assert result.rc == 3
    elif scenario == "pipeline_failure":
        cli_fakes.surfline = source(pipeline=SurflineError(NO_WAVE))
        cli_fakes.jev = FakeJev(answers=noul(0.81))
        result = run_cli("check", "--dry-run")
        assert result.out == SUNSET_ONLY_MESSAGE_81 + "\n"
        assert result.err == NO_WAVE_WARNING
        assert result.rc == 3
    else:
        cli_fakes.surfline = source(
            pipeline=pipeline_forecast(good_hours=()), sunset=sunset_forecast(good_hours=())
        )
        result = run_cli("check", "--dry-run")
        assert result.out == NO_GOOD_SPOTS_LINE
        assert result.rc == 0
    assert cli_fakes.notifier_calls == []


TABLE_I = [
    ("none", {}, ("check",), "TYPESAFE_API_KEY, WHATSAPP_PHONE, CALLMEBOT_API_KEY"),
    ("phone_only", {"WHATSAPP_PHONE": "+34612345678"}, ("check",), "TYPESAFE_API_KEY, CALLMEBOT_API_KEY"),
    (
        "phone_and_empty",
        {"WHATSAPP_PHONE": "+34612345678", "TYPESAFE_API_KEY": "", "CALLMEBOT_API_KEY": ""},
        ("check",),
        "TYPESAFE_API_KEY, CALLMEBOT_API_KEY",
    ),
    (
        "all_blank",
        {"TYPESAFE_API_KEY": "  ", "WHATSAPP_PHONE": "  ", "CALLMEBOT_API_KEY": "  "},
        ("check",),
        "TYPESAFE_API_KEY, WHATSAPP_PHONE, CALLMEBOT_API_KEY",
    ),
    ("typesafe_only", {"TYPESAFE_API_KEY": "ts-key"}, ("check",), "WHATSAPP_PHONE, CALLMEBOT_API_KEY"),
    (
        "whatsapp_only",
        {"WHATSAPP_PHONE": "+34612345678", "CALLMEBOT_API_KEY": "cmb-key"},
        ("check",),
        "TYPESAFE_API_KEY",
    ),
    ("dry_run_none", {}, ("check", "--dry-run"), "TYPESAFE_API_KEY"),
]


@pytest.mark.parametrize(
    "env, command, names", [(e, c, n) for _, e, c, n in TABLE_I], ids=[i for i, _, _, _ in TABLE_I]
)
def test_tc179_missing_variables(run_cli, cli_fakes, monkeypatch, missing_env_path, env, command, names):
    """TC-179: AC-14.9, table I, missing variables are listed in fixed order, each with what it is."""
    set_env(monkeypatch, **env)
    result = run_cli(*command)
    assert result.err == missing_no_file(names, missing_env_path)
    assert result.out == ""
    assert result.rc == 1
    assert cli_fakes.no_factory_called()


def test_tc180_config_checked_before_env(run_cli, cli_fakes, tmp_path):
    """TC-180: the config is loaded before secrets are checked."""
    path = tmp_path / "absent.toml"
    result = run_cli("check", config=path)
    assert result.err == f"error: config file not found: {path}\n"
    assert result.rc == 1


def test_tc181_bad_phone_real_factory(run_cli, cli_fakes, monkeypatch):
    """TC-181: AC-14.10, the real make_notifier rejects a bad phone before any fetch."""
    set_env(monkeypatch, TYPESAFE_API_KEY="ts-key", WHATSAPP_PHONE="0612345678", CALLMEBOT_API_KEY="cmb-key")
    monkeypatch.setattr(cli, "make_notifier", cli_fakes.originals.make_notifier)
    result = run_cli("check")
    assert result.err == f"error: {PHONE_ERROR}\n"
    assert result.out == ""
    assert result.rc == 1
    assert cli_fakes.surfline.fetched == []
    assert cli_fakes.jev.calls == []


BLANK_SECRETS = [
    ("typesafe_blank_dry_run", {"TYPESAFE_API_KEY": "   "}, ("check", "--dry-run"), "TYPESAFE_API_KEY"),
    (
        "callmebot_blank",
        {"TYPESAFE_API_KEY": "ts-key", "WHATSAPP_PHONE": "+34612345678", "CALLMEBOT_API_KEY": "  \t"},
        ("check",),
        "CALLMEBOT_API_KEY",
    ),
    (
        "phone_blank_test_notification",
        {"WHATSAPP_PHONE": " ", "CALLMEBOT_API_KEY": "cmb-key"},
        ("test-notification",),
        "WHATSAPP_PHONE",
    ),
]


@pytest.mark.parametrize(
    "env, command, names",
    [(e, c, n) for _, e, c, n in BLANK_SECRETS],
    ids=[i for i, _, _, _ in BLANK_SECRETS],
)
def test_tc182_whitespace_secrets_missing(run_cli, cli_fakes, monkeypatch, missing_env_path, env, command, names):
    """TC-182: (a)-(c) spec 4.12, whitespace-only values count as missing."""
    set_env(monkeypatch, **env)
    result = run_cli(*command)
    assert result.err == missing_no_file(names, missing_env_path)
    assert result.rc == 1
    assert cli_fakes.no_factory_called()


def test_tc182_values_passed_unstripped(run_cli, cli_fakes, monkeypatch):
    """TC-182: (d) present values are passed to the factories exactly as read."""
    monkeypatch.setenv("TYPESAFE_API_KEY", " ts-key ")
    dry = run_cli("check", "--dry-run")
    assert dry.rc == 0
    assert cli_fakes.jev_calls[0][0] == " ts-key "

    set_env(
        monkeypatch,
        TYPESAFE_API_KEY="ts-key",
        WHATSAPP_PHONE=" +34612345678",
        CALLMEBOT_API_KEY=" cmb-key ",
    )
    full = run_cli("check")
    assert full.rc == 0
    assert cli_fakes.notifier_calls == [(" +34612345678", " cmb-key ")]


# ---------------------------------------------------------------------------
# test-notification
# ---------------------------------------------------------------------------


def test_tc183_test_notification(run_cli, cli_fakes, monkeypatch, tmp_path):
    """TC-183: AC-16.1, sends TEST_MESSAGE without reading the config."""
    set_env(monkeypatch, WHATSAPP_PHONE="+34612345678", CALLMEBOT_API_KEY="cmb-key")
    result = run_cli("test-notification", config=tmp_path / "absent.toml")
    assert cli_fakes.notifier_calls == [("+34612345678", "cmb-key")]
    assert cli_fakes.notifier.sent == [TEST_MESSAGE_TEXT]
    assert result.out == "Test notification sent.\n"
    assert result.err == ""
    assert result.rc == 0
    assert cli_fakes.surfline_calls == []
    assert cli_fakes.jev_calls == []


def test_tc184_test_notification_send_fails(run_cli, cli_fakes, monkeypatch):
    """TC-184: AC-16.2, a send failure prints an error and returns 4."""
    set_env(monkeypatch, WHATSAPP_PHONE="+34612345678", CALLMEBOT_API_KEY="cmb-key")
    cli_fakes.notifier = FakeNotifier(exc=NotificationError("CallMeBot returned HTTP 203: APIKey is invalid"))
    result = run_cli("test-notification")
    assert result.err == "error: failed to send WhatsApp message: CallMeBot returned HTTP 203: APIKey is invalid\n"
    assert result.out == ""
    assert result.rc == 4


@pytest.mark.parametrize(
    "env, names",
    [
        ({}, "WHATSAPP_PHONE, CALLMEBOT_API_KEY"),
        ({"WHATSAPP_PHONE": "+34612345678"}, "CALLMEBOT_API_KEY"),
        ({"CALLMEBOT_API_KEY": "cmb-key"}, "WHATSAPP_PHONE"),
    ],
    ids=["none", "phone_only", "key_only"],
)
def test_tc185_test_notification_missing(run_cli, cli_fakes, monkeypatch, missing_env_path, env, names):
    """TC-185: AC-16.3, only the WhatsApp variables are required."""
    set_env(monkeypatch, **env)
    result = run_cli("test-notification")
    assert result.err == missing_no_file(names, missing_env_path)
    assert result.rc == 1
    assert cli_fakes.notifier_calls == []


def test_tc186_test_notification_bad_phone(run_cli, cli_fakes, monkeypatch):
    """TC-186: a ValueError from the real make_notifier prints `error: ...` and returns 1."""
    set_env(monkeypatch, WHATSAPP_PHONE="0612345678", CALLMEBOT_API_KEY="cmb-key")
    monkeypatch.setattr(cli, "make_notifier", cli_fakes.originals.make_notifier)
    result = run_cli("test-notification")
    assert result.err == f"error: {PHONE_ERROR}\n"
    assert result.rc == 1


# ---------------------------------------------------------------------------
# .env handling and secrets
# ---------------------------------------------------------------------------


def test_tc187_real_env_wins(run_cli, cli_fakes, monkeypatch, tmp_path):
    """TC-187: AC-17.1, real environment variables win over the env file."""
    env_file = tmp_path / "test.env"
    env_file.write_text("TYPESAFE_API_KEY=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fromenv")
    run_cli("check", "--dry-run", env_file=env_file)
    assert cli_fakes.jev_calls[0][0] == "fromenv"


def test_tc188_env_file_used(run_cli, cli_fakes, tmp_path):
    """TC-188: AC-17.2, unset variables are read from the env file."""
    env_file = tmp_path / "test.env"
    env_file.write_text("TYPESAFE_API_KEY=fromfile\n", encoding="utf-8")
    result = run_cli("check", "--dry-run", env_file=env_file)
    assert result.rc == 0
    assert cli_fakes.jev_calls[0][0] == "fromfile"


def test_tc188_env_file_all_three(run_cli, cli_fakes, tmp_path):
    """TC-188: AC-17.2, all three secrets can come from the env file."""
    env_file = tmp_path / "all.env"
    env_file.write_text(
        "TYPESAFE_API_KEY=fromfile\nWHATSAPP_PHONE=+34612345678\nCALLMEBOT_API_KEY=cmb-file\n",
        encoding="utf-8",
    )
    result = run_cli("check", env_file=env_file)
    assert cli_fakes.notifier_calls == [("+34612345678", "cmb-file")]
    assert result.rc == 0


def test_tc189_missing_env_file_ignored(run_cli, cli_fakes, secrets_env, tmp_path):
    """TC-189: AC-17.3, a missing env file (even in a missing directory) is ignored."""
    result = run_cli("check", env_file=tmp_path / "nodir" / "x.env")
    assert result.out == "Notification sent: 2 spot(s) recommended.\n"
    assert result.rc == 0


TS_SECRET = "ts-SECRET-7f3a"
CMB_SECRET = "cmb-SECRET-9c1e"
URL_WITH_QUERY = re.compile(r"https?://\S*\?")


def _wire_real_clients(monkeypatch, jev_session, callmebot_session):
    monkeypatch.setattr(
        cli,
        "make_jev_client",
        lambda api_key, settings: JevClient(api_key, model=settings.jev_model, session=jev_session),
    )
    monkeypatch.setattr(
        cli,
        "make_notifier",
        lambda phone, api_key: CallMeBotNotifier(phone, api_key, session=callmebot_session),
    )


SECURITY_SCENARIOS = {
    "a_success": dict(
        jev=lambda: FakeSession(response=FakeResponse(200, REF_JEV_RESPONSE)),
        cmb=lambda: FakeSession(response=FakeResponse(200, text="ok")),
        args=("check",),
        rc=0,
        err="",
    ),
    "b_callmebot_500": dict(
        jev=lambda: FakeSession(response=FakeResponse(200, REF_JEV_RESPONSE)),
        cmb=lambda: FakeSession(response=FakeResponse(500, text=f"invalid apikey {CMB_SECRET}")),
        args=("check",),
        rc=4,
        err="error: failed to send WhatsApp message: CallMeBot returned HTTP 500: invalid apikey ***\n",
    ),
    "c_callmebot_connection": dict(
        jev=lambda: FakeSession(response=FakeResponse(200, REF_JEV_RESPONSE)),
        cmb=lambda: FakeSession(
            exc=requests.ConnectionError(f"failed url=/whatsapp.php?apikey={CMB_SECRET}")
        ),
        args=("check",),
        rc=4,
        err=(
            "error: failed to send WhatsApp message: request to CallMeBot failed: "
            "failed url=/whatsapp.php?apikey=***\n"
        ),
    ),
    "d_jev_401": dict(
        jev=lambda: FakeSession(response=FakeResponse(401, text="no")),
        cmb=lambda: FakeSession(response=FakeResponse(200, text="ok")),
        args=("check",),
        rc=3,
        err="warning: Jev request failed: authentication failed (HTTP 401); check TYPESAFE_API_KEY\n",
    ),
    "e_jev_422": dict(
        jev=lambda: FakeSession(response=FakeResponse(422, text=f"token {TS_SECRET}")),
        cmb=lambda: FakeSession(response=FakeResponse(200, text="ok")),
        args=("check",),
        rc=3,
        err="warning: Jev request failed: invalid request (HTTP 422): token ***\n",
    ),
    "g_test_notification_203": dict(
        jev=lambda: FakeSession(response=FakeResponse(200, REF_JEV_RESPONSE)),
        cmb=lambda: FakeSession(response=FakeResponse(203, text=f"{CMB_SECRET} invalid")),
        args=("test-notification",),
        rc=4,
        err="error: failed to send WhatsApp message: CallMeBot returned HTTP 203: *** invalid\n",
    ),
}


@pytest.mark.parametrize("name", list(SECURITY_SCENARIOS))
def test_tc190_secrets_never_shown(run_cli, cli_fakes, monkeypatch, name):
    """TC-190: AC-17.4 and Security NFR, secrets never reach stdout or stderr."""
    scenario = SECURITY_SCENARIOS[name]
    set_env(monkeypatch, TYPESAFE_API_KEY=TS_SECRET, WHATSAPP_PHONE="+34612345678", CALLMEBOT_API_KEY=CMB_SECRET)
    _wire_real_clients(monkeypatch, scenario["jev"](), scenario["cmb"]())
    result = run_cli(*scenario["args"])
    assert result.rc == scenario["rc"]
    assert result.err == scenario["err"]
    assert TS_SECRET not in result.out + result.err
    assert CMB_SECRET not in result.out + result.err


def test_tc190_secrets_never_logged(run_cli, cli_fakes, monkeypatch, caplog):
    """TC-190: (f) with -v, app log records contain no secret and no URL with a query string."""
    caplog.set_level(logging.DEBUG, logger="surf_alert")
    set_env(monkeypatch, TYPESAFE_API_KEY=TS_SECRET, WHATSAPP_PHONE="+34612345678", CALLMEBOT_API_KEY=CMB_SECRET)
    _wire_real_clients(
        monkeypatch,
        FakeSession(response=FakeResponse(200, REF_JEV_RESPONSE)),
        FakeSession(response=FakeResponse(200, text="ok")),
    )
    result = run_cli("-v", "check")
    assert result.rc == 0
    assert TS_SECRET not in result.out + result.err
    assert CMB_SECRET not in result.out + result.err
    assert TS_SECRET not in caplog.text
    assert CMB_SECRET not in caplog.text
    for record in caplog.records:
        assert not URL_WITH_QUERY.search(record.getMessage()), record.getMessage()


def test_tc191_unexpected_exception_propagates(run_cli, cli_fakes, secrets_env):
    """TC-191: spec 6.5, unexpected exceptions are not caught by main."""
    cli_fakes.surfline = source(pipeline=RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        run_cli("check")


# ---------------------------------------------------------------------------
# Real factories (offline)
# ---------------------------------------------------------------------------


def test_tc192_real_surfline_factory(cli_fakes):
    """TC-192: (a) make_surfline_client builds a SurflineClient without importing pysurfline."""
    client = cli_fakes.originals.make_surfline_client(Settings())
    assert isinstance(client, SurflineClient)
    assert sys.modules.get("pysurfline") is None


def test_tc192_real_jev_factory(cli_fakes, monkeypatch):
    """TC-192: (b) make_jev_client builds a JevClient using settings.jev_model and a 30 s timeout."""
    calls = []
    answers = {"q": {"type": "noul", "noul": 0.5}}

    def recorder(self, url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": copy.deepcopy(json), "timeout": timeout})
        return FakeResponse(200, {"answers": answers})

    monkeypatch.setattr(requests.Session, "post", recorder)
    client = cli_fakes.originals.make_jev_client("k", Settings(jev_model="jev-test"))
    assert isinstance(client, JevClient)
    questions = {"q": {"type": "noul", "instructions": "x"}}
    assert client.ask({"a": 1}, questions) == answers
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.typesafe.ai/v1/systemone"
    assert calls[0]["headers"] == {"Authorization": "Bearer k", "Content-Type": "application/json"}
    assert calls[0]["json"] == {"model": "jev-test", "state": {"a": 1}, "questions": questions}
    assert calls[0]["timeout"] == 30.0


def test_tc192_real_notifier_factory(cli_fakes, monkeypatch):
    """TC-192: (c) make_notifier builds a CallMeBotNotifier with a 30 s timeout."""
    calls = []

    def recorder(self, url, *, params, timeout):
        calls.append({"url": url, "params": copy.deepcopy(params), "timeout": timeout})
        return FakeResponse(200, text="ok")

    monkeypatch.setattr(requests.Session, "get", recorder)
    notifier = cli_fakes.originals.make_notifier("+34612345678", "k")
    assert isinstance(notifier, CallMeBotNotifier)
    notifier.send("hi")
    assert calls == [
        {
            "url": "https://api.callmebot.com/whatsapp.php",
            "params": {"phone": "+34612345678", "text": "hi", "apikey": "k"},
            "timeout": 30.0,
        }
    ]


def test_tc192_real_utc_now(cli_fakes):
    """TC-192: (d) utc_now returns an aware UTC datetime close to the real time."""
    now = cli_fakes.originals.utc_now()
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(timezone.utc)) < timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Logging, option position, empty real variables
# ---------------------------------------------------------------------------


def test_tc210_verbose_logging_levels(run_cli, cli_fakes):
    """TC-210: (a) -v raises only the surf_alert logger to DEBUG; the root stays at WARNING."""
    result = run_cli("-v", "spots")
    assert result.rc == 0
    assert logging.getLogger("surf_alert").level == logging.DEBUG
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("urllib3").getEffectiveLevel() >= logging.WARNING


def test_tc210_default_logging_levels(run_cli, cli_fakes):
    """TC-210: (b) without -v the surf_alert logger is at WARNING; the root stays at WARNING."""
    result = run_cli("spots")
    assert result.rc == 0
    assert logging.getLogger("surf_alert").level == logging.WARNING
    assert logging.getLogger().level == logging.WARNING


@pytest.mark.parametrize("case", range(3), ids=["config_after_spots", "config_after_check", "verbose_after_rules"])
def test_tc211_global_options_before_command(tmp_path, monkeypatch, capsys, cli_fakes, ref_config_path, missing_env_path, case):
    """TC-211: spec 4.12, a global option after the command is a usage error (exit 2)."""
    monkeypatch.chdir(tmp_path)
    p, m = str(ref_config_path), str(missing_env_path)
    argv = [
        ["spots", "--config", p],
        ["--env-file", m, "check", "--config", p],
        ["--config", p, "rules", "-v"],
    ][case]
    with pytest.raises(SystemExit) as ei:
        cli.main(argv)
    assert ei.value.code == 2
    assert "usage:" in capsys.readouterr().err
    assert cli_fakes.no_factory_called()


def test_tc212_empty_real_variable_not_overridden(run_cli, cli_fakes, monkeypatch, tmp_path):
    """TC-212: spec 5.2, an empty real variable stays empty (and missing) even if .env has a value."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    env_file = tmp_path / "test.env"
    env_file.write_text("TYPESAFE_API_KEY=fromfile\n", encoding="utf-8")
    result = run_cli("check", "--dry-run", env_file=env_file)
    assert result.err == (
        MISSING + "TYPESAFE_API_KEY\n"
        f"  TYPESAFE_API_KEY: {ENV_HELP['TYPESAFE_API_KEY']}; {env_file} has a value, but TYPESAFE_API_KEY "
        "is set to an empty value in your shell, which takes precedence (run: unset TYPESAFE_API_KEY)\n"
    )
    assert result.rc == 1
    assert cli_fakes.no_factory_called()


ENV_FILE_TEXT = (
    "TYPESAFE_API_KEY=\n"
    "WHATSAPP_PHONE=   \n"
    "# CALLMEBOT_API_KEY=commented-out\n"
)


def test_tc218_reasons_from_env_file(run_cli, cli_fakes, tmp_path):
    """TC-218: with an env file, each missing variable says whether it is empty or absent there."""
    env_file = tmp_path / "my.env"
    env_file.write_text(ENV_FILE_TEXT, encoding="utf-8")
    result = run_cli("check", env_file=env_file)
    assert result.err == (
        MISSING + "TYPESAFE_API_KEY, WHATSAPP_PHONE, CALLMEBOT_API_KEY\n"
        f"  TYPESAFE_API_KEY: {ENV_HELP['TYPESAFE_API_KEY']}; it is empty in {env_file}\n"
        f"  WHATSAPP_PHONE: {ENV_HELP['WHATSAPP_PHONE']}; it is empty in {env_file}\n"
        f"  CALLMEBOT_API_KEY: {ENV_HELP['CALLMEBOT_API_KEY']}; it is not in {env_file}\n"
        f"Add the missing values to {env_file} (see .env.example for the format).\n"
    )
    assert result.out == ""
    assert result.rc == 1
    assert cli_fakes.no_factory_called()


def test_tc218_mixed_shell_and_file_reasons(run_cli, cli_fakes, monkeypatch, tmp_path):
    """TC-218: a shell override and a gap in the file are both explained; values are never shown."""
    env_file = tmp_path / "my.env"
    env_file.write_text("TYPESAFE_API_KEY=ts-SECRET\nWHATSAPP_PHONE=+34612345678\n", encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY", " ")
    result = run_cli("test-notification", env_file=env_file)
    assert result.err == (
        MISSING + "CALLMEBOT_API_KEY\n"
        f"  CALLMEBOT_API_KEY: {ENV_HELP['CALLMEBOT_API_KEY']}; it is not in {env_file}\n"
        f"Add the missing values to {env_file} (see .env.example for the format).\n"
    )
    result = run_cli("check", "--dry-run", env_file=env_file)
    assert "ts-SECRET" not in result.err
    assert "run: unset TYPESAFE_API_KEY" in result.err
    assert "Add the missing values" not in result.err
    assert result.rc == 1


# ---------------------------------------------------------------------------
# -v report and spot table
# ---------------------------------------------------------------------------


def read_table():
    with open(cli.REPORT_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_tc223_verbose_check_report_and_table(run_cli, cli_fakes, monkeypatch):
    """TC-223: -v check explains each spot on stderr and writes the table; stdout is unchanged."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    result = run_cli("-v", "check", "--dry-run")
    assert result.rc == 0
    assert result.out == REF_MESSAGE + "\n"
    assert "Verbose report\n" in result.err
    assert "  rule 1 (wave_height_min > 2 ft): 5/12 hours\n" in result.err
    assert "  outcome: recommended #1: Jev confidence 0.92 >= threshold 0.70\n" in result.err
    assert "Jev's best spot: Pipeline (probability 0.88)\n" in result.err
    assert f"Spot table written to {cli.REPORT_PATH}\n" in result.err
    assert cli_fakes.jev.calls == [(REF_STATE, REF_QUESTIONS)]
    rows = read_table()
    assert [(r["spot"], r["rank"], r["jev_confidence"], r["best_spot_probability"]) for r in rows] == [
        ("Pipeline", "1", "0.92", "0.88"),
        ("Sunset Beach", "2", "0.81", "0.12"),
    ]


def test_tc223_no_report_without_verbose(run_cli, cli_fakes, monkeypatch):
    """TC-223: without -v there is no report and no table; -v with other commands writes none."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    result = run_cli("check", "--dry-run")
    assert result.err == ""
    assert not os.path.exists(cli.REPORT_PATH)
    assert run_cli("-v", "spots").rc == 0
    assert not os.path.exists(cli.REPORT_PATH)


def test_tc224_verbose_conditions(run_cli, cli_fakes):
    """TC-224: -v conditions reports the rule results without Jev."""
    result = run_cli("-v", "conditions")
    assert result.rc == 0
    assert result.out == REF_CONDITIONS_OUTPUT
    assert "  outcome: passes rules (Jev not asked)\n" in result.err
    assert "Jev: not asked (no spot passed the rules, or this command does not ask Jev).\n" in result.err
    assert cli_fakes.jev_calls == []
    rows = read_table()
    assert [(r["spot"], r["passes_rules"], r["jev_confidence"], r["sent_to_jev"]) for r in rows] == [
        ("Pipeline", "yes", "", "no"),
        ("Sunset Beach", "yes", "", "no"),
    ]


def test_tc224_verbose_conditions_one_spot(run_cli, cli_fakes):
    """TC-224: with --spot, the table holds only that spot."""
    assert run_cli("-v", "conditions", "--spot", "sunset beach").rc == 0
    assert [r["spot"] for r in read_table()] == ["Sunset Beach"]


def test_tc225_table_write_failure_is_a_warning(run_cli, cli_fakes, monkeypatch, tmp_path):
    """TC-225: if the table cannot be written, -v warns and the exit code is unchanged."""
    monkeypatch.setattr(cli, "REPORT_PATH", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    result = run_cli("-v", "check", "--dry-run")
    assert result.rc == 0
    assert result.out == REF_MESSAGE + "\n"
    assert f"warning: could not write the spot table to {tmp_path}: " in result.err
    assert "Spot table written" not in result.err
