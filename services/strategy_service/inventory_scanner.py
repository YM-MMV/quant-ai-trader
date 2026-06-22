"""Static inventory scanner for the quant-trading strategy repo.

Walks a local checkout of ``je-suis-tm/quant-trading`` (or any fixture tree of
the same shape) and produces one :class:`InventoryItem` per strategy/project,
classified for MT5 suitability via :mod:`strategy_classifier`.

**Static scan only.** This module lists directory entries and may *read* text
files (e.g. a README for a description). It NEVER imports or executes any
third-party code — discovered ``.py``/``.ipynb`` files are recorded as data,
not run.

Inventory JSON schema (see also docs/STRATEGY_INVENTORY.md):

    {
      "repo_url": str,
      "count": int,
      "items": [
        {
          "name": str,                      # strategy/project name
          "source_file_or_folder": str,     # path relative to the repo root
          "repo_url": str,
          "local_path": str,                # path under external/quant-trading
          "category": str,
          "description": str,
          "required_data": [str, ...],
          "supported_asset_classes": [str, ...],
          "mt5_applicability": "direct|adaptable|research_only|not_applicable",
          "reason_for_applicability": str,
          "porting_status": "not_started|adapter_created|tested|approved|rejected"
        }, ...
      ]
    }
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from services.strategy_service.strategy_classifier import (
    MT5Applicability,
    PortingStatus,
    classify,
)

DEFAULT_REPO_URL = "https://github.com/je-suis-tm/quant-trading"
DEFAULT_LOCAL_PREFIX = "external/quant-trading"

# Entries that are never strategies.
IGNORED_NAMES = frozenset(
    {".git", ".github", "__pycache__", ".ipynb_checkpoints", "node_modules"}
)
SCRIPT_SUFFIXES = frozenset({".py", ".ipynb"})
README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md", "readme.txt")


class InventoryItem(BaseModel):
    """One classified strategy/project in the inventory."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: str
    source_file_or_folder: str
    repo_url: str
    local_path: str
    category: str
    description: str
    required_data: list[str] = Field(default_factory=list)
    supported_asset_classes: list[str] = Field(default_factory=list)
    mt5_applicability: MT5Applicability
    reason_for_applicability: str
    porting_status: PortingStatus = PortingStatus.NOT_STARTED


def _first_paragraph(path: Path) -> str | None:
    """Return the first meaningful line of a text file (static read, no exec)."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s and not s.startswith("!["):  # skip blank lines and image markdown
            return s[:300]
    return None


def _read_description(entry: Path) -> str | None:
    if entry.is_dir():
        for readme in README_NAMES:
            candidate = entry / readme
            if candidate.is_file():
                return _first_paragraph(candidate)
    return None


def _make_item(name: str, source: str, repo_url: str, local_prefix: str, desc: str | None):
    cls = classify(name)
    return InventoryItem(
        name=name,
        source_file_or_folder=source,
        repo_url=repo_url,
        local_path=f"{local_prefix}/{source}",
        category=cls.category,
        description=desc or cls.description,
        required_data=cls.required_data,
        supported_asset_classes=cls.supported_asset_classes,
        mt5_applicability=cls.mt5_applicability,
        reason_for_applicability=cls.reason_for_applicability,
        porting_status=PortingStatus.NOT_STARTED,
    )


def scan_repository(
    root: str | Path,
    repo_url: str = DEFAULT_REPO_URL,
    local_prefix: str = DEFAULT_LOCAL_PREFIX,
    include_scripts: bool = True,
) -> list[InventoryItem]:
    """Scan a repo checkout and return classified inventory items.

    Each top-level directory is treated as a strategy/project. Top-level
    ``.py``/``.ipynb`` files are also inventoried when ``include_scripts`` is
    True. Hidden entries, READMEs and assets at the root are ignored. Items are
    returned sorted by name. No file is ever imported or executed.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"repo root not found or not a directory: {root}")

    items: list[InventoryItem] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name in IGNORED_NAMES or entry.name.startswith("."):
            continue
        if entry.is_dir():
            name, source = entry.name, entry.name
        elif entry.is_file() and entry.suffix.lower() in SCRIPT_SUFFIXES and include_scripts:
            name, source = entry.stem, entry.name
        else:
            continue  # root README/LICENSE/images and other files are skipped
        items.append(
            _make_item(name, source, repo_url, local_prefix, _read_description(entry))
        )
    return items


def build_items_from_names(
    names: list[str],
    repo_url: str = DEFAULT_REPO_URL,
    local_prefix: str = DEFAULT_LOCAL_PREFIX,
) -> list[InventoryItem]:
    """Build classified items from strategy names without touching the filesystem.

    Used to produce the curated baseline inventory when a real checkout is not
    available. ``source_file_or_folder`` is taken to be the name itself; run
    :func:`scan_repository` against an actual clone to reconcile exact paths.
    """
    return [
        _make_item(name, name, repo_url, local_prefix, None)
        for name in sorted(names, key=str.lower)
    ]


def inventory_to_dict(items: list[InventoryItem], repo_url: str = DEFAULT_REPO_URL) -> dict:
    """Serialise items into the documented inventory JSON structure."""
    return {
        "repo_url": repo_url,
        "count": len(items),
        "items": [item.model_dump(mode="json") for item in items],
    }


def write_inventory(
    path: str | Path, items: list[InventoryItem], repo_url: str = DEFAULT_REPO_URL
) -> Path:
    """Write the inventory JSON to ``path`` (pretty-printed, stable)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(inventory_to_dict(items, repo_url), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_inventory(path: str | Path) -> list[InventoryItem]:
    """Load and validate an inventory JSON file into typed items."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [InventoryItem.model_validate(rec) for rec in data["items"]]


__all__ = [
    "DEFAULT_REPO_URL",
    "DEFAULT_LOCAL_PREFIX",
    "InventoryItem",
    "scan_repository",
    "build_items_from_names",
    "inventory_to_dict",
    "write_inventory",
    "load_inventory",
]
