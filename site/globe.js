// Globe screen: an orthographic globe (d3-geo + world-atlas, vendored in
// site/vendor) that lights up the countries in today's stories. Drag to spin,
// tap a country (or a chip below) to list its stories.
(() => {
  const $ = (sel) => document.querySelector(sel);
  const NS = "http://www.w3.org/2000/svg";

  const MODES = [
    { key: "explore", label: "Explore", color: "#e0b341", caption: "Countries in today's stories",
      icon: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>' },
    { key: "conflict", label: "Conflict", color: "#ef5b4c", caption: "Countries in conflict stories",
      icon: '<path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6z"/>',
      re: /\b(war|wars|strikes?|airstrikes?|attacks?|missiles?|killed|troops|ceasefire|bomb\w*|drones?|military|clash\w*|invasion|shelling|hostages?|militants?|fighting|rebels?|offensive|soldiers|army|insurgen\w*|massacre|truce)\b/i },
    { key: "elections", label: "Elections", color: "#5b8def", caption: "Countries in election stories",
      icon: '<path d="m9 12 2 2 4-4"/><path d="M12 2.5l2.4 1.8 3-.2.9 2.9 2.5 1.7-.9 2.9.9 2.9-2.5 1.7-.9 2.9-3-.2L12 21.5l-2.4-1.8-3 .2-.9-2.9-2.5-1.7.9-2.9-.9-2.9 2.5-1.7.9-2.9 3 .2z"/>',
      re: /\b(elections?|electoral|vote[sd]?|voters?|voting|ballots?|polls?|polling|campaign\w*|referendum|candidates?|primar(y|ies)|parliament\w*|coalition)\b/i },
    { key: "markets", label: "Markets", color: "#3fa45b", caption: "Countries in market and economy stories",
      icon: '<path d="M4 4v16h16"/><path d="m7 14 4-4 3 3 5-6"/>',
      re: /\b(stocks?|shares|markets?|tariffs?|trade|economy|economic|inflation|oil|gold|central bank|currency|gdp|bonds?|yields?|exports?|imports?|investors?)\b/i },
  ];

  // Extra words that point to a country, beyond its own name.
  // `cs` entries are matched case-sensitively (acronyms like US / UK).
  const ALIASES = {
    "United States of America": { words: ["united states", "americans?", "washington", "white house", "pentagon", "congress", "federal reserve"], cs: ["US", "U\\.S\\.", "USA"] },
    "United Kingdom": { words: ["britain", "british", "england", "london", "scotland", "wales", "downing street"], cs: ["UK", "U\\.K\\."] },
    Russia: { words: ["russians?", "moscow", "kremlin", "putin"] },
    Ukraine: { words: ["ukrainians?", "kyiv", "kiev", "zelensky[iy]?"] },
    China: { words: ["chinese", "beijing", "xi jinping"] },
    Taiwan: { words: ["taiwanese", "taipei"] },
    Japan: { words: ["japanese", "tokyo"] },
    "South Korea": { words: ["south koreans?", "seoul"] },
    "North Korea": { words: ["north koreans?", "pyongyang", "kim jong"] },
    India: { words: ["indians?(?! ocean)", "new delhi", "modi"] },
    Pakistan: { words: ["pakistanis?", "islamabad"] },
    Afghanistan: { words: ["afghans?", "taliban", "kabul"] },
    Iran: { words: ["iranians?", "tehran", "khamenei", "hormuz"] },
    Iraq: { words: ["iraqis?", "baghdad"] },
    Syria: { words: ["syrians?", "damascus"] },
    Israel: { words: ["israelis?", "netanyahu", "tel aviv", "jerusalem"], cs: ["IDF"] },
    Palestine: { words: ["palestin\\w*", "gaza\\w*", "west bank", "hamas", "ramallah"] },
    Lebanon: { words: ["lebanese", "beirut", "hezbollah"] },
    Jordan: { words: ["jordanians?", "amman"] },
    "Saudi Arabia": { words: ["saudis?", "riyadh"] },
    "United Arab Emirates": { words: ["emirati", "dubai", "abu dhabi"], cs: ["UAE"] },
    Qatar: { words: ["qatari", "doha"] },
    Kuwait: { words: ["kuwaiti"] },
    Yemen: { words: ["yemeni", "houthis?", "sanaa"] },
    Egypt: { words: ["egyptians?", "cairo"] },
    Turkey: { words: ["turkish", "ankara", "erdogan", "t[uü]rkiye"] },
    Germany: { words: ["germans?", "berlin"] },
    France: { words: ["french", "paris", "macron"] },
    Italy: { words: ["italians?", "rome"] },
    Spain: { words: ["spanish", "madrid"] },
    Canada: { words: ["canadians?", "ottawa"] },
    Mexico: { words: ["mexicans?"] },
    Brazil: { words: ["brazilians?", "brasilia"] },
    Argentina: { words: ["argentines?", "argentinians?", "buenos aires", "milei"] },
    Venezuela: { words: ["venezuelans?", "caracas", "maduro"] },
    Cuba: { words: ["cubans?", "havana"] },
    Sudan: { skipName: true, words: ["(?<!south )sudan", "sudanese", "khartoum", "darfur"] },
    "S. Sudan": { words: ["south sudan\\w*", "juba"] },
    "Dem. Rep. Congo": { words: ["democratic republic of (the )?congo", "kinshasa"], cs: ["DRC"] },
    Australia: { words: ["australians?", "canberra"] },
    Philippines: { words: ["philippine", "filipinos?", "manila"] },
    Vietnam: { words: ["vietnamese", "hanoi"] },
    Netherlands: { words: ["dutch", "amsterdam", "the hague"] },
    Switzerland: { words: ["swiss", "geneva", "zurich"] },
    Denmark: { words: ["danish", "copenhagen"] },
    Poland: { words: ["warsaw"] },
    Greece: { words: ["greek", "athens"] },
    Ethiopia: { words: ["ethiopians?", "addis ababa"] },
    Nigeria: { words: ["nigerians?", "abuja", "lagos"] },
    Somalia: { words: ["somalis?", "mogadishu"] },
    Libya: { words: ["libyans?", "tripoli"] },
    Georgia: { skipName: true, words: ["tbilisi"] },
    Chad: { skipName: true, words: ["n'?djamena"] },
    Guinea: { skipName: true, words: ["(?<!new |equatorial |papua )guinea(?!-bissau)"] },
  };
  const DISPLAY = { "United States of America": "United States", "Dem. Rep. Congo": "DR Congo", "S. Sudan": "South Sudan",
    "Central African Rep.": "Central African Republic", "Dominican Rep.": "Dominican Republic",
    "Bosnia and Herz.": "Bosnia and Herzegovina", "Eq. Guinea": "Equatorial Guinea", "W. Sahara": "Western Sahara" };

  const reEscape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // Older iPhones (before iOS 16.4) reject look-behind like (?<!south ); drop it there.
  function regex(src, flags) {
    try { return new RegExp(src, flags); } catch { return new RegExp(src.replace(/\(\?<[!=][^)]*\)/g, ""), flags); }
  }

  function matchers(name) {
    const a = ALIASES[name] || {};
    const words = [...(a.words || [])];
    if (!a.skipName && !/\./.test(name)) words.push(reEscape(name));
    const res = [];
    if (words.length) res.push(regex(`\\b(${words.join("|")})\\b`, "i"));
    if (a.cs) res.push(new RegExp(`(^|[^\\w.])(${a.cs.join("|")})(?![\\w])`));
    return res;
  }

  const g = {
    countries: [], mode: "explore", rotate: [-35, -25], zoom: 1, counts: new Map(), stories: new Map(),
    dragging: false, lastInteract: 0, raf: 0, ready: false, visible: false,
  };

  // All of today's stories (not just the 15 per section) when the data has them.
  function worldStories() {
    const world = window.NEWS_DATA?.world;
    if (!world?.length) return window.NEWS_APP.allStories();
    return world.map((item) => ({ item, key: item.topics?.[0] || "all" }));
  }
  const mentions = (c, item) => c.res.some((re) => re.test(`${item.title} ${item.summary || ""}`));

  let initPromise = null;
  function init() {
    if (!window.d3 || !window.topojson) return Promise.resolve();
    initPromise ||= setup().catch((err) => {
      initPromise = null;
      $("#globe-caption").textContent = `Could not load the map (${err.message}).`;
    });
    return initPromise;
  }

  async function setup() {
    const world = await fetch("vendor/countries-110m.json").then((r) => r.json());
    g.countries = topojson.feature(world, world.objects.countries).features
      .filter((f) => f.properties.name !== "Antarctica")
      .map((f) => ({ f, name: f.properties.name, display: DISPLAY[f.properties.name] || f.properties.name,
        res: matchers(f.properties.name), centroid: d3.geoCentroid(f) }));
    g.ready = true;
    buildSvg();
    renderModes();
    compute();
  }

  // ---------- data ----------

  function compute() {
    if (!g.ready || !window.NEWS_DATA) return;
    const mode = MODES.find((m) => m.key === g.mode);
    const stories = worldStories();
    g.counts = new Map();
    g.stories = new Map();
    for (const c of g.countries) {
      const hits = stories.filter(({ item }) => {
        const text = `${item.title} ${item.summary || ""}`;
        return (!mode.re || mode.re.test(text)) && c.res.some((re) => re.test(text));
      });
      if (hits.length) { g.counts.set(c.name, hits.length); g.stories.set(c.name, hits); }
    }
    draw();
    renderChips();
  }

  // ---------- drawing ----------

  let svg, projection, path, sphere, landLayer, labelLayer, size = 0;

  function el(tag, attrs = {}, parent) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (parent) parent.append(n);
    return n;
  }

  function buildSvg() {
    svg = $("#globe");
    svg.innerHTML = `<defs>
      <radialGradient id="ocean" cx="42%" cy="40%" r="65%"><stop offset="0" class="ocean-a"/><stop offset="1" class="ocean-b"/></radialGradient>
      <radialGradient id="shine" cx="68%" cy="36%" r="22%"><stop offset="0" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <radialGradient id="halo" cx="50%" cy="50%" r="50%"><stop offset=".86" class="halo-a"/><stop offset="1" class="halo-b"/></radialGradient>
    </defs>`;
    el("circle", { class: "halo", fill: "url(#halo)" }, svg);
    sphere = el("path", { class: "sphere", fill: "url(#ocean)" }, svg);
    landLayer = el("g", {}, svg);
    for (const c of g.countries) {
      c.node = el("path", { class: "land" }, landLayer);
      c.node.dataset.name = c.name;
    }
    el("circle", { class: "shine", fill: "url(#shine)", "pointer-events": "none" }, svg);
    labelLayer = el("g", { class: "labels", "pointer-events": "none" }, svg);
    projection = d3.geoOrthographic().clipAngle(90).precision(0.6);
    path = d3.geoPath(projection);
    resize();
    bindDrag();
    new ResizeObserver(resize).observe(svg.parentElement);
  }

  function resize() {
    const w = Math.min(svg.parentElement.clientWidth, 560);
    if (!w) return;
    size = w;
    svg.setAttribute("viewBox", `0 0 ${w} ${w}`);
    svg.setAttribute("width", w);
    svg.setAttribute("height", w);
    const r = w / 2 - 14;
    g.radius = r;
    projection.translate([w / 2, w / 2]).scale(r * g.zoom).clipExtent([[0, 0], [w, w]]);
    for (const sel of [".halo", ".shine"]) {
      const c = svg.querySelector(sel);
      c.setAttribute("cx", w / 2); c.setAttribute("cy", w / 2);
      c.setAttribute("r", sel === ".halo" ? r + 12 : r);
    }
    draw();
  }

  function draw() {
    if (!svg || !size) return;
    projection.rotate(g.rotate);
    sphere.setAttribute("d", path({ type: "Sphere" }));
    const mode = MODES.find((m) => m.key === g.mode);
    const max = Math.max(1, ...g.counts.values());
    for (const c of g.countries) {
      c.node.setAttribute("d", path(c.f) || "");
      const n = g.counts.get(c.name) || 0;
      if (n) {
        c.node.classList.add("hot");
        c.node.style.fill = mode.color;
        c.node.style.fillOpacity = (0.28 + 0.62 * (n / max)).toFixed(2);
        c.node.style.stroke = mode.color;
      } else {
        c.node.classList.remove("hot");
        c.node.style.fill = c.node.style.fillOpacity = c.node.style.stroke = "";
      }
    }
    // Labels for the busiest countries on the visible side; more as you zoom in.
    const center = [-g.rotate[0], -g.rotate[1]];
    const top = [...g.counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, Math.round(10 * g.zoom)).map(([name]) => name);
    labelLayer.innerHTML = "";
    const placed = [];
    for (const name of top) {
      const c = g.countries.find((x) => x.name === name);
      if (d3.geoDistance(c.centroid, center) > 1.35) continue;
      const [x, y] = projection(c.centroid);
      if (x < 0 || y < 0 || x > size || y > size) continue;
      // Skip labels that would sit on top of a busier country's label.
      if (placed.some(([px, py]) => Math.abs(px - x) < 70 && Math.abs(py - y) < 16)) continue;
      placed.push([x, y]);
      const t = el("text", { x, y, "text-anchor": "middle", dy: "0.35em" }, labelLayer);
      t.textContent = c.display;
    }
  }

  // ---------- interaction ----------

  function setZoom(z) {
    g.zoom = Math.max(1, Math.min(8, z));
    projection.scale(g.radius * g.zoom);
    g.lastInteract = Date.now();
    draw();
  }

  // One finger turns the globe (about as fast as the finger moves), two fingers pinch to zoom,
  // a mouse wheel zooms too. A tap opens that country.
  function bindDrag() {
    const pointers = new Map();
    let start = null, pinch = null;
    const spread = () => { const [a, b] = [...pointers.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };
    svg.addEventListener("pointerdown", (e) => {
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      try { svg.setPointerCapture(e.pointerId); } catch {}
      g.lastInteract = Date.now();
      if (pointers.size === 2) { pinch = { d: spread(), zoom: g.zoom }; start = null; return; }
      start = { x: e.clientX, y: e.clientY, rotate: [...g.rotate], moved: false };
    });
    svg.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      g.lastInteract = Date.now();
      if (pinch && pointers.size === 2) { setZoom(pinch.zoom * spread() / pinch.d); return; }
      if (!start) return;
      const dx = e.clientX - start.x, dy = e.clientY - start.y;
      if (Math.abs(dx) + Math.abs(dy) > 6) start.moved = true;
      if (!start.moved) return;
      // Degrees per pixel so the surface follows the finger instead of racing ahead.
      const k = (180 / Math.PI) / projection.scale() * 0.9;
      g.rotate = [start.rotate[0] + dx * k, Math.max(-80, Math.min(80, start.rotate[1] - dy * k))];
      draw();
    });
    const end = (e) => {
      const wasTap = start && !start.moved && !pinch;
      pointers.delete(e.pointerId);
      if (wasTap && e.type === "pointerup") {
        const name = document.elementFromPoint(e.clientX, e.clientY)?.dataset?.name;
        if (name) openCountry(name);
      }
      if (pointers.size < 2) pinch = null;
      // After a pinch, the finger still down carries on turning from here.
      const [rest] = pointers.values();
      start = rest ? { x: rest.x, y: rest.y, rotate: [...g.rotate], moved: true } : null;
      g.lastInteract = Date.now();
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
    svg.addEventListener("wheel", (e) => { e.preventDefault(); setZoom(g.zoom * Math.exp(-e.deltaY / 400)); }, { passive: false });
    svg.addEventListener("dblclick", () => setZoom(g.zoom * 2));
  }

  // Gentle spin while nobody is touching the globe.
  function spin() {
    if (!g.visible) { g.raf = 0; return; }
    if (Date.now() - g.lastInteract > 4000 && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      g.rotate = [g.rotate[0] + 0.08 / g.zoom, g.rotate[1]];
      draw();
    }
    g.raf = requestAnimationFrame(spin);
  }

  function rotateTo(lonlat) {
    const from = [...g.rotate], to = [-lonlat[0], Math.max(-70, Math.min(70, -lonlat[1]))];
    let dl = to[0] - from[0];
    dl = ((dl + 540) % 360) - 180;
    const t0 = performance.now();
    g.lastInteract = Date.now() + 2000;
    const step = (now) => {
      const t = Math.min(1, (now - t0) / 700), e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      g.rotate = [from[0] + dl * e, from[1] + (to[1] - from[1]) * e];
      draw();
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function renderModes() {
    $("#globe-modes").innerHTML = MODES.map((m) =>
      `<button class="mode-chip" role="tab" data-mode="${m.key}" aria-selected="${m.key === g.mode}" style="--mc:${m.color}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${m.icon}</svg>${m.label}</button>`
    ).join("");
  }

  function renderChips() {
    const { esc } = window.NEWS_APP;
    const mode = MODES.find((m) => m.key === g.mode);
    const top = [...g.counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12);
    const byName = new Map(g.countries.map((c) => [c.name, c]));
    $("#globe-countries").innerHTML = top.map(([name, n]) =>
      `<button class="country-chip" data-country="${esc(name)}" style="--mc:${mode.color}"><span class="dot"></span>${esc(byName.get(name).display)}<span class="n">${n}</span></button>`
    ).join("");
    $("#globe-caption").textContent = top.length
      ? `${mode.caption} · brighter = more stories` : "No countries in these stories today.";
  }

  function openCountry(name) {
    const { esc, look, timeAgo } = window.NEWS_APP;
    const c = g.countries.find((x) => x.name === name);
    if (!c) return;
    rotateTo(c.centroid);
    // Show every story for the country, not just the current mode's.
    const all = worldStories().filter(({ item }) => mentions(c, item));
    g.open = { country: c.display, stories: all.map(({ item }) => `${item.title} (${item.source})`) };
    $("#sheet-title").textContent = c.display;
    $("#sheet-body").innerHTML = `<p class="muted">${all.length ? `${all.length} ${all.length === 1 ? "story" : "stories"} today` : "No stories mention it today."}</p>
      <div class="sheet-list">${all.map(({ item, key }) => `
        <a class="sheet-story" href="${esc(item.link)}" target="_blank" rel="noopener noreferrer" style="--tc:${look(key).color}">
          <span class="story-meta"><span class="dot"></span>${esc(look(key).short)} · ${esc(item.source)} · ${esc(timeAgo(item.published))}</span>
          <span class="serif">${esc(item.title)}</span>
        </a>`).join("")}</div>
      <a class="more-news" href="https://news.google.com/search?q=${encodeURIComponent(c.display)}" target="_blank" rel="noopener noreferrer">More ${esc(c.display)} news on Google News ›</a>
      <button class="primary wide chat-only" data-country-brief="${esc(c.display)}">✦ Ask Claude about ${esc(c.display)}</button>`;
    $("#sheet").hidden = false;
  }

  function closeSheet() { $("#sheet").hidden = true; g.open = null; }

  // What the Globe screen shows, for Ask Claude.
  window.GLOBE_VIEW = () => {
    const mode = MODES.find((m) => m.key === g.mode);
    const byName = new Map(g.countries.map((c) => [c.name, c.display]));
    const top = [...g.counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)
      .map(([name, n]) => `${byName.get(name)} (${n})`);
    return { mode: mode.label, countries: top, open: g.open };
  };

  document.addEventListener("click", (e) => {
    const zb = e.target.closest("[data-zoom]");
    if (zb) { setZoom(zb.dataset.zoom === "in" ? g.zoom * 1.6 : zb.dataset.zoom === "out" ? g.zoom / 1.6 : 1); return; }
    const mode = e.target.closest("[data-mode]");
    if (mode) { g.mode = mode.dataset.mode; renderModes(); compute(); return; }
    const chip = e.target.closest("[data-country]");
    if (chip) { openCountry(chip.dataset.country); return; }
    const brief = e.target.closest("[data-country-brief]");
    if (brief) {
      closeSheet();
      window.dispatchEvent(new CustomEvent("ask-claude", { detail: {
        text: `Give me a short briefing on ${brief.dataset.countryBrief} today: what's happening, why it matters, and what to watch next.` } }));
      return;
    }
    if (e.target.closest("[data-close-sheet]") || e.target.id === "sheet") closeSheet();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

  window.addEventListener("news-loaded", () => init().then(compute));
  window.addEventListener("globe-show", () => {
    init().then(() => {
      g.visible = true;
      resize();
      if (!g.raf) g.raf = requestAnimationFrame(spin);
    });
  });
  window.addEventListener("hashchange", () => {
    if (!location.hash.startsWith("#globe")) { g.visible = false; closeSheet(); }
  });
})();
