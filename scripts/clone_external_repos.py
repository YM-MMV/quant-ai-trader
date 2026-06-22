#!/usr/bin/env python3
"""Clone or update the external reference repositories.

Reads ``config/external_repos.yaml`` and clones each repo into its
``local_path`` (default: under ``external/``), or fast-forward updates it if it
already exists. Cloned repos are git-ignored and used for reference only — this
script never imports their code.

Usage:
    python scripts/clone_external_repos.py --list
    python scripts/clone_external_repos.py --dry-run
    python scripts/clone_external_repos.py                 # clone/update all
    python scripts/clone_external_repos.py --only kronos openbb
    python scripts/clone_external_repos.py --depth 1       # shallow clones

Network access is only used when actually cloning/updating (not with --list or
--dry-run).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project importable when run as a plain script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.resource_service.repo_manager import RepoManager  # noqa: E402
from services.resource_service.repo_manifest import load_manifest  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="NAME",
        help="only act on these repo names (default: all)",
    )
    parser.add_argument(
        "--depth", type=int, default=None, help="shallow clone depth (e.g. 1)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the git commands without running them",
    )
    parser.add_argument(
        "--list", action="store_true", help="list configured repos and exit"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_manifest()
    manager = RepoManager(manifest, base_dir=PROJECT_ROOT)

    names = args.only or manifest.names()
    unknown = [n for n in names if n not in manifest.repos]
    if unknown:
        print(f"error: unknown repo(s): {unknown}", file=sys.stderr)
        print(f"known repos: {manifest.names()}", file=sys.stderr)
        return 2

    if args.list:
        for name in names:
            spec = manifest.get(name)
            print(f"{name:22} {spec.role:40} {spec.url}")
        return 0

    failures = 0
    for name in names:
        spec = manifest.get(name)
        result = manager.sync(name, spec, depth=args.depth, dry_run=args.dry_run)
        if args.dry_run:
            print(f"[{result.action}] {name}: {' '.join(result.command)}")
        else:
            status = "ok" if result.ok else f"FAILED (rc={result.returncode})"
            print(f"[{result.action}] {name}: {status}")
            if not result.ok:
                failures += 1
                if result.stderr:
                    print(result.stderr.strip(), file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
