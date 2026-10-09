"""The daily launchd job that runs `check` (macOS): install or replace it, remove it, report it.

The job only stores the config file's path. `check` reads the file again on every run, so edits
to it apply from the next run without touching the job.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from surf_alert.errors import ScheduleError

LABEL = "com.leoxyde.surfalert"
DEFAULT_TIME = "06:00"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "surf_alert.log"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
STOP_TIMEOUT = 10.0  # seconds to wait for launchd to stop and forget the old job
_POLL_INTERVAL = 0.2

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


@dataclass(frozen=True)
class Job:
    hour: int  # 0..23, local time
    minute: int  # 0..59
    config_path: str  # absolute
    env_file: str  # absolute
    log_path: str  # absolute; launchd appends stdout and stderr of every run here

    @property
    def time(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class JobStatus:
    job: Job | None  # read from the plist file; None when there is no file
    loaded: bool  # launchd knows the job, so it will run at the scheduled time
    pid: int | None  # set while a run is in progress
    last_exit_code: str | None  # as launchd prints it, e.g. "3" or "(never exited)"


def parse_time(text: str) -> tuple[int, int]:
    """`"6:00"` or `"06:00"` -> (6, 0). Raises ValueError."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if match is None or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"invalid time '{text}': use HH:MM in 24-hour format, e.g. 06:00")
    return int(match[1]), int(match[2])


def build_plist(job: Job, python: str, project_root: Path = PROJECT_ROOT) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            python,
            "-m",
            "surf_alert",
            "--config",
            job.config_path,
            "--env-file",
            job.env_file,
            "check",
        ],
        "WorkingDirectory": str(project_root),
        "StartCalendarInterval": {"Hour": job.hour, "Minute": job.minute},
        "StandardOutPath": job.log_path,
        "StandardErrorPath": job.log_path,
    }


def job_from_plist(data: dict) -> Job:
    """Read a job back from its plist. Relative paths are relative to WorkingDirectory."""
    args = list(data["ProgramArguments"])
    workdir = data.get("WorkingDirectory", "")

    def option(name: str, default: str) -> str:
        value = args[args.index(name) + 1] if name in args[:-1] else default
        return os.path.join(workdir, value)

    interval = data["StartCalendarInterval"]
    hour, minute = interval["Hour"], interval["Minute"]
    if not isinstance(hour, int) or not isinstance(minute, int):
        raise TypeError("StartCalendarInterval Hour and Minute must be integers")
    return Job(
        hour=hour,
        minute=minute,
        config_path=option("--config", "surf_alert.toml"),
        env_file=option("--env-file", ".env"),
        log_path=data.get("StandardOutPath", ""),
    )


def _run_launchctl(args: Sequence[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(args), capture_output=True, text=True)
    except FileNotFoundError:
        raise ScheduleError("launchctl not found: scheduling needs macOS") from None


def _detail(result: subprocess.CompletedProcess) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return text or f"exit code {result.returncode}"


class Scheduler:
    """Manages the one launchd job, LABEL, in the user's GUI domain."""

    def __init__(
        self,
        agents_dir: str | os.PathLike = LAUNCH_AGENTS_DIR,
        uid: int | None = None,
        run: Runner = _run_launchctl,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.plist_path = Path(agents_dir) / f"{LABEL}.plist"
        self._domain = f"gui/{os.getuid() if uid is None else uid}"
        self._target = f"{self._domain}/{LABEL}"
        self._run = run
        self._sleep = sleep
        self._clock = clock

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["launchctl", *args])

    def _describe(self) -> str | None:
        """launchd's description of the job, or None when it is not loaded."""
        result = self._launchctl("print", self._target)
        return result.stdout if result.returncode == 0 else None

    def _stop(self) -> bool:
        """Unload the job, killing a run in progress, and wait until launchd forgets it.

        Returns whether the job was loaded.
        """
        if self._describe() is None:
            return False
        result = self._launchctl("bootout", self._target)
        deadline = self._clock() + STOP_TIMEOUT
        while self._describe() is not None:
            if result.returncode != 0:
                raise ScheduleError(f"launchctl bootout failed: {_detail(result)}")
            if self._clock() >= deadline:
                raise ScheduleError(f"launchd did not stop {LABEL} within {STOP_TIMEOUT:g} s")
            self._sleep(_POLL_INTERVAL)
        return True

    def install(self, job: Job, python: str) -> bool:
        """Replace any existing job with `job`. Returns whether an old job was stopped first."""
        stopped = self._stop()
        try:
            self.plist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.plist_path, "wb") as fp:
                plistlib.dump(build_plist(job, python), fp)
        except OSError as exc:
            raise ScheduleError(f"cannot write {self.plist_path}: {exc}") from None
        # A `launchctl disable` from earlier would make bootstrap fail, so undo it first.
        self._launchctl("enable", self._target)
        result = self._launchctl("bootstrap", self._domain, str(self.plist_path))
        if result.returncode != 0:
            raise ScheduleError(f"launchctl bootstrap failed: {_detail(result)}")
        return stopped

    def uninstall(self) -> bool:
        """Stop the job and delete its plist. Returns whether there was anything to remove."""
        stopped = self._stop()
        existed = self.plist_path.exists()
        try:
            self.plist_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ScheduleError(f"cannot delete {self.plist_path}: {exc}") from None
        return stopped or existed

    def read_job(self) -> Job | None:
        try:
            with open(self.plist_path, "rb") as fp:
                data = plistlib.load(fp)
        except FileNotFoundError:
            return None
        except (OSError, plistlib.InvalidFileException) as exc:
            raise ScheduleError(f"cannot read {self.plist_path}: {exc}") from None
        try:
            return job_from_plist(data)
        except (KeyError, IndexError, TypeError) as exc:
            raise ScheduleError(
                f"{self.plist_path} was not written by `schedule start` ({exc}); "
                "run `schedule start` to replace it"
            ) from None

    def status(self) -> JobStatus:
        job = self.read_job()
        text = self._describe()
        if text is None:
            return JobStatus(job, loaded=False, pid=None, last_exit_code=None)
        pid = re.search(r"^\s*pid = (\d+)\s*$", text, re.MULTILINE)
        last = re.search(r"^\s*last exit code = (.+?)\s*$", text, re.MULTILINE)
        return JobStatus(
            job,
            loaded=True,
            pid=int(pid[1]) if pid else None,
            last_exit_code=last[1] if last else None,
        )
