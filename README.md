# Daily Brief

A personal news dashboard that gathers the top headlines from BBC, Al Jazeera,
Reuters, AP and Yahoo Finance (stocks and AI) into one site, with:

- **Four screens, laid out like the Root News app:**
  - **Home:** "Your world today" with a tile per section (Trump & US Politics · Middle East ·
    Stock Market · Gold & Commodities · AI · All Stories).
  - **Signal:** a swipeable Front Page of the top 15 stories with "seen" tracking, topic chips
    that shape the feed, and The Daily Scan: a read-aloud brief plus a short summary per topic.
  - **Globe:** a spinnable globe that lights up the countries in today's stories
    (Explore, Conflict, Elections, Markets); tap a country to read its stories.
  - **Settings:** dark mode, read-aloud speed and voice, Claude API key, sources status.
- **Live market strip:** S&P 500, Gold, Nvidia, Marvell. Tap a price for its candlestick chart
  (5 days of hourly candles, or 1 month to 1 year of daily ones). The Stock Market, Gold and AI
  pages show chart cards for their tickers.
- **AI daily briefing:** Claude summarizes each section about twice a day
- **Ask Claude button on every screen:** ask questions or "what if" forecasts by typing or
  talking (mic). Claude sees today's headlines and prices, is told what you have open (the story
  card, section page, country on the globe or chart) and can search the web. Spoken questions
  get spoken answers.

It updates itself every hour via GitHub Actions and is hosted free on GitHub Pages.

## One-time setup

1. **Merge this branch into `main`.** Scheduled workflows only run from the default branch.
2. **Turn on Pages:** go to repo *Settings → Pages → Build and deployment → Source* and pick
   **GitHub Actions**. On a free GitHub plan, Pages needs the repo to be **public**.
   The site has `noindex`, so search engines skip it, but anyone with the URL can open it.
3. **Add your API key for the briefing:** go to *Settings → Secrets and variables → Actions →
   New repository secret*, set Name to `ANTHROPIC_API_KEY`, and set Value to your key from
   https://console.anthropic.com. Without it the site still works, just without the briefing.
4. **First run:** go to *Actions → Update news → Run workflow*. The site appears at
   `https://<your-username>.github.io/<repo-name>/`.
5. **Chat (switched off for now):** to turn it on, delete the `no-chat` line in `site/index.html`.
   Then open the site, tap the gold ✦ button, then **⚙** and paste your API key.
   The key is saved only in that browser and sent only to Anthropic. It is never stored in
   the repo or the site.

## Costs

- GitHub Actions and Pages: free.
- Briefing: two Claude calls a day at low effort, roughly a few cents per day.
- Chat: pay per question. Opus 5.5 gives the best forecasting reasoning;
  switch to Sonnet 5.5 in ⚙ for cheaper, faster answers. Web search adds a small per-search fee.

## Customize

- **Add or remove sources:** edit the `feeds` section of `config/sources.yaml`. Any RSS/Atom URL works.
  A feed that breaks is skipped and shown under "Sources status" at the bottom of the page.
- **Change what counts as a topic:** edit that topic's `keywords` (regexes) in the same file.
- **Market tickers:** edit `markets` using Yahoo Finance symbols (e.g. `^FTSE`, `EURUSD=X`);
  `topics` picks the section pages that show its chart card.
- **Briefing frequency:** `BRIEFING_MAX_AGE_HOURS` (default 11) in `scripts/briefing.py`.
  To refresh it now, run the workflow with *Regenerate the AI briefing now* ticked.

## Run locally

```bash
pip install -r requirements.txt
python scripts/fetch_news.py            # writes site/data.json
ANTHROPIC_API_KEY=... python scripts/briefing.py   # optional
python -m http.server -d site 8000      # open http://localhost:8000
python tests/test_fetch_news.py         # offline tests
```

## How it works

```
GitHub Action (hourly)
  └─ scripts/fetch_news.py → fetch RSS feeds + Yahoo prices, tag topics, merge duplicates, rank
  └─ scripts/briefing.py   → Claude writes the briefing (skipped if <11h old)
  └─ deploy site/ to GitHub Pages
Browser
  └─ site/app.js  → renders site/data.json (Home, Signal, Settings, topic pages)
  └─ site/globe.js → the Globe screen (d3-geo + world-atlas, vendored in site/vendor/)
  └─ site/chat.js → calls the Claude API directly with your key
```
