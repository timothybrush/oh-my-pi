"""Typed webhook payload parsing + dispatch routing."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from robomp.db import issue_key

log = logging.getLogger(__name__)

Decision = Literal["queue", "skip"]


@dataclass(slots=True, frozen=True)
class RouteDecision:
    decision: Decision
    task: str | None
    repo: str | None
    issue_key: str | None
    reason: str

    @property
    def should_queue(self) -> bool:
        return self.decision == "queue"


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time HMAC-SHA256 verification of `X-Hub-Signature-256`."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def _repo_full_name(payload: Mapping[str, Any]) -> str | None:
    repo = payload.get("repository")
    if isinstance(repo, dict):
        full = repo.get("full_name")
        if isinstance(full, str):
            return full
    return None


PrIssueResolver = Callable[[str, int], str | None] | None


def _is_bot_account(user: Mapping[str, Any] | None, bot_login: str) -> bool:
    if not isinstance(user, Mapping):
        return False
    login = str(user.get("login") or "")
    if not login:
        return False
    if login == bot_login:
        return True
    if login.endswith("[bot]"):
        return True
    if str(user.get("type") or "") == "Bot":
        return True
    return False


def route(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    allowlist: frozenset[str],
    bot_login: str,
    resolve_issue_from_pr: PrIssueResolver = None,
) -> RouteDecision:
    """Decide whether and how to handle a webhook event.

    `resolve_issue_from_pr(repo, pr_number)` maps a PR number back to its
    originating-issue key (e.g. `octo/widget#42`). Used so PR-derived events
    serialize on the *same* inflight key as the issue's own events. When the
    mapping is unknown (no DB row yet), we fall back to a PR-scoped key.
    """
    repo = _repo_full_name(payload)
    if repo is None or repo.lower() not in allowlist:
        return RouteDecision("skip", None, repo, None, "repo not on allowlist")

    action = str(payload.get("action") or "")

    def _resolve_pr_key(pr_number: int) -> str:
        if resolve_issue_from_pr is not None:
            resolved = resolve_issue_from_pr(repo, pr_number)  # type: ignore[arg-type]
            if resolved:
                return resolved
        return f"{repo}#pr-{pr_number}"

    if event_type == "issues":
        issue = payload.get("issue") or {}
        if "pull_request" in issue:
            return RouteDecision("skip", None, repo, None, "issue is a pull request")
        number = issue.get("number")
        if not isinstance(number, int):
            return RouteDecision("skip", None, repo, None, "issue missing number")
        key = issue_key(repo, number)
        if action == "opened":
            return RouteDecision("queue", "triage_issue", repo, key, "issues.opened")
        if action == "closed":
            return RouteDecision("queue", "cleanup_workspace", repo, key, "issues.closed")
        return RouteDecision("skip", None, repo, key, f"issues.{action} ignored")

    if event_type == "issue_comment" and action == "created":
        comment = payload.get("comment") or {}
        if _is_bot_account(comment.get("user"), bot_login):
            return RouteDecision("skip", None, repo, None, "bot/self comment")
        issue = payload.get("issue") or {}
        number = issue.get("number")
        if not isinstance(number, int):
            return RouteDecision("skip", None, repo, None, "comment missing issue number")
        if "pull_request" in issue:
            # Conversation comment on a PR. The PR number lives at issue.number
            # on this payload type; the *originating-issue* key is whatever
            # the resolver returns. Serialize on the issue, not the PR.
            key = _resolve_pr_key(number)
            return RouteDecision("queue", "handle_pr_conversation", repo, key,
                                 f"issue_comment.created on PR #{number}")
        key = issue_key(repo, number)
        return RouteDecision("queue", "handle_comment", repo, key, "issue_comment.created")

    if event_type == "pull_request_review_comment" and action == "created":
        comment = payload.get("comment") or {}
        if _is_bot_account(comment.get("user"), bot_login):
            return RouteDecision("skip", None, repo, None, "bot/self review comment")
        pr = payload.get("pull_request") or {}
        pr_user = pr.get("user") or {}
        if str(pr_user.get("login") or "") != bot_login:
            return RouteDecision("skip", None, repo, None, "PR not authored by bot")
        number = pr.get("number")
        if not isinstance(number, int):
            return RouteDecision("skip", None, repo, None, "PR missing number")
        return RouteDecision("queue", "handle_review", repo, _resolve_pr_key(number),
                             "pull_request_review_comment.created")

    if event_type == "pull_request" and action == "closed":
        pr = payload.get("pull_request") or {}
        pr_user = pr.get("user") or {}
        if str(pr_user.get("login") or "") != bot_login:
            return RouteDecision("skip", None, repo, None, "PR not bot-authored")
        if not bool(pr.get("merged")):
            return RouteDecision("skip", None, repo, None, "PR closed without merge")
        number = pr.get("number")
        if not isinstance(number, int):
            return RouteDecision("skip", None, repo, None, "PR missing number")
        return RouteDecision("queue", "cleanup_workspace", repo, _resolve_pr_key(number),
                             "pull_request.merged")

    return RouteDecision("skip", None, repo, None, f"{event_type}.{action} not handled")


__all__ = [
    "Decision",
    "RouteDecision",
    "route",
    "verify_signature",
]
