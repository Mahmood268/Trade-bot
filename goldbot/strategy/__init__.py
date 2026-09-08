"""Deterministic signal generation.

Strategies are pure functions of closed bars. They never call the broker, never
read account state, and never size a position — that is the Risk Warden's job.
Keeping them pure is what makes the backtest a faithful replay of the live path.
"""

from goldbot.strategy.base import Signal, Strategy
from goldbot.strategy.trend_pullback import TrendPullback

__all__ = ["Signal", "Strategy", "TrendPullback"]
