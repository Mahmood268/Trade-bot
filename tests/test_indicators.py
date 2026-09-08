"""Indicator tests against hand-computed values.

Every expected number below was worked out on paper from Wilder's definitions
before the code was run. Asserting against whatever the implementation happens
to produce would only prove it is self-consistent — which is not the property
that matters when an ATR value sets a real stop.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot import indicators


def frame(rows):
    """rows: (open, high, low, close) tuples on a 1-minute index."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


class TestEMA:
    def test_matches_hand_computed_recursion(self):
        # alpha = 2/(3+1) = 0.5, seeded with the first value:
        #   e0 = 1
        #   e1 = 1    + 0.5 x (2 - 1)    = 1.5
        #   e2 = 1.5  + 0.5 x (3 - 1.5)  = 2.25
        #   e3 = 2.25 + 0.5 x (4 - 2.25) = 3.125
        #   e4 = 3.125+ 0.5 x (5 - 3.125)= 4.0625
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = indicators.ema(series, 3)
        assert out.iloc[2] == pytest.approx(2.25)
        assert out.iloc[3] == pytest.approx(3.125)
        assert out.iloc[4] == pytest.approx(4.0625)

    def test_warmup_is_nan_not_a_partial_estimate(self):
        out = indicators.ema(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
        assert out.iloc[:2].isna().all()

    def test_flat_series_equals_its_own_level(self):
        out = indicators.ema(pd.Series([2000.0] * 10), 5)
        assert out.dropna().eq(2000.0).all()


class TestATR:
    #  bar |  high   low   close |  true range
    #   0  | 10.0    8.0    9.0  |  2.0   (no previous close: high-low)
    #   1  | 11.0    9.0   10.5  |  2.0
    #   2  | 12.0   10.5   11.8  |  1.5
    #   3  | 12.5   11.0   11.2  |  1.5
    #   4  | 13.0   11.0   12.9  |  2.0
    BARS = [
        (9.0, 10.0, 8.0, 9.0),
        (9.0, 11.0, 9.0, 10.5),
        (10.5, 12.0, 10.5, 11.8),
        (11.8, 12.5, 11.0, 11.2),
        (11.2, 13.0, 11.0, 12.9),
    ]

    def test_true_range_uses_previous_close(self):
        tr = indicators.true_range(frame(self.BARS))
        assert list(tr) == pytest.approx([2.0, 2.0, 1.5, 1.5, 2.0])

    def test_atr_seeds_with_mean_then_smooths(self):
        # seed  = mean(2.0, 2.0, 1.5)               = 1.833333
        # bar 3 = 1.833333 + (1.5 - 1.833333)/3     = 1.722222
        # bar 4 = 1.722222 + (2.0 - 1.722222)/3     = 1.814815
        out = indicators.atr(frame(self.BARS), period=3)
        assert out.iloc[:2].isna().all()
        assert out.iloc[2] == pytest.approx(1.833333, abs=1e-6)
        assert out.iloc[3] == pytest.approx(1.722222, abs=1e-6)
        assert out.iloc[4] == pytest.approx(1.814815, abs=1e-6)

    def test_is_not_an_ema_of_true_range(self):
        # A common bug: substituting ewm(span=period) for Wilder's smoothing.
        # It converges to a different number, and every ATR stop moves with it.
        bars = frame(self.BARS)
        wilder = indicators.atr(bars, period=3).iloc[4]
        ema_version = indicators.true_range(bars).ewm(span=3, adjust=False).mean().iloc[4]
        assert wilder != pytest.approx(ema_version, abs=1e-6)


class TestRSI:
    # closes 10 -> 11 -> 12 -> 11 -> 13, period 2
    #   gains  : -, 1, 1, 0, 2      losses: -, 0, 0, 1, 0
    #   bar 2  : avg_gain 1.0,  avg_loss 0.0   -> RSI 100 (no downside at all)
    #   bar 3  : avg_gain 0.5,  avg_loss 0.5   -> RS 1     -> RSI 50
    #   bar 4  : avg_gain 1.25, avg_loss 0.25  -> RS 5     -> RSI 83.333
    CLOSES = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0])

    def test_hand_computed_values(self):
        out = indicators.rsi(self.CLOSES, period=2)
        assert out.iloc[2] == pytest.approx(100.0)
        assert out.iloc[3] == pytest.approx(50.0)
        assert out.iloc[4] == pytest.approx(83.333333, abs=1e-6)

    def test_unbroken_advance_is_100_not_nan(self):
        # avg_loss of zero divides by zero; the definition says 100, and a NaN
        # here would silently block every signal in a strong trend.
        out = indicators.rsi(pd.Series([10.0, 11.0, 12.0, 13.0, 14.0]), period=2)
        assert out.iloc[-1] == pytest.approx(100.0)

    def test_warmup_is_nan(self):
        out = indicators.rsi(self.CLOSES, period=2)
        assert out.iloc[:2].isna().all()


class TestADX:
    @staticmethod
    def trending(n=120, step=1.0):
        rows = []
        price = 1900.0
        for _ in range(n):
            rows.append((price, price + step, price - step * 0.1, price + step * 0.9))
            price += step
        return frame(rows)

    @staticmethod
    def choppy(n=120):
        rows = []
        base = 1900.0
        for i in range(n):
            price = base + (2.0 if i % 2 else 0.0)
            rows.append((price, price + 1.0, price - 1.0, price))
        return frame(rows)

    def test_trend_scores_high_and_chop_scores_low(self):
        trend = indicators.adx(self.trending(), 14)["adx"].iloc[-1]
        chop = indicators.adx(self.choppy(), 14)["adx"].iloc[-1]
        assert trend > 40, f"a clean one-way trend should read as trending, got {trend}"
        assert chop < 20, f"an alternating series should read as rangebound, got {chop}"
        assert trend > chop

    def test_directional_indicators_agree_with_direction(self):
        up = indicators.adx(self.trending(step=1.0), 14)
        assert up["plus_di"].iloc[-1] > up["minus_di"].iloc[-1]

    def test_needs_roughly_two_periods_of_warmup(self):
        # ADX smooths DX, which is itself smoothed: ~2 x period before a value.
        out = indicators.adx(self.trending(n=40), 14)["adx"]
        assert out.iloc[:26].isna().all()
        assert out.iloc[-1] == out.iloc[-1]  # not NaN


class TestCompute:
    def test_rejects_missing_columns(self):
        bad = pd.DataFrame({"close": [1.0, 2.0]})
        with pytest.raises(ValueError, match="missing required column"):
            indicators.compute(bad, _cfg())

    def test_rejects_unsorted_bars(self):
        bars = TestADX.trending(30).iloc[::-1]
        with pytest.raises(ValueError, match="oldest-first"):
            indicators.compute(bars, _cfg())

    def test_attaches_every_column_the_strategy_reads(self):
        out = indicators.compute(TestADX.trending(150), _cfg())
        for col in ("ema_fast", "ema_trend", "ema_slow", "atr", "rsi", "adx", "plus_di"):
            assert col in out.columns
        assert not out["ema_slow"].dropna().empty

    def test_does_not_mutate_the_input(self):
        bars = TestADX.trending(60)
        before = list(bars.columns)
        indicators.compute(bars, _cfg())
        assert list(bars.columns) == before

    def test_warmup_covers_the_slowest_indicator(self):
        cfg = _cfg()
        assert indicators.warmup_bars(cfg) >= 3 * cfg.slow_ema


def _cfg():
    from goldbot.config import StrategyConfig

    return StrategyConfig(fast_ema=3, trend_ema=5, slow_ema=20, atr_period=5, adx_period=5, rsi_period=5)
