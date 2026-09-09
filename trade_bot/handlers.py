"""Default command handlers, plus the seam where your strategy plugs in."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from trade_bot.commands import CommandContext, CommandRouter
from trade_bot.formatting import esc, format_key_values, format_report


class TradingBridge:
    """The hook between Telegram commands and your trading engine.

    Subclass it and override only what you have. Anything left alone answers
    with a clear "not wired up yet" instead of pretending to have data.

    ``pause`` and ``resume`` are expected to flip a flag your trading loop
    checks; they must not block, since they run on the polling thread.
    """

    def status(self) -> Mapping[str, Any]:
        """Live state: running or paused, equity, open position count, uptime."""
        raise NotImplementedError

    def positions(self) -> Sequence[Sequence[Any]]:
        """Open positions as rows matching :attr:`position_headers`."""
        raise NotImplementedError

    position_headers: Sequence[str] = ("Symbol", "Side", "Size", "Entry", "PnL")

    def report(self, period: str) -> Mapping[str, Any]:
        """Performance metrics for ``period`` (``today``, ``week``, ``all``...)."""
        raise NotImplementedError

    def pause(self) -> str:
        """Stop opening new positions. Returns a short confirmation."""
        raise NotImplementedError

    def resume(self) -> str:
        """Resume trading. Returns a short confirmation."""
        raise NotImplementedError


_NOT_WIRED = (
    "<i>Not wired up yet.</i>\n\nImplement <code>{method}()</code> on a "
    "<code>TradingBridge</code> subclass and pass it to "
    "<code>register_default_commands(router, bridge)</code>."
)


def register_default_commands(router: CommandRouter, bridge: TradingBridge | None = None) -> CommandRouter:
    """Register the built-in command set on ``router``."""
    bridge = bridge or TradingBridge()

    def _guard(method: str, call: Any) -> str:
        try:
            return call()
        except NotImplementedError:
            return _NOT_WIRED.format(method=method)

    @router.command("start", "Greet the bot and show what it can do")
    def _start(context: CommandContext) -> str:
        who = f", {esc(context.username)}" if context.username else ""
        return (
            f"\U0001F44B <b>Trade-bot is connected</b>{who}.\n\n"
            "You will get trade signals, reports and error alerts here, and "
            "you can send commands back.\n\n" + router.help_text()
        )

    @router.command("help", "List the available commands", aliases=("commands",))
    def _help(context: CommandContext) -> str:
        return router.help_text()

    @router.command("ping", "Check the bot is alive")
    def _ping(context: CommandContext) -> str:
        return "\U0001F3D3 pong"

    @router.command("id", "Show this chat's id", aliases=("whoami", "chatid"))
    def _id(context: CommandContext) -> str:
        return format_key_values(
            [
                ("chat id", context.chat_id),
                ("user id", context.user_id if context.user_id is not None else "-"),
                ("username", f"@{context.username}" if context.username else "-"),
            ]
        )

    @router.command("status", "Current trading state")
    def _status(context: CommandContext) -> str:
        def call() -> str:
            data = bridge.status()
            return "<b>Status</b>\n\n" + format_key_values(list(data.items()))

        return _guard("status", call)

    @router.command("positions", "List open positions", aliases=("pos",))
    def _positions(context: CommandContext) -> str:
        def call() -> str:
            rows = list(bridge.positions())
            if not rows:
                return "<i>No open positions.</i>"
            return format_report("Open positions", rows=rows, headers=list(bridge.position_headers))

        return _guard("positions", call)

    @router.command("report", "Performance report - /report [today|week|month|all]")
    def _report(context: CommandContext) -> str:
        period = (context.args[0].lower() if context.args else "today")

        def call() -> str:
            metrics = bridge.report(period)
            return format_report(f"Report - {period}", metrics)

        return _guard("report", call)

    @router.command("pause", "Stop opening new positions")
    def _pause(context: CommandContext) -> str:
        return _guard("pause", lambda: f"⏸ {esc(bridge.pause())}")

    @router.command("resume", "Resume trading")
    def _resume(context: CommandContext) -> str:
        return _guard("resume", lambda: f"▶️ {esc(bridge.resume())}")

    @router.fallback
    def _unknown(context: CommandContext) -> str:
        if context.command:
            return (
                f"Unknown command <code>/{esc(context.command)}</code>.\n\n"
                + router.help_text()
            )
        # Plain text, not a command - point at the menu rather than staying mute.
        return "Send /help to see what I can do."

    return router
