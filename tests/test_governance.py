"""M0 smoke tests: governance files exist and secrets stay out of the repo."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

GOVERNANCE_FILES = [
    "AGENTS.md",
    "PLAN.md",
    "TASKS.md",
    "ARCHITECTURE.md",
    "SAFETY.md",
    "RESOURCES.md",
    "README.md",
    ".gitignore",
    ".env.example",
    "pyproject.toml",
]


@pytest.mark.parametrize("name", GOVERNANCE_FILES)
def test_governance_file_exists_and_nonempty(name):
    path = ROOT / name
    assert path.is_file(), f"missing governance file: {name}"
    assert path.read_text(encoding="utf-8").strip(), f"empty governance file: {name}"


def test_env_is_not_committed():
    assert not (ROOT / ".env").exists(), ".env must never be committed"


def test_gitignore_excludes_env():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore, ".gitignore must exclude .env"


def test_safety_defaults_are_paper_first():
    safety = (ROOT / "SAFETY.md").read_text(encoding="utf-8")
    assert "ALLOW_LIVE_TRADING=false" in safety
    assert "`paper`" in safety


def test_env_example_does_not_enable_live():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ALLOW_LIVE_TRADING=false" in env_example
    assert "TRADING_MODE=paper" in env_example
