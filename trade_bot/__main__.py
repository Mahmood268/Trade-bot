"""Command line entry point: ``python -m trade_bot``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from trade_bot.bot import TelegramBot
from trade_bot.client import TelegramClient, TelegramError
from trade_bot.config import ENV_TOKEN, ConfigError, TelegramConfig
from trade_bot.formatting import Signal
from trade_bot.health import HealthConfig, HealthMonitor
from trade_bot.healthserver import HealthServer


def _load_dotenv(path: str = ".env") -> None:
    """Read simple ``KEY=value`` lines from ``.env`` without adding a dependency.

    Existing environment variables always win, so a real environment can
    override the file.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trade_bot",
        description="Run the Trade-bot Telegram link, or test it.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="verify the bot token and exit")
    group.add_argument("--whoami", action="store_true", help="print the chat id of the next message you send")
    group.add_argument("--send", metavar="TEXT", help="send one message and exit")
    group.add_argument("--demo", action="store_true", help="send a sample signal and report, then exit")
    group.add_argument(
        "--healthcheck",
        action="store_true",
        help="probe the bot and exit 0 if healthy, 1 if not (for Docker HEALTHCHECK)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="log every request")
    return parser


def _cmd_whoami() -> int:
    """Discover a chat id without needing TELEGRAM_CHAT_ID to be set yet."""
    token = (os.environ.get(ENV_TOKEN) or "").strip()
    if not token:
        print(f"{ENV_TOKEN} is not set - put your @BotFather token in .env first.", file=sys.stderr)
        return 2

    client = TelegramClient(token)
    me = client.get_me()
    print(f"Bot: @{me.get('username')} ({me.get('first_name')})")
    print("Now send any message to that bot in Telegram - waiting...")

    client.delete_webhook()
    offset: int | None = None
    while True:
        updates = client.get_updates(offset=offset, timeout=30)
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            if "id" in chat:
                title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
                print(f"\nTELEGRAM_CHAT_ID={chat['id']}   # {chat.get('type')} {title}".rstrip())
                return 0


def _cmd_healthcheck() -> int:
    """Exit 0 when healthy, 1 when not - the contract Docker expects.

    If the bot is serving a health endpoint, probe that, because it reflects
    the running process. Otherwise fall back to checking that Telegram is
    reachable with this token.
    """
    health = HealthConfig.from_env()

    if health.http_port:
        url = f"http://{health.http_host}:{health.http_port}/healthz"
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=10) as response:
                body = response.read().decode("utf-8", "replace")
                print(body.strip())
                return 0 if response.status == 200 else 1
        except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
            print(f"unhealthy: {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    token = (os.environ.get(ENV_TOKEN) or "").strip()
    if not token:
        print(f"unhealthy: {ENV_TOKEN} is not set", file=sys.stderr)
        return 1
    try:
        me = TelegramClient(token, max_attempts=1).get_me()
    except Exception as exc:  # noqa: BLE001
        print(f"unhealthy: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"ok: Telegram reachable as @{me.get('username')}")
    return 0


def _cmd_demo(bot: TelegramBot) -> int:
    bot.send_signal(
        Signal(
            symbol="BTCUSDT",
            side="BUY",
            price=64150.25,
            quantity=0.015,
            stop_loss=63200.0,
            take_profit=66000.0,
            strategy="ema-cross",
            confidence=0.72,
            note="Demo signal from `python -m trade_bot --demo`.",
            timestamp=datetime.now(timezone.utc),
        )
    )
    bot.send_report(
        "Daily report",
        {"trades": 7, "win rate": "57%", "pnl": "+1.83%", "equity": "10,183.40 USDT"},
        rows=[
            ["BTCUSDT", "LONG", "0.015", "64150.25", "+38.10"],
            ["ETHUSDT", "SHORT", "0.400", "3122.80", "-11.45"],
        ],
        headers=["Symbol", "Side", "Size", "Entry", "PnL"],
        footer="Demo report - numbers are made up.",
    )
    print("Sent a demo signal and report.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _load_dotenv()

    try:
        if args.whoami:
            return _cmd_whoami()

        if args.healthcheck:
            return _cmd_healthcheck()

        config = TelegramConfig.from_env()
        bot = TelegramBot(config)

        if args.check:
            me = bot.client.get_me()
            print(f"Token OK - connected as @{me.get('username')}")
            print(f"Default chat: {config.chat_id}")
            print(f"Allowed chats: {sorted(config.allowed_chat_ids)}")
            return 0

        if args.send:
            bot.send_text(args.send)
            print(f"Sent to chat {config.chat_id}.")
            return 0

        if args.demo:
            return _cmd_demo(bot)

        # Default: listen for commands, with health monitoring alongside.
        health_config = HealthConfig.from_env()
        monitor = HealthMonitor(bot, health_config)
        monitor.attach(bot.router)

        server = None
        if health_config.http_port:
            server = HealthServer(
                monitor, host=health_config.http_host, port=health_config.http_port
            ).start()
            print(f"Health endpoint: http://{health_config.http_host}:{server.port}/healthz")

        monitor.start()
        try:
            bot.poll_forever()
        finally:
            monitor.stop()
            if server is not None:
                server.stop()
        return 0

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except TelegramError as exc:
        print(f"Telegram error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
