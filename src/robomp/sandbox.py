"""Per-issue workspace lifecycle: clone pool + git worktrees."""

from __future__ import annotations

import logging
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Workspace:
    """Resolved per-issue scratch space."""

    root: Path
    repo_dir: Path
    session_dir: Path
    context_dir: Path
    artifacts_dir: Path
    branch: str
    repo_full_name: str
    issue_number: int

    @property
    def repro_dir(self) -> Path:
        return self.context_dir / "repro"


def _slug(text: str, *, length: int = 40) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not cleaned:
        cleaned = "issue"
    return cleaned[:length]


def _short_hex(seed: str | None = None) -> str:
    if seed:
        import hashlib

        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return secrets.token_hex(4)


def workspace_key(repo: str, number: int) -> str:
    return f"{repo.replace('/', '__')}__{number}"


def make_branch(*, issue_number: int, title: str, seed: str | None = None) -> str:
    return f"farm/{_short_hex(seed or f'{issue_number}-{title}')}/{_slug(title or f'issue-{issue_number}')}"


_CRED_URL = re.compile(r"(https?://)([^:/@\s]+):([^@/\s]+)@")


def redact_credentials(text: str | None) -> str:
    """Strip `user:password@` from any embedded URL in the given string."""
    if not text:
        return text or ""
    return _CRED_URL.sub(r"\1***@", text)


def _redacted_cmd(cmd: list[str]) -> list[str]:
    return [redact_credentials(part) for part in cmd]


class GitCommandError(RuntimeError):
    """Wraps a failed git subprocess with credentials redacted from argv and stderr."""

    def __init__(self, cmd: list[str], returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = redact_credentials(stdout)
        self.stderr = redact_credentials(stderr)
        self.cmd = _redacted_cmd(cmd)
        msg = self.stderr.strip() or self.stdout.strip() or f"exit {returncode}"
        super().__init__(f"git {' '.join(self.cmd[1:])} failed: {msg}")


def _run(cmd: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    log.debug("git", extra={"cmd": _redacted_cmd(cmd), "cwd": str(cwd) if cwd else None})
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env={**(env or {})} if env else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitCommandError(cmd, proc.returncode, proc.stdout, proc.stderr)
    return proc


def _safe_run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run without raising; caller decides on returncode. Credentials are redacted from any captured output."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        proc.stdout = redact_credentials(proc.stdout)
    if proc.stderr:
        proc.stderr = redact_credentials(proc.stderr)
    return proc


class SandboxManager:
    """Manages a shared clone pool and per-issue worktrees."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.pool = root / "_pool"
        root.mkdir(parents=True, exist_ok=True)
        self.pool.mkdir(parents=True, exist_ok=True)

    # ---- pool ----
    def pool_path(self, repo: str) -> Path:
        return self.pool / repo.replace("/", "__")

    def ensure_clone(self, *, repo: str, clone_url: str, default_branch: str) -> Path:
        """Idempotent shared clone for `repo`.

        `clone_url` may include credentials; never logged or echoed.
        """
        target = self.pool_path(repo)
        if (target / ".git").exists() or (target / "HEAD").exists():
            # Refresh origin URL so a rotated PAT or changed bot login takes effect
            # the next time we fetch through the pool.
            _safe_run(["git", "remote", "set-url", "origin", clone_url], cwd=target)
            _safe_run(["git", "fetch", "--prune", "origin"], cwd=target)
            return target
        target.mkdir(parents=True, exist_ok=True)
        _run([
            "git",
            "clone",
            "--filter=blob:none",
            "--no-tags",
            "--branch",
            default_branch,
            clone_url,
            str(target),
        ])
        return target

    # ---- per-issue workspace ----
    def workspace_root(self, repo: str, number: int) -> Path:
        return self.root / workspace_key(repo, number)

    def ensure_workspace(
        self,
        *,
        repo: str,
        number: int,
        title: str,
        clone_url: str,
        default_branch: str,
        existing_branch: str | None = None,
        author_name: str = "robomp",
        author_email: str = "robomp@users.noreply.github.com",
    ) -> Workspace:
        """Create or resume a per-issue worktree."""
        pool = self.ensure_clone(repo=repo, clone_url=clone_url, default_branch=default_branch)
        ws_root = self.workspace_root(repo, number)
        repo_dir = ws_root / "repo"
        session_dir = ws_root / ".omp-session"
        context_dir = ws_root / "context"
        artifacts_dir = ws_root / "artifacts"
        for path in (ws_root, session_dir, context_dir, context_dir / "repro", artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)

        branch = existing_branch or make_branch(
            issue_number=number,
            title=title,
            seed=f"{repo}#{number}",
        )

        if not (repo_dir / ".git").exists():
            # Make sure the branch's base ref exists locally.
            _safe_run(["git", "fetch", "origin", default_branch], cwd=pool)
            # Try worktree add; if the branch already exists in the pool, reuse it.
            check = _safe_run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], cwd=pool)
            if check.returncode == 0:
                _run(["git", "worktree", "add", str(repo_dir), branch], cwd=pool)
            else:
                _run([
                    "git", "worktree", "add", "-b", branch, str(repo_dir),
                    f"origin/{default_branch}",
                ], cwd=pool)
        # Re-set the credentialed origin URL + identity unconditionally so a
        # rotated PAT, changed bot login, or pre-existing worktree all use the
        # current credentials and author config.
        _safe_run(["git", "remote", "set-url", "origin", clone_url], cwd=repo_dir)
        _safe_run(["git", "config", "user.email", author_email], cwd=repo_dir)
        _safe_run(["git", "config", "user.name", author_name], cwd=repo_dir)
        return Workspace(
            root=ws_root,
            repo_dir=repo_dir,
            session_dir=session_dir,
            context_dir=context_dir,
            artifacts_dir=artifacts_dir,
            branch=branch,
            repo_full_name=repo,
            issue_number=number,
        )

    def remove_workspace(self, *, repo: str, number: int) -> None:
        ws_root = self.workspace_root(repo, number)
        repo_dir = ws_root / "repo"
        if repo_dir.exists():
            pool = self.pool_path(repo)
            _safe_run(["git", "worktree", "remove", "--force", str(repo_dir)], cwd=pool)
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)
        if ws_root.exists():
            shutil.rmtree(ws_root, ignore_errors=True)


__all__ = [
    "SandboxManager",
    "Workspace",
    "make_branch",
    "workspace_key",
]
