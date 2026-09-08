"""Trend pullback — the starting strategy.

The premise, stated plainly so it can be falsified: gold trends within a
session, and the highest-probability entry in a trend is not the breakout but
the first pullback into it, taken when momentum turns back the trend's way.
Whether that is *true enough to pay the spread* is what the backtest exists to
find out. Nothing here assumes it is.

A long needs all five of these on the same closed bar:

1. **Trend stack** — ``EMA20 > EMA50 > EMA200``. All three aligned, not merely
   price above one average. Shorts mirror it exactly.
2. **Trend strength** — ``ADX >= min_adx``. ADX is the range filter: this setup
   loses steadily in a chop, where price crosses the fast EMA constantly and
   every crossing looks like a pullback.
3. **A pullback happened** — price traded down to the fast EMA within the last
   ``pullback_lookback`` bars. Without this the rule would buy any strong bar in
   an uptrend, which is chasing, and chasing puts the stop far away.
4. **Resumption** — the signal bar closes back above the fast EMA *and* closes
   up. The pullback is over, not still going.
5. **Momentum, not exhaustion** — ``rsi_midline <= RSI < rsi_extreme``. Above
   the ceiling the move is extended and the structural stop is furthest away,
   which is the worst risk-to-reward the setup ever offers.

Stops go beyond the swing the setup is built on, never inside it: if the
pullback low is further than the ATR stop, the pullback low wins. A stop inside
the structure gets hit by the same noise that formed the structure.
"""

from __future__ import annotations

import math

import pandas as pd

from goldbot.strategy.base import Signal

# Padding beyond the pullback swing, in ATR. Placing the stop exactly on the
# swing extreme invites the wick that takes out the obvious level and reverses.
SWING_BUFFER_ATR = 0.1

_REQUIRED = ("ema_fast", "ema_trend", "ema_slow", "atr", "rsi", "adx")


class TrendPullback:
    name = "trend_pullback"

    def __init__(self, cfg) -> None:
        """
        Args:
            cfg: a ``StrategyConfig`` (see goldbot.config).
        """
        self.cfg = cfg

    def evaluate(self, bars: pd.DataFrame, i: int) -> Signal | None:
        """Propose a trade from closed bar ``i``, or None.

        Reads nothing beyond bar ``i``. That restriction is the whole reason the
        backtest can be believed.
        """
        cfg = self.cfg
        if i < cfg.pullback_lookback:
            return None

        bar = bars.iloc[i]
        if any(_missing(bar[col]) for col in _REQUIRED):
            return None  # still inside the indicator warm-up

        atr = float(bar["atr"])
        if atr <= 0:
            return None

        if float(bar["adx"]) < cfg.min_adx:
            return None

        fast, trend, slow = float(bar["ema_fast"]), float(bar["ema_trend"]), float(bar["ema_slow"])
        close, open_, rsi = float(bar["close"]), float(bar["open"]), float(bar["rsi"])

        # The pullback window includes the signal bar: price often tags the EMA
        # and closes back above it within a single bar.
        first = i - cfg.pullback_lookback + 1
        window = bars.iloc[first : i + 1]
        fast_window = window["ema_fast"]

        if fast > trend > slow:
            touched = bool((window["low"] <= fast_window).any())
            if not (touched and close > fast and close > open_):
                return None
            if not (cfg.rsi_midline <= rsi < cfg.rsi_extreme):
                return None
            stop = self._stop_for_long(close, atr, float(window["low"].min()))
            direction = "buy"

        elif fast < trend < slow:
            touched = bool((window["high"] >= fast_window).any())
            if not (touched and close < fast and close < open_):
                return None
            if not (100.0 - cfg.rsi_extreme < rsi <= 100.0 - cfg.rsi_midline):
                return None
            stop = self._stop_for_short(close, atr, float(window["high"].max()))
            direction = "sell"

        else:
            return None

        distance = abs(close - stop)
        if distance <= 0:
            return None

        target = (
            close + distance * cfg.tp_r_multiple
            if direction == "buy"
            else close - distance * cfg.tp_r_multiple
        )

        return Signal(
            time=bars.index[i].to_pydatetime(),
            direction=direction,
            reference_price=close,
            stop=stop,
            target=target,
            atr=atr,
            reason=(
                f"{direction} pullback: EMA stack aligned, ADX {float(bar['adx']):.1f} "
                f">= {cfg.min_adx}, price returned through EMA{cfg.fast_ema}, "
                f"RSI {rsi:.1f}"
            ),
            context={
                "ema_fast": fast,
                "ema_trend": trend,
                "ema_slow": slow,
                "atr": atr,
                "adx": float(bar["adx"]),
                "rsi": rsi,
                "stop_distance": distance,
            },
        )

    def _stop_for_long(self, close: float, atr: float, swing_low: float) -> float:
        atr_stop = close - self.cfg.atr_stop_multiple * atr
        if not self.cfg.use_structural_stop:
            return atr_stop
        return min(atr_stop, swing_low - SWING_BUFFER_ATR * atr)

    def _stop_for_short(self, close: float, atr: float, swing_high: float) -> float:
        atr_stop = close + self.cfg.atr_stop_multiple * atr
        if not self.cfg.use_structural_stop:
            return atr_stop
        return max(atr_stop, swing_high + SWING_BUFFER_ATR * atr)


def _missing(value) -> bool:
    try:
        return bool(math.isnan(float(value)))
    except (TypeError, ValueError):
        return True
