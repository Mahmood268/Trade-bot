// Renders data.json: market strip, AI briefing, topic tabs and story cards.
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
    const w = 40, h = 16, min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) =>
      `${(i / (values.length - 1)) * w},${h - 2 - ((v - min) / span) * (h - 4)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <polyline points="${pts}" fill="none" stroke="var(${up ? "--up" : "--down"})" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  }

  function renderMarkets(markets) {
    $("#markets").innerHTML = (markets || []).map((m) => {
      const up = (m.change_pct ?? 0) >= 0;
      const chg = m.change_pct == null ? "" :
        `<span class="chg ${up ? "up" : "down"}">${up ? "+" : "−"}${Math.abs(m.change_pct).toFixed(2)}%</span>`;
      return `<div class="ticker${m.stale ? " stale" : ""}" title="${m.stale ? "Last known value" : ""}">
        <span class="label">${esc(m.label)}</span><span class="price">${fmtPrice(m.price)}</span>${chg}${sparkline(m.history, up)}
      </div>`;
    }).join("");
  }

  function renderBriefing(b) {
    const el = $("#briefing");
    if (!b) { el.hidden = true; return; }
    el.hidden = false;
    const topicKey = (label) => state.data.topics.find((t) => t.label === label)?.key || "";
    el.innerHTML = `
      <summary>
        <span class="spark" aria-hidden="true">✦</span>
        <span class="lede">
          <span class="kicker">Today in 30 seconds · ${esc(timeAgo(b.generated_at))}</span>
          <p>${esc(b.headline)}</p>
        </span>
        <span class="toggle"><span class="more">Read briefing ↓</span><span class="less">Close ↑</span></span>
      </summary>
      <div class="briefing-body">
        <div class="briefing-grid">${(b.sections || []).map((s) => `
          <div data-topic="${esc(topicKey(s.topic))}"><h3><span class="dot"></span>${esc(s.topic)}</h3>
          <ul>${s.bullets.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")}
        </div>
        ${b.watch_next?.length ? `<div class="watch"><h3>👀 Watch next</h3><ul>${
          b.watch_next.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
      </div>`;
  }

  function matches(item) {
    if (!state.query) return true;
    const q = state.query.toLowerCase();
    return `${item.title} ${item.summary} ${item.source}`.toLowerCase().includes(q);
  }

  function labelOf(key) {
    return state.data.topics.find((t) => t.key === key)?.label || "";
  }

  function thumbHtml(item) {
    const link = esc(item.link);
    if (item.image) {
      return `<a class="thumb" href="${link}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true">
        <img src="${esc(item.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-source="${esc(item.source)}"></a>`;
    }
    return `<a class="thumb fallback" href="${link}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true">
      <span>${esc(item.source)}</span></a>`;
  }

  // One story card. `topic` decides the color; `opts.summary` shows the snippet.
  function storyHtml(item, topic, opts = {}) {
    const extra = (item.also || []).length;
    return `<article class="story${opts.hero ? " hero" : ""}" data-topic="${esc(topic)}">
      ${thumbHtml(item)}
      <div class="body">
        ${opts.kicker ? `<span class="kicker">${esc(labelOf(topic))}</span>` : ""}
        <a class="title" href="${esc(item.link)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
        ${opts.summary && item.summary ? `<p class="summary">${esc(item.summary)}</p>` : ""}
        <div class="meta">
          <span class="source">${esc(item.source)}</span>
          ${extra ? `<span title="${esc(item.also.join(", "))}">+${extra}</span>` : ""}
          <span class="sep">·</span><span>${esc(timeAgo(item.published))}</span>
          <button class="ask-btn" data-title="${esc(item.title)}" data-source="${esc(item.source)}" title="Ask Claude about this">✦ Ask</button>
        </div>
      </div>
    </article>`;
  }

  // Lead story: the most widely reported story, preferring one with a photo.
  function pickHero(items) {
    const pool = items.slice(0, 8);
    const rank = (i) => (i.also?.length || 0) * 2 + (i.image ? 3 : 0);
    return pool.reduce((best, i) => (rank(i) > rank(best) ? i : best), pool[0]);
  }

  function renderOverview(el) {
    const topics = state.data.topics;
    const leads = topics.map((t) => t.items[0] && { ...t.items[0], _topic: t.key }).filter(Boolean);
    const hero = pickHero(leads);
    const used = new Set([hero?.link]);
    el.innerHTML = (hero ? storyHtml(hero, hero._topic, { hero: true, summary: true, kicker: true }) : "") +
      topics.map((t) => {
        const items = t.items.filter((i) => !used.has(i.link)).slice(0, 4);
        items.forEach((i) => used.add(i.link));
        if (!items.length) return "";
        return `<section class="section" data-topic="${esc(t.key)}">
          <div class="section-head">
            <h2><span class="dot"></span>${esc(t.label)}</h2>
            <button class="see-all" data-tab="${esc(t.key)}">See all →</button>
          </div>
          <div class="grid">${items.map((i) => storyHtml(i, t.key)).join("")}</div>
        </section>`;
      }).join("");
  }

  function renderTopic(el, t) {
    const hero = pickHero(t.items);
    const rest = t.items.filter((i) => i !== hero);
    el.innerHTML = hero
      ? storyHtml(hero, t.key, { hero: true, summary: true, kicker: true }) +
        `<section class="section"><div class="grid three">${
          rest.map((i) => storyHtml(i, t.key, { summary: true })).join("")}</div></section>`
      : `<p class="empty">No stories right now.</p>`;
  }

  function renderSearch(el) {
    const seen = new Set();
    const results = [];
    for (const t of state.data.topics) {
      for (const i of t.items) {
        if (matches(i) && !seen.has(i.link)) { seen.add(i.link); results.push([i, t.key]); }
      }
    }
    el.innerHTML = `<section class="section"><div class="section-head"><h2>${results.length} result${
      results.length === 1 ? "" : "s"} for “${esc(state.query)}”</h2></div>${
      results.length ? `<div class="grid">${results.map(([i, k]) => storyHtml(i, k, { kicker: true })).join("")}</div>`
        : `<p class="empty">Nothing matches. Try another word.</p>`}</section>`;
  }

  function renderContent() {
    const el = $("#content");
    if (state.query) return renderSearch(el);
    if (state.tab === "overview") return renderOverview(el);
    renderTopic(el, state.data.topics.find((x) => x.key === state.tab));
  }

  function renderTabs() {
    const tabs = [{ key: "overview", label: "Top stories" }, ...state.data.topics];
    $("#tabs").innerHTML = tabs.map((t) =>
      `<button class="tab" role="tab" data-tab="${esc(t.key)}" data-topic="${esc(t.key)}" aria-selected="${
        !state.query && t.key === state.tab}">${t.key === "overview" ? "" : '<span class="dot"></span>'}${esc(t.label)}</button>`
    ).join("");
  }

  function setTab(key) {
    state.tab = key;
    state.query = "";
    $("#search").value = "";
    try { localStorage.setItem("tab", key); } catch {}
    renderTabs();
    renderContent();
  }

  function renderFeeds(feeds) {
    $("#feeds").innerHTML = (feeds || []).map((f) =>
      `<li class="${f.ok ? "" : "bad"}" title="${esc(f.error || f.url)}">${esc(f.name)}: ${f.ok ? `${f.count} items` : "failed"}</li>`
    ).join("");
  }

  function renderUpdated() {
    if (!state.data) return;
    const today = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
    $("#updated").textContent = `${today} · Updated ${timeAgo(state.data.generated_at)}`;
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
    try {
      const saved = localStorage.getItem("tab");
      if (saved && (saved === "overview" || state.data.topics.some((t) => t.key === saved))) state.tab = saved;
    } catch {}
    renderUpdated();
    renderMarkets(state.data.markets);
    renderBriefing(state.data.briefing);
    renderTabs();
    renderContent();
    renderFeeds(state.data.feeds);
  }

  // Broken or hotlink-blocked photos fall back to the colored placeholder.
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (img.tagName !== "IMG" || !img.closest(".thumb")) return;
    const thumb = img.closest(".thumb");
    thumb.classList.add("fallback");
    thumb.innerHTML = `<span>${esc(img.dataset.source)}</span>`;
  }, true);

  document.addEventListener("click", (e) => {
    const tabBtn = e.target.closest("[data-tab]");
    if (tabBtn) {
      setTab(tabBtn.dataset.tab);
      if (tabBtn.classList.contains("see-all") || window.scrollY > $("#tabs").offsetTop) {
        window.scrollTo({ top: $("#content").offsetTop - 70, behavior: "smooth" });
      }
      return;
    }
    const ask = e.target.closest(".ask-btn");
    if (ask) {
      window.dispatchEvent(new CustomEvent("ask-claude", { detail: {
        text: `Explain this headline and what could happen next (scenarios + likely market impact):\n"${ask.dataset.title}" (${ask.dataset.source})`,
      } }));
    }
  });

  $("#search").addEventListener("input", (e) => {
    state.query = e.target.value.trim();
    if (state.data) { renderTabs(); renderContent(); }
  });

  $("#theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch {}
  });

  load();
  setInterval(renderUpdated, 60 * 1000);
  // Pick up the hourly rebuild without a manual reload.
  setInterval(load, 15 * 60 * 1000);
})();
