#!/usr/bin/env python3
"""Run the same strategy on M5 and M15 and put the numbers side by side.

    python scripts/compare_timeframes.py --m5 data/xauusd_m5.parquet \
                                         --m15 data/xauusd_m15.parquet \
                                         --m1 data/xauusd_m1.parquet

M5 and M15 are both supported scalping timeframes, and which one to trade is a
question for the data rather than for taste. The tradeoff is structural:

  * M5 produces roughly three times the SIGNALS, so more chances to be right.
  * M5's ATR is smaller, so stops are tighter — and the SAME spread then eats a
    much larger share of every trade's risk. A 25-point spread costs 6% of risk
    against a 400-point M15 stop and 17% against a 150-point M5 stop.

Three times the signals does not mean three times the trades, and the "blocked
by" column is where that shows up. On a losing run M5 hits the consecutive-loss
halt far more often, so a large share of its extra signals arrive while trading
is stopped. A timeframe that generates more opportunities and converts fewer of
them is telling you something about its hit rate, not its throughput.

The exchange rate between opportunity and cost depends entirely on your broker's
spread, which is why this script sweeps several values. A timeframe that only
works at 15 points is not one you can trade on an account that sees 30 through
a news release.

Held identical across both runs: indicators, entry rules, the Risk Warden and
the cost model — so the comparison isolates the timeframe. Note that the EMA and
ATR periods are bar counts, not clock times, so on M5 they look back a third as
far in wall-clock terms. That is the honest default; tuning the periods per
timeframe is a separate question, and one to answer only after this one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.backtest import Backtester, Costs, default_gold_spec  # noqa: E402
from goldbot.config import load_config  # noqa: E402

ROW = "  {:<10} {:>7} {:>7} {:>12} {:>9} {:>9} {:>8}  {:<24}"


def load(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    if not path.exists():
        raise SystemExit(f"No data at {path}. Run scripts/export_history.py first.")
    frame = pd.read_parquet(path)
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.set_index("time")
    return frame.sort_index()


def run(cfg, bars, m1, timeframe: str, costs: Costs, balance: float):
    """Backtest one timeframe, with the strategy config pinned to it."""
    tuned = cfg.model_copy(update={"strategy": cfg.strategy.model_copy(update={"timeframe": timeframe})})
    tester = Backtester(tuned, spec=default_gold_spec(), costs=costs, starting_balance=balance)
    return tester.run(bars, m1=m1)


def _dominant_skip(skipped: dict[str, int]) -> str:
    """The gate that declined the most signals, with its share.

    Signals outside the trading session are excluded. They are not a gate
    rejecting a viable trade — they are the strategy firing at 3am, when the bot
    was never going to trade anyway. Counting them buries the reason that
    actually matters underneath a number that only tracks how many bars the
    timeframe produces.
    """
    gates = {k: v for k, v in skipped.items() if k != "session"}
    if not gates:
        return "-"
    reason, count = max(gates.items(), key=lambda kv: kv[1])
    share = count / sum(gates.values()) * 100.0
    return f"{reason} ({share:.0f}%)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--m5", default=None, help="Parquet of M5 bars")
    parser.add_argument("--m15", default=None, help="Parquet of M15 bars")
    parser.add_argument("--m1", default=None, help="Parquet of M1 bars (strongly recommended)")
    parser.add_argument("--balance", type=float, default=5000.0)
    parser.add_argument(
        "--spreads",
        default="15,25,35",
        help="Comma-separated spreads in points to test. The point of the exercise: "
        "M5's viability is far more spread-sensitive than M15's.",
    )
    parser.add_argument("--slippage", type=float, default=2.0)
    parser.add_argument("--commission", type=float, default=7.0)
    args = parser.parse_args()

    if not args.m5 and not args.m15:
        raise SystemExit("Give at least one of --m5 / --m15.")

    cfg = load_config(args.config)
    m1 = load(Path(args.m1)) if args.m1 else None
    datasets = [
        (name, load(Path(path)))
        for name, path in (("M5", args.m5), ("M15", args.m15))
        if path
    ]

    print("=" * 78)
    print("  goldbot — M5 vs M15")
    print("=" * 78)
    if m1 is None:
        print("  WARNING: no --m1 file. Every bar holding both the stop and the target")
        print("  is scored as a loss. That penalty is heavier on M15 (bigger bars), so")
        print("  the comparison is biased toward M5. Export M1 before deciding.")
        print()

    for spread in [float(x) for x in args.spreads.split(",")]:
        costs = Costs(
            spread_points=spread,
            slippage_points=args.slippage,
            commission_per_lot_round_turn=args.commission,
        )
        print(f"  SPREAD {spread:g} POINTS")
        print(ROW.format(
            "timeframe", "trades", "win %", "expectancy", "net %", "maxDD %",
            "skipped", "mostly blocked by",
        ))
        print("  " + "-" * 96)

        for name, bars in datasets:
            result = run(cfg, bars, m1, name, costs, args.balance)
            stats = result.stats()
            # Out-of-session signals are excluded: they say more about the bar
            # count of the timeframe than about the strategy.
            skipped = sum(v for k, v in result.skipped.items() if k != "session")
            blocked = _dominant_skip(result.skipped)
            if stats.get("trades", 0) == 0:
                # A blank row invites the reader to assume a bug. Name the gate.
                print(ROW.format(name, 0, "-", "-", "-", "-", skipped, blocked))
                continue
            print(ROW.format(
                name,
                f"{stats['trades']:.0f}",
                f"{stats['win_rate_pct']:.1f}",
                f"{stats['expectancy_r']:+.3f}R",
                f"{stats['total_return_pct']:+.1f}",
                f"{stats['max_drawdown_pct']:.1f}",
                skipped,
                blocked,
            ))
        print()

    print("  HOW TO READ THIS")
    print("    * Zero trades usually means a gate, not a bug — the 'mostly blocked")
    print("      by' column names it. 'spread too wide' means every entry was")
    print("      vetoed because the tested spread exceeds risk.max_spread_points.")
    print("    * 'skipped' counts in-session signals the risk gates declined.")
    print("      Out-of-hours signals are excluded — they track how many bars a")
    print("      timeframe has, not how good it is.")
    print("    * 'halted' as the dominant reason is the important one: it means")
    print("      the timeframe lost five in a row often enough to keep stopping")
    print("      itself, so its extra signals never became trades.")
    print("    * Expectancy is the number that decides it, not trade count and not")
    print("      win rate. More trades at a negative expectancy is a faster loss.")
    print("    * Watch how each row degrades as the spread rises. A timeframe that")
    print("      only works at the tightest spread is one bad news event from")
    print("      unprofitable — your broker will not hold 15 points through NFP.")
    print("    * If M5 wins on expectancy but its drawdown is far deeper, it is")
    print("      taking more risk to get there, not finding a better edge.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
