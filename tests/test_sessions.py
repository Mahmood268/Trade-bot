"""Session gate tests.

The daylight-saving cases matter more than they look. The windows are London
wall-clock, so in summer they sit an hour earlier in UTC. A gate that quietly
worked in UTC would trade the wrong hour for seven months of the year and the
backtest would never show it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from goldbot.config import SessionConfig, SessionWindow
from goldbot.sessions import SessionGate


def utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


@pytest.fixture
def gate():
    return SessionGate(SessionConfig())


class TestWindows:
    def test_inside_london_window_in_winter(self):
        # 2026-03-09 is a Monday, before BST starts: London == UTC.
        verdict = gate_at(utc(2026, 3, 9, 9))
        assert verdict.allowed
        assert verdict.window == "london"

    def test_windows_follow_british_summer_time(self, gate):
        # Monday 2026-06-08, BST: 07:00 UTC is 08:00 London — the window's open.
        assert gate.can_enter(utc(2026, 6, 8, 7)).allowed
        # 06:30 UTC is 07:30 London: half an hour too early.
        assert not gate.can_enter(utc(2026, 6, 8, 6, 30)).allowed

    def test_window_end_is_exclusive(self, gate):
        # London closes at 12:00; in winter that is 12:00 UTC exactly.
        assert gate.can_enter(utc(2026, 3, 9, 11, 59)).allowed
        assert not gate.can_enter(utc(2026, 3, 9, 12, 0)).allowed

    def test_lunch_gap_between_the_two_windows(self, gate):
        assert not gate.can_enter(utc(2026, 3, 9, 13, 0)).allowed  # 13:00 London
        assert gate.can_enter(utc(2026, 3, 9, 13, 30)).window == "ny_overlap"

    def test_asian_session_is_closed(self, gate):
        # Thin book, wide spread — the hours that quietly drain a scalper.
        assert not gate.can_enter(utc(2026, 3, 9, 2)).allowed

    def test_naive_timestamps_are_treated_as_utc(self, gate):
        naive = datetime(2026, 3, 9, 9, 0)
        assert gate.can_enter(naive).allowed


class TestWeekend:
    def test_saturday_is_not_a_trading_day(self, gate):
        verdict = gate.can_enter(utc(2026, 6, 13, 9))
        assert not verdict.allowed
        assert "not a configured trading day" in verdict.reason

    def test_friday_after_close_blocks_new_entries(self, gate):
        # friday_close is 20:00 London = 19:00 UTC in summer.
        assert gate.can_enter(utc(2026, 6, 12, 16)).allowed is False  # outside windows anyway
        verdict = gate.can_enter(utc(2026, 6, 12, 19))
        assert not verdict.allowed
        assert "Friday close" in verdict.reason

    def test_friday_after_close_forces_flattening(self, gate):
        assert gate.must_flatten(utc(2026, 6, 12, 19)).allowed
        assert not gate.must_flatten(utc(2026, 6, 12, 10)).allowed

    def test_weekend_forces_flattening(self, gate):
        assert gate.must_flatten(utc(2026, 6, 13, 12)).allowed
        assert gate.must_flatten(utc(2026, 6, 14, 12)).allowed

    def test_flattening_can_be_disabled(self):
        cfg = SessionConfig(flatten_before_weekend=False)
        assert not SessionGate(cfg).must_flatten(utc(2026, 6, 13, 12)).allowed


class TestCustomWindows:
    def test_single_custom_window(self):
        cfg = SessionConfig(
            timezone="UTC",
            windows=(SessionWindow(name="ny", start="14:00", end="16:00"),),
        )
        g = SessionGate(cfg)
        assert g.can_enter(utc(2026, 3, 9, 15)).window == "ny"
        assert not g.can_enter(utc(2026, 3, 9, 9)).allowed

    def test_reason_names_the_configured_windows(self, gate):
        reason = gate.can_enter(utc(2026, 3, 9, 2)).reason
        assert "london 08:00-12:00" in reason
        assert "ny_overlap 13:30-17:00" in reason


def gate_at(ts):
    return SessionGate(SessionConfig()).can_enter(ts)
