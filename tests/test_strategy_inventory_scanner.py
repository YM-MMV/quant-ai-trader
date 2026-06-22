"""Tests for the inventory scanner (services/strategy_service/inventory_scanner.py).

Uses a fixture repo tree built in a temp directory — the real quant-trading repo
is never required, cloned, imported, or executed.
"""
from pathlib import Path

import pytest

from services.strategy_service.inventory_scanner import (
    DEFAULT_LOCAL_PREFIX,
    DEFAULT_REPO_URL,
    InventoryItem,
    build_items_from_names,
    load_inventory,
    scan_repository,
    write_inventory,
)
from services.strategy_service.strategy_classifier import (
    MT5Applicability,
    PortingStatus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_INVENTORY = PROJECT_ROOT / "strategies" / "inventory" / "quant_trading_inventory.json"

# A script that WOULD create a marker file if it were ever imported/executed.
EVIL_SCRIPT = (
    "from pathlib import Path\n"
    "Path(__file__).with_name('EXECUTED.marker').write_text('ran')\n"
    "raise SystemExit('this script must never run')\n"
)


@pytest.fixture
def fake_repo(tmp_path) -> Path:
    root = tmp_path / "quant-trading"
    root.mkdir()

    # Strategy folders (one with a README to test description extraction).
    macd = root / "MACD Oscillator"
    macd.mkdir()
    (macd / "README.md").write_text(
        "Custom MACD crossover with signal filtering.\n", encoding="utf-8"
    )
    (macd / "macd.py").write_text("# not executed\n", encoding="utf-8")

    for folder in ("Options Straddle", "Monte Carlo Project", "Pair Trading"):
        (root / folder).mkdir()

    # Noise that must be ignored.
    (root / ".git").mkdir()
    (root / "README.md").write_text("# quant-trading\n", encoding="utf-8")
    (root / "preview.png").write_bytes(b"\x89PNG\r\n")

    # A top-level script that must be inventoried but never executed.
    (root / "evil.py").write_text(EVIL_SCRIPT, encoding="utf-8")

    return root


def test_scan_finds_strategies_and_ignores_noise(fake_repo):
    items = scan_repository(fake_repo)
    names = {i.name for i in items}
    assert {"MACD Oscillator", "Options Straddle", "Monte Carlo Project", "Pair Trading"} <= names
    assert "evil" in names  # top-level script inventoried as data
    # Noise excluded:
    assert ".git" not in names
    assert "README" not in names
    assert "preview" not in names


def test_scan_does_not_execute_or_import_scripts(fake_repo):
    scan_repository(fake_repo)
    # If evil.py had been imported/run, this marker would exist.
    assert not (fake_repo / "EXECUTED.marker").exists()
    assert not list(fake_repo.rglob("EXECUTED.marker"))


def test_item_fields_populated(fake_repo):
    items = {i.name: i for i in scan_repository(fake_repo)}

    macd = items["MACD Oscillator"]
    assert isinstance(macd, InventoryItem)
    assert macd.repo_url == DEFAULT_REPO_URL
    assert macd.local_path == f"{DEFAULT_LOCAL_PREFIX}/MACD Oscillator"
    assert macd.source_file_or_folder == "MACD Oscillator"
    assert macd.mt5_applicability == MT5Applicability.DIRECT.value
    assert macd.porting_status == PortingStatus.NOT_STARTED.value
    # README description overrides the classifier default.
    assert "Custom MACD crossover" in macd.description

    assert items["Options Straddle"].mt5_applicability == MT5Applicability.NOT_APPLICABLE.value
    assert items["Monte Carlo Project"].mt5_applicability == MT5Applicability.RESEARCH_ONLY.value
    assert items["Pair Trading"].mt5_applicability == MT5Applicability.ADAPTABLE.value
    # Unknown script -> safe default.
    assert items["evil"].mt5_applicability == MT5Applicability.RESEARCH_ONLY.value
    assert items["evil"].category == "unknown"


def test_folder_without_readme_uses_classifier_description(fake_repo):
    items = {i.name: i for i in scan_repository(fake_repo)}
    # No README in this folder -> classifier-provided description.
    assert "straddle" in items["Options Straddle"].description.lower()


def test_scan_missing_root_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        scan_repository(tmp_path / "does-not-exist")


def test_write_and_load_roundtrip(fake_repo, tmp_path):
    items = scan_repository(fake_repo)
    out = write_inventory(tmp_path / "inv.json", items)
    assert out.is_file()
    reloaded = load_inventory(out)
    assert [i.name for i in reloaded] == [i.name for i in items]
    assert all(isinstance(i, InventoryItem) for i in reloaded)


def test_build_items_from_names_no_filesystem():
    items = build_items_from_names(["MACD Oscillator", "Options Straddle"])
    assert [i.name for i in items] == ["MACD Oscillator", "Options Straddle"]
    assert items[0].local_path == f"{DEFAULT_LOCAL_PREFIX}/MACD Oscillator"


# --------------------------------------------------------------------------- #
# The committed real-repo inventory artifact
# --------------------------------------------------------------------------- #
def test_committed_inventory_is_valid_and_complete():
    items = load_inventory(REAL_INVENTORY)
    assert len(items) >= 17
    by_name = {i.name: i for i in items}
    # Spot-check representative classifications.
    assert by_name["MACD Oscillator"].mt5_applicability == MT5Applicability.DIRECT.value
    assert by_name["Options Straddle"].mt5_applicability == MT5Applicability.NOT_APPLICABLE.value
    assert by_name["Monte Carlo Project"].mt5_applicability == MT5Applicability.RESEARCH_ONLY.value
    # Every record is schema-valid and starts not_started.
    for item in items:
        assert item.porting_status == PortingStatus.NOT_STARTED.value
        assert item.local_path.startswith(DEFAULT_LOCAL_PREFIX + "/")
        assert item.reason_for_applicability
