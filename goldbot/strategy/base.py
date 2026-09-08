"""The contract every strategy implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import pandas as pd

from goldbot.mt5_client import Direction


@dataclass(frozen=True)
class Signal:
    """A proposed trade, priced but not sized.

    A signal carries a stop and a target as absolute prices, decided from the
    bar that produced it. Lot size is deliberately absent: only the Risk Warden
    turns a stop distance into a position size, so there is exactly one place in
    the codebase where money is put at risk.

    ``reference_price`` is the close of the signal bar. The actual fill happens
    at the next bar's open (backtest) or at market (live), and will differ — the
    executor journals both so slippage is measurable rather than assumed.
    """

    time: datetime
    direction: Direction
    reference_price: float
    stop: float
    target: float
    atr: float
    reason: str
    context: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A stop on the wrong side of entry would invert the risk calculation and
        # size the position off a negative distance. Fail loudly, at the source.
        if self.direction == "buy" and not (self.stop < self.reference_price < self.target):
            raise ValueError(
                f"buy signal requires stop < price < target, got "
                f"{self.stop} / {self.reference_price} / {self.target}"
            )
        if self.direction == "sell" and not (self.target < self.reference_price < self.stop):
            raise ValueError(
                f"sell signal requires target < price < stop, got "
                f"{self.target} / {self.reference_price} / {self.stop}"
            )

    @property
    def stop_distance(self) -> float:
        """Price distance from reference to stop. Always positive."""
        return abs(self.reference_price - self.stop)

    @property
    def target_distance(self) -> float:
        return abs(self.target - self.reference_price)

    @property
    def reward_risk(self) -> float:
        """Planned reward-to-risk at the reference price."""
        return self.target_distance / self.stop_distance if self.stop_distance else 0.0


class Strategy(Protocol):
    """Anything the engine and backtester can run."""

    name: str

    def evaluate(self, bars: pd.DataFrame, i: int) -> Signal | None:
        """Inspect bar ``i`` of an indicator-enriched frame and propose a trade.

        Args:
            bars: output of ``goldbot.indicators.compute`` — closed bars only.
            i: index of the bar being evaluated. Implementations must not read
                beyond it; doing so is lookahead bias, and it makes a backtest
                profitable in ways live trading never reproduces.
        """
        ...
