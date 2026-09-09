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
"""

from trade_bot.bot import TelegramBot
from trade_bot.client import TelegramClient, TelegramError
from trade_bot.commands import CommandContext, CommandRouter
from trade_bot.config import ConfigError, TelegramConfig
from trade_bot.formatting import Signal, format_error, format_report, format_signal

__all__ = [
    "CommandContext",
    "CommandRouter",
    "ConfigError",
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
