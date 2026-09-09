"""The executor — the only code that turns a decision into an order.

Everything upstream *proposes*. This module *acts*, under four rules that are
enforced here regardless of what asked for the action:

1. **Every entry carries a server-side stop loss at placement.** The stop lives
   on the broker, so a crashed bot, a sleeping PC or a dropped connection still
   leaves the position protected. An order without a stop is not sent.
2. **Stops only ever move toward profit.** Break-even, trailing, or an agent's
   ``TIGHTEN_STOP`` — any instruction that would widen a stop is refused and
   journalled as an anomaly. Size is never added to an open position.
3. **Idempotent entries.** Each signal gets a tag in the order comment. A retry
   after a timeout, or a restart mid-cycle, finds the existing position by its
   tag instead of opening a second one.
4. **Dry-run is the same code path.** With ``dry_run`` on, the executor keeps
   *paper* positions that are opened at the live ask/bid, stopped and targeted
   on live ticks, and flattened by the same rules. Nothing is sent to the
   broker, and the whole pipeline — including the parts that only run while a
   position is open — is exercised for real.

The executor does not decide anything. It does not know why a trade was
approved, and it does not care. That separation is what keeps this file small
enough to be sure of.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from goldbot.mt5_client import Direction, MT5Client, MT5Error, Position, Tick
from goldbot.risk import RiskDecision
from goldbot.strategy.base import Signal

log = logging.getLogger(__name__)

# Agent instructions the executor understands. Anything else is refused.
TIGHTEN_STOP = "TIGHTEN_STOP"
PARTIAL_CLOSE = "PARTIAL_CLOSE"
EXIT_NOW = "EXIT_NOW"
HOLD = "HOLD"
ALLOWED_INSTRUCTIONS = (HOLD, TIGHTEN_STOP, PARTIAL_CLOSE, EXIT_NOW)


@dataclass
class ManagedPosition:
    """The executor's view of one open position — live or paper."""

    ticket: int
    direction: Direction
    volume: float
    entry: float
    sl: float
    tp: float
    initial_sl: float
    risk_amount: float
    opened_at: datetime
    comment: str
    paper: bool = False
    actions: list[str] = field(default_factory=list)

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.initial_sl)

    def gain_r(self, price: float) -> float:
        """Open profit in R at ``price`` (bid for a long, ask for a short)."""
        if self.risk_distance <= 0:
            return 0.0
        move = price - self.entry if self.direction == "buy" else self.entry - price
        return move / self.risk_distance


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    reason: str
    ticket: int | None = None
    price: float | None = None
    volume: float = 0.0
    pnl: float | None = None
    dry_run: bool = False


