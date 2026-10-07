// Candlestick charts for the market tickers. Tap a price in the top strip (or a
// chart card on a section page) to open one. Bars come from data.json, refreshed
// hourly; Lightweight Charts (vendored) is loaded the first time a chart opens.
(() => {
  const $ = (sel) => document.querySelector(sel);
  const RANGES = [
    { key: "5D", set: "1h", bars: Infinity },
    { key: "1M", set: "1d", bars: 22 },
    { key: "3M", set: "1d", bars: 66 },
    { key: "6M", set: "1d", bars: 130 },
    { key: "1Y", set: "1d", bars: Infinity },
  ];
  // Fallback for data published before `topics` was added to the config.
  const DEFAULT_TOPICS = { "^GSPC": ["markets"], "GC=F": ["gold"], NVDA: ["markets", "ai"], MRVL: ["markets", "ai"] };

  const state = { symbol: null, range: "3M", chart: null, series: null };
  let libPromise = null;

  function loadLib() {
    libPromise ||= new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "vendor/lightweight-charts.standalone.production.js";
      s.onload = () => resolve(window.LightweightCharts);
      s.onerror = () => { libPromise = null; reject(new Error("chart library failed to load")); };
      document.head.append(s);
    });
    return libPromise;
  }

  const markets = () => window.NEWS_DATA?.markets || [];
  const market = (symbol) => markets().find((m) => m.symbol === symbol);
  const fmt = (v) => v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: v >= 1000 ? 0 : 2 });
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  // Some systems report tags like "en-US@posix" that Intl rejects, which would break the chart.
  const locale = (() => {
    try { return Intl.DateTimeFormat.supportedLocalesOf([navigator.language])[0] || "en-US"; } catch { return "en-US"; }
  })();

  function bars(m, rangeKey) {
    const r = RANGES.find((x) => x.key === rangeKey);
    const all = m?.candles?.[r.set] || [];
    return all.slice(-Math.min(all.length, r.bars));
  }

  function changeHtml(m) {
    if (m.change_pct == null) return "";
    const up = m.change_pct >= 0;
    return `<span class="chg ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(m.change_pct).toFixed(2)}%</span>`;
  }

  // ---------- the sheet ----------

  async function open(symbol) {
    const m = market(symbol);
    if (!m) return;
    const { esc } = window.NEWS_APP;
    state.symbol = symbol;
    $("#chart-title").textContent = m.label;
    $("#chart-price").innerHTML = `${fmt(m.price)} ${changeHtml(m)}${m.stale ? ' <span class="muted">· last known</span>' : ""}`;
    $("#chart-ranges").innerHTML = RANGES.map((r) =>
      `<button class="range-chip" data-range="${r.key}" aria-pressed="${r.key === state.range}">${r.key}</button>`).join("");
    $("#chart-live").href = `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}/chart`;
    $("#chart-ask").dataset.symbol = symbol;
    $("#chart-ask").textContent = `✦ Ask Claude about ${m.label}`;
    $("#chart-sheet").hidden = false;
    $("#chart-ohlc").textContent = "";
    try {
      await loadLib();
      draw();
    } catch (err) {
      $("#chart-box").innerHTML = `<p class="empty">Couldn't load the chart (${esc(err.message)}).</p>`;
    }
  }

  function close() {
    $("#chart-sheet").hidden = true;
    state.symbol = null;
    state.chart?.remove();
    state.chart = state.series = null;
  }

  function draw() {
    const LC = window.LightweightCharts;
    const m = market(state.symbol);
    const data = bars(m, state.range).map(([time, open, high, low, close]) => ({ time, open, high, low, close }));
    state.chart?.remove();
    state.chart = state.series = null;
    const box = $("#chart-box");
    box.innerHTML = "";
    if (!data.length) {
      box.innerHTML = `<p class="empty">No candles yet. They arrive with the next hourly update.</p>`;
      return;
    }
    const up = css("--up"), down = css("--down");
    state.chart = LC.createChart(box, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: css("--muted"), fontFamily: "Inter, system-ui, sans-serif", attributionLogo: true },
      grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
      rightPriceScale: { borderColor: css("--line") },
      timeScale: { borderColor: css("--line"), timeVisible: state.range === "5D", secondsVisible: false,
        tickMarkFormatter: (t, type) => new Date(t * 1000).toLocaleString(locale,
          type >= 3 ? { hour: "2-digit", minute: "2-digit" } : type === 2 ? { day: "numeric" } : type === 1 ? { month: "short" } : { year: "numeric" }) },
      crosshair: { mode: LC.CrosshairMode.Normal },
      // Show times in the reader's own time zone (the library uses UTC).
      localization: { locale, timeFormatter: (t) => new Date(t * 1000).toLocaleString(locale,
        state.range === "5D" ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short", year: "numeric" }) },
    });
    state.series = state.chart.addSeries(LC.CandlestickSeries, {
      upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false,
    });
    state.series.setData(data);
    state.chart.timeScale().fitContent();
    const last = data[data.length - 1];
    const showOhlc = (b) => {
      $("#chart-ohlc").textContent = `O ${fmt(b.open)}  H ${fmt(b.high)}  L ${fmt(b.low)}  C ${fmt(b.close)}`;
    };
    showOhlc(last);
    state.chart.subscribeCrosshairMove((p) => showOhlc(p?.seriesData?.get(state.series) || last));
  }

  // ---------- section page cards ----------

  function cardsFor(topicKey) {
    const list = markets().filter((m) => (m.topics || DEFAULT_TOPICS[m.symbol] || []).includes(topicKey));
    if (!list.length) return "";
    const { esc } = window.NEWS_APP;
    return `<div class="chart-cards">${list.map((m) => {
      const closes = (m.candles?.["1d"] || []).slice(-30).map((b) => b[4]);
      return `<button class="chart-card" data-chart="${esc(m.symbol)}">
        <span class="cc-top"><span class="cc-label">${esc(m.label)}</span>${changeHtml(m)}</span>
        <span class="cc-price">${fmt(m.price)}</span>
        ${spark(closes.length > 1 ? closes : m.history || [], (m.change_pct ?? 0) >= 0)}
        <span class="cc-hint">Candles ›</span>
      </button>`;
    }).join("")}</div>`;
  }

  function spark(values, up) {
    if (values.length < 2) return "";
    const w = 120, h = 32, min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 3 - ((v - min) / span) * (h - 6)}`).join(" ");
    return `<svg class="cc-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${pts}" fill="none" stroke="var(${up ? "--up" : "--down"})" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
  }

  // ---------- for Ask Claude ----------

  // What the open chart shows, so questions like "where is this heading?" have context.
  function view() {
    if (!state.symbol) return null;
    const m = market(state.symbol);
    const b = bars(m, state.range);
    if (!b.length) return { label: `${m.label} chart`, text: `${m.label} chart open, but it has no candles yet. Price ${m.price}.` };
    const highs = b.map((x) => x[2]), lows = b.map((x) => x[3]);
    const first = b[0][1], last = b[b.length - 1][4];
    const day = (t) => new Date(t * 1000).toISOString().slice(0, state.range === "5D" ? 16 : 10).replace("T", " ");
    const recent = b.slice(-12).map((x) => `${day(x[0])} O ${x[1]} H ${x[2]} L ${x[3]} C ${x[4]}`);
    return {
      label: `${m.label} chart · ${state.range}`,
      text: [`Candlestick chart of ${m.label} (${m.symbol}), ${state.range} range, ${state.range === "5D" ? "hourly" : "daily"} candles.`,
        `Latest price ${m.price}${m.change_pct != null ? ` (${m.change_pct.toFixed(2)}% on the day)` : ""}.`,
        `Range: open ${first}, close ${last} (${(((last - first) / first) * 100).toFixed(1)}%), high ${Math.max(...highs)}, low ${Math.min(...lows)}.`,
        "Most recent candles:", ...recent.map((x) => `- ${x}`)].join("\n"),
    };
  }

  window.NEWS_CHARTS = { open, cardsFor, view };

  document.addEventListener("click", (e) => {
    const t = e.target;
    const opener = t.closest("[data-chart]");
    if (opener) { open(opener.dataset.chart); return; }
    const range = t.closest("[data-range]");
    if (range) {
      state.range = range.dataset.range;
      document.querySelectorAll("[data-range]").forEach((b) => b.setAttribute("aria-pressed", b === range));
      draw();
      return;
    }
    const ask = t.closest("#chart-ask");
    if (ask) {
      const m = market(ask.dataset.symbol);
      // The chart stays open under the chat, so Claude is told what it shows.
      window.dispatchEvent(new CustomEvent("ask-claude", { detail: {
        text: `Read the ${m.label} chart for me: the trend, key levels, and what could move it next (scenarios with rough odds).`,
      } }));
      return;
    }
    if (t.closest("[data-close-chart]") || t.id === "chart-sheet") close();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.symbol) close(); });
  window.addEventListener("hashchange", () => { if (state.symbol) close(); });
  window.addEventListener("theme-changed", () => { if (state.chart) draw(); });
})();
