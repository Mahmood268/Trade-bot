"""Technical indicators in plain pandas/numpy.

No TA-Lib: it needs a C toolchain and is a recurring installation failure on
Windows, which is exactly where this bot has to run. Every indicator here is a
few lines of arithmetic, so the dependency buys nothing and costs setup pain.

Two conventions apply throughout:

* **Wilder smoothing** (used by ATR, RSI and ADX) is the original recursive
  average — seeded with a simple mean of the first ``period`` values, then
  ``s_t = s_{t-1} + (x_t - s_{t-1}) / period``. It is *not* the same as an EMA
  with ``span=period``; using the wrong one shifts every ATR-derived stop.
* **Warm-up values are NaN, never a partial estimate.** A half-converged
  indicator that looks like a number is how backtests end up trading noise at
  the start of the series. Call :func:`warmup_bars` to size the history you
  need before the first signal is allowed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Columns every function here expects, using MT5's own naming.
OHLC = ("open", "high", "low", "close")


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's recursive smoothing, tolerant of a leading NaN block.

    Series derived from ``diff()`` start with a NaN; the seed window begins at
    the first real value rather than at index 0, so ATR and RSI line up with
    every charting package.
    """
    n = len(values)
    out = np.full(n, np.nan, dtype=float)

    valid = np.flatnonzero(~np.isnan(values))
    if valid.size == 0:
        return out
    start = int(valid[0])
    if n - start < period:
        return out

    seed_end = start + period
    out[seed_end - 1] = float(np.mean(values[start:seed_end]))
    for i in range(seed_end, n):
        out[i] = out[i - 1] + (values[i] - out[i - 1]) / period
    return out


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return pd.Series(_wilder(series.to_numpy(dtype=float), period), index=series.index)


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, warm-up masked.

    ``adjust=False`` seeds the average with the first observation, so early
    values carry the seed's bias. The first ``period - 1`` values are masked to
    make that explicit — but the bias decays rather than vanishing at bar
    ``period``, which is why :func:`warmup_bars` asks for several spans.
    """
    out = series.ewm(span=period, adjust=False).mean()
    out.iloc[: period - 1] = np.nan
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    """max(high-low, |high-prev_close|, |low-prev_close|).

    The first bar has no previous close, so it falls back to high-low — Wilder's
    own convention.
    """
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — the volatility unit every stop in this system uses."""
    return wilder_smooth(true_range(df), period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. Returns 100 where there has been no downside at all."""
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    # Preserve the leading NaN so the seed window starts at the first real move.
    gains.iloc[0] = np.nan
    losses.iloc[0] = np.nan

    avg_gain = wilder_smooth(gains, period)
    avg_loss = wilder_smooth(losses, period)

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 means an unbroken run of up-closes: RSI is 100 by definition.
    out = out.where(~((avg_loss == 0.0) & avg_gain.notna()), 100.0)
    return out


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder's ADX with +DI/-DI.

    ADX measures trend *strength* regardless of direction: the bot uses it to
    stay out of ranges, where a pullback strategy bleeds on spread.

    Returns a frame with columns ``adx``, ``plus_di``, ``minus_di``. ADX needs
    roughly ``2 * period`` bars before it produces a value — it is a smoothed
    average of a value that is itself smoothed.
    """
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    # diff() gives NaN at index 0; keep it so the seed window is honest.
    plus_dm[0] = np.nan
    minus_dm[0] = np.nan

    tr = true_range(df)
    tr_smooth = wilder_smooth(tr, period)
    plus_smooth = pd.Series(_wilder(plus_dm, period), index=df.index)
    minus_smooth = pd.Series(_wilder(minus_dm, period), index=df.index)

    safe_tr = tr_smooth.replace(0.0, np.nan)
    plus_di = 100.0 * plus_smooth / safe_tr
    minus_di = 100.0 * minus_smooth / safe_tr

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_line = wilder_smooth(dx, period)

    return pd.DataFrame(
        {"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di}, index=df.index
    )


def warmup_bars(cfg) -> int:
    """Bars of history required before any signal may be trusted.

    Args:
        cfg: a ``StrategyConfig``.

    The slow EMA dominates: with ``adjust=False`` the average is seeded with a
    single price, and that seed's influence decays geometrically rather than
    ending at bar ``period``. Three spans puts the residual bias below ~5% of a
    one-off shock, which is comfortably inside a tick of gold.
    """
    return max(
        3 * cfg.slow_ema,
        3 * cfg.trend_ema,
        2 * cfg.adx_period + 1,
        cfg.atr_period + 1,
        cfg.rsi_period + 1,
        cfg.pullback_lookback + 1,
    )


def compute(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Attach every indicator the strategy needs to a copy of the bar frame.

    Args:
        df: OHLC bars, oldest first, indexed by bar-open time.
        cfg: a ``StrategyConfig``.
    """
    missing = [c for c in OHLC if c not in df.columns]
    if missing:
        raise ValueError(f"bar frame is missing required column(s): {missing}")
    if not df.index.is_monotonic_increasing:
        raise ValueError("bar frame must be sorted oldest-first before computing indicators")

    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.fast_ema)
    out["ema_trend"] = ema(out["close"], cfg.trend_ema)
    out["ema_slow"] = ema(out["close"], cfg.slow_ema)
    out["atr"] = atr(out, cfg.atr_period)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)

    adx_frame = adx(out, cfg.adx_period)
    out["adx"] = adx_frame["adx"]
    out["plus_di"] = adx_frame["plus_di"]
    out["minus_di"] = adx_frame["minus_di"]

    return out