class Executor:
    def __init__(self, client: MT5Client, cfg, journal, dry_run: bool = True) -> None:
        """
        Args:
            client: a connected ``MT5Client``.
            cfg: the full ``Config``.
            journal: a ``Journal``.
            dry_run: keep paper positions instead of sending orders.
        """
        self.client = client
        self.cfg = cfg
        self.journal = journal
        self.dry_run = dry_run
        self._paper: list[ManagedPosition] = []
        self._next_paper_ticket = 900_000
        # Live positions the executor has adopted, keyed by ticket. The broker is
        # the source of truth for existence; this holds what the broker cannot —
        # the initial stop and the risk taken, for R arithmetic.
        self._live: dict[int, ManagedPosition] = {}

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------

    @staticmethod
    def signal_tag(signal: Signal) -> str:
        """Order comment tagging the signal. MT5 truncates comments at ~31 chars."""
        side = "B" if signal.direction == "buy" else "S"
        return f"gb-{signal.time:%y%m%d-%H%M}-{side}"

    def open(
        self,
        signal: Signal,
        decision: RiskDecision,
        now: datetime,
        day: date,
        signal_id: int | None = None,
    ) -> ExecutionResult:
        """Place the approved trade, or explain why not."""
        if not decision.approved or decision.volume <= 0:
            return ExecutionResult(False, f"not approved: {decision.reason}")

        tag = self.signal_tag(signal)
        existing = self._find_by_comment(tag)
        if existing is not None:
            return ExecutionResult(
                True, f"already open under tag {tag} (ticket {existing.ticket}) — not duplicating",
                ticket=existing.ticket, price=existing.entry, volume=existing.volume,
                dry_run=existing.paper,
            )

        tick = self.client.get_tick()
        entry_ref = tick.ask if signal.direction == "buy" else tick.bid
        try:
            self.client.validate_stops(signal.direction, entry_ref, signal.stop, signal.target)
        except MT5Error as exc:
            self.journal.record_decision(signal_id, now, "executor", False, str(exc))
            return ExecutionResult(False, f"stops rejected before sending: {exc}")

        spec = self.client.symbol_spec()
        sl = spec.normalize_price(signal.stop)
        tp = spec.normalize_price(signal.target)

        if self.dry_run:
            ticket = self._next_paper_ticket
            self._next_paper_ticket += 1
            pos = ManagedPosition(
                ticket=ticket, direction=signal.direction, volume=decision.volume,
                entry=entry_ref, sl=sl, tp=tp, initial_sl=sl, risk_amount=decision.risk_amount,
                opened_at=now, comment=tag, paper=True,
            )
            self._paper.append(pos)
            self.journal.record_order(
                signal_id, now, day, signal.direction, decision.volume, entry_ref, sl, tp,
                decision.risk_amount, True, 0, tag, ticket, dry_run=True,
            )
            log.info("[DRY RUN] %s %.2f lots @ %.2f sl %.2f tp %.2f (%s)",
                     signal.direction, decision.volume, entry_ref, sl, tp, tag)
            return ExecutionResult(True, "paper position opened", ticket, entry_ref,
                                   decision.volume, dry_run=True)

        result = self.client.market_order(signal.direction, decision.volume, sl, tp, comment=tag)
        self.journal.record_order(
            signal_id, now, day, signal.direction, decision.volume, result.price, sl, tp,
            decision.risk_amount, result.ok, result.retcode, result.comment if not result.ok else tag,
            result.ticket, dry_run=False,
        )
        if not result.ok:
            return ExecutionResult(False, f"order rejected: {result.reason}")

        # Fill price is what the broker gave us; the stop stays where the
        # strategy put it. Re-measure the risk actually taken.
        entry = float(result.price or entry_ref)
        risk_points = abs(entry - sl) / spec.point
        pos = ManagedPosition(
            ticket=int(result.ticket), direction=signal.direction, volume=float(result.volume or decision.volume),
            entry=entry, sl=sl, tp=tp, initial_sl=sl,
            risk_amount=risk_points * spec.value_per_point_per_lot * float(result.volume or decision.volume),
            opened_at=now, comment=tag,
        )
        self._live[pos.ticket] = pos
        return ExecutionResult(True, "filled", pos.ticket, entry, pos.volume)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def positions(self) -> list[ManagedPosition]:
        """Open positions the executor owns. Broker first, then paper."""
        if self.dry_run:
            return list(self._paper)
        out: list[ManagedPosition] = []
        for p in self.client.positions():
            managed = self._live.get(p.ticket)
            if managed is None:
                managed = self._adopt_one(p)
            else:
                # The broker is the source of truth for sl/tp/volume.
                managed.sl, managed.tp, managed.volume = p.sl, p.tp, p.volume
            out.append(managed)
        # Forget anything the broker no longer has (closed by stop, by hand, or by us).
        for ticket in list(self._live):
            if ticket not in {p.ticket for p in out}:
                del self._live[ticket]
        return out

    def adopt(self, now: datetime, day: date) -> list[ManagedPosition]:
        """On start-up, take ownership of positions carrying our magic number.

        A restart must never orphan a trade: whatever the bot opened before it
        died is managed from here on, with its current stop treated as the
        initial one — the honest choice when the original is unknown.
        """
        if self.dry_run:
            return []
        adopted = self.positions()
        for pos in adopted:
            self.journal.record_event(
                now, day, "adopt",
                f"adopted {pos.direction} {pos.volume:g} lots ticket {pos.ticket} "
                f"@ {pos.entry:.2f} sl {pos.sl:.2f} tp {pos.tp:.2f}",
            )
        return adopted

    def _adopt_one(self, p: Position) -> ManagedPosition:
        spec = self.client.symbol_spec()
        sl = p.sl if p.sl else (p.open_price * (0.98 if p.direction == "buy" else 1.02))
        risk_points = abs(p.open_price - sl) / spec.point
        managed = ManagedPosition(
            ticket=p.ticket, direction=p.direction, volume=p.volume, entry=p.open_price,
            sl=p.sl, tp=p.tp, initial_sl=sl,
            risk_amount=risk_points * spec.value_per_point_per_lot * p.volume,
            opened_at=p.open_time, comment=p.comment,
        )
        if not p.sl:
            log.error("adopted position %s has NO stop loss — placing a protective one", p.ticket)
            self._set_stop(managed, spec.normalize_price(sl))
        self._live[p.ticket] = managed
        return managed

    def _find_by_comment(self, tag: str) -> ManagedPosition | None:
        for pos in self.positions():
            if pos.comment == tag:
                return pos
        return None

    # ------------------------------------------------------------------
    # Deterministic management: break-even and trailing
    # ------------------------------------------------------------------

    def manage(self, pos: ManagedPosition, tick: Tick, atr: float, now: datetime, day: date) -> list[str]:
        """Apply the strategy's break-even and trailing rules. Returns actions taken.

        Both only ever move the stop toward profit; :meth:`_set_stop` refuses
        anything else, so a bug here cannot widen risk.
        """
        strat = self.cfg.strategy
        spec = self.client.symbol_spec()
        mark = tick.bid if pos.direction == "buy" else tick.ask
        gain = pos.gain_r(mark)
        actions: list[str] = []

        if strat.breakeven_at_r is not None and gain >= strat.breakeven_at_r:
            be = spec.normalize_price(pos.entry)
            if self._is_tighter(pos, be):
                if self._set_stop(pos, be):
                    actions.append(f"breakeven @ {be:.2f} (+{gain:.2f}R)")

        if strat.trail_after_r is not None and gain >= strat.trail_after_r and atr > 0:
            trail = (
                mark - strat.trail_atr_multiple * atr if pos.direction == "buy"
                else mark + strat.trail_atr_multiple * atr
            )
            trail = spec.normalize_price(trail)
            if self._is_tighter(pos, trail):
                if self._set_stop(pos, trail):
                    actions.append(f"trail @ {trail:.2f} (+{gain:.2f}R)")

        for action in actions:
            pos.actions.append(action)
            self.journal.record_event(now, day, "manage", f"ticket {pos.ticket}: {action}")
        return actions

    def _is_tighter(self, pos: ManagedPosition, new_sl: float) -> bool:
        return new_sl > pos.sl if pos.direction == "buy" else new_sl < pos.sl

    def _set_stop(self, pos: ManagedPosition, new_sl: float) -> bool:
        """Move a stop. Refuses to widen — the one thing this must never do."""
        if pos.sl and not self._is_tighter(pos, new_sl):
            log.error("REFUSED: stop on %s would widen %.2f -> %.2f", pos.ticket, pos.sl, new_sl)
            return False
        if pos.paper:
            pos.sl = new_sl
            return True
        result = self.client.modify_position(pos.ticket, sl=new_sl)
        if result.ok:
            pos.sl = new_sl
            return True
        log.warning("stop modify on %s failed: %s", pos.ticket, result.reason)
        return False

    # ------------------------------------------------------------------
    # Agent instructions — reduce-only, by construction
    # ------------------------------------------------------------------

    def apply_instruction(
        self,
        ticket: int,
        instruction: str,
        now: datetime,
        day: date,
        new_sl: float | None = None,
        fraction: float | None = None,
        source: str = "agent",
    ) -> ExecutionResult:
        """Carry out a Trade Manager instruction, or refuse it.

        The vocabulary is closed: HOLD, TIGHTEN_STOP, PARTIAL_CLOSE, EXIT_NOW.
        Anything else — and any TIGHTEN_STOP that would in fact widen — is
        refused and written to the journal as an anomaly, because an agent
        asking to add risk is either a bug or an injection and must be visible.
        """
        pos = next((p for p in self.positions() if p.ticket == ticket), None)
        if pos is None:
            return ExecutionResult(False, f"no open position with ticket {ticket}")

        if instruction not in ALLOWED_INSTRUCTIONS:
            self.journal.record_event(
                now, day, "anomaly", f"{source} sent unknown instruction {instruction!r} for {ticket}"
            )
            return ExecutionResult(False, f"instruction {instruction!r} is not permitted")

        if instruction == HOLD:
            return ExecutionResult(True, "hold", ticket)

        if instruction == TIGHTEN_STOP:
            if new_sl is None:
                return ExecutionResult(False, "TIGHTEN_STOP needs new_sl")
            spec = self.client.symbol_spec()
            new_sl = spec.normalize_price(new_sl)
            if not self._is_tighter(pos, new_sl):
                self.journal.record_event(
                    now, day, "anomaly",
                    f"{source} asked to WIDEN stop on {ticket} from {pos.sl:.2f} to {new_sl:.2f} — refused",
                )
                return ExecutionResult(False, "refused: that would widen the stop")
            ok = self._set_stop(pos, new_sl)
            if ok:
                self.journal.record_event(now, day, "manage", f"ticket {ticket}: {source} tightened stop to {new_sl:.2f}")
            return ExecutionResult(ok, "stop tightened" if ok else "stop modify failed", ticket)

        if instruction == PARTIAL_CLOSE:
            if fraction is None or not 0.0 < fraction < 1.0:
                return ExecutionResult(False, "PARTIAL_CLOSE needs a fraction in (0, 1)")
            spec = self.client.symbol_spec()
            volume = spec.normalize_volume(pos.volume * fraction)
            if volume < spec.volume_min or volume >= pos.volume:
                return ExecutionResult(False, f"partial volume {volume:g} is not closable")
            return self.close(ticket, f"{source} partial close {fraction:.0%}", now, day, volume=volume)

        return self.close(ticket, f"{source} EXIT_NOW", now, day)

    # ------------------------------------------------------------------
    # Exits
    # ------------------------------------------------------------------

    def close(
        self, ticket: int, reason: str, now: datetime, day: date, volume: float | None = None
    ) -> ExecutionResult:
        pos = next((p for p in self.positions() if p.ticket == ticket), None)
        if pos is None:
            return ExecutionResult(False, f"no open position with ticket {ticket}")
        tick = self.client.get_tick()
        close_volume = volume if volume is not None else pos.volume

        if pos.paper:
            exit_price = tick.bid if pos.direction == "buy" else tick.ask
            pnl = self._pnl(pos, exit_price, close_volume)
            if close_volume >= pos.volume:
                self._paper.remove(pos)
            else:
                pos.volume -= close_volume
            self.journal.record_close(ticket, now, day, close_volume, exit_price, pnl, reason, dry_run=True)
            log.info("[DRY RUN] closed %.2f of %s @ %.2f pnl %+.2f (%s)", close_volume, ticket, exit_price, pnl, reason)
            return ExecutionResult(True, reason, ticket, exit_price, close_volume, pnl, dry_run=True)

        result = self.client.close_position(ticket, volume=close_volume)
        if not result.ok:
            self.journal.record_event(now, day, "error", f"close of {ticket} failed: {result.reason}")
            return ExecutionResult(False, f"close failed: {result.reason}", ticket)
        exit_price = float(result.price) if result.price else (tick.bid if pos.direction == "buy" else tick.ask)
        filled = float(result.volume or close_volume)
        pnl = self._pnl(pos, exit_price, filled)
        self.journal.record_close(ticket, now, day, filled, exit_price, pnl, reason, dry_run=False)
        if filled >= pos.volume:
            self._live.pop(ticket, None)
        return ExecutionResult(True, reason, ticket, exit_price, filled, pnl)

    def flatten(self, reason: str, now: datetime, day: date) -> list[ExecutionResult]:
        """Close everything, then confirm nothing is left."""
        results = [self.close(p.ticket, reason, now, day) for p in self.positions()]
        remaining = self.positions()
        if remaining:
            self.journal.record_event(
                now, day, "error", f"flatten '{reason}' left {len(remaining)} position(s) open"
            )
        else:
            self.journal.record_event(now, day, "flatten", f"{reason}: flat, {len(results)} closed")
        return results

    def mark_paper(self, tick: Tick, now: datetime, day: date) -> list[ExecutionResult]:
        """Dry-run only: close paper positions whose stop or target the tick has crossed.

        The broker does this server-side for live positions; paper positions
        need the executor to do it, or dry-run would never lose a trade.
        Stop is checked before target, matching the backtester's pessimism.
        """
        closed: list[ExecutionResult] = []
        for pos in list(self._paper):
            mark = tick.bid if pos.direction == "buy" else tick.ask
            hit_sl = mark <= pos.sl if pos.direction == "buy" else mark >= pos.sl
            hit_tp = mark >= pos.tp if pos.direction == "buy" else mark <= pos.tp
            if hit_sl or hit_tp:
                label = "stop" if hit_sl else "target"
                if hit_sl and pos.sl != pos.initial_sl:
                    label = "breakeven_stop" if pos.sl == pos.entry else "trailing_stop"
                closed.append(self.close(pos.ticket, label, now, day))
        return closed

    def _pnl(self, pos: ManagedPosition, exit_price: float, volume: float) -> float:
        spec = self.client.symbol_spec()
        move = exit_price - pos.entry if pos.direction == "buy" else pos.entry - exit_price
        return move / spec.point * spec.value_per_point_per_lot * volume
