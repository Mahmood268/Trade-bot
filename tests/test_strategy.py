"""Strategy tests on synthetic bars.

The fixtures build price action with a known shape — a trend, a pullback into
the fast EMA, a resumption bar — and then assert the rule fires, or does not,
for the stated reason. Each "no signal" test disables exactly one condition, so
a failure names the rule that broke rather than just saying the signal vanished.

The short cases mirror the long series around a fixed price and swap high/low.
That makes the symmetry provable instead of hand-tuned: if longs and shorts ever
stop behaving as mirror images, the direction handling has a bug.
"""

from __future__ import annotations

import pandas as pd
import pytest

from goldbot import indicators
from goldbot.config import StrategyConfig
from goldbot.strategy import Signal, TrendPullback
from goldbot.strategy.trend_pullback import SWING_BUFFER_ATR

MIRROR_AXIS = 3800.0


def cfg(**overrides) -> StrategyConfig:
    """Short periods so a fixture needs ~85 bars rather than ~600."""
    base = dict(
        fast_ema=3, trend_ema=5, slow_ema=20,
        atr_period=5, adx_period=5, rsi_period=5,
        min_adx=20.0, pullback_lookback=3,
    )
    base.update(overrides)
    return StrategyConfig(**base)


def uptrend_with_pullback(
    trend_bars: int = 80,
    pull_bars: int = 2,
    pull_size: float = 1.2,
    resume: float = 2.0,
    pull_wick: float = 0.2,
    start: float = 1900.0,
) -> pd.DataFrame:
    """A rising market that dips into the fast EMA and closes back above it.

    The climb includes a down bar every fourth bar. A perfectly unbroken advance
    pins RSI at 100, which the strategy correctly refuses to buy — so a fixture
    without retracements would test nothing but the exhaustion filter.
    """
    rows: list[tuple[float, float, float, float]] = []
    price = start
    for i in range(trend_bars):
        step = -1.0 if i % 4 == 3 else 1.5
        rows.append((price, price + max(step, 0) + 0.3, price + min(step, 0) - 0.3, price + step))
        price += step
    for _ in range(pull_bars):
        rows.append((price, price + 0.2, price - pull_size - pull_wick, price - pull_size))
        price -= pull_size
    rows.append((price, price + resume + 0.2, price - 0.2, price + resume))

    idx = pd.date_range("2026-06-08 07:00", periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def mirrored(bars: pd.DataFrame) -> pd.DataFrame:
    """Reflect a series about a fixed price, turning every long setup into a short."""
    out = pd.DataFrame(
        {
            "open": MIRROR_AXIS - bars["open"],
            "high": MIRROR_AXIS - bars["low"],
            "low": MIRROR_AXIS - bars["high"],
            "close": MIRROR_AXIS - bars["close"],
        },
        index=bars.index,
    )
    return out


def last_signal(bars: pd.DataFrame, strategy_cfg: StrategyConfig) -> Signal | None:
    data = indicators.compute(bars, strategy_cfg)
    return TrendPullback(strategy_cfg).evaluate(data, len(data) - 1)


class TestLongSignal:
    def test_fires_on_a_textbook_pullback(self):
        signal = last_signal(uptrend_with_pullback(), cfg())
        assert signal is not None
        assert signal.direction == "buy"
        assert signal.stop < signal.reference_price < signal.target

    def test_target_is_the_configured_r_multiple(self):
        signal = last_signal(uptrend_with_pullback(), cfg(tp_r_multiple=2.0))
        assert signal.reward_risk == pytest.approx(2.0)

        signal3 = last_signal(uptrend_with_pullback(), cfg(tp_r_multiple=3.0))
        assert signal3.reward_risk == pytest.approx(3.0)
        assert signal3.stop == pytest.approx(signal.stop), "the stop must not move with the target"

    def test_records_why_it_fired(self):
        signal = last_signal(uptrend_with_pullback(), cfg())
        assert "EMA stack aligned" in signal.reason
        assert set(signal.context) >= {"ema_fast", "ema_trend", "ema_slow", "atr", "adx", "rsi"}


class TestShortSignal:
    def test_mirrors_the_long_case_exactly(self):
        long_signal = last_signal(uptrend_with_pullback(), cfg())
        short_signal = last_signal(mirrored(uptrend_with_pullback()), cfg())

        assert short_signal is not None
        assert short_signal.direction == "sell"
        assert short_signal.reference_price == pytest.approx(
            MIRROR_AXIS - long_signal.reference_price
        )
        assert short_signal.stop == pytest.approx(MIRROR_AXIS - long_signal.stop)
        assert short_signal.target == pytest.approx(MIRROR_AXIS - long_signal.target)
        assert short_signal.atr == pytest.approx(long_signal.atr)


class TestFilters:
    def test_adx_below_the_minimum_blocks_the_trade(self):
        # Same bars, one filter raised out of reach: no trade in a range.
        assert last_signal(uptrend_with_pullback(), cfg(min_adx=99.0)) is None

    def test_overbought_momentum_blocks_the_trade(self):
        # RSI on the fixture is ~64. Dropping the ceiling below that must veto it:
        # a stretched move is where the structural stop is furthest away.
        assert last_signal(uptrend_with_pullback(), cfg(rsi_extreme=55.0)) is None

    def test_weak_momentum_blocks_the_trade(self):
        assert last_signal(uptrend_with_pullback(), cfg(rsi_midline=70.0, rsi_extreme=80.0)) is None

    def test_no_pullback_means_no_entry(self):
        # A steady climb with no dip: price never returns to the fast EMA, so
        # there is nothing to buy into. Buying here would be chasing.
        bars = uptrend_with_pullback(pull_bars=0, resume=1.5)
        assert last_signal(bars, cfg(pullback_lookback=2)) is None

    def test_unaligned_emas_block_the_trade(self):
        # A flat, alternating market: the EMAs interleave and the stack never forms.
        rows, base = [], 1900.0
        for i in range(90):
            price = base + (3.0 if i % 2 else 0.0)
            rows.append((price, price + 1.0, price - 1.0, price))
        idx = pd.date_range("2026-06-08 07:00", periods=len(rows), freq="15min", tz="UTC")
        bars = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
        assert last_signal(bars, cfg()) is None

    def test_silent_during_indicator_warmup(self):
        data = indicators.compute(uptrend_with_pullback(), cfg())
        strategy = TrendPullback(cfg())
        assert all(strategy.evaluate(data, i) is None for i in range(0, 15))


class TestStops:
    def test_stop_sits_beyond_the_pullback_swing(self):
        # A deep wick on the pullback: the ATR stop would sit inside the swing
        # the setup is built on, so the structural stop must take over.
        bars = uptrend_with_pullback(pull_wick=5.0)
        signal = last_signal(bars, cfg())
        assert signal is not None

        window = bars.iloc[-3:]
        swing_low = float(window["low"].min())
        assert signal.stop < swing_low, "stop must sit below the swing, not inside it"
        assert signal.stop == pytest.approx(swing_low - SWING_BUFFER_ATR * signal.atr)

    def test_atr_stop_used_when_it_is_the_further_of_the_two(self):
        signal = last_signal(uptrend_with_pullback(), cfg(atr_stop_multiple=5.0))
        assert signal.stop == pytest.approx(
            signal.reference_price - 5.0 * signal.atr
        )

    def test_structural_stop_can_be_disabled(self):
        bars = uptrend_with_pullback(pull_wick=5.0)
        signal = last_signal(bars, cfg(use_structural_stop=False))
        assert signal.stop == pytest.approx(signal.reference_price - 1.5 * signal.atr)

    def test_wider_atr_multiple_widens_the_stop(self):
        tight = last_signal(uptrend_with_pullback(), cfg(atr_stop_multiple=3.0))
        wide = last_signal(uptrend_with_pullback(), cfg(atr_stop_multiple=6.0))
        assert wide.stop_distance > tight.stop_distance


class TestNoLookahead:
    def test_evaluation_ignores_bars_after_the_signal_bar(self):
        # The single most damaging bug class in backtesting. Truncating the frame
        # at the signal bar must change nothing about the signal.
        bars = uptrend_with_pullback()
        full = indicators.compute(bars, cfg())
        i = len(full) - 1
        strategy = TrendPullback(cfg())

        from_full = strategy.evaluate(full, i)
        truncated = indicators.compute(bars.iloc[: i + 1], cfg())
        from_truncated = strategy.evaluate(truncated, i)

        assert from_full is not None and from_truncated is not None
        assert from_full.reference_price == pytest.approx(from_truncated.reference_price)
        assert from_full.stop == pytest.approx(from_truncated.stop)
        assert from_full.target == pytest.approx(from_truncated.target)


class TestSignalValidation:
    def test_rejects_a_buy_stop_above_entry(self):
        with pytest.raises(ValueError, match="buy signal requires"):
            Signal(
                time=None, direction="buy", reference_price=2000.0,
                stop=2005.0, target=2010.0, atr=1.0, reason="inverted",
            )

    def test_rejects_a_sell_target_above_entry(self):
        with pytest.raises(ValueError, match="sell signal requires"):
            Signal(
                time=None, direction="sell", reference_price=2000.0,
                stop=1995.0, target=2010.0, atr=1.0, reason="inverted",
            )

    def test_reward_risk_is_computed_from_the_prices(self):
        signal = Signal(
            time=None, direction="buy", reference_price=2000.0,
            stop=1996.0, target=2008.0, atr=2.0, reason="ok",
        )
        assert signal.stop_distance == pytest.approx(4.0)
        assert signal.reward_risk == pytest.approx(2.0)
