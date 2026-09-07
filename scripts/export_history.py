#!/usr/bin/env python3
"""Export MT5 price history to Parquet.

MetaTrader 5 is Windows-only, but the backtester should run anywhere. Run this
once on your Windows PC and the exported file can be backtested on any machine.

    python scripts/export_history.py --months 12
    python scripts/export_history.py --timeframe M5 --months 6 --out data/xauusd_m5.parquet

M1 bars are exported alongside the trading timeframe by default: the backtester
uses them to resolve whether a stop or a target was hit first *within* a bar.
Without that, backtests silently assume the favourable order and report results
that live trading will never reproduce.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.config import load_config  # noqa: E402
from goldbot.mt5_client import MT5Client, MT5Error  # noqa: E402

# Roughly how many bars fit in a month, per timeframe, for gold's 24/5 schedule.
BARS_PER_MONTH = {"M1": 31_000, "M5": 6_200, "M15": 2_100, "M30": 1_050, "H1": 525, "H4": 132, "D1": 22}


def export(client: MT5Client, timeframe: str, months: int, out: Path) -> int:
    count = BARS_PER_MONTH.get(timeframe, 2_100) * months
    print(f"  requesting ~{count:,} {timeframe} bars ...")
    df = client.get_closed_bars(timeframe, count)

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)

    span = df.index[-1] - df.index[0]
    print(
        f"  wrote {len(df):,} bars to {out}\n"
        f"    range: {df.index[0]:%Y-%m-%d %H:%M} -> {df.index[-1]:%Y-%m-%d %H:%M} UTC "
        f"({span.days} days)"
    )
    if len(df) < count * 0.5:
        print(
            "    NOTE: the broker returned far fewer bars than requested. Most brokers\n"
            "    limit downloadable history — scroll back on that chart in the terminal\n"
            "    to make it fetch more, then re-run this script."
        )
    return len(df)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--timeframe", default=None, help="Defaults to strategy.timeframe")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--no-m1", action="store_true",
        help="Skip the M1 export (backtests then cannot resolve intrabar stop/target order)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    timeframe = args.timeframe or cfg.strategy.timeframe

    client = MT5Client(cfg.mt5)
    try:
        client.connect()
        symbol = client.discover_symbol()
        print(f"Exporting {symbol} history ({args.months} months)")

        out = Path(args.out) if args.out else Path(f"data/{symbol.lower()}_{timeframe.lower()}.parquet")
        export(client, timeframe, args.months, out)

        if not args.no_m1 and timeframe != "M1":
            print("\nExporting M1 bars for intrabar fill simulation:")
            export(client, "M1", args.months, Path(f"data/{symbol.lower()}_m1.parquet"))
    except MT5Error as exc:
        print(f"FAILED: {exc}")
        return 1
    finally:
        client.shutdown()

    print("\nDone. These files are gitignored — they are data, not code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
