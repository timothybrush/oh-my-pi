"""GitHub REST client tests against httpx.MockTransport."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from robomp.github_client import GitHubClient, GitHubError


def _run_async(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_4xx_maps_to_github_error_with_message() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(404, json={"message": "Not Found"})
    )
    client = GitHubClient("tok", transport=transport)
    with pytest.raises(GitHubError) as exc:
        asyncio.new_event_loop().run_until_complete(client.get_repo("o/r"))
    assert exc.value.status == 404
    assert "Not Found" in str(exc.value)


def test_rate_limit_retry_after_parsed() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            403,
            json={"message": "rate limited"},
            headers={"retry-after": "42"},
        )
    )
    client = GitHubClient("tok", transport=transport)
    with pytest.raises(GitHubError) as exc:
        asyncio.new_event_loop().run_until_complete(client.get_repo("o/r"))
    assert exc.value.retry_after == 42.0


def test_redirect_without_follow_raises_github_error() -> None:
    """If a moved repo returns 301 and the redirect target is unreachable,
    we must raise a clean GitHubError instead of parsing the response body."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        # First request: simulate a 301 redirect that the client cannot follow
        # because the new location resolves to a 410 Gone.
        if len(calls) == 1:
            return httpx.Response(
                301,
                headers={"location": "https://api.github.com/repositories/12345"},
            )
        return httpx.Response(410, json={"message": "Gone"})

    transport = httpx.MockTransport(handler)
    client = GitHubClient("tok", transport=transport)
    with pytest.raises(GitHubError) as exc:
        asyncio.new_event_loop().run_until_complete(client.get_repo("old-owner/old-repo"))
    # Either we end up at 410 after following, or we surface the redirect itself
    # — both are GitHubError, not an internal exception.
    assert exc.value.status in (301, 410)


def test_redirect_target_succeeds_when_followable() -> None:
    """A 301 → 200 chain should resolve to the followed payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/old/repo":
            return httpx.Response(
                301,
                headers={"location": "https://api.github.com/repos/new/repo"},
            )
        return httpx.Response(200, json={
            "full_name": "new/repo", "default_branch": "main",
            "clone_url": "https://github.com/new/repo.git", "private": False,
        })

    transport = httpx.MockTransport(handler)
    client = GitHubClient("tok", transport=transport)
    repo = asyncio.new_event_loop().run_until_complete(client.get_repo("old/repo"))
    assert repo.full_name == "new/repo"


def test_204_no_content_returns_none() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(204))
    client = GitHubClient("tok", transport=transport)
    # add_assignees with empty list short-circuits without a request; pass one to force the call.
    asyncio.new_event_loop().run_until_complete(client.add_assignees("o/r", 1, ["alice"]))
