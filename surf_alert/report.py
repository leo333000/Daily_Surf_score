"""Verbose run report: why each spot passed or failed, Jev's confidence, and a CSV table."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Any, Sequence

from surf_alert.evaluate import describe_rule, rule_passes
from surf_alert.jev import JevLike
from surf_alert.message import format_hours
from surf_alert.models import AppConfig, Decision, SpotEvaluation

DEFAULT_REPORT_PATH: str = "surf_alert_report.csv"


def _probability(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
        return float(value)
    return None


class RecordingJev:
    """A JevLike that passes requests through and keeps the last state and answers.

    The Decision only keeps approved spots, so the report reads every candidate's confidence
    (and the best-spot probabilities) from the recorded answers instead.
    """

    def __init__(self, inner: JevLike) -> None:
        self._inner = inner
        self.state: dict | None = None
        self.answers: dict | None = None

    def ask(self, state: dict, questions: dict[str, dict]) -> dict[str, dict]:
        self.state = state
        self.answers = None
        answers = self._inner.ask(state, questions)
        self.answers = answers
        return answers

    def _names_by_ref(self) -> dict[str, str]:
        spots = self.state.get("spots") if isinstance(self.state, dict) else None
        if not isinstance(spots, list):
            return {}
        return {
            s["ref"]: s["name"] for s in spots if isinstance(s, dict) and "ref" in s and "name" in s
        }

    def confidences(self) -> dict[str, float]:
        """Spot name -> Jev's yes probability, for every valid good_spot answer."""
        if not isinstance(self.answers, dict):
            return {}
        result: dict[str, float] = {}
        for ref, name in self._names_by_ref().items():
            answer = self.answers.get(f"good_{ref}")
            value = _probability(answer.get("noul")) if isinstance(answer, dict) else None
            if value is not None:
                result[name] = value
        return result

    def best_spot(self) -> tuple[str | None, dict[str, float]]:
        """The spot Jev chose as best (if any) and spot name -> its choice probability."""
        best = self.answers.get("best_spot") if isinstance(self.answers, dict) else None
        if not isinstance(best, dict):
            return None, {}
        names = self._names_by_ref()
        probabilities = best.get("probabilities")
        result: dict[str, float] = {}
        if isinstance(probabilities, dict):
            for ref, value in probabilities.items():
                p = _probability(value)
                if ref in names and p is not None:
                    result[names[ref]] = p
        choice = best.get("choice")
        return names.get(choice) if isinstance(choice, str) else None, result


@dataclass(frozen=True)
class SpotReport:
    name: str
    spot_id: str
    evaluation: SpotEvaluation | None  # None when the fetch failed
    error: str  # the fetch error, or ""
    rule_hours: tuple[int, ...]  # per rule: window hours where that rule passes
    sent_to_jev: bool
    jev_confidence: float | None
    best_spot_probability: float | None
    rank: int | None  # 1-based position in the recommendations, None if not recommended
    outcome: str


def _outcome(
    ev: SpotEvaluation | None,
    error: str,
    config: AppConfig,
    decision: Decision | None,
    confidence: float | None,
    rank: int | None,
) -> str:
    settings = config.settings
    if ev is None:
        return f"skipped: {error}"
    if not ev.window:
        return "not checked: no daylight hours left today"
    if not ev.passes_rules:
        return (
            f"failed rules: {len(ev.good_slots)} good hour(s) today, "
            f"needs {settings.min_good_hours}"
        )
    if decision is None:
        return "passes rules (Jev not asked)"
    threshold = f"{settings.jev_threshold:.2f}"
    shown = "unknown" if confidence is None else f"{confidence:.2f}"
    if decision.jev_used:
        if rank is not None:
            return f"recommended #{rank}: Jev confidence {shown} >= threshold {threshold}"
        return f"rejected by Jev: confidence {shown} < threshold {threshold}"
    if rank is not None:
        return f"recommended #{rank} by rules only: Jev failed ({decision.jev_error})"
    return f"not recommended: Jev failed ({decision.jev_error}) and on_jev_error is skip"


