"""Telegram integration for Trade-bot.

Two-way link between the trading bot and a Telegram chat:

* outbound - trade signals, periodic reports and error alerts are pushed to
  the configured chat;
* inbound  - messages you send to the bot are routed to command handlers and
  answered in the same chat.

Typical use::

    from trade_bot import Signal, TelegramBot, TelegramConfig

    bot = TelegramBot(TelegramConfig.from_env())
    bot.send_signal(Signal(symbol="BTCUSDT", side="BUY", price=64150.0))
    bot.poll_forever()

A :class:`~trade_bot.health.HealthMonitor` can watch the trading loop and
alert you if it stalls; see ``trade_bot.health``.
"""

from trade_bot.bot import TelegramBot
from trade_bot.client import TelegramClient, TelegramError
from trade_bot.commands import CommandContext, CommandRouter
from trade_bot.config import ConfigError, TelegramConfig
from trade_bot.formatting import Signal, format_error, format_report, format_signal
from trade_bot.health import HealthConfig, HealthMonitor, HealthReport

__all__ = [
    "CommandContext",
    "CommandRouter",
    "ConfigError",
    "HealthConfig",
    "HealthMonitor",
    "HealthReport",
    "Signal",
    "TelegramBot",
    "TelegramClient",
    "TelegramConfig",
    "TelegramError",
    "format_error",
    "format_report",
    "format_signal",
]

__version__ = "0.1.0"
