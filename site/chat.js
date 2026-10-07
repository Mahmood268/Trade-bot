// "Ask Claude" chat panel. Calls the Anthropic API directly from the browser
// with the user's own key (kept in localStorage), giving Claude today's
// headlines and market prices as context.
import { marked } from "https://cdn.jsdelivr.net/npm/marked@18.1.0/+esm";
import DOMPurify from "https://cdn.jsdelivr.net/npm/dompurify@3.4.16/+esm";

const SDK_URL = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.131.0/+esm";
const MAX_CONTINUATIONS = 5;
const HISTORY_KEY = "chat-history";

const SYSTEM_PROMPT = `You are a sharp news and markets analyst chatting with one reader on their \
personal news dashboard. They follow US politics (Trump), the Middle East, stock markets, gold and \
commodities, and AI.

You are given today's headlines and market prices below. Use them, and use web search when you \
need fresher or deeper information than the headlines give.

For "what if" and forecasting questions:
- Lay out 2-4 concrete scenarios with rough probabilities and the reasoning behind each.
- Trace second-order effects across markets (oil, gold, the dollar, yields, equities, sectors) \
and name historical precedents where they are useful.
- Name the signposts to watch that would tell which scenario is playing out.
- Be candid about uncertainty, and say briefly that this is analysis, not financial advice.

Keep answers well-structured and skimmable: short paragraphs, bullets, and a small table when \
comparing scenarios. Cite outlets when you rely on a specific headline.

Each question comes with a note of what the reader has open on screen right now (a story card, \
a section page, a country on the globe). When they say "this", "here" or "these", they mean what \
is on that screen, so answer about it directly.`;

const $ = (sel) => document.querySelector(sel);
const store = {
  get(key, fallback = null) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch {} },
  remove(key) { try { localStorage.removeItem(key); } catch {} },
};

// One conversation: the news context is frozen when it starts so earlier
// turns (and their thinking blocks) stay valid.
let convo = loadHistory();
let busy = false;
let clientPromise = null;

function loadHistory() {
  try {
    const saved = JSON.parse(store.get(HISTORY_KEY, "null"));
    if (saved && Array.isArray(saved.messages)) return saved;
  } catch {}
  return { context: null, messages: [] };
}

function saveHistory() {
  store.set(HISTORY_KEY, JSON.stringify(convo));
}

function newsContext() {
  const data = window.NEWS_DATA;
  if (!data) return "No news data is loaded right now.";
  const lines = [`News snapshot generated at ${data.generated_at} (UTC).`, "", "## Markets"];
  for (const m of data.markets || []) {
    const chg = m.change_pct == null ? "n/a" : `${m.change_pct >= 0 ? "+" : ""}${m.change_pct.toFixed(2)}%`;
    lines.push(`- ${m.label}: ${m.price} (${chg} vs previous close)`);
  }
  if (data.briefing) {
    lines.push("", "## Today's AI briefing", data.briefing.headline);
  }
  for (const t of data.topics || []) {
    lines.push("", `## ${t.label}`);
    for (const i of t.items.slice(0, 12)) {
      const outlets = [i.source, ...(i.also || [])].join(", ");
      lines.push(`- [${outlets}] ${i.title}${i.summary ? ` — ${i.summary}` : ""} (${i.link})`);
    }
  }
  return lines.join("\n");
}

function getClient(apiKey) {
  if (!clientPromise) {
    clientPromise = import(SDK_URL).then((mod) => mod.default);
  }
  return clientPromise.then((Anthropic) => new Anthropic({ apiKey, dangerouslyAllowBrowser: true }));
}

// ---------- rendering ----------

function md(text) {
  return DOMPurify.sanitize(marked.parse(text || ""), { ADD_ATTR: ["target"] });
}

function scrollToEnd() {
  const log = $("#chat-log");
  log.scrollTop = log.scrollHeight;
}

function addUserBubble(text, screenLabel) {
  $(".chat-empty")?.remove();
  const div = document.createElement("div");
  div.className = "msg user";
  div.textContent = text;
  $("#chat-log").append(div);
  if (screenLabel) {
    const note = document.createElement("div");
    note.className = "msg-screen";
    note.textContent = `Looking at: ${screenLabel}`;
    $("#chat-log").append(note);
  }
  scrollToEnd();
}

// What the reader has open, attached to each question they ask.
function currentScreen() {
  try { return window.NEWS_APP?.screenContext() || null; } catch { return null; }
}

