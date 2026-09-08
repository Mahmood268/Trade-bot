"""The Risk Warden — position sizing and every hard veto.

This module is the last thing standing between a signal and the account. It is
deliberately boring: pure arithmetic and explicit branches, no LLM anywhere near
it, every path unit-tested against hand-computed numbers. An agent may ask for a
smaller position; nothing in the system can ask for a larger one.

Three invariants hold no matter what is passed in:

* **Size is floored, never rounded.** ``0.125`` lots becomes ``0.12``, so the
  realised risk is always at or below the authorised budget.
* **The daily budget is spent once.** Realised losses and the open positions'
  risk-to-stop both count against it, so the cap cannot be breached by opening
  a third trade while two are already exposed.
* **Uncertainty means no trade.** A missing broker limit, a stop the broker
  would reject, or a lot size below the broker minimum all produce a veto, not
  a best guess.

Sizing, in one line and in account currency:

    lots = risk_amount / (stop_points x value_per_point_per_lot)

Worked through for the configured $5,000 demo: 1% of equity is $50; a $4.00 stop
on gold is 400 points; each point is worth $1.00 per lot; 50 / 400 = 0.125 lots,
floored to **0.12 lots** risking **$48.00**.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from goldbot.mt5_client import SymbolSpec
from goldbot.strategy.base import Signal


@dataclass(frozen=True)
class RiskState:
    """The account and the day so far, as the warden needs to see it.

    The engine assembles this each cycle from the broker plus the journal. It is
    a plain snapshot on purpose: the warden holds no state of its own, so the
    same inputs always produce the same decision, in live trading and in replay.
    """

    equity: float
    balance: float
    starting_balance: float
    day_start_equity: float
    realised_pnl_today: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    open_positions: int = 0
    open_risk_amount: float = 0.0
    spread_points: float = 0.0
    margin_free: float | None = None
    halted: bool = False
    halt_reason: str = ""


@dataclass(frozen=True)
class RiskDecision:
    """The warden's verdict, journalled verbatim next to the trade."""

    approved: bool
    reason: str
    volume: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    stop_points: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rejected(self) -> bool:
        return not self.approved


