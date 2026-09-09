# Setup guide

Follow these in order. Nothing here trades — by the end you will have proven the
bot can talk to your broker, and nothing more.

Total time: about 45 minutes, most of it waiting for installers.

---

## 1. MetaTrader 5 and a demo account

1. Download MetaTrader 5 from your broker (or from metatrader5.com) and install it.
2. Open a **demo** account with about **$5,000**. Any broker offering `XAUUSD` works.
   Write down three things — you will need them:
   - login number (e.g. `51234567`)
   - password
   - server name (e.g. `ICMarketsSC-Demo`)
3. In the terminal: **Tools → Options → Expert Advisors** → tick
   **"Allow algorithmic trading"**. Also check the **Algo Trading** button in the
   toolbar is enabled (it turns green).

   Without this the bot can read prices but every order is rejected.
4. Open a gold chart (Ctrl+M for Market Watch → right-click → Show All → find
   `XAUUSD` or whatever your broker calls gold) and **scroll far back on the
   chart**. MT5 only downloads history you have looked at, and the backtester
   needs it.

> **Keep the terminal running.** The bot talks to it over Windows IPC — if the
> terminal is closed, the bot is blind.

---

## 2. Python 3.11 (64-bit)

The `MetaTrader5` package ships only as a **64-bit Windows** wheel. 32-bit Python
will fail to install it with a confusing error.

1. Download **Python 3.11.x, Windows installer (64-bit)** from python.org.
2. During install, tick **"Add python.exe to PATH"**.
3. Verify in a new Command Prompt — it must say `64 bit`:

   ```
   python -c "import platform; print(platform.python_version(), platform.architecture())"
   ```

Then install the bot's dependencies from the project folder:

```
pip install -r requirements.txt
```

---

## 3. Anthropic API key (for the AI agents)

The agents are disabled by default, so you can skip this until we switch them on
— but it is a two-minute job.

1. Go to **console.anthropic.com** and sign up.
2. **Billing** → add about **$10** of credit. That lasts a long time at our usage
   (see "Cost" below).
3. **API keys** → **Create key** → copy it. It is shown **once**.
4. Store it as a Windows environment variable — never in a file:

   ```
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```

   Close and reopen your Command Prompt for it to take effect.

**Cost:** with the default per-agent models and event-driven triggering, expect
roughly **$1.25/day (~$38/month)**. On a $5,000 account that is about 0.75% of
equity per month, which comes straight out of returns — so `daily_cost_limit_usd`
in the config stops the agents once the day's spend passes your ceiling.

---

## 4. Telegram alerts and kill switch

Also optional at this stage, but this is how you keep control of a bot running
while you are away from the PC.

1. In Telegram, message **@BotFather** → send `/newbot` → follow the prompts →
   copy the **token** it gives you.
2. Message **@userinfobot** → it replies with your numeric **Id**. That number is
   your **chat ID**.
3. Store both as environment variables:

   ```
   setx TELEGRAM_BOT_TOKEN "123456:ABC-..."
   setx TELEGRAM_CHAT_ID "987654321"
   ```

> **The chat ID is a number, not a username.** Your bot's `@name` (the handle you
> chose in `/newbot`) identifies the *bot*; the chat ID identifies *you*, the
> person it sends alerts to. Putting the bot's username in `chat_id` produces a
> "chat not found" error and no alerts. Get the number from @userinfobot.

> **Treat the token like a password.** Anyone who has it can read every alert and
> send your bot commands — including `/flat`, which closes your positions. Never
> put it in `config.yaml`, a screenshot, a chat message, or a file in the repo:
> it belongs only in the environment variable above. If it is ever exposed,
> revoke it immediately in @BotFather: `/mybots` → your bot → **API Token** →
> **Revoke current token**, then set the new one with `setx`.

Once enabled you get alerts on every entry, exit and error, and can send
`/status`, `/pause`, `/resume`, `/flat` (close everything) and `/pnl` from your phone.

---

## 5. Create your config

```
copy config\config.example.yaml config\config.yaml
```

Then open `config\config.yaml` and set:

```yaml
mt5:
  login: 51234567              # your demo login
  password: ${MT5_PASSWORD}    # set with:  setx MT5_PASSWORD "yourpassword"
  server: "ICMarketsSC-Demo"   # exact server name from the terminal
```

Leave everything else at its defaults for now. In particular leave:

```yaml
dry_run: true
live_confirm: null
```

`config/config.yaml` is gitignored — your credentials never reach the repository.
Only `config.example.yaml` is committed.

---

## 6. Run the connection check

**Do this during London or New York hours** so the spread reading is meaningful.

```
python scripts/check_connection.py
```

It places no orders. It verifies your config is valid and consistent, the terminal
is reachable, algo trading is on, finds your broker's gold symbol, checks that 1%
risk produces a legal position size on your balance, and reports the live spread.

Expected output ends with `All checks passed.`

**If something fails**, the message says what to do. The three common ones:

| Message | Fix |
|---|---|
| `MetaTrader5 could not be imported` | You are not on 64-bit Windows Python, or `pip install -r requirements.txt` did not run. |
| `algorithmic trading is DISABLED` | Enable the Algo Trading button in the terminal toolbar. |
| `Could not find a tradable gold symbol` | Market Watch → Show All, find your broker's exact gold name, put it in `mt5.symbol`. |

Send me the output either way and I will read it.

---

## 7. Export history for backtesting

Once the connection check passes:

```
python scripts/export_history.py --months 12
```

This writes `data/xauusd_m15.parquet` and `data/xauusd_m1.parquet`. The M1 file
matters: the backtester uses it to determine whether a stop or a target was hit
first *inside* a bar. Without it, backtest results are optimistic in a way live
trading never reproduces.

If it reports far fewer bars than requested, scroll further back on the chart in
the terminal and re-run — brokers only serve history you have viewed.

---

## What happens next

Nothing trades yet. The next slice adds the strategy, the Risk Warden and the
backtester, and we look at whether the strategy has any edge *before* it is
allowed near an order.

The order of operations from here is deliberate:

1. Backtest on your exported history — does the strategy have an edge at all?
2. Dry run for a few days — the full pipeline, journalling every decision, sending nothing.
3. Demo trading for 2–4 weeks with real order flow.
4. Only then, and only if the numbers justify it, discuss a small live account.
