(() => {
  // Emblems are original badge designs on a 64x64 grid that suggest each maker's
  // character; they are not the manufacturers' real logos.
  // `F` = solid accent fill, `T` = group drawn in the theme's text colour.
  const F = `fill="currentColor" stroke="none"`;
  const T = (body) => `<g style="color:var(--text)">${body}</g>`;
  const THEMES = [
    { id: "rsi", name: "RSI", full: "Roberts Space Industries", tag: "Classic interstellar lines",
      // Orbital ascent: a ship rising through a planetary ring.
      emblem: `<circle cx="32" cy="32" r="27"/><circle cx="32" cy="32" r="20" opacity=".3"/>
        <path d="M32 8 40 34 32 28 24 34Z" ${F}/>
        <path d="M5 41c12-9 36-17 54-15" stroke-width="3.5"/>
        ${T(`<path d="M21 47h22"/><path d="M25 53h14" opacity=".6"/>`)}` },
    { id: "drake", name: "Drake", full: "Drake Interplanetary", tag: "Built rough, flown hard",
      // Talon: three claw slashes across a heavy hex nut.
      emblem: `<path d="M32 4 56 18v28L32 60 8 46V18Z" stroke-width="3.5"/>
        <path d="M32 12 49 22v20L32 52 15 42V22Z" opacity=".3"/>
        <path d="M19 15c9 8 12 18 10 33l-5-3c1-11-1-20-5-30Z" ${F}/>
        <path d="M29 13c9 9 12 19 10 35l-5-3c1-12-1-22-5-32Z" ${F}/>
        <path d="M40 17c6 7 9 16 7 27l-4-3c0-9-1-16-3-24Z" ${F} opacity=".75"/>` },
    { id: "aegis", name: "Aegis", full: "Aegis Dynamics", tag: "Military precision",
      // Winged spear: a central blade with swept delta wings.
      emblem: `<path d="M27 24 4 40h23Z" ${F} opacity=".55"/><path d="M37 24l23 16H37Z" ${F} opacity=".55"/>
        <path d="M27 31 12 44h15M37 31l15 13H37"/>
        <path d="M32 3 38 30 32 54 26 30Z" ${F}/>
        ${T(`<path d="M21 50h22M27 59h10"/>`)}` },
    { id: "anvil", name: "Anvil", full: "Anvil Aerospace", tag: "Frontline hardware",
      // Bulwark: a heavy shield with a command band and rank chevrons.
      emblem: `<path d="M32 4 56 12v18c0 14-10 24-24 30C18 54 8 44 8 30V12Z" stroke-width="3.5"/>
        <path d="M32 10 50 16v14c0 11-8 19-18 24-10-5-18-13-18-24V16Z" opacity=".3"/>
        <path d="M15 17h34v6H15Z" ${F}/>
        ${T(`<path d="M20 30l12 8 12-8M20 40l12 8 12-8" stroke-width="3.5"/>`)}` },
    { id: "origin", name: "Origin", full: "Origin Jumpworks", tag: "Luxury in the void",
      // Halo: a fine ring, a crescent and a single axis line.
      emblem: `<circle cx="32" cy="32" r="26" stroke-width="1.6"/>
        <path d="M40 10a22 22 0 1 0 0 44a30 30 0 0 1 0-44Z" ${F}/>
        <circle cx="45" cy="21" r="2.2" ${F}/>
        ${T(`<path d="M32 1v9M32 54v9" stroke-width="1.6"/>`)}` },
    { id: "misc", name: "MISC", full: "Musashi Industrial & Starflight", tag: "Industrial workhorses",
      // Cargo hex: a horizontal hexagon holding stacked cargo modules.
      emblem: `<path d="M18 8h28l14 24-14 24H18L4 32Z" stroke-width="3"/>
        <rect x="19" y="19" width="26" height="6" rx="1" ${F}/>
        <rect x="14" y="29" width="36" height="6" rx="1" ${F}/>
        <rect x="19" y="39" width="26" height="6" rx="1" ${F} opacity=".6"/>
        ${T(`<path d="M8 32h3M53 32h3"/>`)}` },
    { id: "crusader", name: "Crusader", full: "Crusader Industries", tag: "Open skies, big haulers",
      // Skysail: two wind-filled sails above a horizon.
      emblem: `<circle cx="32" cy="32" r="27"/>
        <path d="M19 46C23 28 35 16 51 12c-6 10-9 22-9 34Z" ${F}/>
        <path d="M13 46c3-10 8-17 15-22-2 7-3 14-3 22Z" ${F} opacity=".45"/>
        ${T(`<path d="M8 46h48"/><path d="M15 52h34" opacity=".4"/>`)}` },
    { id: "banu", name: "Banu", full: "Banu Souli", tag: "Alien craft, merchant spirit",
      // Bloom sigil: four petals and four buds around a seed, in a dotted ring.
      emblem: `<circle cx="32" cy="32" r="28" stroke-dasharray="1 5"/>
        ${[0, 90, 180, 270].map((r) => `<path d="M32 5c7 8 7 15 0 21-7-6-7-13 0-21Z" ${F} transform="rotate(${r} 32 32)"/>`).join("")}
        ${[45, 135, 225, 315].map((r) => `<path d="M32 12c4 5 4 9 0 13-4-4-4-8 0-13Z" ${F} opacity=".5" transform="rotate(${r} 32 32)"/>`).join("")}
        ${T(`<circle cx="32" cy="32" r="4.5"/>`)}` },
    { id: "aopoa", name: "Aopoa", full: "Aopoa", tag: "Xi'an elegance",
      // Twin crescents: two mirrored, flowing blades.
      emblem: `<path d="M12 52C9 31 22 13 45 8 32 18 27 31 29 52Z" ${F}/>
        <path d="M52 12c3 21-10 38-33 44 13-10 18-24 16-44Z" ${F} opacity=".5"/>
        ${T(`<circle cx="32" cy="32" r="2.6" fill="currentColor" stroke="none"/>`)}` },
    { id: "argo", name: "Argo", full: "Argo Astronautics", tag: "Utility first",
      // Liftframe: a load-bearing triangle with a crane hook and rivets.
      emblem: `<path d="M32 5 59 55H5Z" stroke-width="3.5"/><path d="M32 19 47 47H17Z" opacity=".3"/>
        <circle cx="14" cy="50" r="2.4" ${F}/><circle cx="50" cy="50" r="2.4" ${F}/>
        ${T(`<path d="M32 15v15a5 5 0 1 1-5 5" stroke-width="3.5"/>`)}` },
    { id: "cnou", name: "CO", full: "Consolidated Outland", tag: "Frontier newcomer",
      // Frontier sunrise: a bold open ring around a rising sun.
      emblem: `<path d="M48 14a24 24 0 1 0 0 36" stroke-width="6"/>
        <path d="M33 41a9 9 0 0 1 18 0Z" ${F}/>
        ${T(`<path d="M29 41h30"/><path d="M42 26v-4M32 30l-3-3M52 30l3-3" opacity=".7"/>`)}` },
    { id: "esperia", name: "Esperia", full: "Esperia", tag: "Reborn Tevarin designs",
      // Tevarin crescent: a sweeping blade under a four-point star.
      emblem: `<path d="M8 32A24 24 0 0 1 32 8" opacity=".4"/>
        <path d="M10 52C14 27 30 10 55 8 39 16 29 30 27 54Z" ${F}/>
        ${T(`<path d="M45 30l2.5 8 8 2.5-8 2.5-2.5 8-2.5-8-8-2.5 8-2.5Z" fill="currentColor" stroke="none"/>`)}` },
  ];
  const themeById = Object.fromEntries(THEMES.map((t) => [t.id, t]));
  const svg = (body) => `<svg viewBox="0 0 48 48" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
  const emblemSvg = (body) => `<svg viewBox="0 0 64 64" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };

  const $ = (id) => document.getElementById(id);
  const grid = $("grid");
  const tabs = $("tabs");
  let token = (new URLSearchParams(location.search).get("t") || store.get("token") || "").trim().toLowerCase();
  // Phones (short side under 500pt, e.g. iPhone 15 Pro at 393pt) get the phone layout;
  // ?device=tablet or ?device=phone in the address overrides the guess.
  const device = new URLSearchParams(location.search).get("device")
    || (Math.min(screen.width, screen.height) < 500 ? "phone" : "tablet");
  document.documentElement.dataset.device = device;
  // Demo mode (?demo, or the website's "Try it" page): no PC at all. Layouts come from the
  // layouts/ folder next to the page, and presses only play their animations.
  const DEMO = new URLSearchParams(location.search).has("demo") || window.DECK_DEMO === true;
  const DEMO_LAYOUTS = [
    [device === "phone" ? "layouts/hud-phone.json" : "layouts/hud.json", "Tablet Deck HUD"],
    ...[["combat", "Combat"], ["mining-salvage", "Mining & Salvage"], ["hauling-exploration", "Hauling & Exploration"],
      ["prospector", "Prospector"], ["vulture", "Vulture"]]
      .map(([id, name]) => [`layouts/${id}${device === "phone" ? "-phone" : ""}.json`, name]),
    ["layouts/star-citizen.json", "Star Citizen (tabbed grid)"],
  ];
  // ?safe=top,right,bottom,left (CSS px) stands in for the screen's safe-area insets. Pages
  // in an iframe always see zero insets, so the showcase frame passes the phone's here.
  const safe = (new URLSearchParams(location.search).get("safe") || "").split(",").map(Number);
  if (safe.length === 4 && safe.every((n) => n >= 0)) {
    ["top", "right", "bottom", "left"].forEach((side, i) => document.documentElement.style.setProperty(`--safe-${side}`, `${safe[i]}px`));
  }
  // Shown text in the deck's language (i18n.js); layouts, keys and voice phrases stay English.
  const tr = window.DeckI18n.t;
  let layout = null;
  let ws = null;
  let pageIndex = Number(store.get("page") || 0);

  // ---------- theme ----------

  function applyTheme(id) {
    const theme = themeById[id] || THEMES[0];
    document.documentElement.dataset.theme = theme.id;
    document.documentElement.dataset.maker = theme.full; // shown on the intro screen
    for (const m of document.querySelectorAll("#brandMark, .js-emblem")) m.innerHTML = emblemSvg(theme.emblem);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    store.set("theme", theme.id);
    for (const card of document.querySelectorAll(".card")) card.classList.toggle("current", card.dataset.theme === theme.id);
  }

  function buildPicker() {
    const cards = $("cards");
    cards.innerHTML = "";
    for (const t of THEMES) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "card";
      card.dataset.theme = t.id;
      card.innerHTML = `
        <div class="top"><span class="emblem">${emblemSvg(t.emblem)}</span>
          <div><div class="name"></div><div class="tag"></div></div></div>
        <div class="key sample" data-color="">
          <span class="glyph">${window.deckIcon("quantum-mode")}</span><span class="label">${tr("Quantum Mode")}</span>
        </div>
        <div class="swatches"><i style="background:var(--accent)"></i><i style="background:var(--green)"></i><i style="background:var(--amber)"></i><i style="background:var(--red)"></i><i style="background:var(--blue)"></i></div>`;
      card.querySelector(".name").textContent = t.name;
      card.querySelector(".tag").textContent = `${t.full} · ${tr(t.tag)}`;
      card.onclick = () => { applyTheme(t.id); closePicker(); };
      cards.appendChild(card);
    }
  }
  const openPicker = () => { $("picker").classList.add("open"); applyTheme(document.documentElement.dataset.theme); fillLayoutPick(); fillLinks(); fillLightSwitches(); };

  // "Open on PC": websites set up on the PC (Settings → Links), opened in its browser. The
  // tablet only gets their names and asks by position, so it can't open anything else.
  function fillLinks() {
    const names = DEMO ? Promise.resolve(["UEX", "Erkul", "SPViewer", "SCMDB"])
      : fetch(`/links.json?t=${encodeURIComponent(token)}`).then((r) => (r.ok ? r.json() : Promise.reject())).then((d) => d.links);
    names.then((list) => {
      const box = $("pcLinkBtns");
      box.innerHTML = "";
      list.forEach((name, index) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = name;
        b.onclick = () => {
          if (DEMO) { toast(tr("With the app, this opens {0} on your PC", name), 2600); return; }
          buzz();
          send({ t: "open", index });
        };
        box.appendChild(b);
      });
      $("pcLinks").hidden = !list.length;
    }).catch(() => { $("pcLinks").hidden = true; });
  }
  const closePicker = () => $("picker").classList.remove("open");
  // The same sliders icon as the HUD layouts' settings button, so it reads as the way to
  // other layouts too, not just a theme switcher.
  $("settingsIco").innerHTML = window.deckIcon("sliders");
  $("themeBtn").onclick = openPicker;
  $("closePicker").onclick = closePicker;

  // ---------- layout choice ----------

  // Each device can pick its own layout (e.g. Combat on one tablet, Mining on another);
  // "" means the one chosen on the PC. Kept per device type, since phones have their own.
  const layoutKey = `layout-${device}`;
  let chosenLayout = store.get(layoutKey) || "";
  function fillLayoutPick() {
    if (DEMO) {
      const pick = $("layoutPick");
      pick.innerHTML = "";
      for (const [file, name] of DEMO_LAYOUTS) { const o = document.createElement("option"); o.value = file; o.textContent = tr(name); pick.appendChild(o); }
      pick.value = DEMO_LAYOUTS.some(([f]) => f === chosenLayout) ? chosenLayout : DEMO_LAYOUTS[0][0];
      return;
    }
    fetch(`/layouts.json?t=${encodeURIComponent(token)}&device=${device}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        const pick = $("layoutPick");
        pick.innerHTML = "";
        const pcName = (data.layouts.find((l) => l.file === data.pcChoice) || {}).name || "default";
        const add = (value, text) => { const o = document.createElement("option"); o.value = value; o.textContent = text; pick.appendChild(o); };
        add("", tr("PC's choice: {0}", tr(pcName)));
        for (const l of data.layouts) add(l.file, l.custom ? tr("{0} (yours)", l.name) : tr(l.name));
        pick.value = data.layouts.some((l) => l.file === chosenLayout) ? chosenLayout : "";
      })
      .catch(() => {});
  }
  $("layoutPick").onchange = (e) => {
    closePicker();
    useLayout(e.target.value);
  };

  // ---------- language ----------

  // Kept per device, like the layout. Switching redraws everything in place, without the
  // reload that would replay the intro.
  const langPick = $("langPick");
  for (const [code, name] of window.DeckI18n.languages) {
    const o = document.createElement("option");
    o.value = code;
    o.textContent = name;
    langPick.appendChild(o);
  }
  langPick.value = window.DeckI18n.lang;
  langPick.onchange = () => {
    window.DeckI18n.set(langPick.value);
    window.DeckI18n.apply();
    buildPicker();
    applyTheme(document.documentElement.dataset.theme);
    fillLayoutPick();
    refreshStatus();
    if (document.body.classList.contains("pairing")) showPairing(false);
    else if (layout) showLayout(layout);
  };

  // ---------- two-finger swipe between panels ----------

  // Swiping sideways with two fingers over one half of the HUD flips that half to its next
  // or previous panel, like its arrows (on tabbed layouts, to the next or previous tab).
  // The first finger usually lands on a button, so while swiping is on, a press waits
  // SWIPE_WAIT_MS; if a second finger lands meanwhile, it's a swipe and nothing is
  // pressed. Off unless turned on in the sliders sheet, so presses are instant by default.
  const SWIPE_WAIT_MS = 60;
  const swipeKey = `swipe-${device}`;
  const swipeOn = () => store.get(swipeKey) === "on";
  const pendingPresses = new Set(); // cancel functions of presses still waiting
  const fingers = new Map();        // pointerId -> { x, y } for fingers now down
  let swipe = null;                 // { startX, startY, x, y } while two fingers are down

  function centre() {
    let x = 0, y = 0;
    for (const f of fingers.values()) { x += f.x; y += f.y; }
    return { x: x / fingers.size, y: y / fingers.size };
  }
  document.addEventListener("pointerdown", (e) => {
    if (!swipeOn() || e.pointerType === "mouse") return;
    // The first finger of a new touch: forget any finger whose lift was missed, so a stale
    // one can't turn every later tap into a "second finger" and swallow it.
    if (e.isPrimary) { fingers.clear(); swipe = null; }
    fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (fingers.size === 2) {
      for (const cancel of [...pendingPresses]) cancel();
      pendingPresses.clear();
      const c = centre();
      swipe = { startX: c.x, startY: c.y, x: c.x, y: c.y };
    }
  }, true);
  document.addEventListener("pointermove", (e) => {
    if (!fingers.has(e.pointerId)) return;
    fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (swipe && fingers.size >= 2) Object.assign(swipe, centre());
  }, true);
  const lift = (e) => {
    if (!fingers.delete(e.pointerId) || !swipe || fingers.size > 0) return;
    const s = swipe;
    swipe = null;
    const dx = s.x - s.startX, dy = s.y - s.startY;
    if (Math.abs(dx) < 90 || Math.abs(dy) > Math.abs(dx) * 0.6) return;
    const step = dx < 0 ? 1 : -1; // swipe left: next panel, as on a phone
    if (deckCanvas) deckCanvas.pageAt(s.startX, s.startY, step);
    else if (layout && layout.pages) {
      pageIndex = (pageIndex + step + layout.pages.length) % layout.pages.length;
      store.set("page", pageIndex);
      renderTabs();
      renderPage();
    }
  };
  document.addEventListener("pointerup", lift, true);
  document.addEventListener("pointercancel", lift, true);

  function applySwipe(on) {
    store.set(swipeKey, on ? "on" : "off");
    $("swipeBtn").setAttribute("aria-checked", String(on));
  }
  $("swipeBtn").onclick = () => applySwipe(!swipeOn());
  $("swipeBtn").setAttribute("aria-checked", String(swipeOn()));

  // Night mode dims the whole deck for playing in a dark room; each device remembers it.
  function applyNight(on) {
    document.documentElement.toggleAttribute("data-night", on);
    $("nightBtn").setAttribute("aria-checked", String(on));
    store.set("night", on ? "1" : "");
  }
  $("nightBtn").onclick = () => applyNight(!document.documentElement.hasAttribute("data-night"));
  $("resetStatesBtn").onclick = resetStates;
  applyNight(store.get("night") === "1");

  // Large text and high contrast, for reading the deck at a glance; each device remembers them.
  for (const [btn, attr] of [["largeBtn", "data-large"], ["contrastBtn", "data-contrast"]]) {
    const apply = (on) => {
      document.documentElement.toggleAttribute(attr, on);
      $(btn).setAttribute("aria-checked", String(on));
      store.set(attr, on ? "1" : "");
    };
    $(btn).onclick = () => apply(!document.documentElement.hasAttribute(attr));
    apply(store.get(attr) === "1");
  }

  // Button lights: on unless turned off on this device. Kept as "data-no-lights" so the
  // default (no attribute) is lit.
  function applyLights(on, refresh) {
    document.documentElement.toggleAttribute("data-no-lights", !on);
    $("lightsBtn").setAttribute("aria-checked", String(on));
    store.set("lights", on ? "" : "off");
    if (refresh) showStates(); // not at start: the buttons and their states come later
  }
  $("lightsBtn").onclick = () => applyLights(!lightsOn(), true);
  applyLights(store.get("lights") !== "off", false);

  // ---------- connection ----------

  // Status shows in the header (grid layouts) and in canvas chrome, so update every copy.
  let online = { on: false, text: "Connecting…" };
  const navLink = $("nav-deck-link");
  function setOnline(on, text) {
    online = { on, text };
    window.NavDesk.online(on);
    refreshStatus();
  }
  function refreshStatus() {
    document.body.classList.toggle("offline", !online.on);
    for (const d of document.querySelectorAll("#dot, .js-dot")) d.classList.toggle("on", online.on);
    for (const t of document.querySelectorAll("#statusText, .js-status")) t.textContent = tr(online.text);
    const statusBox = document.querySelector(".hud-status") || $("status");
    if (navLink && statusBox) {
      statusBox.appendChild(navLink); // retain the link across canvas redraws
      const canvas = statusBox.closest("#canvas");
      const scale = canvas ? canvas.getBoundingClientRect().width / parseFloat(canvas.style.width) : 1;
      // Desk content counter-scales for touch targets; section headers still
      // match the surrounding deck's rendered type size, including its sheet.
      document.documentElement.style.setProperty("--nd-header-scale", scale);
      if (!canvas) {
        statusBox.style.position = "relative";
        statusBox.style.paddingBottom = "46px"; // reserve the target in grid header flow
      }
      const r = statusBox.getBoundingClientRect();
      const label = statusBox.querySelector(".js-status, #statusText").getBoundingClientRect();
      // Follow the rendered label, including theme fonts and large-text changes.
      const left = label.left, top = label.bottom + 2;
      const controls = [...document.querySelectorAll(canvas ? "#canvas > button" : "header > button")]
        .map(e => e.getBoundingClientRect())
        .filter(b => b.width && b.left >= left && b.top < top + 44 && b.bottom > top);
      const end = Math.min(canvas ? canvas.getBoundingClientRect().right : innerWidth,
        ...controls.map(b => b.left)) - 8;
      navLink.style.position = "absolute";
      navLink.style.left = ((left - r.left) / scale) + "px";
      navLink.style.top = ((top - r.top) / scale) + "px";
      navLink.style.height = (44.1 / scale) + "px";
      // Native buttons centre their anonymous text box: use bottom-only padding
      // so the visible micro-label starts at the measured top of the hit area.
      navLink.style.paddingBottom = Math.max(0, 44.1 / scale - parseFloat(getComputedStyle(navLink).lineHeight)) + "px";
      navLink.style.width = ((end - left) / scale) + "px";
    }
  }

  // Header metrics can change without a layout rebuild (theme, accessibility, fonts).
  new MutationObserver(refreshStatus).observe(document.documentElement, {
    attributes: true, attributeFilter: ["data-theme", "data-large"],
  });
  window.addEventListener("resize", refreshStatus);
  document.fonts?.addEventListener("loadingdone", refreshStatus);

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  // Short notice at the bottom of the screen, e.g. when the PC skipped a press.
  let toastTimer = null;
  function toast(text, ms = 1800) {
    const el = $("toast");
    el.textContent = tr(text);
    // On canvas layouts, sit just above the pager bars so a message never covers the page names.
    const pager = document.querySelector("#canvas .hud-pager");
    el.style.bottom = pager ? `${Math.round(innerHeight - pager.getBoundingClientRect().top + 10)}px` : "";
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), ms);
  }

  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws?t=${encodeURIComponent(token)}&device=${device}&layout=${encodeURIComponent(chosenLayout)}`);
    ws.onopen = () => setOnline(true, "Connected");
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.t === "blocked") { toast("Star Citizen not focused"); buzz([30, 60, 30]); }
      else if (msg.t === "hello") { applyVoiceStatus(msg.voiceStatus || { state: msg.voice ? "ready" : "off" }); states = msg.states || {}; showStates(); }
      else if (msg.t === "state") { states[msg.group] = msg.on; showStates(msg.group); }
      else if (msg.t === "not-set-up") { toast(tr("Not set up") + ". " + tr("Enable deck setup in the skill settings.")); }
      else if (msg.t === "no-key") { toast(tr("No key set for {0}", msg.label)); }
      else if (msg.t === "states") { states = msg.states || {}; showStates(); }
      else if (msg.t === "voice-status") applyVoiceStatus(msg);
      else if (msg.t === "heard") toast(tr("Heard: {0}", msg.text || tr(msg.label)));
      else if (msg.t === "unheard") toast("Didn't catch that");
      else if (msg.t === "voice-off") toast(msg.reason);
      // Push-to-talk held on the PC: the mic buttons light up as if held here.
      else if (msg.t === "listening") for (const el of document.querySelectorAll(".js-talk")) el.classList.toggle("listening", msg.on);
      else if (msg.t === "reload") load().then(reconnect); // the layout was changed in the editor
      else if (msg.t === "nav") window.NavDesk.update(msg.state || {});
      else if (msg.t === "opened" && msg.url !== "/nav") toast(tr(msg.dryRun ? "{0}: test mode, not opened" : "Opened {0} on the PC", msg.name));
    };
    ws.onclose = () => { setOnline(false, "Reconnecting…"); setTimeout(connect, 1000); };
    ws.onerror = () => ws.close();
  }
  setInterval(() => send({ t: "hb" }), 1500);

  // ---------- buttons ----------

  function renderTabs() {
    tabs.innerHTML = "";
    layout.pages.forEach((p, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = tr(p.name);
      b.className = i === pageIndex ? "active" : "";
      b.onclick = () => { pageIndex = i; store.set("page", i); renderTabs(); renderPage(); };
      tabs.appendChild(b);
    });
  }

  const modeLabel = { tap: "", hold: "Hold", timed: "Long press" };
  const keyLabel = (keys) => keys.split("+").map((k) => k.replace(/^np_/, "num ").replace("period", ".")).join(" + ");

  function renderPage() {
    grid.innerHTML = "";
    if (layout.pages[pageIndex].navdesk) { window.NavDesk.mount(grid, layout.pages[pageIndex].navdesk); return; }
    for (const btn of layout.pages[pageIndex].buttons) {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "key";
      el.dataset.color = btn.color || "";
      el.innerHTML = `<span class="glyph">${window.deckIcon(btn.icon || btn.id)}</span>
        <span class="label"></span>
        <span class="foot"><span class="mode"></span><span class="keycap"></span></span>`;
      el.querySelector(".label").textContent = tr(btn.label);
      el.querySelector(".keycap").textContent = keyLabel(btn.keys);
      el.querySelector(".mode").textContent = tr(modeLabel[btn.mode] || "");
      bindButton(el, btn);
      grid.appendChild(el);
    }
  }

  const buzz = (ms = 12) => { if (navigator.vibrate) navigator.vibrate(ms); };

  // ---------- lit buttons ----------

  // On/off state per group (doors open, gear down), kept by the PC so every tablet and
  // voice command agree. Buttons with a "state" light up while their group is on.
  let states = {};
  // With Button lights off (Deck settings), everything shows as off and stays that way.
  const lightsOn = () => !document.documentElement.hasAttribute("data-no-lights");
  const groupLabels = {"door-lock":"Door locks",doors:"Doors",esp:"ESP","flight-ready":"Flight ready",
    "g-safety":"G-force safety",gear:"Landing gear",lights:"Headlights",power:"Main power",
    "shield-power":"Shields","speed-limiter":"Speed limiter","thruster-power":"Engines",vtol:"VTOL","weapons-power":"Weapons"};
  function fillLightSwitches() {
    const groups = new Set();
    function scan(value) {
      if (!value || typeof value !== "object") return;
      if (value.state?.group) groups.add(value.state.group);
      Object.values(value).forEach(scan);
    }
    scan(layout);
    $("fixLightSwitches").replaceChildren();
    for (const group of [...groups].sort()) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "sheet-switch fix-light";
      button.dataset.lightGroup = group; button.setAttribute("role", "switch");
      const text = document.createElement("span"); text.className = "sw-text";
      const label = document.createElement("b"); label.textContent = tr(groupLabels[group] || group);
      const status = document.createElement("span"); status.className = "fix-light-state";
      const knob = document.createElement("span"); knob.className = "sw-knob"; knob.setAttribute("aria-hidden", "true");
      text.append(label, status); button.append(text, knob);
      button.onclick = () => {
        const on = !Boolean(states[group]);
        if (DEMO) { states[group] = on; showStates(group); }
        else send({t:"set-state", group, on});
      };
      $("fixLightSwitches").append(button);
    }
    showStates();
  }
  function showStates(group) {
    for (const button of document.querySelectorAll("[data-light-group]")) {
      const on = Boolean(states[button.dataset.lightGroup]);
      button.setAttribute("aria-checked", String(on));
      button.querySelector(".fix-light-state").textContent = tr(on ? "ON" : "OFF");
    }
    for (const el of document.querySelectorAll("[data-state-group]")) {
      if (group === undefined || el.dataset.stateGroup === group) el._showState(lightsOn() && Boolean(states[el.dataset.stateGroup]), group !== undefined && lightsOn());
    }
  }
  // Demo mode has no PC, so presses change the state here, the way server.js does.
  function demoState(btn) {
    const s = btn.state;
    states[s.group] = typeof s.set === "boolean" ? s.set : !states[s.group];
    showStates(s.group);
  }
  function resetStates() {
    if (DEMO) { states = {}; showStates(); } else send({ t: "reset-states" });
    toast("Button lights reset");
  }

  function bindButton(el, btn) {
    const fx = window.DeckFX.attach(el, btn);
    if (btn.not_set_up || btn.no_key) {
      const unavailable = tr(btn.not_set_up ? "Not set up" : "No key set");
      el.classList.toggle("not-set-up", Boolean(btn.not_set_up));
      el.classList.add("no-key"); el.setAttribute("aria-description", unavailable);
      const note = document.createElement("span"); note.className = "no-key-note";
      note.textContent = unavailable; (el.querySelector(".hud-lbl, .label") || el).append(note);
    }
    if (btn.state && typeof btn.state.group === "string") {
      el.dataset.stateGroup = btn.state.group;
      el._showState = fx.show;
      fx.show(lightsOn() && Boolean(states[btn.state.group]), false);
    }
    let pointer = null;
    let waiting = null; // a press held back in case a second finger starts a swipe
    const press = (e) => {
      waiting = null;
      el.classList.add("pressed");
      buzz();
      send({ t: btn.mode === "hold" ? "down" : "tap", id: btn.id });
      fx.down(e);
      if (DEMO && el.dataset.stateGroup) demoState(btn);
    };
    el.addEventListener("pointerdown", (e) => {
      if (pointer !== null || swipe) return; // fingers that are part of a swipe press nothing
      pointer = e.pointerId;
      el.setPointerCapture(e.pointerId);
      if (!swipeOn()) { press(e); return; }
      const cancel = () => { clearTimeout(waiting.timer); waiting = null; pointer = null; };
      waiting = { cancel, timer: setTimeout(() => { pendingPresses.delete(cancel); press(e); }, SWIPE_WAIT_MS) };
      pendingPresses.add(cancel);
    });
    const end = (e) => {
      if (e.pointerId !== pointer) return;
      if (waiting) {
        // Lifted before the wait was over: still a press, just a quick one. A touch the
        // browser cancelled presses nothing.
        const { cancel } = waiting;
        cancel();
        pendingPresses.delete(cancel);
        if (e.type === "pointercancel") return;
        pointer = e.pointerId;
        press(e);
      }
      pointer = null;
      el.classList.remove("pressed");
      if (btn.mode === "hold") send({ t: "up", id: btn.id });
      fx.up();
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  // Hold-to-talk: the PC listens for a voice command while this is held down.
  function bindTalk(el) {
    let pointer = null;
    el.addEventListener("pointerdown", (e) => {
      if (pointer !== null) return;
      pointer = e.pointerId;
      el.setPointerCapture(e.pointerId);
      el.classList.add("listening");
      buzz();
      if (DEMO) toast("Voice commands work with Tablet Deck on your PC", 2600);
      send({ t: "voice", on: true });
    });
    const end = (e) => {
      if (e.pointerId !== pointer) return;
      pointer = null;
      el.classList.remove("listening");
      send({ t: "voice", on: false });
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("contextmenu", (e) => e.preventDefault());
  }
  bindTalk($("talk"));

  // ---------- voice commands list ----------

  // The mic and "?" only show when voice can work: on, and starting or ready on the PC.
  let voiceStatus = { state: "starting" };
  function applyVoiceStatus(status) {
    voiceStatus = status;
    document.body.classList.toggle("no-voice", status.state === "off" || status.state === "unavailable");
    if ($("voiceHelp").classList.contains("open")) loadVoiceList();
  }

  // What can be said and which button it presses, from the PC (the same list the PC listens for).
  let voiceData = null;
  function openVoiceHelp() {
    $("voiceHelp").classList.add("open");
    $("voiceSearch").value = "";
    voiceNote("Loading…");
    loadVoiceList();
  }
  function loadVoiceList() {
    if (DEMO) { voiceData = { on: true, state: "ready", commands: phrasesInLayout() }; renderVoiceList(); return; }
    fetch(`/voice.json?t=${encodeURIComponent(token)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => { voiceData = data; renderVoiceList(); })
      .catch(() => voiceNote("Couldn't load the list from the PC."));
  }
  const closeVoiceHelp = () => $("voiceHelp").classList.remove("open");

  // Demo mode has no PC to ask, so the list comes from the layout, the way server.js
  // builds it: each button's label as said aloud ("Shields +" is "shields up") plus its
  // "voice" phrases, leaving out buttons with "voice": false.
  const spoken = (label) => String(label).toLowerCase()
    .replace(/\s*\+$/, " up").replace(/\s*[−-]$/, " down").replace(/&/g, " and ")
    .replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim();
  function phrasesInLayout() {
    const seen = new Map();
    (function walk(n) {
      if (Array.isArray(n)) { n.forEach(walk); return; }
      if (!n || typeof n !== "object") return;
      if (typeof n.id === "string" && typeof n.keys === "string" && n.voice !== false) {
        for (const p of [n.label, ...(Array.isArray(n.voice) ? n.voice : [])].map(spoken)) if (p && !seen.has(p)) seen.set(p, n.label);
      }
      for (const v of Object.values(n)) if (v && typeof v === "object") walk(v);
    })(layout);
    return [...seen].sort((a, b) => a[1].localeCompare(b[1]) || a[0].localeCompare(b[0]));
  }

  function voiceNote(text) {
    const list = $("voiceList");
    list.innerHTML = `<p class="vh-note"></p>`;
    list.querySelector("p").textContent = tr(text);
  }

  // One card per button, with its phrases; the search matches phrases and button names.
  function renderVoiceList() {
    if (!voiceData) return;
    // The live status from the PC wins over the one the list was loaded with.
    const state = DEMO ? "ready" : voiceStatus.state;
    const reason = voiceStatus.reason || voiceData.reason;
    if (!voiceData.on || state === "off") { voiceNote("Voice commands are turned off on the PC. Turn them on in the Tablet Deck window's Settings."); return; }
    if (state === "unavailable") {
      voiceNote(tr("Voice commands can't run on this PC{0}. The Tablet Deck window shows what to do.", reason ? `: ${reason}` : ""));
      return;
    }
    const q = $("voiceSearch").value.trim().toLowerCase();
    const byLabel = new Map();
    for (const [phrase, label] of voiceData.commands) {
      if (q && !phrase.includes(q) && !label.toLowerCase().includes(q) && !tr(label).toLowerCase().includes(q)) continue;
      if (!byLabel.has(label)) byLabel.set(label, []);
      byLabel.get(label).push(phrase);
    }
    if (!byLabel.size) { voiceNote(q ? "Nothing matches that." : "The PC is still starting voice commands. Try again in a moment."); return; }
    const list = $("voiceList");
    list.innerHTML = "";
    for (const [label, phrases] of byLabel) {
      const card = document.createElement("div");
      card.className = "vh-card";
      card.innerHTML = `<div class="vh-label"></div><div class="vh-phrases"></div>`;
      card.querySelector(".vh-label").textContent = tr(label);
      for (const phrase of phrases) {
        const chip = document.createElement("span");
        chip.textContent = phrase;
        card.querySelector(".vh-phrases").appendChild(chip);
      }
      list.appendChild(card);
    }
  }
  $("voiceSearch").oninput = renderVoiceList;
  $("voiceHelpBtn").onclick = openVoiceHelp;
  $("closeVoiceHelp").onclick = closeVoiceHelp;

  // ---------- pairing + load ----------

  // Shown until the device has a working pairing code. The deck's own controls stay hidden
  // (body.pairing), since none of them work before pairing.
  function showPairing(wrong) {
    setOnline(false, "Not paired");
    document.body.classList.add("pairing");
    tabs.innerHTML = "";
    grid.innerHTML = `
      <form id="pair" novalidate>
        <div class="pair-emblem"></div>
        <h2><b>//</b> ${tr("Pair this device")}</h2>
        <p class="pair-help">${tr("Open {0} on your gaming PC and type the pairing code its window shows.", "<strong>Tablet Deck</strong>")}</p>
        <label for="code">${tr("Pairing code")}</label>
        <input id="code" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" maxlength="32"
          placeholder="${tr("e.g. 1a2b3c4d")}" aria-describedby="pairError">
        <p id="pairError" class="pair-error" role="alert" hidden></p>
        <button type="submit">${tr("Connect")}</button>
        <p class="pair-hint">${tr("Quicker: scan the QR code in the Tablet Deck window with this device's camera.")}</p>
      </form>`;
    grid.querySelector(".pair-emblem").innerHTML = emblemSvg((themeById[document.documentElement.dataset.theme] || THEMES[0]).emblem);
    const input = $("code"), error = $("pairError"), button = grid.querySelector("#pair button");
    const fail = (text) => {
      error.textContent = tr(text);
      error.hidden = false;
      input.classList.add("bad");
      input.setAttribute("aria-invalid", "true");
      buzz([30, 60, 30]);
    };
    input.value = token;
    input.oninput = () => { input.classList.remove("bad"); input.removeAttribute("aria-invalid"); error.hidden = true; };
    if (wrong === "locked") fail("Too many wrong codes. Wait a minute, then try again.");
    else if (wrong) fail("That code didn't match. Check the code in the Tablet Deck window on your PC and try again.");
    input.focus();
    $("pair").onsubmit = (e) => {
      e.preventDefault();
      token = input.value.trim().toLowerCase();
      if (!token) { fail("Type the pairing code first."); return; }
      button.disabled = true;
      button.textContent = tr("Connecting…");
      load();
    };
  }

  // Draws a layout: canvas layouts on the stage, tabbed grid layouts in the page. Also used
  // to switch layouts in place, without reloading (which would replay the intro and leave
  // full screen).
  let deckCanvas = null;
  function openNav() {
    if (deckCanvas) deckCanvas.showPanel("nav");
    else {
      const nav = layout.pages.findIndex(p => p.navdesk);
      if (nav >= 0) { pageIndex = nav; store.set("page", nav); renderTabs(); renderPage(); }
    }
  }
  function showLayout(l) {
    layout = l;
    fillLightSwitches();
    window.NavDesk.configure({ token: () => token, send, toast, reset: resetStates, openNav });
    $("title").textContent = tr(layout.name);
    document.title = tr(layout.name);
    if (layout.canvas) {
      document.body.classList.add("canvas-mode");
      deckCanvas = window.renderCanvas(layout, {
        send, bindButton, bindTalk, buzz, store, refreshStatus, openPicker, openVoiceHelp, toggleFullscreen,
        deckIcon: window.deckIcon,
        emblemHtml: () => emblemSvg((themeById[document.documentElement.dataset.theme] || THEMES[0]).emblem),
      });
    } else {
      if (deckCanvas) { deckCanvas.dispose(); deckCanvas = null; }
      document.body.classList.remove("canvas-mode");
      $("stage").hidden = true;
      $("canvas").innerHTML = "";
      if (pageIndex >= layout.pages.length) pageIndex = 0;
      renderTabs();
      renderPage();
      refreshStatus();
    }
  }

  // Fetches the chosen layout (from the PC, or the layouts/ folder in demo mode) and shows it.
  function load() {
    const demoFile = (DEMO_LAYOUTS.find(([f]) => f === chosenLayout) || DEMO_LAYOUTS[0])[0];
    return fetch(DEMO ? demoFile : `/layout.json?t=${encodeURIComponent(token)}&device=${device}&layout=${encodeURIComponent(chosenLayout)}`)
      .then((r) => {
        if (r.status === 403) return Promise.reject("pair");
        if (r.status === 429) return Promise.reject("locked");
        return r.ok ? r.json() : Promise.reject(tr("Layout error ({0})", r.status));
      })
      .then((l) => {
        const first = !layout;
        if (!DEMO) {
          store.set("token", token);
          // Strip only the saved code, after successful pairing; keep device/safe/etc.
          if (store.get("token") === token && new URLSearchParams(location.search).has("t")) {
            const url = new URL(location.href);
            url.searchParams.delete("t");
            history.replaceState(history.state, "", url);
          }
        }
        document.body.classList.remove("pairing");
        showLayout(l);
        if (DEMO) {
          setOnline(true, "Demo");
          if (first) setTimeout(() => toast("Demo: taps play their animations; with the app, they press keys in Star Citizen", 4500), 400);
        } else if (!ws) connect();
      })
      .catch((err) => {
        if (err === "pair") { showPairing(Boolean(token)); return; }
        if (err === "locked") { showPairing("locked"); return; }
        grid.innerHTML = `<div id="msg"></div>`;
        $("msg").textContent = String(err);
        setOnline(false, "Error");
      });
  }

  // A new layout means new buttons on the PC's side too, so connect again with it.
  function reconnect() {
    if (DEMO || !ws) return;
    ws.onclose = null;
    ws.close();
    ws = null;
    connect();
  }

  // Shows another layout (by file), remembered for this device.
  function useLayout(file, name) {
    chosenLayout = file;
    store.set(layoutKey, file);
    return load().then(() => { reconnect(); if (name) toast(name, 1600); });
  }

  function toggleFullscreen() {
    const d = document.documentElement;
    if (document.fullscreenElement) document.exitFullscreen();
    else (d.requestFullscreen || d.webkitRequestFullscreen)?.call(d);
  }
  $("fs").onclick = toggleFullscreen;

  window.DeckI18n.apply();
  buildPicker();
  applyTheme(store.get("theme") || "rsi");
  // The QR share link authenticates through an HttpOnly cookie, not localStorage.
  load();
})();
