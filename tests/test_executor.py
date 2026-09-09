"""Executor tests against the fake terminal.

The four rules the executor enforces — server-side stop on every entry, stops
only ever tighten, idempotent entries, dry-run on the same code path — each get
tests that try to break them. The widening cases matter most: an agent asking
to widen a stop is the one instruction that must be refused no matter how it
is phrased.
"""

from __future__ import annotations

import time as _time
from datetime import date, datetime, timezone

import pytest

from goldbot.config import Config
from goldbot.executor import EXIT_NOW, PARTIAL_CLOSE, TIGHTEN_STOP, Executor
from goldbot.journal import Journal
from goldbot.mt5_client import MT5Client, Tick
from goldbot.risk import RiskDecision
from goldbot.strategy.base import Signal
from tests.fake_mt5 import FakeMT5, _Position

NOW = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
DAY = date(2026, 3, 9)
ASK, BID = 2650.25, 2650.00


def signal(direction="buy", t=NOW) -> Signal:
    ref = ASK if direction == "buy" else BID
    if direction == "buy":
        return Signal(time=t, direction="buy", reference_price=ref, stop=ref - 4, target=ref + 8, atr=2.0, reason="test")
    return Signal(time=t, direction="sell", reference_price=ref, stop=ref + 4, target=ref - 8, atr=2.0, reason="test")


def approved(volume=0.12, risk=48.0) -> RiskDecision:
    return RiskDecision(True, "ok", volume=volume, risk_amount=risk, risk_pct=0.96, stop_points=400)


@pytest.fixture
def fake():
    return FakeMT5(balance=5000.0)


@pytest.fixture
def client(fake):
    cfg = Config()
    c = MT5Client(cfg.mt5, mt5_module=fake)
    c.connect()
    c.discover_symbol()
    return c


@pytest.fixture
def journal():
    j = Journal(":memory:")
    yield j
    j.close()


def executor(client, journal, dry_run):
    return Executor(client, Config(), journal, dry_run=dry_run)


def set_price(fake, bid, ask=None):
    sym = fake.symbols["XAUUSD"]
    sym.bid = bid
    sym.ask = ask if ask is not None else bid + 0.25


