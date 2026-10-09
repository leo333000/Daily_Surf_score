"""Command-line interface: `python -m surf_alert [global options] COMMAND [options]`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import dotenv

from surf_alert.app import evaluate_spots, run_check
from surf_alert.config import load_config
from surf_alert.errors import ConfigError, NotificationError
from surf_alert.evaluate import describe_rule
from surf_alert.jev import JevClient, JevLike
from surf_alert.message import format_rules_status, format_spot_block
from surf_alert.models import AppConfig, Decision, Settings, SpotEvaluation
from surf_alert.notify import TEST_MESSAGE, CallMeBotNotifier, Notifier
from surf_alert.report import (
    DEFAULT_REPORT_PATH,
    RecordingJev,
    build_spot_reports,
    format_report,
    write_csv,
)
from surf_alert.surfline import ForecastSource, SurflineClient

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "surf_alert.toml"
DEFAULT_ENV_FILE = ".env"
REPORT_PATH = DEFAULT_REPORT_PATH
ENV_HELP: dict[str, str] = {
    "TYPESAFE_API_KEY": "your Jev API key, from console.typesafe.ai",
    "WHATSAPP_PHONE": "your WhatsApp number in international format, e.g. +34612345678",
    "CALLMEBOT_API_KEY": "the key CallMeBot sends you after activation (see README)",
}


def make_surfline_client(settings: Settings) -> ForecastSource:
    return SurflineClient(settings)


def make_jev_client(api_key: str, settings: Settings) -> JevLike:
    return JevClient(api_key, model=settings.jev_model)


def make_notifier(phone: str, api_key: str) -> Notifier:
    return CallMeBotNotifier(phone, api_key)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="surf_alert",
        description="Daily surf check: find spots that meet your rules, ask Jev, "
        "and send a WhatsApp message.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        metavar="PATH",
        help="TOML config file (default: surf_alert.toml, relative to the current directory)",
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        metavar="PATH",
        help="dotenv file (default: .env); a missing file is silently ignored",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log at DEBUG level to stderr; with check or conditions, also explain each spot's "
        f"result and write the per-spot table to {DEFAULT_REPORT_PATH}",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    commands.add_parser("spots", help="list configured spots (no network)")
    commands.add_parser("rules", help="list configured rules (no network)")
    conditions = commands.add_parser(
        "conditions", help="fetch and print current conditions and rule status (Surfline only)"
    )
    conditions.add_argument("--spot", metavar="NAME", help="only this spot (name, ignoring case)")
    check = commands.add_parser(
        "check", help="fetch, evaluate, ask Jev, send WhatsApp (or print with --dry-run)"
    )
    check.add_argument(
        "--dry-run", action="store_true", help="print the message instead of sending it"
    )
    commands.add_parser("test-notification", help="send a test message through WhatsApp")
    return parser


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _warning(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _read_env(names: list[str]) -> tuple[dict[str, str], list[str]]:
    """Return (present values exactly as read, missing names in the given order)."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = os.environ.get(name)
        if value is None or value.strip() == "":
            missing.append(name)
        else:
            values[name] = value
    return values, missing


def _missing_env_message(missing: list[str], env_file: str) -> str:
    """The `missing environment variable(s)` error: one line per variable, then how to fix it."""
    lines = [f"missing environment variable(s): {', '.join(missing)}"]
    if not os.path.isfile(env_file):
        lines += [f"  {name}: {ENV_HELP[name]}" for name in missing]
        lines.append(
            f"No env file was found at {os.path.abspath(env_file)}. "
            f"Create it with: cp .env.example {env_file}"
        )
        return "\n".join(lines)

    file_values = dotenv.dotenv_values(env_file)
    needs_edit = False
    for name in missing:
        value = file_values.get(name)
        if value is not None and value.strip() != "":
            reason = (
                f"{env_file} has a value, but {name} is set to an empty value in your shell, "
                f"which takes precedence (run: unset {name})"
            )
        else:
            needs_edit = True
            reason = f"it is empty in {env_file}" if name in file_values else f"it is not in {env_file}"
        lines.append(f"  {name}: {ENV_HELP[name]}; {reason}")
    if needs_edit:
        lines.append(f"Add the missing values to {env_file} (see .env.example for the format).")
    return "\n".join(lines)


def _cmd_spots(config: AppConfig) -> int:
    for i, spot in enumerate(config.spots, start=1):
        print(f"{i}. {spot.name} ({spot.spot_id})")
    return 0


