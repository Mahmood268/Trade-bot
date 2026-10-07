// Live TradingView chart on the Stock Market page (TradingView's free embed widget).
// It streams its own prices and has a symbol search, so any stock can be looked up.
(() => {
  const $ = (sel) => document.querySelector(sel);
  const PICKS = [
    { label: "S&P 500", symbol: "AMEX:SPY" },
    { label: "Nvidia", symbol: "NASDAQ:NVDA" },
    { label: "Marvell", symbol: "NASDAQ:MRVL" },
    { label: "Gold", symbol: "OANDA:XAUUSD" },
  ];
  let current = PICKS[1].symbol;

  function mount() {
    const box = $("#tv-chart");
    if (!box) return;
    const dark = document.documentElement.dataset.theme !== "light";
    box.innerHTML = `<div class="tradingview-widget-container" style="height:100%;width:100%">
      <div class="tradingview-widget-container__widget" style="height:calc(100% - 32px);width:100%"></div>
      <div class="tradingview-widget-copyright"><a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">Chart by TradingView</a></div>
    </div>`;
    const s = document.createElement("script");
    s.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    s.async = true;
    s.textContent = JSON.stringify({
      autosize: true, symbol: current, interval: "D", timezone: "exchange", theme: dark ? "dark" : "light",
      style: "1", locale: "en", allow_symbol_change: true, hide_side_toolbar: true, calendar: false,
      backgroundColor: dark ? "#16171d" : "#ffffff", gridColor: dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)",
      support_host: "https://www.tradingview.com",
    });
    box.firstElementChild.append(s);
  }

  // HTML for the section; app.js puts it on the Stock Market page.
  function section() {
    return `<section class="tv">
      <div class="tv-head"><h3 class="label">Live chart</h3><span class="muted">Tap the symbol at the top of the chart to search any stock</span></div>
      <div class="tv-picks">${PICKS.map((p) =>
        `<button class="range-chip" data-tv="${p.symbol}" aria-pressed="${p.symbol === current}">${p.label}</button>`).join("")}</div>
      <div id="tv-chart" class="tv-chart"></div>
    </section>`;
  }

  window.NEWS_TV = { section, mount };

  document.addEventListener("click", (e) => {
    const pick = e.target.closest("[data-tv]");
    if (!pick) return;
    current = pick.dataset.tv;
    document.querySelectorAll("[data-tv]").forEach((b) => b.setAttribute("aria-pressed", b === pick));
    mount();
  });
  window.addEventListener("theme-changed", () => { if ($("#tv-chart")) mount(); });
})();
