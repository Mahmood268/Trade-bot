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

## 2. Python (64-bit)

The `MetaTrader5` package ships **only as 64-bit Windows wheels** — every file
on PyPI ends in `win_amd64`. 32-bit Python fails to install it with a confusing
error, and there is no macOS or Linux build at all.

**Any Python from 3.10 to 3.14 works** (3.12 included — there is a `cp312`
wheel). What matters is the *bitness*, not the version.

1. Download a **64-bit Windows** Python installer from python.org.
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

**Cost:** all eight agents run on Opus 5, so expect roughly **$4–8/day
(~$120–240/month)**. On a $5,000 account that is **2.5–5% of equity per month**,
which comes straight out of returns — more than many strategies produce in edge.
This is the single biggest structural problem with running eight Opus agents
against a small account, and it is why `daily_cost_limit_usd` (default $8) stops
them once the day's spend passes your ceiling. The lever is *cadence*, not model
choice: `trade_manager.min_interval_s` and `news_scout.cache_ttl_s` dominate the
bill, and doubling both roughly halves it.

The agents are all `enabled: false` today, so until Slice 4 this costs nothing.

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

This writes **three** files — `data/xauusd_m5.parquet`,
`data/xauusd_m15.parquet` and `data/xauusd_m1.parquet`. Both scalping
timeframes come down in one pass because exporting is the slow, Windows-only
step, and the M5-vs-M15 question is then settled from data with
`scripts/compare_timeframes.py` without coming back to the terminal.

The M1 file matters most: the backtester uses it to determine whether a stop or a
target was hit first *inside* a bar. Without it, backtest results are optimistic
in a way live trading never reproduces.

If it reports far fewer bars than requested, scroll further back on the chart in
the terminal and re-run — brokers only serve history you have viewed.

---

## What happens next

Nothing trades yet. The strategy, the Risk Warden, the backtester, the executor
and the engine are all built — see section 8 below for running the bot in
dry-run. The eight Claude agents are the next slice.

The order of operations from here is deliberate:

1. Backtest on your exported history — does the strategy have an edge at all?
2. Dry run for a few days — the full pipeline, journalling every decision, sending nothing.
3. Demo trading for 2–4 weeks with real order flow.
4. Only then, and only if the numbers justify it, discuss a small live account.

---

## 8. Run the bot (dry run)

Everything above was preparation. This is the first time the bot actually runs
its full loop — and in dry-run mode it still sends **no orders**.

```
python scripts\run_bot.py --cycles 3
```

`--cycles 3` runs three loop iterations and exits, so you can see it start up,
connect, and shut down cleanly. A healthy first run looks like this:

```
INFO  run_bot: dry run: no orders will be sent
INFO  goldbot.mt5_client: Connected to MT5: account 51234567 on ICMarketsSC-Demo, balance 5000.00 USD
INFO  goldbot.mt5_client: Resolved gold symbol: XAUUSD
INFO  goldbot.calendar: calendar refreshed: 6 relevant events this week
INFO  goldbot.engine: goldbot started (DRY RUN — no orders will be sent) | account ... | symbol XAUUSD, timeframe M15 | adopted 0 open position(s)
```

and your phone gets the same "goldbot started" message on Telegram. If Telegram
is enabled and you get nothing, check the three things in order: the bot token
is set as `TELEGRAM_BOT_TOKEN`, the chat ID is your **numeric** ID from
@userinfobot, and you have sent the bot at least one message (Telegram bots
cannot message you first).

Then leave it running for real:

```
python scripts\run_bot.py
```

or double-click `scripts\run_bot.bat`. It sleeps until the next phase boundary
or bar close, so it is idle most of the time. Stop it with **Ctrl-C**.

### What you will see during a dry-run day

| When (London) | What happens |
|---|---|
| 07:30 | Phase → pre-flight (agents join here in Stage B; for now it's logged) |
| 08:00 | Phase → hunt. Each closed M15 bar is evaluated. A signal that passes the session, calendar and Risk Warden gates opens a **paper** position at the live ask/bid — Telegram: `[DRY RUN] OPEN BUY 0.12 lots @ ...` |
| while open | Paper positions are stopped, targeted, moved to break-even and trailed on live ticks — Telegram: `CLOSED #900000 ... pnl -51.00 (stop)` |
| 12:00–13:30 | Phase → hold. Manages only. |
| 17:00 | Phase → wind-down. Tighten and exit only. |
| 19:30 | Phase → flatten. Everything closed — Telegram: `Flattened 1 position(s) — eod_flatten. Day P&L so far +48.00` |

Every one of those is also a row in `data/journal.db`. That file is the record,
and later the dashboard's data source.

### Commands from your phone

`/status` `/pnl` `/pause` `/resume` `/flat` `/help` — sent to your bot in
Telegram. `/flat` closes everything and pauses; `/resume` un-pauses and also
clears a consecutive-loss halt. Only your chat ID is honoured; anyone else who
finds the bot gets silence.

### When to go beyond dry run

Not yet. Leave it in dry run for **at least two or three trading days**, read
the journal, and check the paper trades make sense against the chart. Then we
look at the numbers together before `dry_run: false` is even discussed — and
that step needs *both* opt-ins in the config, on purpose.

---

## Troubleshooting the bot itself

| Symptom | Cause | Fix |
|---|---|---|
| `calendar download failed ... keeping cache from None` | ForexFactory unreachable | Harmless at start-up — it retries daily. Until it succeeds, entries are **blocked** (treated as a blackout). If it never succeeds, check your firewall allows `nfs.faireconomy.media`. |
| `terminal not connected — attempting reconnect` every cycle | MT5 closed, or lost login | Open the terminal and log in. The bot reconnects on its own. |
| Bot opens nothing all day | Look at `decisions` in the journal | Every declined signal has a `reason`. Spread over the cap and "outside the trading windows" are the usual ones. |
| Telegram silent | See section 4 | Token, numeric chat ID, and you must message the bot first. |
