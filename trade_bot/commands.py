"""Routing for the messages you send *to* the bot."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)


@dataclass
class CommandContext:
    """Everything a handler needs about the incoming message."""

    command: str
    args: list[str]
    text: str
    chat_id: int
    user_id: int | None = None
    username: str | None = None
    message_id: int | None = None
    bot: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def argument_string(self) -> str:
        """The raw text after the command, for handlers that want it verbatim."""
        stripped = self.text.strip()
        if not stripped.startswith("/"):
            return stripped
        _, _, rest = stripped.partition(" ")
        return rest.strip()


#: A handler returns the reply text, or ``None`` to stay silent.
Handler = Callable[[CommandContext], "str | None"]


def parse_command(text: str, *, bot_username: str | None = None) -> tuple[str | None, list[str]]:
    """Split ``text`` into a command name and its arguments.

    Handles the ``/status@my_trade_bot`` form Telegram uses in groups. Returns
    ``(None, [])`` for messages that are not commands.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []

    head, _, rest = text.partition(" ")
    name = head[1:]
    if "@" in name:
        name, _, addressee = name.partition("@")
        # In a group, `/status@other_bot` is not addressed to us.
        if bot_username and addressee.lower() != bot_username.lower():
            return None, []
    if not name:
        return None, []

    try:
        args = shlex.split(rest)
    except ValueError:
        # Unbalanced quotes - fall back to whitespace splitting.
        args = rest.split()
    return name.lower(), args


class CommandRouter:
    """Maps command names to handlers, with aliases and generated help."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._help: dict[str, str] = {}
        self._canonical: dict[str, str] = {}
        self._fallback: Handler | None = None

    def command(self, name: str, help_text: str = "", *, aliases: tuple[str, ...] = ()) -> Callable[[Handler], Handler]:
        """Decorator registering ``name`` (and any aliases) to a handler."""

        def register(handler: Handler) -> Handler:
            self.add(name, handler, help_text, aliases=aliases)
            return handler

        return register

    def add(self, name: str, handler: Handler, help_text: str = "", *, aliases: tuple[str, ...] = ()) -> None:
        key = name.lstrip("/").lower()
        if key in self._handlers:
            raise ValueError(f"command /{key} is already registered")
        self._handlers[key] = handler
        self._help[key] = help_text
        self._canonical[key] = key
        for alias in aliases:
            alias_key = alias.lstrip("/").lower()
            if alias_key in self._handlers:
                raise ValueError(f"command /{alias_key} is already registered")
            self._handlers[alias_key] = handler
            self._canonical[alias_key] = key

    def fallback(self, handler: Handler) -> Handler:
        """Register the handler used for text that is not a known command."""
        self._fallback = handler
        return handler

    def __contains__(self, name: str) -> bool:
        return name.lstrip("/").lower() in self._handlers

    def __iter__(self) -> Iterator[tuple[str, str]]:
        """Yield ``(name, help)`` for canonical commands only, in sorted order."""
        for key in sorted(self._help):
            yield key, self._help[key]

    def dispatch(self, context: CommandContext) -> str | None:
        """Run the handler for ``context``, converting failures into a reply."""
        handler = self._handlers.get(context.command) if context.command else None
        if handler is None:
            handler = self._fallback
        if handler is None:
            return None
        try:
            return handler(context)
        except Exception as exc:  # noqa: BLE001 - a bad handler must not kill the poller
            log.exception("handler for /%s failed", context.command)
            from trade_bot.formatting import format_error

            return format_error(f"/{context.command} failed", exc)

    def help_text(self) -> str:
        """Render the command list for ``/help``."""
        from trade_bot.formatting import esc

        if not self._help:
            return "<i>no commands are registered</i>"
        lines = ["<b>Commands</b>", ""]
        lines.extend(
            f"/{esc(name)} - {esc(description)}" if description else f"/{esc(name)}"
            for name, description in self
        )
        return "\n".join(lines)

    def telegram_commands(self) -> list[tuple[str, str]]:
        """The ``(name, description)`` pairs for ``setMyCommands``."""
        return [(name, description or name) for name, description in self if description]
