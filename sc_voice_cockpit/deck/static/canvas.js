// Renders "canvas" layouts: a fixed-size HUD (e.g. 1024x768) scaled to fit the screen,
// with regions that page through panels. Element coordinates are canvas pixels;
// panel elements are relative to their region.
(() => {
  const NS = "deck-canvas";
  // Shown text goes through the deck's language (i18n.js); English when it isn't loaded.
  const tr = (s) => (window.DeckI18n ? window.DeckI18n.t(s) : s);

  let current = null; // the deck drawn last; a new one replaces it (switching layouts)

  window.renderCanvas = function renderCanvas(layout, D) {
    if (current) current.dispose();
    const [W, H] = layout.canvas;
    const stage = document.getElementById("stage");
    const canvas = document.getElementById("canvas");
    stage.hidden = false;
    // Small canvases (phone layouts) get tighter menus; wide ones ask phones to turn sideways.
    canvas.classList.toggle("compact", H < 500);
    stage.classList.toggle("landscape-only", W > H * 1.5);
    stage.querySelector(".hud-rotate")?.remove(); // its text follows the language
    stage.insertAdjacentHTML("beforeend", `<div class="hud-rotate"><span class="hud-ico">${D.deckIcon("swap")}</span>${tr("Turn your phone sideways")}</div>`);

    // Separate stores per canvas size, so phone and tablet keep their own page positions.
    const key = `${NS}-${W}x${H}`;
    const saved = (() => { try { return JSON.parse(D.store.get(key) || "{}"); } catch { return {}; } })();
    const state = { groups: saved.groups || {}, panels: saved.panels || {} };
    const save = () => D.store.set(key, JSON.stringify(state));

    // Fill the whole stage (which CSS keeps inside the safe area, clear of the iPhone's
    // Dynamic Island and home indicator). Text and icons scale uniformly by `s`; when the
    // screen's shape differs from the layout's, element boxes stretch along the longer
    // axis by kx/ky so there are no empty bands.
    let s = 1, kx = 1, ky = 1;
    const scaleNow = () => s;
    function measure() {
      const aw = stage.clientWidth || W, ah = stage.clientHeight || H;
      s = Math.min(aw / W, ah / H);
      const nkx = aw / s / W, nky = ah / s / H;
      const changed = Math.abs(nkx - kx) > 0.002 || Math.abs(nky - ky) > 0.002;
      kx = nkx; ky = nky;
      canvas.style.width = `${W * kx}px`;
      canvas.style.height = `${H * ky}px`;
      canvas.style.transform = `translate(-50%, -50%) scale(${s})`;
      return changed;
    }
    const fit = () => { measure(); render(); };
    const turned = () => setTimeout(fit, 250);
    window.addEventListener("resize", fit);
    window.addEventListener("orientationchange", turned);
    measure();

    // Layout coordinates are in design pixels; place them on the stretched canvas.
    const box = (el, e) => {
      el.style.left = `${e.x * kx}px`;
      el.style.top = `${e.y * ky}px`;
      if (e.w !== undefined) el.style.width = `${e.w * kx}px`;
      if (e.h !== undefined) el.style.height = `${e.h * ky}px`;
      return el;
    };
    const make = (tag, cls, html) => {
      const el = document.createElement(tag);
      el.className = cls;
      if (html !== undefined) el.innerHTML = html;
      return el;
    };
    const text = (el, sel, value) => { el.querySelector(sel).textContent = tr(value); return el; };
    // Each switch group starts on the first value its switch lists.
    const groupDefaults = {};
    (function scan(node) {
      if (Array.isArray(node)) { node.forEach(scan); return; }
      if (!node || typeof node !== "object") return;
      if (node.type === "switch" && !(node.group in groupDefaults)) groupDefaults[node.group] = node.values[0];
      for (const v of Object.values(node)) if (v && typeof v === "object") scan(v);
    })(layout);
    const groupValue = (g, values) => state.groups[g] || groupDefaults[g] || (values && values[0]);

    // ---------- element renderers ----------

    function hudButton(def, style) {
      const el = make("button", `hud-btn s-${style || def.style || "std"}`);
      el.type = "button";
      el.dataset.color = def.color || "";
      const icon = def.icon ? `<span class="hud-ico">${D.deckIcon(def.icon)}</span>` : "";
      const s = style || def.style || "std";
      if (s === "top" || s === "chev") {
        el.innerHTML = icon || `<span class="hud-lbl"></span>`;
        if (!def.icon) text(el, ".hud-lbl", def.label);
      } else if (s === "dial") {
        el.innerHTML = `<span class="hud-lbl"></span><span class="dial"><svg viewBox="0 0 40 40"><circle cx="20" cy="20" r="16"/><path d="M20 4a16 16 0 0 1 16 16"/></svg></span>`;
        text(el, ".hud-lbl", def.label);
      } else {
        el.innerHTML = `${icon}<span class="hud-lbl"></span>`;
        text(el, ".hud-lbl", def.label);
      }
      el.setAttribute("aria-label", tr(def.label));
      el.title = `${tr(def.label)} — ${def.keys}`;
      D.bindButton(el, def);
      return el;
    }

    const R = {
      button: (e) => box(hudButton(e), e),

      action(e) {
        // icon "emblem" shows the current manufacturer emblem (updated when the theme changes).
        const ico = e.icon === "emblem"
          ? `<span class="hud-ico hud-emblem js-emblem">${D.emblemHtml()}</span>`
          : `<span class="hud-ico">${D.deckIcon(e.icon)}</span>`;
        const el = box(make("button", "hud-btn s-top", ico), e);
        el.type = "button";
        el.onclick = e.action === "theme" ? D.openPicker : D.toggleFullscreen;
        el.setAttribute("aria-label", tr(e.action === "theme" ? "Deck settings" : "Fullscreen"));
        return el;
      },

      // Hold-to-talk mic for voice commands; hidden when they're turned off on the PC.
      // An optional "label" shows next to the mic.
      talk(e) {
        const el = box(make("button", "hud-btn s-top js-talk", `<span class="hud-ico">${D.deckIcon("mic")}</span>${e.label ? `<span class="hud-lbl"></span>` : ""}`), e);
        if (e.label) text(el, ".hud-lbl", e.label);
        el.type = "button";
        el.setAttribute("aria-label", tr("Hold to talk"));
        D.bindTalk(el);
        return el;
      },

      // Opens the list of voice commands; hidden with the mic when voice is off on the PC.
      voicehelp(e) {
        const el = box(make("button", "hud-btn s-top js-voice", `<span class="hud-ico">${D.deckIcon("help")}</span>`), e);
        el.type = "button";
        el.setAttribute("aria-label", tr("Voice commands list"));
        el.onclick = D.openVoiceHelp;
        return el;
      },

      status: (e) => box(make("div", "hud-status", `<span class="js-dot dot"></span><span class="js-status">Connecting…</span>`), e),

      rule: (e) => box(make("div", "hud-rule", "<i></i><i></i>"), e),
      divider: (e) => box(make("div", "hud-divider"), e),

      header: (e) => text(box(make("div", "hud-header", `<b>//</b><span></span>`), e), "span", e.text),

      "state-header"(e) {
        const values = Object.keys(e.labels);
        const cur = groupValue(e.group, values);
        const el = box(make("div", "hud-header state", `<div><b>//</b><span class="a"></span> <em>_ :${tr("ENABLED")}</em></div><div class="b"></div>`), e);
        text(el, ".a", e.labels[cur]);
        text(el, ".b", values.filter((v) => v !== cur).map((v) => e.labels[v]).join(" · "));
        return el;
      },

      switch(e) {
        const el = box(make("button", "hud-btn s-switch", `<span class="hud-lbl"></span>`), e);
        el.type = "button";
        text(el, ".hud-lbl", e.label);
        el.onclick = () => {
          const cur = groupValue(e.group, e.values);
          state.groups[e.group] = e.values[(e.values.indexOf(cur) + 1) % e.values.length];
          save();
          render();
        };
        return el;
      },

      vlabel: (e) => text(box(make("div", "hud-vlabel", "<span></span>"), e), "span", e.text),

      caption(e) {
        const el = box(make("div", "hud-caption", `<span class="hud-ico">${D.deckIcon(e.icon)}</span><span class="t"></span>`), e);
        el.dataset.color = e.color || "";
        return text(el, ".t", e.text);
      },

      bracket: (e) => box(make("div", "hud-bracket",
        `<svg viewBox="0 0 380 24" preserveAspectRatio="none"><path d="M2 2v8h120l10 10h116l10-10h120V2"/><path d="m184 12 6 6 6-6Z" class="f"/></svg>`), e),

      "danger-zone": (e) => box(make("div", "hud-danger"), e),

      slider(e) {
        const el = box(make("div", "hud-slider", `<div class="track"><span class="txt"></span></div><div class="knob"></div>`), e);
        text(el, ".txt", e.label);
        const knob = el.querySelector(".knob");
        const max = e.w * kx - 26;
        let start = null, pos = 0;
        const setPos = (p) => { pos = Math.max(0, Math.min(max, p)); knob.style.transform = `translateX(${pos}px)`; el.style.setProperty("--fill", `${(pos / max) * 100}%`); };
        knob.addEventListener("pointerdown", (ev) => {
          knob.setPointerCapture(ev.pointerId);
          start = ev.clientX - pos * scaleNow();
          el.classList.add("dragging");
        });
        knob.addEventListener("pointermove", (ev) => { if (start !== null) setPos((ev.clientX - start) / scaleNow()); });
        const end = () => {
          if (start === null) return;
          start = null;
          el.classList.remove("dragging");
          if (pos >= max * 0.94) {
            D.send({ t: "tap", id: e.id });
            D.buzz(40);
            window.DeckFX.pulse(el, "eject");
            el.classList.add("fired");
            setTimeout(() => el.classList.remove("fired"), 600);
          }
          knob.style.transition = "transform .25s ease";
          setPos(0);
          setTimeout(() => (knob.style.transition = ""), 260);
        };
        knob.addEventListener("pointerup", end);
        knob.addEventListener("pointercancel", end);
        return el;
      },

      menu(e) {
        const el = box(make("button", "hud-btn s-std s-menu", `<span class="hud-lbl"></span><span class="caret">▾</span>`), e);
        el.type = "button";
        text(el, ".hud-lbl", e.label);
        el.onclick = () => openMenu(e);
        return el;
      },

      grid(e) {
        const wrap = box(make("div", `hud-grid${e.compact ? " compact" : ""}`), e);
        const rows = Math.ceil(e.buttons.length / e.cols);
        const gap = e.compact ? 8 : 12;
        const cellH = Math.min((e.compact ? 64 : 120) * ky, (e.h * ky - gap * (rows - 1)) / rows);
        wrap.style.gridTemplateColumns = `repeat(${e.cols}, 1fr)`;
        wrap.style.gridAutoRows = `${cellH}px`;
        wrap.style.gap = `${gap}px`;
        for (const b of e.buttons) wrap.appendChild(hudButton(b, e.compact ? "std" : "cell"));
        return wrap;
      },
    };


    function openMenu(e) {
      const overlay = make("div", "hud-menu", `<div class="sheet"><div class="hud-header"><b>//</b><span></span></div><div class="opts"></div></div>`);
      text(overlay, ".hud-header span", e.title || e.label);
      const opts = overlay.querySelector(".opts");
      for (const o of e.options) {
        const b = hudButton(o, "cell");
        b.addEventListener("pointerup", () => setTimeout(() => overlay.remove(), 120));
        opts.appendChild(b);
      }
      overlay.addEventListener("pointerdown", (ev) => { if (ev.target === overlay) overlay.remove(); });
      canvas.appendChild(overlay);
    }

    function renderElements(parent, elements) {
      for (const e of elements) {
        if (e.when && groupValue(e.when[0]) !== e.when[1]) continue;
        const fn = R[e.type];
        if (fn) parent.appendChild(fn(e));
      }
    }

    // Set while paging, so the region's new panel slides in from that side.
    let slide = null;

    // Moves a region (one half of the HUD) d panels on, as its pager arrows do.
    function page(regionId, d) {
      const region = layout.regions.find((r) => r.id === regionId);
      if (!region) return;
      const n = region.panels.length;
      state.panels[regionId] = (Math.min(state.panels[regionId] || 0, n - 1) + d + n) % n;
      save();
      slide = { region: regionId, dir: d };
      render();
      slide = null;
    }

    function renderRegion(region) {
      const el = box(make("div", "hud-region"), region);
      el.dataset.region = region.id;
      const pagerH = region.pager || 44; // pager bar height; phone layouts use a slimmer one
      el.style.setProperty("--pager-h", `${pagerH}px`);
      const ids = region.panels;
      const idx = Math.min(state.panels[region.id] || 0, ids.length - 1);
      const panel = layout.panels[ids[idx]];
      const body = make("div", "hud-panel");
      if (slide && slide.region === region.id) body.classList.add(slide.dir > 0 ? "enter-next" : "enter-prev");
      body.style.height = `${region.h * ky - pagerH - 8}px`;
      if (panel.navdesk) {
        // Counter canvas scaling: desk controls retain real 44px touch targets.
        body.style.setProperty("--nd-scale", s);
        const host = make("div", "nd-host");
        host.style.cssText = `position:absolute;inset:0;width:${region.w*kx*s}px;height:${(region.h*ky-pagerH-8)*s}px;transform:scale(${1/s});transform-origin:top left`;
        body.appendChild(host);
        window.NavDesk.mount(host, panel.navdesk);
      } else renderElements(body, panel.elements);
      el.appendChild(body);

      const pager = make("div", "hud-pager",
        `<button type="button" class="hud-btn s-pg prev" aria-label="${tr("Previous panel")}">${D.deckIcon("chev-left")}</button>
         <div class="title"><span></span><div class="dots"></div></div>
         <button type="button" class="hud-btn s-pg next" aria-label="${tr("Next panel")}">${D.deckIcon("chev-right")}</button>`);
      text(pager, ".title span", panel.title);
      pager.querySelector(".dots").innerHTML = ids.map((_, i) => `<i class="${i === idx ? "on" : ""}"></i>`).join("");
      const go = (d) => page(region.id, d);
      pager.querySelector(".prev").onclick = () => go(-1);
      pager.querySelector(".next").onclick = () => go(1);
      el.appendChild(pager);
      return el;
    }

    function render() {
      canvas.innerHTML = "";
      renderElements(canvas, layout.chrome || []);
      for (const region of layout.regions) canvas.appendChild(renderRegion(region));
      D.refreshStatus();
      if (D.afterRender) D.afterRender();
    }

    render();
    // The layout editor redraws after changing the layout, and asks which panels are showing.
    current = {
      render,
      showPanel(id) {
        const region = layout.regions.find(r => r.panels.includes(id));
        if (!region) return false;
        state.panels[region.id] = region.panels.indexOf(id); save(); render(); return true;
      },
      panelShown: (regionId) => state.panels[regionId] || 0,
      // Pages the half at screen point (x, y), for the two-finger swipe; false if none is there.
      pageAt(x, y, d) {
        for (const el of canvas.querySelectorAll(".hud-region")) {
          const r = el.getBoundingClientRect();
          if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) { page(el.dataset.region, d); return true; }
        }
        return false;
      },
      scale: scaleNow,
      // Stops this deck redrawing itself, before another layout is drawn or a grid layout shown.
      dispose() {
        window.removeEventListener("resize", fit);
        window.removeEventListener("orientationchange", turned);
        if (current === this) current = null;
      },
    };
    return current;
  };
})();
