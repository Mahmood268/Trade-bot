// Globe screen: an orthographic globe (d3-geo + world-atlas, vendored in
// site/vendor) that lights up the countries in today's stories. Drag to spin,
// tap a country (or a chip below) to list its stories. Missile, drone and air strikes in the
// headlines are drawn as arcs from where they came from to where they landed, and ships sail
// through the Strait of Hormuz and the Red Sea.
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
    Bahrain: { words: ["bahraini", "manama"] },
    Singapore: { words: ["singaporeans?"] },
    Malta: { words: ["maltese", "valletta"] },
    Maldives: { words: ["maldivians?"] },
    Chad: { skipName: true, words: ["n'?djamena"] },
    Guinea: { skipName: true, words: ["(?<!new |equatorial |papua )guinea(?!-bissau)"] },
  };
  // Countries too small for the world map get a dot so they can still be tapped.
  const TINY = { Bahrain: [50.55, 26.07], Singapore: [103.82, 1.35], Malta: [14.45, 35.9], Maldives: [73.5, 3.2], "Hong Kong": [114.17, 22.32] };
  const NO_LABEL = new Set(["Siachen Glacier", "Ashmore and Cartier Is.", "Indian Ocean Ter.", "Heard I. and McDonald Is.", "Br. Indian Ocean Ter."]);
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
    countries: [], mode: "explore", rotate: [-35, -25], zoom: 1, counts: new Map(), stories: new Map(), strikes: [],
    dragging: false, lastInteract: 0, raf: 0, ready: false, visible: false,
  };

  // All of today's stories (not just the 15 per section) when the data has them.
  function worldStories() {
    const world = window.NEWS_DATA?.world;
    if (!world?.length) return window.NEWS_APP.allStories();
    return world.map((item) => ({ item, key: item.topics?.[0] || "all" }));
  }
  const mentions = (c, item) => c.res.some((re) => re.test(`${item.title} ${item.summary || ""}`));

  // ---------- strikes ----------
  // Missile, drone and air strikes worked out from the headlines (and summaries): who fired
  // (origin) and where it landed (target). Plain word matching, so it can miss or misread some.

  const STRONG = /\b(missiles?|drones?|rockets?|air ?strikes?|airstrikes?|shelling|shelled|bombardment|barrage|artillery|ballistic|warplanes?|projectiles?|intercept(ed|ors?)?)\b|\bunder (\w+ ){0,2}fire\b/i;
  const WEAK = /\b(strikes?|struck|attacks?|attacked|bomb(s|ed|ing)?|raids?)\b/i;
  const MILITARY = /\b(military|army|forces|troops|soldiers|kill|killed|kills|killing|dead|deaths?|wounded|injured|casualties|war|jets?|militants?|rebels?|explosions?|blasts?|civilians|houthis?|hezbollah|hamas|idf|retaliat\w*|offensive|ceasefire)\b/i;
  const NOT_WAR = /\b(workers|unions?|walk-?outs?|wages?|pay|general strike|on strike|strike action|strikers?|goal|match|deal|lightning|hunger strike|heart attack|panic attack|cyber-?attacks?|ransomware|hackers?|shark)\b/i;
  const isAttack = (s) => !NOT_WAR.test(s) && (STRONG.test(s) || (WEAK.test(s) && MILITARY.test(s)));

  const MOD = "(?:(?:the|a|an|its|their|northern|southern|eastern|western|central|north|south|east|west|occupied|neighbouring|neighboring|several|two|three|targets? in|parts of|sites? in|cities in|\\w+'s)\\s+){0,2}";
  const TARGET_BEFORE = new RegExp(`\\b(on|at|against|into|in|inside|toward|towards|over|targeting|targeted|targets?|hits?|hitting|strikes?|struck|striking|attacks?|attacked|attacking|bomb(s|ed|ing)?|pound(s|ed|ing)?|shell(s|ed|ing)?|raids?|raided)\\s+${MOD}$`, "i");
  const ORIGIN_BEFORE = new RegExp(`\\b(from|by)\\s+${MOD}$`, "i");
  const ATTACK_NEXT = "(strikes?|struck|attacks?|attacked|launch(es|ed)?|fire[sd]?|missiles?|drones?|rockets?|bomb(s|ed|ing)?|pound(s|ed)?|shell(s|ed|ing)?|hits?|target(s|ed)?|forces|army|military|jets|warplanes|barrage|bombardment|offensive|raids?|air ?strikes?|airstrikes?)";
  const ORIGIN_AFTER = new RegExp(`^('s?|-(led|backed))?\\s+((air|missile|drone|rocket|ballistic|cruise|military|army|armed|naval|forces?|fighters?|militants?|rebels?|troops|soldiers|taliban|new|deadly|massive|overnight|retaliatory)\\s+){0,2}${ATTACK_NEXT}\\b`, "i");
  const TARGET_AFTER = /^\s+((is|was|were|are|has been|have been)\s+)?(hit|struck|targeted|attacked|bombed|shelled|pounded)\s+by\b|^\s+(comes? |came )?under\s+(\w+\s+){0,2}(attack|fire)\b/i;
  // A bare place name before a single strike ("Gaza strike kills...") is where it landed.
  const PLACE_STRIKE = /^\s+(air ?strike|airstrike|strike|attack|bombing|raid|blast)\b(?!s)/i;
  const PLACE_END = /(stan|tehran|sudan|taiwan|japan|oman|jordan|amman|bhutan|azerbaijan|yerevan)$/i;
  const isPeople = (w) => /^(US|U\.S\.|USA|UK|U\.K\.|IDF|hamas|hezbollah|houthis?|taliban|pentagon|kremlin|white house)$/i.test(w)
    || (/(i|is|ians?|eans?|ese|ish|ench|utch|ans|an)$/i.test(w) && !PLACE_END.test(w));

  // Spots more exact than a country's middle, for cities in the news and big countries.
  const PLACES = [
    [/\b(kyiv|kiev)\b/i, [30.52, 50.45]], [/\bkharkiv\b/i, [36.23, 49.99], "Ukraine"], [/\bodesa\b/i, [30.72, 46.48], "Ukraine"],
    [/\bmoscow\b/i, [37.62, 55.75]], [/\bgaza\b/i, [34.42, 31.45]], [/\bwest bank\b/i, [35.25, 31.95]],
    [/\b(tel aviv)\b/i, [34.78, 32.08]], [/\bjerusalem\b/i, [35.21, 31.77]], [/\bbeirut\b/i, [35.5, 33.89]],
    [/\bsanaa\b/i, [44.21, 15.35]], [/\bhodeidah\b/i, [42.95, 14.8], "Yemen"], [/\briyadh\b/i, [46.72, 24.71]],
    [/\btehran\b/i, [51.39, 35.69]], [/\bdamascus\b/i, [36.29, 33.51]], [/\bbaghdad\b/i, [44.36, 33.31]],
    [/\bkhartoum\b/i, [32.53, 15.5]], [/\bel fasher\b/i, [25.35, 13.63], "Sudan"], [/\bdoha\b/i, [51.53, 25.29]],
    [/\b(dubai|abu dhabi)\b/i, [54.9, 24.8]], [/\bkabul\b/i, [69.18, 34.53]], [/\bislamabad\b/i, [73.05, 33.68]],
    [/\btaipei\b/i, [121.56, 25.03]], [/\bseoul\b/i, [126.98, 37.57]], [/\btripoli\b/i, [13.19, 32.89], "Libya"],
    [/\bmogadishu\b/i, [45.32, 2.05]],
  ];
  // Where to aim when only the country is known (big countries' middles are far from the news).
  const ANCHOR = { Russia: [38, 55], "United States of America": [-77, 38.9], China: [116, 36], Canada: [-79, 45],
    Israel: [34.85, 31.9], Palestine: [34.42, 31.45], Brazil: [-47.9, -15.8], Australia: [149, -35], India: [77.2, 28.6] };

  // Every place in `text` where a country is named: [start, end, country].
  function spans(text) {
    const out = [];
    for (const c of g.countries) {
      for (const re of c.res) {
        const gre = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
        for (const m of text.matchAll(gre)) {
          const word = m[2] ?? m[1] ?? m[0];
          const start = m.index + m[0].length - word.length;
          out.push({ start, end: start + word.length, word, c });
        }
      }
    }
    return out.sort((a, b) => a.start - b.start);
  }

  function readSentence(s) {
    let origin = null, target = null;
    for (const sp of spans(s)) {
      sp.s = s;
      const before = s.slice(Math.max(0, sp.start - 60), sp.start), after = s.slice(sp.end, sp.end + 60);
      const placeName = !isPeople(sp.word);
      let role = null;
      if (TARGET_AFTER.test(after)) role = "target";
      else if (placeName && PLACE_STRIKE.test(after) && !/'s$/.test(sp.word)) role = "target";
      else if (ORIGIN_AFTER.test(after)) role = "origin";
      else if (ORIGIN_BEFORE.test(before)) role = "origin";
      else if (TARGET_BEFORE.test(before) && (STRONG.test(s.slice(0, sp.start)) || WEAK.test(s.slice(0, sp.start)))) role = "target";
      if (role === "origin" && !origin && sp.c !== target?.c) origin = sp;
      if (role === "target" && !target && sp.c !== origin?.c) target = sp;
    }
    return { origin, target };
  }

  function pointFor(sp) {
    const hit = PLACES.find(([re, , country]) => !country && re.test(sp.word))
      || PLACES.find(([re, , country]) => country === sp.c.name && re.test(sp.s));
    return hit ? hit[1] : ANCHOR[sp.c.name] || sp.c.centroid;
  }

  // One story -> { origin, target } countries, or null when it isn't about a strike.
  function readStrike(item) {
    const tidy = (t) => (t || "").replace(/\bU\.S\./g, "US").replace(/\bU\.K\./g, "UK");
    const title = tidy(item.title);
    const sentences = [title, ...(tidy(item.summary).match(/[^.!?]+[.!?]*/g) || []).slice(0, 3)];
    let origin = null, target = null;
    for (const s of sentences) {
      if (!isAttack(s)) continue;
      const r = readSentence(s);
      origin ||= r.origin && r.origin.c !== target?.c ? r.origin : null;
      target ||= r.target && r.target.c !== origin?.c ? r.target : null;
      if (origin && target) break;
    }
    if (!target && isAttack(title)) {
      // One country in an attack headline with nobody named as firing: that's where it hit.
      const named = [...new Map(spans(title).map((sp) => [sp.c.name, { ...sp, s: title }])).values()];
      if (named.length === 1 && named[0].c !== origin?.c && !origin) target = named[0];
    }
    if (!target) return null;
    return {
      origin: origin && { c: origin.c, p: pointFor(origin) },
      target: { c: target.c, p: pointFor(target) },
    };
  }

  function computeStrikes() {
    const seen = new Map();
    const stories = worldStories().slice().sort((a, b) => new Date(b.item.published || 0) - new Date(a.item.published || 0));
    for (const { item, key } of stories) {
      const s = readStrike(item);
      if (!s) continue;
      const id = `${s.origin?.c.name || ""}>${s.target.c.name}`;
      if (seen.has(id)) { seen.get(id).n++; continue; }
      seen.set(id, { ...s, item, key, n: 1 });
    }
    return [...seen.values()].slice(0, 8);
  }

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
    for (const [name, p] of Object.entries(TINY)) {
      g.countries.push({ f: pointOf(p), name, display: name, res: matchers(name), centroid: p, dot: true });
    }
    for (const c of g.countries) prepare(c, c.f);
    g.facts = await fetch("countries.json").then((r) => r.json()).catch(() => ({}));
    g.ready = true;
    buildSvg();
    renderModes();
    compute();
    loadDetail("50m");
  }

  // The map starts with a light outline set; sharper coastlines and islands load in the
  // background (1:50m) and when you zoom in close (1:10m).
  const DETAIL = { "50m": { file: "vendor/countries-50m.json", zoom: 1.8 }, "10m": { file: "vendor/countries-10m.json", zoom: 7 } };
  const pointOf = (p) => ({ type: "Feature", geometry: { type: "Point", coordinates: p } });

  function prepare(c, f) {
    c.lo ||= f;
    c.tiny = c.dot || d3.geoArea(c.lo) < 0.004;
    // How far (in radians) the country reaches from its middle, to skip drawing it when off screen.
    const [[w, so], [e, n]] = d3.geoBounds(f);
    const reach = e < w ? Math.PI : Math.max(...[[w, so], [w, n], [e, so], [e, n]].map((p) => d3.geoDistance(c.centroid, p)));
    c.reach = Math.max(c.reach || 0, reach);
  }

  async function loadDetail(level) {
    const d = DETAIL[level];
    if (d.state) return;
    d.state = "loading";
    try {
      const world = await fetch(d.file).then((r) => r.json());
      const byName = new Map(g.countries.map((c) => [c.name, c]));
      for (const f of topojson.feature(world, world.objects.countries).features) {
        const name = f.properties.name;
        if (!name || name === "Antarctica") continue;
        rewind(f);
        let c = byName.get(name);
        if (!c) {
          if (level !== "50m") continue;
          // Small places missing from the light map: a dot when zoomed out if we have notes on them.
          const centroid = d3.geoCentroid(f);
          c = { name, display: DISPLAY[name] || name, res: matchers(name), centroid, dot: !!g.facts?.[name] };
          c.lo = c.dot ? pointOf(centroid) : null;
          c.node = el("path", { class: c.dot ? "land tiny-dot" : "land" }, landLayer);
          c.node.dataset.name = name;
          g.countries.push(c);
          byName.set(name, c);
          c.tiny = true;
        }
        c[level] = f;
        c.parts = { ...c.parts, [level]: splitParts(f) };
        prepare(c, f);
      }
      d.state = "ready";
      compute();
    } catch {
      d.state = null;
    }
  }

  // A few shapes in the sharper files are wound the wrong way round, which d3 reads as "the
  // whole world except this island" and paints over everything; turn those back.
  function rewind(f) {
    const geo = f.geometry;
    const polys = geo.type === "Polygon" ? [geo.coordinates] : geo.type === "MultiPolygon" ? geo.coordinates : [];
    for (const poly of polys) {
      if (d3.geoArea({ type: "Polygon", coordinates: poly }) > 2 * Math.PI) for (const ring of poly) ring.reverse();
    }
  }

  // Each island or piece of a country on its own, so pieces off screen can be skipped.
  function splitParts(f) {
    const geo = f.geometry;
    const polys = geo.type === "Polygon" ? [geo.coordinates] : geo.type === "MultiPolygon" ? geo.coordinates : [];
    return polys.map((poly) => {
      const p = { type: "Polygon", coordinates: poly }, centroid = d3.geoCentroid(p), [[w, so], [e, n]] = d3.geoBounds(p);
      const reach = e < w ? Math.PI : Math.max(...[[w, so], [w, n], [e, so], [e, n]].map((q) => d3.geoDistance(centroid, q)));
      return { poly, centroid, reach };
    });
  }

  function featureFor(c, center, view) {
    const level = g.zoom >= DETAIL["10m"].zoom && c["10m"] ? "10m" : g.zoom >= DETAIL["50m"].zoom && c["50m"] ? "50m" : null;
    if (!level) return c.lo;
    const polys = c.parts[level].filter((p) => d3.geoDistance(p.centroid, center) < view + p.reach).map((p) => p.poly);
    return polys.length ? { type: "Feature", geometry: { type: "MultiPolygon", coordinates: polys } } : null;
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
    g.strikes = computeStrikes();
    computeLanes();
    buildLanes();
    buildStrikes();
    draw();
    renderChips();
    renderStrikeList();
    renderLaneList();
  }

  // ---------- drawing ----------

  let svg, projection, path, sphere, landLayer, laneLayer, strikeLayer, labelLayer, size = 0;

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
      c.node = el("path", { class: c.dot ? "land tiny-dot" : "land" }, landLayer);
      c.node.dataset.name = c.name;
    }
    el("circle", { class: "shine", fill: "url(#shine)", "pointer-events": "none" }, svg);
    laneLayer = el("g", { class: "lanes" }, svg);
    strikeLayer = el("g", { class: "strikes" }, svg);
    labelLayer = el("g", { class: "labels", "pointer-events": "none" }, svg);
    projection = d3.geoOrthographic().clipAngle(90).precision(0.6);
    path = d3.geoPath(projection).pointRadius(3.5);
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
    const center = [-g.rotate[0], -g.rotate[1]];
    // Roughly how far from the middle the screen reaches; anything beyond isn't drawn.
    const view = Math.min(Math.PI / 2, Math.asin(Math.min(1, 1.45 / g.zoom))) + 0.05;
    if (g.zoom >= DETAIL["10m"].zoom) loadDetail("10m");
    for (const c of g.countries) {
      const shown = d3.geoDistance(c.centroid, center) < view + (c.reach || 0);
      c.f = shown ? featureFor(c, center, view) : null;
      c.node.setAttribute("d", c.f ? path(c.f) || "" : "");
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
    // Labels for the busiest countries on the visible side; more as you zoom in, and once
    // zoomed in close, every country in view gets its name.
    const top = [...g.counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, Math.round(10 * g.zoom)).map(([name]) => name);
    if (g.zoom >= 2.5) {
      // Bigger countries first so their names win when labels would overlap.
      const rest = g.countries.filter((c) => !g.counts.has(c.name) && !NO_LABEL.has(c.name) && c.node.getAttribute("d"));
      for (const c of rest) c.screenArea = path.area(c.lo);
      top.push(...rest.sort((a, b) => b.screenArea - a.screenArea).map((c) => c.name));
    }
    labelLayer.innerHTML = "";
    const placed = [];
    const byName = new Map(g.countries.map((c) => [c.name, c]));
    for (const name of top) {
      const c = byName.get(name);
      if (!c.node.getAttribute("d") || d3.geoDistance(c.centroid, center) > 1.35) continue;
      // Zoomed in, a big country's label sits in the middle of the part on screen.
      const [x, y] = g.zoom >= 2.5 && c.lo.geometry.type !== "Point" ? path.centroid(c.lo) : projection(c.centroid);
      if (!(x >= 0 && y >= 0 && x <= size && y <= size)) continue;
      const fs = g.zoom >= 2.5 ? Math.max(10, Math.min(20, Math.sqrt(path.area(c.lo)) / 9)) : 11.5;
      const half = c.display.length * fs * 0.3 + 4;
      // Skip labels that would sit on top of a busier or bigger country's label.
      if (placed.some(([px, py, ph, pf]) => Math.abs(px - x) < half + ph && Math.abs(py - y) < (fs + pf) * 0.6)) continue;
      placed.push([x, y, half, fs]);
      const t = el("text", { x, y, "text-anchor": "middle", dy: "0.35em" }, labelLayer);
      t.style.fontSize = `${fs.toFixed(1)}px`;
      t.textContent = c.display;
    }
    drawLanes();
    drawStrikes();
  }

  // ---------- shipping lanes ----------
  // Tankers and cargo ships sailing through the Strait of Hormuz and the Red Sea. When the news
  // reports trouble there (a seizure, an attack, a closure) the strait lights up red.

  const LANE_MODES = new Set(["explore", "conflict", "markets"]);
  const LANES = [
    { key: "hormuz", name: "Strait of Hormuz", at: [56.45, 26.45], ships: 7,
      route: [[50.3, 26.9], [51.8, 26.6], [53.6, 26.1], [55.3, 26.35], [56.45, 26.45], [57.2, 25.7], [58.4, 24.6], [60.2, 23.6], [62.5, 22.6]],
      re: /\bhormuz\b|\b(gulf of oman|persian gulf)\b|\b(tankers?|oil ships?|shipping|vessels?|ships?)\b[^.]{0,80}\b(iran|persian gulf|arabian gulf|gulf of oman|oman|uae|emirati|fujairah|bandar abbas)\b|\b(iran\w*|irgc|revolutionary guards?)\b[^.]{0,80}\b(tankers?|ships?|vessels?|shipping)\b/i,
      blurb: "About a fifth of the world's oil passes through this narrow channel between Iran and Oman." },
    { key: "redsea", name: "Red Sea", at: [43.4, 12.6], ships: 6,
      route: [[32.55, 29.9], [33.6, 28.1], [35.6, 25.6], [37.9, 21.8], [40.1, 18.0], [41.9, 14.9], [43.4, 12.6], [45.4, 12.1], [48.5, 12.6], [51.5, 12.8]],
      re: /\bred sea\b|\bbab[ -]el[ -]mandeb\b|\bbab al[ -]mandab\b|\bgulf of aden\b|\bsuez\b|\b(houthis?)\b[^.]{0,80}\b(ships?|vessels?|tankers?|shipping|freighters?)\b|\b(ships?|vessels?|tankers?|shipping|freighters?)\b[^.]{0,80}\bhouthis?\b/i,
      blurb: "Ships between Asia and Europe use this route to reach the Suez Canal." },
  ];
  const TROUBLE = /\b(seiz\w*|attack\w*|struck|strikes?|missiles?|drones?|explosions?|blasts?|hit|fire[ds]?|mines?|hijack\w*|detain\w*|board(ed|ing)|threat\w*|clos(e|es|ed|ure|ing)|block\w*|shut\w*|halt\w*|sink\w*|sank|damaged)\b/i;
  const CLOSED = /\b(clos(e[sd]?|ure|ing)|block(s|ed|ade|ing)?|shut(s|ting)?( down)?|halt(s|ed)?)\b/i;
  const showLanes = () => LANE_MODES.has(g.mode);

  function computeLanes() {
    const stories = worldStories().slice().sort((a, b) => new Date(b.item.published || 0) - new Date(a.item.published || 0));
    for (const lane of LANES) {
      lane.news = stories.filter(({ item }) => lane.re.test(`${item.title} ${item.summary || ""}`));
      const top = lane.news[0]?.item;
      const text = top ? `${top.title} ${top.summary || ""}` : "";
      lane.state = !top ? "calm" : TROUBLE.test(text) ? (CLOSED.test(text) ? "closed" : "alert") : "news";
      if (!lane.lengths) {
        lane.lengths = lane.route.slice(1).map((p, i) => d3.geoDistance(lane.route[i], p));
        lane.total = d3.sum(lane.lengths);
      }
    }
  }

  // Point and heading at fraction f (0..1) along a lane.
  function along(lane, f) {
    let d = f * lane.total;
    for (let i = 0; i < lane.lengths.length; i++) {
      if (d <= lane.lengths[i] || i === lane.lengths.length - 1) {
        const t = Math.min(1, d / lane.lengths[i]);
        return { p: d3.geoInterpolate(lane.route[i], lane.route[i + 1])(t), a: lane.route[i], b: lane.route[i + 1] };
      }
      d -= lane.lengths[i];
    }
  }

  function buildLanes() {
    laneLayer.innerHTML = "";
    if (!showLanes()) return;
    LANES.forEach((lane, li) => {
      const grp = el("g", { class: `lane lane-${lane.state}` }, laneLayer);
      lane.nodes = {
        path: el("path", { class: "lane-path" }, grp),
        ships: d3.range(lane.ships).map((k) => el("path", { class: k % 3 === 0 ? "ship tanker" : "ship" }, grp)),
        zone: el("circle", { class: "lane-zone" }, grp),
        dot: el("circle", { class: "lane-dot", r: 3.2 }, grp),
        hit: el("circle", { class: "strike-hit", r: 9, "data-lane": li }, grp),
      };
    });
  }

  function drawLanes(now = performance.now()) {
    if (!laneLayer || !showLanes()) return;
    const center = [-g.rotate[0], -g.rotate[1]], still = calm();
    const seen = (ll) => d3.geoDistance(ll, center) < Math.PI / 2 - 0.02;
    const k = Math.min(1.6, 0.6 + g.zoom * 0.15);
    for (const lane of LANES) {
      const n = lane.nodes;
      if (!n) continue;
      n.path.setAttribute("d", path({ type: "LineString", coordinates: lane.route }) || "");
      // Ships half go out, half come in; they crawl when the strait is reported closed.
      const speed = lane.state === "closed" ? 0.004 : 0.022;
      n.ships.forEach((ship, i) => {
        const out = i % 2 === 0;
        let f = ((still ? 0 : now / 1000 * speed) + i / lane.ships) % 1;
        if (!out) f = 1 - f;
        const { p, a, b } = along(lane, f);
        if (!seen(p)) { ship.setAttribute("opacity", 0); return; }
        const [x, y] = projection(p), [ax, ay] = projection(a), [bx, by] = projection(b);
        const ang = Math.atan2(by - ay, bx - ax) * 180 / Math.PI + (out ? 0 : 180);
        const fade = Math.min(1, f * 12, (1 - f) * 12);
        ship.setAttribute("opacity", fade.toFixed(2));
        ship.setAttribute("d", "M-5,-1.7L2.6,-1.7L5.4,0L2.6,1.7L-5,1.7Z");
        ship.setAttribute("transform", `translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${ang.toFixed(0)}) scale(${k.toFixed(2)})`);
      });
      const vis = seen(lane.at), [sx, sy] = vis ? projection(lane.at) : [-99, -99];
      const pulse = still ? 0.5 : (now % 1800) / 1800;
      for (const c of [n.zone, n.dot, n.hit]) { c.setAttribute("cx", sx); c.setAttribute("cy", sy); }
      n.zone.setAttribute("r", lane.state === "calm" ? 0 : (6 + pulse * 16) * Math.min(1.6, k));
      n.zone.setAttribute("opacity", (1 - pulse).toFixed(2));
      n.dot.setAttribute("opacity", vis && lane.state !== "calm" ? 1 : 0);
      n.hit.style.display = vis && lane.state !== "calm" ? "" : "none";
    }
  }

  const LANE_STATE = { calm: "No news today", news: "In the news", alert: "Trouble reported", closed: "Closure reported" };

  function renderLaneList() {
    const { esc, timeAgo } = window.NEWS_APP;
    const box = $("#globe-lanes");
    if (!showLanes()) { box.innerHTML = ""; box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<h3 class="label">Shipping lanes</h3>` + LANES.map((lane, i) => {
      const top = lane.news[0]?.item;
      return `<a class="lane-row lane-${lane.state}" href="#" data-lane="${i}">
        <span class="strike-route"><span class="ship-icon" aria-hidden="true"></span>${esc(lane.name)}<span class="n">${esc(LANE_STATE[lane.state])}</span></span>
        ${top ? `<span class="strike-title serif">${esc(top.title)}</span>
        <span class="story-meta">${esc(top.source)} · ${esc(timeAgo(top.published))}${lane.news.length > 1 ? ` · ${lane.news.length} stories` : ""}</span>`
          : `<span class="muted">${esc(lane.blurb)}</span>`}
      </a>`;
    }).join("");
  }

  function openLane(i) {
    const lane = LANES[i];
    if (!lane) return;
    rotateTo(lane.at);
    if (g.zoom < 3) setZoom(3);
    const top = lane.news[0]?.item;
    if (top) window.NEWS_APP.openStory(top.link);
  }

  // ---------- strike animation ----------
  // Each strike loops: a missile flies along a raised arc from origin to target, then a flash and
  // a ring burst where it lands. The red danger zone around the target pulses the whole time.

  const STRIKE_MODES = new Set(["explore", "conflict"]);
  const CYCLE = 4200, FLIGHT = 0.42, BLAST = 0.3, SAMPLES = 48;
  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const showStrikes = () => STRIKE_MODES.has(g.mode) && g.strikes.length > 0;

  function buildStrikes() {
    strikeLayer.innerHTML = "";
    if (!STRIKE_MODES.has(g.mode)) return;
    g.strikes.forEach((s, i) => {
      const grp = el("g", { class: "strike" }, strikeLayer);
      s.nodes = {
        zone: el("path", { class: "strike-zone" }, grp),
        track: s.origin ? el("path", { class: "strike-track" }, grp) : null,
        trail: s.origin ? el("path", { class: "strike-trail" }, grp) : null,
        head: s.origin ? el("circle", { class: "strike-head", r: 3.6 }, grp) : null,
        ring: el("circle", { class: "strike-ring" }, grp),
        core: el("circle", { class: "strike-core" }, grp),
        hit: el("circle", { class: "strike-hit", r: 9, "data-strike": i }, grp),
      };
      s.zoneGeo = d3.geoCircle().center(s.target.p).radius(3)();
      if (s.origin) {
        const interp = d3.geoInterpolate(s.origin.p, s.target.p);
        s.samples = d3.range(SAMPLES + 1).map((k) => interp(k / SAMPLES));
        // Longer flights arc higher above the surface.
        s.lift = Math.min(0.32, 0.06 + d3.geoDistance(s.origin.p, s.target.p) * 0.35);
      }
    });
  }

  // Screen points along the arc, lifted off the globe toward the middle of the flight.
  function arcPoints(s, upto) {
    const center = [-g.rotate[0], -g.rotate[1]], [cx, cy] = projection.translate();
    const pts = [];
    for (let k = 0; k <= upto; k++) {
      const ll = s.samples[k];
      if (d3.geoDistance(ll, center) > Math.PI / 2 - 0.02) { pts.push(null); continue; }
      const [x, y] = projection(ll), h = 1 + s.lift * Math.sin(Math.PI * k / SAMPLES);
      pts.push([cx + (x - cx) * h, cy + (y - cy) * h]);
    }
    return pts;
  }
  const line = (pts) => {
    let d = "", pen = false;
    for (const p of pts) {
      if (!p) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
      pen = true;
    }
    return d;
  };

  function drawStrikes(now = performance.now()) {
    if (!strikeLayer || !STRIKE_MODES.has(g.mode)) return;
    const center = [-g.rotate[0], -g.rotate[1]], still = calm();
    g.strikes.forEach((s, i) => {
      const n = s.nodes;
      if (!n) return;
      n.zone.setAttribute("d", path(s.zoneGeo) || "");
      const seen = d3.geoDistance(s.target.p, center) < Math.PI / 2 - 0.03;
      const [tx, ty] = seen ? projection(s.target.p) : [-99, -99];
      const t = still ? 1 : ((now + i * 650) % CYCLE) / CYCLE;
      if (s.origin) {
        n.track.setAttribute("d", line(arcPoints(s, SAMPLES)));
        if (t < FLIGHT) {
          const k = Math.round((t / FLIGHT) * SAMPLES);
          const pts = arcPoints(s, k);
          n.trail.setAttribute("d", line(pts.slice(Math.max(0, k - 16))));
          const tip = pts[k];
          n.head.setAttribute("opacity", tip ? 1 : 0);
          if (tip) { n.head.setAttribute("cx", tip[0]); n.head.setAttribute("cy", tip[1]); }
        } else {
          n.trail.setAttribute("d", "");
          n.head.setAttribute("opacity", 0);
        }
      }
      // Landing: a bright flash, then a ring that spreads and fades.
      const b = s.origin ? (t - FLIGHT) / BLAST : (t < BLAST ? t / BLAST : -1);
      const blasting = !still && b >= 0 && b <= 1;
      for (const c of [n.ring, n.core, n.hit]) { c.setAttribute("cx", tx); c.setAttribute("cy", ty); }
      n.ring.setAttribute("r", blasting ? 3 + b * 26 : 0);
      n.ring.setAttribute("opacity", blasting ? (1 - b).toFixed(2) : 0);
      n.core.setAttribute("r", blasting ? 6 * (1 - b) + 2.5 : 2.6);
      n.core.setAttribute("opacity", seen ? 1 : 0);
      n.core.classList.toggle("flash", blasting && b < 0.35);
      n.hit.style.display = seen ? "" : "none";
    });
  }

  function renderStrikeList() {
    const { esc, timeAgo } = window.NEWS_APP;
    const box = $("#globe-strikes");
    if (!showStrikes()) { box.innerHTML = ""; box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<h3 class="label">Strikes in the news</h3>` + g.strikes.map((s, i) => `
      <a class="strike-row" href="#" data-strike="${i}">
        <span class="strike-route"><span class="strike-icon" aria-hidden="true"></span>${s.origin ? `${esc(s.origin.c.display)} <span class="arrow">→</span> ` : ""}${esc(s.target.c.display)}${s.n > 1 ? `<span class="n">${s.n}</span>` : ""}</span>
        <span class="strike-title serif">${esc(s.item.title)}</span>
        <span class="story-meta">${esc(s.item.source)} · ${esc(timeAgo(s.item.published))}</span>
      </a>`).join("") + `<p class="caption muted">Worked out from headlines, so a few may be missed or misread.</p>`;
  }

  function openStrike(i) {
    const s = g.strikes[i];
    if (!s) return;
    rotateTo(s.target.p);
    window.NEWS_APP.openStory(s.item.link);
  }


  // ---------- interaction ----------

  function setZoom(z) {
    g.zoom = Math.max(1, Math.min(60, z));
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
        const els = document.elementsFromPoint(e.clientX, e.clientY);
        const marker = els.find((n) => n.dataset?.strike || n.dataset?.lane);
        const name = countryAt(e.clientX, e.clientY, els.find((n) => n.dataset?.name)?.dataset.name);
        // A strike or strait marker wins only for a tap right on it, so countries under it stay tappable.
        const r = svg.getBoundingClientRect(), k = size / r.width;
        const onMarker = marker && Math.hypot((e.clientX - r.left) * k - marker.getAttribute("cx"),
          (e.clientY - r.top) * k - marker.getAttribute("cy")) < 6;
        if (onMarker || (marker && !name)) {
          if (marker.dataset.strike) openStrike(+marker.dataset.strike); else openLane(+marker.dataset.lane);
        } else if (name) openCountry(name);
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

  // Which country a tap meant. Small countries (UAE, Qatar, Lebanon...) are hard to hit with a
  // finger, so a tap close to one picks it even if it landed on a bigger neighbour or the sea.
  function countryAt(clientX, clientY, hitName) {
    const isSmall = (c) => { const [[x0, y0], [x1, y1]] = path.bounds(c.f); return c.tiny || Math.max(x1 - x0, y1 - y0) < 26; };
    const hitC = hitName && g.countries.find((c) => c.name === hitName);
    // Over a big country only a tap right next to a small one switches to it.
    const reach = hitC && !isSmall(hitC) ? 12 : 22;
    const r = svg.getBoundingClientRect(), k = size / r.width;
    const x = (clientX - r.left) * k, y = (clientY - r.top) * k;
    const center = [-g.rotate[0], -g.rotate[1]];
    let best = null, bestD = Infinity;
    for (const c of g.countries) {
      if (!c.f || d3.geoDistance(c.centroid, center) > Math.PI / 2 - 0.05) continue;
      const [cx, cy] = projection(c.centroid), d = Math.hypot(cx - x, cy - y);
      if (d < reach * k && d < bestD && isSmall(c)) { best = c.name; bestD = d; }
    }
    if (best) return best;
    if (hitName) return hitName;
    // A tap on the sea: the nearest country, if it is close.
    for (const c of g.countries) {
      if (!c.f || d3.geoDistance(c.centroid, center) > Math.PI / 2 - 0.05) continue;
      const [cx, cy] = projection(c.centroid), d = Math.hypot(cx - x, cy - y);
      if (d < 30 * k && d < bestD) { best = c.name; bestD = d; }
    }
    return best;
  }

  // Gentle spin while nobody is touching the globe.
  function spin() {
    if (!g.visible) { g.raf = 0; return; }
    if (Date.now() - g.lastInteract > 4000 && !calm()) {
      g.rotate = [g.rotate[0] + 0.08 / g.zoom, g.rotate[1]];
      draw();
    } else if (!calm() && (showStrikes() || showLanes())) { drawLanes(); drawStrikes(); }
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
    const fact = g.facts?.[name];
    const about = fact ? `<div class="country-about">
        <h3 class="label">About ${esc(c.display)}</h3>
        <p>${esc(fact.about)}</p>
        <p class="country-facts"><span>Capital <b>${esc(fact.capital)}</b></span><span>Population <b>${esc(fact.population)}</b></span></p>
      </div>` : "";
    $("#sheet-body").innerHTML = `<p class="muted">${all.length ? `${all.length} ${all.length === 1 ? "story" : "stories"} today` : "No stories mention it today."}</p>
      ${all.length ? "" : about}
      <div class="sheet-list">${all.map(({ item, key }) => `
        <a class="sheet-story" href="#" data-story="${esc(item.link)}" style="--tc:${look(key).color}">
          <span class="story-meta"><span class="dot"></span>${esc(look(key).short)} · ${esc(item.source)} · ${esc(timeAgo(item.published))}</span>
          <span class="serif">${esc(item.title)}</span>
        </a>`).join("")}</div>
      ${all.length ? about : ""}
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
    const strikes = g.strikes.map((s) => `${s.origin ? `${s.origin.c.display} -> ` : ""}${s.target.c.display}: ${s.item.title}`);
    const lanes = LANES.map((l) => `${l.name}: ${LANE_STATE[l.state]}${l.news[0] ? ` (${l.news[0].item.title})` : ""}`);
    return { mode: mode.label, countries: top, strikes, lanes, open: g.open };
  };

  document.addEventListener("click", (e) => {
    const zb = e.target.closest("[data-zoom]");
    if (zb) { setZoom(zb.dataset.zoom === "in" ? g.zoom * 1.6 : zb.dataset.zoom === "out" ? g.zoom / 1.6 : 1); return; }
    const mode = e.target.closest("[data-mode]");
    if (mode) { g.mode = mode.dataset.mode; renderModes(); compute(); return; }
    const row = e.target.closest(".strike-row");
    if (row) { e.preventDefault(); openStrike(+row.dataset.strike); return; }
    const laneRow = e.target.closest(".lane-row");
    if (laneRow) { e.preventDefault(); openLane(+laneRow.dataset.lane); return; }
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
