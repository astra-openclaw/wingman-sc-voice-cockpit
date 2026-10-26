// SC Voice-Cockpit navigation desk. One renderer for canvas, phone and grid.
// Uses NavPoint's API verdicts, never a browser-calculated bearing.
(() => {
  const tr = (s, ...args) => window.DeckI18n.t(s, ...args);
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };
  const finite = n => typeof n === "number" && Number.isFinite(n);
  const coords = p => p && ["x", "y", "z"].every(k => finite(p[k]));
  const system = p => p && (p.frame === "system" || p.system_frame === true);
  const sameBody = (a, b) => a?.body && b?.body &&
    ["body", "system", "frame_id"].every(k => !a[k] || !b[k] || String(a[k]).toLowerCase() === String(b[k]).toLowerCase());
  const fmt = n => finite(n) ? n.toLocaleString(document.documentElement.lang, { maximumFractionDigits: 0 }) : tr("Unavailable");
  const distance = km => km >= 1e6 ? (km/1e6).toFixed(2)+" Gm" : km >= 1e3 ? (km/1e3).toFixed(1)+" Mm" : km >= 1 ? km.toFixed(1)+" km" : (km*1000).toFixed(0)+" m";
  const tag = p => p?.coordinate_schema === 1 ? tr("Root") : p?.body || (system(p) ? tr("Deep space") : tr("No local frame"));
  function turn(h) {
    const a = Math.abs(h);
    if (a <= 5) return tr("Ahead");
    if (a > 150) return tr("Turn around");
    if (a > 90) return tr(h > 0 ? "Hard right" : "Hard left");
    if (a > 45) return tr(h > 0 ? "Turn right" : "Turn left");
    return tr(h > 0 ? "Bear right {0}°" : "Bear left {0}°", a.toFixed(0));
  }
  const elevation = v => Math.abs(v) <= 5 ? tr("Level") : tr(v > 0 ? "Pitch up {0}°" : "Pitch down {0}°", Math.abs(v).toFixed(0));
  let config, state = {}, received = 0, connected = false, points = [], bodies = [];
  let revision = null, loadedRevision, loading = null, filter = "", error = "", initialized = false;
  let listRequestedAt = -Infinity, listTimer = null;
  let stateGeneration = 0;
  const mounts = new Set();
  const liveMounts = () => [...mounts].filter(m => { if (m.root.isConnected) return true; mounts.delete(m); return false; });

  async function api(path, method = "GET", body) {
    const url = new URL(path, location.origin);
    url.searchParams.set("t", config.token());
    const r = await fetch(url, { method, headers: body === undefined ? {} : {"Content-Type":"application/json"},
      body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw Error(data.target_body ? tr("Travel to {0} first", data.target_body) : tr("Navigation request failed ({0})", r.status));
    if (data.success === false) throw Error(tr("Navigation request failed ({0})", r.status));
    return data;
  }
  function notice(e) {
    error = e?.message || tr("Navigation unavailable");
    config.toast(error, 4500);
    renderAll();
  }
  async function refreshPoints(force = false) {
    if (loading) return loading;
    const remaining = 1000 - (performance.now() - listRequestedAt);
    if (!force && remaining > 0) {
      if (listTimer === null) listTimer = setTimeout(() => {listTimer = null; refreshPoints();}, remaining);
      return;
    }
    clearTimeout(listTimer); listTimer = null;
    listRequestedAt = performance.now();
    const wanted = revision;
    loading = (async () => {
      try {
        const data = await api("/api/navpoints");
        points = data.navpoints || []; bodies = data.bodies || [];
        loadedRevision = wanted; error = "";
        liveMounts().forEach(renderList);
      } catch (e) { notice(e); }
      finally { loading = null; }
    })();
    return loading;
  }
  async function readState() {
    // A once-per-second push received during a REST request is newer.
    const generation = stateGeneration;
    const data = await api("/api/nav/state");
    if (generation === stateGeneration) update(data);
  }
  function update(data) {
    state = data; received = performance.now(); stateGeneration++;
    if (data.navpoints_revision !== undefined) revision = data.navpoints_revision;
    if (loadedRevision !== revision) refreshPoints();
    renderAll();
  }
  function view() {
    const p = state.current_position, t = state.active_target;
    const online = connected && received > 0 && performance.now() - received < 3500;
    const age = finite(state.position_age_s) ? Math.max(0, state.position_age_s) + (performance.now()-received)/1000 : null;
    const fresh = online && !!p && age !== null && age <= 30;
    const precisionMatches = p?.precision?.mode !== "cruise" || p.precision.target_id === t?.id;
    const g = state.space_guidance, local = state.planet_guidance, route = state.stack_route;
    let km = null, h = null, v = null, bearing = tr("Unavailable"), direction = tr("Direction unavailable");
    if (t && fresh && precisionMatches) {
      const space = g && ((system(t) && system(p)) || (t.frame === "stack" && p.active_frame === "space"));
      if (space) {
        km = g.distance_km;
        h = g.horizontal_offset_deg; v = g.vertical_angle_deg;
        bearing = finite(g.total_offset_deg) ? tr("{0}° off", g.total_offset_deg.toFixed(1)) : tr("No compass (open space)");
      } else if (route && finite(route.distance_km)) {
        km = route.distance_km;
        direction = tr(route.mode === "LOCAL_TRACKING" ? "Local tracking" : "Root approach (coarse)");
        bearing = tr("No bearing (uncalibrated)");
      } else if (coords(p) && coords(t) && sameBody(p, t)) {
        if (p.active_frame === "planet" || p.active_frame === "site") {
          if (local) { km = local.distance_km; h = local.horizontal_offset_deg; v = local.vertical_angle_deg; }
          bearing = tr("Camera relative");
        } else km = Math.hypot(t.x-p.x, t.y-p.y, t.z-p.z) / 1000;
      } else if (system(t) && p.body) direction = tr("Deep-space target: leave the surface first");
      else if (t.body && !sameBody(p, t)) direction = tr("Travel to {0} first", t.body);
      else if (!p.body && !system(p)) direction = tr("No local frame");
    }
    if (!finite(h) || !finite(v)) { h = null; v = null; }
    if (h !== null) direction = turn(h);
    const coarse = p?.precision?.target_id === t?.id && p?.precision?.mode === "cruise";
    const arrival = finite(km) ? coarse ? tr("COARSE") :
      state.arrival_stop_m > 0 && km*1000 <= state.arrival_stop_m ? tr("ARRIVED") :
      state.arrival_alert_m > 0 && km*1000 <= state.arrival_alert_m ? tr("APPROACHING") : "" : "";
    return {p,t,age,fresh,online,km,h,v,bearing,direction,arrival,coarse};
  }
  function button(label, action, cls = "") {
    const el = make("button", "nd-button "+cls, tr(label)); el.type = "button";
    el.onclick = action; return el;
  }
  function heading(label) {
    const title = make("h3", "hud-header nd-heading");
    title.append(make("b", "", "//"), make("span", "", tr(label).replaceAll(" ", "_")));
    return title;
  }
  function mount(parent, mode) {
    const root = make("section", "navdesk"); root.dataset.navdesk = mode;
    const m = {root, mode, listKey: null};
    parent.append(root); mounts.add(m);
    if (mode === "both") {
      const tabs = make("div", "nd-tabs");
      const a = button("NAV", () => switchTab("nav")), b = button("WAYPOINTS", () => switchTab("waypoints"));
      tabs.append(a,b); root.append(tabs);
      m.nav = make("div","nd-content"); m.list = make("div","nd-content");
      root.append(m.nav,m.list);
      function switchTab(active) {
        m.nav.hidden = active !== "nav"; m.list.hidden = active !== "waypoints";
        a.setAttribute("aria-pressed", active === "nav"); b.setAttribute("aria-pressed", active === "waypoints");
      }
      switchTab("nav");
    } else {
      const content = make("div","nd-content"); root.append(content);
      if (mode === "nav") m.nav = content; else m.list = content;
    }
    if (m.nav) {
      m.nav.append(heading("Navigation"));
      m.guidance = make("div", "nd-guidance"); m.nav.append(m.guidance);
      const actions = make("div","nd-actions");
      actions.append(button("Mark Location", () => config.send({t:"tap",id:"nav-mark"}), "nd-blue"),
        button("Clear Target", () => config.send({t:"tap",id:"nav-clear"}), "nd-blue"),
        button("Reset Lights", () => config.reset()));
      m.nav.append(actions);
    }
    renderNav(m); renderList(m);
    return root;
  }
  function renderNav(m) {
    if (!m.nav) return;
    const q = view(), wrap = m.guidance, scroll = wrap.scrollTop;
    // Keep the action buttons/focus stable across every push.
    wrap.replaceChildren();
    const target = make("div","nd-target");
    target.append(make("strong","nd-target-name",q.t?.name || tr("No target set")));
    target.append(make("div","nd-meta", q.t ? [...new Set([tag(q.t),q.t.system,q.t.display_location || q.t.zone].filter(Boolean))].join(" · ") : tr("Select a waypoint or say “navigate to [name]”")));
    if (q.arrival) target.append(make("span","nd-arrival",q.arrival));
    wrap.append(target);
    if (!q.online || error) wrap.append(make("p","nd-warning",error || tr("Navigation offline")));
    const instrument = make("div","nd-instrument");
    // SVG inherits theme tokens directly, including Origin and high contrast.
    // CSS supplies a short transition/pulse; reduce-motion disables both.
    const ring = make("div","nd-ring");
    const mag = Math.hypot(q.h || 0, q.v || 0), scale = Math.max(45, mag);
    const x = 100 + (q.h || 0)/scale*51, y = 100 - (q.v || 0)/scale*51;
    ring.innerHTML = '<svg viewBox="0 0 200 200" role="img"><circle class="nd-rose" cx="100" cy="100" r="92"/><circle class="nd-bound" cx="100" cy="100" r="64"/><path class="nd-cross" d="M100 12v16M100 172v16M12 100h16M172 100h16M92 100h16M100 92v16"/><path class="nd-forward" d="M100 10l-5 9h10Z"/><circle class="nd-bubble" r="11"/><text class="nd-unknown" x="100" y="107" text-anchor="middle">?</text></svg>';
    const svg = ring.firstChild;
    svg.setAttribute("aria-label",tr("Direction ring: see readouts"));
    const bubble = ring.querySelector(".nd-bubble");
    bubble.setAttribute("cx",x); bubble.setAttribute("cy",y);
    bubble.style.display = q.h === null ? "none" : "";
    bubble.style.color = mag <= 20 ? "var(--green)" : mag < 90 ? "var(--amber)" : "var(--red)";
    bubble.classList.toggle("nd-on-course", q.h !== null && mag < 5);
    ring.querySelector(".nd-unknown").style.display = q.h === null ? "" : "none";
    const values = make("dl","nd-readouts");
    const dist = finite(q.km) ? (q.coarse ? tr("Approximately {0}",distance(q.km)) : distance(q.km)) : tr("Unavailable");
    [["Distance",dist],["Direction",q.direction],["Elevation",q.v === null ? tr("Unavailable") : elevation(q.v)],["Bearing",q.bearing]].forEach(([label,val]) => {
      values.append(make("dt","",tr(label)),make("dd","nd-"+label.toLowerCase(),val));
    });
    instrument.append(ring,values); wrap.append(instrument);
    const route = make("div","nd-route"); route.append(heading("Route"));
    const steps = make("ol");
    if (q.t) {
      const t = q.t;
      const rows = [t.system && tr("{0} system",t.system),t.body && tr("Go to {0}",t.body),
        t.zone && t.zone !== t.body && tr("Zone: {0}",t.zone),
        tr("Coordinates: {0}",[fmt(t.x),fmt(t.y),fmt(t.z)].join(" · "))].filter(Boolean);
      rows.forEach(row => steps.append(make("li","",row)));
      if (q.p?.precision?.slow_down_advisory) steps.append(make("li","",tr("Slow down for final approach")));
    } else steps.append(make("li","",tr("No target set")));
    route.append(steps); wrap.append(route);
    const position = make("div","nd-position");
    position.append(heading("Current Position"));
    if (q.p && !q.fresh) position.append(make("strong","nd-warning",tr("STALE")));
    position.append(make("div","",q.p ? tr("Coordinates: {0}",[fmt(q.p.x),fmt(q.p.y),fmt(q.p.z)].join(" · ")) : tr("Unavailable")));
    if (q.p) {
      position.append(make("div","nd-meta",[tag(q.p),q.p.zone].filter(Boolean).join(" · ")));
      position.append(make("div","nd-age",tr("Read age: {0}",q.age === null ? tr("Unknown") : q.age.toFixed(1)+" s")));
      if (q.p.precision?.mode) position.append(make("div","nd-meta",tr(q.p.precision.mode === "cruise" ? "Coarse estimate; fractional digits omitted" : "Final approach; slow down")));
    }
    wrap.append(position); wrap.scrollTop = scroll;
  }
  function renderList(m) {
    if (!m.list) return;
    const key = JSON.stringify([points,bodies,filter,state.active_target?.id,document.documentElement.lang,error]);
    if (key === m.listKey) return;
    m.listKey = key;
    const scroll = m.list.querySelector(".nd-waypoints")?.scrollTop || 0;
    m.list.replaceChildren(heading("WAYPOINTS"));
    const label = make("label","nd-filter",tr("Filter by body"));
    const select = make("select"); select.setAttribute("aria-label",tr("Filter by body"));
    const all = make("option","",tr("All Bodies")); all.value = ""; select.append(all);
    bodies.forEach(body => {const option = make("option","",body); option.value = body; select.append(option);});
    select.value = filter;
    select.onchange = () => { filter = select.value; liveMounts().forEach(renderList); };
    label.append(select); m.list.append(label);
    const list = make("div","nd-waypoints"); m.list.append(list);
    if (error) list.append(make("p","nd-warning",error));
    const visible = points.filter(p => !filter || p.body === filter);
    if (!visible.length) list.append(make("p","nd-empty",tr(points.length ? "No waypoints on this body" : "No waypoints yet; say “mark location” in game.")));
    visible.forEach(wp => {
      const row = make("div","nd-waypoint"); row.dataset.id = wp.id;
      const active = state.active_target?.id === wp.id; row.classList.toggle("nd-active",active);
      const choose = button("", () => setTarget(wp.id), "nd-choose");
      choose.replaceChildren(make("strong","",wp.name),make("span","nd-meta",[...new Set([tag(wp),wp.display_location || wp.zone,wp.system].filter(Boolean))].join(" · ")));
      choose.append(make("span","nd-coords",tr("Coordinates: {0}",[fmt(wp.x),fmt(wp.y),fmt(wp.z)].join(" · "))));
      if (wp.timestamp) choose.append(make("span","nd-meta",new Date(wp.timestamp).toLocaleDateString(document.documentElement.lang)));
      if (active) choose.append(make("span","nd-active-label",tr("Active target")));
      choose.setAttribute("aria-pressed",active);
      const more = button("More", () => sheet(wp,more), "nd-more");
      more.setAttribute("aria-label",tr("More: {0}",wp.name));
      row.append(choose,more); list.append(row);
    });
    list.scrollTop = scroll;
  }
  async function setTarget(id) {
    try { await api("/api/nav/target/"+encodeURIComponent(id),"POST"); error = ""; await readState(); config.openNav(); return true; }
    catch (e) { notice(e); return false; }
  }
  function sheet(wp, opener) {
    document.querySelector(".nd-sheet")?.remove();
    const overlay = make("div","nd-sheet");
    const dialog = make("div","nd-dialog"); dialog.setAttribute("role","dialog");
    dialog.setAttribute("aria-modal","true"); dialog.setAttribute("aria-label",tr("Waypoint details"));
    overlay.append(dialog); document.body.append(overlay);
    const close = () => {overlay.remove(); opener?.isConnected && opener.focus();};
    dialog.append(heading("Waypoint details"),make("strong","nd-sheet-name",wp.name));
    const closeBtn = button("Close",close); dialog.append(closeBtn);
    dialog.append(button("Set Target",async () => { if (await setTarget(wp.id)) close(); },"nd-blue"));
    const nameLabel = make("label","",tr("Name")), name = make("input");
    name.value = wp.name; name.maxLength = 200; nameLabel.append(name); dialog.append(nameLabel);
    const notesLabel = make("label","",tr("Notes")), notes = make("textarea");
    notes.value = wp.notes || ""; notes.maxLength = 4000; notesLabel.append(notes); dialog.append(notesLabel);
    const status = make("p","nd-sheet-status"); status.setAttribute("role","status");
    let busy = false;
    async function save(action, body, label) {
      if (busy) return; busy = true;
      dialog.querySelectorAll("button").forEach(b => {b.disabled = true;});
      status.textContent = tr("Saving…");
      try {
        await api("/api/navpoints/"+encodeURIComponent(wp.id)+action,action ? "PUT" : "DELETE",body);
        await refreshPoints(true); await readState();
        status.textContent = tr(label);
        if (!action) close();
      } catch(e) {status.textContent = e.message;}
      finally {busy = false; dialog.querySelectorAll("button").forEach(b => {b.disabled = false;});}
    }
    dialog.append(button("Rename",() => {
      if (!name.value.trim()) {status.textContent = tr("Enter a name"); name.focus(); return;}
      save("/name",{name:name.value.trim()},"Saved");
    }));
    dialog.append(button("Save Notes",() => save("/notes",{notes:notes.value},"Saved")));
    const del = button("Delete",() => {
      del.hidden = true;
      const confirm = make("div","nd-confirm");
      confirm.append(make("p","",tr("Delete this waypoint?")));
      confirm.append(button("Confirm Delete",() => save("",undefined,"Deleted"),"nd-danger"));
      confirm.append(button("Cancel",() => {confirm.remove();del.hidden = false;del.focus();}));
      dialog.append(confirm); confirm.querySelector("button").focus();
    },"nd-danger");
    dialog.append(del,status);
    overlay.onclick = e => {if (e.target === overlay) close();};
    overlay.onkeydown = e => {
      if (e.key === "Escape") close();
      if (e.key === "Tab") {
        const controls = [...dialog.querySelectorAll("button,input,textarea")].filter(e=>!e.hidden && e.offsetHeight);
        if (e.shiftKey && document.activeElement === controls[0]) {e.preventDefault();controls.at(-1).focus();}
        else if (!e.shiftKey && document.activeElement === controls.at(-1)) {e.preventDefault();controls[0].focus();}
      }
    };
    closeBtn.focus();
  }
  function renderAll() {
    liveMounts().forEach(m => {renderNav(m);renderList(m);});
    const status = document.getElementById("nav-status"), q = view();
    if (status) status.textContent = !q.online ? tr("Navigation offline") : q.t ? q.t.name+" · "+(finite(q.km) ? distance(q.km) : tr("Distance unavailable")) : tr("No target set");
  }
  window.NavDesk = {
    mount, update,
    online(on) {connected = on; renderAll();},
    configure(options) {
      config = options;
      document.getElementById("nav-deck-link").onclick = () => config.openNav();
      if (!initialized) {
        initialized = true;
        setInterval(() => {
          renderAll();
          // Failed list loads retry on the next push/tick, at most once a second.
          if (connected && loadedRevision !== revision) refreshPoints();
        },1000);
      }
      Promise.all([readState(),refreshPoints(true)]).catch(notice);
    }
  };
})();
