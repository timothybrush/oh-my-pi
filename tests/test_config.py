from __future__ import annotations

import pytest

from robomp.config import Settings, reset_settings_cache


def test_settings_load_from_env(env: dict[str, str]) -> None:
    cfg = Settings()  # type: ignore[call-arg]
    assert cfg.bot_login == "robomp-bot"
    assert cfg.repo_allowlist == frozenset({"octo/widget"})
    assert cfg.allows("octo/widget")
    assert cfg.allows("Octo/Widget")
    assert not cfg.allows("other/widget")


def test_settings_missing_required(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(str(tmp_path))
    for key in (
        "GITHUB_TOKEN",
        "GITHUB_WEBHOOK_SECRET",
        "ROBOMP_BOT_LOGIN",
        "ROBOMP_REPO_ALLOWLIST",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    with pytest.raises(Exception):
        Settings()  # type: ignore[call-arg]


def test_allowlist_csv_parsing(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.setenv("ROBOMP_REPO_ALLOWLIST", "  alpha/one ,beta/two, ,gamma/three ")
    reset_settings_cache()
    cfg = Settings()  # type: ignore[call-arg]
    assert cfg.repo_allowlist == frozenset({"alpha/one", "beta/two", "gamma/three"})


def test_blank_replay_token_treated_as_disabled(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.setenv("ROBOMP_REPLAY_TOKEN", "")
    reset_settings_cache()
    cfg = Settings()  # type: ignore[call-arg]
    assert cfg.replay_token is None


def test_whitespace_replay_token_treated_as_disabled(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.setenv("ROBOMP_REPLAY_TOKEN", "   ")
    reset_settings_cache()
    cfg = Settings()  # type: ignore[call-arg]
    assert cfg.replay_token is None


def test_real_replay_token_preserved(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.setenv("ROBOMP_REPLAY_TOKEN", "abc")
    reset_settings_cache()
    cfg = Settings()  # type: ignore[call-arg]
    assert cfg.replay_token is not None
    assert cfg.replay_token.get_secret_value() == "abc"


def test_blank_bot_login_rejected(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.setenv("ROBOMP_BOT_LOGIN", "   ")
    reset_settings_cache()
    with pytest.raises(Exception):
        Settings()  # type: ignore[call-arg]
