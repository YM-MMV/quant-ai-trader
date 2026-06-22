"""Clone/update management for external repositories.

``RepoManager`` builds the git commands needed to clone or update each repo in
the manifest and (optionally) runs them through an injectable *runner*. The
runner indirection is what keeps this testable and offline-safe: unit tests
pass a fake runner and assert on the commands, so no real ``git`` process or
network access is ever required.

We deliberately shell out to ``git`` rather than importing a git library, and
we never import the cloned repositories' code (see PLAN.md / AGENTS.md).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from services.resource_service.repo_manifest import (
    PROJECT_ROOT,
    RepoManifest,
    RepoSpec,
)

# A runner takes a git argv list and returns a CompletedProcess-like object.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def default_runner(cmd: list[str]) -> "subprocess.CompletedProcess[str]":
    """Run a command for real via subprocess (used outside tests)."""
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@dataclass
class SyncResult:
    """Outcome of syncing one repo."""

    name: str
    action: str  # "clone" | "update"
    command: list[str]
    returncode: Optional[int] = None
    skipped: bool = False
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.skipped or self.returncode == 0


class RepoManager:
    """Constructs and (optionally) executes clone/update commands for repos."""

    def __init__(
        self,
        manifest: RepoManifest,
        base_dir: Path = PROJECT_ROOT,
        runner: Optional[Runner] = None,
    ) -> None:
        self.manifest = manifest
        self.base_dir = Path(base_dir)
        self._runner: Runner = runner or default_runner

    # -- path helpers ------------------------------------------------------- #
    def resolve_path(self, repo: RepoSpec) -> Path:
        """Absolute local checkout path for a repo, anchored at ``base_dir``."""
        p = Path(repo.local_path)
        return p if p.is_absolute() else self.base_dir / p

    def is_cloned(self, repo: RepoSpec) -> bool:
        """True if the repo already has a ``.git`` directory locally."""
        return (self.resolve_path(repo) / ".git").is_dir()

    # -- command construction ---------------------------------------------- #
    def clone_command(self, repo: RepoSpec, depth: Optional[int] = None) -> list[str]:
        cmd = ["git", "clone"]
        if depth is not None:
            cmd += ["--depth", str(depth)]
        if repo.ref:
            cmd += ["--branch", repo.ref]
        cmd += [repo.url, str(self.resolve_path(repo))]
        return cmd

    def update_command(self, repo: RepoSpec) -> list[str]:
        # --ff-only: never create merge commits in a reference checkout.
        return ["git", "-C", str(self.resolve_path(repo)), "pull", "--ff-only"]

    def command_for(self, repo: RepoSpec, depth: Optional[int] = None) -> list[str]:
        """Pick update if already cloned, otherwise clone."""
        if self.is_cloned(repo):
            return self.update_command(repo)
        return self.clone_command(repo, depth=depth)

    # -- execution ---------------------------------------------------------- #
    def sync(
        self, name: str, repo: RepoSpec, depth: Optional[int] = None, dry_run: bool = False
    ) -> SyncResult:
        """Clone or update a single repo. With ``dry_run`` only build the command."""
        cloned = self.is_cloned(repo)
        action = "update" if cloned else "clone"
        cmd = self.command_for(repo, depth=depth)

        if dry_run:
            return SyncResult(name=name, action=action, command=cmd, skipped=True)

        if not cloned:
            # Ensure the parent (e.g. external/) exists before git clone.
            self.resolve_path(repo).parent.mkdir(parents=True, exist_ok=True)

        proc = self._runner(cmd)
        return SyncResult(
            name=name,
            action=action,
            command=cmd,
            returncode=getattr(proc, "returncode", None),
            stdout=getattr(proc, "stdout", "") or "",
            stderr=getattr(proc, "stderr", "") or "",
        )

    def sync_all(
        self, depth: Optional[int] = None, dry_run: bool = False
    ) -> list[SyncResult]:
        """Clone/update every repo in the manifest, in declared order."""
        return [
            self.sync(name, spec, depth=depth, dry_run=dry_run)
            for name, spec in self.manifest.items()
        ]


__all__ = ["RepoManager", "SyncResult", "Runner", "default_runner"]
