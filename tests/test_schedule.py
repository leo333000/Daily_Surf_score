"""The daily launchd job: surf_alert.schedule and the `schedule` command, against a fake launchctl."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys

import pytest

import surf_alert.cli as cli
from surf_alert.errors import ScheduleError, SurfAlertError
from surf_alert.schedule import (
    LABEL,
    LOG_PATH,
    PROJECT_ROOT,
    Job,
    Scheduler,
    build_plist,
    job_from_plist,
    parse_time,
)
from tests.scenario import REF_CONFIG_TOML

UID = 501
DOMAIN = f"gui/{UID}"
TARGET = f"{DOMAIN}/{LABEL}"
BAD_CONFIG = REF_CONFIG_TOML.replace('height_unit = "ft"', 'height_unit = "yd"')
BAD_CONFIG_ERROR = "settings.height_unit must be one of: ft, m"


class FakeLaunchctl:
    """Stands in for `launchctl`: records every call and keeps the one job's state.

    `linger` is how many `print` calls still find the job after a bootout, like launchd taking a
    moment to stop a run in progress. `fail` maps a verb to the stderr it fails with.
    """

    def __init__(self, loaded=False, pid=None, last_exit="(never exited)", linger=0, fail=None):
        self.loaded = loaded
        self.pid = pid
        self.last_exit = last_exit
        self.linger = linger
        self.fail = fail or {}
        self.calls = []
        self._pending_unload = None

    def __call__(self, args):
        assert args[0] == "launchctl"
        verb = args[1]
        self.calls.append(tuple(args[1:]))
        if verb in self.fail:
            return subprocess.CompletedProcess(args, 1, "", self.fail[verb] + "\n")
        if verb == "print":
            if self._pending_unload == 0:
                self.loaded, self._pending_unload = False, None
            elif self._pending_unload is not None:
                self._pending_unload -= 1
            if not self.loaded:
                return subprocess.CompletedProcess(args, 113, "", "Could not find service\n")
            return subprocess.CompletedProcess(args, 0, self._describe(), "")
        if verb == "bootout":
            if not self.loaded:
                return subprocess.CompletedProcess(args, 3, "", "Boot-out failed: 3: No such process\n")
            self.pid = None
            if self.linger:
                self._pending_unload = self.linger
            else:
                self.loaded = False
            return subprocess.CompletedProcess(args, 0, "", "")
        if verb == "bootstrap":
            if self.loaded:
                return subprocess.CompletedProcess(args, 5, "", "Bootstrap failed: 5: Input/output error\n")
            self.loaded, self.last_exit = True, "(never exited)"
            return subprocess.CompletedProcess(args, 0, "", "")
        if verb == "enable":
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected launchctl call: {args}")

    def _describe(self):
        state = "running" if self.pid is not None else "not running"
        pid_line = f"\tpid = {self.pid}\n" if self.pid is not None else ""
        return (
            f"{TARGET} = {{\n"
            f"\tactive count = {int(self.pid is not None)}\n"
            f"\tstate = {state}\n"
            f"{pid_line}"
            f"\tlast exit code = {self.last_exit}\n"
            "\tevent triggers = {\n"
            f"\t\t{LABEL}.268435470 => {{\n"
            "\t\t\tstate = active\n"
            "\t\t}\n"
            "\t}\n"
            "}\n"
        )


class FakeClock:
    def __init__(self):
        self.time = 0.0
        self.sleeps = []

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.time += seconds


@pytest.fixture
def launchctl():
    return FakeLaunchctl()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def scheduler(tmp_path, launchctl, clock):
    return Scheduler(
        agents_dir=tmp_path / "LaunchAgents", uid=UID, run=launchctl, sleep=clock.sleep, clock=clock.now
    )


@pytest.fixture
def use_scheduler(monkeypatch, scheduler):
    """Make the `schedule` command use the fake-backed scheduler."""
    monkeypatch.setattr(cli, "make_scheduler", lambda: scheduler)
    return scheduler


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("TYPESAFE_API_KEY=ts-key\n", encoding="utf-8")
    return path


def make_job(config_path="/cfg/surf_alert.toml", hour=6, minute=0):
    return Job(hour, minute, config_path, "/cfg/.env", str(LOG_PATH))


def read_plist(scheduler):
    with open(scheduler.plist_path, "rb") as fp:
        return plistlib.load(fp)


def scheduled_out(config_path, time="06:00"):
    return (
        f"Scheduled `check` every day at {time}.\n"
        f"  config: {config_path}\n"
        f"  log:    {LOG_PATH}\n"
        "Edits to the config file apply from the next run; there is no need to restart the job.\n"
    )


def status_out(config_path, state, time="06:00"):
    return (
        f"Scheduled `check` every day at {time} ({state}).\n"
        f"  config: {config_path}\n"
        f"  log:    {LOG_PATH}\n"
    )


# ---------------------------------------------------------------------------
# surf_alert.schedule
# ---------------------------------------------------------------------------


def test_schedule_error_is_a_surf_alert_error():
    assert issubclass(ScheduleError, SurfAlertError)


@pytest.mark.parametrize("text, expected", [("06:00", (6, 0)), ("6:05", (6, 5)), (" 23:59 ", (23, 59))])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["24:00", "06:60", "6", "6:5", "06:00:00", "noon", ""])
def test_parse_time_rejects(text):
    with pytest.raises(ValueError, match="use HH:MM in 24-hour format"):
        parse_time(text)


def test_log_file_is_in_the_project_folder():
    assert LOG_PATH == PROJECT_ROOT / "surf_alert.log"
    assert (PROJECT_ROOT / "surf_alert" / "schedule.py").is_file()


def test_build_plist():
    plist = build_plist(make_job(hour=7, minute=30), "/venv/bin/python", PROJECT_ROOT)
    assert plist == {
        "Label": LABEL,
        "ProgramArguments": [
            "/venv/bin/python", "-m", "surf_alert",
            "--config", "/cfg/surf_alert.toml", "--env-file", "/cfg/.env", "check",
        ],
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartCalendarInterval": {"Hour": 7, "Minute": 30},
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }


def test_job_from_plist_round_trip():
    job = make_job(hour=7, minute=30)
    assert job_from_plist(build_plist(job, "/venv/bin/python")) == job


def test_job_from_plist_without_options_uses_the_working_directory():
    """A hand-written plist (like the original one) runs `check` with the default relative paths."""
    data = {
        "Label": LABEL,
        "ProgramArguments": ["/p/.venv/bin/python", "-m", "surf_alert", "check"],
        "WorkingDirectory": "/p",
        "StartCalendarInterval": {"Hour": 6, "Minute": 0},
        "StandardOutPath": "/logs/surf_alert.log",
    }
    assert job_from_plist(data) == Job(6, 0, "/p/surf_alert.toml", "/p/.env", "/logs/surf_alert.log")


def test_install_on_a_clean_system(scheduler, launchctl):
    assert scheduler.install(make_job(), "/venv/bin/python") is False
    assert read_plist(scheduler) == build_plist(make_job(), "/venv/bin/python")
    assert launchctl.calls == [
        ("print", TARGET),
        ("enable", TARGET),
        ("bootstrap", DOMAIN, str(scheduler.plist_path)),
    ]
    assert launchctl.loaded


def test_install_stops_the_old_job_first(scheduler, launchctl):
    scheduler.install(make_job("/old.toml"), "/venv/bin/python")
    launchctl.pid = 4242  # a run is in progress
    launchctl.calls.clear()

    assert scheduler.install(make_job("/new.toml"), "/venv/bin/python") is True
    assert launchctl.calls == [
        ("print", TARGET),
        ("bootout", TARGET),
        ("print", TARGET),
        ("enable", TARGET),
        ("bootstrap", DOMAIN, str(scheduler.plist_path)),
    ]
    assert scheduler.read_job().config_path == "/new.toml"


def test_install_waits_until_launchd_forgets_the_old_job(scheduler, launchctl, clock):
    launchctl.loaded, launchctl.linger = True, 3
    scheduler.install(make_job(), "/venv/bin/python")
    assert len(clock.sleeps) == 3
    assert launchctl.calls[-1] == ("bootstrap", DOMAIN, str(scheduler.plist_path))


def test_stop_gives_up_after_the_timeout(scheduler, launchctl):
    launchctl.loaded, launchctl.linger = True, 10**6
    with pytest.raises(ScheduleError, match=rf"^launchd did not stop {LABEL} within 10 s$"):
        scheduler.install(make_job(), "/venv/bin/python")
    assert not scheduler.plist_path.exists()


def test_failed_bootout_is_reported(scheduler, launchctl):
    launchctl.loaded = True
    launchctl.fail = {"bootout": "Boot-out failed: 1: Operation not permitted"}
    with pytest.raises(ScheduleError) as ei:
        scheduler.uninstall()
    assert str(ei.value) == "launchctl bootout failed: Boot-out failed: 1: Operation not permitted"


def test_failed_bootstrap_is_reported(scheduler, launchctl):
    launchctl.fail = {"bootstrap": "Bootstrap failed: 5: Input/output error"}
    with pytest.raises(ScheduleError) as ei:
        scheduler.install(make_job(), "/venv/bin/python")
    assert str(ei.value) == "launchctl bootstrap failed: Bootstrap failed: 5: Input/output error"


def test_uninstall(scheduler, launchctl):
    scheduler.install(make_job(), "/venv/bin/python")
    assert scheduler.uninstall() is True
    assert not scheduler.plist_path.exists()
    assert not launchctl.loaded
    assert scheduler.uninstall() is False


def test_uninstall_removes_a_plist_launchd_did_not_load(scheduler, launchctl):
    scheduler.install(make_job(), "/venv/bin/python")
    launchctl.loaded = False
    assert scheduler.uninstall() is True
    assert not scheduler.plist_path.exists()


def test_status(scheduler, launchctl):
    assert scheduler.status().job is None
    scheduler.install(make_job(), "/venv/bin/python")

    status = scheduler.status()
    assert (status.job, status.loaded, status.pid, status.last_exit_code) == (
        make_job(), True, None, "(never exited)"
    )

    launchctl.pid, launchctl.last_exit = 4242, "3"
    status = scheduler.status()
    assert (status.pid, status.last_exit_code) == (4242, "3")


def test_unreadable_plist(scheduler):
    scheduler.plist_path.parent.mkdir(parents=True)
    scheduler.plist_path.write_bytes(b"not a plist")
    with pytest.raises(ScheduleError, match="^cannot read "):
        scheduler.status()


def test_plist_without_a_calendar_interval(scheduler):
    scheduler.plist_path.parent.mkdir(parents=True)
    with open(scheduler.plist_path, "wb") as fp:
        plistlib.dump({"Label": LABEL, "ProgramArguments": ["python"]}, fp)
    with pytest.raises(ScheduleError, match="was not written by `schedule start`"):
        scheduler.read_job()


def test_missing_launchctl(monkeypatch, tmp_path):
    def no_launchctl(*args, **kwargs):
        raise FileNotFoundError("launchctl")

    monkeypatch.setattr(subprocess, "run", no_launchctl)
    with pytest.raises(ScheduleError, match="^launchctl not found: scheduling needs macOS$"):
        Scheduler(agents_dir=tmp_path, uid=UID).status()


# ---------------------------------------------------------------------------
# `schedule start`
# ---------------------------------------------------------------------------


def test_start(run_cli, use_scheduler, launchctl, ref_config_path, env_file):
    result = run_cli("schedule", "start", env_file=env_file)

    assert result.out == scheduled_out(ref_config_path)
    assert result.err == ""
    assert result.rc == 0
    job = Job(6, 0, str(ref_config_path), str(env_file), str(LOG_PATH))
    assert read_plist(use_scheduler) == build_plist(job, sys.executable)
    assert launchctl.loaded


def test_start_at_another_time(run_cli, use_scheduler, ref_config_path, env_file):
    result = run_cli("schedule", "start", "--time", "7:30", env_file=env_file)
    assert result.out == scheduled_out(ref_config_path, "07:30")
    assert read_plist(use_scheduler)["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}


def test_start_stores_absolute_paths(run_cli, use_scheduler, monkeypatch, ref_config_path, env_file):
    monkeypatch.chdir(ref_config_path.parent)
    result = run_cli("schedule", "start", config="surf_alert.toml", env_file=".env")
    assert result.rc == 0
    job = use_scheduler.read_job()
    assert (job.config_path, job.env_file) == (str(ref_config_path), str(env_file))


def test_start_replaces_the_running_job(run_cli, use_scheduler, launchctl, write_config, env_file):
    """Running `schedule start` again kills a run in progress and switches the config file."""
    first = write_config(REF_CONFIG_TOML, "first.toml")
    second = write_config(REF_CONFIG_TOML, "second.toml")
    run_cli("schedule", "start", config=first, env_file=env_file)
    launchctl.pid = 4242

    result = run_cli("schedule", "start", config=second, env_file=env_file)

    assert result.out == "Stopped the previous daily job.\n" + scheduled_out(second)
    assert result.rc == 0
    assert ("bootout", TARGET) in launchctl.calls
    assert use_scheduler.read_job().config_path == str(second)


def test_start_with_an_invalid_config_leaves_the_job_alone(
    run_cli, use_scheduler, launchctl, ref_config_path, write_config, env_file
):
    run_cli("schedule", "start", env_file=env_file)
    before = read_plist(use_scheduler)
    launchctl.calls.clear()

    result = run_cli("schedule", "start", config=write_config(BAD_CONFIG), env_file=env_file)

    assert result.err == f"error: {BAD_CONFIG_ERROR}\nThe daily job was not changed.\n"
    assert result.out == ""
    assert result.rc == 1
    assert launchctl.calls == []
    assert read_plist(use_scheduler) == before


def test_start_with_a_missing_config(run_cli, use_scheduler, launchctl, tmp_path, env_file):
    path = tmp_path / "absent.toml"
    result = run_cli("schedule", "start", config=path, env_file=env_file)
    assert result.err == f"error: config file not found: {path}\nThe daily job was not changed.\n"
    assert result.rc == 1
    assert not use_scheduler.plist_path.exists()


def test_start_warns_about_a_missing_env_file(run_cli, use_scheduler, ref_config_path, missing_env_path):
    result = run_cli("schedule", "start")
    assert result.err == (
        f"warning: no env file at {missing_env_path}; the daily check reads its API keys from "
        "there (see .env.example)\n"
    )
    assert result.out == scheduled_out(ref_config_path)
    assert result.rc == 0


@pytest.mark.parametrize("value", ["25:00", "06:60", "6", "noon"])
def test_start_rejects_a_bad_time(run_cli, use_scheduler, launchctl, capsys, value):
    with pytest.raises(SystemExit) as ei:
        run_cli("schedule", "start", "--time", value)
    assert ei.value.code == 2
    assert "use HH:MM in 24-hour format" in capsys.readouterr().err
    assert launchctl.calls == []


def test_launchctl_errors_are_reported(run_cli, use_scheduler, launchctl, env_file):
    launchctl.fail = {"bootstrap": "Bootstrap failed: 5: Input/output error"}
    result = run_cli("schedule", "start", env_file=env_file)
    assert result.err == "error: launchctl bootstrap failed: Bootstrap failed: 5: Input/output error\n"
    assert result.out == ""
    assert result.rc == 1


# ---------------------------------------------------------------------------
# `schedule stop` and `schedule status`
# ---------------------------------------------------------------------------


def test_stop(run_cli, use_scheduler, launchctl, env_file):
    run_cli("schedule", "start", env_file=env_file)
    launchctl.pid = 4242

    result = run_cli("schedule", "stop")

    assert result.out == "Stopped and removed the daily job.\n"
    assert result.rc == 0
    assert not launchctl.loaded
    assert not use_scheduler.plist_path.exists()

    again = run_cli("schedule", "stop")
    assert again.out == "There is no daily job to stop.\n"
    assert again.rc == 0


def test_stop_and_status_do_not_need_a_valid_config(run_cli, use_scheduler, tmp_path):
    absent = tmp_path / "absent.toml"
    assert run_cli("schedule", "stop", config=absent).rc == 0
    assert run_cli("schedule", "status", config=absent).rc == 0


def test_status_when_not_scheduled(run_cli, use_scheduler):
    result = run_cli("schedule", "status")
    assert result.out == "Not scheduled.\n"
    assert result.err == ""
    assert result.rc == 0


def test_status_when_only_launchd_has_the_job(run_cli, use_scheduler, launchctl):
    launchctl.loaded = True
    result = run_cli("schedule", "status")
    assert result.out == "Not scheduled, but launchd still has the job loaded; run `schedule stop`.\n"


@pytest.mark.parametrize(
    "loaded, pid, last_exit, state",
    [
        (True, None, "(never exited)", "has not run yet"),
        (True, None, "3", "last run exited with code 3"),
        (True, 4242, "0", "running now, pid 4242"),
        (False, None, None, "not loaded, so it will not run; run `schedule start` again"),
    ],
)
def test_status(run_cli, use_scheduler, launchctl, ref_config_path, env_file, loaded, pid, last_exit, state):
    run_cli("schedule", "start", env_file=env_file)
    launchctl.loaded, launchctl.pid, launchctl.last_exit = loaded, pid, last_exit

    result = run_cli("schedule", "status")

    assert result.out == status_out(ref_config_path, state) + "The config file is valid.\n"
    assert result.err == ""
    assert result.rc == 0


def test_config_edits_apply_without_restarting(run_cli, use_scheduler, launchctl, ref_config_path, env_file):
    """The job keeps only the path, so `status` (like the next run) sees the edited file."""
    run_cli("schedule", "start", env_file=env_file)
    launchctl.calls.clear()

    ref_config_path.write_text(BAD_CONFIG, encoding="utf-8")
    broken = run_cli("schedule", "status")
    assert broken.out == status_out(ref_config_path, "has not run yet")
    assert broken.err == f"error: the next run will fail: {BAD_CONFIG_ERROR}\n"
    assert broken.rc == 1

    ref_config_path.write_text(REF_CONFIG_TOML, encoding="utf-8")
    fixed = run_cli("schedule", "status")
    assert fixed.out.endswith("The config file is valid.\n")
    assert fixed.rc == 0
    assert all(call[0] == "print" for call in launchctl.calls)  # the job was never reloaded
