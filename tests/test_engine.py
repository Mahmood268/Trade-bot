"""Engine tests: the whole loop, one cycle at a time, against the fake terminal.

The clock is injected, so every scenario is a specific minute of a specific
day and nothing sleeps. The strategy is a stub that fires on command, so the
tests exercise sequencing and gating — the engine's actual job — rather than
whether the fixture happens to contain a pullback.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from goldbot.calendar import Calendar
from goldbot.config import CalendarConfig, Config, SessionConfig, TelegramConfig
from goldbot.engine import Engine
from goldbot.journal import Journal
from goldbot.mt5_client import MT5Client, MT5Error
from goldbot.notifier import Telegram
from goldbot.routine import Phase
from goldbot.strategy.base import Signal
from tests.fake_mt5 import FakeMT5
from tests.test_notifier import FakeTransport, update

ASK, BID = 2650.25, 2650.00
MONDAY_0900 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


class StubStrategy:
    """Fires a fixed long setup around the fake's ask whenever told to."""

    name = "stub"

    def __init__(self, clock):
        self.clock = clock
        self.fire = False

    def evaluate(self, bars, i):
        if not self.fire:
            return None
        return Signal(time=self.clock(), direction="buy", reference_price=ASK,
                      stop=ASK - 4, target=ASK + 8, atr=2.0, reason="stub setup")


class Harness:
    def __init__(self, tmp_path, calendar_feed=None, cfg=None):
        self.clock = [MONDAY_0900]
        self.cfg = cfg or Config(
            sessions=SessionConfig(timezone="UTC"),
            telegram=TelegramConfig(enabled=True, bot_token="t", chat_id="777"),
        )
        self.fake = FakeMT5(balance=5000.0)
        self.fake.clock = lambda: self.clock[0].timestamp()
        self.client = MT5Client(self.cfg.mt5, mt5_module=self.fake)
        self.journal = Journal(":memory:")
        self.transport = FakeTransport()
        self.notifier = Telegram(self.cfg.telegram, transport=self.transport)
        self.calendar = Calendar(
            self.cfg.calendar, cache_path=tmp_path / "cal.json",
            fetch=lambda url: calendar_feed or [], now=lambda: self.clock[0],
        )
        self.strategy = StubStrategy(lambda: self.clock[0])
        self.engine = Engine(self.cfg, self.client, self.journal, self.notifier, self.calendar,
                             strategy=self.strategy, clock=lambda: self.clock[0])
        self.engine.start()

    def at(self, hh, mm=0, day=9):
        self.clock[0] = datetime(2026, 3, day, hh, mm, tzinfo=timezone.utc)

    def tick(self, minutes=1):
        self.clock[0] += timedelta(minutes=minutes)

    def price(self, bid, ask=None):
        sym = self.fake.symbols["XAUUSD"]
        sym.bid, sym.ask = bid, (ask if ask is not None else bid + 0.25)

    def command(self, text):
        self.transport.updates.append(update(len(self.transport.calls) + 1, 777, text))

    def sent(self, needle):
        return [m for m in self.notifier.sent if needle in m]

    @property
    def day(self):
        return self.clock[0].date()


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


class TestStartup:
    def test_announces_itself_as_a_dry_run(self, h):
        assert h.sent("DRY RUN")
        assert h.journal.last_event("start") is not None
        assert h.engine.dry_run

    def test_opens_the_days_record_with_starting_equity(self, h):
        assert h.journal.day_stats(h.day).start_equity == 5000.0


