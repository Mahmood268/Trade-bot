"""Calendar blackout tests. No network: the feed is a stub."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from goldbot.calendar import Calendar
from goldbot.config import CalendarConfig

NFP = datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc)  # a Friday
FEED = [
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-03-06T08:30:00-05:00", "impact": "High"},
    {"title": "Unemployment Rate", "country": "USD", "date": "2026-03-06T08:30:00-05:00", "impact": "High"},
    {"title": "German ZEW", "country": "EUR", "date": "2026-03-10T10:00:00+00:00", "impact": "High"},
    {"title": "Crude Oil Inventories", "country": "USD", "date": "2026-03-04T15:30:00+00:00", "impact": "Medium"},
    {"title": "broken row", "country": "USD"},
]


@pytest.fixture
def now():
    return [datetime(2026, 3, 6, 7, 0, tzinfo=timezone.utc)]


@pytest.fixture
def cal(tmp_path, now):
    c = Calendar(CalendarConfig(), cache_path=tmp_path / "cal.json", fetch=lambda url: FEED, now=lambda: now[0])
    c.refresh()
    return c


class TestParsing:
    def test_keeps_only_high_impact_usd(self, cal):
        titles = {e.title for e in cal.events}
        assert titles == {"Non-Farm Employment Change", "Unemployment Rate"}

    def test_converts_the_feeds_offset_to_utc(self, cal):
        assert all(e.time == NFP for e in cal.events)

    def test_malformed_rows_are_skipped_not_fatal(self, cal):
        assert len(cal.events) == 2


class TestBlackout:
    def test_inside_the_window_before_and_after(self, cal):
        assert cal.blackout_at(NFP - timedelta(minutes=15)).blocked
        assert cal.blackout_at(NFP).blocked
        assert cal.blackout_at(NFP + timedelta(minutes=15)).blocked

    def test_outside_the_window(self, cal):
        assert not cal.blackout_at(NFP - timedelta(minutes=16)).blocked
        assert not cal.blackout_at(NFP + timedelta(minutes=16)).blocked

    def test_names_the_event(self, cal):
        verdict = cal.blackout_at(NFP)
        assert "Non-Farm" in verdict.reason and verdict.event is not None

    def test_next_event(self, cal):
        assert cal.next_event_after(NFP - timedelta(hours=2)).time == NFP
        assert cal.next_event_after(NFP + timedelta(hours=2)) is None


class TestFailClosed:
    def test_no_data_at_all_is_a_blackout(self, tmp_path, now):
        c = Calendar(CalendarConfig(), cache_path=tmp_path / "none.json",
                     fetch=lambda url: (_ for _ in ()).throw(OSError("down")), now=lambda: now[0])
        c.refresh()
        verdict = c.blackout_at(now[0])
        assert verdict.blocked and verdict.unknown

    def test_a_download_failure_keeps_the_old_cache(self, tmp_path, now):
        calls = {"n": 0}

        def flaky(url):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("rate limited")
            return FEED

        c = Calendar(CalendarConfig(refresh_hours=1), cache_path=tmp_path / "c.json", fetch=flaky, now=lambda: now[0])
        c.refresh()
        now[0] += timedelta(hours=2)
        assert c.refresh() == 2
        assert not c.blackout_at(now[0]).unknown

    def test_a_week_old_cache_is_too_stale_to_trust(self, cal, now):
        now[0] += timedelta(days=8)
        verdict = cal.blackout_at(now[0])
        assert verdict.blocked and verdict.unknown

    def test_disabled_never_blocks(self, tmp_path, now):
        c = Calendar(CalendarConfig(enabled=False), cache_path=tmp_path / "x.json", fetch=lambda u: FEED, now=lambda: now[0])
        assert not c.blackout_at(NFP).blocked


class TestCaching:
    def test_refresh_is_throttled(self, tmp_path, now):
        calls = {"n": 0}

        def counting(url):
            calls["n"] += 1
            return FEED

        c = Calendar(CalendarConfig(refresh_hours=24), cache_path=tmp_path / "c.json", fetch=counting, now=lambda: now[0])
        c.refresh(); c.refresh(); c.refresh()
        assert calls["n"] == 1, "the feed rate-limits; three refreshes in a row must be one download"
        now[0] += timedelta(hours=25)
        c.refresh()
        assert calls["n"] == 2

    def test_cache_survives_a_restart(self, tmp_path, now):
        path = tmp_path / "c.json"
        Calendar(CalendarConfig(), cache_path=path, fetch=lambda u: FEED, now=lambda: now[0]).refresh()
        reloaded = Calendar(CalendarConfig(), cache_path=path, fetch=lambda u: [], now=lambda: now[0])
        assert len(reloaded.events) == 2
        assert reloaded.blackout_at(NFP).blocked
