"""Thin, dependency-light wrapper over the Telegram Bot HTTP API."""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Sequence

import requests

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"

#: Telegram rejects messages longer than this, so long reports are chunked.
MAX_MESSAGE_CHARS = 4096

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class TelegramError(RuntimeError):
    """A Telegram API call failed after exhausting retries."""

    def __init__(self, message: str, *, status: int | None = None, method: str = ""):
        super().__init__(message)
        self.status = status
        self.method = method


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split ``text`` into Telegram-sized chunks, preferring line boundaries."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


class TelegramClient:
    """Calls Bot API methods, retrying on rate limits and transient failures.

    The bot token never appears in log messages or exceptions - only the
    method name does - because the token is embedded in the request URL.
    """

    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | None = None,
        api_root: str = API_ROOT,
        timeout: float = 30.0,
        max_attempts: int = 4,
        backoff_base: float = 1.5,
        sleep: Any = time.sleep,
    ) -> None:
        self._token = token
        self._session = session or requests.Session()
        self._api_root = api_root.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base
        self._sleep = sleep

    # -- plumbing ---------------------------------------------------------

    def _url(self, method: str) -> str:
        return f"{self._api_root}/bot{self._token}/{method}"

    def call(self, method: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        """Invoke a Bot API method and return its ``result`` field."""
        body = {k: v for k, v in (payload or {}).items() if v is not None}
        last_error: str = "no attempt was made"

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.post(
                    self._url(method),
                    json=body,
                    timeout=timeout if timeout is not None else self._timeout,
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("telegram %s failed (attempt %d): %s", method, attempt, last_error)
                self._wait(attempt)
                continue

            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return data.get("result")
                last_error = str(data.get("description") or "response was not ok")
                # A rejected payload (bad chat id, malformed markup) will be
                # rejected identically next time, so do not burn retries.
                raise TelegramError(f"{method}: {last_error}", status=200, method=method)

            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code not in _RETRY_STATUS:
                raise TelegramError(f"{method}: {last_error}", status=response.status_code, method=method)

            log.warning("telegram %s failed (attempt %d): %s", method, attempt, last_error)
            self._wait(attempt, response=response)

        raise TelegramError(f"{method}: giving up after {self._max_attempts} attempts - {last_error}", method=method)

    def _wait(self, attempt: int, *, response: Any = None) -> None:
        delay = self._backoff_base ** attempt
        retry_after = self._retry_after(response)
        if retry_after is not None:
            delay = max(delay, retry_after)
        self._sleep(delay)

    @staticmethod
    def _retry_after(response: Any) -> float | None:
        """Honour Telegram's ``retry_after`` hint when it rate-limits us."""
        if response is None:
            return None
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON error body is not fatal
            return None
        if not isinstance(payload, dict):
            return None
        parameters = payload.get("parameters")
        if isinstance(parameters, dict) and "retry_after" in parameters:
            try:
                return float(parameters["retry_after"])
            except (TypeError, ValueError):
                return None
        return None

    # -- API methods ------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        """Return the bot's own account - the cheapest way to verify a token."""
        return self.call("getMe")

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
        disable_web_page_preview: bool = True,
    ) -> list[dict[str, Any]]:
        """Send ``text``, splitting it across messages when it is too long.

        Returns the sent Message objects, one per chunk.
        """
        sent: list[dict[str, Any]] = []
        chunks = split_message(text)
        for index, chunk in enumerate(chunks):
            result = self.call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": parse_mode or None,
                    # Only the first chunk quotes the message being answered.
                    "reply_to_message_id": reply_to_message_id if index == 0 else None,
                    "disable_notification": disable_notification,
                    "link_preview_options": {"is_disabled": bool(disable_web_page_preview)},
                },
            )
            sent.append(result)
        return sent

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 25,
        limit: int = 100,
        allowed_updates: Sequence[str] | None = ("message",),
    ) -> list[dict[str, Any]]:
        """Long-poll for updates.

        The HTTP timeout is kept above the long-poll timeout so the server,
        not the socket, decides when an empty poll ends.
        """
        result = self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "limit": limit,
                "allowed_updates": list(allowed_updates) if allowed_updates else None,
            },
            timeout=self._timeout + timeout,
        )
        return list(result or [])

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> Any:
        """Long polling and webhooks are mutually exclusive; clear any webhook."""
        return self.call("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def send_chat_action(self, chat_id: int | str, action: str = "typing") -> Any:
        return self.call("sendChatAction", {"chat_id": chat_id, "action": action})

    def set_my_commands(self, commands: Iterable[tuple[str, str]]) -> Any:
        """Register the command list Telegram shows in its "/" menu."""
        payload = [{"command": name, "description": description} for name, description in commands]
        return self.call("setMyCommands", {"commands": payload})
