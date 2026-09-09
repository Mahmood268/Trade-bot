"""The engine — one cycle at a time, driven by the clock and the phase.

Each cycle asks, in this order:

1. Is the terminal still there? (watchdog — reconnect or stand down)
2. Did the operator say anything on Telegram? (pause / resume / flat / status)
3. What phase is it? → what is permitted right now
4. Must we flatten? (daily close, weekend, /flat, halt)
5. Are there positions to manage? (break-even, trailing; paper stops in dry-run)
6. Has a new bar closed, and may we open? → strategy → gates → warden → executor

Everything that decides money is somewhere else — the strategy proposes, the
Risk Warden sizes and vetoes, the executor acts. The engine's job is sequence
and gating, and to make sure that whatever happens, it is written down.

``cycle()`` is a pure function of the injected clock, so the whole loop is
testable against a fake terminal at any minute of any day without sleeping.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from goldbot import indicators
from goldbot.calendar import Calendar
from goldbot.executor import Executor
from goldbot.journal import Journal
from goldbot.mt5_client import MT5Client, MT5Error
from goldbot.notifier import Telegram
from goldbot.risk import RiskState, RiskWarden
from goldbot.routine import DailyRoutine, Phase
from goldbot.sessions import SessionGate
from goldbot.strategy.base import Strategy

log = logging.getLogger(__name__)


@dataclass
class CycleReport:
    """What one cycle did — returned for tests and logged at DEBUG."""

    now: datetime
    phase: Phase
    new_bar: bool = False
    signal: bool = False
    opened: bool = False
    closed: int = 0
    managed: list[str] = field(default_factory=list)
    skipped: str | None = None
    notes: list[str] = field(default_factory=list)


class Engine:
    def __init__(
        self,
        cfg,
        client: MT5Client,
        journal: Journal,
        notifier: Telegram,
        calendar: Calendar,
        strategy: Strategy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        from goldbot.strategy.trend_pullback import TrendPullback

        self.cfg = cfg
        self.client = client
        self.journal = journal
        self.notifier = notifier
        self.calendar = calendar
        self.strategy = strategy or TrendPullback(cfg.strategy)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

        self.dry_run = not cfg.live_orders_armed
        self.executor = Executor(client, cfg, journal, dry_run=self.dry_run)
        self.warden = RiskWarden(cfg.risk)
        self.gate = SessionGate(cfg.sessions)
        self.routine = DailyRoutine(cfg.sessions, cfg.routine)

        self.paused = False
        self.halt_reason: str | None = None
        self._last_bar_time = None
        self._last_phase: Phase | None = None
        self._day = None
        self._day_start_equity: float | None = None
        self._flattened_today = False
        self._latest_atr = 0.0
        self._warmup = indicators.warmup_bars(cfg.strategy)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect, adopt, announce. Called once."""
        now = self.clock()
        day = self.gate.local(now).date()
        info = self.client.connect()
        self.client.discover_symbol()
        self.calendar.refresh()
        self._roll_day(now, info.equity)
        adopted = self.executor.adopt(now, day)
        mode = "DRY RUN — no orders will be sent" if self.dry_run else "LIVE — real orders"
        msg = (
            f"goldbot started ({mode})\n"
            f"account {info.login} @ {info.server}: balance {info.balance:.2f} {info.currency}\n"
            f"symbol {self.client.symbol}, timeframe {self.cfg.strategy.timeframe}\n"
            f"adopted {len(adopted)} open position(s)"
        )
        log.info(msg.replace("\n", " | "))
        self.notifier.send(msg)
        self.journal.record_event(now, day, "start", msg)

    def run(self, max_cycles: int | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        """The loop. Sleeps until the next bar close or phase boundary."""
        cycles = 0
        try:
            while max_cycles is None or cycles < max_cycles:
                report = self.cycle()
                cycles += 1
                sleep(self._sleep_seconds(report))
        except KeyboardInterrupt:
            log.info("interrupted by operator")
        finally:
            self.stop()

    def stop(self) -> None:
        now = self.clock()
        day = self.gate.local(now).date()
        msg = "goldbot stopped" + (
            f" — {len(self.executor.positions())} position(s) remain open with server-side stops"
            if not self.dry_run and self.executor.positions() else ""
        )
        self.journal.record_event(now, day, "stop", msg)
        self.notifier.send(msg)
        self.client.shutdown()

    # ------------------------------------------------------------------
    # One cycle
    # ------------------------------------------------------------------

    def cycle(self) -> CycleReport:
        now = self.clock()
        local = self.gate.local(now)
        day = local.date()
        phase = self.routine.phase_at(now)
        report = CycleReport(now=now, phase=phase.phase)

        # 1. Watchdog. A dead terminal means no prices and no ability to act.
        if not self.client.is_connected():
            report.skipped = "terminal not connected"
            self._reconnect(now, day)
            return report

        # Day rollover: open the day's record with start-of-day equity.
        if day != self._day:
            self._roll_day(now, self.client.account_info().equity)

        # 2. Operator commands.
        for cmd in self.notifier.poll_commands():
            self._handle_command(cmd, now, day, phase)

        # 3. Phase transitions are journalled once, not every cycle.
        if phase.phase != self._last_phase:
            self.journal.record_event(now, day, "phase", f"{phase.phase.value}: {phase.reason}")
            # Visible in the log too: watching a live run, the phase is the
            # single most useful thing to know, and "why is it not trading?"
            # is usually answered by it.
            log.info("phase -> %s (%s)", phase.phase.value.upper(), phase.reason)
            self._last_phase = phase.phase
            if phase.phase in (Phase.PREFLIGHT, Phase.DEBRIEF):
                report.notes.append(f"{phase.phase.value} phase — agents run here in Stage B")

        perms = phase.permissions
        tick = self.client.get_tick()
        positions = self.executor.positions()

        # 4. Flatten: daily close, weekend, or a halt that closes everything.
        flatten = self.gate.must_flatten(now)
        if positions and (flatten.allowed or perms.flatten):
            reason = "eod_flatten" if flatten.kind == "daily" else ("weekend_flatten" if flatten.kind else "phase_flatten")
            results = self.executor.flatten(reason, now, day)
            report.closed += sum(1 for r in results if r.ok)
            pnl = sum(r.pnl or 0.0 for r in results if r.ok)
            self.notifier.send(f"Flattened {len(results)} position(s) — {reason}. Day P&L so far {self._today_pnl(day):+.2f}")
            if flatten.kind == "daily" and not self._flattened_today:
                self._flattened_today = True
                self.journal.close_day(day, self.client.account_info().equity)
            return report

        # 5. Manage open positions.
        if positions and (perms.manage or perms.tighten_only):
            if self.dry_run:
                for r in self.executor.mark_paper(tick, now, day):
                    if r.ok:
                        report.closed += 1
                        self.notifier.send(self._close_text(r))
            for pos in self.executor.positions():
                actions = self.executor.manage(pos, tick, self._latest_atr, now, day)
                report.managed.extend(actions)
            self._check_halts(now, day)

        # 6. New bar → maybe a new trade.
        bars = self._closed_bars()
        if bars is None:
            report.skipped = "no bars"
            return report
        bar_time = bars.index[-1]
        if bar_time != self._last_bar_time:
            self._last_bar_time = bar_time
            report.new_bar = True
            data = indicators.compute(bars, self.cfg.strategy)
            self._latest_atr = float(data["atr"].iloc[-1]) if data["atr"].notna().iloc[-1] else 0.0
            info = self.client.account_info()
            self.journal.record_equity(now, day, info.balance, info.equity, len(self.executor.positions()))

            if not perms.open_new:
                report.skipped = f"phase {phase.phase.value}: no new entries"
                return report
            if self.paused:
                report.skipped = "paused by operator"
                return report

            signal = self.strategy.evaluate(data, len(data) - 1)
            if signal is None:
                return report
            report.signal = True
            signal_id = self.journal.record_signal(signal, now, day, phase.phase.value)

            verdict = self._gates(now)
            if verdict is not None:
                self.journal.record_decision(signal_id, now, "gate", False, verdict)
                report.skipped = verdict
                return report

            state = self._risk_state(day)
            decision = self.warden.evaluate(signal, self.client.symbol_spec(), state)
            self.journal.record_decision(
                signal_id, now, "warden", decision.approved, decision.reason,
                decision.volume, decision.risk_amount, 1.0, decision.notes,
            )
            if not decision.approved:
                report.skipped = decision.reason
                if "halted" in decision.reason and self.halt_reason is None:
                    self._halt(now, day, decision.reason)
                return report

            result = self.executor.open(signal, decision, now, day, signal_id)
            report.opened = result.ok
            if result.ok:
                self.notifier.send(
                    f"{'[DRY RUN] ' if result.dry_run else ''}OPEN {signal.direction.upper()} "
                    f"{result.volume:g} lots @ {result.price:.2f}\n"
                    f"stop {signal.stop:.2f}  target {signal.target:.2f}  risk {decision.risk_amount:.2f}\n"
                    f"{signal.reason}"
                )
            else:
                report.skipped = result.reason
                self.notifier.send(f"Entry FAILED: {result.reason}")
        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _closed_bars(self):
        try:
            return self.client.get_closed_bars(self.cfg.strategy.timeframe, self._warmup + 5)
        except MT5Error as exc:
            log.warning("bars unavailable: %s", exc)
            return None

    def _gates(self, now: datetime) -> str | None:
        """Deterministic pre-checks before the warden. None = pass."""
        session = self.gate.can_enter(now)
        if not session.allowed:
            return f"session: {session.reason}"
        blackout = self.calendar.blackout_at(now)
        if blackout.blocked:
            return f"calendar: {blackout.reason}"
        return None

    def _risk_state(self, day) -> RiskState:
        info = self.client.account_info()
        stats = self.journal.day_stats(day)
        positions = self.executor.positions()
        spec = self.client.symbol_spec()
        open_risk = 0.0
        for p in positions:
            dist = p.entry - p.sl if p.direction == "buy" else p.sl - p.entry
            open_risk += max(0.0, dist / spec.point * spec.value_per_point_per_lot * p.volume)
        return RiskState(
            equity=info.equity,
            balance=info.balance,
            starting_balance=self._starting_balance(info.balance),
            day_start_equity=self._day_start_equity or info.equity,
            realised_pnl_today=stats.realised_pnl,
            trades_today=stats.trades,
            consecutive_losses=stats.consecutive_losses,
            open_positions=len(positions),
            open_risk_amount=open_risk,
            spread_points=self.client.spread_points(),
            margin_free=info.margin_free,
            halted=self.halt_reason is not None,
            halt_reason=self.halt_reason or "",
        )

    def _starting_balance(self, fallback: float) -> float:
        row = self.journal.last_event("start")
        first = self.journal._db.execute("SELECT start_equity FROM days ORDER BY day LIMIT 1").fetchone()
        return float(first["start_equity"]) if first else fallback

    def _roll_day(self, now: datetime, equity: float) -> None:
        day = self.gate.local(now).date()
        self._day = day
        self._day_start_equity = self.journal.open_day(day, equity)
        self._flattened_today = False
        # A daily-loss halt clears with the day; a consecutive-loss halt does not.
        if self.halt_reason and "daily loss" in self.halt_reason:
            self.halt_reason = None
            self.journal.record_event(now, day, "resume", "new trading day — daily-loss halt cleared")
        self.calendar.refresh()

    def _check_halts(self, now: datetime, day) -> None:
        if self.halt_reason:
            return
        reason = self.warden.halt_reason(self._risk_state(day))
        if reason:
            self._halt(now, day, reason)

    def _halt(self, now: datetime, day, reason: str) -> None:
        self.halt_reason = reason
        self.journal.record_event(now, day, "halt", reason)
        self.notifier.send(f"⛔ HALT: {reason}")
        if "daily loss" in reason or "equity floor" in reason:
            self.executor.flatten("halt", now, day)

    def _reconnect(self, now: datetime, day) -> None:
        log.warning("terminal not connected — attempting reconnect")
        try:
            self.client.connect()
            self.journal.record_event(now, day, "reconnect", "terminal reconnected")
        except MT5Error as exc:
            self.journal.record_event(now, day, "error", f"reconnect failed: {exc}")
            self.notifier.send(f"⚠️ MT5 terminal unreachable: {exc}")

    def _handle_command(self, cmd, now: datetime, day, phase) -> None:
        if cmd.name == "help":
            self.notifier.send(self.notifier.help_text())
        elif cmd.name == "status":
            self.notifier.send(self.status_text(now, phase))
        elif cmd.name == "pnl":
            stats = self.journal.day_stats(day)
            self.notifier.send(f"Today: {stats.realised_pnl:+.2f} over {stats.trades} trade(s); agent cost {stats.agent_cost_usd:.2f}")
        elif cmd.name == "pause":
            self.paused = True
            self.journal.record_event(now, day, "pause", "operator /pause")
            self.notifier.send("Paused. No new entries; open positions keep their stops. /resume to continue.")
        elif cmd.name == "resume":
            self.paused = False
            cleared = self.halt_reason
            self.halt_reason = None
            self.journal.record_event(now, day, "resume", f"operator /resume (cleared: {cleared or 'nothing'})")
            self.notifier.send("Resumed." + (f" Cleared halt: {cleared}" if cleared else ""))
        elif cmd.name == "flat":
            results = self.executor.flatten("operator /flat", now, day)
            self.paused = True
            self.notifier.send(f"Closed {sum(1 for r in results if r.ok)} position(s). Paused — /resume to trade again.")

    def status_text(self, now: datetime, phase) -> str:
        info = self.client.account_info()
        positions = self.executor.positions()
        lines = [
            f"{'DRY RUN' if self.dry_run else 'LIVE'} · {phase.phase.value} · {phase.reason}",
            f"equity {info.equity:.2f}  balance {info.balance:.2f}",
            f"paused: {self.paused}  halt: {self.halt_reason or 'none'}",
            f"open: {len(positions)}",
        ]
        for p in positions:
            lines.append(f"  #{p.ticket} {p.direction} {p.volume:g} @ {p.entry:.2f} sl {p.sl:.2f} tp {p.tp:.2f}")
        return "\n".join(lines)

    def _today_pnl(self, day) -> float:
        return self.journal.day_stats(day).realised_pnl

    @staticmethod
    def _close_text(r) -> str:
        return f"CLOSED #{r.ticket} {r.volume:g} lots @ {r.price:.2f}  pnl {r.pnl:+.2f}  ({r.reason})"

    def _sleep_seconds(self, report: CycleReport) -> float:
        """Poll every few seconds while positions are open; otherwise until the next event."""
        if self.executor.positions():
            return 5.0
        nxt = self.routine.next_boundary(report.now)
        until = (nxt - self.gate.local(report.now)).total_seconds()
        # But never sleep through a bar close.
        return max(5.0, min(until, 60.0))