class TestDryRun:
    def test_opens_a_paper_position_and_sends_nothing(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        result = ex.open(signal(), approved(), NOW, DAY)
        assert result.ok and result.dry_run
        assert fake.sent_requests == [], "dry run must never reach order_send"
        pos = ex.positions()[0]
        assert pos.paper and pos.entry == ASK and pos.sl == pytest.approx(ASK - 4) and pos.tp == pytest.approx(ASK + 8)
        row = journal.orders_for_day(DAY)[0]
        assert row["dry_run"] == 1 and row["sl"] == pytest.approx(ASK - 4)

    def test_paper_stop_is_hit_on_a_tick(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        set_price(fake, ASK - 4.5)
        closed = ex.mark_paper(client.get_tick(), NOW, DAY)
        assert len(closed) == 1 and closed[0].reason == "stop"
        # (2645.75 - 2650.25) / 0.01 x $1 x 0.12 lots = -$54.00
        assert closed[0].pnl == pytest.approx(-54.0)
        assert ex.positions() == []
        assert journal.day_stats(DAY).realised_pnl == pytest.approx(-54.0)

    def test_paper_target_is_hit_on_a_tick(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        set_price(fake, ASK + 8.0)
        closed = ex.mark_paper(client.get_tick(), NOW, DAY)
        assert closed[0].reason == "target" and closed[0].pnl > 0

    def test_stop_is_checked_before_target(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        # A tick cannot be both; the pessimistic order is still the contract.
        set_price(fake, ASK - 4.5)
        assert ex.mark_paper(client.get_tick(), NOW, DAY)[0].reason == "stop"


class TestLiveEntry:
    def test_sends_a_market_order_with_the_stop_attached(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        result = ex.open(signal(), approved(), NOW, DAY)
        assert result.ok and not result.dry_run
        req = fake.sent_requests[-1]
        assert req["sl"] == pytest.approx(ASK - 4) and req["tp"] == pytest.approx(ASK + 8)
        assert req["volume"] == pytest.approx(0.12) and req["magic"] == Config().mt5.magic
        assert req["comment"].startswith("gb-")
        assert len(ex.positions()) == 1

    def test_a_rejected_order_is_journalled_and_reported(self, fake, client, journal):
        fake.order_script = [10019, 10019, 10019]  # NO_MONEY, not retried
        ex = executor(client, journal, dry_run=False)
        result = ex.open(signal(), approved(), NOW, DAY)
        assert not result.ok and "rejected" in result.reason
        row = journal.orders_for_day(DAY)[0]
        assert row["ok"] == 0 and row["retcode"] == 10019
        assert ex.positions() == []

    def test_a_stop_on_the_wrong_side_never_reaches_the_broker(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        bad = Signal(time=NOW, direction="buy", reference_price=ASK, stop=ASK - 4, target=ASK + 8, atr=2.0, reason="x")
        # Move the market so the signal's stop is now above the ask.
        set_price(fake, ASK - 10)
        result = ex.open(bad, approved(), NOW, DAY)
        assert not result.ok and "stops rejected" in result.reason
        assert fake.sent_requests == []

    def test_risk_is_remeasured_from_the_fill(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        ex.open(signal(), approved(), NOW, DAY)
        pos = ex.positions()[0]
        assert pos.risk_amount == pytest.approx(abs(pos.entry - pos.sl) / 0.01 * 0.12)


class TestIdempotency:
    def test_the_same_signal_is_not_opened_twice(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        first = ex.open(signal(), approved(), NOW, DAY)
        second = ex.open(signal(), approved(), NOW, DAY)
        assert first.ok and second.ok
        assert "not duplicating" in second.reason
        assert len(ex.positions()) == 1
        assert len([r for r in fake.sent_requests if r["action"] == FakeMT5.TRADE_ACTION_DEAL]) == 1

    def test_a_different_bar_is_a_different_trade(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        ex.open(signal(t=NOW), approved(), NOW, DAY)
        later = NOW.replace(minute=15)
        ex.open(signal(t=later), approved(), later, DAY)
        assert len(ex.positions()) == 2

    def test_the_tag_fits_mt5s_comment_limit(self):
        assert len(Executor.signal_tag(signal())) <= 31


class TestStopManagement:
    def test_break_even_at_one_r(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        pos = ex.positions()[0]
        set_price(fake, ASK + 4.0)   # bid +4.00 = exactly 1R on a 4.00 stop
        actions = ex.manage(pos, client.get_tick(), atr=2.0, now=NOW, day=DAY)
        assert any("breakeven" in a for a in actions)
        assert pos.sl == pytest.approx(ASK)

    def test_trailing_after_one_and_a_half_r(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        pos = ex.positions()[0]
        set_price(fake, ASK + 6.5)   # +1.625R; trail = bid - 2 x ATR(2.0) = ASK + 2.5
        ex.manage(pos, client.get_tick(), atr=2.0, now=NOW, day=DAY)
        assert pos.sl == pytest.approx(ASK + 2.5)

    def test_a_stop_never_moves_backwards(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        pos = ex.positions()[0]
        set_price(fake, ASK + 6.5)
        ex.manage(pos, client.get_tick(), atr=2.0, now=NOW, day=DAY)
        locked = pos.sl
        set_price(fake, ASK + 4.2)   # retrace; the trailing level is now lower
        ex.manage(pos, client.get_tick(), atr=2.0, now=NOW, day=DAY)
        assert pos.sl == pytest.approx(locked)

    def test_live_management_modifies_on_the_broker(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        ex.open(signal(), approved(), NOW, DAY)
        pos = ex.positions()[0]
        set_price(fake, ASK + 4.0)
        ex.manage(pos, client.get_tick(), atr=2.0, now=NOW, day=DAY)
        sltp = [r for r in fake.sent_requests if r["action"] == FakeMT5.TRADE_ACTION_SLTP]
        assert sltp and sltp[-1]["sl"] == pytest.approx(ASK)
        assert client.positions()[0].sl == pytest.approx(ASK)


class TestAgentInstructions:
    def test_tighten_is_honoured(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        t = ex.positions()[0].ticket
        r = ex.apply_instruction(t, TIGHTEN_STOP, NOW, DAY, new_sl=ASK - 2.0, source="trade_manager")
        assert r.ok and ex.positions()[0].sl == pytest.approx(ASK - 2.0)

    def test_widening_disguised_as_tighten_is_refused_and_flagged(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        t = ex.positions()[0].ticket
        r = ex.apply_instruction(t, TIGHTEN_STOP, NOW, DAY, new_sl=ASK - 6.0, source="trade_manager")
        assert not r.ok and "widen" in r.reason
        assert ex.positions()[0].sl == pytest.approx(ASK - 4.0)
        anomaly = journal.last_event("anomaly")
        assert anomaly is not None and "WIDEN" in anomaly["message"]

    def test_unknown_instructions_are_refused_and_flagged(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        t = ex.positions()[0].ticket
        for bad in ("ADD_SIZE", "WIDEN_STOP", "DOUBLE_DOWN", "hold"):
            r = ex.apply_instruction(t, bad, NOW, DAY, source="trade_manager")
            assert not r.ok
        assert ex.positions()[0].volume == pytest.approx(0.12)
        assert journal.last_event("anomaly") is not None

    def test_partial_close(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(volume=0.20), NOW, DAY)
        t = ex.positions()[0].ticket
        r = ex.apply_instruction(t, PARTIAL_CLOSE, NOW, DAY, fraction=0.5, source="trade_manager")
        assert r.ok and r.volume == pytest.approx(0.10)
        assert ex.positions()[0].volume == pytest.approx(0.10)

    def test_exit_now(self, fake, client, journal):
        ex = executor(client, journal, dry_run=True)
        ex.open(signal(), approved(), NOW, DAY)
        t = ex.positions()[0].ticket
        assert ex.apply_instruction(t, EXIT_NOW, NOW, DAY, source="trade_manager").ok
        assert ex.positions() == []


class TestAdoption:
    def _seed(self, fake, magic, sl=2646.0, direction="buy"):
        fake._positions.append(_Position(
            ticket=555, symbol="XAUUSD",
            type=FakeMT5.ORDER_TYPE_BUY if direction == "buy" else FakeMT5.ORDER_TYPE_SELL,
            volume=0.12, price_open=2650.0, sl=sl, tp=2658.0, profit=0.0, magic=magic,
            comment="gb-old", time=int(_time.time()),
        ))

    def test_adopts_positions_carrying_our_magic(self, fake, client, journal):
        self._seed(fake, Config().mt5.magic)
        ex = executor(client, journal, dry_run=False)
        adopted = ex.adopt(NOW, DAY)
        assert [p.ticket for p in adopted] == [555]
        assert adopted[0].initial_sl == 2646.0
        assert journal.last_event("adopt") is not None

    def test_ignores_positions_placed_by_hand(self, fake, client, journal):
        self._seed(fake, magic=1)
        ex = executor(client, journal, dry_run=False)
        assert ex.adopt(NOW, DAY) == []

    def test_an_adopted_position_without_a_stop_gets_one(self, fake, client, journal):
        self._seed(fake, Config().mt5.magic, sl=0.0)
        ex = executor(client, journal, dry_run=False)
        ex.adopt(NOW, DAY)
        assert client.positions()[0].sl > 0, "no position may sit at the broker unprotected"


class TestFlatten:
    def test_closes_everything_and_confirms(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        ex.open(signal(t=NOW), approved(), NOW, DAY)
        ex.open(signal(t=NOW.replace(minute=15)), approved(), NOW, DAY)
        results = ex.flatten("eod_flatten", NOW, DAY)
        assert len(results) == 2 and all(r.ok for r in results)
        assert ex.positions() == []
        assert journal.last_event("flatten") is not None
        assert len(journal.closes_for_day(DAY)) == 2

    def test_a_failed_close_is_an_error_event_not_silence(self, fake, client, journal):
        ex = executor(client, journal, dry_run=False)
        ex.open(signal(), approved(), NOW, DAY)
        fake.order_script = [10018] * 4   # MARKET_CLOSED on the close attempt
        results = ex.flatten("test", NOW, DAY)
        assert not results[0].ok
        assert journal.last_event("error") is not None
