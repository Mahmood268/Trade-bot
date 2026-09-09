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
python -m trade_bot --check        # confirms the token and shows the allowed chats
python -m trade_bot --demo         # sends a sample signal and report
python -m trade_bot --healthcheck  # exits 0 if healthy, 1 if not
python -m trade_bot                # starts listening, with monitoring alongside
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

## Health monitoring

Three different questions, answered three different ways.

### "Is Telegram reachable?"

```bash
python -m trade_bot --healthcheck
```

Exits `0` when healthy and `1` when not, so it drops straight into a Docker
`HEALTHCHECK` or a cron alert. If `HEALTH_HTTP_PORT` is set it probes the
running bot through its own endpoint — which catches a wedged trading loop,
not just an unreachable API. Otherwise it checks that your token still works.

### "Is the trading loop still turning?"

The watchdog. Your loop calls `beat()` once per pass; if the beats stop, you
get a Telegram alert — and another message when it recovers.

```python
from trade_bot import HealthConfig, HealthMonitor, TelegramBot, TelegramConfig

bot = TelegramBot(TelegramConfig.from_env())
monitor = HealthMonitor(bot, HealthConfig(watchdog_timeout=300))
monitor.attach(bot.router)      # adds the /health command
monitor.start()

while True:
    monitor.beat()              # "still alive"
    ...                         # your trading logic
```

```
⚠️ Trading loop stalled

No tick for 10m 0s (limit 5m 0s).
```

The watchdog **arms on the first `beat()`**, so a bot that never calls it is
never falsely reported as stalled. It alerts **once** per stall, not every
tick.

Add your own checks — they join the same report:

```python
monitor.register("exchange", lambda: (binance.ping(), "binance"))
monitor.register("database", lambda: (db.is_connected(), ""))
```

A check returns `True`/`False`, `(ok, detail)`, or raises — a raising check
becomes a failure with the exception in the detail, never a crash.

### "How is everything right now?"

Send `/health` in Telegram:

```
💚 Healthy

Uptime: 1d 4h 12m 3s
Last tick: 2s ago
Ticks: 8241

✅ telegram api - @my_trade_bot
✅ exchange - binance
```

Set `HEALTH_HEARTBEAT_INTERVAL` to have that same report pushed on a schedule
(`86400` = daily). It is **off by default** — it is a recurring message even
when nothing is wrong. The watchdog is **on by default**, because it only
speaks when something is actually broken.

### HTTP endpoint

Set `HEALTH_HTTP_PORT=8080` and the bot serves `GET /healthz` — `200` with a
JSON body when healthy, `503` when not, which is what Docker, Kubernetes
probes and services like UptimeRobot expect.

```bash
curl -s localhost:8080/healthz | jq
```

```json
{
  "status": "ok",
  "uptime": "1d 4h 12m 3s",
  "beats": 8241,
  "checks": [{"name": "telegram api", "status": "ok", "detail": "@my_trade_bot"}]
}
```

It binds to **loopback by default**. The body describes your bot's internals,
so put it behind a reverse proxy before exposing it.

### Docker

`Dockerfile` and `docker-compose.yml` are included and already wired:

```bash
docker compose up -d
docker compose ps        # STATUS shows "healthy" once probes pass
```

The container's `HEALTHCHECK` runs `python -m trade_bot --healthcheck` against
the in-process endpoint, so Docker marks the container unhealthy when the
trading loop wedges — not only when the process dies.


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
| `HEALTH_WATCHDOG_TIMEOUT` | no | `300` | Alert if the loop stops ticking for this long; `0` disables |
| `HEALTH_HEARTBEAT_INTERVAL` | no | `0` (off) | Push a status report every N seconds |
| `HEALTH_HTTP_PORT` | no | — | Serve `GET /healthz` on this port |
| `HEALTH_HTTP_HOST` | no | `127.0.0.1` | Bind address for the health endpoint |

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
- Monitoring never takes down what it monitors: health messages go out via
  `notify_safely`, and a failing check is reported, not raised.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

No network access and no test dependencies — the Telegram API is faked.
