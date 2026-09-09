"""The daily routine — which phase the trading day is in, and what it permits.

The day is a fixed sequence of phases keyed to the London open. Phases decide
what is *permitted*; within a phase the engine is still event-driven. Two
things follow from making the schedule explicit rather than reactive:

* The number of agent calls per day becomes predictable. A reactive design
  bills you in proportion to how noisy the market is; a routine bills you for
  the phases you configured.
* "What is the bot allowed to do right now?" has one unambiguous answer at
  every moment, computed from the clock — not from the state of a long-running
  loop that may have been restarted at 14:07.

    PRE-FLIGHT  the Session Supervisor sets the day plan       (07:30)
    HUNT        entries permitted, inside a session window     (08:00-12:00)
    HOLD        between windows: manage only, no new entries   (12:00-13:30)
    HUNT        second window                                  (13:30-17:00)
    WIND_DOWN   after the last window: tighten only            (17:00-19:30)
    FLATTEN     close everything, confirm flat                 (19:30)
    DEBRIEF     the Day Auditor reconciles and reports         (19:45)
    CLOSED      nothing runs                                   (until pre-flight)
    WEEKEND     nothing runs                                   (Sat/Sun)

Like the session gate, this is deterministic and shared by the live engine and
the backtester. Phase boundaries are a lookup, not a judgement, so they are not
an agent's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from goldbot.sessions import FRIDAY, SessionGate


class Phase(str, Enum):
    PREFLIGHT = "preflight"
    HUNT = "hunt"
    HOLD = "hold"
    WIND_DOWN = "wind_down"
    FLATTEN = "flatten"
    DEBRIEF = "debrief"
    CLOSED = "closed"
    WEEKEND = "weekend"


@dataclass(frozen=True)
class Permissions:
    """What the engine may do in a phase. Everything defaults to forbidden."""

    open_new: bool = False        # place a new entry
    manage: bool = False          # run the Trade Manager on open positions
    tighten_only: bool = False    # manage, but only stop-tightening and exits
    flatten: bool = False         # close every position now
    run_preflight: bool = False   # Session Supervisor + News Scout + Regime Analyst
    run_debrief: bool = False     # Day Auditor


PERMISSIONS: dict[Phase, Permissions] = {
    Phase.PREFLIGHT: Permissions(run_preflight=True),
    Phase.HUNT: Permissions(open_new=True, manage=True),
    Phase.HOLD: Permissions(manage=True),
    Phase.WIND_DOWN: Permissions(manage=True, tighten_only=True),
    Phase.FLATTEN: Permissions(flatten=True),
    Phase.DEBRIEF: Permissions(run_debrief=True),
    Phase.CLOSED: Permissions(),
    Phase.WEEKEND: Permissions(),
}


@dataclass(frozen=True)
class PhaseVerdict:
    phase: Phase
    window: str | None
    reason: str

    @property
    def permissions(self) -> Permissions:
        return PERMISSIONS[self.phase]


class DailyRoutine:
    def __init__(self, sessions_cfg, routine_cfg) -> None:
        """
        Args:
            sessions_cfg: a ``SessionConfig``.
            routine_cfg: a ``RoutineConfig``.
        """
        self.sessions = sessions_cfg
        self.routine = routine_cfg
        self.gate = SessionGate(sessions_cfg)

    def phase_at(self, ts: datetime) -> PhaseVerdict:
        """Which phase contains ``ts``, and why."""
        local = self.gate.local(ts)
        now = local.time()
        hhmm = f"{local:%H:%M}"

        if local.weekday() not in self.sessions.trade_days or local.weekday() > FRIDAY:
            return PhaseVerdict(Phase.WEEKEND, None, f"{local:%A} is not a trading day")

        if not self.routine.enabled:
            return self._without_routine(ts, local)

        preflight = self.routine.preflight_time
        first_open = self.sessions.first_open_time
        close = self.sessions.daily_close_time
        debrief_start = _add_minutes(close, self.routine.debrief_after_min)
        # The debrief is a single run; give it a window as long as its lead so a
        # restart inside that window still runs it rather than skipping the day.
        debrief_end = _add_minutes(debrief_start, self.routine.debrief_after_min)

        if now < preflight:
            return PhaseVerdict(Phase.CLOSED, None, f"{hhmm} is before pre-flight at {self.routine.preflight}")
        if now < first_open:
            return PhaseVerdict(Phase.PREFLIGHT, None, f"{hhmm}: pre-flight, before the {first_open:%H:%M} open")

        window = self.gate.window_at(ts)
        if window is not None:
            return PhaseVerdict(Phase.HUNT, window, f"{hhmm}: in the {window} window")

        last_end = max(w.end_time for w in self.sessions.windows)
        if now < last_end:
            return PhaseVerdict(Phase.HOLD, None, f"{hhmm}: between windows — manage only")
        if now < close:
            return PhaseVerdict(
                Phase.WIND_DOWN, None, f"{hhmm}: after the last window, before the {close:%H:%M} close"
            )
        if now < debrief_start:
            return PhaseVerdict(Phase.FLATTEN, None, f"{hhmm}: at the {close:%H:%M} close — flatten")
        if now < debrief_end:
            return PhaseVerdict(Phase.DEBRIEF, None, f"{hhmm}: debrief")
        return PhaseVerdict(Phase.CLOSED, None, f"{hhmm}: day complete")

    def _without_routine(self, ts: datetime, local: datetime) -> PhaseVerdict:
        """Routine disabled: only the session windows and the flatten rule apply."""
        hhmm = f"{local:%H:%M}"
        if self.gate.must_flatten(ts).allowed:
            return PhaseVerdict(Phase.FLATTEN, None, f"{hhmm}: flatten boundary")
        window = self.gate.window_at(ts)
        if window is not None:
            return PhaseVerdict(Phase.HUNT, window, f"{hhmm}: in the {window} window")
        return PhaseVerdict(Phase.HOLD, None, f"{hhmm}: outside the windows — manage only")

    def next_boundary(self, ts: datetime) -> datetime:
        """The next instant at which the phase changes.

        Lets the engine sleep until something is allowed to happen instead of
        polling — the cheapest agent call is the one that is never made.
        """
        local = self.gate.local(ts)
        candidates = [self.routine.preflight_time, self.sessions.first_open_time, self.sessions.daily_close_time]
        for window in self.sessions.windows:
            candidates.extend((window.start_time, window.end_time))
        candidates.append(_add_minutes(self.sessions.daily_close_time, self.routine.debrief_after_min))
        candidates.append(_add_minutes(self.sessions.daily_close_time, 2 * self.routine.debrief_after_min))

        today = [local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0) for t in candidates]
        future = sorted(t for t in today if t > local)
        if future:
            return future[0]
        tomorrow = (local + timedelta(days=1)).replace(
            hour=self.routine.preflight_time.hour,
            minute=self.routine.preflight_time.minute,
            second=0,
            microsecond=0,
        )
        return tomorrow


def _add_minutes(t, minutes: int):
    base = datetime(2000, 1, 1, t.hour, t.minute) + timedelta(minutes=minutes)
    return base.time()
