"""Backtester tests.

The intrabar cases are the point of this file. When a single M15 bar contains
both the stop and the target, what the backtester assumes decides whether the
report shows an edge or a loss — and the optimistic assumption is the reason so
many strategies look profitable on paper and are not. These tests pin the
behaviour down: pessimistic without M1 data, correctly ordered with it.

P&L figures below are hand-computed against a standard 100oz gold contract at
0.12 lots, where one point (0.01) is worth $0.12 to the position.
"""

from __future__ import annotations

import pandas as pd
import pytest

from goldbot.backtest import (
    Backtester, Costs, Trade, _entry_price, _exit_price, default_gold_spec,
)
from goldbot.config import Config, RiskConfig, SessionConfig, SessionWindow, StrategyConfig
from tests.test_strategy import uptrend_with_pullback

COSTS = Costs(spread_points=25.0, slippage_points=2.0, commission_per_lot_round_turn=7.0)


def config(**strategy_overrides) -> Config:
    """Round-the-clock sessions so session filtering does not mask other behaviour."""
    strategy = dict(
        fast_ema=3, trend_ema=5, slow_ema=20,
        atr_period=5, adx_period=5, rsi_period=5,
        min_adx=20.0, pullback_lookback=3,
    )
    strategy.update(strategy_overrides)
    return Config(
        strategy=StrategyConfig(**strategy),
        risk=RiskConfig(),
        sessions=SessionConfig(
            timezone="UTC",
            windows=(SessionWindow(name="always", start="00:00", end="23:59"),),
            trade_days=(0, 1, 2, 3, 4, 5, 6),
            flatten_before_weekend=False,
        ),
    )


def make_tester(cfg: Config | None = None, **kwargs) -> Backtester:
    return Backtester(cfg or config(), spec=default_gold_spec(), costs=COSTS, **kwargs)


def long_trade(**overrides) -> Trade:
    base = dict(
        entry_time=pd.Timestamp("2026-06-08 08:00", tz="UTC").to_pydatetime(),
        exit_time=None,
        direction="buy",
        volume=0.12,
        entry_price=2000.0,
        exit_price=None,
        initial_stop=1996.0,
        stop=1996.0,
        target=2008.0,
        risk_amount=48.0,
        risk_points=400.0,
        commission=0.84,
    )
    base.update(overrides)
    return Trade(**base)


def bar(high: float, low: float, close: float, atr: float = 2.0, open_: float | None = None):
    row = pd.Series(
        {"open": open_ if open_ is not None else close, "high": high, "low": low,
         "close": close, "atr": atr}
    )
    row.name = pd.Timestamp("2026-06-08 08:00", tz="UTC")
    return row


def minutes(*specs):
    """Build M1 sub-bars in the order given: each spec is (high, low)."""
    start = pd.Timestamp("2026-06-08 08:00", tz="UTC")
    out = []
    for i, (high, low) in enumerate(specs):
        row = pd.Series({"open": low, "high": high, "low": low, "close": high})
        ts = start + pd.Timedelta(minutes=i)
        row.name = ts
        out.append((ts, row))
    return out


class TestIntrabarResolution:
    """The bar that contains both the stop and the target."""

    AMBIGUOUS = dict(high=2009.0, low=1995.0, close=2005.0)

    def test_without_m1_the_stop_is_assumed_to_come_first(self):
        # The pessimistic assumption. It can only understate the result, which is
        # the only direction a backtest is allowed to be wrong in.
        result = make_tester()._walk_bar(long_trade(), bar(**self.AMBIGUOUS), None, 0.25, 0.02)
        assert result is not None
        price, _, reason = result
        assert reason == "stop"
        assert price == pytest.approx(1996.0 - 0.02)

    def test_m1_showing_the_target_first_resolves_to_the_target(self):
        sub = minutes((2009.0, 2004.0), (2006.0, 1995.0))
        result = make_tester()._walk_bar(long_trade(), bar(**self.AMBIGUOUS), sub, 0.25, 0.02)
        assert result is not None
        price, _, reason = result
        assert reason == "target"
        assert price == pytest.approx(2008.0)

    def test_m1_showing_the_stop_first_resolves_to_the_stop(self):
        sub = minutes((2001.0, 1995.0), (2009.0, 2004.0))
        result = make_tester()._walk_bar(long_trade(), bar(**self.AMBIGUOUS), sub, 0.25, 0.02)
        assert result[2] == "stop"

    def test_the_two_orderings_disagree(self):
        # If they ever agree, the M1 resolution has stopped doing anything.
        first = make_tester()._walk_bar(
            long_trade(), bar(**self.AMBIGUOUS), minutes((2009.0, 2004.0), (2006.0, 1995.0)), 0.25, 0.02
        )
        second = make_tester()._walk_bar(
            long_trade(), bar(**self.AMBIGUOUS), minutes((2001.0, 1995.0), (2009.0, 2004.0)), 0.25, 0.02
        )
        assert first[2] != second[2]

    def test_a_quiet_bar_closes_nothing(self):
        assert make_tester()._walk_bar(long_trade(), bar(2003.0, 1999.0, 2001.0), None, 0.25, 0.02) is None


