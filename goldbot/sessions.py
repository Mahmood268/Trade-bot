"""When the bot is allowed to trade.

Gold trades nearly around the clock, but the hours are not equal: the Asian
session is thin and wide-spread, and a pullback strategy pays the spread on
every entry. This module answers two questions, and both the live engine and the
backtester ask exactly this code — a session filter that differs between
backtest and live is a silent way to make results unreproducible.

    * :meth:`SessionGate.can_enter`  — may a NEW position be opened now?
    * :meth:`SessionGate.must_flatten` — must OPEN positions be closed now?

All configured times are wall-clock in ``sessions.timezone`` (default
Europe/London), so the windows follow British Summer Time automatically rather
than drifting by an hour twice a year. Timestamps handed in are expected to be
timezone-aware UTC, as everything from MT5 is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

FRIDAY = 4


@dataclass(frozen=True)
class SessionVerdict:
    """Why the gate said what it said — journalled next to every skipped signal."""

    allowed: bool
    window: str | None
    reason: str


class SessionGate:
    def __init__(self, cfg) -> None:
        """
        Args:
            cfg: a ``SessionConfig`` (see goldbot.config).
        """
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)

    def local(self, ts: datetime) -> datetime:
        """Convert an aware timestamp into configured local time.

        A naive timestamp is assumed to be UTC — that is what every MT5 call in
        this codebase returns, and guessing the machine's local zone instead
        would move the session windows on a traveller's laptop.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(self.tz)

    def window_at(self, ts: datetime) -> str | None:
        """Name of the trading window containing ``ts``, or None."""
        local = self.local(ts)
        now = local.time()
        for window in self.cfg.windows:
            if window.start_time <= now < window.end_time:
                return window.name
        return None

    def can_enter(self, ts: datetime) -> SessionVerdict:
        """May a new position be opened at this moment?"""
        local = self.local(ts)

        if local.weekday() not in self.cfg.trade_days:
            return SessionVerdict(False, None, f"{local:%A} is not a configured trading day")

        if self._past_friday_close(local):
            return SessionVerdict(
                False, None, f"past Friday close ({self.cfg.friday_close} {self.cfg.timezone})"
            )

        window = self.window_at(ts)
        if window is None:
            hhmm = f"{local:%H:%M}"
            windows = ", ".join(f"{w.name} {w.start}-{w.end}" for w in self.cfg.windows)
            return SessionVerdict(
                False, None, f"{hhmm} {self.cfg.timezone} is outside the trading windows ({windows})"
            )

        return SessionVerdict(True, window, f"in {window} session")

    def must_flatten(self, ts: datetime) -> SessionVerdict:
        """Must open positions be closed now?

        Only the weekend rule lives here. Gold gaps over the weekend on news the
        market never got to price, and a stop cannot protect against a gap — it
        becomes a market order at whatever Monday opens at. Closing on Friday
        turns an unbounded risk into a known cost.
        """
        if not self.cfg.flatten_before_weekend:
            return SessionVerdict(False, None, "weekend flattening disabled")

        local = self.local(ts)
        if local.weekday() == FRIDAY and self._past_friday_close(local):
            return SessionVerdict(
                True,
                None,
                f"Friday {local:%H:%M} is past the {self.cfg.friday_close} close — "
                "flattening to avoid weekend gap risk",
            )
        if local.weekday() > FRIDAY:
            return SessionVerdict(True, None, f"{local:%A} — market weekend")
        return SessionVerdict(False, None, "not the weekend boundary")

    def _past_friday_close(self, local: datetime) -> bool:
        if local.weekday() != FRIDAY:
            return False
        hour, minute = self.cfg.friday_close.split(":")
        return local.time() >= dt_time(int(hour), int(minute))