// History keeps the question and the screen apart (for display); the API gets them joined.
function toApi(m) {
  if (m.role !== "user" || !m.screen) return { role: m.role, content: m.content };
  return { role: "user", content: `<screen>\n${m.screen.text}\n</screen>\n\n${m.content}` };
}

function addAssistantBubble() {
  const div = document.createElement("div");
  div.className = "msg assistant";
  $("#chat-log").append(div);
  return div;
}

function textOf(content) {
  return content.filter((b) => b.type === "text").map((b) => b.text).join("");
}

function sourcesOf(content) {
  const seen = new Map();
  for (const block of content) {
    for (const c of block.citations || []) {
      if (c.url && !seen.has(c.url)) seen.set(c.url, c.title || new URL(c.url).hostname);
    }
  }
  return [...seen].map(([url, title]) => ({ url, title }));
}

function renderAssistant(div, text, sources = []) {
  div.innerHTML = md(text) + (sources.length ? `<div class="sources">Sources: ${
    sources.map((s) => `<a href="${encodeURI(s.url)}" target="_blank" rel="noopener noreferrer">${
      DOMPurify.sanitize(s.title)}</a>`).join(" · ")}</div>` : "");
  div.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
}

function setStatus(div, text) {
  div.innerHTML = `<span class="status">${text}</span>`;
}

function showError(text) {
  const div = document.createElement("div");
  div.className = "msg error";
  div.textContent = text;
  $("#chat-log").append(div);
  scrollToEnd();
}

function renderHistory() {
  if (!convo.messages.length) return;
  $(".chat-empty")?.remove();
  for (const m of convo.messages) {
    if (m.role === "user") {
      addUserBubble(typeof m.content === "string" ? m.content : textOf(m.content), m.screen?.label);
    } else {
      renderAssistant(addAssistantBubble(), textOf(m.content), sourcesOf(m.content));
    }
  }
  scrollToEnd();
}

// ---------- sending ----------

async function send(question, { spoken = false } = {}) {
  if (busy || !question.trim()) return;
  stopSpeaking();
  const apiKey = store.get("anthropic-key");
  if (!apiKey) {
    openSettings();
    showError("Add your Anthropic API key in settings first.");
    return;
  }

  busy = true;
  $("#chat-send").disabled = true;
  if (!convo.context) convo.context = newsContext();
  const screen = currentScreen();
  convo.messages.push({ role: "user", content: question, ...(screen ? { screen } : {}) });
  addUserBubble(question, screen?.label);
  const bubble = addAssistantBubble();
  setStatus(bubble, "Thinking…");
  scrollToEnd();

  const model = store.get("model", "claude-opus-5-5");
  const webSearch = store.get("web-search", "1") === "1";
  const assistant = { role: "assistant", content: [] };

  try {
    const client = await getClient(apiKey);
    let streamedText = "";
    for (let i = 0; i <= MAX_CONTINUATIONS; i++) {
      const history = convo.messages.map(toApi);
      const messages = assistant.content.length ? [...history, assistant] : history;
      const stream = client.beta.messages.stream({
        model,
        max_tokens: 32000,
        system: [
          { type: "text", text: SYSTEM_PROMPT },
          { type: "text", text: convo.context, cache_control: { type: "ephemeral" } },
        ],
        messages,
        output_config: { effort: "medium" },
        ...(webSearch ? { tools: [{ type: "web_search_20260209", name: "web_search", max_uses: 5 }] } : {}),
        betas: ["server-side-fallback-2026-07-01"],
        fallbacks: "default",
      });

      let frame = 0;
      stream.on("streamEvent", (event) => {
        if (event.type === "content_block_start" && event.content_block.type === "server_tool_use" && !streamedText) {
          setStatus(bubble, "Searching the web…");
        }
      });
      stream.on("text", (delta) => {
        streamedText += delta;
        if (!frame) {
          frame = requestAnimationFrame(() => { frame = 0; renderAssistant(bubble, streamedText); scrollToEnd(); });
        }
      });

      const final = await stream.finalMessage();
      if (frame) cancelAnimationFrame(frame);
      assistant.content.push(...final.content);

      if (final.stop_reason === "pause_turn") continue; // server-side search loop paused; resume it
      if (final.stop_reason === "refusal") {
        renderAssistant(bubble, streamedText || "_Claude declined to answer this one._");
      } else {
        renderAssistant(bubble, textOf(assistant.content), sourcesOf(assistant.content));
        if (final.stop_reason === "max_tokens") showError("The answer was cut off (length limit).");
        if (spoken || store.get("chat-speak") === "1") speak(bubble.innerText);
      }
      break;
    }
    convo.messages.push(assistant);
    saveHistory();
  } catch (err) {
    convo.messages.pop(); // drop the unanswered question so history stays valid
    bubble.remove();
    const status = err?.status;
    showError(status === 401 ? "Your API key was rejected — check it in settings."
      : status === 429 ? "Rate limited — wait a moment and try again."
      : `Request failed: ${err?.message || err}`);
  } finally {
    busy = false;
    $("#chat-send").disabled = false;
    scrollToEnd();
  }
}