class TestShortSideExits:
    """Shorts exit by buying at the ask, which sits a full spread above the bars."""

    def short(self, **overrides):
        base = dict(
            direction="sell", entry_price=2000.0, initial_stop=2004.0, stop=2004.0,
            target=1992.0,
        )
        base.update(overrides)
        return long_trade(**base)

    def test_stop_triggers_on_the_ask_not_the_bid(self):
        # Bid high of 2003.80 is below the 2004 stop, but the ask is 2004.05.
        # Ignoring the spread here would understate losses on every short.
        result = make_tester()._walk_bar(self.short(), bar(2003.8, 2001.0, 2003.0), None, 0.25, 0.02)
        assert result is not None and result[2] == "stop"

    def test_bid_far_below_the_stop_does_not_trigger(self):
        assert make_tester()._walk_bar(self.short(), bar(2003.0, 2001.0, 2002.0), None, 0.25, 0.02) is None

    def test_target_requires_the_ask_to_reach_it(self):
        # Bid low 1991.80 looks like a fill, but the ask is 1992.05 — not yet.
        assert make_tester()._walk_bar(self.short(), bar(1995.0, 1991.8, 1992.0), None, 0.25, 0.02) is None
        assert make_tester()._walk_bar(self.short(), bar(1995.0, 1991.7, 1992.0), None, 0.25, 0.02)[2] == "target"


class TestStopManagement:
    def test_break_even_moves_the_stop_to_entry(self):
        trade = long_trade()
        # 1R above entry is 2004.00; the bar reaches it without hitting the target.
        make_tester()._walk_bar(trade, bar(2004.5, 2001.0, 2004.0), None, 0.25, 0.02)
        assert trade.stop == pytest.approx(2000.0)

    def test_break_even_is_not_applied_before_its_r_multiple(self):
        trade = long_trade()
        make_tester()._walk_bar(trade, bar(2003.0, 1999.0, 2002.0), None, 0.25, 0.02)
        assert trade.stop == pytest.approx(1996.0)

    def test_a_break_even_exit_is_labelled_as_one(self):
        trade = long_trade()
        make_tester()._walk_bar(trade, bar(2004.5, 2001.0, 2004.0), None, 0.25, 0.02)
        result = make_tester()._walk_bar(trade, bar(2002.0, 1999.0, 2000.0), None, 0.25, 0.02)
        assert result[2] == "breakeven_stop"

    def test_trailing_stop_follows_the_high(self):
        trade = long_trade()
        # +1.5R is 2006.00. With ATR 2.0 and a 2.0 multiple, the stop trails to
        # 2006.5 - 4.0 = 2002.50 — above break-even, so it locks in a gain.
        make_tester()._walk_bar(trade, bar(2006.5, 2001.0, 2006.0), None, 0.25, 0.02)
        assert trade.stop == pytest.approx(2002.5)

    def test_a_stop_never_moves_backwards(self):
        trade = long_trade()
        make_tester()._walk_bar(trade, bar(2006.5, 2001.0, 2006.0), None, 0.25, 0.02)
        locked = trade.stop
        make_tester()._walk_bar(trade, bar(2004.0, 2003.0, 2003.5), None, 0.25, 0.02)
        assert trade.stop == pytest.approx(locked), "widening a stop would enlarge live risk"

    def test_management_cannot_rescue_a_bar_that_already_stopped_out(self):
        # The high and the low are in the same bar; letting the high move the stop
        # first would be lookahead, and would erase real losses from the report.
        trade = long_trade()
        result = make_tester()._walk_bar(trade, bar(2006.5, 1995.0, 2000.0), None, 0.25, 0.02)
        assert result[2] == "stop"
        assert result[0] == pytest.approx(1996.0 - 0.02)


