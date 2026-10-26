// Layout editor: shows a canvas layout with the deck's own renderer (canvas.js), where
// clicking a button selects it instead of pressing keys. Drag to move, drag the corner to
// resize, arrow keys to nudge; the side panel edits the selected button. Saving goes to
// the server (/editor/save), which checks the layout and stores it as the player's own.
// Export saves the layout as a file to share; Import opens such a file unsaved, to look at
// and then save like any edit, so it gets the same checks.
(() => {
  const $ = (id) => document.getElementById(id);
  const token = (new URLSearchParams(location.search).get("t") || "").trim().toLowerCase();
  const api = (p) => `/editor/${p}${p.includes("?") ? "&" : "?"}t=${encodeURIComponent(token)}`;
  try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "rsi"; } catch { /* default theme */ }

  const COLORS = ["", "green", "amber", "red", "blue"];
  const ANIMS = ["", "flash", "ripple", "gear", "charge", "burst", "glow", "recoil", "streak", "doors", "lock", "hazard", "timed"];
  const MODES = { tap: "Tap (quick press)", hold: "Hold (held while your finger is down)", timed: "Long press (held for a set time)" };

  let info = null;       // { layouts, keys, overrides } from the server
  let file = "";         // the layout file being edited
  let layout = null;     // the layout itself, changed in place
  let deck = null;       // canvas.js handle: render(), panelShown()
  let selectedId = null; // id of the selected button
  let dirty = false;
  const undoStack = [];
  const redoStack = [];
  const elementOf = new Map(); // button id -> its element in the preview
  const fxOf = new Map();      // button id -> its press animation, for "Try animation"
  const memory = {};           // the preview's page positions, kept apart from the real deck's
  const store = { get: (k) => (k in memory ? memory[k] : null), set: (k, v) => { memory[k] = v; } };

  // ---------- messages ----------

  let statusTimer = null;
  function status(text, bad) {
    const el = $("edStatus");
    el.textContent = text;
    el.classList.toggle("bad", Boolean(bad));
    el.classList.add("show");
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => el.classList.remove("show"), bad ? 6000 : 3000);
  }

  // ---------- layout helpers ----------

  const isButton = (n) => n && typeof n === "object" && typeof n.id === "string" && typeof n.keys === "string";

  // Every button with the array it sits in.
  function eachButton(fn) {
    (function walk(node, parent) {
      if (Array.isArray(node)) { node.forEach((n) => walk(n, node)); return; }
      if (!node || typeof node !== "object") return;
      if (isButton(node)) fn(node, parent);
      for (const v of Object.values(node)) if (v && typeof v === "object") walk(v, Array.isArray(node) ? node : null);
    })(layout, null);
  }
  function find(id) {
    let found = null;
    eachButton((b, parent) => { if (!found && b.id === id) found = { button: b, parent }; });
    return found;
  }
  const selected = () => (selectedId && find(selectedId)?.button) || null;

  // The keys a button presses: its Star Citizen keybind when it follows one.
  const effectiveKeys = (b) => (b.scAction && info.overrides[b.scAction]) || b.keys;

  // Same as server.js spoken(): "Shields +" is said as "shields up".
  const spoken = (label) => String(label).toLowerCase()
    .replace(/\s*\+$/, " up").replace(/\s*[−-]$/, " down").replace(/&/g, " and ")
    .replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim();
  const phrasesOf = (b) => (b.voice === false ? [] : [b.label, ...(Array.isArray(b.voice) ? b.voice : [])].map(spoken).filter(Boolean));

  function newId() {
    let n = 1;
    while (find(`my-button-${n}`)) n++;
    return `my-button-${n}`;
  }

  // ---------- undo ----------

  function snapshot() {
    undoStack.push(JSON.stringify(layout));
    if (undoStack.length > 200) undoStack.shift();
    redoStack.length = 0;
    markDirty();
  }
  function restore(json) {
    const prev = JSON.parse(json);
    for (const k of Object.keys(layout)) delete layout[k];
    Object.assign(layout, prev);
    if (selectedId && !find(selectedId)) selectedId = null;
    redraw();
    renderProps();
  }
  function undo() { if (!undoStack.length) return; redoStack.push(JSON.stringify(layout)); restore(undoStack.pop()); markDirty(); }
  function redo() { if (!redoStack.length) return; undoStack.push(JSON.stringify(layout)); restore(redoStack.pop()); markDirty(); }
  function markDirty() {
    dirty = true;
    $("undoBtn").disabled = !undoStack.length;
    $("redoBtn").disabled = !redoStack.length;
  }
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  // ---------- preview ----------

  // Gives the stage the layout's exact shape, so canvas pixels map 1:1 to layout coordinates.
  function fitStage() {
    if (!layout) return;
    const pane = $("pane");
    const [W, H] = layout.canvas;
    const s = Math.min((pane.clientWidth - 32) / W, (pane.clientHeight - 32) / H);
    const stage = $("stage");
    stage.style.width = `${W * s}px`;
    stage.style.height = `${H * s}px`;
    stage.style.left = `${(pane.clientWidth - W * s) / 2}px`;
    stage.style.top = `${(pane.clientHeight - H * s) / 2}px`;
  }
  window.addEventListener("resize", fitStage);

  const D = {
    send() {}, buzz() {}, openPicker() {}, openVoiceHelp() {}, toggleFullscreen() {}, bindTalk() {},
    // The preview isn't connected to anything; its status line says so instead of "Connecting…".
    refreshStatus() {
      for (const d of document.querySelectorAll(".js-dot")) d.classList.add("on");
      for (const t of document.querySelectorAll(".js-status")) t.textContent = "Editing";
    },
    store,
    deckIcon: window.deckIcon,
    emblemHtml: () => window.deckIcon("ship"),
    // Buttons select (and drag) instead of pressing keys.
    bindButton(el, def) {
      elementOf.set(def.id, el);
      fxOf.set(def.id, window.DeckFX.attach(el, def));
      el.addEventListener("pointerdown", (e) => startDrag(e, el, def));
      el.addEventListener("contextmenu", (e) => e.preventDefault());
    },
    afterRender: drawSelection,
  };

  function redraw() {
    elementOf.clear();
    fxOf.clear();
    if (deck) deck.render();
  }

  // ---------- selecting, dragging, resizing ----------

  function select(id) {
    selectedId = id;
    drawSelection();
    renderProps();
  }

  // The canvas is scaled to fit; this turns screen pixels into layout pixels.
  const scale = () => $("canvas").getBoundingClientRect().width / $("canvas").offsetWidth;

  function drawSelection() {
    const canvas = $("canvas");
    let box = $("edSel");
    const el = selectedId && elementOf.get(selectedId);
    if (!el || !el.isConnected) { if (box) box.remove(); return; }
    if (!box || box.parentNode !== canvas) {
      if (box) box.remove();
      box = document.createElement("div");
      box.id = "edSel";
      box.innerHTML = `<i class="handle" title="Drag to resize"></i>`;
      box.querySelector(".handle").addEventListener("pointerdown", startResize);
      canvas.appendChild(box);
    }
    const s = scale(), c = canvas.getBoundingClientRect(), r = el.getBoundingClientRect();
    Object.assign(box.style, {
      left: `${(r.left - c.left) / s}px`, top: `${(r.top - c.top) / s}px`,
      width: `${r.width / s}px`, height: `${r.height / s}px`,
    });
    // Buttons in automatic grids and menus have no position of their own.
    box.querySelector(".handle").hidden = typeof selected()?.x !== "number";
  }

  function startDrag(e, el, def) {
    e.preventDefault();
    e.stopPropagation();
    if (selectedId !== def.id) select(def.id);
    if (typeof def.x !== "number") return;
    const s = scale(), start = { px: e.clientX, py: e.clientY, x: def.x, y: def.y };
    let moved = false;
    el.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = Math.round((ev.clientX - start.px) / s), dy = Math.round((ev.clientY - start.py) / s);
      if (!moved && Math.abs(dx) + Math.abs(dy) < 3) return;
      if (!moved) { snapshot(); moved = true; }
      def.x = start.x + dx;
      def.y = start.y + dy;
      el.style.left = `${def.x}px`;
      el.style.top = `${def.y}px`;
      drawSelection();
    };
    const end = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", end);
      el.removeEventListener("pointercancel", end);
      if (moved) { redraw(); renderProps(); }
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  function startResize(e) {
    const def = selected();
    if (!def || typeof def.w !== "number") return;
    e.preventDefault();
    e.stopPropagation();
    const handle = e.currentTarget, s = scale();
    const start = { px: e.clientX, py: e.clientY, w: def.w, h: def.h };
    snapshot();
    handle.setPointerCapture(e.pointerId);
    const move = (ev) => {
      def.w = Math.max(24, start.w + Math.round((ev.clientX - start.px) / s));
      def.h = Math.max(24, start.h + Math.round((ev.clientY - start.py) / s));
      const el = elementOf.get(def.id);
      if (el) { el.style.width = `${def.w}px`; el.style.height = `${def.h}px`; }
      drawSelection();
    };
    const end = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      redraw();
      renderProps();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
  }

  // Clicking empty space clears the selection.
  $("pane").addEventListener("pointerdown", (e) => {
    if (e.target === $("pane") || e.target === $("stage")) select(null);
  });

  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if (ctrl && e.key.toLowerCase() === "y" && !typing) { e.preventDefault(); redo(); return; }
    if (ctrl && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
    if (typing) return;
    const def = selected();
    if (!def) return;
    if (ctrl && e.key.toLowerCase() === "d") { e.preventDefault(); duplicate(); return; }
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); remove(); return; }
    const step = e.shiftKey ? 10 : 1;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[e.key] && typeof def.x === "number") {
      e.preventDefault();
      snapshot();
      def.x += moves[e.key][0];
      def.y += moves[e.key][1];
      redraw();
      renderProps();
    }
  });

  // ---------- add, duplicate, delete ----------

  // The panel shown in the first region, for "Add button" with nothing selected.
  function visibleElements() {
    const region = layout.regions && layout.regions[0];
    if (!region) return layout.chrome || [];
    return layout.panels[region.panels[deck.panelShown(region.id)]].elements;
  }

  // An F-key no button uses yet, so a new button doesn't clash.
  function freeKey() {
    const used = new Set();
    eachButton((b) => used.add(effectiveKeys(b)));
    for (let n = 13; n <= 24; n++) if (!used.has(`f${n}`)) return `f${n}`;
    return "f13";
  }

  function add() {
    const near = selected() && find(selectedId);
    const target = near ? near.parent : visibleElements();
    const placed = !near || typeof near.button.x === "number";
    const b = { type: "button", id: newId(), label: "New button", keys: freeKey(), mode: "tap" };
    if (placed) {
      Object.assign(b, near ? { x: near.button.x + 12, y: near.button.y + 12, w: near.button.w, h: near.button.h } : { x: 20, y: 20, w: 140, h: 56 });
      if (near && near.button.when) b.when = near.button.when;
      if (near && near.button.style) b.style = near.button.style;
    }
    snapshot();
    target.splice(near ? target.indexOf(near.button) + 1 : target.length, 0, b);
    redraw();
    select(b.id);
    status("Added a button. Set its label and keys on the right.");
  }

  function duplicate() {
    const near = selected() && find(selectedId);
    if (!near) return;
    const copy = JSON.parse(JSON.stringify(near.button));
    copy.id = newId();
    copy.label = `${near.button.label} copy`;
    delete copy.voice; // the copy's phrases would clash with the original's
    if (typeof copy.x === "number") { copy.x += 12; copy.y += 12; }
    snapshot();
    near.parent.splice(near.parent.indexOf(near.button) + 1, 0, copy);
    redraw();
    select(copy.id);
  }

  function remove() {
    const near = selected() && find(selectedId);
    if (!near) return;
    snapshot();
    near.parent.splice(near.parent.indexOf(near.button), 1);
    selectedId = null;
    redraw();
    renderProps();
    status(`Deleted "${near.button.label}". Undo brings it back.`);
  }

  // Grid and menu buttons have no position; they move within their list instead.
  function shift(by) {
    const near = selected() && find(selectedId);
    if (!near) return;
    const i = near.parent.indexOf(near.button), j = i + by;
    if (j < 0 || j >= near.parent.length) return;
    snapshot();
    near.parent.splice(i, 1);
    near.parent.splice(j, 0, near.button);
    redraw();
    renderProps();
  }

  // ---------- properties panel ----------

  function field(label, control, hint) {
    const wrap = document.createElement("div");
    const l = document.createElement("label");
    l.className = "f";
    l.textContent = label;
    wrap.append(l, control);
    if (hint) { const h = document.createElement("div"); h.className = "hint"; h.textContent = hint; wrap.append(h); }
    return wrap;
  }
  function make(tag, props = {}) { return Object.assign(document.createElement(tag), props); }

  // A text field: one undo step per edit, live preview while typing.
  function textInput(value, apply) {
    const input = make("input", { value: value ?? "" });
    let snapped = false;
    input.addEventListener("focus", () => { snapped = false; });
    input.addEventListener("input", () => {
      if (!snapped) { snapshot(); snapped = true; }
      apply(input.value);
      redraw();
    });
    return input;
  }
  function change(apply, rerenderProps = true) {
    return (e) => { snapshot(); apply(e.target); redraw(); if (rerenderProps) renderProps(); };
  }

  function renderProps() {
    const side = $("props");
    side.innerHTML = "";
    const b = selected();
    $("dupBtn").disabled = $("delBtn").disabled = !b;
    if (!b) {
      side.innerHTML = `<div class="help"><h2>Edit the layout</h2>
        <p>Click a button to select it, then drag it to move it, or drag the yellow corner to resize it. Arrow keys nudge it (hold Shift for bigger steps).</p>
        <p>The arrows under each half flip through panels, and mode switches (like Landing / Docking) work, so you can reach every button.</p>
        <p>Buttons in automatic grids and menus keep their place in the grid; move them with "Earlier" and "Later".</p>
        <p>Saving makes your own copy of the layout. The built-in layouts never change.</p></div>`;
      return;
    }
    side.append(make("h2", { textContent: b.label || "(no label)" }), make("div", { className: "id", textContent: `id: ${b.id}` }));

    side.append(field("Label", textInput(b.label, (v) => { b.label = v; side.querySelector("h2").textContent = v || "(no label)"; })));

    // Keys: follows the Star Citizen keybind for its action, or its own keys.
    const following = Boolean(b.scAction);
    const keysBox = make("div");
    if (b.scAction || b._scAction) {
      const action = b.scAction || b._scAction;
      const follow = make("label", { className: "check" });
      const cb = make("input", { type: "checkbox", checked: following });
      cb.addEventListener("change", change(() => {
        if (cb.checked) { b.scAction = b._scAction; delete b._scAction; }
        else { b._scAction = b.scAction; delete b.scAction; b.keys = effectiveKeys({ ...b, scAction: b._scAction }); }
      }));
      follow.append(cb, make("span", { textContent: "Follow game keybind" }));
      keysBox.append(follow);
      keysBox.append(make("div", { className: "hint", textContent: following
        ? `Uses your Star Citizen keybind for "${action}". To change its key, rebind it in the game, or untick this to set your own.`
        : `Uses its own keys below instead of your Star Citizen keybind for "${action}".` }));
    }
    const chips = make("div", { className: `chips${following ? " locked" : ""}` });
    const keys = String(effectiveKeys(b)).split("+").filter(Boolean);
    keys.forEach((k, i) => {
      const chip = make("span", { textContent: k });
      if (!following) {
        const x = make("button", { type: "button", textContent: "×", title: "Remove this key" });
        x.addEventListener("click", change(() => { b.keys = keys.filter((_, j) => j !== i).join("+") || "f13"; }));
        chip.append(x);
      }
      chips.append(chip);
    });
    keysBox.append(chips);
    if (!following) {
      const pick = make("select");
      pick.append(make("option", { value: "", textContent: "Add a key…" }));
      for (const k of info.keys) pick.append(make("option", { value: k, textContent: k }));
      pick.addEventListener("change", change(() => {
        if (!pick.value) return;
        // Modifiers go first, the main key last, as Star Citizen writes them.
        const mods = ["lalt", "ralt", "lctrl", "rctrl", "lshift", "rshift"];
        const next = [...keys.filter((k) => k !== pick.value), pick.value];
        next.sort((a, c) => (mods.includes(c) ? 1 : 0) - (mods.includes(a) ? 1 : 0));
        b.keys = next.join("+");
      }));
      keysBox.append(pick);
    }
    const sameKeys = [];
    eachButton((o) => { if (o !== b && effectiveKeys(o) === effectiveKeys(b)) sameKeys.push(o.label); });
    if (sameKeys.length) keysBox.append(make("div", { className: "warn", textContent: `Same keys as: ${[...new Set(sameKeys)].join(", ")}` }));
    side.append(field("Keys", keysBox));

    // Mode.
    const mode = make("select");
    for (const [v, t] of Object.entries(MODES)) mode.append(make("option", { value: v, textContent: t, selected: (b.mode || "tap") === v }));
    mode.addEventListener("change", change(() => { b.mode = mode.value; if (b.mode === "timed" && !b.ms) b.ms = 1500; }));
    side.append(field("How it presses", mode));
    if (b.mode === "timed") {
      const ms = make("input", { type: "number", min: 100, max: 10000, step: 100, value: b.ms || 1500 });
      ms.addEventListener("change", change(() => { b.ms = Math.max(100, Math.min(10000, Number(ms.value) || 1500)); }));
      side.append(field("Hold time (milliseconds)", ms));
    }

    // Look.
    const color = make("select");
    for (const c of COLORS) color.append(make("option", { value: c, textContent: c || "Theme colour", selected: (b.color || "") === c }));
    color.addEventListener("change", change(() => { if (color.value) b.color = color.value; else delete b.color; }));
    side.append(field("Colour", color));

    const iconBox = make("div");
    const current = make("div", { className: "icon-pick" });
    current.innerHTML = b.icon ? window.deckIcon(b.icon) : "";
    const toggle = make("button", { type: "button", textContent: b.icon ? `${b.icon} (change)` : "Pick an icon" });
    current.append(toggle);
    const gridEl = make("div", { className: "icon-grid", hidden: true });
    const none = make("button", { type: "button", textContent: "None", title: "No icon" });
    none.addEventListener("click", change(() => { delete b.icon; }));
    gridEl.append(none);
    for (const name of Object.keys(window.DECK_ICONS).filter((n) => n !== "default")) {
      const ib = make("button", { type: "button", title: name, className: b.icon === name ? "on" : "" });
      ib.innerHTML = window.deckIcon(name);
      ib.addEventListener("click", change(() => { b.icon = name; }));
      gridEl.append(ib);
    }
    toggle.addEventListener("click", () => { gridEl.hidden = !gridEl.hidden; });
    iconBox.append(current, gridEl);
    side.append(field("Icon", iconBox));

    const anim = make("select");
    for (const a of ANIMS) anim.append(make("option", { value: a, textContent: a || "Automatic", selected: (b.anim || "") === a }));
    anim.addEventListener("change", change(() => { if (anim.value) b.anim = anim.value; else delete b.anim; }));
    const tryIt = make("button", { type: "button", textContent: "Try it" });
    tryIt.addEventListener("click", () => {
      const fx = fxOf.get(b.id), el = elementOf.get(b.id);
      if (!fx || !el) return;
      const r = el.getBoundingClientRect();
      fx.down({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
      setTimeout(() => fx.up(), b.mode === "timed" ? Math.min(b.ms || 1500, 3000) : 250);
    });
    const animRow = make("div", { className: "row" });
    animRow.append(anim, tryIt);
    side.append(field("Press animation", animRow));

    // Voice.
    const voiceOn = make("label", { className: "check" });
    const vcb = make("input", { type: "checkbox", checked: b.voice !== false });
    vcb.addEventListener("change", change(() => { if (vcb.checked) delete b.voice; else b.voice = false; }));
    voiceOn.append(vcb, make("span", { textContent: "Responds to voice commands" }));
    const voiceBox = make("div");
    voiceBox.append(voiceOn);
    if (b.voice !== false) {
      const phrases = make("textarea", { value: (Array.isArray(b.voice) ? b.voice : []).join("\n"), placeholder: "One phrase per line, e.g. gear up" });
      let snapped = false;
      phrases.addEventListener("focus", () => { snapped = false; });
      phrases.addEventListener("input", () => {
        if (!snapped) { snapshot(); snapped = true; }
        const list = phrases.value.split("\n").map((p) => p.trim()).filter(Boolean);
        if (list.length) b.voice = list; else delete b.voice;
      });
      phrases.addEventListener("change", renderProps);
      voiceBox.append(phrases, make("div", { className: "hint", textContent: `Its label ("${spoken(b.label)}") always works too.` }));
      const clashes = [];
      const mine = phrasesOf(b), myKeys = effectiveKeys(b);
      eachButton((o) => {
        if (o === b || effectiveKeys(o) === myKeys) return;
        for (const p of phrasesOf(o)) if (mine.includes(p)) clashes.push(`"${p}" (${o.label})`);
      });
      if (clashes.length) voiceBox.append(make("div", { className: "warn", textContent: `Also used by other buttons, so voice ignores: ${clashes.join(", ")}` }));
    }
    side.append(field("Voice", voiceBox));

    // Position.
    if (typeof b.x === "number") {
      const row = make("div", { className: "row" });
      for (const k of ["x", "y", "w", "h"]) {
        const n = make("input", { type: "number", value: b[k], title: { x: "Left", y: "Top", w: "Width", h: "Height" }[k] });
        n.addEventListener("change", change(() => { b[k] = Math.max(k === "w" || k === "h" ? 24 : -2000, Math.round(Number(n.value) || 0)); }));
        row.append(n);
      }
      side.append(field("Left, top, width, height", row, "In layout pixels. You can also drag in the preview."));
    } else {
      const row = make("div", { className: "row" });
      const earlier = make("button", { type: "button", textContent: "◀ Earlier" });
      const later = make("button", { type: "button", textContent: "Later ▶" });
      earlier.addEventListener("click", () => shift(-1));
      later.addEventListener("click", () => shift(1));
      row.append(earlier, later);
      side.append(field("Place in its grid", row));
    }
  }

  // ---------- loading and saving ----------

  const kindOf = (l) => (l.canvas && l.canvas[1] < 500 ? "phone" : "tablet");

  function fillLayoutList(current) {
    const select = $("layoutSelect");
    select.innerHTML = "";
    for (const l of info.layouts) {
      const tags = [l.custom ? "yours" : "built-in", l.active ? `in use on ${l.active}s` : ""].filter(Boolean).join(", ");
      const o = make("option", { value: l.file, textContent: `${l.name} (${tags})`, selected: l.file === current });
      if (!l.canvas) { o.disabled = true; o.textContent += " - can't be edited here yet"; }
      select.append(o);
    }
  }

  function loadInfo() {
    return fetch(api("layouts")).then((r) => r.json().then((data) => (r.ok ? data : Promise.reject(data.error))));
  }

  function open(nextFile) {
    if (dirty && !confirm("Discard your unsaved changes?")) { $("layoutSelect").value = file; return; }
    fetch(api(`layout?file=${encodeURIComponent(nextFile)}`))
      .then((r) => r.json().then((data) => (r.ok ? data : Promise.reject(data.error))))
      .then((data) => {
        file = nextFile;
        layout = data;
        undoStack.length = redoStack.length = 0;
        dirty = false;
        markDirty();
        dirty = false;
        selectedId = null;
        const base = file.replace(/^layouts\//, "").replace(/\.json$/, "");
        $("saveName").value = layout.custom ? base : `my-${base}`;
        $("useLabel").textContent = kindOf(layout) === "phone" ? "Use on phones" : "Use on tablets";
        document.title = `${layout.name || base} - Tablet Deck layout editor`;
        fitStage();
        $("canvas").innerHTML = "";
        deck = window.renderCanvas(layout, D);
        renderProps();
      })
      .catch((err) => status(`Couldn't open the layout: ${err}`, true));
  }

  // The layout as saved: "_scAction" only remembers an unticked "Follow game keybind"
  // while editing, and scancodes come from the keys.
  const cleaned = () => JSON.parse(JSON.stringify(layout, (k, v) => (k.startsWith("_") || k === "scancodes" ? undefined : v)));

  function exportLayout() {
    if (!layout) return;
    const clean = cleaned();
    delete clean.custom;
    // The note on generated layouts is about this repository, not the shared file.
    if (/^Generated by tools\//.test(clean.notes || "")) delete clean.notes;
    const name = ($("saveName").value.trim().toLowerCase() || "layout").replace(/^my-/, "");
    const a = make("a", { href: URL.createObjectURL(new Blob([JSON.stringify(clean, null, 2) + "\n"], { type: "application/json" })), download: `${name}.json` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    status(`Exported as ${name}.json. Send that file to share the layout.`);
  }

  // A free name for an imported layout: the file's own, with -2, -3... if it's taken.
  function freeName(base) {
    const taken = new Set(info.layouts.map((l) => l.file.replace(/^layouts\//, "").replace(/\.json$/, "")));
    base = String(base).toLowerCase().replace(/\.json$/, "").replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 36) || "imported";
    let name = base;
    for (let n = 2; taken.has(name); n++) name = `${base}-${n}`;
    return name;
  }

  function importLayout(picked) {
    if (!picked) return;
    if (dirty && !confirm("Discard your unsaved changes?")) return;
    if (picked.size > 4e6) { status("That file is too big to be a layout.", true); return; }
    picked.text().then((text) => {
      let data;
      try { data = JSON.parse(text); } catch { status("That file isn't a layout (it isn't JSON).", true); return; }
      if (!data || typeof data !== "object" || !Array.isArray(data.canvas) || !data.panels) {
        status("That file isn't a layout this editor can open (it needs a HUD-style layout).", true);
        return;
      }
      delete data.custom;
      file = "";
      $("layoutSelect").value = ""; // not one of the saved layouts yet
      layout = data;
      undoStack.length = redoStack.length = 0;
      selectedId = null;
      $("saveName").value = freeName(picked.name);
      $("useIt").checked = false;
      $("useLabel").textContent = kindOf(layout) === "phone" ? "Use on phones" : "Use on tablets";
      document.title = `${layout.name || picked.name} - Tablet Deck layout editor`;
      fitStage();
      $("canvas").innerHTML = "";
      deck = window.renderCanvas(layout, D);
      renderProps();
      dirty = false;
      markDirty();
      // An imported layout presses whatever keys it says; look before saving.
      status(`Opened ${picked.name}, not saved yet. Check its buttons and keys, then Save to add it to your layouts.`);
    }).catch((err) => status(`Couldn't read that file: ${err}`, true));
  }

  function save() {
    if (!layout) return;
    const name = $("saveName").value.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(name)) { status("Use letters, numbers and dashes for the name.", true); $("saveName").focus(); return; }
    const clean = cleaned();
    clean.name = layout.custom || !layout.name ? layout.name : `${layout.name} (mine)`;
    fetch(api("save"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, layout: clean, use: $("useIt").checked }) })
      .then((r) => r.json().then((data) => (r.ok ? data : Promise.reject(data.error))))
      .then((data) => {
        dirty = false;
        layout.custom = true;
        layout.name = clean.name;
        status(data.active ? `Saved as ${data.file}. ${kindOf(layout) === "phone" ? "Phones" : "Tablets"} now use it and reload by themselves.` : `Saved as ${data.file}.`);
        return loadInfo().then((i) => { info = i; file = data.file; fillLayoutList(file); });
      })
      .catch((err) => status(`Not saved: ${err}`, true));
  }

  $("layoutSelect").addEventListener("change", (e) => open(e.target.value));
  $("addBtn").onclick = add;
  $("dupBtn").onclick = duplicate;
  $("delBtn").onclick = remove;
  $("undoBtn").onclick = undo;
  $("redoBtn").onclick = redo;
  $("saveBtn").onclick = save;
  $("exportBtn").onclick = exportLayout;
  $("importBtn").onclick = () => $("importFile").click();
  $("importFile").addEventListener("change", (e) => { importLayout(e.target.files[0]); e.target.value = ""; });

  loadInfo()
    .then((data) => {
      info = data;
      const wanted = new URLSearchParams(location.search).get("file");
      const start = (wanted && info.layouts.find((l) => l.file === wanted && l.canvas))
        || info.layouts.find((l) => l.active === "tablet" && l.canvas) || info.layouts.find((l) => l.canvas);
      if (!start) { status("No layouts to edit.", true); return; }
      fillLayoutList(start.file);
      open(start.file);
    })
    .catch((err) => {
      $("props").innerHTML = `<div class="help"><h2>Can't open the editor</h2><p></p></div>`;
      $("props").querySelector("p").textContent = String(err || "The PC didn't answer.");
    });
})();
