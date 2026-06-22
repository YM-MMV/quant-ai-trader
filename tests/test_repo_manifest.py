"""Tests for external-repo manifest loading and the repo manager.

All tests are offline: no real ``git`` runs and no network is touched. The
manager's runner is mocked and clone targets live under temp directories.
"""
import pytest
from pydantic import ValidationError

from services.resource_service.repo_manager import RepoManager, SyncResult
from services.resource_service.repo_manifest import (
    DEFAULT_MANIFEST_PATH,
    RepoManifest,
    RepoSpec,
    load_manifest,
)

EXPECTED_REPOS = {
    "quant_trading": ("https://github.com/je-suis-tm/quant-trading", "external/quant-trading", "strategy_source"),
    "quantdinger": ("https://github.com/brokermr810/QuantDinger", "external/QuantDinger", "backtest_and_trading_platform_reference"),
    "openbb": ("https://github.com/OpenBB-finance/OpenBB", "external/OpenBB", "data_platform_reference"),
    "kronos": ("https://github.com/shiyu-coder/Kronos", "external/Kronos", "kline_prediction_model"),
    "codebase_memory_mcp": ("https://github.com/DeusData/codebase-memory-mcp", "external/codebase-memory-mcp", "codebase_intelligence"),
    "worldmonitor": ("https://github.com/koala73/worldmonitor", "external/worldmonitor", "optional_macro_news_monitoring_later"),
}


# --------------------------------------------------------------------------- #
# Fake runner — records commands, never touches git/network
# --------------------------------------------------------------------------- #
class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self, returncode=0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd):
        self.calls.append(cmd)
        return FakeProc(returncode=self.returncode, stdout="done")


@pytest.fixture
def manifest() -> RepoManifest:
    return load_manifest()


# --------------------------------------------------------------------------- #
# Manifest loading
# --------------------------------------------------------------------------- #
def test_manifest_file_exists():
    assert DEFAULT_MANIFEST_PATH.is_file()


def test_manifest_loads_all_repos(manifest):
    assert set(manifest.names()) == set(EXPECTED_REPOS)


@pytest.mark.parametrize("name", list(EXPECTED_REPOS))
def test_manifest_repo_fields(manifest, name):
    url, local_path, role = EXPECTED_REPOS[name]
    spec = manifest.get(name)
    assert spec.url == url
    assert spec.local_path == local_path
    assert spec.role == role


def test_manifest_get_unknown_raises(manifest):
    with pytest.raises(KeyError):
        manifest.get("does_not_exist")


def test_repospec_rejects_non_https():
    with pytest.raises(ValidationError):
        RepoSpec(url="git@github.com:foo/bar.git", local_path="external/bar", role="x")


def test_repospec_rejects_path_traversal():
    with pytest.raises(ValidationError):
        RepoSpec(url="https://github.com/foo/bar", local_path="../escape", role="x")


def test_repospec_rejects_absolute_path():
    with pytest.raises(ValidationError):
        RepoSpec(url="https://github.com/foo/bar", local_path="/etc/passwd", role="x")


def test_repospec_rejects_unknown_field():
    with pytest.raises(ValidationError):
        RepoSpec(
            url="https://github.com/foo/bar",
            local_path="external/bar",
            role="x",
            bogus=1,
        )


def test_manifest_requires_at_least_one_repo():
    with pytest.raises(ValidationError):
        RepoManifest(repos={})


# --------------------------------------------------------------------------- #
# Command construction (no execution)
# --------------------------------------------------------------------------- #
def test_resolve_path_anchored_at_base_dir(manifest, tmp_path):
    mgr = RepoManager(manifest, base_dir=tmp_path)
    spec = manifest.get("kronos")
    assert mgr.resolve_path(spec) == tmp_path / "external" / "Kronos"


def test_clone_command(manifest, tmp_path):
    mgr = RepoManager(manifest, base_dir=tmp_path)
    spec = manifest.get("openbb")
    cmd = mgr.clone_command(spec)
    assert cmd[:2] == ["git", "clone"]
    assert spec.url in cmd
    assert str(tmp_path / "external" / "OpenBB") in cmd


