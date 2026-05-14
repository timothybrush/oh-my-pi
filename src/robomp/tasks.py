"""Task entry points dispatched off the durable event queue."""

from __future__ import annotations

import logging
from typing import Any, Mapping
from urllib.parse import urlparse

from robomp.config import Settings
from robomp.db import Database, IssueRow, IssueState, issue_key
from robomp.github_client import (
    CommentInfo,
    GitHubClient,
    GitHubError,
    IssueInfo,
    RepoInfo,
    parse_issue_payload,
)
from robomp.sandbox import SandboxManager
from robomp.worker import TaskInputs, run_task

log = logging.getLogger(__name__)


def _credentialed_clone_url(clone_url: str, token: str, bot_login: str) -> str:
    parsed = urlparse(clone_url)
    if parsed.scheme not in {"http", "https"}:
        return clone_url
    netloc = parsed.netloc.split("@", 1)[-1]
    return f"{parsed.scheme}://{bot_login}:{token}@{netloc}{parsed.path}"


def _comment_from_payload(payload: Mapping[str, Any]) -> CommentInfo:
    c = payload.get("comment") or {}
    user = c.get("user") or {}
    return CommentInfo(
        id=int(c.get("id") or 0),
        author=str(user.get("login") or ""),
        body=str(c.get("body") or ""),
        created_at=str(c.get("created_at") or ""),
    )


async def _resolve_repo_and_issue(
    github: GitHubClient,
    payload: Mapping[str, Any],
) -> tuple[RepoInfo, IssueInfo]:
    repo, issue = parse_issue_payload(payload)
    if not issue.body:
        # Webhook payloads sometimes omit body; refetch to be safe.
        try:
            issue = await github.get_issue(repo.full_name, issue.number)
        except GitHubError as exc:
            log.warning("issue refetch failed", extra={"err": str(exc)})
    return repo, issue


async def triage_issue(
    *,
    settings: Settings,
    db: Database,
    github: GitHubClient,
    sandbox: SandboxManager,
    payload: Mapping[str, Any],
) -> None:
    repo, issue = await _resolve_repo_and_issue(github, payload)
    if issue.is_pull_request:
        log.info("skip: triage on PR-like issue", extra={"repo": repo.full_name, "n": issue.number})
        return
    key = issue_key(repo.full_name, issue.number)
    db.upsert_issue(key=key, repo=repo.full_name, number=issue.number, state="reproducing")
    clone_url = _credentialed_clone_url(
        repo.clone_url,
        settings.github_token.get_secret_value(),
        settings.bot_login,
    )
    workspace = sandbox.ensure_workspace(
            repo=repo.full_name,
            number=issue.number,
            title=issue.title,
            clone_url=clone_url,
            default_branch=repo.default_branch,
            author_name=settings.resolved_author_name,
            author_email=settings.git_author_email,
        )
    db.upsert_issue(
        key=key,
        repo=repo.full_name,
        number=issue.number,
        state="reproducing",
        branch=workspace.branch,
        session_dir=str(workspace.session_dir),
    )
    inputs = TaskInputs(
        settings=settings,
        db=db,
        github=github,
        repo=repo,
        issue=issue,
        workspace=workspace,
    )
    await run_task(task_kind="triage_issue", inputs=inputs)


async def handle_comment(
    *,
    settings: Settings,
    db: Database,
    github: GitHubClient,
    sandbox: SandboxManager,
    payload: Mapping[str, Any],
) -> None:
    repo, issue = await _resolve_repo_and_issue(github, payload)
    key = issue_key(repo.full_name, issue.number)
    existing = db.get_issue(key)
    if existing is None:
        log.info("skip: comment on unknown issue", extra={"key": key})
        return
    if existing.state in ("merged", "closed", "abandoned"):
        log.info("skip: comment on finalized issue", extra={"key": key, "state": existing.state})
        try:
            await github.post_comment(
                repo.full_name, issue.number,
                "This issue is closed. If the bug is back, please reopen and I'll triage again from scratch.",
            )
        except GitHubError as exc:
            log.warning("ack comment failed", extra={"err": str(exc)})
        return
    comment = _comment_from_payload(payload)
    clone_url = _credentialed_clone_url(
        repo.clone_url,
        settings.github_token.get_secret_value(),
        settings.bot_login,
    )
    workspace = sandbox.ensure_workspace(
            repo=repo.full_name,
            number=issue.number,
            title=issue.title,
            clone_url=clone_url,
            default_branch=repo.default_branch,
            existing_branch=existing.branch,
            author_name=settings.resolved_author_name,
            author_email=settings.git_author_email,
        )
    inputs = TaskInputs(
        settings=settings,
        db=db,
        github=github,
        repo=repo,
        issue=issue,
        workspace=workspace,
    )
    await run_task(task_kind="handle_comment", inputs=inputs, comment=comment)


