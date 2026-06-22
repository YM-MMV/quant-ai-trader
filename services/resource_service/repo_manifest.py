"""Manifest of external repositories (config/external_repos.yaml).

Loads and validates the list of third-party repos the project references. The
manifest is pure data — it describes *where* repos live and *why*, but does not
clone anything or import their code. Cloning is handled by ``repo_manager``.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.config_loader import CONFIG_DIR, PROJECT_ROOT, load_yaml

DEFAULT_MANIFEST_PATH = CONFIG_DIR / "external_repos.yaml"


class RepoSpec(BaseModel):
    """A single external repository entry."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1)
    local_path: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    ref: Optional[str] = Field(
        None, description="branch/tag/commit to check out; default branch if None"
    )

    @field_validator("url")
    @classmethod
    def _must_be_https(cls, value: str) -> str:
        # Only allow https clone URLs: no ssh / git@ (which can carry creds),
        # no file:// or local paths. Keeps external fetches safe and auditable.
        if not value.startswith("https://"):
            raise ValueError(f"repo url must start with https:// (got {value!r})")
        return value

    @field_validator("local_path")
    @classmethod
    def _no_escape(cls, value: str) -> str:
        # Guard against path traversal / absolute paths in the manifest so a
        # clone target can never land outside the project tree. Checked against
        # both posix and windows semantics so e.g. "/etc/passwd" (not
        # drive-absolute on Windows) and "C:\\x" are both rejected regardless of
        # the host OS running the validation.
        win = PureWindowsPath(value)
        if (
            value.startswith(("/", "\\"))
            or win.drive
            or ".." in PurePosixPath(value).parts
            or ".." in win.parts
        ):
            raise ValueError(
                f"local_path must be a relative path inside the repo: {value!r}"
            )
        return value


class RepoManifest(BaseModel):
    """The full set of external repositories, keyed by short name."""

    model_config = ConfigDict(extra="forbid")

    repos: dict[str, RepoSpec] = Field(..., min_length=1)

    def names(self) -> list[str]:
        return list(self.repos.keys())

    def get(self, name: str) -> RepoSpec:
        if name not in self.repos:
            raise KeyError(f"unknown repo {name!r}; known: {self.names()}")
        return self.repos[name]

    def items(self):
        return self.repos.items()


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> RepoManifest:
    """Load and validate the external-repo manifest from YAML."""
    return RepoManifest.model_validate(load_yaml(Path(path)))


__all__ = [
    "RepoSpec",
    "RepoManifest",
    "load_manifest",
    "DEFAULT_MANIFEST_PATH",
    "PROJECT_ROOT",
]
