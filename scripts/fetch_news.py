"""Fetch headlines from every feed in config/sources.yaml and write site/data.json.

Usage: python scripts/fetch_news.py [--config config/sources.yaml] [--out site/data.json]

A feed that fails is logged and skipped; market prices fall back to the
previous data.json when Yahoo Finance can't be reached.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[a-z0-9]+")
IMG_TAG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
OG_IMAGE_RE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)(?::src)?[\"'][^>]*>", re.IGNORECASE)
CONTENT_RE = re.compile(r"content=[\"']([^\"']+)[\"']", re.IGNORECASE)
IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "at", "by",
    "with", "from", "as", "is", "are", "was", "be", "after", "over", "says",
    "said", "new", "its", "it", "his", "her", "their", "this", "that", "will",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def short_error(exc: Exception) -> str:
    lines = str(exc).splitlines()
    return f"{type(exc).__name__}: {lines[0] if lines else repr(exc)}"


def clean_text(raw: str, limit: int = 280) -> str:
    text = WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", raw or ""))).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            # feedparser normalizes these struct_times to UTC.
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def upgrade_image(url: str) -> str:
    """Swap known small-thumbnail URLs for larger renditions."""
    # BBC serves 240px thumbnails in RSS; the same image exists at 800px.
    return re.sub(r"(ichef\.bbci\.co\.uk/(?:ace|news)/(?:standard|ws))/\d+/", r"\1/800/", url)


def entry_image(entry) -> str | None:
    """Best image for a feed entry: media:content, thumbnail, enclosure, or inline <img>."""
    candidates = []
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        is_image = (media.get("medium") == "image" or media.get("type", "").startswith("image")
                    or IMAGE_EXT_RE.search(url))
        if url and is_image:
            candidates.append((int(media.get("width") or 0), url))
    if candidates:
        return upgrade_image(max(candidates)[1])
    for thumb in entry.get("media_thumbnail", []):
        if thumb.get("url"):
            return upgrade_image(thumb["url"])
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and link.get("type", "").startswith("image") and link.get("href"):
            return link["href"]
    html_parts = [entry.get("summary", "")] + [c.get("value", "") for c in entry.get("content", [])]
    for part in html_parts:
        match = IMG_TAG_RE.search(part or "")
        if match and not match.group(1).startswith("data:"):
            return html.unescape(match.group(1))
    return None


def page_image(client: httpx.Client, url: str) -> str | None:
    """og:image / twitter:image from an article page (only the <head> is read)."""
    try:
        with client.stream("GET", url, timeout=8) as resp:
            if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
                return None
            head = b""
            for chunk in resp.iter_bytes():
                head += chunk
                if len(head) > 300_000 or b"</head>" in head.lower():
                    break
        text = head.decode("utf-8", "ignore")
        for tag in OG_IMAGE_RE.findall(text):
            match = CONTENT_RE.search(tag)
            if match and match.group(1).startswith("http"):
                return html.unescape(match.group(1))
    except Exception:  # noqa: BLE001 - a missing picture is fine
        return None
    return None


def add_page_images(client: httpx.Client, items: list[dict]) -> None:
    """Fill in pictures for shown stories whose feed had none."""
    missing = [i for i in items if not i.get("image") and "news.google.com" not in i["link"]]
    with ThreadPoolExecutor(max_workers=16) as pool:
        for item, image in zip(missing, pool.map(lambda i: page_image(client, i["link"]), missing)):
            item["image"] = image
    found = sum(1 for i in missing if i["image"])
    log(f"Images: {sum(1 for i in items if i.get('image'))}/{len(items)} shown stories "
        f"({found} from article pages)")


def parse_feed(feed: dict, content: bytes) -> list[dict]:
    parsed = feedparser.parse(content)
    items = []
    for entry in parsed.entries:
        title = clean_text(entry.get("title", ""), 300)
        link = entry.get("link", "")
        if not title or not link:
            continue
        source = feed["name"]
        # Google News titles end in " - Outlet"; strip it.
        if "news.google.com" in feed["url"] and " - " in title:
            title = title.rsplit(" - ", 1)[0]
        published = entry_time(entry)
        items.append({
            "title": title,
            "link": link,
            "source": source,
            "published": published.isoformat() if published else None,
            "summary": "" if "news.google.com" in feed["url"]
            else clean_text(entry.get("summary", "") or entry.get("description", "")),
            "image": None if "news.google.com" in feed["url"] else entry_image(entry),
            "feed_topics": list(feed.get("topics", [])),
        })
    return items


def fetch_feed(client: httpx.Client, feed: dict) -> tuple[dict, list[dict], str | None]:
    try:
        resp = client.get(feed["url"])
        resp.raise_for_status()
        items = parse_feed(feed, resp.content)
        if not items:
            return feed, [], "no items"
        return feed, items, None
    except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the run
        return feed, [], short_error(exc)


def compile_topics(topics: dict) -> dict[str, list[re.Pattern]]:
    return {
        key: [re.compile(p, re.IGNORECASE) for p in spec["keywords"]]
        for key, spec in topics.items()
    }


def assign_topics(item: dict, patterns: dict[str, list[re.Pattern]]) -> list[str]:
    text = f"{item['title']} {item['summary']}"
    found = set(item["feed_topics"])
    for key, pats in patterns.items():
        if any(p.search(text) for p in pats):
            found.add(key)
    return [k for k in patterns if k in found]


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in WORD_RE.findall(title.lower()) if w not in STOPWORDS)


def dedupe(items: list[dict], threshold: float = 0.6) -> list[dict]:
    """Merge items whose titles are near-identical; keep the earliest copy."""
    items = sorted(items, key=lambda i: i["published"] or "9999")
    kept: list[dict] = []
    kept_tokens: list[frozenset[str]] = []
    for item in items:
        tokens = title_tokens(item["title"])
        match = None
        if len(tokens) >= 3:
            for idx, other in enumerate(kept_tokens):
                union = tokens | other
                if union and len(tokens & other) / len(union) >= threshold:
                    match = idx
                    break
        if match is None:
            item["also"] = []
            kept.append(item)
            kept_tokens.append(tokens)
            continue
        base = kept[match]
        if item["source"] != base["source"] and item["source"] not in base["also"]:
            base["also"].append(item["source"])
        base["topics"] = list(dict.fromkeys(base["topics"] + item["topics"]))
        if not base["summary"] and item["summary"]:
            base["summary"] = item["summary"]
        if not base.get("image") and item.get("image"):
            base["image"] = item["image"]
    return kept


def score(item: dict, now: datetime) -> float:
    age_h = 24.0
    if item["published"]:
        age_h = max(0.0, (now - datetime.fromisoformat(item["published"])).total_seconds() / 3600)
    return len(item["also"]) * 1.0 - age_h / 12.0


def yfinance_quote(symbol: str) -> tuple[float, float | None, list[float]]:
    """Price, previous close and recent daily closes via yfinance.

    yfinance handles Yahoo's cookie/crumb handshake, which plain requests from
    cloud runners (e.g. GitHub Actions) get rate-limited without.
    """
    import yfinance as yf

    hist = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
    closes = [float(c) for c in hist["Close"].dropna()]
    if not closes:
        raise ValueError("no data")
    return closes[-1], closes[-2] if len(closes) >= 2 else None, closes


def chart_quote(client: httpx.Client, symbol: str) -> tuple[float, float | None, list[float]]:
    """Price, previous close and recent daily closes from Yahoo's chart endpoint."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    resp = client.get(url, params={"range": "5d", "interval": "1d"})
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]
    closes = [c for c in result["indicators"]["quote"][0].get("close", []) if c is not None]
    price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    prev_close = meta.get("previousClose")
    if prev_close is None and len(closes) >= 2:
        prev_close = closes[-2]
    if price is None:
        raise ValueError("no price")
    return price, prev_close, closes


