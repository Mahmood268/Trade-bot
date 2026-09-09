"""Economic-calendar blackouts. Deterministic — no LLM anywhere near it.

The timing of a known event is a lookup, not a judgement. NFP is at 13:30
London on the first Friday; a language model brings nothing to that except the
chance of getting it wrong.

Source: the ForexFactory weekly JSON feed. It rate-limits aggressively (a
couple of downloads per few minutes gets you blocked), so the feed is cached on
disk and refreshed at most every ``refresh_hours``. A stale cache is used with
a warning for up to a week; no cache at all is treated as *unknown*, and the
engine reads unknown as "block entries" — missing a trade is free, walking into
CPI blind is not.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=7)


@dataclass(frozen=True)
class Event:
    title: str
    currency: str
    impact: str
    time: datetime  # UTC


@dataclass(frozen=True)
class BlackoutVerdict:
    blocked: bool
    reason: str
    event: Event | None = None
    unknown: bool = False


def _default_fetch(url: str) -> list[dict[str, Any]]:
    import requests

    resp = requests.get(url, timeout=20, headers={"User-Agent": "goldbot/0.1"})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("calendar feed did not return a list")
    return data


class Calendar:
    def __init__(
        self,
        cfg,
        cache_path: str | Path = "data/calendar.json",
        fetch: Callable[[str], list[dict[str, Any]]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Args:
            cfg: a ``CalendarConfig``.
            cache_path: where the last good download is kept.
            fetch: injectable downloader (tests pass a stub; no network).
            now: injectable clock.
        """
        self.cfg = cfg
        self.cache_path = Path(cache_path)
        self._fetch = fetch or _default_fetch
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.events: list[Event] = []
        self.fetched_at: datetime | None = None
        self._load_cache()

    # ------------------------------------------------------------------

    def refresh(self, force: bool = False) -> int:
        """Download if the cache is older than ``refresh_hours``. Returns event count.

        Never raises: a failed download leaves the previous cache in place and
        logs why. The engine decides what a missing calendar means.
        """
        if not self.cfg.enabled:
            return 0
        age_ok = (
            self.fetched_at is not None
            and self._now() - self.fetched_at < timedelta(hours=self.cfg.refresh_hours)
        )
        if age_ok and not force:
            return len(self.events)
        try:
            raw = self._fetch(self.cfg.feed_url)
        except Exception as exc:  # noqa: BLE001 - a calendar outage must not crash the bot
            log.warning("calendar download failed (%s); keeping cache from %s", exc, self.fetched_at)
            return len(self.events)
        self.events = self._parse(raw)
        self.fetched_at = self._now()
        self._save_cache(raw)
        log.info("calendar refreshed: %d relevant events this week", len(self.events))
        return len(self.events)

    def blackout_at(self, ts: datetime) -> BlackoutVerdict:
        """Is ``ts`` inside a blackout window around a relevant event?"""
        if not self.cfg.enabled:
            return BlackoutVerdict(False, "calendar disabled")
        if self.fetched_at is None:
            return BlackoutVerdict(
                True, "no calendar data — treating as blackout until a download succeeds", unknown=True
            )
        if self._now() - self.fetched_at > STALE_AFTER:
            return BlackoutVerdict(
                True,
                f"calendar last refreshed {self.fetched_at:%Y-%m-%d}; too stale to trust",
                unknown=True,
            )

        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        before = timedelta(minutes=self.cfg.blackout_before_min)
        after = timedelta(minutes=self.cfg.blackout_after_min)
        for event in self.events:
            if event.time - before <= ts <= event.time + after:
                return BlackoutVerdict(
                    True,
                    f"{event.currency} {event.impact}-impact '{event.title}' at "
                    f"{event.time:%H:%M} UTC (±{self.cfg.blackout_before_min}/"
                    f"{self.cfg.blackout_after_min} min)",
                    event=event,
                )
        return BlackoutVerdict(False, "no event in window")

    def next_event_after(self, ts: datetime) -> Event | None:
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        upcoming = sorted((e for e in self.events if e.time > ts), key=lambda e: e.time)
        return upcoming[0] if upcoming else None

    # ------------------------------------------------------------------

    def _parse(self, raw: list[dict[str, Any]]) -> list[Event]:
        impacts = {i.lower() for i in self.cfg.impacts}
        currencies = {c.upper() for c in self.cfg.currencies}
        out: list[Event] = []
        for item in raw:
            try:
                impact = str(item.get("impact", "")).lower()
                currency = str(item.get("country", item.get("currency", ""))).upper()
                if impact not in impacts or currency not in currencies:
                    continue
                when = datetime.fromisoformat(str(item["date"]).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                out.append(
                    Event(
                        title=str(item.get("title", "?")),
                        currency=currency,
                        impact=str(item.get("impact", "")),
                        time=when.astimezone(timezone.utc),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                log.debug("skipping malformed calendar row %r: %s", item, exc)
        return sorted(out, key=lambda e: e.time)

    def _save_cache(self, raw: list[dict[str, Any]]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"fetched_at": self.fetched_at.isoformat(), "events": raw}),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("could not write calendar cache %s: %s", self.cache_path, exc)

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            blob = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.fetched_at = datetime.fromisoformat(blob["fetched_at"])
            self.events = self._parse(blob["events"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("ignoring unreadable calendar cache %s: %s", self.cache_path, exc)
            self.fetched_at = None
            self.events = []
