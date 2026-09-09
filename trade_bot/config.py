"""Configuration for the Telegram link, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

#: Environment variable names, kept in one place so the README and the
#: ``.env.example`` file cannot drift away from the code.
ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_CHAT_ID = "TELEGRAM_CHAT_ID"
ENV_ALLOWED = "TELEGRAM_ALLOWED_CHAT_IDS"
ENV_POLL_TIMEOUT = "TELEGRAM_POLL_TIMEOUT"
ENV_OFFSET_FILE = "TELEGRAM_OFFSET_FILE"
ENV_PARSE_MODE = "TELEGRAM_PARSE_MODE"


class ConfigError(RuntimeError):
    """Raised when the environment does not describe a usable bot."""


def _parse_chat_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"{ENV_ALLOWED}: {part!r} is not a chat id") from exc
    return frozenset(ids)


@dataclass(frozen=True)
class TelegramConfig:
    """Everything the bot needs to talk to Telegram.

    ``allowed_chat_ids`` is the security boundary: a Telegram bot answers
    anyone who finds its handle, so inbound messages from any other chat are
    ignored. It defaults to just ``chat_id``.
    """

    token: str
    chat_id: int
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    poll_timeout: int = 25
    parse_mode: str = "HTML"
    offset_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.token or ":" not in self.token:
            raise ConfigError(
                f"{ENV_TOKEN} looks wrong - expected a @BotFather token of the "
                "form 123456789:AA..."
            )
        if not self.allowed_chat_ids:
            object.__setattr__(self, "allowed_chat_ids", frozenset({self.chat_id}))
        if self.poll_timeout < 0:
            raise ConfigError(f"{ENV_POLL_TIMEOUT} must not be negative")
        if self.parse_mode not in ("HTML", "MarkdownV2", "Markdown", ""):
            raise ConfigError(f"{ENV_PARSE_MODE}: unsupported value {self.parse_mode!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelegramConfig":
        """Build a config from ``os.environ`` (or any mapping, for tests)."""
        env = os.environ if env is None else env

        token = (env.get(ENV_TOKEN) or "").strip()
        if not token:
            raise ConfigError(f"{ENV_TOKEN} is not set - see .env.example")

        raw_chat = (env.get(ENV_CHAT_ID) or "").strip()
        if not raw_chat:
            raise ConfigError(
                f"{ENV_CHAT_ID} is not set - message your bot and run "
                "`python -m trade_bot --whoami` to discover it"
            )
        try:
            chat_id = int(raw_chat)
        except ValueError as exc:
            raise ConfigError(f"{ENV_CHAT_ID}: {raw_chat!r} is not a chat id") from exc

        allowed = _parse_chat_ids(env.get(ENV_ALLOWED) or "")

        raw_timeout = (env.get(ENV_POLL_TIMEOUT) or "25").strip()
        try:
            poll_timeout = int(raw_timeout)
        except ValueError as exc:
            raise ConfigError(f"{ENV_POLL_TIMEOUT}: {raw_timeout!r} is not a number") from exc

        offset_raw = (env.get(ENV_OFFSET_FILE) or "").strip()

        return cls(
            token=token,
            chat_id=chat_id,
            allowed_chat_ids=allowed,
            poll_timeout=poll_timeout,
            parse_mode=(env.get(ENV_PARSE_MODE) or "HTML").strip(),
            offset_file=Path(offset_raw) if offset_raw else None,
        )

    def is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chat_ids

    def __repr__(self) -> str:  # keep the token out of logs and tracebacks
        return (
            f"TelegramConfig(token='***', chat_id={self.chat_id}, "
            f"allowed_chat_ids={sorted(self.allowed_chat_ids)}, "
            f"poll_timeout={self.poll_timeout}, parse_mode={self.parse_mode!r})"
        )
