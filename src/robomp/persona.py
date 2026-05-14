"""Prompt template loader + renderer.

Templates use a tiny mustache-style `{{path.to.value}}` placeholder. We do not
import a real template engine: the substitution rules are deliberately
restrictive so a malformed prompt is impossible to render with surprising
side-effects.
"""

from __future__ import annotations

import re
from functools import cache
from importlib import resources
from typing import Any, Mapping

from robomp.github_client import CommentInfo, IssueInfo, RepoInfo
from robomp.sandbox import Workspace

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _lookup(path: str, scope: Mapping[str, Any]) -> str:
    parts = path.split(".")
    value: Any = scope
    for part in parts:
        if isinstance(value, Mapping):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
        if value is None:
            return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def render(template: str, scope: Mapping[str, Any]) -> str:
    return _PLACEHOLDER.sub(lambda m: _lookup(m.group(1), scope), template)


@cache
def _load(name: str) -> str:
    return resources.files("robomp.prompts").joinpath(name).read_text(encoding="utf-8")


def system_append(*, repo: RepoInfo, issue: IssueInfo, workspace: Workspace) -> str:
    return render(_load("system_append.md"), {"repo": repo, "issue": issue, "workspace": workspace})


def kickoff(*, repo: RepoInfo, issue: IssueInfo, workspace: Workspace) -> str:
    return render(_load("kickoff_issue.md"), {"repo": repo, "issue": issue, "workspace": workspace})


def followup_comment(
    *,
    repo: RepoInfo,
    issue: IssueInfo,
    comment: CommentInfo,
    workspace: Workspace,
    pr_status: str,
) -> str:
    return render(
        _load("followup_comment.md"),
        {
            "repo": repo,
            "issue": issue,
            "workspace": workspace,
            "comment": comment,
            "state": {"pr_status": pr_status},
        },
    )


def followup_review(
    *,
    repo: RepoInfo,
    workspace: Workspace,
    pr_number: int,
    comment_author: str,
    comment_body: str,
    comment_path: str,
    comment_line_range: str,
) -> str:
    return render(
        _load("followup_review.md"),
        {
            "repo": repo,
            "workspace": workspace,
            "pr": {"number": pr_number},
            "comment": {
                "author": comment_author,
                "body": comment_body,
                "path": comment_path,
                "line_range": comment_line_range,
            },
        },
    )


__all__ = [
    "followup_comment",
    "followup_review",
    "kickoff",
    "render",
    "system_append",
]
