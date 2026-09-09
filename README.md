# Trade-bot — automated XAUUSD trading on MetaTrader 5

A gold trading bot for MetaTrader 5, with a deterministic strategy core and a
layer of Claude agents that can only ever *reduce* risk.

**Status: Slice 3 of 4 complete — the bot runs end to end in dry-run.**
Strategy, Risk Warden, executor, engine loop, journal, calendar blackouts,
Telegram alerts and the daily routine are built and tested (329 tests). In
dry-run it opens *paper* positions at live prices and manages them on live
ticks; **no order is sent**. `dry_run: true` is the shipped default and sending
real orders requires two independent opt-ins. The eight Claude agents are
Slice 4 and are not yet wired in.

## Start here

[`docs/SETUP.md`](docs/SETUP.md) — MT5 demo account, 64-bit Python, API key,
Telegram, then the read-only connection check.

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml    # then fill in your MT5 login
python scripts/check_connection.py                  # places no orders

# on Windows, once connected — export history for the backtester
python scripts/export_history.py --months 12

# anywhere
python scripts/run_backtest.py --data data/xauusd_m15.parquet \
                               --m1 data/xauusd_m1.parquet
python scripts/check_no_lookahead.py                # proves the backtest isn't cheating

# on Windows — the bot itself, dry run
python scripts/run_bot.py --cycles 3                # smoke test: start, 3 cycles, stop
python scripts/run_bot.py                           # leave it running
```

[`docs/STRATEGY.md`](docs/STRATEGY.md) — what the rules actually are, how
positions are sized, and how to read a backtest without fooling yourself.

## Architecture

Authority flows one way, and no agent can widen risk:

```
strategy (code) → agents may VETO or SHRINK → Risk Warden (code) → executor
```

The day runs to a fixed routine keyed to the London open — pre-flight at 07:30,
two hunt windows, a wind-down, a flatten at 19:30, a debrief. Every day ends
flat. Phases decide what is *permitted*; inside a phase the engine is
event-driven. Full schedule and roster in [`docs/AGENTS.md`](docs/AGENTS.md).

| Agent | Runs | Model / effort | Authority |
|---|---|---|---|
| Session Supervisor | Once, pre-flight | Opus 5 / xhigh | Day plan: normal / reduced / stand aside. **Reduce only.** |
| News Scout | Pre-flight + hourly | Opus 5 / high | Advisory |
| Regime Analyst | Pre-flight + every 4h | Opus 5 / high | Reduce only |
| Decision Agent | Per signal | Opus 5 / xhigh | Veto / shrink |
| Devil's Advocate | Per signal | Opus 5 / xhigh | Veto / shrink |
| Trade Manager | While a position is open | Opus 5 / high | Reduce risk only |
| Day Auditor | Once, debrief | Opus 5 / high | Reports only |
| Performance Reviewer | Weekly | Opus 5 / xhigh | Reports only |

Deterministic by design, never an agent: **risk management, order execution,
news-event blackout timing, the connection watchdog, and signal generation.**

## Hard safety rules

- Every order carries a **server-side stop loss** — a crashed bot or sleeping PC still has protection.
- Two independent opt-ins required for live orders (`dry_run: false` **and** an exact confirmation phrase).
- Daily loss cap, equity floor, consecutive-loss halt, spread gate, daily trade cap.
- Every day ends flat at 19:30 London — no overnight holds, no weekend gaps.
- Magic-number isolation — the bot cannot see or touch trades you place by hand.
- Agents fail **closed**: an API error, timeout, malformed response or refusal means *no trade*.

## Platform requirement

The `MetaTrader5` Python package is **Windows-only** — it talks to a running MT5
desktop terminal over Windows IPC, and there is no cloud or REST alternative. The
bot must run on a Windows machine alongside the terminal. Everything else
(strategy, risk, backtester, tests) runs anywhere.

## Tests

```bash
python -m pytest tests/ -q
```

The suite runs on any OS: a fake MT5 terminal (`tests/fake_mt5.py`) stands in for
the real one, so requotes, filling-mode rejection and invalid stops are all tested
without a live account. Indicators are checked against values computed by hand
from Wilder's definitions, every risk veto has its own test, and the strategy is
tested for lookahead by re-evaluating it on a truncated frame.

Separately, `scripts/check_no_lookahead.py` replays the strategy over random
walks. Random data holds no edge, so a positive result there would mean the
backtester is reading bars it should not see. It currently reports about −0.10R
mean expectancy across six seeds, which is the correct answer.

## Expectations

This builds correct, auditable machinery. It does not guarantee a profitable
strategy. The backtester and the demo phase exist to find out whether the strategy
has an edge before real money is involved — including the possibility that it does not.

Risk per trade stays at 1% regardless of any return target. The weekly goal in
`reporting:` is a benchmark the reports measure against; nothing in the trading
path can read it, so a return target can never push the bot into over-trading to
catch up.
