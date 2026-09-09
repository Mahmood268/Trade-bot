"""Journal tests.

The journal is where the Risk Warden's view of "today" comes from after a
restart. The numbers it returns — realised P&L, trade count, losing streak —
set the daily loss budget and the halt, so they are checked against hand-built
sequences rather than against each other.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from goldbot.journal import Journal
from goldbot.strategy.base import Signal

DAY = date(2026, 3, 9)
T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def signal() -> Signal:
    return Signal(time=T0, direction="buy", reference_price=2000.0, stop=1996.0,
                  target=2008.0, atr=2.0, reason="test", context={"adx": 25.0})


@pytest.fixture
def journal():
    j = Journal(":memory:")
    yield j
    j.close()


class TestDayStats:
    def test_empty_day(self, journal):
        stats = journal.day_stats(DAY)
        assert stats.realised_pnl == 0.0 and stats.trades == 0 and stats.consecutive_losses == 0
        assert stats.start_equity is None

    def test_realised_pnl_sums_only_todays_closes(self, journal):
        journal.record_close(1, T0, DAY, 0.12, 2008.0, +96.0, "target", dry_run=True)
        journal.record_close(2, T0, DAY, 0.12, 1996.0, -48.0, "stop", dry_run=True)
        yesterday = DAY - timedelta(days=1)
        journal.record_close(3, T0 - timedelta(days=1), yesterday, 0.12, 1990.0, -500.0, "stop", dry_run=True)
        assert journal.day_stats(DAY).realised_pnl == pytest.approx(48.0)

    def test_trades_counts_only_successful_orders(self, journal):
        sid = journal.record_signal(signal(), T0, DAY, "hunt")
        journal.record_order(sid, T0, DAY, "buy", 0.12, 2000.0, 1996.0, 2008.0, 48.0, True, 10009, "gb-1", 1, False)
        journal.record_order(sid, T0, DAY, "buy", 0.12, None, 1996.0, 2008.0, 48.0, False, 10019, "no money", None, False)
        assert journal.day_stats(DAY).trades == 1

    def test_agent_cost_accumulates(self, journal):
        journal.record_agent_call(T0, DAY, "decision", "claude-opus-5", True, 1000, 200, cost_usd=0.10)
        journal.record_agent_call(T0, DAY, "news_scout", "claude-opus-5", True, 5000, 500, cost_usd=0.04)
        assert journal.day_stats(DAY).agent_cost_usd == pytest.approx(0.14)


class TestConsecutiveLosses:
    def test_counts_back_from_the_most_recent_close(self, journal):
        t = T0
        for pnl in (+10, -5, -5, -5):
            journal.record_close(1, t, DAY, 0.1, 2000.0, pnl, "x", True)
            t += timedelta(minutes=1)
        assert journal.consecutive_losses() == 3

    def test_a_win_breaks_the_streak(self, journal):
        t = T0
        for pnl in (-5, -5, +10, -5):
            journal.record_close(1, t, DAY, 0.1, 2000.0, pnl, "x", True)
            t += timedelta(minutes=1)
        assert journal.consecutive_losses() == 1

    def test_breakeven_counts_as_a_loss(self, journal):
        # A scratch is not a win. Five scratches in a row is still a system
        # that is not working, and the halt should say so.
        journal.record_close(1, T0, DAY, 0.1, 2000.0, 0.0, "breakeven_stop", True)
        assert journal.consecutive_losses() == 1

    def test_resume_resets_the_streak(self, journal):
        t = T0
        for _ in range(5):
            journal.record_close(1, t, DAY, 0.1, 2000.0, -5, "stop", True)
            t += timedelta(minutes=1)
        assert journal.consecutive_losses() == 5
        journal.record_event(t, DAY, "resume", "operator /resume")
        assert journal.consecutive_losses() == 0
        journal.record_close(1, t + timedelta(minutes=1), DAY, 0.1, 2000.0, -5, "stop", True)
        assert journal.consecutive_losses() == 1


class TestDayRecord:
    def test_open_day_is_idempotent(self, journal):
        # A restart at 14:07 must not reset the daily budget to 14:07's equity.
        assert journal.open_day(DAY, 5000.0) == 5000.0
        assert journal.open_day(DAY, 4800.0) == 5000.0
        assert journal.day_stats(DAY).start_equity == 5000.0

    def test_close_day_records_end_equity(self, journal):
        journal.open_day(DAY, 5000.0)
        journal.close_day(DAY, 5050.0, audit="fine")
        row = journal._db.execute("SELECT * FROM days WHERE day = ?", (DAY.isoformat(),)).fetchone()
        assert row["end_equity"] == 5050.0 and row["audit"] == "fine"


class TestIdempotency:
    def test_finds_an_order_by_its_signal_tag(self, journal):
        journal.record_order(None, T0, DAY, "buy", 0.12, 2000.0, 1996.0, 2008.0, 48.0, True, 10009, "gb-260309-0900-B", 42, False)
        row = journal.open_order_for_comment("gb-260309-0900-B")
        assert row is not None and row["ticket"] == 42
        assert journal.open_order_for_comment("gb-nope") is None


class TestPersistence:
    def test_survives_reopen(self, tmp_path):
        path = tmp_path / "j.db"
        j = Journal(path)
        j.record_close(1, T0, DAY, 0.1, 2000.0, -5, "stop", True)
        j.close()
        j2 = Journal(path)
        assert j2.day_stats(DAY).realised_pnl == -5
        j2.close()

    def test_signal_context_round_trips_as_json(self, journal):
        sid = journal.record_signal(signal(), T0, DAY, "hunt")
        row = journal._db.execute("SELECT context FROM signals WHERE id = ?", (sid,)).fetchone()
        assert '"adx": 25.0' in row["context"]
