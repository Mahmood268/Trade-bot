#!/usr/bin/env python3
"""Ask why the strategy loses, instead of guessing at fixes.

    python scripts/diagnose.py --data data/xauusd_m15.parquet \
                               --m1 data/xauusd_m1.parquet --spread 25

`run_backtest.py` answers "does this make money". It does not. This answers the
next question — *where does the money go* — which is the only question worth
asking before changing a single parameter.

Six sections, in the order they should be read:

  1. Baseline vs unhalted. The consecutive-loss halt stops the bot after five
     losses in a row, so a bad run measures the halt as much as the rules.
     Section 1 runs it again with the halt effectively off. That is the honest
     expectancy of the rules themselves.
  2. Where the money goes. Every exit reason with its share and its total R.
     A strategy whose winners mostly end as `breakeven_stop` is protecting
     itself out of its own edge, and the headline stats cannot show that.
  3. The excursion question. Of the trades that lost, how many were winning
     first? If most losers reached +1R before dying, the entries are fine and
     the exits are wrong. If they never went anywhere, the entries are wrong.
     These two diagnoses lead to opposite fixes, which is why guessing is
     expensive.
  4. Long vs short. Gold has trended hard; a rule set that is flat overall may
     be a good long strategy paying for a bad short one.
  5. By entry hour, and by month. A schedule problem and a decay problem look
     identical in the headline number.
  6. An exit-rule grid, scored IN-SAMPLE and OUT-OF-SAMPLE separately.

READ THIS BEFORE USING SECTION 6. Twelve variants over one year of one
instrument will always produce a best one, even from pure noise. That is why
the trades are split chronologically and both halves are printed: a variant
that is good in the first 60% and bad in the last 40% is a coincidence you
found, not an edge you can trade. The rule is fixed in advance — a variant is
only interesting if it is positive in BOTH halves. Anything else gets thrown
away, however good the combined number looks.

Nothing here connects to a broker and nothing is ordered.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.backtest import Backtester, Costs, Trade, default_gold_spec  # noqa: E402
from goldbot.config import load_config  # noqa: E402

# The halt is capped at 50 by the config schema, which is far more losses in a
# row than any survivable strategy produces — so this switches it off in
# practice without weakening the validation that protects the live path.
NO_HALT = 50

# Fraction of trades treated as in-sample. The remainder is never used to
# choose anything; it only ever confirms or refutes a choice made on the first
# part. 60/40 keeps enough trades in the second half to be worth reading.
IN_SAMPLE_FRACTION = 0.6

# Deliberately small. Every extra variant raises the chance that the winner is
# noise, and these three knobs are the ones the excursion analysis in section 3
# actually points at.
BREAKEVEN_VARIANTS: list[float | None] = [None, 1.0]
TRAIL_VARIANTS: list[float | None] = [None, 1.5]
TARGET_VARIANTS: list[float] = [1.5, 2.0, 3.0]


def load_bars(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(
            f"No data at {path}. Run scripts/export_history.py on your Windows machine first."
        )
    frame = pd.read_parquet(path)
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.set_index("time")
    return frame.sort_index()


def with_risk(cfg, **changes):
    """A copy of the config with some risk limits changed. The original is frozen."""
    return cfg.model_copy(update={"risk": cfg.risk.model_copy(update=changes)})


def with_strategy(cfg, **changes):
    return cfg.model_copy(update={"strategy": cfg.strategy.model_copy(update=changes)})


def expectancy(trades: list[Trade]) -> float:
    return float(np.mean([t.r_multiple for t in trades])) if trades else 0.0


def win_rate(trades: list[Trade]) -> float:
    return float(np.mean([t.pnl > 0 for t in trades]) * 100.0) if trades else 0.0


def rule(title: str) -> None:
    print()
    print(f"  {title}")
    print("  " + "-" * 72)


# --- sections ---------------------------------------------------------------

def section_halt(tester_for, bars, m1) -> list[Trade]:
    """Baseline against the same rules with the halt off. Returns the unhalted trades."""
    rule("1. THE HALT — is it measuring the rules, or the circuit breaker?")

    baseline = tester_for().run(bars, m1=m1)
    unhalted = tester_for(risk={"max_consecutive_losses": NO_HALT}).run(bars, m1=m1)

    base_t, un_t = baseline.closed, unhalted.closed
    print(f"    {'':22} {'trades':>8} {'win %':>8} {'expectancy':>12} {'net %':>9}")
    for label, res, trades in (
        ("as configured", baseline, base_t),
        ("halt disabled", unhalted, un_t),
    ):
        stats = res.stats()
        net = stats.get("total_return_pct", 0.0)
        print(f"    {label:22} {len(trades):>8} {win_rate(trades):>7.1f}% "
              f"{expectancy(trades):>+11.3f}R {net:>+8.1f}%")

    print(f"    the halt fired and was resumed {baseline.halt_resumes} time(s)")
    if not un_t:
        return un_t

    gap = expectancy(un_t) - expectancy(base_t)
    if abs(gap) < 0.02:
        print("    -> The halt is not distorting the picture. Read the rest as the rules.")
    elif gap > 0:
        print("    -> The halt is COSTING expectancy: it keeps stopping the bot after")
        print("       streaks that would have recovered. That is a risk-appetite")
        print("       question, not evidence the rules work.")
    else:
        print("    -> The halt is EARNING its place: the rules are worse without it.")
        print("       Losses cluster, so stopping after five is doing real work.")
    return un_t


def section_exits(trades: list[Trade]) -> None:
    rule("2. WHERE THE MONEY GOES — every exit reason, with its total")
    if not trades:
        print("    no trades")
        return

    frame = pd.DataFrame({
        "reason": [t.exit_reason or "unknown" for t in trades],
        "r": [t.r_multiple for t in trades],
    })
    grouped = frame.groupby("reason").agg(
        n=("r", "size"), mean_r=("r", "mean"), total_r=("r", "sum")
    ).sort_values("total_r")

    print(f"    {'exit reason':18} {'count':>7} {'share':>7} {'avg R':>9} {'total R':>10}")
    total = len(frame)
    for reason, row in grouped.iterrows():
        print(f"    {reason:18} {row['n']:>7.0f} {row['n'] / total * 100:>6.1f}% "
              f"{row['mean_r']:>+8.2f}R {row['total_r']:>+9.1f}R")

    worst = grouped.index[0]
    print(f"    -> The single biggest drain is '{worst}' at "
          f"{grouped.loc[worst, 'total_r']:+.1f}R.")
    if "breakeven_stop" in grouped.index:
        be = grouped.loc["breakeven_stop"]
        print(f"       Break-even stops: {be['n']:.0f} trades ({be['n'] / total * 100:.0f}%) "
              f"for {be['total_r']:+.1f}R. Each one was a trade that reached +1R")
        print("       and was then given back — the cost of the break-even rule,")
        print("       which section 6 prices by turning it off.")


def section_excursions(trades: list[Trade]) -> None:
    rule("3. WERE THE LOSERS EVER WINNING? — entries problem or exits problem")
    losers = [t for t in trades if t.pnl <= 0]
    winners = [t for t in trades if t.pnl > 0]
    if not losers:
        print("    no losing trades")
        return

    mfe = np.array([t.mfe_r for t in losers])
    print(f"    losing trades: {len(losers)}   (winners: {len(winners)})")
    print(f"    best point each loser reached, in R:")
    for level in (0.5, 1.0, 1.5, 2.0):
        share = float((mfe >= level).mean() * 100.0)
        print(f"       reached +{level:.1f}R before losing : {share:>5.1f}%  "
              f"({int((mfe >= level).sum())} trades)")
    print(f"    median best point of a loser : {np.median(mfe):+.2f}R")

    if winners:
        mae = np.array([t.mae_r for t in winners])
        print(f"    median worst point of a winner: {np.median(mae):+.2f}R  "
              "(how much heat the winners took)")

    reached_1r = float((mfe >= 1.0).mean())
    print()
    if reached_1r >= 0.40:
        print(f"    -> {reached_1r * 100:.0f}% of losers were up a full R first. The entries")
        print("       are finding real moves; the EXITS are giving them back. Fixing")
        print("       exits is cheap and does not require a new strategy.")
    elif reached_1r <= 0.20:
        print(f"    -> Only {reached_1r * 100:.0f}% of losers ever reached +1R. The entries")
        print("       are not finding moves at all. No exit rule rescues this — the")
        print("       ENTRY criteria are what is wrong, and section 6 will not help.")
    else:
        print(f"    -> {reached_1r * 100:.0f}% of losers reached +1R. Mixed: the entries have")
        print("       some signal but not much. Exit changes are worth pricing, but")
        print("       do not expect them to turn this positive on their own.")


def section_direction(trades: list[Trade]) -> None:
    rule("4. LONG vs SHORT — is one side paying for the other?")
    if not trades:
        print("    no trades")
        return
    frame = pd.DataFrame({
        "direction": [t.direction for t in trades],
        "r": [t.r_multiple for t in trades],
        "win": [t.pnl > 0 for t in trades],
    })
    grouped = frame.groupby("direction").agg(
        n=("r", "size"), win_pct=("win", "mean"), mean_r=("r", "mean"), total_r=("r", "sum")
    )
    print(f"    {'side':8} {'trades':>8} {'win %':>8} {'expectancy':>12} {'total R':>10}")
    for side, row in grouped.iterrows():
        print(f"    {side:8} {row['n']:>8.0f} {row['win_pct'] * 100:>7.1f}% "
              f"{row['mean_r']:>+11.3f}R {row['total_r']:>+9.1f}R")

    if len(grouped) == 2:
        best, worst = grouped["mean_r"].idxmax(), grouped["mean_r"].idxmin()
        if grouped.loc[best, "mean_r"] > 0 > grouped.loc[worst, "mean_r"]:
            print(f"    -> {best.upper()}s are profitable and {worst.upper()}s are not.")
            print("       Tempting, and dangerous: over a year in which gold rose, this is")
            print("       what a long-only bias looks like whether or not the rules work.")
            print("       Do not trade one side only on the strength of one trending year.")


def section_by_period(trades: list[Trade]) -> None:
    rule("5. WHEN — by entry hour, then by month")
    if not trades:
        print("    no trades")
        return

    frame = pd.DataFrame({
        "hour": [t.entry_time.hour for t in trades],
        "month": [t.entry_time.strftime("%Y-%m") for t in trades],
        "r": [t.r_multiple for t in trades],
    })

    hours = frame.groupby("hour").agg(n=("r", "size"), mean_r=("r", "mean"), total_r=("r", "sum"))
    print(f"    {'hour (UTC)':>11} {'trades':>8} {'expectancy':>12} {'total R':>10}")
    for hour, row in hours.iterrows():
        print(f"    {hour:>11} {row['n']:>8.0f} {row['mean_r']:>+11.3f}R {row['total_r']:>+9.1f}R")

    months = frame.groupby("month").agg(n=("r", "size"), total_r=("r", "sum"))
    print()
    print(f"    {'month':>11} {'trades':>8} {'total R':>10}")
    for month, row in months.iterrows():
        magnitude = min(40, int(abs(row["total_r"])))
        bar = ("+" if row["total_r"] >= 0 else "-") * magnitude
        print(f"    {month:>11} {row['n']:>8.0f} {row['total_r']:>+9.1f}R  {bar}")

    negative = int((months["total_r"] < 0).sum())
    total = len(months)
    if negative >= total * 0.7:
        print(f"    -> {negative} of {total} months lost. That is not a bad patch; the rules")
        print("       are simply unprofitable in this market, month after month.")
    elif negative == 0:
        print("    -> Every month positive. Check this is not one long trend flattering")
        print("       a trend-following rule — section 4 and a second year will tell.")
    else:
        print(f"    -> {negative} of {total} months lost. Mixed months are usually a regime")
        print("       problem: the rules work in one kind of market and lose in another.")


def section_variants(tester_for, bars, m1) -> None:
    rule("6. EXIT-RULE GRID — scored separately in-sample and out-of-sample")
    print("    Break-even and trailing both protect open profit; the target decides")
    print("    how much profit there is to protect. They interact, so they are")
    print("    varied together rather than one at a time.")
    print()
    print(f"    {'break-even':>11} {'trail':>7} {'target':>7} "
          f"{'IS n':>6} {'IS exp':>9} {'OOS n':>6} {'OOS exp':>9}  verdict")
    print("    " + "-" * 76)

    rows = []
    for be in BREAKEVEN_VARIANTS:
        for trail in TRAIL_VARIANTS:
            for target in TARGET_VARIANTS:
                tester = tester_for(
                    risk={"max_consecutive_losses": NO_HALT},
                    strategy={
                        "breakeven_at_r": be,
                        "trail_after_r": trail,
                        "tp_r_multiple": target,
                    },
                )
                trades = tester.run(bars, m1=m1).closed
                trades.sort(key=lambda t: t.entry_time)
                cut = int(len(trades) * IN_SAMPLE_FRACTION)
                in_sample, out_sample = trades[:cut], trades[cut:]

                is_exp, oos_exp = expectancy(in_sample), expectancy(out_sample)
                both = is_exp > 0 and oos_exp > 0
                verdict = "BOTH POSITIVE" if both else ""
                rows.append((be, trail, target, is_exp, oos_exp, both))

                print(f"    {str(be) if be else 'off':>11} {str(trail) if trail else 'off':>7} "
                      f"{target:>6.1f}R {len(in_sample):>6} {is_exp:>+8.3f}R "
                      f"{len(out_sample):>6} {oos_exp:>+8.3f}R  {verdict}")

    survivors = [r for r in rows if r[5]]
    print()
    if not survivors:
        print("    -> NOTHING SURVIVED. No exit variant is positive in both halves.")
        print("       That is a real answer, and the useful one: the problem is not")
        print("       the exits, so tuning them further is wasted effort. Either the")
        print("       entry rules change, or this strategy does not trade.")
    else:
        print(f"    -> {len(survivors)} variant(s) positive in BOTH halves. That is a")
        print("       candidate, not a result. Before it means anything it has to")
        print("       hold on the other timeframe and on a second year of data —")
        print("       one instrument, one year, twelve variants is not enough")
        print("       evidence to risk money on.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--data", required=True, help="Parquet of trading-timeframe bars")
    parser.add_argument("--m1", default=None, help="Parquet of M1 bars for intrabar resolution")
    parser.add_argument("--balance", type=float, default=5000.0)
    parser.add_argument("--spread", type=float, default=25.0, help="Spread in points")
    parser.add_argument("--slippage", type=float, default=2.0)
    parser.add_argument("--commission", type=float, default=7.0)
    parser.add_argument(
        "--timeframe",
        default=None,
        help="Override strategy.timeframe so the indicator settings match the data file.",
    )
    parser.add_argument("--skip-grid", action="store_true", help="Skip section 6 (it is slow).")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.timeframe:
        cfg = with_strategy(cfg, timeframe=args.timeframe)
    bars = load_bars(Path(args.data))
    m1 = load_bars(Path(args.m1)) if args.m1 else None

    costs = Costs(
        spread_points=args.spread,
        slippage_points=args.slippage,
        commission_per_lot_round_turn=args.commission,
    )

    def tester_for(risk: dict | None = None, strategy: dict | None = None) -> Backtester:
        local = cfg
        if risk:
            local = with_risk(local, **risk)
        if strategy:
            local = local.model_copy(
                update={"strategy": local.strategy.model_copy(update=strategy)}
            )
        return Backtester(
            local, spec=default_gold_spec(), costs=costs, starting_balance=args.balance
        )

    print("=" * 76)
    print("  goldbot — diagnosis")
    print("=" * 76)
    print(f"  data      : {Path(args.data).name}  {len(bars):,} bars  "
          f"({bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d})")
    print(f"  timeframe : {cfg.strategy.timeframe}")
    print(f"  intrabar  : {'M1 resolved' if m1 is not None else 'NONE — ties scored as losses'}")
    print(f"  costs     : {args.spread:g}pt spread, {args.slippage:g}pt slippage, "
          f"{args.commission:g}/lot commission")

    unhalted = section_halt(tester_for, bars, m1)
    section_exits(unhalted)
    section_excursions(unhalted)
    section_direction(unhalted)
    section_by_period(unhalted)
    if not args.skip_grid:
        section_variants(tester_for, bars, m1)

    print()
    print("=" * 76)
    print("  Sections 2 and 3 say what is broken. Section 6 says whether the exits")
    print("  can fix it. If section 3 says the entries never went anywhere, believe")
    print("  it — no exit rule rescues an entry with no edge.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