def test_clone_command_with_depth(manifest, tmp_path):
    mgr = RepoManager(manifest, base_dir=tmp_path)
    cmd = mgr.clone_command(manifest.get("openbb"), depth=1)
    assert "--depth" in cmd and "1" in cmd


def test_clone_command_with_ref(tmp_path):
    spec = RepoSpec(
        url="https://github.com/foo/bar", local_path="external/bar", role="x", ref="v1.2.3"
    )
    mgr = RepoManager(RepoManifest(repos={"bar": spec}), base_dir=tmp_path)
    cmd = mgr.clone_command(spec)
    assert "--branch" in cmd and "v1.2.3" in cmd


def test_update_command(manifest, tmp_path):
    mgr = RepoManager(manifest, base_dir=tmp_path)
    spec = manifest.get("kronos")
    cmd = mgr.update_command(spec)
    assert cmd[:3] == ["git", "-C", str(tmp_path / "external" / "Kronos")]
    assert cmd[-2:] == ["pull", "--ff-only"]


def test_command_for_picks_clone_then_update(manifest, tmp_path):
    mgr = RepoManager(manifest, base_dir=tmp_path)
    spec = manifest.get("kronos")
    # Not cloned yet -> clone.
    assert mgr.command_for(spec)[:2] == ["git", "clone"]
    # Simulate an existing checkout with a .git dir -> update.
    (tmp_path / "external" / "Kronos" / ".git").mkdir(parents=True)
    assert mgr.is_cloned(spec)
    assert mgr.command_for(spec)[:2] == ["git", "-C"]


# --------------------------------------------------------------------------- #
# Sync with mocked runner
# --------------------------------------------------------------------------- #
def test_sync_dry_run_does_not_call_runner(manifest, tmp_path):
    runner = FakeRunner()
    mgr = RepoManager(manifest, base_dir=tmp_path, runner=runner)
    result = mgr.sync("kronos", manifest.get("kronos"), dry_run=True)
    assert isinstance(result, SyncResult)
    assert result.action == "clone"
    assert result.skipped is True
    assert runner.calls == []  # nothing executed


def test_sync_clone_invokes_runner_and_makes_parent(manifest, tmp_path):
    runner = FakeRunner(returncode=0)
    mgr = RepoManager(manifest, base_dir=tmp_path, runner=runner)
    result = mgr.sync("openbb", manifest.get("openbb"))
    assert len(runner.calls) == 1
    assert runner.calls[0][:2] == ["git", "clone"]
    assert result.action == "clone"
    assert result.ok
    # Parent (external/) created so a real clone would have somewhere to land.
    assert (tmp_path / "external").is_dir()


def test_sync_update_when_already_cloned(manifest, tmp_path):
    runner = FakeRunner()
    mgr = RepoManager(manifest, base_dir=tmp_path, runner=runner)
    (tmp_path / "external" / "Kronos" / ".git").mkdir(parents=True)
    result = mgr.sync("kronos", manifest.get("kronos"))
    assert result.action == "update"
    assert runner.calls[0][-2:] == ["pull", "--ff-only"]


def test_sync_reports_failure(manifest, tmp_path):
    runner = FakeRunner(returncode=128)
    mgr = RepoManager(manifest, base_dir=tmp_path, runner=runner)
    result = mgr.sync("openbb", manifest.get("openbb"))
    assert not result.ok
    assert result.returncode == 128


def test_sync_all_covers_every_repo(manifest, tmp_path):
    runner = FakeRunner()
    mgr = RepoManager(manifest, base_dir=tmp_path, runner=runner)
    results = mgr.sync_all(dry_run=True)
    assert {r.name for r in results} == set(EXPECTED_REPOS)
    assert all(r.action == "clone" for r in results)
    assert runner.calls == []  # dry run executes nothing