async def handle_review(
    *,
    settings: Settings,
    db: Database,
    github: GitHubClient,
    sandbox: SandboxManager,
    payload: Mapping[str, Any],
) -> None:
    pr = payload.get("pull_request") or {}
    pr_number = int(pr.get("number") or 0)
    if pr_number <= 0:
        log.info("skip: review without PR number")
        return
    repo_payload = payload.get("repository") or {}
    repo_full = str(repo_payload.get("full_name") or "")
    if not repo_full:
        log.info("skip: review without repo")
        return
    # Discover the originating issue from the DB.
    issue_row = db.find_issue_by_pr(repo_full, pr_number)
    if issue_row is None:
        log.info("skip: review on unknown PR", extra={"repo": repo_full, "pr": pr_number})
        return
    try:
        repo = await github.get_repo(repo_full)
        issue = await github.get_issue(repo_full, issue_row.number)
    except GitHubError as exc:
        log.warning("review fetch failed", extra={"err": str(exc)})
        return
    clone_url = _credentialed_clone_url(
        repo.clone_url,
        settings.github_token.get_secret_value(),
        settings.bot_login,
    )
    workspace = sandbox.ensure_workspace(
            repo=repo.full_name,
            number=issue.number,
            title=issue.title,
            clone_url=clone_url,
            default_branch=repo.default_branch,
            existing_branch=issue_row.branch,
            author_name=settings.resolved_author_name,
            author_email=settings.git_author_email,
        )
    comment = payload.get("comment") or {}
    user = comment.get("user") or {}
    review_payload = {
        "author": str(user.get("login") or ""),
        "body": str(comment.get("body") or ""),
        "path": str(comment.get("path") or ""),
        "line": comment.get("line"),
        "start_line": comment.get("start_line"),
        "original_line": comment.get("original_line"),
    }
    inputs = TaskInputs(
        settings=settings,
        db=db,
        github=github,
        repo=repo,
        issue=issue,
        workspace=workspace,
    )
    await run_task(
        task_kind="handle_review",
        inputs=inputs,
        pr_number=pr_number,
        review_payload=review_payload,
    )


async def handle_pr_conversation(
    *,
    settings: Settings,
    db: Database,
    github: GitHubClient,
    sandbox: SandboxManager,
    payload: Mapping[str, Any],
) -> None:
    """Handle a regular (non-review) comment on a bot-authored PR.

    The `issue_comment.created` payload's `issue.number` IS the PR number on
    these events; we resolve back to the originating issue via the DB and
    drive `handle_comment` so the agent works on the same session/branch.
    """
    repo_payload = payload.get("repository") or {}
    repo_full = str(repo_payload.get("full_name") or "")
    issue_payload = payload.get("issue") or {}
    pr_number = issue_payload.get("number")
    if not repo_full or not isinstance(pr_number, int):
        log.info("skip: pr-conversation missing repo/number")
        return
    issue_row = db.find_issue_by_pr(repo_full, pr_number)
    if issue_row is None:
        log.info("skip: pr-conversation on unknown PR", extra={"repo": repo_full, "pr": pr_number})
        return
    if issue_row.state in ("merged", "closed", "abandoned"):
        log.info("skip: pr-conversation on finalized issue", extra={"key": issue_row.key, "state": issue_row.state})
        # Still acknowledge so the reporter knows the bot saw it.
        try:
            await github.post_comment(
                repo_full, pr_number,
                "This PR has been closed/merged — opening a fresh fix for further changes is recommended. "
                "If this is a regression, reopen the original issue and I'll triage from scratch.",
            )
        except GitHubError as exc:
            log.warning("ack comment failed", extra={"err": str(exc)})
        return
    try:
        repo = await github.get_repo(repo_full)
        issue = await github.get_issue(repo_full, issue_row.number)
    except GitHubError as exc:
        log.warning("pr-conversation fetch failed", extra={"err": str(exc)})
        return
    clone_url = _credentialed_clone_url(
        repo.clone_url,
        settings.github_token.get_secret_value(),
        settings.bot_login,
    )
    workspace = sandbox.ensure_workspace(
            repo=repo.full_name,
            number=issue.number,
            title=issue.title,
            clone_url=clone_url,
            default_branch=repo.default_branch,
            existing_branch=issue_row.branch,
            author_name=settings.resolved_author_name,
            author_email=settings.git_author_email,
        )
    comment = _comment_from_payload(payload)
    inputs = TaskInputs(
        settings=settings, db=db, github=github,
        repo=repo, issue=issue, workspace=workspace,
    )
    await run_task(task_kind="handle_comment", inputs=inputs, comment=comment, pr_number=pr_number)


async def cleanup_workspace(
    *,
    settings: Settings,
    db: Database,
    sandbox: SandboxManager,
    payload: Mapping[str, Any],
    target_state: IssueState,
) -> None:
    """Tear down the workspace for a finished issue/PR."""
    repo_payload = payload.get("repository") or {}
    repo_full = str(repo_payload.get("full_name") or "")
    if not repo_full:
        return
    issue_payload = payload.get("issue") or payload.get("pull_request") or {}
    number = issue_payload.get("number")
    if not isinstance(number, int):
        return
    # If this is a PR close, map to the originating issue.
    issue_row: IssueRow | None
    if "pull_request" in payload:
        issue_row = db.find_issue_by_pr(repo_full, number)
    else:
        issue_row = db.get_issue(issue_key(repo_full, number))
    if issue_row is None:
        return
    sandbox.remove_workspace(repo=issue_row.repo, number=issue_row.number)
    db.set_issue_state(issue_row.key, target_state)
    log.info("cleanup", extra={"key": issue_row.key, "state": target_state})


__all__ = [
    "cleanup_workspace",
    "handle_comment",
    "handle_pr_conversation",
    "handle_review",
    "triage_issue",
]
