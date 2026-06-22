"""M3 checks: codebase-memory MCP docs and helper scripts exist and are sane.

These are static checks only — they never require the codebase-memory-mcp
binary, run any script, or touch the network.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "CODEBASE_MEMORY_MCP.md"
SH = ROOT / "scripts" / "index_codebase_memory.sh"
PS1 = ROOT / "scripts" / "index_codebase_memory.ps1"


@pytest.mark.parametrize("path", [DOC, SH, PS1])
def test_artifact_exists_and_nonempty(path):
    assert path.is_file(), f"missing: {path.relative_to(ROOT)}"
    assert path.read_text(encoding="utf-8").strip(), f"empty: {path.relative_to(ROOT)}"


def test_doc_states_purpose_and_non_purpose():
    text = DOC.read_text(encoding="utf-8").lower()
    assert "source-code intelligence" in text
    # Must make clear it is NOT for OHLCV / market data.
    assert "ohlcv" in text
    assert "not" in text and "market data" in text


def test_doc_lists_repos_to_index():
    text = DOC.read_text(encoding="utf-8")
    assert "quant-ai-trader" in text
    assert "external/quant-trading" in text
    assert "external/QuantDinger" in text  # optional
    assert "external/Kronos" in text  # optional


def test_doc_lists_useful_queries():
    text = DOC.read_text(encoding="utf-8").lower()
    for phrase in [
        "place a trade",
        "call chain",
        "strategy adapter",
        "risk check",
        "order_send",
        "kronos",
    ]:
        assert phrase in text, f"doc missing useful query: {phrase!r}"


def test_readme_links_to_doc():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/CODEBASE_MEMORY_MCP.md" in readme


@pytest.mark.parametrize("script", [SH, PS1])
def test_scripts_do_not_require_binary(script):
    text = script.read_text(encoding="utf-8")
    # Both scripts must degrade gracefully when the binary is absent and allow
    # overriding the binary path — i.e. they never hard-require it to exist.
    assert "CODEBASE_MEMORY_MCP_BIN" in text
    assert "index_repository" in text


def test_scripts_reference_target_repos():
    for script in (SH, PS1):
        text = script.read_text(encoding="utf-8")
        assert "external/quant-trading" in text
        assert "external/QuantDinger" in text
        assert "external/Kronos" in text