def build_spot_reports(
    config: AppConfig,
    evaluations: Sequence[SpotEvaluation],
    failures: dict[str, str],
    decision: Decision | None,
    jev: RecordingJev | None,
) -> list[SpotReport]:
    """One report per evaluated or failed spot, in config order."""
    by_name = {ev.spot.name: ev for ev in evaluations}
    ranks = (
        {rec.evaluation.spot.name: i for i, rec in enumerate(decision.recommendations, start=1)}
        if decision is not None
        else {}
    )
    confidences = jev.confidences() if jev is not None else {}
    _, best_probabilities = jev.best_spot() if jev is not None else (None, {})

    reports: list[SpotReport] = []
    for spot in config.spots:
        ev = by_name.get(spot.name)
        if ev is None and spot.name not in failures:
            continue
        error = failures.get(spot.name, "")
        rule_hours = (
            tuple(sum(rule_passes(rule, s) for s in ev.window) for rule in config.rules)
            if ev is not None
            else ()
        )
        sent = decision is not None and ev is not None and ev.passes_rules
        confidence = confidences.get(spot.name)
        rank = ranks.get(spot.name)
        reports.append(
            SpotReport(
                name=spot.name,
                spot_id=spot.spot_id,
                evaluation=ev,
                error=error,
                rule_hours=rule_hours,
                sent_to_jev=sent,
                jev_confidence=confidence,
                best_spot_probability=best_probabilities.get(spot.name),
                rank=rank,
                outcome=_outcome(ev, error, config, decision, confidence, rank),
            )
        )
    return reports


def _ratings(ev: SpotEvaluation) -> list[int]:
    return [s.surf_rating for s in ev.window if s.surf_rating is not None]


def format_report(
    config: AppConfig,
    reports: Sequence[SpotReport],
    decision: Decision | None,
    jev: RecordingJev | None,
) -> str:
    """The step-by-step explanation printed with -v."""
    settings = config.settings
    h, w = settings.height_unit, settings.wind_unit
    lines = [
        "Verbose report",
        f"Rules (all must pass in the same hour; a spot needs {settings.min_good_hours} "
        "such hour(s) today):",
    ]
    lines += [f"  {i}. {describe_rule(r, settings)}" for i, r in enumerate(config.rules, start=1)]

    for report in reports:
        ev = report.evaluation
        lines.append("")
        if ev is None:
            lines.append(f"{report.name}: fetch failed: {report.error}")
            lines.append(f"  outcome: {report.outcome}")
            continue
        lines.append(
            f"{report.name}: {ev.local_date.isoformat()} local, {len(ev.window)} daylight hour(s) "
            f"left ({format_hours(ev.window)})"
        )
        c = ev.current
        now = [f"surf {c.surf_min:.1f}-{c.surf_max:.1f} {h}"]
        if c.surf_rating is not None:
            now.append(f"Surfline rating {c.surf_rating}")
        if c.primary_swell is not None:
            now.append(f"swell {c.primary_swell.height:.1f} {h} @ {c.primary_swell.period:.0f}s")
        if c.wind_speed is not None:
            now.append(f"wind {c.wind_speed:.0f} {w} {c.wind_direction_type or ''}".rstrip())
        lines.append(f"  now ({c.local_datetime():%H:%M}): " + ", ".join(now))
        if not ev.window:
            lines.append(f"  outcome: {report.outcome}")
            continue
        ratings = _ratings(ev)
        if ratings:
            lines.append(f"  Surfline rating today: best {max(ratings)}, lowest {min(ratings)}")
        for i, (rule, hours) in enumerate(zip(config.rules, report.rule_hours), start=1):
            lines.append(
                f"  rule {i} ({describe_rule(rule, settings)}): {hours}/{len(ev.window)} hours"
            )
        lines.append(
            f"  all rules: {len(ev.good_slots)} hour(s) ({format_hours(ev.good_slots)}), "
            f"needs {settings.min_good_hours} -> {'PASS' if ev.passes_rules else 'FAIL'}"
        )
        if report.jev_confidence is not None:
            jev_line = (
                f"  Jev confidence {report.jev_confidence:.2f} "
                f"(threshold {settings.jev_threshold:.2f})"
            )
            if report.best_spot_probability is not None:
                jev_line += f", best-spot probability {report.best_spot_probability:.2f}"
            lines.append(jev_line)
        lines.append(f"  outcome: {report.outcome}")

    lines.append("")
    if decision is None:
        lines.append("Jev: not asked (no spot passed the rules, or this command does not ask Jev).")
    else:
        asked = sum(r.sent_to_jev for r in reports)
        if decision.jev_used:
            lines.append(f"Jev ({settings.jev_model}): asked about {asked} spot(s).")
            best, probabilities = jev.best_spot() if jev is not None else (None, {})
            if best is not None:
                p = probabilities.get(best)
                lines.append(
                    f"Jev's best spot: {best}" + (f" (probability {p:.2f})" if p is not None else "")
                )
        else:
            lines.append(
                f"Jev failed: {decision.jev_error}; on_jev_error = {settings.on_jev_error}."
            )
        if decision.recommendations:
            order = ", ".join(rec.evaluation.spot.name for rec in decision.recommendations)
            lines.append(f"Result: recommended {order}.")
        else:
            lines.append("Result: no spot recommended, so no message.")
    return "\n".join(lines)


