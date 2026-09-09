"""Liveness monitoring: heartbeats, a watchdog, and an on-demand report.

Three separate questions, deliberately kept apart:

* **Is Telegram reachable?**  A connectivity check - ``python -m trade_bot
  --healthcheck`` answers it with an exit code, for Docker or an uptime monitor.
* **Is the trading loop still turning?**  The watchdog. Your loop calls
  :meth:`HealthMonitor.beat` each pass; if the beats stop, you get alerted.
* **How is everything doing right now?**  :meth:`HealthMonitor.snapshot`, sent
  on demand by the ``/health`` command or on a schedule by the heartbeat.

Defaults are chosen so the bot is quiet when healthy: the watchdog is on
(it only speaks when something is wrong) and the periodic heartbeat is off
(it is recurring noise, so you opt in).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from trade_bot.formatting import esc

log = logging.getLogger(__name__)

ENV_HEARTBEAT = "HEALTH_HEARTBEAT_INTERVAL"
ENV_WATCHDOG = "HEALTH_WATCHDOG_TIMEOUT"
ENV_HTTP_PORT = "HEALTH_HTTP_PORT"
ENV_HTTP_HOST = "HEALTH_HTTP_HOST"

#: A check returns True/False, ``(ok, detail)``, or raises to signal failure.
Check = Callable[[], "bool | tuple[bool, str]"]


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(max(0, seconds))
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3_600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


@dataclass(frozen=True)
class HealthConfig:
    """How loudly and how often the monitor reports."""

    #: Seconds between "still alive" messages. 0 disables them.
    heartbeat_interval: int = 0
    #: Alert if the trading loop has not called ``beat()`` for this long.
    #: 0 disables the watchdog.
    watchdog_timeout: int = 300
    #: Serve ``/healthz`` over HTTP on this port, for Docker or an uptime
    #: monitor. ``None`` disables the server.
    http_port: int | None = None
    #: Bind address. Loopback by default - do not expose this without a proxy.
    http_host: str = "127.0.0.1"
    #: Announce startup, and announce recovery after a watchdog alert.
    notify_on_start: bool = True
    notify_on_recovery: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "HealthConfig":
        env = os.environ if env is None else env

        def integer(name: str, default: int) -> int:
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                log.warning("%s=%r is not a number - using %d", name, raw, default)
                return default

        port = integer(ENV_HTTP_PORT, 0)
        return cls(
            heartbeat_interval=max(0, integer(ENV_HEARTBEAT, 0)),
            watchdog_timeout=max(0, integer(ENV_WATCHDOG, 300)),
            http_port=port or None,
            http_host=(env.get(ENV_HTTP_HOST) or "127.0.0.1").strip(),
        )


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True)
class HealthReport:
    """The answer to "how are you?", renderable as HTML or JSON."""

    ok: bool
    checks: tuple[CheckResult, ...] = ()
    uptime_seconds: float = 0.0
    last_beat_age: float | None = None
    beats: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "unhealthy",
            "uptime_seconds": round(self.uptime_seconds, 1),
            "uptime": _fmt_duration(self.uptime_seconds),
            "last_beat_age_seconds": (
                None if self.last_beat_age is None else round(self.last_beat_age, 1)
            ),
            "beats": self.beats,
            "note": self.note,
            "checks": [
                {
                    "name": check.name,
                    "status": "ok" if check.ok else "failed",
                    "detail": check.detail,
                    "duration_ms": round(check.duration_ms, 1),
                }
                for check in self.checks
            ],
        }

    def to_message(self) -> str:
        """Render as a Telegram HTML message."""
        headline = "\U0001F49A <b>Healthy</b>" if self.ok else "\U0001F534 <b>Unhealthy</b>"
        lines = [headline, ""]
        lines.append(f"Uptime: <code>{esc(_fmt_duration(self.uptime_seconds))}</code>")
        if self.last_beat_age is not None:
            lines.append(f"Last tick: <code>{esc(_fmt_duration(self.last_beat_age))} ago</code>")
            lines.append(f"Ticks: <code>{esc(self.beats)}</code>")
        if self.note:
            lines.append(f"Note: {esc(self.note)}")
        if self.checks:
            lines.append("")
            for check in self.checks:
                icon = "✅" if check.ok else "❌"
                detail = f" - {esc(check.detail)}" if check.detail else ""
                lines.append(f"{icon} {esc(check.name)}{detail}")
        return "\n".join(lines)


class HealthMonitor:
    """Watches the trading loop and reports on it through Telegram.

    ``clock`` is injectable so tests do not have to wait in real time.
    """

    def __init__(
        self,
        bot: Any = None,
        config: HealthConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bot = bot
        self.config = config or HealthConfig()
        self._clock = clock
        self._started_at = clock()
        self._checks: dict[str, Check] = {}
        self._lock = threading.Lock()
        self._last_beat: float | None = None
        self._beat_note: str = ""
        self._beats = 0
        self._watchdog_fired = False
        self._last_heartbeat = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        if bot is not None:
            self.register("telegram api", self._check_telegram)

    # -- checks -----------------------------------------------------------

    def register(self, name: str, check: Check) -> None:
        """Add a named check. Later registrations replace earlier ones."""
        self._checks[name] = check

    def _check_telegram(self) -> tuple[bool, str]:
        me = self.bot.client.get_me()
        return True, f"@{me.get('username')}"

    def run_checks(self) -> tuple[CheckResult, ...]:
        """Run every registered check, converting exceptions into failures."""
        results = []
        for name, check in list(self._checks.items()):
            started = time.monotonic()
            try:
                outcome = check()
                if isinstance(outcome, tuple):
                    ok, detail = bool(outcome[0]), str(outcome[1])
                else:
                    ok, detail = bool(outcome), ""
            except Exception as exc:  # noqa: BLE001 - a check must never propagate
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            results.append(
                CheckResult(name, ok, detail, (time.monotonic() - started) * 1000)
            )
        return tuple(results)

    # -- heartbeat from the trading loop ----------------------------------

    def beat(self, note: str = "") -> None:
        """Record that the trading loop is still turning.

        Call this once per pass. Cheap, thread-safe, and never raises - it
        must be safe to sprinkle through a hot loop.
        """
        with self._lock:
            self._last_beat = self._clock()
            self._beats += 1
            if note:
                self._beat_note = note
            recovered = self._watchdog_fired
            self._watchdog_fired = False

        if recovered and self.config.notify_on_recovery:
            self._notify(
                "\U0001F49A <b>Recovered</b>\n\nThe trading loop is ticking again."
            )

    @property
    def last_beat_age(self) -> float | None:
        with self._lock:
            if self._last_beat is None:
                return None
            return self._clock() - self._last_beat

    @property
    def uptime(self) -> float:
        return self._clock() - self._started_at

    def snapshot(self, *, run_checks: bool = True) -> HealthReport:
        """Build a report. Checks may do I/O, so this can block briefly."""
        checks = self.run_checks() if run_checks else ()
        age = self.last_beat_age
        with self._lock:
            beats, note = self._beats, self._beat_note

        stale = (
            self.config.watchdog_timeout > 0
            and age is not None
            and age > self.config.watchdog_timeout
        )
        ok = all(check.ok for check in checks) and not stale
        if stale:
            note = f"no tick for {_fmt_duration(age)}"
        return HealthReport(
            ok=ok,
            checks=checks,
            uptime_seconds=self.uptime,
            last_beat_age=age,
            beats=beats,
            note=note,
        )

    # -- background loop --------------------------------------------------

    def tick(self) -> None:
        """One pass of the monitor: watchdog first, then heartbeat.

        Exposed so tests can drive it with a fake clock instead of waiting.
        """
        self._check_watchdog()
        self._maybe_heartbeat()

    def _check_watchdog(self) -> None:
        timeout = self.config.watchdog_timeout
        if timeout <= 0:
            return
        age = self.last_beat_age
        # The watchdog arms on the first beat, so a bot that never calls
        # beat() is never falsely reported as stalled.
        if age is None or age <= timeout:
            return
        with self._lock:
            if self._watchdog_fired:
                return  # already alerted; do not repeat every tick
            self._watchdog_fired = True
        log.error("watchdog: no tick for %.0fs", age)
        self._notify(
            "⚠️ <b>Trading loop stalled</b>\n\n"
            f"No tick for <code>{esc(_fmt_duration(age))}</code> "
            f"(limit {esc(_fmt_duration(timeout))})."
        )

    def _maybe_heartbeat(self) -> None:
        interval = self.config.heartbeat_interval
        if interval <= 0:
            return
        if self._clock() - self._last_heartbeat < interval:
            return
        self._last_heartbeat = self._clock()
        self._notify(self.snapshot().to_message(), silent=True)

    def _notify(self, text: str, *, silent: bool = False) -> None:
        if self.bot is None:
            return
        # A monitoring message must never take down what it is monitoring.
        self.bot.notify_safely(text, silent=silent)

    def _poll_interval(self) -> float:
        candidates = [float(self.config.watchdog_timeout or 0), float(self.config.heartbeat_interval or 0)]
        active = [value for value in candidates if value > 0]
        if not active:
            return 30.0
        return max(1.0, min(30.0, min(active) / 2))

    def start(self) -> threading.Thread:
        """Run the monitor on a daemon thread."""
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        if self.config.notify_on_start:
            self._notify(
                "\U0001F680 <b>Trade-bot started</b>\n\nMonitoring is active. "
                "Send /health for a status report.",
                silent=True,
            )
        interval = self._poll_interval()

        def run() -> None:
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - the monitor must outlive its checks
                    log.exception("health monitor tick failed")

        self._thread = threading.Thread(target=run, name="health-monitor", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def __enter__(self) -> "HealthMonitor":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # -- wiring -----------------------------------------------------------

    def attach(self, router: Any) -> Any:
        """Register the ``/health`` command on ``router``."""

        @router.command("health", "Bot health and uptime", aliases=("healthcheck",))
        def _health(context: Any) -> str:
            return self.snapshot().to_message()

        return router
