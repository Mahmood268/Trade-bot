// Renders data.json as four screens: Home (topic tiles), Signal (front page
// and daily scan), Globe (globe.js) and Settings, plus a page per topic.
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];
  const state = { data: null, query: "", frontIndex: 0 };
  const FRONT_PAGE_SIZE = 15;

  const store = {
    get(key, fallback = null) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch {} },
    json(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Look of each topic: tile color and icon. Unknown topics get the default.
  const ICONS = {
    capitol: '<path d="M12 3 3 8h18zM5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20.5h18"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    chart: '<path d="M4 4v16h16"/><path d="m7 14 4-4 3 3 5-6"/>',
    bars: '<path d="M2.5 20 5 14h6.5l2 6zM10.5 20l2-6H19l2.5 6zM6.5 12.5 9 7h6l2.5 5.5z"/>',
    chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9.5 9.5h5v5h-5zM9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
    paper: '<path d="M4 5h13v14H6a2 2 0 0 1-2-2zM17 8h3v9a2 2 0 0 1-2 2M7 9h7M7 12h7M7 15h4"/>',
  };
  const LOOKS = {
    trump: { short: "US", color: "#e5534b", icon: "capitol" },
    middle_east: { short: "Middle East", color: "#f0883e", icon: "globe" },
    markets: { short: "Markets", color: "#3fa45b", icon: "chart" },
    gold: { short: "Gold", color: "#e9b03b", icon: "bars" },
    ai: { short: "AI", color: "#3fb8d0", icon: "chip" },
    all: { short: "All", color: "#4a8fe7", icon: "paper" },
  };
  const look = (key) => LOOKS[key] || { short: labelOf(key), color: "#8b8b95", icon: "paper" };
  const icon = (key, cls = "") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[look(key).icon]}</svg>`;

  // ---------- helpers ----------

  function timeAgo(iso) {
    if (!iso) return "";
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const h = Math.round(mins / 60);
    return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
  }

  const today = () => new Date().toLocaleDateString("en-CA");
  const partOfDay = () => {
    const h = new Date().getHours();
    return h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
  };

  function fmtPrice(p) {
    if (p == null) return "—";
    return p.toLocaleString(undefined, { maximumFractionDigits: p >= 1000 ? 0 : 2 });
  }

  function labelOf(key) {
    if (key === "all") return "All Stories";
    return state.data?.topics.find((t) => t.key === key)?.label || "";
  }

  function topicOf(key) { return state.data.topics.find((t) => t.key === key); }

  // Read/scanned progress resets every day.
  function daily(key) {
    const saved = store.json(key, null);
    return saved && saved.date === today() ? new Set(saved.items) : new Set();
  }
  function saveDaily(key, set) { store.set(key, JSON.stringify({ date: today(), items: [...set] })); }
  let seen = daily("seen");
  let scanned = daily("scanned");

  function myTopics() {
    const keys = state.data.topics.map((t) => t.key);
    const saved = store.json("my-topics", null);
    const picked = Array.isArray(saved) ? saved.filter((k) => keys.includes(k)) : keys;
    return picked.length ? picked : keys;
  }

  function briefingFor(key) {
    const label = labelOf(key);
    return state.data.briefing?.sections?.find((s) => s.topic === label) || null;
  }

  function outlets(item) { return [item.source, ...(item.also || [])]; }

  function askAbout(item) {
    window.dispatchEvent(new CustomEvent("ask-claude", { detail: {
      text: `Explain this headline and what could happen next (scenarios + likely market impact):\n"${item.title}" (${item.source})`,
    } }));
  }

  // ---------- markets ----------

  function sparkline(values, up) {
    if (!values || values.length < 2) return "";
    const w = 36, h = 14, min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) =>
      `${(i / (values.length - 1)) * w},${h - 2 - ((v - min) / span) * (h - 4)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <polyline points="${pts}" fill="none" stroke="var(${up ? "--up" : "--down"})" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  }

  function renderMarkets(markets) {
    $("#markets").innerHTML = `<div class="markets-inner">${(markets || []).map((m) => {
      const up = (m.change_pct ?? 0) >= 0;
      const chg = m.change_pct == null ? "" :
        `<span class="chg ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(m.change_pct).toFixed(2)}%</span>`;
      return `<button class="ticker${m.stale ? " stale" : ""}" data-chart="${esc(m.symbol)}" title="${m.stale ? "Last known value · " : ""}Open the ${esc(m.label)} chart">
        <span class="t-label">${esc(m.label)}</span><span class="price">${fmtPrice(m.price)}</span>${chg}${sparkline(m.history, up)}
      </button>`;
    }).join("")}</div>`;
  }

  // ---------- Home ----------

  function renderHome() {
    const tiles = [...state.data.topics.map((t) => t.key), "all"];
    $("#tiles").innerHTML = tiles.map((key) => {
      const count = key === "all" ? allStories().length : topicOf(key).items.length;
      return `<a class="tile" href="#topic/${esc(key)}" style="--tc:${look(key).color}">
        <span class="tile-icon">${icon(key)}</span>
        <span class="tile-label">${esc(labelOf(key))}</span>
        <span class="tile-count">${count} ${count === 1 ? "story" : "stories"}</span>
      </a>`;
    }).join("");
  }

  // ---------- Signal ----------

  // Up to 15 stories across the reader's topics, taking each topic's best in turn.
  function frontPage() {
    const lists = myTopics().map((k) => topicOf(k).items.map((i) => ({ item: i, key: k })));
    const out = [], links = new Set();
    for (let rank = 0; out.length < FRONT_PAGE_SIZE && lists.some((l) => l.length > rank); rank++) {
      for (const l of lists) {
        const entry = l[rank];
        if (entry && !links.has(entry.item.link) && out.length < FRONT_PAGE_SIZE) {
          links.add(entry.item.link);
          out.push(entry);
        }
      }
    }
    return out;
  }

  function frontCard({ item, key }, idx) {
    const lk = look(key);
    const n = outlets(item).length;
    return `<article class="front-card" data-idx="${idx}" data-link="${esc(item.link)}" style="--tc:${lk.color}">
      <div class="card-top">
        <span class="topic-pill">${icon(key)}${esc(lk.short)}</span>
        <span class="seen-mark">✓ Seen</span>
      </div>
      <a class="card-title serif" href="#" data-story="${esc(item.link)}">${esc(item.title)}</a>
      ${item.summary ? `<p class="card-summary" data-story="${esc(item.link)}">${esc(item.summary)}</p>` : ""}
      <p class="card-meta">${esc(item.source)}${n > 1 ? ` <span title="${esc(outlets(item).join(", "))}">+${n - 1} more</span>` : ""} · ${esc(timeAgo(item.published))}</p>
      <button class="card-foot chat-only" data-analyze="${idx}">
        <span>Get Claude's analysis</span><span class="badge">✦ AI</span>
        <span class="round-arrow" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></span>
      </button>
    </article>`;
  }

  let front = [];
  function renderFront() {
    front = frontPage();
    $("#front").innerHTML = front.length ? front.map(frontCard).join("")
      : `<p class="empty">No stories in your topics right now.</p>`;
    $("#dots").innerHTML = front.map((_, i) => `<span data-dot="${i}"></span>`).join("");
    $("#front").scrollLeft = 0;
    state.frontIndex = 0;
    updateSeen();
    observeFront();
  }

  function updateSeen() {
    $$(".front-card").forEach((c) => c.classList.toggle("is-seen", seen.has(c.dataset.link)));
    const n = front.filter((f) => seen.has(f.item.link)).length;
    $("#seen-count").textContent = front.length ? `${n} of ${front.length} seen` : "";
    $$("#dots span").forEach((d, i) => d.classList.toggle("on", i === state.frontIndex));
    updateCaughtUp();
  }

  function markSeen(link) {
    if (!link || seen.has(link)) return;
    seen.add(link);
    saveDaily("seen", seen);
    updateSeen();
  }

  // A card counts as seen once it has been mostly on screen for a moment.
  let frontObserver = null;
  function observeFront() {
    frontObserver?.disconnect();
    const timers = new Map();
    frontObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        const card = e.target;
        if (e.isIntersecting && e.intersectionRatio > 0.6) {
          if (card.parentElement.scrollWidth > card.parentElement.clientWidth) {
            state.frontIndex = Number(card.dataset.idx);
            updateSeen();
          }
          timers.set(card, setTimeout(() => markSeen(card.dataset.link), 1500));
        } else {
          clearTimeout(timers.get(card));
        }
      }
    }, { root: $("#front"), threshold: [0, 0.6, 1] });
    $$(".front-card").forEach((c) => frontObserver.observe(c));
  }

  function renderChips() {
    const mine = new Set(myTopics());
    $("#topic-chips").innerHTML = state.data.topics.map((t) =>
      `<button class="chip-toggle" data-topic-toggle="${esc(t.key)}" aria-pressed="${mine.has(t.key)}">${esc(look(t.key).short)}</button>`
    ).join("");
  }

  function renderScan() {
    const keys = myTopics();
    $("#scan").innerHTML = keys.map((key) => {
      const t = topicOf(key);
      const b = briefingFor(key);
      const bullets = b?.bullets?.length ? b.bullets
        : t.items.slice(0, 3).map((i) => `${i.title} (${i.source})`);
      return `<details class="scan-item${scanned.has(key) ? " done" : ""}" data-scan="${esc(key)}" style="--tc:${look(key).color}">
        <summary>
          <span class="scan-icon">${icon(key)}</span>
          <span class="scan-label">${esc(t.label)}</span>
          <span class="scan-check" aria-label="Scanned">✓</span>
          <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        </summary>
        <div class="scan-body">
          ${bullets.length ? `<ul>${bullets.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted">Nothing new here today.</p>`}
          <a class="see-all" href="#topic/${esc(key)}">All ${t.items.length} ${esc(t.label)} stories →</a>
        </div>
      </details>`;
    }).join("");
    const watch = state.data.briefing?.watch_next || [];
    $("#watch").hidden = !watch.length;
    $("#watch").innerHTML = watch.length ? `<h3 class="label">Watch next</h3><ul>${
      watch.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
    updateScanCount();
  }

  function updateScanCount() {
    const keys = myTopics();
    const n = keys.filter((k) => scanned.has(k)).length;
    $("#scan-count").textContent = `${n} of ${keys.length} scanned`;
    updateCaughtUp();
  }

  function updateCaughtUp() {
    if (!state.data) return;
    const stories = front.filter((f) => !seen.has(f.item.link)).length;
    const topics = myTopics().filter((k) => !scanned.has(k)).length;
    const parts = [];
    if (stories) parts.push(`${stories} ${stories === 1 ? "story" : "stories"}`);
    if (topics) parts.push(`${topics} ${topics === 1 ? "topic" : "topics"}`);
    $("#caught-up").textContent = parts.length
      ? `${parts.join(" and ")} until you're caught up.` : "You're all caught up.";
  }

  function renderSignal() {
    $("#greeting").textContent = `Good ${partOfDay()}.`;
    $("#signal-date").textContent = new Date().toLocaleDateString(undefined, { month: "short", day: "numeric" });
    $("#brief-title").textContent = `${partOfDay()[0].toUpperCase()}${partOfDay().slice(1)} brief`;
    renderChips();
    renderFront();
    renderScan();
    if (!speech.playing) speech.reset();
  }

  // ---------- read-aloud brief ----------

  const speech = {
    lines: [], idx: 0, playing: false,
    supported: "speechSynthesis" in window,
    script() {
      const d = state.data, b = d.briefing, lines = [`Good ${partOfDay()}. Here is your brief.`];
      if (b?.headline) lines.push(b.headline);
      for (const key of myTopics()) {
        const t = topicOf(key), sec = briefingFor(key);
        const items = sec?.bullets?.length ? sec.bullets
          : t.items.slice(0, 2).map((i) => `${i.source} reports: ${i.title}.`);
        if (!items.length) continue;
        lines.push(`${t.label}.`, ...items);
      }
      if (b?.watch_next?.length) lines.push("What to watch next.", ...b.watch_next);
      const m = d.markets || [];
      if (m.length) {
        lines.push(`Markets: ${m.filter((x) => x.change_pct != null).map((x) =>
          `${x.label} ${x.change_pct >= 0 ? "up" : "down"} ${Math.abs(x.change_pct).toFixed(1)} percent`).join(", ")}.`);
      }
      return lines;
    },
    reset() {
      if (this.supported) speechSynthesis.cancel();
      this.lines = []; this.idx = 0; this.playing = false;
      this.ui(this.supported ? "Ready" : "Read-aloud isn't supported in this browser");
    },
    speakCurrent() {
      speechSynthesis.cancel();
      if (this.idx >= this.lines.length) { this.playing = false; this.idx = 0; this.ui("Finished"); return; }
      const u = new SpeechSynthesisUtterance(this.lines[this.idx]);
      u.rate = Number(store.get("speech-rate", "1"));
      const voice = speechSynthesis.getVoices().find((v) => v.voiceURI === store.get("speech-voice"));
      if (voice) { u.voice = voice; u.lang = voice.lang; }
      u.onend = () => { if (this.playing && u === this.current) { this.idx++; this.speakCurrent(); } };
      this.current = u;
      speechSynthesis.speak(u);
      this.ui("Playing");
    },
    toggle() {
      if (!this.supported) return;
      if (this.playing) { this.playing = false; speechSynthesis.cancel(); this.ui("Paused"); return; }
      if (!this.lines.length) this.lines = this.script();
      this.playing = true;
      this.speakCurrent();
    },
    skip(step) {
      if (!this.supported) return;
      if (!this.lines.length) this.lines = this.script();
      this.idx = Math.max(0, Math.min(this.lines.length - 1, this.idx + step));
      if (this.playing) this.speakCurrent(); else this.ui("Paused");
    },
    ui(status) {
      $("#brief-status").textContent = status;
      $("#player").classList.toggle("playing", this.playing);
      const total = this.lines.length || 1;
      $("#brief-progress").style.width = `${(this.lines.length ? this.idx / total : 0) * 100}%`;
    },
  };

  // ---------- topic page ----------

  function allStories() {
    const seenLinks = new Set(), out = [];
    for (const t of state.data.topics) {
      for (const i of t.items) {
        if (!seenLinks.has(i.link)) { seenLinks.add(i.link); out.push({ item: i, key: t.key }); }
      }
    }
    return out.sort((a, b) => (b.item.published || "").localeCompare(a.item.published || ""));
  }

  function storyRow({ item, key }) {
    const n = outlets(item).length;
    const img = item.image
      ? `<img src="${esc(item.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "";
    return `<article class="story" data-story="${esc(item.link)}" style="--tc:${look(key).color}">
      <div class="story-text">
        <p class="story-meta"><span class="dot"></span>${esc(look(key).short)} · ${esc(item.source)}${n > 1 ? ` +${n - 1}` : ""} · ${esc(timeAgo(item.published))}</p>
        <a class="story-title serif" href="#">${esc(item.title)}</a>
        ${item.summary ? `<p class="story-summary">${esc(item.summary)}</p>` : ""}
        <button class="ask-btn chat-only" data-ask-link="${esc(item.link)}">✦ Ask Claude</button>
      </div>
      ${img ? `<span class="story-thumb" aria-hidden="true">${img}</span>` : ""}
    </article>`;
  }

  function renderTopicPage(key) {
    const isAll = key === "all";
    const list = isAll ? allStories() : (topicOf(key)?.items || []).map((i) => ({ item: i, key }));
    $("#topic-head").innerHTML = `<span class="tile-icon" style="--tc:${look(key).color}">${icon(key)}</span>
      <div><h1 class="serif">${esc(labelOf(key))}</h1>
      <p class="muted">${list.length} stories · updated ${esc(timeAgo(state.data.generated_at))}</p></div>`;
    state.topicList = list;
    $("#search").placeholder = isAll ? "Search headlines" : "Search a stock, gold, a name...";
    $("#topic-charts").innerHTML = isAll ? "" : window.NEWS_CHARTS?.cardsFor(key) || "";
    // Stock Market gets TradingView's live chart; it loads once per visit, not on every refresh.
    const tv = key === "markets" && window.NEWS_TV;
    if (!tv) $("#topic-tv").innerHTML = "";
    else if (!$("#tv-chart")) { $("#topic-tv").innerHTML = tv.section(); tv.mount(); }
    const b = isAll ? null : briefingFor(key);
    $("#topic-brief").innerHTML = b?.bullets?.length ? `<div class="short-version" style="--tc:${look(key).color}">
      <h3 class="label">The short version</h3><ul>${b.bullets.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "";
    renderTopicList(list);
  }

  function renderTopicList(list) {
    const q = state.query.toLowerCase();
    // Chart cards (Nvidia, Gold...) stay only if they match what was typed.
    $$("#topic-charts .chart-card").forEach((c) => {
      c.hidden = !!q && !`${c.textContent} ${c.dataset.chart}`.toLowerCase().includes(q);
    });
    const shown = q ? list.filter(({ item }) => `${item.title} ${item.summary} ${item.source}`.toLowerCase().includes(q)) : list;
    $("#topic-list").innerHTML = shown.length ? shown.map(storyRow).join("")
      : `<p class="empty">${q ? "Nothing matches. Try another word." : "No stories right now."}</p>`;
  }

  // ---------- Settings ----------

  function renderSettings() {
    $("#dark-toggle").checked = document.documentElement.dataset.theme !== "light";
    $("#speech-rate").value = store.get("speech-rate", "1");
    $("#key-state").textContent = store.get("anthropic-key") ? "Key saved in this browser" : "Not set up yet";
    if (!state.data) return;
    $("#about-updated").textContent = timeAgo(state.data.generated_at);
    const feeds = state.data.feeds || [];
    const names = [...new Set(feeds.map((f) => f.name))];
    const bad = feeds.filter((f) => !f.ok).length;
    $("#sources-line").textContent = `${names.join(", ")}${bad ? ` · ${bad} feed${bad > 1 ? "s" : ""} failing` : ""}`;
    $("#feeds").innerHTML = feeds.map((f) =>
      `<li class="${f.ok ? "" : "bad"}" title="${esc(f.error || f.url)}"><span>${esc(f.name)}</span><span>${f.ok ? `${f.count} items` : "failed"}</span></li>`
    ).join("");
  }

  // The browser's own voices; English ones first. They can load late, so this reruns.
  function renderVoices() {
    if (!speech.supported) { $("#speech-voice").closest(".row").hidden = true; return; }
    const lang = (navigator.language || "en").slice(0, 2);
    const voices = speechSynthesis.getVoices().slice().sort((a, b) =>
      (b.lang.startsWith(lang) - a.lang.startsWith(lang)) || a.name.localeCompare(b.name));
    const saved = store.get("speech-voice", "");
    $("#speech-voice").innerHTML = `<option value="">Default</option>${voices.map((v) =>
      `<option value="${esc(v.voiceURI)}"${v.voiceURI === saved ? " selected" : ""}>${esc(v.name)} (${esc(v.lang)})</option>`).join("")}`;
  }

  function setTheme(dark) {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    store.set("theme", document.documentElement.dataset.theme);
    $('meta[name="theme-color"]').content = dark ? "#121318" : "#f6f4ef";
    $("#dark-toggle").checked = dark;
    window.dispatchEvent(new CustomEvent("theme-changed"));
  }

  // ---------- routing ----------

  function route() {
    const [view, arg] = (location.hash.slice(1) || "home").split("/");
    const name = ["home", "signal", "globe", "settings", "topic"].includes(view) ? view : "home";
    $$(".view").forEach((v) => { v.hidden = v.dataset.view !== name; });
    $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === (name === "topic" ? "home" : name)));
    if (name !== "signal" && speech.playing) speech.toggle();
    if (state.data) {
      if (name === "topic") {
        state.query = "";
        $("#search").value = "";
        renderTopicPage(arg && (arg === "all" || topicOf(arg)) ? arg : "all");
      }
      if (name === "signal") observeFront();
      if (name === "settings") renderSettings();
      if (name === "globe") window.dispatchEvent(new CustomEvent("globe-show"));
    }
    window.scrollTo(0, 0);
  }

  function renderDates() {
    $("#home-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
    if (state.data) $("#updated").textContent = `Updated ${timeAgo(state.data.generated_at)}`;
  }

  async function load() {
    try {
      const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // Nothing new since last check: leave the screen alone (keeps your scroll place).
      if (state.loaded && data.generated_at === state.data?.generated_at) { renderDates(); return; }
      state.data = data;
    } catch (err) {
      if (!state.loaded) $("#updated").textContent = `Could not load news (${err.message}).`;
      return;
    }
    window.NEWS_DATA = state.data;
    window.dispatchEvent(new CustomEvent("news-loaded"));
    seen = daily("seen");
    scanned = daily("scanned");
    renderDates();
    renderMarkets(state.data.markets);
    renderHome();
    renderSignal();
    renderSettings();
    if (!state.loaded) route();
    state.loaded = true;
  }

  // ---------- story page (read inside the site) ----------

  function findStory(link) {
    const hit = allStories().find((x) => x.item.link === link);
    if (hit) return hit;
    const w = (state.data.world || []).find((i) => i.link === link);
    return w ? { item: w, key: w.topics?.[0] || "all" } : null;
  }

  const COMMON = new Set("that this with from have after over says said will into about more than amid what when were their they been could would three years year first last week weeks month months days today news report live update people time make".split(" "));
  const keyWords = (t) => (t.toLowerCase().match(/[a-z]{4,}/g) || []).filter((w) => !COMMON.has(w));

  function related(item, key) {
    const words = new Set(keyWords(item.title));
    return allStories()
      .filter((x) => x.item.link !== item.link)
      .map((x) => ({ x, n: keyWords(x.item.title).filter((w) => words.has(w)).length + (x.key === key ? 0.5 : 0) }))
      .filter((r) => r.n >= 1.5)
      .sort((a, b) => b.n - a.n).slice(0, 4).map((r) => r.x);
  }

  function openStory(link) {
    const found = findStory(link);
    if (!found) return;
    const { item, key } = found;
    const lk = look(key);
    const others = (item.also || []);
    const host = (() => { try { return new URL(item.link).hostname.replace(/^www\./, ""); } catch { return item.source; } })();
    $("#story-body").innerHTML = `
      <span class="topic-pill" style="--tc:${lk.color}">${icon(key)}${esc(lk.short)}</span>
      <h1 class="story-headline serif">${esc(item.title)}</h1>
      <p class="story-byline">${esc(item.source)} · ${esc(timeAgo(item.published))}${others.length ? ` · also covered by ${esc(others.join(", "))}` : ""}</p>
      ${item.image ? `<img class="story-hero" src="${esc(item.image)}" alt="" referrerpolicy="no-referrer">` : ""}
      ${item.summary ? `<p class="story-lede">${esc(item.summary)}</p>` : `<p class="story-lede muted">No summary came with this story yet.</p>`}
      ${(() => { const r = related(item, key); return r.length ? `<h3 class="label story-related-label">Related</h3>
        <div class="story-related">${r.map(({ item: o, key: k }) => `<a href="#" class="story-related-item" data-story="${esc(o.link)}" style="--tc:${look(k).color}">
          <span class="story-meta"><span class="dot"></span>${esc(look(k).short)} · ${esc(o.source)} · ${esc(timeAgo(o.published))}</span>
          <span class="serif">${esc(o.title)}</span></a>`).join("")}</div>` : ""; })()}
      <p class="story-credit">Reported by ${esc(item.source)}. <a href="${esc(item.link)}" target="_blank" rel="noopener noreferrer">Original at ${esc(host)}</a></p>`;
    $("#story-body").querySelector(".story-hero")?.addEventListener("error", (e) => e.target.remove());
    const page = $("#story");
    const wasOpen = !page.hidden;
    page.hidden = false;
    page.scrollTop = 0;
    document.body.classList.add("story-open");
    if (!wasOpen) history.pushState({ story: true }, "");
    $("#sheet").hidden = true;
  }

  function closeStory(fromHistory) {
    if ($("#story").hidden) return;
    $("#story").hidden = true;
    document.body.classList.remove("story-open");
    if (!fromHistory && history.state?.story) history.back();
  }
  window.addEventListener("popstate", () => closeStory(true));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeStory(); });

  // ---------- events ----------

  // Broken or hotlink-blocked photos are dropped.
  document.addEventListener("error", (e) => {
    if (e.target.tagName === "IMG" && e.target.closest(".story-thumb")) e.target.closest(".story-thumb").remove();
  }, true);

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-close-story]")) { closeStory(); return; }
    const story = t.closest("[data-story]");
    if (story && !t.closest("button:not([data-story]), .ask-btn")) {
      e.preventDefault();
      const card = story.closest(".front-card");
      if (card) markSeen(card.dataset.link);
      openStory(story.dataset.story);
      return;
    }
    const toggle = t.closest("[data-topic-toggle]");
    if (toggle) {
      const mine = new Set(myTopics());
      const key = toggle.dataset.topicToggle;
      if (mine.has(key)) { if (mine.size > 1) mine.delete(key); } else mine.add(key);
      store.set("my-topics", JSON.stringify(state.data.topics.map((x) => x.key).filter((k) => mine.has(k))));
      renderSignal();
      return;
    }
    const analyze = t.closest("[data-analyze]");
    if (analyze) {
      const entry = front[Number(analyze.dataset.analyze)];
      markSeen(entry.item.link);
      askAbout(entry.item);
      return;
    }
    const dot = t.closest("[data-dot]");
    if (dot) {
      const card = $$(".front-card")[Number(dot.dataset.dot)];
      $("#front").scrollTo({ left: card.offsetLeft - $("#front").offsetLeft, behavior: "smooth" });
      return;
    }
    const ask = t.closest("[data-ask-link]");
    if (ask) {
      const entry = allStories().find((x) => x.item.link === ask.dataset.askLink);
      if (entry) askAbout(entry.item);
      return;
    }
    if (t.closest("#play")) { speech.toggle(); return; }
    const skip = t.closest("[data-skip]");
    if (skip) { speech.skip(Number(skip.dataset.skip)); return; }
    if (t.closest("[data-theme-toggle]")) { setTheme(document.documentElement.dataset.theme === "light"); }
  });

  document.addEventListener("toggle", (e) => {
    const item = e.target.closest?.("[data-scan]");
    if (!item || !item.open) return;
    scanned.add(item.dataset.scan);
    saveDaily("scanned", scanned);
    item.classList.add("done");
    updateScanCount();
  }, true);

  $("#search").addEventListener("input", (e) => {
    state.query = e.target.value.trim();
    // Typing searches every section, so "Nvidia" finds it wherever it was reported.
    renderTopicList(state.query ? allStories() : state.topicList || allStories());
  });
  $("#dark-toggle").addEventListener("change", (e) => setTheme(e.target.checked));
  $("#speech-rate").addEventListener("change", (e) => store.set("speech-rate", e.target.value));
  $("#speech-voice").addEventListener("change", (e) => {
    store.set("speech-voice", e.target.value);
    // A short sample so the choice can be heard right away.
    if (speech.supported && !speech.playing) {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(`Good ${partOfDay()}. Here is your brief.`);
      const voice = speechSynthesis.getVoices().find((v) => v.voiceURI === e.target.value);
      if (voice) { u.voice = voice; u.lang = voice.lang; }
      u.rate = Number(store.get("speech-rate", "1"));
      speechSynthesis.speak(u);
    }
  });
  if (speech.supported) speechSynthesis.addEventListener("voiceschanged", renderVoices);
  renderVoices();
  window.addEventListener("chat-settings-saved", renderSettings);
  window.addEventListener("hashchange", route);

  // ---------- what's on screen, for Ask Claude ----------

  const storyLine = (item) => `- ${item.title} (${outlets(item).join(", ")}, ${timeAgo(item.published)})${
    item.summary ? `: ${item.summary}` : ""}`;

  // A short label and a plain-text description of the screen the reader has open.
  function screenContext() {
    if (!state.data) return { label: "Loading", text: "The page is still loading." };
    const chart = window.NEWS_CHARTS?.view();
    if (chart) return chart;
    const [view, arg] = (location.hash.slice(1) || "home").split("/");
    if (view === "signal") {
      const box = $("#front").getBoundingClientRect();
      const visible = front.filter((_, i) => {
        const r = $$(".front-card")[i]?.getBoundingClientRect();
        return r && r.right > box.left + 20 && r.left < box.right - 20;
      });
      const open = $$(".scan-item[open]").map((d) => topicOf(d.dataset.scan).label);
      const lines = ["Signal screen (front page and daily scan)."];
      if (visible.length) lines.push("Front Page story card(s) on screen:", ...visible.map((f) => `${storyLine(f.item)} [${labelOf(f.key)}]`));
      if (open.length) lines.push(`Daily Scan sections expanded: ${open.join(", ")}.`);
      const label = visible[0] ? `Signal · “${visible[0].item.title}”` : "Signal";
      return { label, text: lines.join("\n") };
    }
    if (view === "globe") {
      const gv = window.GLOBE_VIEW?.();
      if (!gv) return { label: "Globe", text: "Globe screen." };
      const lines = [`Globe screen, "${gv.mode}" mode. Countries lit up (stories): ${gv.countries.join(", ") || "none"}.`];
      if (gv.open) lines.push(`Open country: ${gv.open.country}. Its stories:`, ...gv.open.stories.map((x) => `- ${x}`));
      return { label: gv.open ? `Globe · ${gv.open.country}` : `Globe · ${gv.mode}`, text: lines.join("\n") };
    }
    if (view === "topic") {
      const key = arg && (arg === "all" || topicOf(arg)) ? arg : "all";
      const list = key === "all" ? allStories() : topicOf(key).items.map((i) => ({ item: i, key }));
      const q = state.query.toLowerCase();
      const shown = q ? list.filter(({ item }) => `${item.title} ${item.summary} ${item.source}`.toLowerCase().includes(q)) : list;
      const b = key === "all" ? null : briefingFor(key);
      const lines = [`${labelOf(key)} page${q ? `, searching for "${state.query}"` : ""}.`];
      if (b?.bullets?.length) lines.push("The short version:", ...b.bullets.map((x) => `- ${x}`));
      lines.push("Stories listed:", ...shown.slice(0, 20).map(({ item }) => storyLine(item)));
      return { label: labelOf(key), text: lines.join("\n") };
    }
    if (view === "settings") return { label: "Settings", text: "Settings screen." };
    return { label: "Home", text: `Home screen: "Your world today" with a tile per section (${
      state.data.topics.map((t) => `${t.label}: ${t.items.length} stories`).join(", ")}).` };
  }

  window.NEWS_APP = { openStory, look, icon, labelOf, timeAgo, esc, askAbout, allStories, screenContext };

  route();
  load();
  setInterval(renderDates, 60 * 1000);
  // The site rebuilds every few minutes; check for new stories each minute while open.
  setInterval(() => { if (!document.hidden) load(); }, 60 * 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
})();
