"""How to wire the Telegram link into a trading loop.

Run with::

    python examples/trading_loop.py

It uses fake market data, so it is safe to run before any strategy exists.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone

from trade_bot import Signal, TelegramBot, TelegramConfig
from trade_bot.commands import CommandContext, CommandRouter
from trade_bot.formatting import format_signal
from trade_bot.handlers import TradingBridge, register_default_commands


class MyStrategy(TradingBridge):
    """Adapts the trading engine to the commands you send from Telegram."""

    def __init__(self) -> None:
        # A plain flag the loop checks; /pause and /resume flip it. Using an
        # Event keeps this safe to touch from the polling thread.
        self.running = threading.Event()
        self.running.set()
        self.equity = 10_000.0
        self.trades = 0
        self.open_positions: list[list[object]] = []

    def status(self):
        return {
            "state": "running" if self.running.is_set() else "paused",
            "equity": f"{self.equity:,.2f} USDT",
            "open": len(self.open_positions),
            "trades": self.trades,
        }

    def positions(self):
        return self.open_positions

    def report(self, period: str):
        return {
            "period": period,
            "trades": self.trades,
            "equity": f"{self.equity:,.2f} USDT",
            "pnl": f"{self.equity - 10_000:+,.2f} USDT",
        }

    def pause(self) -> str:
        self.running.clear()
        return "No new positions will be opened."

    def resume(self) -> str:
        self.running.set()
        return "Trading resumed."


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    strategy = MyStrategy()
    config = TelegramConfig.from_env()

    # Build the router first so the bot publishes the full command list.
    router = register_default_commands(CommandRouter(), strategy)
    bot = TelegramBot(config, router=router)

    @bot.command("equity", "Show account equity")
    def _equity(context: CommandContext) -> str:
        return f"Equity: <b>{strategy.equity:,.2f} USDT</b>"

    # Listen for your messages on a background thread; trade on this one.
    bot.start_polling()
    bot.send_text("\U0001F680 <b>Trade-bot started</b> - send /help for commands.")

    try:
        while True:
            if strategy.running.is_set() and random.random() < 0.3:
                price = round(random.uniform(60_000, 68_000), 2)
                signal = Signal(
                    symbol="BTCUSDT",
                    side=random.choice(["BUY", "SELL"]),
                    price=price,
                    quantity=0.01,
                    stop_loss=round(price * 0.985, 2),
                    take_profit=round(price * 1.03, 2),
                    strategy="example",
                    confidence=random.uniform(0.5, 0.9),
                    timestamp=datetime.now(timezone.utc),
                )
                strategy.trades += 1
                strategy.equity += random.uniform(-25, 40)
                # notify_safely: a Telegram outage must not stop trading.
                bot.notify_safely(format_signal(signal))
            time.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        bot.notify_safely("\U0001F6D1 <b>Trade-bot stopped</b>")
        bot.stop()


if __name__ == "__main__":
    main()
