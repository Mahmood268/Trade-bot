#!/usr/bin/env python3
"""Replay the strategy over exported history and report the result honestly.

    python scripts/run_backtest.py --data data/xauusd_m15.parquet \
                                   --m1 data/xauusd_m1.parquet

Get the data first with ``scripts/export_history.py`` on the Windows machine
where MT5 runs. The M1 file is optional but strongly recommended: without it,
any bar containing both the stop and the target is scored as a loss, because
the trading bar alone cannot say which came first.

Nothing here connects to a broker and nothing is ordered. The numbers are only
as good as the cost assumptions — pass ``--spread`` the value that
``check_connection.py`` reports during your actual trading hours.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.backtest import Backtester, Costs, default_gold_spec  # noqa: E402
from goldbot.config import load_config  # noqa: E402


def load_bars(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(
            f"No data at {path}. Run scripts/export_history.py on your Windows machine "
            "first — the backtester needs real bars from your own broker, since spreads "
            "and gold pricing differ between them."
        )
    frame = pd.read_parquet(path)
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.set_index("time")
    return frame.sort_index()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--data", required=True, help="Parquet of trading-timeframe bars")
    parser.add_argument("--m1", default=None, help="Parquet of M1 bars for intrabar resolution")
    parser.add_argument("--balance", type=float, default=5000.0)
    parser.add_argument("--spread", type=float, default=25.0, help="Spread in points")
    parser.add_argument("--slippage", type=float, default=2.0, help="Slippage in points")
    parser.add_argument(
        "--commission", type=float, default=7.0, help="Commission per lot, round turn"
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    bars = load_bars(Path(args.data))
    m1 = load_bars(Path(args.m1)) if args.m1 else None

    costs = Costs(
        spread_points=args.spread,
        slippage_points=args.slippage,
        commission_per_lot_round_turn=args.commission,
    )
    tester = Backtester(
        cfg, spec=default_gold_spec(), costs=costs, starting_balance=args.balance
    )
    result = tester.run(bars, m1=m1)

    print("=" * 74)
    print("  goldbot — backtest")
    print("=" * 74)
    print(f"  bars           : {result.bars:,}  ({bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d})")
    print(f"  intrabar data  : {'M1 resolved' if m1 is not None else 'NONE — ties scored as losses'}")
    print(f"  costs          : {args.spread:g}pt spread, {args.slippage:g}pt slippage, "
          f"{args.commission:g}/lot commission")
    print()

    stats = result.stats()
    if stats.get("trades", 0) == 0:
        print("  No trades. Check the session windows, the ADX filter and the data span.")
        _print_skips(result)
        return 1

    print("  RESULTS")
    print(f"    trades           : {stats['trades']:.0f}")
    print(f"    win rate         : {stats['win_rate_pct']:.1f}%")
    print(f"    expectancy       : {stats['expectancy_r']:+.3f}R per trade")
    print(f"    profit factor    : {stats['profit_factor']:.2f}")
    print(f"    net P&L          : {stats['net_pnl']:+,.2f}  "
          f"({stats['total_return_pct']:+.1f}%)")
    print(f"    max drawdown     : {stats['max_drawdown_pct']:.1f}%")
    print(f"    avg win / loss   : {stats['avg_win_r']:+.2f}R / {stats['avg_loss_r']:+.2f}R")
    print(f"    worst trade      : {stats['worst_trade_r']:+.2f}R")
    print(f"    commission paid  : {stats['commission_paid']:,.2f}")
    if result.halt_resumes:
        print(f"    halts resumed    : {result.halt_resumes}  "
              "(5-loss halts; live these need a manual /resume)")
    print()

    weekly = result.weekly_returns_pct()
    if not weekly.empty:
        target_min = cfg.reporting.weekly_target_pct_min
        hit = int((weekly >= target_min).sum())
        print("  AGAINST YOUR WEEKLY GOAL "
              f"({target_min:g}-{cfg.reporting.weekly_target_pct_max:g}%)")
        print(f"    weeks traded     : {len(weekly)}")
        print(f"    weeks at goal    : {hit} ({hit / len(weekly) * 100:.0f}%)")
        print(f"    median week      : {weekly.median():+.2f}%")
        print(f"    best / worst week: {weekly.max():+.2f}% / {weekly.min():+.2f}%")
        print()

    hours = result.by_hour()
    if not hours.empty:
        print("  BY ENTRY HOUR (UTC)")
        print(f"    {'hour':>5} {'trades':>7} {'expectancy':>12} {'net P&L':>12}")
        for hour, row in hours.iterrows():
            print(f"    {hour:>5} {row['trades']:>7.0f} {row['expectancy_r']:>+11.3f}R "
                  f"{row['net_pnl']:>+12,.2f}")
        print()

    _print_skips(result)

    print("  READ THIS BEFORE BELIEVING ANY OF IT")
    print("    * These are the deterministic rules only. The Claude agents cannot be")
    print("      replayed against historical news, so their effect is unmeasured here.")
    print("    * Costs are assumptions. Confirm the spread against check_connection.py")
    print("      run during your real trading hours, not an overnight reading.")
    print("    * A profitable backtest is a necessary condition, not a sufficient one.")
    print("=" * 74)
    return 0


def _print_skips(result) -> None:
    if not result.skipped:
        return
    print("  SIGNALS NOT TAKEN")
    for reason, count in sorted(result.skipped.items(), key=lambda kv: -kv[1]):
        print(f"    {count:>6}  {reason}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
