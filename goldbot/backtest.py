"""Bar-replay backtester.

This exists to answer one question before any money is involved: does the rules
engine have an edge that survives its own costs? It is built to fail honestly
rather than to look good.

**Three things it does that naive backtesters skip**

1. **Intrabar resolution with M1 bars.** When a trading bar contains both the
   stop and the target, the M15 bar alone cannot say which came first. Assuming
   the target is the single most common way a backtest reports profits that
   never appear live. Given an M1 frame this walks the minute bars in order and
   resolves it properly; without one it assumes the stop hit first, every time.
2. **Bid/ask modelling.** MT5 bars are bid prices. A buy fills at the ask and
   exits at the bid; a sell does the reverse. Spread is therefore paid on the
   round turn rather than quietly ignored, and stops are checked against the
   price the position actually exits at.
3. **The live sizing code.** Positions are sized by :class:`goldbot.risk.RiskWarden`
   — the same class the live engine uses, with the same vetoes, the same daily
   budget and the same lot flooring. A backtest that sizes differently from the
   bot is measuring a system nobody is going to trade.

**What it deliberately cannot measure:** the Claude agents. They react to news
that no historical bar records, so replaying them is not possible. The backtest
scores the deterministic core alone; the agents' contribution is measured
forward, on demo, by journalling what would have happened both ways.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from goldbot import indicators
from goldbot.mt5_client import Direction, SymbolSpec
from goldbot.risk import RiskState, RiskWarden
from goldbot.sessions import SessionGate
from goldbot.strategy.base import Signal, Strategy


def default_gold_spec(name: str = "XAUUSD") -> SymbolSpec:
    """A standard 100oz gold contract, for backtesting before the broker is known.

    Replace it with your broker's real numbers — ``scripts/check_connection.py``
    prints them — before believing any result. A wrong ``tick_value`` scales
    every position and every P&L figure in the report.
    """
    return SymbolSpec(
        name=name,
        digits=2,
        point=0.01,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level_points=0,
        freeze_level_points=0,
        tick_value=1.0,
        tick_size=0.01,
        filling_modes=1,
        currency_profit="USD",
    )


# A fill that leaves less than this reward-to-risk is not the trade the strategy
# proposed. Taking it anyway is how slippage quietly turns a 2R system into a 1R one.
MIN_REWARD_RISK = 1.0


@dataclass(frozen=True)
class Costs:
    """Trading frictions, in the units brokers quote them in."""

    spread_points: float = 25.0
    slippage_points: float = 2.0
    commission_per_lot_round_turn: float = 7.0


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime | None
    direction: Direction
    volume: float
    entry_price: float
    exit_price: float | None
    initial_stop: float
    stop: float
    target: float
    risk_amount: float
    risk_points: float
    commission: float
    authorised_risk: float = 0.0
    equity_at_entry: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""
    bars_held: int = 0
    mae_r: float = 0.0
    mfe_r: float = 0.0
    reason: str = ""

    @property
    def r_multiple(self) -> float:
        """Result in units of the risk taken — the only comparable P&L measure."""
        return self.pnl / self.risk_amount if self.risk_amount else 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    starting_balance: float
    ending_balance: float
    bars: int
    skipped: dict[str, int] = field(default_factory=dict)
    halt_resumes: int = 0

    @property
    def closed(self) -> list[Trade]:
        return [t for t in self.trades if not t.is_open]

    def stats(self) -> dict[str, float]:
        trades = self.closed
        n = len(trades)
        if n == 0:
            return {"trades": 0}

        rs = np.array([t.r_multiple for t in trades])
        pnls = np.array([t.pnl for t in trades])
        wins = pnls > 0
        gross_win = float(pnls[wins].sum())
        gross_loss = float(-pnls[~wins].sum())

        return {
            "trades": float(n),
            "wins": float(wins.sum()),
            "losses": float((~wins).sum()),
            "win_rate_pct": float(wins.mean() * 100.0),
            "expectancy_r": float(rs.mean()),
            "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "avg_win_r": float(rs[wins].mean()) if wins.any() else 0.0,
            "avg_loss_r": float(rs[~wins].mean()) if (~wins).any() else 0.0,
            "worst_trade_r": float(rs.min()),
            "best_trade_r": float(rs.max()),
            "net_pnl": float(pnls.sum()),
            "total_return_pct": float(
                (self.ending_balance - self.starting_balance) / self.starting_balance * 100.0
            ),
            "max_drawdown_pct": self.max_drawdown_pct(),
            "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
            "commission_paid": float(sum(t.commission for t in trades)),
        }

    def max_drawdown_pct(self) -> float:
        """Deepest peak-to-trough fall of the equity curve, as a percentage."""
        if self.equity_curve.empty:
            return 0.0
        curve = self.equity_curve
        peak = curve.cummax()
        return float(((peak - curve) / peak).max() * 100.0)

    def weekly_returns_pct(self) -> pd.Series:
        """Percentage return per calendar week — what the 3-5% goal is measured against."""
        if self.equity_curve.empty:
            return pd.Series(dtype=float)
        weekly = self.equity_curve.resample("W").last().dropna()
        first = pd.Series([self.starting_balance], index=[self.equity_curve.index[0]])
        joined = pd.concat([first, weekly])
        return (joined.pct_change().dropna() * 100.0).rename("weekly_return_pct")

    def by_hour(self) -> pd.DataFrame:
        """Trade count and expectancy grouped by entry hour (UTC).

        Almost always the most actionable table in the report: a strategy that
        makes all its money in two hours and gives it back in six is a schedule
        problem, not a strategy problem.
        """
        trades = self.closed
        if not trades:
            return pd.DataFrame(columns=["trades", "expectancy_r", "net_pnl"])
        frame = pd.DataFrame(
            {
                "hour": [t.entry_time.hour for t in trades],
                "r": [t.r_multiple for t in trades],
                "pnl": [t.pnl for t in trades],
            }
        )
        grouped = frame.groupby("hour").agg(
            trades=("r", "size"), expectancy_r=("r", "mean"), net_pnl=("pnl", "sum")
        )
        return grouped.sort_index()


class Backtester:
    """Replays closed bars through the real strategy, warden and cost model."""

    def __init__(
        self,
        cfg,
        spec: SymbolSpec | None = None,
        costs: Costs | None = None,
        starting_balance: float = 5000.0,
        strategy: Strategy | None = None,
        resume_after_halt_daily: bool = True,
    ) -> None:
        """
        Args:
            cfg: a full ``Config``.
            spec: broker contract spec; defaults to a standard 100oz gold contract.
            costs: spread, slippage and commission assumptions.
            starting_balance: opening account balance in account currency.
            strategy: defaults to ``TrendPullback`` built from ``cfg.strategy``.
            resume_after_halt_daily: clear the consecutive-loss halt at the start
                of each new trading day, modelling a user who acknowledges the
                Telegram alert overnight and resumes. Live, that halt needs a
                manual ``/resume``; leaving it latched in a replay would end the
                backtest at the first five-loss streak and measure the halt
                rather than the strategy. The count of resumes is reported, so
                a strategy that only survives by being restarted daily is
                visible rather than hidden.
        """
        from goldbot.strategy.trend_pullback import TrendPullback

        self.cfg = cfg
        self.spec = spec or default_gold_spec()
        self.costs = costs or Costs()
        self.starting_balance = starting_balance
        self.strategy = strategy or TrendPullback(cfg.strategy)
        self.warden = RiskWarden(cfg.risk)
        self.gate = SessionGate(cfg.sessions)
        self.resume_after_halt_daily = resume_after_halt_daily

    # ------------------------------------------------------------------

    def run(self, bars: pd.DataFrame, m1: pd.DataFrame | None = None) -> BacktestResult:
        """Replay ``bars``, optionally resolving intrabar order with ``m1``.

        Args:
            bars: OHLC frame on the trading timeframe, oldest first, UTC index.
            m1: optional M1 OHLC frame covering the same span.
        """
        data = indicators.compute(bars, self.cfg.strategy)
        warmup = indicators.warmup_bars(self.cfg.strategy)
        spread_price = self.costs.spread_points * self.spec.point
        slip = self.costs.slippage_points * self.spec.point

        balance = self.starting_balance
        open_trades: list[Trade] = []
        closed: list[Trade] = []
        pending: Signal | None = None
        skipped: dict[str, int] = {}

        day_key = None
        day_start_equity = balance
        realised_today = 0.0
        trades_today = 0
        consecutive_losses = 0
        halt_resumes = 0

        equity_times: list[pd.Timestamp] = []
        equity_values: list[float] = []

        sub_bars = _M1Index(data.index, m1, self._bar_duration(data.index))

        for i in range(len(data)):
            ts = data.index[i]
            bar = data.iloc[i]

            local_day = self.gate.local(ts.to_pydatetime()).date()
            if local_day != day_key:
                day_key = local_day
                day_start_equity = balance + _unrealised(open_trades, bar, spread_price, self.spec)
                realised_today = 0.0
                trades_today = 0
                if (
                    self.resume_after_halt_daily
                    and consecutive_losses >= self.cfg.risk.max_consecutive_losses
                ):
                    consecutive_losses = 0
                    halt_resumes += 1

            # 1. A signal from the previous bar fills at this bar's open.
            if pending is not None:
                trade = self._open_trade(
                    pending, bar, ts, balance, open_trades, day_start_equity,
                    realised_today, trades_today, consecutive_losses, spread_price, slip,
                )
                if isinstance(trade, Trade):
                    open_trades.append(trade)
                    trades_today += 1
                else:
                    skipped[trade] = skipped.get(trade, 0) + 1
                pending = None

            # 2. Walk this bar's price action against every open position.
            for trade in list(open_trades):
                trade.bars_held += 1
                exit_info = self._walk_bar(trade, bar, sub_bars.slice_for(i), spread_price, slip)
                if exit_info is not None:
                    price, when, reason = exit_info
                    self._close_trade(trade, price, when, reason)
                    balance += trade.pnl
                    realised_today += trade.pnl
                    consecutive_losses = consecutive_losses + 1 if trade.pnl <= 0 else 0
                    open_trades.remove(trade)
                    closed.append(trade)

            bar_close_time = ts.to_pydatetime() + sub_bars.duration

            # 3. Flattening. Daily: the routine ends every day flat. Weekend: a
            #    stop cannot protect against a Monday gap. Labelled separately so
            #    the journal can show what the end-of-day rule costs or saves.
            flatten = self.gate.must_flatten(bar_close_time)
            if open_trades and flatten.allowed:
                label = "eod_flatten" if flatten.kind == "daily" else "weekend_flatten"
                for trade in list(open_trades):
                    price = _exit_price(trade.direction, float(bar["close"]), spread_price, 0.0)
                    self._close_trade(trade, price, bar_close_time, label)
                    balance += trade.pnl
                    realised_today += trade.pnl
                    consecutive_losses = consecutive_losses + 1 if trade.pnl <= 0 else 0
                    open_trades.remove(trade)
                    closed.append(trade)

            equity = balance + _unrealised(open_trades, bar, spread_price, self.spec)
            equity_times.append(ts)
            equity_values.append(equity)

            # 4. Look for a new signal, to be filled at the next bar's open.
            if i >= warmup and i + 1 < len(data):
                verdict = self.gate.can_enter(data.index[i + 1].to_pydatetime())
                if verdict.allowed:
                    pending = self.strategy.evaluate(data, i)
                elif self.strategy.evaluate(data, i) is not None:
                    skipped["session"] = skipped.get("session", 0) + 1

        # Anything still open at the end of the data is marked, not silently dropped.
        for trade in open_trades:
            trade.exit_reason = "still_open_at_end_of_data"

        return BacktestResult(
            trades=closed + open_trades,
            equity_curve=pd.Series(equity_values, index=pd.DatetimeIndex(equity_times)),
            starting_balance=self.starting_balance,
            ending_balance=balance,
            bars=len(data),
            skipped=skipped,
            halt_resumes=halt_resumes,
        )

    # ------------------------------------------------------------------

    def _open_trade(
        self, signal, bar, ts, balance, open_trades, day_start_equity,
        realised_today, trades_today, consecutive_losses, spread_price, slip,
    ):
        """Size and fill a pending signal, or return a string naming the veto."""
        equity = balance + _unrealised(open_trades, bar, spread_price, self.spec)
        state = RiskState(
            equity=equity,
            balance=balance,
            starting_balance=self.starting_balance,
            day_start_equity=day_start_equity,
            realised_pnl_today=realised_today,
            trades_today=trades_today,
            consecutive_losses=consecutive_losses,
            open_positions=len(open_trades),
            open_risk_amount=sum(_open_risk(t, self.spec) for t in open_trades),
            spread_points=self.costs.spread_points,
        )
        # Size against the price we will actually fill at, never the signal's close.
        # The signal was generated on the previous bar's close; this bar opens
        # somewhere else, and the stop does not move to follow it. Sizing off the
        # stale close is how a system that believes it risks 1% risks rather more
        # on exactly the fast markets where the gap is widest.
        entry = _entry_price(signal.direction, float(bar["open"]), spread_price, slip)
        try:
            at_fill = replace(signal, reference_price=entry)
        except ValueError:
            # The open jumped past the stop or the target: there is no trade left
            # to take, only a fill that is already wrong.
            return "fill outside the signal's own levels"

        if at_fill.reward_risk < MIN_REWARD_RISK:
            return "fill degraded the reward-to-risk below 1:1"

        decision = self.warden.evaluate(at_fill, self.spec, state)
        if not decision.approved:
            return _veto_key(decision.reason)

        risk_points = abs(entry - signal.stop) / self.spec.point
        risk_amount = risk_points * self.spec.value_per_point_per_lot * decision.volume
        commission = self.costs.commission_per_lot_round_turn * decision.volume

        return Trade(
            entry_time=ts.to_pydatetime(),
            exit_time=None,
            direction=signal.direction,
            volume=decision.volume,
            entry_price=entry,
            exit_price=None,
            initial_stop=signal.stop,
            stop=signal.stop,
            target=signal.target,
            risk_amount=max(risk_amount, 1e-9),
            risk_points=risk_points,
            commission=commission,
            authorised_risk=decision.risk_amount,
            equity_at_entry=equity,
            reason=signal.reason,
        )

    def _walk_bar(self, trade, bar, sub, spread_price, slip):
        """Resolve one trading bar against an open position.

        Returns ``(price, time, reason)`` if the position closed, else None.
        Within any single bar the stop is checked first: when both levels sit
        inside the same bar the order is genuinely unknown, and assuming the
        worse of the two is the only assumption that cannot flatter the result.
        """
        frames = sub if sub is not None and len(sub) else [(bar.name, bar)]
        atr = float(bar["atr"]) if not _isnan(bar["atr"]) else 0.0

        for when, sb in frames:
            high, low = float(sb["high"]), float(sb["low"])
            self._track_excursion(trade, high, low, spread_price)

            if trade.direction == "buy":
                if low <= trade.stop:
                    return (trade.stop - slip, _as_dt(when), _stop_reason(trade))
                if high >= trade.target:
                    return (trade.target, _as_dt(when), "target")
            else:
                # A short exits by buying at the ask, which sits above the bid bars.
                ask_high, ask_low = high + spread_price, low + spread_price
                if ask_high >= trade.stop:
                    return (trade.stop + slip, _as_dt(when), _stop_reason(trade))
                if ask_low <= trade.target:
                    return (trade.target, _as_dt(when), "target")

            # Stop management runs after the exit checks so a bar cannot both
            # trigger a trailing move and be measured against the moved stop.
            self._manage_stop(trade, high, low, atr, spread_price)

        return None

    def _manage_stop(self, trade, high: float, low: float, atr: float, spread_price: float) -> None:
        strat = self.cfg.strategy
        risk = abs(trade.entry_price - trade.initial_stop)
        if risk <= 0:
            return

        if trade.direction == "buy":
            gain_r = (high - trade.entry_price) / risk
            if strat.breakeven_at_r is not None and gain_r >= strat.breakeven_at_r:
                trade.stop = max(trade.stop, trade.entry_price)
            if strat.trail_after_r is not None and gain_r >= strat.trail_after_r and atr > 0:
                trade.stop = max(trade.stop, high - strat.trail_atr_multiple * atr)
        else:
            ask_low = low + spread_price
            gain_r = (trade.entry_price - ask_low) / risk
            if strat.breakeven_at_r is not None and gain_r >= strat.breakeven_at_r:
                trade.stop = min(trade.stop, trade.entry_price)
            if strat.trail_after_r is not None and gain_r >= strat.trail_after_r and atr > 0:
                trade.stop = min(trade.stop, ask_low + strat.trail_atr_multiple * atr)

    def _track_excursion(self, trade, high: float, low: float, spread_price: float) -> None:
        risk = abs(trade.entry_price - trade.initial_stop)
        if risk <= 0:
            return
        if trade.direction == "buy":
            trade.mfe_r = max(trade.mfe_r, (high - trade.entry_price) / risk)
            trade.mae_r = min(trade.mae_r, (low - trade.entry_price) / risk)
        else:
            trade.mfe_r = max(trade.mfe_r, (trade.entry_price - (low + spread_price)) / risk)
            trade.mae_r = min(trade.mae_r, (trade.entry_price - (high + spread_price)) / risk)

    def _close_trade(self, trade, price: float, when: datetime, reason: str) -> None:
        points = (
            (price - trade.entry_price) if trade.direction == "buy"
            else (trade.entry_price - price)
        ) / self.spec.point
        trade.exit_price = price
        trade.exit_time = when
        trade.exit_reason = reason
        trade.pnl = points * self.spec.value_per_point_per_lot * trade.volume - trade.commission

    @staticmethod
    def _bar_duration(index: pd.DatetimeIndex) -> timedelta:
        if len(index) < 2:
            return timedelta(minutes=15)
        return pd.Timedelta(pd.Series(index).diff().median()).to_pytimedelta()


class _M1Index:
    """Maps each trading bar onto the M1 bars inside it."""

    def __init__(self, index: pd.DatetimeIndex, m1: pd.DataFrame | None, duration: timedelta):
        self.duration = duration
        self._rows: list[list[tuple[pd.Timestamp, pd.Series]]] | None = None
        if m1 is None or m1.empty:
            return
        m1 = m1.sort_index()
        starts = m1.index.searchsorted(index, side="left")
        ends = m1.index.searchsorted(index + pd.Timedelta(duration), side="left")
        self._rows = [list(m1.iloc[s:e].iterrows()) for s, e in zip(starts, ends)]

    def slice_for(self, i: int):
        if self._rows is None:
            return None
        return self._rows[i]


def _entry_price(direction: Direction, open_price: float, spread: float, slip: float) -> float:
    """Fill price for a market entry. Buys pay the ask; slippage always hurts."""
    return open_price + spread + slip if direction == "buy" else open_price - slip


def _exit_price(direction: Direction, bid: float, spread: float, slip: float) -> float:
    return bid - slip if direction == "buy" else bid + spread + slip


def _stop_reason(trade: Trade) -> str:
    """Distinguish the three ways a stop closes a trade.

    Worth separating in the journal: a system whose winners mostly end as
    ``breakeven_stop`` is protecting itself out of its own edge, and that is
    invisible if every stop exit is labelled the same.
    """
    if trade.stop == trade.entry_price:
        return "breakeven_stop"
    moved = (
        trade.stop > trade.initial_stop if trade.direction == "buy"
        else trade.stop < trade.initial_stop
    )
    return "trailing_stop" if moved else "stop"


def _open_risk(trade: Trade, spec: SymbolSpec) -> float:
    """Currency still at risk on an open trade, floored at zero past break-even."""
    distance = (
        trade.entry_price - trade.stop if trade.direction == "buy"
        else trade.stop - trade.entry_price
    )
    return max(0.0, distance / spec.point * spec.value_per_point_per_lot * trade.volume)


def _unrealised(trades: list[Trade], bar, spread: float, spec: SymbolSpec) -> float:
    total = 0.0
    close = float(bar["close"])
    for trade in trades:
        exit_px = _exit_price(trade.direction, close, spread, 0.0)
        points = (
            (exit_px - trade.entry_price) if trade.direction == "buy"
            else (trade.entry_price - exit_px)
        ) / spec.point
        total += points * spec.value_per_point_per_lot * trade.volume - trade.commission
    return total


# Warden reasons are written for a human reading one journal line. The report
# needs to count them, so each is collapsed to a short label. Order matters:
# the first matching phrase wins, so more specific phrases come first.
_VETO_LABELS = (
    ("trading halted", "halted"),
    ("spread", "spread too wide"),
    ("below the broker minimum", "position below min lot"),
    ("daily cap", "daily trade cap"),
    ("remaining loss budget", "daily loss budget spent"),
    ("point minimum", "stop too tight"),
    ("invalid stops", "stop inside broker limit"),
    ("limit is", "max open positions"),
    ("margin", "insufficient margin"),
    ("may only reduce risk", "agent tried to size up"),
)


def _veto_key(reason: str) -> str:
    """Collapse a warden reason into a short, countable label for the report."""
    for phrase, label in _VETO_LABELS:
        if phrase in reason:
            return label
    return reason.split(",")[0][:60]


def _isnan(value) -> bool:
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def _as_dt(value) -> datetime:
    return value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
