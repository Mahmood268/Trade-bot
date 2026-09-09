"""The journal — every signal, decision, order, fill and agent call, in SQLite.

Three jobs, in order of importance:

1. **Risk state.** The Risk Warden needs today's realised P&L, trade count and
   the current losing streak. Those come from here, not from memory, so a
   restart at 14:07 knows what happened at 09:30.
2. **Attribution.** Every veto is recorded next to what the trade would have
   done; every agent call next to what it cost. Without this the agents cannot
   be measured, and an agent that cannot be measured cannot be cut.
3. **The dashboard's data source**, later. The schema is queried from day one,
   so it is designed to be queried.

SQLite because the bot is one process on one machine, and a single file that
can be copied, opened and inspected beats a database server. ``":memory:"`` is
accepted for tests.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    phase       TEXT,
    direction   TEXT NOT NULL,
    ref_price   REAL NOT NULL,
    stop        REAL NOT NULL,
    target      REAL NOT NULL,
    atr         REAL,
    reason      TEXT,
    context     TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY,
    signal_id   INTEGER REFERENCES signals(id),
    ts          TEXT NOT NULL,
    source      TEXT NOT NULL,       -- 'gate' | 'warden' | agent name
    approved    INTEGER NOT NULL,
    reason      TEXT,
    volume      REAL,
    risk_amount REAL,
    multiplier  REAL,
    notes       TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY,
    signal_id   INTEGER REFERENCES signals(id),
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    ticket      INTEGER,
    direction   TEXT NOT NULL,
    volume      REAL NOT NULL,
    entry       REAL,
    sl          REAL,
    tp          REAL,
    risk_amount REAL,
    ok          INTEGER NOT NULL,
    retcode     INTEGER,
    comment     TEXT,
    dry_run     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS closes (
    id          INTEGER PRIMARY KEY,
    ticket      INTEGER,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    volume      REAL NOT NULL,
    exit_price  REAL,
    pnl         REAL NOT NULL,
    reason      TEXT,
    dry_run     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_calls (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    agent       TEXT NOT NULL,
    model       TEXT,
    ok          INTEGER NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd    REAL NOT NULL DEFAULT 0,
    latency_s   REAL,
    summary     TEXT,
    payload     TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    balance     REAL NOT NULL,
    equity      REAL NOT NULL,
    open_positions INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    kind        TEXT NOT NULL,       -- 'halt' | 'resume' | 'pause' | 'phase' | 'error' | 'flatten' | ...
    message     TEXT
);
CREATE TABLE IF NOT EXISTS days (
    day         TEXT PRIMARY KEY,
    start_equity REAL NOT NULL,
    end_equity  REAL,
    plan        TEXT,               -- Session Supervisor's plan, when enabled
    audit       TEXT                -- Day Auditor's record, when enabled
);
CREATE INDEX IF NOT EXISTS ix_closes_day ON closes(day);
CREATE INDEX IF NOT EXISTS ix_orders_day ON orders(day);
CREATE INDEX IF NOT EXISTS ix_agent_calls_day ON agent_calls(day);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, ts);
"""


