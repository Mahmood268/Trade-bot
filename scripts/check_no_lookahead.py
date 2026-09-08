#!/usr/bin/env python3
"""Lookahead check: replay the strategy over independent random walks.

    python scripts/check_no_lookahead.py

Real price series contain whatever edge the strategy is meant to exploit. A
random walk contains none. So on random data the result must be near zero or,
after spread and commission, negative. A consistent positive edge here would
mean the backtester is reading bars it should not be able to see — the single
most common and most expensive bug in this kind of system, and one that makes
every other number in the report meaningless.

Run this after any change to the strategy, the indicators or the backtester's
fill logic. It needs no broker connection and no historical data. It takes a
few minutes, which is why it is a script rather than part of the test suite.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.backtest import Backtester, Costs, default_gold_spec
from goldbot.config import load_config

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", default="config/config.yaml")
parser.add_argument("--seeds", type=int, default=6)
parser.add_argument("--months", type=int, default=6)
parser.add_argument("--spread", type=float, default=25.0)
args = parser.parse_args()

cfg = load_config(args.config)
costs = Costs(
    spread_points=args.spread, slippage_points=2.0, commission_per_lot_round_turn=7.0
)


def walk(seed: int, months: int):
    """A gold-like geometric random walk in M1 bars, plus its M15 resampling."""
    rng = np.random.default_rng(seed)
    # Gold runs about 0.9% daily volatility, which is ~0.024% per minute.
    n = 60 * 24 * 22 * months
    idx = pd.date_range("2025-01-06", periods=n, freq="1min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    close = 2000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.00024, n)))
    wick = np.abs(rng.normal(0.0, 0.35, n))
    open_ = np.concatenate([[2000.0], close[:-1]])
    m1 = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + wick,
                       "low": np.minimum(open_, close) - wick, "close": close}, index=idx)
    m15 = m1.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return m15, m1

print(f"{'seed':>6} {'trades':>7} {'win%':>7} {'expectancy':>12} {'net %':>9} {'maxDD %':>9}")
rows = []
for seed in range(1, args.seeds + 1):
    m15, m1 = walk(seed, args.months)
    r = Backtester(cfg, spec=default_gold_spec(), costs=costs, starting_balance=5000.0).run(m15, m1=m1)
    s = r.stats()
    if s.get("trades", 0) == 0:
        print(f"{seed:>6} {0:>7}")
        continue
    rows.append((s["expectancy_r"], s["total_return_pct"]))
    print(f"{seed:>6} {s['trades']:>7.0f} {s['win_rate_pct']:>6.1f}% "
          f"{s['expectancy_r']:>+11.3f}R {s['total_return_pct']:>+8.2f}% {s['max_drawdown_pct']:>8.1f}%")

exp = np.array([r[0] for r in rows]); ret = np.array([r[1] for r in rows])
print()
print(f"  mean expectancy across seeds : {exp.mean():+.3f}R  (sd {exp.std(ddof=1):.3f})")
print(f"  mean return across seeds     : {ret.mean():+.2f}%  (sd {ret.std(ddof=1):.2f})")
print()
if exp.mean() > 0.05:
    print("  FAIL: a positive edge on random data. Investigate before trusting any")
    print("        backtest — look for the strategy reading past bar i, or the")
    print("        backtester filling at a price the bar never traded at.")
    raise SystemExit(1)

print("  PASS: no edge on random data, which is the expected result. This does not")
print("        prove the strategy works — only that the backtest is not cheating.")
