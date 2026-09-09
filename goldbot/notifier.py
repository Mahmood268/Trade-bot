"""Telegram alerts and the remote kill switch.

Plain Bot API over ``requests`` — no async framework, no long-lived
connection, nothing that can wedge the trading loop. Sending an alert must
never raise: a Telegram outage is a logging problem, not a trading problem.

Two directions:

* **Out**: entries, exits, halts, phase changes, errors, the day summary.
* **In**: ``/status /pause /resume /flat /pnl /help``, polled with
  ``getUpdates``. Only messages from the configured ``chat_id`` are honoured;
  anyone else who finds the bot gets nothing, including no reply.

The token is a password. It is read from config (which reads it from the
environment) and never logged, never echoed, never put in a message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

COMMANDS = ("status", "pause", "resume", "flat", "pnl", "help")

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Command:
    name: str
    args: str
    update_id: int


def _requests_transport(token: str) -> Transport:
    import requests

    base = f"https://api.telegram.org/bot{token}/"

    def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(base + method, json=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    return call


class Telegram:
    def __init__(self, cfg, transport: Transport | None = None) -> None:
        """
        Args:
            cfg: a ``TelegramConfig``.
            transport: injectable ``(method, params) -> response`` so tests run
                with no network. Defaults to the real Bot API.
        """
        self.cfg = cfg
        self.enabled = bool(cfg.enabled and cfg.bot_token and cfg.chat_id)
        self._call = transport or (_requests_transport(cfg.bot_token) if self.enabled else None)
        self._offset: int | None = None
        self.sent: list[str] = []          # last messages, for tests and /status
        self.failures = 0

    # ------------------------------------------------------------------

    def send(self, text: str, silent: bool = False) -> bool:
        """Send a message. Returns False on failure; never raises."""
        self.sent.append(text)
        del self.sent[:-50]
        if not self.enabled or self._call is None:
            log.info("[telegram off] %s", text.replace("\n", " | "))
            return False
        try:
            self._call(
                "sendMessage",
                {
                    "chat_id": self.cfg.chat_id,
                    "text": text[:4000],
                    "disable_notification": silent,
                },
            )
            return True
        except Exception as exc:  # noqa: BLE001 - an alert failure must never stop trading
            self.failures += 1
            log.warning("telegram send failed (%d so far): %s", self.failures, exc)
            return False

    def poll_commands(self) -> list[Command]:
        """Fetch new commands from the configured chat. Never raises."""
        if not self.enabled or self._call is None or not self.cfg.allow_commands:
            return []
        params: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            resp = self._call("getUpdates", params)
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            log.warning("telegram poll failed: %s", exc)
            return []

        commands: list[Command] = []
        for update in resp.get("result", []) or []:
            update_id = int(update.get("update_id", 0))
            self._offset = max(self._offset or 0, update_id + 1)
            message = update.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id", ""))
            text = str(message.get("text", "")).strip()
            if chat_id != str(self.cfg.chat_id):
                # Not you. No reply either — replying confirms the bot exists.
                log.warning("ignoring command from unknown chat %s", chat_id)
                continue
            if not text.startswith("/"):
                continue
            name, _, args = text[1:].partition(" ")
            name = name.split("@", 1)[0].lower()
            if name in COMMANDS:
                commands.append(Command(name=name, args=args.strip(), update_id=update_id))
            else:
                self.send(f"Unknown command /{name}. Try /help.")
        return commands

    @staticmethod
    def help_text() -> str:
        return (
            "goldbot commands:\n"
            "/status — phase, positions, today's P&L\n"
            "/pnl — today's realised P&L and trade count\n"
            "/pause — stop opening new trades (existing ones keep their stops)\n"
            "/resume — allow new trades again; also clears a consecutive-loss halt\n"
            "/flat — close every position now\n"
            "/help — this"
        )