// ---------- talking ----------

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;

function listen() {
  if (recognizer) { recognizer.stop(); return; }
  recognizer = new Recognition();
  recognizer.lang = navigator.language || "en-US";
  recognizer.interimResults = true;
  let finalText = "";
  const before = $("#chat-input").value;
  stopSpeaking();
  $("#chat-mic").classList.add("listening");
  recognizer.onresult = (e) => {
    let interim = "";
    finalText = "";
    for (const r of e.results) { if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript; }
    $("#chat-input").value = `${before}${before ? " " : ""}${finalText}${interim}`;
  };
  recognizer.onerror = (e) => {
    if (e.error === "not-allowed") showError("Microphone access was blocked. Allow it in your browser to talk to Claude.");
  };
  recognizer.onend = () => {
    $("#chat-mic").classList.remove("listening");
    recognizer = null;
    const q = $("#chat-input").value.trim();
    if (finalText.trim() && q) {
      $("#chat-input").value = "";
      send(q, { spoken: true });
    }
  };
  recognizer.start();
}

function speak(text) {
  if (!("speechSynthesis" in window) || !text.trim()) return;
  speechSynthesis.cancel();
  const voice = speechSynthesis.getVoices().find((v) => v.voiceURI === store.get("speech-voice"));
  // Speak sentence by sentence: some browsers cut off long utterances.
  for (const part of text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]*/g) || []) {
    const u = new SpeechSynthesisUtterance(part.trim());
    if (voice) { u.voice = voice; u.lang = voice.lang; }
    u.rate = Number(store.get("speech-rate", "1"));
    speechSynthesis.speak(u);
  }
}

function stopSpeaking() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

// ---------- UI wiring ----------

function openChat(prefill) {
  $("#chat").hidden = false;
  $("#chat-seeing").textContent = currentScreen()?.label || "";
  if (!store.get("anthropic-key")) openSettings();
  if (prefill) $("#chat-input").value = prefill;
  $("#chat-input").focus();
}

function openSettings() {
  $("#chat-settings").hidden = false;
  $("#api-key").value = store.get("anthropic-key", "");
  $("#model").value = store.get("model", "claude-opus-5-5");
  $("#web-search").checked = store.get("web-search", "1") === "1";
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-open-chat]")) openChat();
  if (e.target.closest("[data-chat-settings]")) { openChat(); openSettings(); }
});
$("#chat-close").addEventListener("click", () => { $("#chat").hidden = true; stopSpeaking(); recognizer?.stop(); });
if (Recognition) {
  $("#chat-mic").hidden = false;
  $("#chat-mic").addEventListener("click", listen);
}
$("#chat-speak").setAttribute("aria-pressed", store.get("chat-speak") === "1");
$("#chat-speak").addEventListener("click", () => {
  const on = store.get("chat-speak") !== "1";
  store.set("chat-speak", on ? "1" : "0");
  $("#chat-speak").setAttribute("aria-pressed", on);
  if (!on) stopSpeaking();
});
$("#chat-settings-toggle").addEventListener("click", () => {
  if ($("#chat-settings").hidden) openSettings(); else $("#chat-settings").hidden = true;
});
$("#chat-settings").addEventListener("submit", (e) => {
  e.preventDefault();
  const key = $("#api-key").value.trim();
  if (key) store.set("anthropic-key", key); else store.remove("anthropic-key");
  store.set("model", $("#model").value);
  store.set("web-search", $("#web-search").checked ? "1" : "0");
  $("#chat-settings").hidden = true;
  window.dispatchEvent(new CustomEvent("chat-settings-saved"));
});
$("#chat-new").addEventListener("click", () => {
  if (busy) return;
  convo = { context: null, messages: [] };
  store.remove(HISTORY_KEY);
  $("#chat-log").innerHTML = '<div class="chat-empty"><p class="muted">New conversation started.</p></div>';
});
$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#chat-input").value;
  $("#chat-input").value = "";
  send(q);
});
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
$("#chat-log").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) send(chip.textContent);
});
window.addEventListener("ask-claude", (e) => openChat(e.detail.text));

renderHistory();