def _number(value: float | None, digits: int) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def _csv_row(report: SpotReport, config: AppConfig, rule_headers: Sequence[str]) -> dict[str, Any]:
    settings = config.settings
    h, w = settings.height_unit, settings.wind_unit
    row: dict[str, Any] = {
        "spot": report.name,
        "outcome": report.outcome,
        "rank": report.rank or "",
        "jev_confidence": _number(report.jev_confidence, 2),
        "best_spot_probability": _number(report.best_spot_probability, 2),
        "jev_threshold": f"{settings.jev_threshold:.2f}",
        "sent_to_jev": "yes" if report.sent_to_jev else "no",
        "spot_id": report.spot_id,
        "error": report.error,
    }
    ev = report.evaluation
    if ev is None:
        return row
    c, swell, ratings = ev.current, ev.current.primary_swell, _ratings(ev)
    row.update(
        {
            "passes_rules": "yes" if ev.passes_rules else "no",
            "good_hours": len(ev.good_slots),
            "required_hours": settings.min_good_hours,
            "surf_rating_now": "" if c.surf_rating is None else c.surf_rating,
            "surf_rating_best_today": max(ratings) if ratings else "",
            "window_hours": len(ev.window),
            "good_hour_ranges": format_hours(ev.good_slots),
            "local_date": ev.local_date.isoformat(),
            "now_time": f"{c.local_datetime():%H:%M}",
            f"now_surf_min_{h}": _number(c.surf_min, 1),
            f"now_surf_max_{h}": _number(c.surf_max, 1),
            f"now_swell_height_{h}": _number(swell.height if swell else None, 1),
            "now_swell_period_s": _number(swell.period if swell else None, 0),
            f"now_wind_speed_{w}": _number(c.wind_speed, 0),
            "now_wind_direction_type": c.wind_direction_type or "",
        }
    )
    row.update(dict(zip(rule_headers, report.rule_hours)))
    return row


def write_csv(path: str, config: AppConfig, reports: Sequence[SpotReport]) -> str:
    """Write one row per spot to `path` (overwriting it) and return its absolute path.

    The decision columns come first; rule columns hold how many of today's hours pass that rule.
    """
    settings = config.settings
    h, w = settings.height_unit, settings.wind_unit
    rule_headers = [
        f"rule {i}: {describe_rule(r, settings)}" for i, r in enumerate(config.rules, start=1)
    ]
    header = [
        "spot", "outcome", "rank", "jev_confidence", "best_spot_probability", "jev_threshold",
        "passes_rules", "good_hours", "required_hours", "surf_rating_now", "surf_rating_best_today",
        *rule_headers,
        "window_hours", "good_hour_ranges", "local_date", "now_time",
        f"now_surf_min_{h}", f"now_surf_max_{h}", f"now_swell_height_{h}", "now_swell_period_s",
        f"now_wind_speed_{w}", "now_wind_direction_type", "sent_to_jev", "spot_id", "error",
    ]  # fmt: skip
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, restval="")
        writer.writeheader()
        for report in reports:
            writer.writerow(_csv_row(report, config, rule_headers))
    return os.path.abspath(path)
