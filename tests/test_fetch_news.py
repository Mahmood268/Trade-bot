"""Offline test of the fetch pipeline using canned RSS and market responses.

Run: python -m pytest tests/  (or python tests/test_fetch_news.py)
"""

import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_news  # noqa: E402

NOW = datetime.now(timezone.utc)


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>https://example.com/{i}</link>"
        f"<description>{d}</description><pubDate>{format_datetime(NOW - timedelta(hours=h))}</pubDate></item>"
        for i, (t, d, h) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


FEEDS = {
    "https://a.test/rss": rss([
        ("Trump signs new tariff order on Chinese imports", "The White House said...", 1),
        ("Gold hits record high as investors seek safety", "Bullion rose 2%...", 2),
        ("Israel and Iran exchange strikes overnight", "<b>Tensions</b> rise in Tehran", 3),
        ("Very old story about Trump", "", 100),
        ("Local bakery wins award", "Nothing relevant here", 1),
    ]),
    "https://b.test/rss": rss([
        ("Trump signs new tariff order on Chinese imports, White House says", "", 0.5),
        ("Nvidia shares jump after AI chip demand surges", "", 1),
    ]),
    "https://ai.test/rss": rss([("Some product launch", "", 1)]),
    "https://news.google.com/rss/search?q=x": rss([("Stocks rally on Wall Street - Reuters", "", 1)]),
    "https://broken.test/rss": None,
}


MEDIA_RSS = f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>m</title>
<item><title>Gold price climbs on media content feed</title><link>https://m.test/1</link>
 <pubDate>{format_datetime(NOW)}</pubDate>
 <media:content url="https://img.test/small.jpg" medium="image" width="140"/>
 <media:content url="https://img.test/big.jpg" medium="image" width="1200"/></item>
<item><title>Gold miners rally on thumbnail feed</title><link>https://m.test/2</link>
 <pubDate>{format_datetime(NOW)}</pubDate>
 <media:thumbnail url="https://ichef.bbci.co.uk/ace/standard/240/cpsprodpb/abc.jpg"/></item>
<item><title>Gold demand from central banks inline image</title><link>https://m.test/3</link>
 <pubDate>{format_datetime(NOW)}</pubDate>
 <description>&lt;img src="https://img.test/inline.png"&gt; text</description></item>
<item><title>Gold story whose picture is only on the article page</title><link>https://m.test/4</link>
 <pubDate>{format_datetime(NOW)}</pubDate></item>
