from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from robomp.sandbox import SandboxManager, make_branch, workspace_key


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def upstream_repo(tmp_path: Path) -> Path:
    """Create a local --bare-ish remote with one commit on main."""
    repo = tmp_path / "upstream.git"
    repo.mkdir()
    _git(["init", "--initial-branch=main", "--bare", str(repo)], cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["init", "--initial-branch=main", str(seed)], cwd=tmp_path)
    (seed / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["-C", str(seed), "add", "."], cwd=tmp_path)
    env = os.environ | {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(seed), check=True, capture_output=True, text=True, env=env,
    )
    _git(["-C", str(seed), "remote", "add", "origin", str(repo)], cwd=tmp_path)
    _git(["-C", str(seed), "push", "origin", "main"], cwd=tmp_path)
    return repo


def test_workspace_key_and_branch_shape() -> None:
    assert workspace_key("oven-sh/bun", 30654) == "oven-sh__bun__30654"
    branch = make_branch(issue_number=30654, title="JSON.parse crashes on BOM", seed="oven-sh/bun#30654")
    assert branch.startswith("farm/")
    parts = branch.split("/")
    assert len(parts) == 3 and len(parts[1]) == 8
    assert "json-parse-crashes" in parts[2]


def test_ensure_workspace_creates_worktree(tmp_path: Path, upstream_repo: Path) -> None:
    mgr = SandboxManager(tmp_path / "workspaces")
    ws = mgr.ensure_workspace(
        repo="octo/widget",
        number=42,
        title="something is wrong",
        clone_url=str(upstream_repo),
        default_branch="main",
    )
    assert ws.repo_dir.is_dir()
    assert (ws.repo_dir / "README.md").read_text() == "hello\n"
    # Branch is checked out.
    result = subprocess.run(
        ["git", "-C", str(ws.repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == ws.branch
    assert ws.branch.startswith("farm/")
    # Session and context dirs exist.
    assert ws.session_dir.is_dir()
    assert ws.context_dir.is_dir()
    assert ws.repro_dir.is_dir()
    assert ws.artifacts_dir.is_dir()


def test_ensure_workspace_is_idempotent(tmp_path: Path, upstream_repo: Path) -> None:
    mgr = SandboxManager(tmp_path / "workspaces")
    ws1 = mgr.ensure_workspace(
        repo="octo/widget", number=5, title="t",
        clone_url=str(upstream_repo), default_branch="main",
    )
    ws2 = mgr.ensure_workspace(
        repo="octo/widget", number=5, title="t",
        clone_url=str(upstream_repo), default_branch="main",
    )
    assert ws1.repo_dir == ws2.repo_dir
    assert ws1.branch == ws2.branch


def test_remove_workspace(tmp_path: Path, upstream_repo: Path) -> None:
    mgr = SandboxManager(tmp_path / "workspaces")
    ws = mgr.ensure_workspace(
        repo="octo/widget", number=12, title="t",
        clone_url=str(upstream_repo), default_branch="main",
    )
    assert ws.repo_dir.exists()
    mgr.remove_workspace(repo="octo/widget", number=12)
    assert not ws.repo_dir.exists()
    assert not ws.root.exists()


def test_redact_credentials_strips_userinfo() -> None:
    from robomp.sandbox import redact_credentials
    assert (
        redact_credentials("Cloning into 'x' from https://bot:ghp_secret@github.com/o/r.git failed")
        == "Cloning into 'x' from https://***@github.com/o/r.git failed"
    )
    # Multiple URLs in one string.
    assert (
        redact_credentials("a https://x:y@example.com b https://q:z@example.org c")
        == "a https://***@example.com b https://***@example.org c"
    )
    # No-op on strings without credentials.
    assert redact_credentials("plain message") == "plain message"
    assert redact_credentials(None) == ""


def test_git_command_error_redacts_url_in_args_and_stderr(tmp_path: Path) -> None:
    """An ENOENT-style git failure on a credentialed clone URL must not echo the token."""
    from robomp.sandbox import _run
    import pytest as _pytest

    cred_url = "https://bot:ghp_abc123secret@example.invalid/o/r.git"
    with _pytest.raises(Exception) as exc:
        _run(["git", "clone", cred_url, str(tmp_path / "out")])
    text = str(exc.value)
    assert "ghp_abc123secret" not in text
    assert "bot" not in text or "https://bot:" not in text
    assert "***" in text or "example.invalid" in text
