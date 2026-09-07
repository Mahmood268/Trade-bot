# Trade-bot — automated XAUUSD trading on MetaTrader 5

A gold trading bot for MetaTrader 5, with a deterministic strategy core and a
layer of Claude agents that can only ever *reduce* risk.

**Status: Slice 1 of 4 — foundation.** Nothing trades yet. `dry_run: true` is the
shipped default and sending real orders requires two independent opt-ins.

## Start here

[`docs/SETUP.md`](docs/SETUP.md) — MT5 demo account, 64-bit Python, API key,
Telegram, then the read-only connection check.

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml    # then fill in your MT5 login
python scripts/check_connection.py                  # places no orders
```

## Architecture

Authority flows one way, and no agent can widen risk:

```
strategy (code) → agents may VETO or SHRINK → Risk Warden (code) → executor
```

| Agent | Model | Role | Authority |
|---|---|---|---|
| News Scout | Sonnet 5 | Live gold/USD coverage → structured brief | Advisory |
| Regime Analyst | Sonnet 5 | H4/D1 structure + volatility → regime label | Reduce only |
| Decision Agent | Opus 5 | The main call on each signal | Veto / shrink |
| Devil's Advocate | Opus 5 | Argues the case against each trade | Veto / shrink |
| Trade Manager | Opus 5 | Manages open positions | Reduce risk only |
| Performance Reviewer | Opus 5 | Weekly journal analysis | Reports only |

Deterministic by design, never an agent: **risk management, order execution,
news-event blackout timing, the connection watchdog, and signal generation.**

## Hard safety rules

- Every order carries a **server-side stop loss** — a crashed bot or sleeping PC still has protection.
- Two independent opt-ins required for live orders (`dry_run: false` **and** an exact confirmation phrase).
- Daily loss cap, equity floor, consecutive-loss halt, spread gate, daily trade cap.
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
without a live account.

## Expectations

This builds correct, auditable machinery. It does not guarantee a profitable
strategy. The backtester and the demo phase exist to find out whether the strategy
has an edge before real money is involved — including the possibility that it does not.
