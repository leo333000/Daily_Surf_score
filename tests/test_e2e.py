"""End-to-end offline runs: TC-193 to TC-198.

TC-193 to TC-196 run a fresh interpreter in a subprocess (marker `e2e`).
TC-197 and TC-198 run the full stack in-process with only the transport faked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import surf_alert.cli as cli
from surf_alert.jev import JevClient
from surf_alert.notify import CallMeBotNotifier
from surf_alert.surfline import SurflineClient
from tests.fakes import FakeResponse, FakeSession
from tests.scenario import (
    PIPELINE,
    REF_CONFIG_TOML,
    REF_JEV_RESPONSE,
    REF_MESSAGE,
    REF_MESSAGE_RULES_ONLY,
    REF_QUESTIONS,
    REF_STATE,
    SUNSET,
    pipeline_raw,
    sunset_raw,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_VARS = ("TYPESAFE_API_KEY", "WHATSAPP_PHONE", "CALLMEBOT_API_KEY")


def run_module(args, cwd):
    """Run `python -m surf_alert <args>` in a fresh interpreter without secrets."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_VARS}
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "surf_alert", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


@pytest.fixture
def subprocess_config(tmp_path):
    path = tmp_path / "ref.toml"
    path.write_text(REF_CONFIG_TOML, encoding="utf-8")
    return path


@pytest.mark.e2e
def test_tc193_module_spots(tmp_path, subprocess_config):
    """TC-193: `python -m surf_alert ... spots` works through __main__ (AC-2.1)."""
    proc = run_module(
        ["--config", str(subprocess_config), "--env-file", str(tmp_path / "missing.env"), "spots"],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "1. Pipeline (5842041f4e65fad6a7708890)\n2. Sunset Beach (0123456789abcdef01234567)\n"


@pytest.mark.e2e
def test_tc194_module_rules(tmp_path, subprocess_config):
    """TC-194: `python -m surf_alert ... rules` (AC-3.1)."""
    proc = run_module(
        ["--config", str(subprocess_config), "--env-file", str(tmp_path / "missing.env"), "rules"],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "1. wave_height_min > 2 ft\n2. swell_period > 7 s\n"


@pytest.mark.e2e
def test_tc195_module_no_arguments(tmp_path):
    """TC-195: no arguments is a usage error with exit status 2."""
    proc = run_module([], tmp_path)
    assert proc.returncode == 2
    assert "usage:" in proc.stderr


LAZY_IMPORT_CODE = textwrap.dedent(
    """
    import importlib
    import json
    import sys

    BLOCKED = ("pysurfline", "pandas", "matplotlib")
    attempts = []


    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.partition(".")[0] in BLOCKED:
                attempts.append(name)
                raise ImportError("blocked in test: " + name)
            return None


    sys.meta_path.insert(0, Blocker())

    importlib.import_module("surf_alert")
    for sub in ("errors", "models", "config", "units", "surfline", "evaluate", "jev",
                "decision", "message", "notify", "app", "cli"):
        importlib.import_module("surf_alert." + sub)

    from surf_alert.models import Settings
    from surf_alert.surfline import SurflineClient

    SurflineClient(Settings())
    print(json.dumps({"attempts": attempts, "loaded": [n for n in BLOCKED if n in sys.modules]}))
    """
)


@pytest.mark.e2e
def test_tc196_lazy_pysurfline_import(tmp_path):
    """TC-196: AC-4.6, importing every surf_alert module never imports pysurfline, pandas or matplotlib."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_VARS}
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", LAZY_IMPORT_CODE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report == {"attempts": [], "loaded": []}


class FullStack:
    """Real SurflineClient, JevClient and CallMeBotNotifier with only the transport faked."""

    def __init__(self, monkeypatch, jev_response):
        self.factory_params = []
        self.js = FakeSession(response=jev_response)
        self.cs = FakeSession(response=FakeResponse(200, text="ok"))

        def factory(params):
            self.factory_params.append(dict(params))
            if params["spotId"] == PIPELINE.spot_id:
                return pipeline_raw()
            return sunset_raw()

        monkeypatch.setattr(
            cli, "make_surfline_client", lambda settings: SurflineClient(settings, forecast_factory=factory)
        )
        monkeypatch.setattr(
            cli,
            "make_jev_client",
            lambda api_key, settings: JevClient(api_key, model=settings.jev_model, session=self.js),
        )
        monkeypatch.setattr(
            cli, "make_notifier", lambda phone, api_key: CallMeBotNotifier(phone, api_key, session=self.cs)
        )


def test_tc197_full_stack_check(run_cli, cli_fakes, secrets_env, monkeypatch):
    """TC-197: full stack `check` (FR-4, 5, 8, 9, 12, 13, 14) with only the transport faked."""
    stack = FullStack(monkeypatch, FakeResponse(200, REF_JEV_RESPONSE))
    result = run_cli("check")

    assert result.rc == 0
    assert result.out == "Notification sent: 2 spot(s) recommended.\n"
    assert result.err == ""
    assert stack.factory_params == [
        {"spotId": PIPELINE.spot_id, "days": 1, "intervalHours": 1},
        {"spotId": SUNSET.spot_id, "days": 1, "intervalHours": 1},
    ]
    assert stack.js.calls == [
        {
            "method": "post",
            "url": "https://api.typesafe.ai/v1/systemone",
            "headers": {"Authorization": "Bearer ts-key", "Content-Type": "application/json"},
            "json": {"model": "jev-latest", "state": REF_STATE, "questions": REF_QUESTIONS},
            "timeout": 30.0,
        }
    ]
    assert stack.cs.calls == [
        {
            "method": "get",
            "url": "https://api.callmebot.com/whatsapp.php",
            "params": {"phone": "+34612345678", "text": REF_MESSAGE, "apikey": "cmb-key"},
            "timeout": 30.0,
        }
    ]


def test_tc198_full_stack_dry_run_jev_overloaded(run_cli, cli_fakes, secrets_env, monkeypatch):
    """TC-198: full stack dry run with Jev 529 prints the rules-only message and exits 3."""
    stack = FullStack(monkeypatch, FakeResponse(529, text="busy"))
    result = run_cli("check", "--dry-run")

    assert result.rc == 3
    assert result.out == REF_MESSAGE_RULES_ONLY + "\n"
    assert result.err == "warning: Jev request failed: service overloaded (HTTP 529)\n"
    assert len(stack.js.calls) == 1
    assert stack.cs.calls == []
