"""The two-way bot: pushes signals out, answers messages coming back."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from trade_bot.client import TelegramClient, TelegramError
from trade_bot.commands import CommandContext, CommandRouter, parse_command
from trade_bot.config import TelegramConfig
from trade_bot.formatting import Signal, format_error, format_report, format_signal

log = logging.getLogger(__name__)


class TelegramBot:
    """Outbound notifications and inbound command handling in one object.

    Outbound calls are safe to make from your trading loop. Inbound handling
    runs in :meth:`poll_forever`, either on the main thread or on the
    background thread started by :meth:`start_polling`.
    """

    def __init__(
        self,
        config: TelegramConfig,
        *,
        client: TelegramClient | None = None,
        router: CommandRouter | None = None,
        register_defaults: bool = True,
    ) -> None:
        self.config = config
        self.client = client or TelegramClient(config.token)
        self.router = router or CommandRouter()
        self.bot_username: str | None = None
        self._offset: int | None = self._load_offset()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        if register_defaults and router is None:
            from trade_bot.handlers import register_default_commands

            register_default_commands(self.router)

    # -- outbound ---------------------------------------------------------

    def send_text(
        self,
        text: str,
        *,
        chat_id: int | None = None,
        reply_to_message_id: int | None = None,
        silent: bool = False,
    ) -> list[dict[str, Any]]:
        """Send a pre-rendered message body to a chat."""
        return self.client.send_message(
            chat_id if chat_id is not None else self.config.chat_id,
            text,
            parse_mode=self.config.parse_mode or None,
            reply_to_message_id=reply_to_message_id,
            disable_notification=silent,
        )

    def send_signal(self, signal: Signal, *, chat_id: int | None = None, silent: bool = False) -> list[dict[str, Any]]:
        """Push a trading signal."""
        return self.send_text(format_signal(signal), chat_id=chat_id, silent=silent)

    def send_report(
        self,
        title: str,
        metrics: Mapping[str, Any] | None = None,
        *,
        rows: Sequence[Sequence[Any]] | None = None,
        headers: Sequence[str] | None = None,
        footer: str | None = None,
        timestamp: datetime | None = None,
        chat_id: int | None = None,
        silent: bool = True,
    ) -> list[dict[str, Any]]:
        """Push a periodic report. Silent by default - reports are not urgent."""
        body = format_report(
            title, metrics, rows=rows, headers=headers, footer=footer, timestamp=timestamp
        )
        return self.send_text(body, chat_id=chat_id, silent=silent)

    def send_error(self, context: str, error: BaseException | str, *, chat_id: int | None = None) -> list[dict[str, Any]]:
        """Push an alert about a failure in the trading loop."""
        return self.send_text(format_error(context, error), chat_id=chat_id)

    def notify_safely(self, text: str, **kwargs: Any) -> bool:
        """Send ``text``, swallowing Telegram failures.

        Use this on paths where a messaging outage must never take the
        trading loop down with it. Returns whether the send succeeded.
        """
        try:
            self.send_text(text, **kwargs)
            return True
        except (TelegramError, OSError) as exc:
            log.error("could not deliver Telegram message: %s", exc)
            return False

    # -- inbound ----------------------------------------------------------

    def command(self, name: str, help_text: str = "", *, aliases: tuple[str, ...] = ()) -> Callable[..., Any]:
        """Shorthand for ``bot.router.command(...)``."""
        return self.router.command(name, help_text, aliases=aliases)

    def handle_update(self, update: Mapping[str, Any]) -> str | None:
        """Process one update; returns the reply that was sent, if any."""
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return None

        text = message.get("text") or message.get("caption") or ""
        sender = message.get("from") or {}

        if not self.config.is_allowed(chat_id):
            # Anyone can find a bot's handle, so unknown chats are dropped.
            # We do not reply, to avoid confirming the bot exists.
            log.warning(
                "ignoring message from unauthorised chat %s (user %s)",
                chat_id,
                sender.get("username") or sender.get("id"),
            )
            return None

        command, args = parse_command(text, bot_username=self.bot_username)
        if command is None and not text:
            return None

        context = CommandContext(
            command=command or "",
            args=args,
            text=text,
            chat_id=chat_id,
            user_id=sender.get("id"),
            username=sender.get("username"),
            message_id=message.get("message_id"),
            bot=self,
        )

        reply = self.router.dispatch(context)
        if reply:
            self.send_text(reply, chat_id=chat_id, reply_to_message_id=context.message_id)
        return reply

    def poll_once(self, *, timeout: int | None = None) -> int:
        """Fetch and process one batch of updates. Returns how many were handled."""
        updates = self.client.get_updates(
            offset=self._offset,
            timeout=self.config.poll_timeout if timeout is None else timeout,
        )
        for update in updates:
            update_id = update.get("update_id")
            try:
                self.handle_update(update)
            except TelegramError as exc:
                log.error("could not answer update %s: %s", update_id, exc)
            except Exception:  # noqa: BLE001 - one bad update must not stop the loop
                log.exception("failed to handle update %s", update_id)
            finally:
                # Advance past this update either way, so a message that always
                # fails cannot wedge the bot in a retry loop forever.
                if isinstance(update_id, int):
                    self._set_offset(update_id + 1)
        return len(updates)

    def prepare(self) -> None:
        """One-time setup: verify the token, clear webhooks, publish commands."""
        try:
            me = self.client.get_me()
            self.bot_username = me.get("username")
            log.info("connected to Telegram as @%s", self.bot_username)
        except TelegramError as exc:
            raise TelegramError(f"could not authenticate with Telegram: {exc}") from exc

        try:
            self.client.delete_webhook()
        except TelegramError as exc:
            log.warning("could not clear webhook: %s", exc)

        commands = self.router.telegram_commands()
        if commands:
            try:
                self.client.set_my_commands(commands)
            except TelegramError as exc:
                log.warning("could not publish command list: %s", exc)

    def poll_forever(self, *, stop: threading.Event | None = None, prepare: bool = True) -> None:
        """Long-poll until ``stop`` is set or the process is interrupted."""
        stop = stop or self._stop
        if prepare:
            self.prepare()

        backoff = 1.0
        log.info("listening for Telegram messages")
        while not stop.is_set():
            try:
                self.poll_once()
                backoff = 1.0
            except TelegramError as exc:
                log.error("polling failed: %s - retrying in %.0fs", exc, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
            except KeyboardInterrupt:
                log.info("interrupted - stopping")
                break
        log.info("stopped listening")

    def start_polling(self) -> threading.Thread:
        """Run :meth:`poll_forever` on a daemon thread and return it."""
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self.poll_forever, name="telegram-poller", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, *, timeout: float = 5.0) -> None:
        """Ask the polling loop to finish and wait briefly for it."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def __enter__(self) -> "TelegramBot":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # -- offset persistence ----------------------------------------------

    @property
    def offset(self) -> int | None:
        return self._offset

    def _set_offset(self, value: int) -> None:
        self._offset = value
        path = self.config.offset_file
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash mid-write cannot truncate the file.
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(str(value), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            log.warning("could not persist update offset to %s: %s", path, exc)

    def _load_offset(self) -> int | None:
        path: Path | None = self.config.offset_file
        if path is None or not path.exists():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable offset file %s: %s", path, exc)
            return None


def build_bot(env: Mapping[str, str] | None = None, **kwargs: Any) -> TelegramBot:
    """Convenience constructor: config from the environment, then a bot."""
    return TelegramBot(TelegramConfig.from_env(env), **kwargs)
