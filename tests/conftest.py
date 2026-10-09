"""Shared fixtures for the surf alert tests (test strategy section 5).

Nothing here imports `surf_alert` at module level: the reference data and the
CLI module are imported inside the fixtures that need them.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from collections import namedtuple
from types import SimpleNamespace

import pytest

SECRET_VARS = ("TYPESAFE_API_KEY", "WHATSAPP_PHONE", "CALLMEBOT_API_KEY")

CliResult = namedtuple("CliResult", ["rc", "out", "err"])

_DEFAULT = object()


# ---------------------------------------------------------------------------
# Autouse isolation fixtures (strategy 5.1)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Remove the three secrets and restore `os.environ` exactly afterwards.

    `load_dotenv` writes to `os.environ` directly, and `monkeypatch` does not
    undo writes to keys that were absent, so the snapshot is restored by hand.
    """
    snapshot = dict(os.environ)
    for name in SECRET_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make every outbound socket connection or DNS lookup fail."""

    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(autouse=True)
def block_pysurfline(monkeypatch):
    """`import pysurfline` raises ImportError whether or not it is installed."""
    monkeypatch.setitem(sys.modules, "pysurfline", None)


@pytest.fixture(autouse=True)
def isolate_report(monkeypatch, tmp_path):
    """Write the -v spot table into the test's tmp directory, never the project root."""
    import surf_alert.cli as cli_module

    monkeypatch.setattr(cli_module, "REPORT_PATH", str(tmp_path / "surf_alert_report.csv"))


@pytest.fixture(autouse=True)
def no_launchd(monkeypatch):
    """`cli.make_scheduler` fails unless a test replaces it, so no test touches the real launchd."""
    import surf_alert.cli as cli_module

    def _blocked():
        raise RuntimeError("launchd access is disabled in tests")

    monkeypatch.setattr(cli_module, "make_scheduler", _blocked)


@pytest.fixture(autouse=True)
def reset_logging():
    """Undo `cli.main`'s logging changes (levels and plain StreamHandlers)."""
    root = logging.getLogger()
    app = logging.getLogger("surf_alert")
    saved = [(root, root.level, list(root.handlers)), (app, app.level, list(app.handlers))]
    yield
    for logger, level, handlers in saved:
        logger.setLevel(level)
        for handler in list(logger.handlers):
            if handler not in handlers and type(handler) is logging.StreamHandler:
                logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# Shared fixtures (strategy 5.2)
# ---------------------------------------------------------------------------


@pytest.fixture
def ref_config_path(tmp_path):
    from tests.scenario import REF_CONFIG_TOML

    path = tmp_path / "surf_alert.toml"
    path.write_text(REF_CONFIG_TOML, encoding="utf-8")
    return path


@pytest.fixture
def missing_env_path(tmp_path):
    return tmp_path / "missing.env"


@pytest.fixture
def write_config(tmp_path):
    def _write(text, name="cfg.toml"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def secrets_env(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    monkeypatch.setenv("WHATSAPP_PHONE", "+34612345678")
    monkeypatch.setenv("CALLMEBOT_API_KEY", "cmb-key")


class CliFakes:
    """Replaces the four `surf_alert.cli` module globals with recording fakes."""

    def __init__(self, cli_module, monkeypatch):
        from tests.fakes import FakeForecastSource, FakeJev, FakeNotifier
        from tests.scenario import NOW, REF_ANSWERS, pipeline_forecast, sunset_forecast

        self.surfline = FakeForecastSource(
            {"Pipeline": pipeline_forecast(), "Sunset Beach": sunset_forecast()}
        )
        self.jev = FakeJev(answers=REF_ANSWERS)
        self.notifier = FakeNotifier()
        self.now = NOW
        self.surfline_calls = []
        self.jev_calls = []
        self.notifier_calls = []
        self.originals = SimpleNamespace(
            make_surfline_client=cli_module.make_surfline_client,
            make_jev_client=cli_module.make_jev_client,
            make_notifier=cli_module.make_notifier,
            utc_now=cli_module.utc_now,
        )

        def make_surfline_client(settings):
            self.surfline_calls.append(settings)
            return self.surfline

        def make_jev_client(api_key, settings):
            self.jev_calls.append((api_key, settings))
            return self.jev

        def make_notifier(phone, api_key):
            self.notifier_calls.append((phone, api_key))
            return self.notifier

        def utc_now():
            return self.now

        monkeypatch.setattr(cli_module, "make_surfline_client", make_surfline_client)
        monkeypatch.setattr(cli_module, "make_jev_client", make_jev_client)
        monkeypatch.setattr(cli_module, "make_notifier", make_notifier)
        monkeypatch.setattr(cli_module, "utc_now", utc_now)

    def no_factory_called(self):
        return self.surfline_calls == [] and self.jev_calls == [] and self.notifier_calls == []


@pytest.fixture
def cli_fakes(monkeypatch):
    import surf_alert.cli as cli_module

    return CliFakes(cli_module, monkeypatch)


@pytest.fixture
def run_cli(capsys, ref_config_path, missing_env_path):
    """Call `surf_alert.cli.main` in-process and capture its output.

    `run_cli(*args, config=<ref config>, env_file=<missing path>)`; pass
    `None` to leave out the matching global option.
    """
    import surf_alert.cli as cli_module

    def _run(*args, config=_DEFAULT, env_file=_DEFAULT):
        if config is _DEFAULT:
            config = ref_config_path
        if env_file is _DEFAULT:
            env_file = missing_env_path
        argv = []
        if config is not None:
            argv += ["--config", str(config)]
        if env_file is not None:
            argv += ["--env-file", str(env_file)]
        argv += list(args)
        capsys.readouterr()  # discard anything printed before this call
        rc = cli_module.main(argv)
        captured = capsys.readouterr()
        return CliResult(rc, captured.out, captured.err)

    return _run
