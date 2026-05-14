from __future__ import annotations

import hashlib
import hmac

from robomp.github_events import route, verify_signature

ALLOWLIST = frozenset({"octo/widget"})
BOT = "robomp-bot"


def test_verify_signature_positive() -> None:
    secret = "shh"
    body = b'{"x":1}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(secret, body, f"sha256={sig}")


def test_verify_signature_rejects_missing_header() -> None:
    assert not verify_signature("shh", b"{}", None)
    assert not verify_signature("shh", b"{}", "")
    assert not verify_signature("shh", b"{}", "md5=deadbeef")


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b'{"x":1}'
    sig = hmac.new(b"right", body, hashlib.sha256).hexdigest()
    assert not verify_signature("wrong", body, f"sha256={sig}")


def test_route_issue_opened_queues_triage() -> None:
    decision = route(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 4, "user": {"login": "alice"}},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert decision.should_queue
    assert decision.task == "triage_issue"
    assert decision.issue_key == "octo/widget#4"


def test_route_skips_disallowed_repo() -> None:
    decision = route(
        "issues",
        {"action": "opened", "issue": {"number": 1}, "repository": {"full_name": "other/repo"}},
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert not decision.should_queue
    assert "allowlist" in decision.reason


def test_route_skips_self_comment() -> None:
    decision = route(
        "issue_comment",
        {
            "action": "created",
            "comment": {"user": {"login": BOT}, "body": "hi"},
            "issue": {"number": 4},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert not decision.should_queue


def test_route_skips_bot_suffix_comment() -> None:
    decision = route(
        "issue_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": "ci ran"},
            "issue": {"number": 4},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert not decision.should_queue
    assert "bot" in decision.reason


def test_route_skips_user_type_bot() -> None:
    decision = route(
        "issue_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "renovate", "type": "Bot"}, "body": "deps"},
            "issue": {"number": 4},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert not decision.should_queue


def test_route_comment_routes_handle_comment() -> None:
    decision = route(
        "issue_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "alice"}, "body": "hi"},
            "issue": {"number": 4},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert decision.should_queue
    assert decision.task == "handle_comment"
    assert decision.issue_key == "octo/widget#4"


def test_route_pr_conversation_uses_handle_pr_conversation() -> None:
    """A regular comment on a PR (not a review) must NOT route to handle_review."""
    decision = route(
        "issue_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "alice"}, "body": "looks good"},
            "issue": {"number": 9, "pull_request": {"url": "x"}},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert decision.should_queue
    assert decision.task == "handle_pr_conversation"


def test_route_pr_conversation_uses_resolver_for_inflight_key() -> None:
    """PR-derived events MUST serialize on the originating issue's key."""

    def resolver(repo: str, pr_number: int) -> str | None:
        assert repo == "octo/widget"
        assert pr_number == 9
        return "octo/widget#42"

    decision = route(
        "issue_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "alice"}, "body": "looks good"},
            "issue": {"number": 9, "pull_request": {"url": "x"}},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
        resolve_issue_from_pr=resolver,
    )
    assert decision.should_queue
    # Same key as if the user had commented on issue #42 directly.
    assert decision.issue_key == "octo/widget#42"


def test_route_pr_conversation_falls_back_when_resolver_misses() -> None:
    """If the DB doesn't know the PR yet, fall back to a PR-scoped key."""

    decision = route(
        "issue_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "alice"}, "body": "hi"},
            "issue": {"number": 9, "pull_request": {"url": "x"}},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
        resolve_issue_from_pr=lambda _r, _n: None,
    )
    assert decision.should_queue
    assert decision.issue_key == "octo/widget#pr-9"


def test_route_review_only_for_bot_authored_pr() -> None:
    decision = route(
        "pull_request_review_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "alice"}, "body": "nit"},
            "pull_request": {"number": 9, "user": {"login": BOT}},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
        resolve_issue_from_pr=lambda _r, _n: "octo/widget#42",
    )
    assert decision.should_queue
    assert decision.task == "handle_review"
    assert decision.issue_key == "octo/widget#42"

    not_ours = route(
        "pull_request_review_comment",
        {
            "action": "created",
            "comment": {"user": {"login": "alice"}, "body": "nit"},
            "pull_request": {"number": 9, "user": {"login": "someone-else"}},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert not not_ours.should_queue


def test_route_pr_closed_only_when_merged_by_bot() -> None:
    payload = {
        "action": "closed",
        "pull_request": {"number": 9, "user": {"login": BOT}, "merged": True},
        "repository": {"full_name": "octo/widget"},
    }
    decision = route(
        "pull_request", payload, allowlist=ALLOWLIST, bot_login=BOT,
        resolve_issue_from_pr=lambda _r, _n: "octo/widget#42",
    )
    assert decision.should_queue
    assert decision.task == "cleanup_workspace"
    assert decision.issue_key == "octo/widget#42"

    payload["pull_request"]["merged"] = False  # type: ignore[index]
    assert not route("pull_request", payload, allowlist=ALLOWLIST, bot_login=BOT).should_queue


def test_route_skips_pull_request_issues_event() -> None:
    decision = route(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 4, "pull_request": {"url": "x"}},
            "repository": {"full_name": "octo/widget"},
        },
        allowlist=ALLOWLIST,
        bot_login=BOT,
    )
    assert not decision.should_queue