def _cmd_rules(config: AppConfig) -> int:
    for i, rule in enumerate(config.rules, start=1):
        print(f"{i}. {describe_rule(rule, config.settings)}")
    return 0


def _report(
    config: AppConfig,
    evaluations: list[SpotEvaluation] | tuple[SpotEvaluation, ...],
    failures: dict[str, str],
    decision: Decision | None,
    jev: RecordingJev | None,
) -> None:
    """The -v explanation on stderr, plus the per-spot CSV table."""
    reports = build_spot_reports(config, evaluations, failures, decision, jev)
    print(format_report(config, reports, decision, jev), file=sys.stderr)
    try:
        path = write_csv(REPORT_PATH, config, reports)
    except OSError as exc:
        _warning(f"could not write the spot table to {REPORT_PATH}: {exc}")
    else:
        print(f"Spot table written to {path}", file=sys.stderr)


def _cmd_conditions(config: AppConfig, spot_name: str | None, verbose: bool) -> int:
    settings = config.settings
    spots = None
    if spot_name is not None:
        wanted = spot_name.casefold()
        match = next((s for s in config.spots if s.name.casefold() == wanted), None)
        if match is None:
            _error(f"unknown spot: {spot_name}")
            return 1
        spots = [match]

    client = make_surfline_client(settings)
    evaluations, failures = evaluate_spots(config, client, utc_now(), spots=spots)
    for name, message in failures.items():
        _warning(f"{name}: {message}")
    if evaluations:
        print(
            "\n\n".join(
                format_spot_block(ev, settings) + "\n" + format_rules_status(ev, settings)
                for ev in evaluations
            )
        )
    if verbose:
        _report(config, evaluations, failures, None, None)
    return 3 if failures else 0


def _cmd_check(config: AppConfig, dry_run: bool, env_file: str, verbose: bool) -> int:
    settings = config.settings
    required = ["TYPESAFE_API_KEY"]
    if not dry_run:
        required += ["WHATSAPP_PHONE", "CALLMEBOT_API_KEY"]
    env, missing = _read_env(required)
    if missing:
        _error(_missing_env_message(missing, env_file))
        return 1

    try:
        surfline = make_surfline_client(settings)
        jev = make_jev_client(env["TYPESAFE_API_KEY"], settings)
        notifier = (
            None
            if dry_run
            else make_notifier(env["WHATSAPP_PHONE"], env["CALLMEBOT_API_KEY"])
        )
    except ValueError as exc:
        _error(str(exc))
        return 1

    recorder = RecordingJev(jev) if verbose else None
    result = run_check(
        config, surfline=surfline, jev=recorder or jev, notifier=notifier, now=utc_now()
    )

    for name, message in result.failures.items():
        _warning(f"{name}: {message}")
    if result.decision is not None and result.decision.jev_error is not None:
        _warning(f"Jev request failed: {result.decision.jev_error}")

    if result.message is None:
        print("No good spots today; no notification sent.")
    elif dry_run:
        print(result.message)
    elif result.sent:
        print(f"Notification sent: {len(result.decision.recommendations)} spot(s) recommended.")
    else:
        _error(f"failed to send WhatsApp message: {result.send_error}")
    if verbose:
        _report(config, result.evaluations, result.failures, result.decision, recorder)
    return result.exit_code


def _cmd_test_notification(env_file: str) -> int:
    env, missing = _read_env(["WHATSAPP_PHONE", "CALLMEBOT_API_KEY"])
    if missing:
        _error(_missing_env_message(missing, env_file))
        return 1
    try:
        notifier = make_notifier(env["WHATSAPP_PHONE"], env["CALLMEBOT_API_KEY"])
    except ValueError as exc:
        _error(str(exc))
        return 1
    try:
        notifier.send(TEST_MESSAGE)
    except NotificationError as exc:
        _error(f"failed to send WhatsApp message: {exc}")
        return 4
    print("Test notification sent.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = _build_parser().parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    logging.getLogger("surf_alert").setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    dotenv.load_dotenv(dotenv_path=args.env_file, override=False)

    if args.command == "test-notification":
        return _cmd_test_notification(args.env_file)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        _error(str(exc))
        return 1

    if args.command == "spots":
        return _cmd_spots(config)
    if args.command == "rules":
        return _cmd_rules(config)
    if args.command == "conditions":
        return _cmd_conditions(config, args.spot, args.verbose)
    return _cmd_check(config, args.dry_run, args.env_file, args.verbose)
