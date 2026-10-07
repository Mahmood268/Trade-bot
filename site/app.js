// Renders data.json: market tickers, AI briefing, topic tabs and headlines.
(() => {
  const $ = (sel) => document.querySelector(sel);
  const state = { data: null, tab: "overview", query: "" };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function timeAgo(iso) {
    if (!iso) return "";
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const h = Math.round(mins / 60);
    return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
  }

  function fmtPrice(p) {
    if (p == null) return "—";
    return p.toLocaleString(undefined, { maximumFractionDigits: p >= 1000 ? 0 : 2 });
  }

  function sparkline(values, up) {
    if (!values || values.length < 2) return "";
    const w = 64, h = 22, min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) =>
      `${(i / (values.length - 1)) * w},${h - 2 - ((v - min) / span) * (h - 4)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <polyline points="${pts}" fill="none" stroke="var(${up ? "--up" : "--down"})" stroke-width="1.5"/></svg>`;
  }

  function renderMarkets(markets) {
    $("#markets").innerHTML = (markets || []).map((m) => {
      const up = (m.change_pct ?? 0) >= 0;
      const chg = m.change_pct == null ? "" :
        `<span class="chg ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(m.change_pct).toFixed(2)}%</span>`;
      return `<div class="ticker${m.stale ? " stale" : ""}" title="${m.stale ? "Stale — last known value" : ""}">
        <div class="label">${esc(m.label)}</div>
        <div class="row"><div><div class="price">${fmtPrice(m.price)}</div>${chg}</div>${sparkline(m.history, up)}</div>
      </div>`;
    }).join("");
  }

  function renderBriefing(b) {
    const el = $("#briefing");
    if (!b) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `
      <h2>Today's briefing <span class="muted small">· AI summary, ${esc(timeAgo(b.generated_at))}</span></h2>
      <p class="lede">${esc(b.headline)}</p>
      <div class="briefing-grid">${(b.sections || []).map((s) => `
        <div><h3>${esc(s.topic)}</h3><ul>${s.bullets.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")}
      </div>
      ${b.watch_next?.length ? `<div class="watch"><h3>Watch next</h3><ul>${
        b.watch_next.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}`;
  }

  function matches(item) {
    if (!state.query) return true;
    const q = state.query.toLowerCase();
    return `${item.title} ${item.summary} ${item.source}`.toLowerCase().includes(q);
  }

  function headlineHtml(item, withSummary) {
    const outlets = [item.source, ...(item.also || [])];
    return `<li class="headline">
      <a class="title" href="${esc(item.link)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
      ${withSummary && item.summary ? `<p class="summary">${esc(item.summary)}</p>` : ""}
      <div class="meta">
        <span class="badge">${esc(item.source)}</span>
        ${outlets.length > 1 ? `<span title="${esc(outlets.slice(1).join(", "))}">+${outlets.length - 1} more</span>` : ""}
        <span>${esc(timeAgo(item.published))}</span>
        <button class="ask-btn" data-title="${esc(item.title)}" data-source="${esc(item.source)}">Ask Claude</button>
      </div>
    </li>`;
  }

  function renderContent() {
    const topics = state.data.topics;
    const el = $("#content");
    if (state.tab === "overview") {
      el.className = "grid";
      el.innerHTML = topics.map((t) => {
        const items = t.items.filter(matches).slice(0, 6);
        return `<section class="card topic">
          <h2>${esc(t.label)} <button data-tab="${esc(t.key)}">See all →</button></h2>
          <ul class="headlines">${items.map((i) => headlineHtml(i, false)).join("") ||
            `<li class="empty">No matching headlines.</li>`}</ul>
        </section>`;
      }).join("");
    } else {
      const t = topics.find((x) => x.key === state.tab);
      const items = t.items.filter(matches);
      el.className = "single";
      el.innerHTML = `<section class="card topic"><h2>${esc(t.label)}</h2>
        <ul class="headlines">${items.map((i) => headlineHtml(i, true)).join("") ||
          `<li class="empty">No matching headlines.</li>`}</ul></section>`;
    }
  }

  function renderTabs() {
    const tabs = [{ key: "overview", label: "Overview" }, ...state.data.topics];
    $("#tabs").innerHTML = tabs.map((t) =>
      `<button class="tab" role="tab" data-tab="${esc(t.key)}" aria-selected="${t.key === state.tab}">${esc(t.label)}</button>`
    ).join("");
  }

  function setTab(key) {
    state.tab = key;
    try { localStorage.setItem("tab", key); } catch {}
    renderTabs();
    renderContent();
  }

  function renderFeeds(feeds) {
    $("#feeds").innerHTML = (feeds || []).map((f) =>
      `<li class="${f.ok ? "" : "bad"}" title="${esc(f.error || f.url)}">${esc(f.name)}: ${f.ok ? `${f.count} items` : "failed"}</li>`
    ).join("");
  }

  async function load() {
    try {
      const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
    } catch (err) {
      $("#updated").textContent = `Could not load news (${err.message}).`;
      return;
    }
    window.NEWS_DATA = state.data;
    window.dispatchEvent(new CustomEvent("news-loaded"));
    const d = new Date(state.data.generated_at);
    $("#updated").textContent = `Updated ${timeAgo(state.data.generated_at)} · ${d.toLocaleString()}`;
    try {
      const saved = localStorage.getItem("tab");
      if (saved && (saved === "overview" || state.data.topics.some((t) => t.key === saved))) state.tab = saved;
    } catch {}
    renderMarkets(state.data.markets);
    renderBriefing(state.data.briefing);
    renderTabs();
    renderContent();
    renderFeeds(state.data.feeds);
  }

  document.addEventListener("click", (e) => {
    const tabBtn = e.target.closest("[data-tab]");
    if (tabBtn) { setTab(tabBtn.dataset.tab); window.scrollTo({ top: $("#tabs").offsetTop - 8, behavior: "smooth" }); return; }
    const ask = e.target.closest(".ask-btn");
    if (ask) {
      window.dispatchEvent(new CustomEvent("ask-claude", { detail: {
        text: `Explain this headline and what could happen next (scenarios + likely market impact):\n"${ask.dataset.title}" (${ask.dataset.source})`,
      } }));
    }
  });

  $("#search").addEventListener("input", (e) => { state.query = e.target.value.trim(); if (state.data) renderContent(); });

  $("#theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch {}
  });

  load();
  // Pick up the hourly rebuild without a manual reload.
  setInterval(load, 15 * 60 * 1000);
})();