class TestEntries:
    def test_a_signal_in_the_hunt_phase_opens_a_paper_trade(self, h):
        h.strategy.fire = True
        report = h.engine.cycle()
        assert report.phase is Phase.HUNT and report.signal and report.opened
        assert h.fake.sent_requests == [], "dry run must never reach the broker"
        assert len(h.engine.executor.positions()) == 1
        assert h.sent("[DRY RUN] OPEN BUY")
        order = h.journal.orders_for_day(h.day)[0]
        assert order["dry_run"] == 1 and order["volume"] == pytest.approx(0.12)

    def test_no_entries_outside_the_hunt_phase(self, h):
        h.strategy.fire = True
        h.at(12, 30)                       # HOLD, between the windows
        report = h.engine.cycle()
        assert report.phase is Phase.HOLD
        assert "no new entries" in (report.skipped or "")
        assert h.engine.executor.positions() == []

    def test_the_same_signal_is_not_opened_twice(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.engine.cycle()                   # same minute → same tag
        assert len(h.engine.executor.positions()) == 1

    def test_paused_blocks_new_entries(self, h):
        h.strategy.fire = True
        h.command("/pause")
        report = h.engine.cycle()
        assert report.skipped == "paused by operator"
        assert h.engine.executor.positions() == []

    def test_calendar_blackout_blocks_the_entry_and_records_why(self, tmp_path):
        feed = [{"title": "CPI", "country": "USD", "impact": "High", "date": "2026-03-09T09:05:00+00:00"}]
        h = Harness(tmp_path, calendar_feed=feed)
        h.strategy.fire = True
        report = h.engine.cycle()
        assert report.signal and not report.opened
        assert "calendar" in report.skipped and "CPI" in report.skipped
        decision = h.journal.decisions_for_day(h.day)[0]
        assert decision["source"] == "gate" and decision["approved"] == 0

    def test_the_warden_veto_is_journalled(self, h):
        h.strategy.fire = True
        h.fake.symbols["XAUUSD"].ask = BID + 0.50   # 50-point spread > the 30-point cap
        report = h.engine.cycle()
        assert not report.opened and "spread" in report.skipped
        decision = h.journal.decisions_for_day(h.day)[-1]
        assert decision["source"] == "warden" and decision["approved"] == 0


class TestManagement:
    def test_a_paper_stop_out_is_closed_journalled_and_reported(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.strategy.fire = False
        h.tick()
        h.price(ASK - 4.5)
        report = h.engine.cycle()
        assert report.closed == 1
        assert h.engine.executor.positions() == []
        assert h.journal.day_stats(h.day).realised_pnl < 0
        assert h.sent("CLOSED")

    def test_break_even_is_applied_on_the_way_up(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.strategy.fire = False
        h.tick()
        h.price(ASK + 4.0)
        report = h.engine.cycle()
        assert any("breakeven" in a for a in report.managed)
        assert h.engine.executor.positions()[0].sl == pytest.approx(ASK)

    def test_wind_down_still_manages_but_will_not_open(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.at(18, 0)                        # WIND_DOWN
        h.price(ASK + 4.0)
        report = h.engine.cycle()
        assert report.phase is Phase.WIND_DOWN
        assert any("breakeven" in a for a in report.managed)
        assert len(h.engine.executor.positions()) == 1


class TestFlatten:
    def test_the_daily_close_flattens_and_closes_the_day(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.strategy.fire = False
        h.at(19, 30)
        report = h.engine.cycle()
        assert report.phase is Phase.FLATTEN and report.closed == 1
        assert h.engine.executor.positions() == []
        assert h.journal.closes_for_day(h.day)[0]["reason"] == "eod_flatten"
        assert h.sent("Flattened")
        row = h.journal._db.execute("SELECT end_equity FROM days").fetchone()
        assert row["end_equity"] == 5000.0

    def test_slash_flat_closes_everything_and_pauses(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.command("/flat")
        h.tick()
        h.engine.cycle()
        assert h.engine.executor.positions() == []
        assert h.engine.paused
        assert h.sent("Closed 1 position")


class TestHalts:
    def _lose(self, h, n):
        for _ in range(n):
            h.strategy.fire = True
            h.price(BID, ASK)
            h.engine.cycle()
            h.strategy.fire = False
            h.tick()
            h.price(ASK - 4.5)
            h.engine.cycle()
            h.tick()

    def test_repeated_losses_halt_the_bot_and_alert(self, h):
        self._lose(h, 5)
        assert h.engine.halt_reason is not None
        assert h.journal.last_event("halt") is not None
        assert h.sent("HALT")

    def test_a_halted_bot_rejects_the_next_signal(self, h):
        self._lose(h, 5)
        h.strategy.fire = True
        h.price(BID, ASK)
        report = h.engine.cycle()
        assert not report.opened and "halted" in (report.skipped or "")

    def test_resume_clears_the_halt_and_the_streak(self, h):
        self._lose(h, 5)
        h.command("/resume")
        h.tick()
        h.engine.cycle()
        assert h.engine.halt_reason is None
        assert h.journal.consecutive_losses() == 0


class TestOperator:
    def test_status_reports_phase_and_positions(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.command("/status")
        h.tick()
        h.engine.cycle()
        msg = h.sent("DRY RUN · hunt")[-1]
        assert "open: 1" in msg

    def test_pnl_reports_todays_result(self, h):
        h.command("/pnl")
        h.engine.cycle()
        assert h.sent("Today:")

    def test_a_stranger_cannot_flatten_the_account(self, h):
        h.strategy.fire = True
        h.engine.cycle()
        h.strategy.fire = False
        h.transport.updates.append(update(99, 31337, "/flat"))
        h.tick()
        h.engine.cycle()
        assert len(h.engine.executor.positions()) == 1


class TestWatchdog:
    def test_a_dead_terminal_skips_the_cycle_and_tries_to_reconnect(self, h, monkeypatch):
        monkeypatch.setattr(h.client, "is_connected", lambda: False)
        monkeypatch.setattr(h.client, "connect", lambda: (_ for _ in ()).throw(MT5Error("no terminal")))
        h.strategy.fire = True
        report = h.engine.cycle()
        assert report.skipped == "terminal not connected"
        assert h.engine.executor.positions() == []
        assert h.journal.last_event("error") is not None
        assert h.sent("unreachable")


class TestDayRollover:
    def test_a_new_day_opens_a_new_record(self, h):
        h.engine.cycle()
        h.at(9, 0, day=10)
        h.engine.cycle()
        rows = h.journal._db.execute("SELECT day FROM days ORDER BY day").fetchall()
        assert [r["day"] for r in rows] == ["2026-03-09", "2026-03-10"]

    def test_phase_changes_are_journalled_once(self, h):
        h.engine.cycle(); h.engine.cycle(); h.engine.cycle()
        phases = h.journal._db.execute("SELECT COUNT(*) AS n FROM events WHERE kind='phase'").fetchone()["n"]
        assert phases == 1


class TestShutdown:
    def test_stop_journals_and_notifies(self, h):
        h.engine.stop()
        assert h.journal.last_event("stop") is not None
        assert h.sent("goldbot stopped")