def fetch_markets(quote_sources: list, markets: list[dict], previous: dict) -> list[dict]:
    """Try each quote source in turn; fall back to the last published value."""
    prev_by_symbol = {m["symbol"]: m for m in previous.get("markets", [])}
    out = []
    for m in markets:
        errors = []
        for source in quote_sources:
            try:
                price, prev_close, closes = source(m["symbol"])
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(short_error(exc))
        else:
            log(f"  market {m['symbol']}: {' | '.join(errors)}")
            if m["symbol"] in prev_by_symbol:
                out.append({**prev_by_symbol[m["symbol"]], "stale": True})
            continue
        change = (price - prev_close) / prev_close * 100 if prev_close else None
        out.append({**m, "price": price, "change_pct": change, "history": closes,
                    "stale": False})
    return out


def build(config: dict, previous: dict, client: httpx.Client,
          quote_sources: list | None = None) -> dict:
    settings = config.get("settings", {})
    max_age_h = settings.get("max_age_hours", 36)
    per_topic = settings.get("per_topic", 15)
    patterns = compile_topics(config["topics"])
    now = datetime.now(timezone.utc)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda f: fetch_feed(client, f), config["feeds"]))

    all_items: list[dict] = []
    feed_status = []
    for feed, items, error in results:
        feed_status.append({"name": feed["name"], "url": feed["url"], "ok": error is None,
                            "count": len(items), "error": error})
        if error:
            log(f"  FAIL {feed['name']} ({feed['url']}): {error}")
        all_items.extend(items)
    ok = sum(1 for s in feed_status if s["ok"])
    log(f"Feeds: {ok}/{len(feed_status)} ok, {len(all_items)} raw items")

    fresh = []
    for item in all_items:
        if item["published"]:
            age_h = (now - datetime.fromisoformat(item["published"])).total_seconds() / 3600
            if age_h > max_age_h or age_h < -2:
                continue
        item["topics"] = assign_topics(item, patterns)
        if item["topics"]:
            fresh.append(item)

    merged = dedupe(fresh)
    for item in merged:
        item.pop("feed_topics", None)

    topics_out = []
    for key, spec in config["topics"].items():
        ranked = sorted((i for i in merged if key in i["topics"]),
                        key=lambda i: score(i, now), reverse=True)
        topics_out.append({"key": key, "label": spec["label"], "items": ranked[:per_topic]})
        log(f"  {spec['label']}: {len(ranked)} stories")

    shown = list({id(i): i for t in topics_out for i in t["items"]}.values())
    add_page_images(client, shown)

    return {
        "generated_at": now.isoformat(),
        "topics": topics_out,
        "markets": fetch_markets(quote_sources or [yfinance_quote, lambda s: chart_quote(client, s)],
                                 config.get("markets", []), previous),
        "briefing": previous.get("briefing"),
        "feeds": feed_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=ROOT / "config" / "sources.yaml", type=Path)
    parser.add_argument("--out", default=ROOT / "site" / "data.json", type=Path)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text())
    previous = {}
    if args.out.exists():
        try:
            previous = json.loads(args.out.read_text())
        except json.JSONDecodeError:
            log("Previous data.json unreadable; starting fresh")

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20,
                      follow_redirects=True) as client:
        data = build(config, previous, client)

    if not any(t["items"] for t in data["topics"]):
        log("No headlines fetched at all; keeping previous data.json")
        return 1 if not previous else 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    log(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
