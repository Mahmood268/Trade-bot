"""Daily routine tests.

The phase clock is what decides whether the bot may open a trade, must close
one, or should be doing nothing at all — from the wall clock alone, so a bot
restarted at 14:07 lands in exactly the phase it would have been in anyway.

Every boundary is tested from both sides: one minute before and the minute
itself. Off-by-one at a phase boundary is a trade opened during wind-down, or a
flatten that fires at the start of the NY session.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from goldbot.config import Config, RoutineConfig, SessionConfig, SessionWindow
from goldbot.routine import PERMISSIONS, DailyRoutine, Phase


def london(hh, mm=0, day=9):
    """A winter Monday (2026-03-09) so London == UTC and times read literally."""
    return datetime(2026, 3, day, hh, mm, tzinfo=timezone.utc)


@pytest.fixture
def routine():
    cfg = Config()
    return DailyRoutine(cfg.sessions, cfg.routine)


class TestPhaseSequence:
    """Default config: pre-flight 07:30, London 08:00-12:00, NY 13:30-17:00,
    close 19:30, debrief 19:45."""

    @pytest.mark.parametrize(
        "hh, mm, expected",
        [
            (0, 0, Phase.CLOSED),
            (7, 29, Phase.CLOSED),
            (7, 30, Phase.PREFLIGHT),
            (7, 59, Phase.PREFLIGHT),
            (8, 0, Phase.HUNT),
            (11, 59, Phase.HUNT),
            (12, 0, Phase.HOLD),
            (13, 29, Phase.HOLD),
            (13, 30, Phase.HUNT),
            (16, 59, Phase.HUNT),
            (17, 0, Phase.WIND_DOWN),
            (19, 29, Phase.WIND_DOWN),
            (19, 30, Phase.FLATTEN),
            (19, 44, Phase.FLATTEN),
            (19, 45, Phase.DEBRIEF),
            (19, 59, Phase.DEBRIEF),
            (20, 0, Phase.CLOSED),
            (23, 59, Phase.CLOSED),
        ],
    )
    def test_each_boundary_from_both_sides(self, routine, hh, mm, expected):
        assert routine.phase_at(london(hh, mm)).phase is expected

    def test_hunt_names_its_window(self, routine):
        assert routine.phase_at(london(9)).window == "london"
        assert routine.phase_at(london(15)).window == "ny_overlap"

    def test_weekend_is_its_own_phase(self, routine):
        assert routine.phase_at(london(10, day=14)).phase is Phase.WEEKEND  # Saturday
        assert routine.phase_at(london(10, day=15)).phase is Phase.WEEKEND  # Sunday

    def test_phases_follow_british_summer_time(self, routine):
        # Monday 2026-06-08: 07:00 UTC is 08:00 London — the open.
        summer = datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc)
        assert routine.phase_at(summer).phase is Phase.HUNT
        assert routine.phase_at(summer.replace(hour=6, minute=59)).phase is Phase.PREFLIGHT


class TestPermissions:
    def test_new_entries_only_during_the_hunt(self):
        allowed = {phase for phase, perms in PERMISSIONS.items() if perms.open_new}
        assert allowed == {Phase.HUNT}

    def test_flatten_is_the_only_phase_that_closes_everything(self):
        assert {p for p, perms in PERMISSIONS.items() if perms.flatten} == {Phase.FLATTEN}

    def test_wind_down_can_tighten_but_not_open(self):
        perms = PERMISSIONS[Phase.WIND_DOWN]
        assert perms.manage and perms.tighten_only and not perms.open_new

    def test_hold_manages_without_opening(self):
        perms = PERMISSIONS[Phase.HOLD]
        assert perms.manage and not perms.open_new and not perms.tighten_only

    def test_agents_that_bracket_the_day_have_their_own_phase(self):
        assert PERMISSIONS[Phase.PREFLIGHT].run_preflight
        assert PERMISSIONS[Phase.DEBRIEF].run_debrief
        assert not PERMISSIONS[Phase.PREFLIGHT].open_new
        assert not PERMISSIONS[Phase.DEBRIEF].manage

    def test_closed_and_weekend_permit_nothing(self):
        for phase in (Phase.CLOSED, Phase.WEEKEND):
            perms = PERMISSIONS[phase]
            assert not any(vars(perms).values()), f"{phase} permits something"

    def test_every_phase_has_permissions(self):
        assert set(PERMISSIONS) == set(Phase)


class TestNextBoundary:
    def test_sleeps_until_the_next_phase_change(self, routine):
        # Mid-hold at 12:30 the next thing that can happen is the 13:30 open.
        nxt = routine.next_boundary(london(12, 30))
        assert (nxt.hour, nxt.minute) == (13, 30)

    def test_after_the_day_ends_the_next_boundary_is_tomorrows_preflight(self, routine):
        nxt = routine.next_boundary(london(21))
        assert nxt.date().day == 10 and (nxt.hour, nxt.minute) == (7, 30)

    def test_boundary_is_strictly_in_the_future(self, routine):
        # Sitting exactly on a boundary must not return that same instant, or
        # the engine would spin.
        nxt = routine.next_boundary(london(13, 30))
        assert (nxt.hour, nxt.minute) == (17, 0)


class TestRoutineDisabled:
    def test_falls_back_to_session_windows_only(self):
        cfg = Config(routine=RoutineConfig(enabled=False))
        r = DailyRoutine(cfg.sessions, cfg.routine)
        assert r.phase_at(london(7, 45)).phase is Phase.HOLD      # no pre-flight
        assert r.phase_at(london(9)).phase is Phase.HUNT
        assert r.phase_at(london(19, 45)).phase is Phase.FLATTEN  # daily close still applies


class TestCustomSchedule:
    def test_single_window_day(self):
        sessions = SessionConfig(
            timezone="UTC",
            windows=(SessionWindow(name="ny", start="13:30", end="17:00"),),
            daily_close="18:00",
        )
        r = DailyRoutine(sessions, RoutineConfig(preflight="12:00", debrief_after_min=10))
        assert r.phase_at(london(12, 30)).phase is Phase.PREFLIGHT
        assert r.phase_at(london(14)).phase is Phase.HUNT
        assert r.phase_at(london(17, 30)).phase is Phase.WIND_DOWN
        assert r.phase_at(london(18)).phase is Phase.FLATTEN
        assert r.phase_at(london(18, 10)).phase is Phase.DEBRIEF
        assert r.phase_at(london(18, 20)).phase is Phase.CLOSED
