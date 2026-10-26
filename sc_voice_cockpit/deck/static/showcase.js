// Showcase page: shows the deck in an iPhone or iPad frame and logs, live, the keys the PC
// would press. The log comes from /events, which the server only offers in --dry-run.
(() => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const token = (params.get("t") || "").trim().toLowerCase();

  // Sizes in CSS points, landscape. `safe` is top,right,bottom,left as iOS reports it.
  const DEVICES = {
    phone: { name: "iPhone 15 Pro", w: 852, h: 393, bezel: 12, radius: 55, safe: [0, 59, 21, 59] },
    tablet: { name: "iPad", w: 1024, h: 768, bezel: 26, radius: 18, safe: [0, 0, 0, 0] },
  };
  let device = DEVICES[params.get("device")] ? params.get("device") : "phone";

  const frame = $("device");
  const deck = $("deck");
  const stage = $("stage");

  function deckUrl() {
    return `/?t=${encodeURIComponent(token)}&device=${device}&safe=${DEVICES[device].safe.join(",")}`;
  }

  function showDevice(id) {
    device = id;
    const d = DEVICES[id];
    frame.className = `device ${id}`;
    frame.style.setProperty("--w", `${d.w}px`);
    frame.style.setProperty("--h", `${d.h}px`);
    frame.style.setProperty("--bezel", `${d.bezel}px`);
    frame.style.setProperty("--radius", `${d.radius}px`);
    for (const b of document.querySelectorAll(".seg button")) b.setAttribute("aria-pressed", String(b.dataset.device === id));
    params.set("device", id);
    history.replaceState(null, "", `?${params}`);
    if (token) deck.src = deckUrl();
    fit();
  }

  // Scale the frame to fit the stage, never above real size.
  let scale = 1;
  function fit() {
    const d = DEVICES[device];
    const w = d.w + 2 * d.bezel + 8, h = d.h + 2 * d.bezel + 8;
    const cs = getComputedStyle(stage);
    const aw = stage.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const ah = stage.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - 32; // room for the caption
    scale = Math.max(0.2, Math.min(1, aw / w, ah / h));
    frame.style.transform = `scale(${scale})`;
    // A transform doesn't shrink the layout box; negative margins take up the slack.
    frame.style.margin = `${(-(1 - scale) * h) / 2}px ${(-(1 - scale) * w) / 2}px`;
    const [, r, b, l] = d.safe;
    $("caption").textContent = `${d.name} · ${d.w}×${d.h} pt landscape · ` +
      (r || b || l ? `safe area ${l} / ${r} / ${b} pt (left / right / bottom)` : "no safe-area insets") +
      ` · shown at ${Math.round(scale * 100)}%`;
  }
  new ResizeObserver(fit).observe(stage);

  for (const b of document.querySelectorAll(".seg button")) b.onclick = () => showDevice(b.dataset.device);
  $("reload").onclick = () => { if (token) deck.src = deckUrl(); };

  // ---------- key log ----------

  const entries = $("entries");
  let count = 0;
  let focusCheck = true;
  const time = (ms) => new Date(ms).toLocaleTimeString([], { hour12: false });
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };

  // Adds a row: `what` is a list of nodes or strings, `meta` a list of [class, text].
  function add(ms, what, meta = [], cls = "") {
    const li = el("li", `new ${cls}`);
    li.append(el("span", "t", time(ms)));
    const w = el("div", "what");
    w.append(...what);
    li.append(w);
    const m = el("div", "meta");
    for (const [c, text] of meta) m.append(el("span", c, text));
    li.append(m);
    // Newest first. A press can arrive a moment late (it waits for the focus check).
    li.dataset.time = ms;
    const after = [...entries.children].find((row) => Number(row.dataset.time) <= ms);
    entries.insertBefore(li, after || null);
    while (entries.children.length > 300) entries.lastChild.remove();
    $("empty").hidden = true;
    $("count").textContent = `${++count} event${count === 1 ? "" : "s"}`;
  }

  const keycaps = (keys) => {
    const span = el("span", "what");
    String(keys).split("+").forEach((k, i) => {
      if (i) span.append("+");
      span.append(el("kbd", "", k.trim().replace(/^np_/, "num ")));
    });
    return span;
  };

  const KIND = {
    tap: (e) => `Tap · ${e.ms} ms`,
    timed: (e) => `Long press · ${e.ms} ms`,
    voice: (e) => `Voice · ${e.ms} ms`,
    down: () => "Hold ↓",
    up: () => "Release ↑",
  };

  function verdict(e) {
    if (e.kind === "up") return ["ok", "released (always sent)"];
    if (!focusCheck) return ["ok", "would send (focus check off)"];
    if (e.blocked === true) return ["warn-text", "would be blocked: Star Citizen not in front"];
    if (e.blocked === false) return ["ok", "would send: Star Citizen in front"];
    return ["dim", "focus unknown"];
  }

  function onEvent(e) {
    if (e.t === "hello") {
      focusCheck = e.focusCheck;
      setMode("Dry run · no keys sent", "ok");
      add(e.time, ["Sandbox connected"], [
        ["", focusCheck ? (e.focusProbe ? "Focus check on: each press says if it would be blocked" : "Focus check on, but it can't be probed") : "Focus check is off in config.json"],
        ["", e.voice ? "Voice commands ready" : "Voice commands not running"],
      ], "info");
    } else if (e.t === "client") {
      add(e.time, [`Deck ${e.on ? "connected" : "disconnected"} (${e.device})`], [], "info");
    } else if (e.t === "press") {
      add(e.time, [el("b", "", e.label), el("span", "arrow", "→"), keycaps(e.keys)], [["tag", KIND[e.kind](e)], verdict(e)]);
    } else if (e.t === "voice") {
      if (e.result === "listen") add(e.time, ["Hold to talk: listening…"], e.running ? [] : [["warn-text", "voice commands aren't running on the PC"]], "info");
      else if (e.result === "stop") add(e.time, ["Talk button released"], [], "info");
      else if (e.result === "heard") add(e.time, [el("b", "", `Heard “${e.text}”`), el("span", "arrow", "→"), e.label], [["tag", "Voice"], ["", `confidence ${e.confidence.toFixed(2)}`]]);
      else if (e.result === "ignored") add(e.time, [`Heard “${e.text}” (${e.label})`], [["warn-text", `ignored, confidence ${e.confidence.toFixed(2)}`]]);
      else add(e.time, ["Voice: didn't catch that"], [], "info");
    }
  }

  function setMode(text, cls) {
    const m = $("mode");
    m.textContent = text;
    m.className = `pill ${cls || ""}`;
  }

  function warn(text) {
    $("warn").textContent = text;
    $("warn").hidden = false;
  }

  function connectEvents() {
    const url = `/events?t=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch {} };
    es.onerror = async () => {
      if (es.readyState !== EventSource.CLOSED) { setMode("Reconnecting…"); return; }
      // The stream was refused: find out why from the status code.
      const ctl = new AbortController();
      const r = await fetch(url, { signal: ctl.signal }).catch(() => null);
      ctl.abort();
      if (r && r.status === 404) {
        setMode("LIVE: real keys", "bad");
        warn("This server isn't running with --dry-run, so taps on this page send real keys to the PC and there's no key log. Start it with --dry-run (or the deck-sandbox launch config) to use the sandbox.");
      } else if (r && r.status === 403) {
        setMode("Not paired", "bad");
        warn("That pairing code didn't match. Check config.json or the Tablet Deck window on the PC.");
      } else {
        setMode("Reconnecting…");
        setTimeout(connectEvents, 2000);
      }
    };
  }

  $("clear").onclick = () => {
    entries.innerHTML = "";
    count = 0;
    $("count").textContent = "";
    $("empty").hidden = false;
  };

  // Mode switches, panel arrows and menus change only the screen; log them so it's clear
  // that nothing reached the PC. The deck is served from this origin, so its DOM is reachable.
  deck.addEventListener("load", () => {
    const doc = deck.contentDocument;
    if (!doc) return;
    doc.addEventListener("click", (ev) => {
      const btn = ev.target.closest && ev.target.closest(".s-switch, .s-pg, .s-menu");
      if (!btn) return;
      const regions = [...doc.querySelectorAll(".hud-region")];
      const idx = regions.indexOf(btn.closest(".hud-region"));
      const label = btn.getAttribute("aria-label") || btn.textContent.trim();
      // Read the result once the deck has re-rendered the region.
      setTimeout(() => {
        const region = doc.querySelectorAll(".hud-region")[idx];
        let what;
        if (btn.classList.contains("s-switch")) {
          const now = region && region.querySelector(".hud-header.state .a");
          what = `${label} → ${now ? now.textContent : "next mode"}`;
        } else if (btn.classList.contains("s-pg")) {
          const title = region && region.querySelector(".hud-pager .title span");
          what = `${label} → ${title ? title.textContent : "panel"}`;
        } else {
          what = `Opened menu: ${label.replace(/▾/, "").trim()}`;
        }
        add(Date.now(), [what], [["tag", "Screen only"], ["dim", "no keys sent"]], "info");
      }, 0);
    }, true);
  });

  // ---------- start ----------

  if (!token) {
    $("pair").hidden = false;
    $("code").focus();
    $("pair").onsubmit = (ev) => {
      ev.preventDefault();
      const code = $("code").value.trim();
      if (!code) return;
      params.set("t", code);
      location.search = `?${params}`;
    };
    setMode("Not paired");
    showDevice(device);
    return;
  }
  showDevice(device);
  connectEvents();
})();