@dataclass(frozen=True)
class DayStats:
    """What the Risk Warden needs to know about today."""

    day: str
    start_equity: float | None
    realised_pnl: float
    trades: int
    consecutive_losses: int
    agent_cost_usd: float


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------
    # Writes. Each takes the local trading day explicitly: the journal does
    # not know the session timezone, and guessing it is how a 23:30 trade
    # lands on the wrong day's P&L.
    # ------------------------------------------------------------------

    def record_signal(self, signal, ts: datetime, day: date, phase: str | None) -> int:
        cur = self._db.execute(
            "INSERT INTO signals (ts, day, phase, direction, ref_price, stop, target, atr, reason, context)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                _iso(ts), day.isoformat(), phase, signal.direction, signal.reference_price,
                signal.stop, signal.target, signal.atr, signal.reason,
                json.dumps(signal.context, sort_keys=True),
            ),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def record_decision(
        self,
        signal_id: int | None,
        ts: datetime,
        source: str,
        approved: bool,
        reason: str,
        volume: float | None = None,
        risk_amount: float | None = None,
        multiplier: float | None = None,
        notes: tuple[str, ...] | list[str] = (),
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO decisions (signal_id, ts, source, approved, reason, volume, risk_amount,"
            " multiplier, notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                signal_id, _iso(ts), source, int(approved), reason, volume, risk_amount,
                multiplier, json.dumps(list(notes)),
            ),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def record_order(
        self,
        signal_id: int | None,
        ts: datetime,
        day: date,
        direction: str,
        volume: float,
        entry: float | None,
        sl: float | None,
        tp: float | None,
        risk_amount: float | None,
        ok: bool,
        retcode: int | None,
        comment: str,
        ticket: int | None,
        dry_run: bool,
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO orders (signal_id, ts, day, ticket, direction, volume, entry, sl, tp,"
            " risk_amount, ok, retcode, comment, dry_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                signal_id, _iso(ts), day.isoformat(), ticket, direction, volume, entry, sl, tp,
                risk_amount, int(ok), retcode, comment, int(dry_run),
            ),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def record_close(
        self,
        ticket: int | None,
        ts: datetime,
        day: date,
        volume: float,
        exit_price: float | None,
        pnl: float,
        reason: str,
        dry_run: bool,
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO closes (ticket, ts, day, volume, exit_price, pnl, reason, dry_run)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ticket, _iso(ts), day.isoformat(), volume, exit_price, pnl, reason, int(dry_run)),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def record_agent_call(
        self,
        ts: datetime,
        day: date,
        agent: str,
        model: str | None,
        ok: bool,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float = 0.0,
        latency_s: float | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO agent_calls (ts, day, agent, model, ok, input_tokens, output_tokens,"
            " cost_usd, latency_s, summary, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                _iso(ts), day.isoformat(), agent, model, int(ok), input_tokens, output_tokens,
                cost_usd, latency_s, summary,
                json.dumps(payload, sort_keys=True, default=str) if payload is not None else None,
            ),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def record_equity(
        self, ts: datetime, day: date, balance: float, equity: float, open_positions: int
    ) -> None:
        self._db.execute(
            "INSERT INTO equity (ts, day, balance, equity, open_positions) VALUES (?,?,?,?,?)",
            (_iso(ts), day.isoformat(), balance, equity, open_positions),
        )
        self._db.commit()

    def record_event(self, ts: datetime, day: date, kind: str, message: str) -> None:
        self._db.execute(
            "INSERT INTO events (ts, day, kind, message) VALUES (?,?,?,?)",
            (_iso(ts), day.isoformat(), kind, message),
        )
        self._db.commit()

    def open_day(self, day: date, start_equity: float) -> float:
        """Record the day's opening equity once; return whichever value stands.

        Idempotent on purpose: a restart mid-day must not reset the daily loss
        budget to whatever equity happens to be at 14:07.
        """
        row = self._db.execute("SELECT start_equity FROM days WHERE day = ?", (day.isoformat(),)).fetchone()
        if row is not None:
            return float(row["start_equity"])
        self._db.execute(
            "INSERT INTO days (day, start_equity) VALUES (?, ?)", (day.isoformat(), start_equity)
        )
        self._db.commit()
        return start_equity

    def close_day(self, day: date, end_equity: float, audit: str | None = None) -> None:
        self._db.execute(
            "UPDATE days SET end_equity = ?, audit = COALESCE(?, audit) WHERE day = ?",
            (end_equity, audit, day.isoformat()),
        )
        self._db.commit()

    def set_day_plan(self, day: date, plan: str) -> None:
        self._db.execute("UPDATE days SET plan = ? WHERE day = ?", (plan, day.isoformat()))
        self._db.commit()

    # ------------------------------------------------------------------
    # Reads.
    # ------------------------------------------------------------------

    def day_stats(self, day: date) -> DayStats:
        d = day.isoformat()
        start = self._db.execute("SELECT start_equity FROM days WHERE day = ?", (d,)).fetchone()
        pnl = self._db.execute("SELECT COALESCE(SUM(pnl), 0) AS v FROM closes WHERE day = ?", (d,)).fetchone()["v"]
        trades = self._db.execute(
            "SELECT COUNT(*) AS v FROM orders WHERE day = ? AND ok = 1", (d,)
        ).fetchone()["v"]
        cost = self._db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS v FROM agent_calls WHERE day = ?", (d,)
        ).fetchone()["v"]
        return DayStats(
            day=d,
            start_equity=float(start["start_equity"]) if start else None,
            realised_pnl=float(pnl),
            trades=int(trades),
            consecutive_losses=self.consecutive_losses(),
            agent_cost_usd=float(cost),
        )

    def consecutive_losses(self) -> int:
        """Losing closes in a row, counted back from the most recent.

        A ``resume`` event resets the streak: that is what the operator's
        ``/resume`` after a five-loss halt means, and it is recorded so the
        reset is auditable rather than a number that changed in memory.
        """
        last_resume = self._db.execute(
            "SELECT ts FROM events WHERE kind = 'resume' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        since = last_resume["ts"] if last_resume else ""
        rows = self._db.execute(
            "SELECT pnl FROM closes WHERE ts > ? ORDER BY ts DESC, id DESC", (since,)
        ).fetchall()
        streak = 0
        for row in rows:
            if row["pnl"] <= 0:
                streak += 1
            else:
                break
        return streak

    def last_event(self, kind: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM events WHERE kind = ? ORDER BY ts DESC, id DESC LIMIT 1", (kind,)
        ).fetchone()

    def open_order_for_comment(self, comment: str) -> sqlite3.Row | None:
        """The order placed under a signal tag, if any — idempotency on retry."""
        return self._db.execute(
            "SELECT * FROM orders WHERE comment = ? AND ok = 1 ORDER BY id DESC LIMIT 1", (comment,)
        ).fetchone()

    def closes_for_day(self, day: date) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM closes WHERE day = ? ORDER BY ts", (day.isoformat(),)
        ).fetchall()

    def orders_for_day(self, day: date) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM orders WHERE day = ? ORDER BY ts", (day.isoformat(),)
        ).fetchall()

    def decisions_for_day(self, day: date) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT d.* FROM decisions d JOIN signals s ON s.id = d.signal_id"
            " WHERE s.day = ? ORDER BY d.ts",
            (day.isoformat(),),
        ).fetchall()

    def equity_curve(self, days: int | None = None) -> list[tuple[str, float]]:
        sql = "SELECT ts, equity FROM equity ORDER BY ts"
        rows = self._db.execute(sql).fetchall()
        if days is not None:
            rows = rows[-days:]
        return [(r["ts"], float(r["equity"])) for r in rows]
