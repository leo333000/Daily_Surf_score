"""Deliverable files (spec 2, 4.1, 4.13, 5.1, 5.2, 7): TC-199 to TC-201 and TC-213."""

from __future__ import annotations

import re
from pathlib import Path

from surf_alert.config import load_config
from tests.scenario import PIPELINE, REF_RULES, REF_SETTINGS

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_tc199_example_config():
    """TC-199: surf_alert.example.toml loads to Pipeline only with the reference rules and settings."""
    cfg = load_config(PROJECT_ROOT / "surf_alert.example.toml")
    assert cfg.spots == (PIPELINE,)
    assert cfg.rules == REF_RULES
    assert cfg.settings == REF_SETTINGS


def test_tc200_env_example():
    """TC-200: .env.example lists the three variables with the example phone."""
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    assert lines == ["TYPESAFE_API_KEY=", "WHATSAPP_PHONE=+34612345678", "CALLMEBOT_API_KEY="]


def test_tc201_requirements():
    """TC-201: requirements.txt pins the spec 7 dependencies."""
    text = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    entries = set()
    for line in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", line)  # pip comments start with '#' at the start or after whitespace
        compact = re.sub(r"\s+", "", line)
        if compact:
            entries.add(compact)
    for required in (
        "requests>=2.31",
        "python-dotenv>=1.0",
        "pytest>=8",
        "pysurfline@git+https://github.com/wolfinger/pysurfline@f0c98ea06ce9f26f5ba9e3b94ac98963150f7ddc",
    ):
        assert required in entries, required


def test_tc213_readme():
    """TC-213: README.md exists and documents the dry run and the test notification."""
    path = PROJECT_ROOT / "README.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "check --dry-run" in text
    assert "test-notification" in text