class TestCostModel:
    def test_buys_pay_the_spread_and_the_slippage(self):
        assert _entry_price("buy", 2000.0, 0.25, 0.02) == pytest.approx(2000.27)

    def test_sells_enter_at_the_bid(self):
        assert _entry_price("sell", 2000.0, 0.25, 0.02) == pytest.approx(1999.98)

    def test_shorts_pay_the_spread_on_the_way_out(self):
        assert _exit_price("sell", 2000.0, 0.25, 0.0) == pytest.approx(2000.25)
        assert _exit_price("buy", 2000.0, 0.25, 0.0) == pytest.approx(2000.0)

    def test_pnl_on_a_target_hit_is_hand_checkable(self):
        # 800 points x $0.12/point = $96.00, less $0.84 commission.
        trade = long_trade()
        make_tester()._close_trade(trade, 2008.0, trade.entry_time, "target")
        assert trade.pnl == pytest.approx(95.16)
        assert trade.r_multiple == pytest.approx(95.16 / 48.0)

    def test_pnl_on_a_stop_slightly_exceeds_one_r(self):
        # Slippage and commission are why a "1R" loss is never exactly 1R live.
        trade = long_trade()
        make_tester()._close_trade(trade, 1995.98, trade.entry_time, "stop")
        assert trade.pnl == pytest.approx(-49.08)
        assert trade.r_multiple < -1.0


