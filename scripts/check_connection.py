#!/usr/bin/env python3
"""Smoke test: prove the bot can talk to your MetaTrader 5 terminal.

Run this FIRST, before anything else. It places no orders and changes nothing —
it only reads. Run it during your trading session (London or New York hours) so
the spread figures are representative.

    python scripts/check_connection.py

What it verifies:
  1. The config file loads and every risk value is internally consistent.
  2. The MT5 terminal is reachable and logged in.
  3. Algorithmic trading is enabled (without it the bot can read but never trade).
  4. Your broker's gold symbol is found and tradable.
  5. Contract specs are sane, and 1% risk actually produces a valid position size.
  6. The current spread is inside your configured limit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.config import load_config  # noqa: E402
from goldbot.mt5_client import MT5Client, MT5Error, MT5UnavailableError  # noqa: E402

OK, WARN, FAIL = "  [OK]  ", " [WARN] ", " [FAIL] "


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--bars", type=int, default=10, help="How many recent bars to print")
    args = parser.parse_args()

    print("=" * 74)
    print("  goldbot — connection check   (read-only, places no orders)")
    print("=" * 74)

    # --- 1. config ----------------------------------------------------------
    try:
        cfg = load_config(args.config)
    except FileNotFoundError as exc:
        print(FAIL + str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the user
        print(FAIL + f"config is invalid:\n{exc}")
        return 1

    print(OK + f"config loaded from {args.config}")
    print(
        f"        dry_run={cfg.dry_run}  live_orders_armed={cfg.live_orders_armed}  "
        f"risk={cfg.risk.risk_per_trade_pct}%/trade  daily_cap={cfg.risk.daily_loss_limit_pct}%"
    )
    if cfg.live_orders_armed:
        print(WARN + "LIVE ORDERS ARE ARMED. This config will send real orders.")

    # --- 2. terminal --------------------------------------------------------
    client = MT5Client(cfg.mt5)
    try:
        account = client.connect()
    except MT5UnavailableError as exc:
        print(FAIL + str(exc))
        return 1
    except MT5Error as exc:
        print(FAIL + str(exc))
        return 1

    print(OK + f"connected to MT5 — account {account.login} on {account.server}")
    print(
        f"        balance={account.balance:,.2f} {account.currency}  "
        f"equity={account.equity:,.2f}  free margin={account.margin_free:,.2f}  "
        f"leverage=1:{account.leverage}"
    )

    # --- 3. algo trading ----------------------------------------------------
    if account.trade_allowed:
        print(OK + "algorithmic trading is enabled")
    else:
        print(
            FAIL
            + "algorithmic trading is DISABLED. Click the 'Algo Trading' button in the\n"
            "        terminal toolbar, or enable Tools -> Options -> Expert Advisors ->\n"
            "        'Allow algorithmic trading'. Until then the bot can read prices but\n"
            "        every order will be rejected."
        )

    exit_code = 0
    try:
        # --- 4. symbol ------------------------------------------------------
        try:
            symbol = client.discover_symbol()
        except MT5Error as exc:
            print(FAIL + str(exc))
            return 1

        print(OK + f"gold symbol resolved: {symbol}")
        if not cfg.mt5.symbol:
            print(f"        (auto-discovered — pin it with mt5.symbol: {symbol} in config)")

        spec = client.symbol_spec()
        print(
            f"        digits={spec.digits}  point={spec.point}  "
            f"contract_size={spec.contract_size:g} oz/lot  profit_ccy={spec.currency_profit}"
        )
        print(
            f"        volume: min={spec.volume_min} max={spec.volume_max} "
            f"step={spec.volume_step}   broker min stop distance="
            f"{spec.stops_level_points} points ({spec.stops_level_points * spec.point:.2f} price)"
        )
        print(f"        value of a 1-point move on 1.00 lot: "
              f"{spec.value_per_point_per_lot:.4f} {account.currency}")
        source = (
            "broker's order_calc_profit" if spec.broker_value_per_point
            else "tick_value/tick_size" if spec.tick_value_per_point
            else "contract size (fallback)"
        )
        print(f"        source: {source}")
        if spec.broker_value_per_point and spec.tick_value_per_point:
            print(f"        (tick_value implies {spec.tick_value_per_point:.4f} — "
                  f"the calculator is authoritative)")

        # --- 4b. contract sanity ---------------------------------------------
        # A wrong point value mis-sizes EVERY position by that factor, and an
        # understated one oversizes. This is the check that has to fail loudly.
        sane, why = client.contract_sanity()
        if sane:
            print(f"{OK}point value agrees with the contract")
            print(f"        {why}")
        else:
            exit_code = 1
            print(f"{FAIL}POINT VALUE IS NOT TRUSTWORTHY — do not trade this account")
            print(f"        {why}")
            print("        Every position would be sized by that factor. Pin the correct")
            print("        value with mt5.value_per_point_override in config, or use a")
            print("        broker whose contract specification is self-consistent.")

        # --- 5. sizing sanity ------------------------------------------------
        tick = client.get_tick()
        spread_pts = client.spread_points()
        risk_amount = account.equity * cfg.risk.risk_per_trade_pct / 100.0

        # Use a representative stop of 1.5 x a typical gold ATR (~$4 on M15).
        example_stop_price = 4.0
        stop_points = example_stop_price / spec.point
        raw_lots = risk_amount / (stop_points * spec.value_per_point_per_lot)
        lots = spec.normalize_volume(raw_lots)

        print(
            OK
            + f"sizing check: risking {cfg.risk.risk_per_trade_pct}% "
            f"({risk_amount:,.2f} {account.currency}) with a ${example_stop_price:.2f} stop"
        )
        print(f"        -> {lots} lots (raw {raw_lots:.4f}, floored to the {spec.volume_step} step)")
        if lots < spec.volume_min:
            print(
                FAIL
                + f"that is below the broker minimum of {spec.volume_min} lots. Your account\n"
                "        is too small for this stop distance at this risk percentage — the bot\n"
                "        would skip nearly every trade. Use a larger demo balance."
            )
            exit_code = 1
        else:
            actual_risk = lots * stop_points * spec.value_per_point_per_lot
            print(
                f"        actual risk at {lots} lots: {actual_risk:,.2f} {account.currency} "
                f"({actual_risk / account.equity * 100:.2f}% of equity)"
            )

        # --- 6. spread ------------------------------------------------------
        print(
            OK
            + f"current market: bid={tick.bid:.{spec.digits}f} ask={tick.ask:.{spec.digits}f} "
            f"spread={tick.spread:.{spec.digits}f} ({spread_pts:.0f} points)"
        )
        if spread_pts > cfg.risk.max_spread_points:
            print(
                WARN
                + f"spread {spread_pts:.0f} exceeds your max_spread_points "
                f"({cfg.risk.max_spread_points}); the bot would decline to enter right now.\n"
                "        Normal outside session hours. If it is also wide during London/NY,\n"
                "        raise the limit or reconsider scalping with this broker."
            )
        else:
            print(f"        inside your {cfg.risk.max_spread_points}-point limit")

        # --- bars -----------------------------------------------------------
        tf = cfg.strategy.timeframe
        bars = client.get_closed_bars(tf, args.bars)
        print(OK + f"fetched {len(bars)} closed {tf} bars (most recent last)")
        print()
        print(bars[["open", "high", "low", "close", "volume"]].to_string())
        print()

        positions = client.positions()
        if positions:
            print(WARN + f"{len(positions)} position(s) already open with magic {cfg.mt5.magic}:")
            for p in positions:
                print(
                    f"        #{p.ticket} {p.direction} {p.volume} @ {p.open_price} "
                    f"sl={p.sl} tp={p.tp} pnl={p.profit:.2f}"
                )
        else:
            print(OK + f"no open positions carrying this bot's magic number ({cfg.mt5.magic})")

    finally:
        client.shutdown()

    print()
    print("=" * 74)
    if exit_code == 0:
        print("  All checks passed. Nothing was traded.")
        print("  Next: python scripts/export_history.py --months 12")
        print("  Then: python scripts/run_bot.py --cycles 3   (still sends no orders)")
    else:
        print("  Some checks FAILED — see above. Do not proceed until they are resolved.")
    print("=" * 74)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
