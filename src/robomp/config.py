"""Env-driven configuration for robomp."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ThinkingLevel = Literal["off", "low", "medium", "high"]


class Settings(BaseSettings):
    """Strongly-typed runtime configuration.

    Loaded from process env, optionally pre-populated by `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # GitHub
    github_token: SecretStr = Field(..., alias="GITHUB_TOKEN")
    github_webhook_secret: SecretStr = Field(..., alias="GITHUB_WEBHOOK_SECRET")
    bot_login: str = Field(..., alias="ROBOMP_BOT_LOGIN")
    git_author_name: str | None = Field(None, alias="ROBOMP_GIT_AUTHOR_NAME")
    git_author_email: str = Field(..., alias="ROBOMP_GIT_AUTHOR_EMAIL")
    repo_allowlist_raw: str = Field("", alias="ROBOMP_REPO_ALLOWLIST")

    # Model selection
    model: str = Field("anthropic/claude-sonnet-4-5", alias="ROBOMP_MODEL")
    provider: str | None = Field(None, alias="ROBOMP_PROVIDER")
    thinking_level: ThinkingLevel = Field("high", alias="ROBOMP_THINKING")

    # Runtime
    max_concurrency: int = Field(2, alias="ROBOMP_MAX_CONCURRENCY")
    task_timeout_seconds: float = Field(2400.0, alias="ROBOMP_TASK_TIMEOUT_SECONDS")
    request_timeout_seconds: float = Field(120.0, alias="ROBOMP_REQUEST_TIMEOUT_SECONDS")
    omp_command: str = Field("omp", alias="ROBOMP_OMP_COMMAND")

    # Paths
    workspace_root: Path = Field(Path("./data/workspaces"), alias="ROBOMP_WORKSPACE_ROOT")
    sqlite_path: Path = Field(Path("./data/robomp.sqlite"), alias="ROBOMP_SQLITE_PATH")
    log_dir: Path = Field(Path("./data/logs"), alias="ROBOMP_LOG_DIR")

    # Server
    bind_host: str = Field("0.0.0.0", alias="ROBOMP_BIND_HOST")
    bind_port: int = Field(8080, alias="ROBOMP_BIND_PORT")

    # Dev-only replay header value; if empty, /replay is disabled
    replay_token: SecretStr | None = Field(None, alias="ROBOMP_REPLAY_TOKEN")

    @field_validator("bot_login", mode="after")
    @classmethod
    def _require_bot_login(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("ROBOMP_BOT_LOGIN must be a non-empty GitHub login")
        return cleaned

    @field_validator("replay_token", mode="before")
    @classmethod
    def _blank_replay_disables(cls, value: object) -> object:
        # Treat empty/whitespace strings as 'disabled'. Without this, an empty
        # ROBOMP_REPLAY_TOKEN becomes SecretStr("") which the server would
        # happily compare against an empty X-Robomp-Replay-Token header.
        if isinstance(value, str) and not value.strip():
            return None
        if hasattr(value, "get_secret_value"):
            inner = value.get_secret_value()  # type: ignore[attr-defined]
            if isinstance(inner, str) and not inner.strip():
                return None
        return value

    @field_validator("repo_allowlist_raw", mode="before")
    @classmethod
    def _coerce_allowlist(cls, v: object) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, (list, tuple)):
            return ",".join(str(item) for item in v)
        return str(v)

    @property
    def repo_allowlist(self) -> frozenset[str]:
        items = [piece.strip().lower() for piece in self.repo_allowlist_raw.split(",")]
        return frozenset(item for item in items if item)

    def allows(self, full_name: str) -> bool:
        return full_name.lower() in self.repo_allowlist

    @property
    def resolved_author_name(self) -> str:
        """Falls back to bot_login if ROBOMP_GIT_AUTHOR_NAME isn't set."""
        return (self.git_author_name or self.bot_login).strip()

    def ensure_paths(self) -> None:
        for path in (self.workspace_root, self.sqlite_path.parent, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


@cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    """Invalidate the cached settings (tests)."""
    get_settings.cache_clear()