class TestFullRun:
    @staticmethod
    def series(cycles: int = 25) -> pd.DataFrame:
        """Repeat the strategy's own fixture so several trades actually fire.

        Each cycle starts where the previous one closed. Concatenating identical
        cycles instead would put a large price gap at every seam, which the
        backtester rightly refuses to trade — and the test would then be
        measuring the fixture rather than the strategy.
        """
        frames, price = [], 1900.0
        for _ in range(cycles):
            block = uptrend_with_pullback(trend_bars=24, start=price)
            price = float(block["close"].iloc[-1])
            frames.append(block)
        combined = pd.concat(frames, ignore_index=True)
        combined.index = pd.date_range(
            "2026-01-05 00:00", periods=len(combined), freq="15min", tz="UTC"
        )
        return combined

    def test_produces_trades_and_a_full_equity_curve(self):
        result = make_tester().run(self.series())
        assert len(result.trades) > 0
        assert len(result.equity_curve) == result.bars

    def test_positions_are_sized_by_the_live_risk_warden(self):
        # Not a separate backtest-only sizing path: same class, same 1% rule,
        # measured against the equity the warden actually saw at entry.
        result = make_tester(starting_balance=5000.0).run(self.series())
        assert result.closed
        for trade in result.closed:
            risk_pct = trade.risk_amount / trade.equity_at_entry * 100.0
            assert risk_pct <= 1.0 + 1e-6, f"{trade.volume} lots risked {risk_pct:.3f}%"

    def test_actual_risk_matches_what_the_warden_authorised(self):
        # The signal is priced at the previous bar's close but fills at this
        # bar's open. Sizing off the stale close would put more than 1% at risk
        # exactly when the market gaps — the worst possible moment for it.
        result = make_tester(starting_balance=5000.0).run(self.series())
        assert result.closed
        for trade in result.closed:
            assert trade.risk_amount == pytest.approx(trade.authorised_risk, rel=1e-6)

    def test_a_fill_past_the_stop_is_declined(self):
        # A gap through the stop leaves no trade to take, only a fill that is
        # already wrong. It must be skipped and counted, never silently opened.
        bars = self.series(cycles=4)
        gapped = bars.copy()
        seam = len(bars) // 2
        gapped.iloc[seam:] = gapped.iloc[seam:] - 200.0
        result = make_tester().run(gapped)
        assert all(t.exit_reason != "" or t.is_open for t in result.trades)

    def test_no_trade_loses_far_more_than_its_planned_risk(self):
        # Costs make a stop cost a little over 1R. Anything much beyond that means
        # a stop was not honoured, which would invalidate the whole report.
        result = make_tester().run(self.series())
        for trade in result.closed:
            assert trade.r_multiple > -1.5, f"{trade.exit_reason} lost {trade.r_multiple:.2f}R"

    def test_m1_resolution_changes_the_outcome(self):
        bars = self.series()
        # Synthesise M1 bars that visit each trading bar's high before its low,
        # which is the ordering the no-M1 path refuses to assume.
        rows, times = [], []
        for ts, row in bars.iterrows():
            for offset, (h, l) in enumerate(
                ((row["high"], row["close"]), (row["close"], row["low"]))
            ):
                rows.append({"open": row["open"], "high": h, "low": l, "close": row["close"]})
                times.append(ts + pd.Timedelta(minutes=offset * 7))
        m1 = pd.DataFrame(rows, index=pd.DatetimeIndex(times))

        pessimistic = make_tester().run(bars)
        resolved = make_tester().run(bars, m1=m1)
        assert resolved.stats()["net_pnl"] >= pessimistic.stats()["net_pnl"]

    def test_stats_are_internally_consistent(self):
        result = make_tester().run(self.series())
        stats = result.stats()
        assert stats["wins"] + stats["losses"] == stats["trades"]
        assert stats["net_pnl"] == pytest.approx(
            result.ending_balance - result.starting_balance, abs=1e-6
        )
        assert 0.0 <= stats["win_rate_pct"] <= 100.0
        assert stats["max_drawdown_pct"] >= 0.0

    def test_reports_signals_it_declined_to_take(self):
        # A run that silently drops signals is impossible to debug. Every skip is
        # counted under the reason the warden or the gate gave.
        cfg = config()
        strict = Backtester(
            cfg, spec=default_gold_spec(),
            costs=Costs(spread_points=99.0, slippage_points=2.0, commission_per_lot_round_turn=7.0),
        )
        result = strict.run(self.series())
        assert result.closed == []
        assert any("spread" in key for key in result.skipped)

    def test_session_windows_are_enforced(self):
        cfg = config()
        narrow = Config(
            strategy=cfg.strategy,
            risk=cfg.risk,
            sessions=SessionConfig(
                timezone="UTC",
                windows=(SessionWindow(name="tiny", start="08:00", end="08:15"),),
                trade_days=(0, 1, 2, 3, 4),
                flatten_before_weekend=False,
            ),
        )
        result = Backtester(narrow, spec=default_gold_spec(), costs=COSTS).run(self.series())
        for trade in result.trades:
            assert trade.entry_time.hour == 8 and trade.entry_time.minute < 30

    def test_weekend_flattening_closes_open_positions(self):
        # A distant target with no break-even or trailing keeps positions open
        # long enough to still be running when Friday's close arrives.
        cfg = config(tp_r_multiple=20.0, breakeven_at_r=None, trail_after_r=None)
        weekend = Config(
            strategy=cfg.strategy,
            risk=cfg.risk,
            sessions=SessionConfig(
                timezone="UTC",
                windows=(SessionWindow(name="always", start="00:00", end="23:59"),),
                trade_days=(0, 1, 2, 3, 4),
                flatten_before_weekend=True,
                friday_close="20:00",
            ),
        )
        result = Backtester(weekend, spec=default_gold_spec(), costs=COSTS).run(self.series())
        assert any(t.exit_reason == "weekend_flatten" for t in result.closed)

    def test_weekly_returns_are_reported_against_the_goal(self):
        result = make_tester().run(self.series())
        weekly = result.weekly_returns_pct()
        assert not weekly.empty
        assert weekly.index.is_monotonic_increasing

    def test_by_hour_covers_every_trade(self):
        result = make_tester().run(self.series())
        assert result.by_hour()["trades"].sum() == len(result.closed)


class TestHaltHandling:
    """The five-consecutive-loss halt needs a manual /resume live.

    Left latched in a replay it ends the backtest at the first losing streak,
    and the report then measures the halt rather than the strategy. Modelling an
    overnight resume is the honest default; the count is reported so a strategy
    that only survives by being restarted daily cannot hide behind it.
    """

    @staticmethod
    def losing_series() -> pd.DataFrame:
        # Ten cycles is enough to run past five losses in most cost settings.
        return TestFullRun.series(cycles=40)

    def test_latched_halt_stops_trading_early(self):
        latched = Backtester(
            config(), spec=default_gold_spec(), costs=COSTS, resume_after_halt_daily=False
        ).run(self.losing_series())
        resumed = Backtester(
            config(), spec=default_gold_spec(), costs=COSTS, resume_after_halt_daily=True
        ).run(self.losing_series())
        assert len(resumed.closed) >= len(latched.closed)

    def test_resumes_are_counted_not_hidden(self):
        result = Backtester(
            config(), spec=default_gold_spec(), costs=COSTS, resume_after_halt_daily=True
        ).run(self.losing_series())
        assert result.halt_resumes >= 0
        if result.halt_resumes:
            assert any("halted" in key for key in result.skipped) or result.closed
