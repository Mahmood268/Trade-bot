# Trade-bot

A two-way Telegram link for a trading bot: it **pushes** signals, reports and
error alerts to your chat, and **answers** commands you send back.

```
Trading loop  ──sends──▶  Telegram chat  ──you reply──▶  command handlers ──▶ your strategy
```

## Setup

### 1. Create the bot

In Telegram, message [@BotFather](https://t.me/BotFather):

```
/newbot
```

Pick a name and a username ending in `bot`. BotFather replies with a token
like `123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`.

### 2. Configure

```bash
cp .env.example .env
```

Put the token in `.env` as `TELEGRAM_BOT_TOKEN`. **`.env` is gitignored — never
commit the token.** Anyone holding it controls the bot; if it leaks, send
`/revoke` to BotFather.

### 3. Find your chat id

Open a chat with your new bot and send it any message (`hi` will do), then:

```bash
pip install -r requirements.txt
python -m trade_bot --whoami
```

It prints the line to paste into `.env`:

```
TELEGRAM_CHAT_ID=123456789
```

For a group, add the bot to the group first and send a message there; group
ids are negative (`-1001234567890`).

### 4. Verify

```bash
python -m trade_bot --check    # confirms the token and shows the allowed chats
python -m trade_bot --demo     # sends a sample signal and report
python -m trade_bot            # starts listening for your commands
```

## Sending from your trading code

```python
from trade_bot import Signal, TelegramBot, TelegramConfig

bot = TelegramBot(TelegramConfig.from_env())

bot.send_signal(Signal(
    symbol="BTCUSDT",
    side="BUY",
    price=64150.25,
    quantity=0.015,
    stop_loss=63200.0,
    take_profit=66000.0,
    strategy="ema-cross",
    confidence=0.72,
))

bot.send_report(
    "Daily report",
    {"trades": 7, "win rate": "57%", "pnl": "+1.83%"},
    rows=[["BTCUSDT", "LONG", "0.015", "+38.10"]],
    headers=["Symbol", "Side", "Size", "PnL"],
)

bot.send_error("order rejected", ValueError("insufficient margin"))
```

Inside the trading loop, prefer `bot.notify_safely(text)` — it logs and returns
`False` on a Telegram outage instead of raising, so a messaging problem can
never take the strategy down with it.

## Replying to you

Run the listener alongside your loop:

```python
bot.start_polling()      # background daemon thread
...
bot.stop()
```

or `bot.poll_forever()` to block the current thread.

Built-in commands: `/start`, `/help`, `/ping`, `/id`, `/status`, `/positions`,
`/report [today|week|month|all]`, `/pause`, `/resume`.

`/status`, `/positions`, `/report`, `/pause` and `/resume` need to reach your
engine. Subclass `TradingBridge` and override what you have — anything you skip
answers "not wired up yet" rather than inventing numbers:

```python
from trade_bot.commands import CommandRouter
from trade_bot.handlers import TradingBridge, register_default_commands

class MyStrategy(TradingBridge):
    def status(self):
        return {"state": "running", "equity": f"{self.equity:,.2f}"}

    def pause(self):
        self.running.clear()
        return "No new positions will be opened."

router = register_default_commands(CommandRouter(), MyStrategy())
bot = TelegramBot(TelegramConfig.from_env(), router=router)
```

Add your own commands with a decorator:

```python
@bot.command("pnl", "Profit and loss for a period")
def pnl(context):
    period = context.args[0] if context.args else "today"
    return f"PnL for {period}: <b>+1.83%</b>"
```

A handler returns the reply text (Telegram HTML) or `None` to stay silent.
`context` carries `command`, `args`, `argument_string`, `chat_id`, `user_id`,
`username` and `bot`.

See [`examples/trading_loop.py`](examples/trading_loop.py) for a runnable
loop that sends signals and answers commands at the same time.

## Access control

A Telegram bot replies to anyone who finds its username, and these commands can
pause live trading. Only chats in `TELEGRAM_ALLOWED_CHAT_IDS` are served —
everything else is logged and dropped without a reply. The default is just
`TELEGRAM_CHAT_ID`, so out of the box only you can command the bot.

## Configuration reference

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | — | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | yes | — | Where signals and reports go |
| `TELEGRAM_ALLOWED_CHAT_IDS` | no | `TELEGRAM_CHAT_ID` | Chats allowed to send commands |
| `TELEGRAM_POLL_TIMEOUT` | no | `25` | Long-poll seconds per request |
| `TELEGRAM_OFFSET_FILE` | no | — | Remembers the last update across restarts |
| `TELEGRAM_PARSE_MODE` | no | `HTML` | `HTML` or `MarkdownV2` |

Set `TELEGRAM_OFFSET_FILE` for any long-running deployment; without it a
restart can replay messages Telegram still has queued.

## Notes on reliability

- Rate limits (HTTP 429) and transient 5xx responses are retried with backoff,
  honouring Telegram's `retry_after` hint. Rejected payloads are not retried.
- Messages over 4096 characters are split at line boundaries.
- One bad update never stops the poller: the offset always advances, and a
  handler that raises produces an error reply instead of a crash.
- The bot token is kept out of log messages and exceptions.
- Long polling and webhooks are mutually exclusive; `prepare()` clears any
  webhook before polling starts.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

No network access and no test dependencies — the Telegram API is faked.