class RiskWarden:
    def __init__(self, cfg) -> None:
        """
        Args:
            cfg: a ``RiskConfig`` (see goldbot.config).
        """
        self.cfg = cfg

    # ------------------------------------------------------------------
    # Halt checks — asked every cycle, independently of any signal.
    # ------------------------------------------------------------------

    def halt_reason(self, state: RiskState) -> str | None:
        """Reason trading must stop entirely, or None to continue.

        Ordered most-severe first so the reported reason is the real one. These
        are also checked before sizing, because a halted account must not open a
        position however good the setup looks.
        """
        if state.halted:
            return state.halt_reason or "manually halted"

        floor = state.starting_balance * self.cfg.equity_floor_pct / 100.0
        if state.equity < floor:
            return (
                f"equity {state.equity:.2f} is below the equity floor {floor:.2f} "
                f"({self.cfg.equity_floor_pct}% of starting balance "
                f"{state.starting_balance:.2f}) — permanent halt until cleared by hand"
            )

        daily_budget = self.daily_budget(state)
        if state.realised_pnl_today <= -daily_budget:
            return (
                f"daily loss {abs(state.realised_pnl_today):.2f} has reached the "
                f"{self.cfg.daily_loss_limit_pct}% limit ({daily_budget:.2f}) — "
                "halted until the next trading day"
            )

        if state.consecutive_losses >= self.cfg.max_consecutive_losses:
            return (
                f"{state.consecutive_losses} consecutive losses reached the limit of "
                f"{self.cfg.max_consecutive_losses} — halted pending manual /resume"
            )

        return None

    def daily_budget(self, state: RiskState) -> float:
        """Currency amount the day is allowed to lose, from start-of-day equity."""
        return state.day_start_equity * self.cfg.daily_loss_limit_pct / 100.0

    def remaining_daily_budget(self, state: RiskState) -> float:
        """What is left of today's loss allowance.

        Open positions count at their full risk-to-stop. Treating an open trade
        as costing nothing until it closes is how a system with a 4% daily cap
        loses 6% in an afternoon.
        """
        spent = max(0.0, -state.realised_pnl_today) + max(0.0, state.open_risk_amount)
        return self.daily_budget(state) - spent

    # ------------------------------------------------------------------
    # The main entry point.
    # ------------------------------------------------------------------

    def evaluate(
        self,
        signal: Signal,
        spec: SymbolSpec,
        state: RiskState,
        size_multiplier: float = 1.0,
        margin_per_lot: float | None = None,
    ) -> RiskDecision:
        """Approve and size a proposed trade, or reject it with a reason.

        Args:
            signal: the strategy's proposal.
            spec: broker contract specification for the traded symbol.
            state: account and day snapshot.
            size_multiplier: agent-supplied shrink factor in [0, 1]. Values above
                1.0 are rejected outright rather than clamped — an agent asking
                to size *up* is a bug or a prompt injection, and it should be
                visible in the journal as an anomaly, not silently normalised.
            margin_per_lot: margin the broker requires per 1.00 lot, if known.
                ``None`` skips the margin check.
        """
        notes: list[str] = []

        halt = self.halt_reason(state)
        if halt:
            return RiskDecision(False, f"trading halted: {halt}")

        if size_multiplier > 1.0:
            return RiskDecision(
                False,
                f"size_multiplier {size_multiplier} exceeds 1.0 — agents may only reduce "
                "risk, never increase it. Refusing the trade and flagging the anomaly.",
            )
        if size_multiplier <= 0.0:
            return RiskDecision(False, f"size_multiplier {size_multiplier} is not positive")

        if state.open_positions >= self.cfg.max_open_positions:
            return RiskDecision(
                False,
                f"already holding {state.open_positions} position(s), limit is "
                f"{self.cfg.max_open_positions}",
            )

        if state.trades_today >= self.cfg.max_daily_trades:
            return RiskDecision(
                False,
                f"{state.trades_today} trades today has reached the daily cap of "
                f"{self.cfg.max_daily_trades}",
            )

        if state.spread_points > self.cfg.max_spread_points:
            return RiskDecision(
                False,
                f"spread {state.spread_points:.1f} points exceeds the limit of "
                f"{self.cfg.max_spread_points} — the edge does not survive this cost",
            )

        if spec.point <= 0:
            return RiskDecision(False, f"broker reports point size {spec.point} for {spec.name}")

        stop_points = signal.stop_distance / spec.point
        if stop_points < self.cfg.min_stop_points:
            return RiskDecision(
                False,
                f"stop is {stop_points:.0f} points, below the {self.cfg.min_stop_points}-point "
                "minimum — spread noise alone would close it",
                stop_points=stop_points,
            )

        if spec.stops_level_points and stop_points < spec.stops_level_points:
            return RiskDecision(
                False,
                f"stop is {stop_points:.0f} points but the broker requires at least "
                f"{spec.stops_level_points} — the order would be rejected as invalid stops",
                stop_points=stop_points,
            )

        value_per_point = spec.value_per_point_per_lot
        if value_per_point <= 0:
            return RiskDecision(
                False,
                f"cannot value a point for {spec.name} (tick_value={spec.tick_value}, "
                f"tick_size={spec.tick_size}, contract_size={spec.contract_size}) — refusing "
                "to size a position on a guess",
                stop_points=stop_points,
            )

        # Budget: the smaller of per-trade risk and what remains of the day.
        per_trade = state.equity * self.cfg.risk_per_trade_pct / 100.0 * size_multiplier
        if size_multiplier < 1.0:
            notes.append(f"agent size multiplier {size_multiplier:.2f}")

        remaining = self.remaining_daily_budget(state)
        if remaining <= 0:
            return RiskDecision(
                False,
                f"today's remaining loss budget is {remaining:.2f} once realised losses "
                f"({state.realised_pnl_today:.2f}) and open risk "
                f"({state.open_risk_amount:.2f}) are counted",
                stop_points=stop_points,
            )
        risk_amount = per_trade
        if risk_amount > remaining:
            risk_amount = remaining
            notes.append(
                f"shrunk to the {remaining:.2f} left of today's loss budget "
                f"(from {per_trade:.2f})"
            )

        raw_volume = risk_amount / (stop_points * value_per_point)
        volume = spec.normalize_volume(raw_volume)

        if volume > self.cfg.max_lot:
            volume = spec.normalize_volume(self.cfg.max_lot)
            notes.append(f"clamped to the {self.cfg.max_lot} lot ceiling")

        if volume < spec.volume_min:
            return RiskDecision(
                False,
                f"sizing gives {raw_volume:.4f} lots, below the broker minimum "
                f"{spec.volume_min}. Taking the minimum would risk "
                f"{spec.volume_min * stop_points * value_per_point:.2f} against a budget of "
                f"{risk_amount:.2f} — skipping the trade instead of over-risking",
                stop_points=stop_points,
            )

        actual_risk = volume * stop_points * value_per_point
        # Flooring guarantees this, but a broker reporting a nonsense volume_step
        # could break it, and silently over-risking is the one failure that must
        # never happen quietly.
        if actual_risk > risk_amount + 1e-9:
            return RiskDecision(
                False,
                f"sized position would risk {actual_risk:.2f}, above the authorised "
                f"{risk_amount:.2f} — refusing (check the broker's volume_step "
                f"{spec.volume_step})",
                stop_points=stop_points,
            )

        if margin_per_lot is not None and state.margin_free is not None:
            required = margin_per_lot * volume
            if required > state.margin_free:
                return RiskDecision(
                    False,
                    f"position needs {required:.2f} margin but only {state.margin_free:.2f} "
                    "is free",
                    stop_points=stop_points,
                )

        risk_pct = actual_risk / state.equity * 100.0 if state.equity else 0.0
        return RiskDecision(
            approved=True,
            reason=(
                f"{volume:g} lots risking {actual_risk:.2f} ({risk_pct:.2f}% of equity) "
                f"over a {stop_points:.0f}-point stop"
            ),
            volume=volume,
            risk_amount=actual_risk,
            risk_pct=risk_pct,
            stop_points=stop_points,
            notes=tuple(notes),
        )
