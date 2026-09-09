# The strategy, and how to judge it

This document describes what the bot actually does when it decides to trade, and
how to read a backtest without fooling yourself. It covers the deterministic
core only — the Claude agents can veto and shrink these trades, but they never
create one.

---

## The rule: trend pullback

The premise, stated so it can be proven wrong: **gold trends within a session,
and the better entry in a trend is the first pullback into it, taken when
momentum turns back the trend's way.** Whether that is true enough to pay the
spread is what the backtest is for. Nothing in the code assumes it is.

A long needs all five conditions on the same closed bar. Shorts mirror them.

| # | Condition | Why it is there |
|---|---|---|
| 1 | `EMA20 > EMA50 > EMA200` | All three aligned, not just price above one average. A stack that has not formed is not a trend. |
| 2 | `ADX >= min_adx` (default 20) | The range filter. This setup bleeds in a chop, where price crosses the fast EMA constantly and every crossing looks like a pullback. |
| 3 | Price touched EMA20 within the last `pullback_lookback` bars | Without it the rule buys any strong bar in an uptrend. That is chasing, and it puts the stop far away. |
| 4 | The bar closes back above EMA20, and closes up | The pullback is over, not still going. |
| 5 | `50 <= RSI < 70` | Momentum, not exhaustion. Above the ceiling the move is extended and the structural stop is furthest from entry — the worst reward-to-risk the setup offers. |

Every parameter above lives in `config/config.yaml` under `strategy:`. None of
them are magic; they are starting points to be tested, not settings to trust.

### Where the stop goes

Two candidates, and the **further** one wins:

* the ATR stop — `atr_stop_multiple x ATR(14)` from the close, default 1.5x
* the structural stop — just beyond the pullback swing, padded by 0.1 ATR

A stop inside the swing the setup is built on gets taken out by the same noise
that formed the swing. Using the further of the two means a wider stop and
therefore a *smaller* position for the same 1% risk — that is the correct
trade-off, not a cost.

The target is a fixed multiple of whatever the stop distance turned out to be
(`tp_r_multiple`, default 2R), so reward-to-risk is constant regardless of how
wide the stop had to be.

### Managing the position

* **Break-even** at +1R: the stop moves to entry. Costs a share of the winners.
* **Trailing** after +1.5R: the stop follows at 2x ATR behind the high.
* A stop **never widens**. The executor and the backtester both enforce it, and
  no agent can request it.

Break-even and trailing are the reason a losing trade in the backtest averages
around −0.8R rather than a clean −1R. They are also why some winners end early.
The journal labels stop exits as `stop`, `breakeven_stop` or `trailing_stop`
precisely so this trade-off is measurable: a system whose winners mostly end as
`breakeven_stop` is protecting itself out of its own edge.

---

## Sizing: one place, one rule

`goldbot/risk.py` is the only code in the system that turns a stop distance into
a position size:

```
lots = risk_amount / (stop_points x value_per_point_per_lot)
```

Worked through for the demo account this is built for:

| Quantity | Value |
|---|---|
| Equity | $5,000 |
| Risk per trade | 1% = **$50.00** |
| Stop distance | $4.00 = 400 points |
| Point value (1.00 lot) | $1.00 |
| Raw size | 50 / (400 x 1) = 0.125 lots |
| **Floored to the lot step** | **0.12 lots** |
| **Actual risk** | **$48.00** (0.96% of equity) |

Lots are always **floored**, never rounded. Rounding 0.125 up to 0.13 would risk
$52 against a $50 budget, and over-risking by rounding is still over-risking.

The backtester calls this same class. There is no separate backtest sizing path,
because a backtest that sizes differently from the bot is measuring a system
nobody is going to trade.

### Sizing happens at the fill price, not the signal price

The signal is generated on a bar's close; the fill happens at the next bar's
open, somewhere else. The stop does not move to follow it. Sizing off the stale
close would put more than 1% at risk exactly when the market gaps — the worst
possible moment for it. So the warden is given the expected fill price, and two
guards apply:

* a fill **past the stop or the target** is not a trade, it is a fill that is
  already wrong — skipped, and counted in the report
* a fill that drags reward-to-risk **below 1:1** is not the trade the strategy
  proposed — also skipped

---

## Choosing between M5 and M15

Both are supported and both are tested. Which one to trade is a question for
your data, not for preference:

```bash
python scripts/compare_timeframes.py --m5 data/xauusd_m5.parquet \
                                     --m15 data/xauusd_m15.parquet \
                                     --m1 data/xauusd_m1.parquet
```

`export_history.py` pulls both timeframes by default, so this needs no second
trip to the Windows machine.

The tradeoff is structural. M5 produces roughly three times the signals, so more
chances to be right. But M5's ATR is smaller, so its stops are tighter, and the
*same* spread then consumes a far larger share of each trade's risk:

| | M15 | M5 |
|---|---|---|
| Typical stop | ~400 points | ~150 points |
| 25-point spread as a share of risk | 6% | 17% |

So M5 wins on opportunity and loses on cost, and the exchange rate between them
is set entirely by your broker's spread. That is why the comparison sweeps
several spread values rather than using one. A timeframe that only works at 15
points is not one you can trade on an account that sees 30 through a news
release.

Two things to watch in the output:

* **Three times the signals is not three times the trades.** The `mostly blocked
  by` column shows why. On a losing run, M5 hits the five-consecutive-loss halt
  far more often, so many of its extra signals arrive while trading is stopped.
  A timeframe that generates more opportunities and converts fewer of them is
  telling you about its hit rate, not its throughput.
* **`min_stop_points` bites M5 first.** The default 100-point floor is 1.00 in
  gold. M15 stops clear it comfortably; M5 stops sit much closer to it, so
  raising it excludes M5 setups disproportionately.

One thing held constant across both runs, worth knowing: the EMA and ATR periods
are *bar counts*, not clock times, so on M5 they look back a third as far in
wall-clock terms. That is the honest default for an apples-to-apples comparison.
Tuning periods per timeframe is a separate question, and one to answer only
after this one.

---

## Reading a backtest honestly

Run it:

```bash
python scripts/run_backtest.py --data data/xauusd_m15.parquet \
                               --m1 data/xauusd_m1.parquet
```

**Always pass `--m1`.** When one M15 bar contains both the stop and the target,
the M15 bar alone cannot say which came first. Without the M1 file the
backtester assumes the stop hit — pessimistic on purpose, because the optimistic
assumption is the single most common way a backtest shows profits that never
appear live. With the M1 file it resolves the order properly.

### What the numbers mean

| Metric | How to read it |
|---|---|
| **Expectancy (R)** | The number that matters. Average result per trade in units of risk. Below zero the system loses money by design; costs alone put it there. |
| **Profit factor** | Gross win / gross loss. Under 1.0 is losing. Over 2.0 on a small sample usually means a small sample. |
| **Win rate** | Nearly meaningless alone. A 2R target makes ~35% winners perfectly healthy. |
| **Max drawdown** | What you will actually have to sit through. Assume the live version is worse. |
| **By entry hour** | Usually the most actionable table. A strategy that makes its money in two hours and gives it back in six is a schedule problem, not a strategy problem. |
| **Signals not taken** | Where the filters bite. A run dominated by `session` skips means the strategy fires mostly outside your trading hours. |
| **Halts resumed** | Five-loss halts. Live, each needs a manual `/resume`. A strategy that only survives by being restarted daily is not a strategy. |

### What it cannot tell you

* **Nothing about the agents.** They react to news that no historical bar
  records. Their contribution is measured forward, on demo, by journalling what
  would have happened both ways for every veto.
* **Nothing about your broker's real costs.** Spread and commission are
  assumptions. Get the spread from `check_connection.py` run during your real
  trading hours — not an overnight reading, when gold's spread is widest.
* **Nothing about a regime it never saw.** A year of data is a handful of
  independent market conditions, not a hundred.

### Before trusting any result

```bash
python scripts/check_no_lookahead.py
```

This replays the strategy over several independent random walks. Random data
contains no edge, so the result must be near zero or, after costs, negative. A
consistent positive edge there means the backtester is reading bars it should
not be able to see, and every other number in the report is meaningless.

At the time of writing it reports a mean expectancy of about **−0.10R** across
six seeds — the correct result. It proves the backtest is not cheating. It does
not prove the strategy works.

---

## The honest position

A profitable backtest is a necessary condition, not a sufficient one. The order
of operations is: backtest, then dry run, then demo for weeks, then a
conversation about whether any of it justifies real money. If the backtest shows
no edge, the right outcome is to change the strategy or not trade it — not to
loosen the filters until the curve points up.