</channel></rss>"""
ARTICLE_HTML = ('<html><head><meta property="og:image" content="https://img.test/og.jpg">'
                '</head><body>x</body></html>')


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://m.test/rss":
        return httpx.Response(200, text=MEDIA_RSS)
    if url == "https://m.test/4":
        return httpx.Response(200, text=ARTICLE_HTML, headers={"content-type": "text/html"})
    if "finance.yahoo.com" in url:
        if "GC%3DF" in url or "GC=F" in url:
            return httpx.Response(200, json={"chart": {"result": [{
                "meta": {"regularMarketPrice": 2700.0, "previousClose": 2650.0},
                "timestamp": [1, 2, 3, 4],
                "indicators": {"quote": [{"open": [2590, 2610, None, 2660], "high": [2610, 2660, None, 2710],
                                          "low": [2580, 2600, None, 2650], "close": [2600, 2650, None, 2700]}]}}]}})
        return httpx.Response(500)
    body = FEEDS.get(url)
    if body is None:
        return httpx.Response(404)
    return httpx.Response(200, text=body)


CONFIG = {
    "settings": {"max_age_hours": 36, "per_topic": 10},
    "topics": {
        "trump": {"label": "Trump", "keywords": [r"\btrump\b"]},
        "middle_east": {"label": "ME", "keywords": [r"\biran\b", r"\bisrael\b"]},
        "markets": {"label": "Markets", "keywords": [r"\bstocks?\b", r"\bshares\b"]},
        "gold": {"label": "Gold", "keywords": [r"\bgold\b"]},
        "ai": {"label": "AI", "keywords": [r"(?-i:\bA\.?I\b)", r"\bnvidia\b"]},
    },
    "feeds": [
        {"name": "A", "url": "https://a.test/rss"},
        {"name": "B", "url": "https://b.test/rss"},
        {"name": "AI Blog", "url": "https://ai.test/rss", "topics": ["ai"]},
        {"name": "Reuters", "url": "https://news.google.com/rss/search?q=x"},
        {"name": "Broken", "url": "https://broken.test/rss"},
        {"name": "Media", "url": "https://m.test/rss"},
    ],
    "markets": [{"symbol": "GC=F", "label": "Gold"}, {"symbol": "^GSPC", "label": "S&P 500"}],
}


def run(previous=None):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return fetch_news.build(CONFIG, previous or {}, client,
                                quote_sources=[failing_source, lambda s: fetch_news.chart_quote(client, s)],
                                candle_sources=[failing_source, lambda s: fetch_news.chart_candles(client, s)])


def failing_source(symbol):
    raise RuntimeError("primary source down")


def titles(data, key):
    return [i["title"] for t in data["topics"] if t["key"] == key for i in t["items"]]


def test_topics_dedupe_and_filters():
    data = run()
    trump = titles(data, "trump")
    assert len(trump) == 1, trump  # duplicate merged, 100h-old story dropped
    merged = next(t for t in data["topics"] if t["key"] == "trump")["items"][0]
    assert merged["source"] == "A" and merged["also"] == ["B"] or \
        merged["source"] == "B" and merged["also"] == ["A"]
    assert "Gold hits record high as investors seek safety" in titles(data, "gold")
    assert titles(data, "middle_east") == ["Israel and Iran exchange strikes overnight"]
    assert "Some product launch" in titles(data, "ai")  # feed default topic
    assert any("Nvidia" in t for t in titles(data, "ai"))
    assert "Stocks rally on Wall Street" in titles(data, "markets")  # Google suffix stripped
    assert not any("bakery" in t for k in CONFIG["topics"] for t in titles(data, k))
    me = next(t for t in data["topics"] if t["key"] == "middle_east")["items"][0]
    assert me["summary"] == "Tensions rise in Tehran"  # HTML stripped


def test_images():
    images = {i["title"]: i["image"] for t in run()["topics"] for i in t["items"]}
    assert images["Gold price climbs on media content feed"] == "https://img.test/big.jpg"
    assert images["Gold miners rally on thumbnail feed"] == \
        "https://ichef.bbci.co.uk/ace/standard/800/cpsprodpb/abc.jpg"
    assert images["Gold demand from central banks inline image"] == "https://img.test/inline.png"
    assert images["Gold story whose picture is only on the article page"] == "https://img.test/og.jpg"
    assert images["Stocks rally on Wall Street"] is None  # Google News: no picture, no page fetch


def test_feed_failures_are_reported_not_fatal():
    status = {f["name"]: f for f in run()["feeds"]}
    assert not status["Broken"]["ok"] and status["A"]["ok"]


def test_markets_with_stale_fallback():
    previous = {"markets": [{"symbol": "^GSPC", "label": "S&P 500", "price": 5000, "change_pct": 1.0}]}
    markets = {m["symbol"]: m for m in run(previous)["markets"]}
    assert markets["GC=F"]["price"] == 2700.0 and not markets["GC=F"]["stale"]
    assert abs(markets["GC=F"]["change_pct"] - 1.8868) < 0.01
    assert markets["^GSPC"]["stale"] and markets["^GSPC"]["price"] == 5000


def test_candles_with_fallback():
    old_bars = {"1d": [[1, 1, 2, 0.5, 1.5]], "1h": []}
    previous = {"markets": [{"symbol": "^GSPC", "label": "S&P 500", "price": 5000, "candles": old_bars}]}
    markets = {m["symbol"]: m for m in run(previous)["markets"]}
    gold = markets["GC=F"]["candles"]
    assert gold["1d"] == [[1, 2590, 2610, 2580, 2600], [2, 2610, 2660, 2600, 2650], [4, 2660, 2710, 2650, 2700]]
    assert gold["1h"] == gold["1d"]  # the mock serves the same bars for every range
    assert markets["^GSPC"]["candles"] == old_bars  # source down: keep the last published bars


def test_real_config_parses():
    import re
    import yaml
    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "sources.yaml").read_text())
    for spec in config["topics"].values():
        for kw in spec["keywords"]:
            re.compile(kw, re.IGNORECASE)
    for feed in config["feeds"]:
        assert feed["name"] and feed["url"].startswith("http")
        assert set(feed.get("topics", [])) <= set(config["topics"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
