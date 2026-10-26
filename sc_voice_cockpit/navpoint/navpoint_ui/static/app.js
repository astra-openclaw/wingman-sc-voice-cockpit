/* SC NavPoint HUD — Frontend Logic */

const API = '';

// A position whose server-computed age is null or older than this (seconds) is
// STALE: no bearing is drawn from it and it cannot drive the deflection bubble.
// Mirrors POSITION_FRESHNESS_S in app.py.
const POSITION_FRESHNESS_S = 30;

let state = {
  navpoints: [],
  bodies: [],
  activeTarget: null,
  currentPos: null,
  positionAgeS: null,
  arrivalAlertM: 1000,
  arrivalStopM: 250,
  lastToken: -1,
  bodyFilter: '',
  // Server-computed guidance for a system-frame (open space) target; null in
  // every other case. See navpoint_ui/app.py for why it is not computed here.
  spaceGuidance: null,
  planetGuidance: null,
  // Server-computed route for a coordinate-stack target: mode, distance and
  // whether a direction may be drawn at all. Null for every legacy waypoint.
  stackRoute: null,
  // Whether the last poll reached the skill. A page whose server has gone away
  // keeps rendering the last data it received, so without this flag a dead HUD
  // is indistinguishable from a live one and every button silently does
  // nothing.
  online: true,
};

const OFFLINE_MSG = 'HUD OFFLINE: no answer from the NavPoint skill';

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  refreshWaypoints();
  pollNavState();
  setInterval(pollNavState, 2000);
});

// ── Data fetching ─────────────────────────────────────────────────────────────

async function refreshWaypoints() {
  const params = new URLSearchParams();
  if (state.bodyFilter) params.set('body', state.bodyFilter);
  const qs = params.toString();
  const url = qs ? `/api/navpoints?${qs}` : '/api/navpoints';
  try {
    const data = await fetchJSON(url);
    state.navpoints = data.navpoints || [];
    state.bodies    = data.bodies    || [];
    renderBodyFilter();
    renderWaypointList();
  } catch (e) {
    setStatus('Error loading waypoints');
  }
}

async function pollNavState() {
  try {
    const data = await fetchJSON('/api/nav/state');
    state.activeTarget = data.active_target;
    state.currentPos   = data.current_position;
    state.positionAgeS = data.position_age_s;
    state.spaceGuidance = data.space_guidance || null;
    state.planetGuidance = data.planet_guidance || null;
    state.stackRoute = data.stack_route || null;
    if (data.arrival_alert_m != null) state.arrivalAlertM = data.arrival_alert_m;
    if (data.arrival_stop_m  != null) state.arrivalStopM  = data.arrival_stop_m;
    // The waypoint list only changes on a token bump; re-render it then.
    if (data.update_token !== state.lastToken) {
      state.lastToken = data.update_token;
      await refreshWaypoints();
    }
    // The nav panel re-renders every poll so a growing position age crosses
    // the freshness threshold on its own, without waiting for a token change.
    renderNavPanel();
    setOnline(true);
  } catch (_) {
    // Swallowing this used to be the page's worst habit: with the skill gone
    // the display froze on its last good read and looked perfectly healthy.
    setOnline(false);
  }
}

async function fetchJSON(url) {
  const r = await fetch(API + url);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

// ── Render: Body filter ───────────────────────────────────────────────────────

function renderBodyFilter() {
  const sel = document.getElementById('body-filter');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">All Bodies</option>';
  state.bodies.forEach(body => {
    const opt = document.createElement('option');
    opt.value = body;
    opt.textContent = body;
    if (body === current) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.onchange = () => { state.bodyFilter = sel.value; refreshWaypoints(); };
}

// ── Render: Waypoint list ─────────────────────────────────────────────────────

function renderWaypointList() {
  const list = document.getElementById('waypoint-list');
  if (!state.navpoints.length) {
    list.innerHTML = '<div class="empty-state">No waypoints saved yet.<br>Say "mark location" in game to save one.</div>';
    return;
  }

  list.innerHTML = '';
  state.navpoints.forEach(wp => {
    const isActive = state.activeTarget && state.activeTarget.id === wp.id;
    const card = document.createElement('div');
    card.className = 'waypoint-card' + (isActive ? ' active' : '');
    card.dataset.id = wp.id;

    const location = wp.display_location || wp.zone || wp.body;
    const ts = wp.timestamp ? new Date(wp.timestamp).toLocaleDateString() : '';

    card.innerHTML = `
      <div class="wp-header">
        <span class="wp-name" id="wp-name-${wp.id}">${escHtml(wp.name)}</span>
        <input class="wp-name-edit" id="wp-edit-${wp.id}" type="text"
               value="${escHtml(wp.name)}" aria-label="Waypoint name"
               onblur="saveRename(${wp.id})"
               onkeydown="if(event.key==='Enter')this.blur();if(event.key==='Escape')cancelRename(${wp.id})">
        <div class="wp-actions">
          <button class="wp-btn nav-btn" onclick="setTarget(${wp.id})" title="Navigate here" aria-label="Navigate to ${escHtml(wp.name)}">▶</button>
          <button class="wp-btn edit-btn" onclick="startRename(${wp.id})" title="Rename" aria-label="Rename ${escHtml(wp.name)}">✏</button>
          <button class="wp-btn del-btn" onclick="deleteWaypoint(${wp.id})" title="Delete" aria-label="Delete ${escHtml(wp.name)}">✕</button>
        </div>
      </div>
      <div class="wp-meta">
        ${location ? `<span class="wp-tag">${escHtml(location)}</span>` : ''}
        <span class="wp-tag">${escHtml(waypointTag(wp))}</span>
        ${wp.system ? `<span class="wp-tag">${escHtml(wp.system)}</span>` : ''}
        ${ts ? `<span>${ts}</span>` : ''}
      </div>
      <div class="wp-coords">X ${fmt(wp.x)} · Y ${fmt(wp.y)} · Z ${fmt(wp.z)}</div>
    `;

    list.appendChild(card);
  });
}

// ── Render: Navigation panel ──────────────────────────────────────────────────

function renderNavPanel() {
  const target = state.activeTarget;
  const pos    = state.currentPos;

  // A position with no server age, or one older than the freshness window, is
  // stale: it never yields a bearing and the position box shows dimmed + STALE.
  const posStale = !!pos && (state.positionAgeS == null || state.positionAgeS > POSITION_FRESHNESS_S);
  const posFresh = !!pos && state.positionAgeS != null && state.positionAgeS <= POSITION_FRESHNESS_S;

  const targetCard = document.getElementById('target-card');
  const noTargetMsg = document.getElementById('no-target-msg');
  const clearBtn = document.getElementById('clear-target-btn');

  if (!target) {
    targetCard.classList.add('hidden');
    noTargetMsg.style.display = '';
    clearBtn.style.display = 'none';
    clearDeflection();
    setNavRow('nav-distance', '—');
    setNavRow('nav-turn', '—');
    setNavRow('nav-elev', '—');
    setNavRow('nav-bearing', '—');
    renderGuidance([]);
    renderPosition(pos, posStale);
    return;
  }

  noTargetMsg.style.display = 'none';
  targetCard.classList.remove('hidden');
  clearBtn.style.display = '';

  const location = target.display_location || target.zone || target.body;
  document.getElementById('target-name').textContent = target.name;
  document.getElementById('target-meta').textContent =
    [location, target.system].filter(Boolean).join(' · ');

  // Two frames can be guided in. A body-fixed local frame needs matching
  // bodies; the system frame of open space has no north, so the server supplies
  // the guidance and the ring renders offsets relative to where the player
  // actually points.
  const spaceG = state.spaceGuidance;
  const inSpaceFrame = posFresh && !!spaceG &&
    ((isSystemFrame(target) && isSystemFrame(pos)) ||
     (target.frame === 'stack' && pos.active_frame === 'space'));
  const precisionMatches = pos?.precision?.mode !== 'cruise' || pos.precision.target_id === target.id;
  const framesMatch = posFresh && precisionMatches && hasCoords(pos) && hasCoords(target) && sameBody(pos, target);

  // Confirmed space reads receive camera-relative Root guidance from the
  // server. Other stack routes retain their distance-only presentation.
  const stackRoute = state.stackRoute;
  const inStackRoute = posFresh && !!stackRoute && stackRoute.distance_km != null;

  if (inStackRoute && !inSpaceFrame) {
    clearDeflection();
    renderDistance(stackRoute.distance_km);
    setNavRow('nav-turn', stackRoute.mode === 'LOCAL_TRACKING'
      ? 'LOCAL' : 'ROOT APPROACH (coarse)');
    setNavRow('nav-elev',    '—');
    setNavRow('nav-bearing', 'NO BEARING (direction uncalibrated)');
    renderGuidance(buildApproachSteps(target));
  } else if (inSpaceFrame) {
    if (spaceG.horizontal_offset_deg != null && spaceG.vertical_angle_deg != null) {
      setDeflection(spaceG.horizontal_offset_deg, spaceG.vertical_angle_deg);
    } else {
      clearDeflection();
    }
    renderDistance(spaceG.distance_km);
    setNavRow('nav-turn',    spaceG.turn_instruction || '—');
    setNavRow('nav-elev',    spaceG.elevation_instruction || '—');
    // No compass exists in open space, so no bearing is shown rather than a
    // number that would look meaningful and not be.
    setNavRow('nav-bearing', spaceG.total_offset_deg != null
      ? spaceG.total_offset_deg.toFixed(1) + '° off' : 'NO COMPASS (open space)');
    renderGuidance(buildApproachSteps(target));
  } else if (framesMatch) {
    if (pos.active_frame === 'planet' || pos.active_frame === 'site') {
      const g = state.planetGuidance;
      if (g && g.horizontal_offset_deg != null && g.vertical_angle_deg != null) {
        setDeflection(g.horizontal_offset_deg, g.vertical_angle_deg);
        setNavRow('nav-turn', turnInstruction(g.horizontal_offset_deg));
        setNavRow('nav-elev', elevInstruction(g.vertical_angle_deg));
      } else {
        clearDeflection();
        setNavRow('nav-turn', 'Direction unavailable');
        setNavRow('nav-elev', '—');
      }
      if (g) renderDistance(g.distance_km);
      else setNavRow('nav-distance', '—');
      setNavRow('nav-bearing', 'Camera relative');
      renderGuidance(buildApproachSteps(target));
      renderPosition(pos, posStale);
      return;
    }
    clearDeflection();
    renderDistance(Math.hypot(target.x-pos.x, target.y-pos.y, target.z-pos.z) / 1000);
    setNavRow('nav-turn', 'Direction unavailable');
    setNavRow('nav-elev', '—');
    setNavRow('nav-bearing', '—');
    renderGuidance(buildApproachSteps(target));
  } else {
    clearDeflection();
    setNavRow('nav-distance', '—');
    setNavRow('nav-elev',     '—');
    setNavRow('nav-bearing',  '—');
    if (posStale) {
      // Stale reading: no trustworthy frame, so the direction row stays blank.
      setNavRow('nav-turn', '—');
    } else if (isSystemFrame(target) && pos && pos.body) {
      setNavRow('nav-turn', 'Deep-space waypoint, leave the surface first');
    } else if (isSystemFrame(pos) && target.body) {
      setNavRow('nav-turn', `Target on ${target.body}, travel there first`);
    } else if (pos && !pos.body && !isSystemFrame(pos)) {
      setNavRow('nav-turn', 'NO LOCAL FRAME (station or base interior)');
    } else if (pos && pos.body && target.body && !sameBody(pos, target)) {
      setNavRow('nav-turn', `Target on ${target.body}, travel there first`);
    } else {
      setNavRow('nav-turn', '—');
    }
    renderGuidance(buildApproachSteps(target));
  }

  renderPosition(pos, posStale);
}

// ── Navigation math ───────────────────────────────────────────────────────────

function hasCoords(obj) {
  return obj && obj.x != null && obj.y != null && obj.z != null;
}

// How a waypoint is labelled in the list. A coordinate-stack row has no body
// and no system name by design, so neither of the legacy labels fits it.
function waypointTag(wp) {
  if (wp && wp.coordinate_schema === 1) return 'Root';
  if (wp && wp.body) return wp.body;
  return isSystemFrame(wp) ? 'Deep space' : '';
}

// A row or capture in the star system's own frame, as opposed to a body-fixed
// one. Waypoints in open space carry no body, so a body comparison can never
// match and would silently hide them.
function isSystemFrame(obj) {
  return !!(obj && (obj.frame === 'system' || obj.system_frame === true));
}

function sameBody(a, b) {
  return !!(a && b && a.body && b.body
    && (!a.frame_id || !b.frame_id || String(a.frame_id).toLowerCase() === String(b.frame_id).toLowerCase())
    && (!a.system || !b.system || String(a.system).toLowerCase() === String(b.system).toLowerCase())
    && String(a.body).toLowerCase() === String(b.body).toLowerCase());
}

function turnInstruction(offset) {
  const a = Math.abs(offset);
  if (a <= 5)   return 'Ahead';
  if (a > 150)  return 'Turn around';
  const side = offset > 0 ? 'right' : 'left';
  if (a > 90)   return `Hard ${side}`;
  if (a > 45)   return `Turn ${side}`;
  return `Bear ${side} ${a.toFixed(0)}°`;
}

function elevInstruction(angle) {
  if (Math.abs(angle) <= 5) return 'Level';
  const dir = angle > 0 ? 'up' : 'down';
  return `Pitch ${dir} ${Math.abs(angle).toFixed(0)}°`;
}

function formatDist(km) {
  if (km >= 1e6) return (km/1e6).toFixed(2) + ' Gm';
  if (km >= 1e3) return (km/1e3).toFixed(1) + ' Mm';
  if (km >= 1)   return km.toFixed(1) + ' km';
  return (km*1000).toFixed(0) + ' m';
}

// ── Deflection ring canvas ────────────────────────────────────────────────────
// A small bubble inside a boundary circle: concentric means the target is dead
// ahead, and the bubble reaches the inner wall at 45 degrees or more off centre.

const compassCanvas = document.getElementById('compass');
const compassCtx    = compassCanvas.getContext('2d');

const CX = 110, CY = 110;
const R_ROSE   = Math.min(CX, CY) - 8; // 102, matches the original rose radius
const R_BOUND  = 70;                   // boundary circle
const R_BUBBLE = 12;                   // bubble radius (smaller = more travel)
const D = R_BOUND - R_BUBBLE;          // 58 px max displacement, reached at 45 deg

const GREEN = [0, 232, 144];
const AMBER = [255, 182, 72];
const RED   = [255, 77, 94];

// Ring state. sH / sElev are the smoothed (displayed) angular offsets; tH /
// tElev are the latest fresh-bearing target the smoothing glides toward.
const ring = {
  hasBubble: false,
  sH: 0, sElev: 0,
  tH: 0, tElev: 0,
  rafId: null,
  lastTs: null,
  pendingSnap: false,
};

// Colour / pulse from an angular offset pair. Colour is a continuous gradient
// driven by totalOff alone (green -> amber -> red); anything past 90 deg total
// is solid red, which subsumes the behind-you case. Colour follows the smoothed
// position so it never disagrees with where the bubble is drawn.
function stateFor(h, elev) {
  const totalOff = Math.sqrt(h * h + elev * elev);
  if (totalOff <= 20) {
    // Solid green; the dead-on pulse cue fires only under 5 deg.
    return { rgb: GREEN, pulse: totalOff < 5 };
  }
  if (totalOff < 45) {
    return { rgb: blend(GREEN, AMBER, (totalOff - 20) / 25), pulse: false };
  }
  if (totalOff < 90) {
    // Bubble is already pinned at the wall; colour keeps shifting to encode
    // how far past the wall the target sits.
    return { rgb: blend(AMBER, RED, (totalOff - 45) / 45), pulse: false };
  }
  return { rgb: RED, pulse: false };
}

function blend(a, b, t) {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

function rgba(arr, a) {
  return 'rgba(' + arr[0] + ',' + arr[1] + ',' + arr[2] + ',' + a + ')';
}

// Bubble centre from smoothed offsets, with a radial clamp so the bubble never
// crosses the wall (magnitude beyond 45 deg is scaled back to unit length).
function bubbleVector(h, elev) {
  let vx = h / 45;
  let vy = -elev / 45; // screen up = target above
  const mag = Math.sqrt(vx * vx + vy * vy);
  if (mag > 1) { vx /= mag; vy /= mag; }
  return { x: CX + vx * D, y: CY + vy * D };
}

function drawRose(ctx) {
  const r = R_ROSE;
  ctx.clearRect(0, 0, compassCanvas.width, compassCanvas.height);

  // Background
  ctx.beginPath();
  ctx.arc(CX, CY, r, 0, 2*Math.PI);
  ctx.fillStyle = '#131f29';
  ctx.fill();
  ctx.strokeStyle = '#263642';
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Tick marks (emphasis kept at the 90 and 45 degree marks)
  for (let i = 0; i < 360; i += 15) {
    const rad = (i - 90) * Math.PI / 180;
    const outer = r - 2;
    const inner = i % 90 === 0 ? r - 14 : (i % 45 === 0 ? r - 10 : r - 6);
    ctx.beginPath();
    ctx.moveTo(CX + outer * Math.cos(rad), CY + outer * Math.sin(rad));
    ctx.lineTo(CX + inner * Math.cos(rad), CY + inner * Math.sin(rad));
    ctx.strokeStyle = i % 90 === 0 ? '#a0ebc7' : '#263642';
    ctx.lineWidth = i % 90 === 0 ? 2 : 1;
    ctx.stroke();
  }

  // Forward indicator (small triangle at top)
  ctx.beginPath();
  ctx.moveTo(CX, CY - r + 4);
  ctx.lineTo(CX - 5, CY - r + 13);
  ctx.lineTo(CX + 5, CY - r + 13);
  ctx.closePath();
  ctx.fillStyle = '#a0ebc7';
  ctx.fill();
}

function drawNoTargetGlyph(ctx) {
  ctx.fillStyle = '#9aafbd';
  ctx.font = '600 28px system-ui';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('?', CX, CY);
}

function drawBoundary(ctx) {
  ctx.beginPath();
  ctx.arc(CX, CY, R_BOUND, 0, 2*Math.PI);
  ctx.strokeStyle = '#263642';
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function drawBubble(ctx, x, y, rgb, glowAlpha) {
  // Soft radial glow matching the state colour
  const glowR = R_BUBBLE * 2.4;
  const g = ctx.createRadialGradient(x, y, 0, x, y, glowR);
  g.addColorStop(0, rgba(rgb, glowAlpha));
  g.addColorStop(1, rgba(rgb, 0));
  ctx.beginPath();
  ctx.arc(x, y, glowR, 0, 2*Math.PI);
  ctx.fillStyle = g;
  ctx.fill();

  // Filled circle at 85% opacity
  ctx.beginPath();
  ctx.arc(x, y, R_BUBBLE, 0, 2*Math.PI);
  ctx.fillStyle = rgba(rgb, 0.85);
  ctx.fill();

  // 1.5px solid rim at full opacity
  ctx.strokeStyle = rgba(rgb, 1);
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function renderRing(ts) {
  const ctx = compassCtx;
  drawRose(ctx);

  if (!ring.hasBubble) {
    // No bubble to draw: keep the no-target face identical to before (the
    // boundary circle appears only alongside a bubble).
    drawNoTargetGlyph(ctx);
    return;
  }

  const st = stateFor(ring.sH, ring.sElev);
  let glowAlpha = 0.22;
  if (st.pulse) {
    // On-course pulse: glow alpha oscillates 0.15..0.35, period 1.2s, no radius pulse.
    glowAlpha = 0.25 + 0.10 * Math.sin(2 * Math.PI * ts / 1200);
  }
  const c = bubbleVector(ring.sH, ring.sElev);
  drawBoundary(ctx);
  drawBubble(ctx, c.x, c.y, st.rgb, glowAlpha);
}

function ringFrame(ts) {
  if (!ring.hasBubble) { ring.rafId = null; return; }
  if (ring.lastTs == null) ring.lastTs = ts;
  let dt = (ts - ring.lastTs) / 1000;
  ring.lastTs = ts;
  if (dt > 0.1) dt = 0.1; // guard against long tab-hidden gaps

  if (ring.pendingSnap) {
    ring.sH = ring.tH;
    ring.sElev = ring.tElev;
    ring.pendingSnap = false;
  } else {
    const a = 1 - Math.exp(-dt / 0.25); // 250 ms time constant
    ring.sH += (ring.tH - ring.sH) * a;
    ring.sElev += (ring.tElev - ring.sElev) * a;
  }

  renderRing(ts);
  ring.rafId = requestAnimationFrame(ringFrame);
}

function ensureRingLoop() {
  if (ring.rafId == null) {
    ring.lastTs = null;
    ring.rafId = requestAnimationFrame(ringFrame);
  }
}

// Feed the ring a fresh bearing. New poll data only moves the smoothing target;
// the rAF loop does the gliding. The first frame after re-acquiring a target
// snaps directly so the bubble never streaks in from the centre.
function setDeflection(horizontalOffset, elevation) {
  ring.tH = horizontalOffset;
  ring.tElev = elevation;
  if (!ring.hasBubble) {
    ring.hasBubble = true;
    ring.pendingSnap = true;
  }
  ensureRingLoop();
}

// Drop back to the no-bubble face (no target, no local frame, cross-body, or
// stale position) and stop burning frames.
function clearDeflection() {
  ring.hasBubble = false;
  if (ring.rafId != null) {
    cancelAnimationFrame(ring.rafId);
    ring.rafId = null;
  }
  renderRing(performance.now());
}

// ── Guidance steps ────────────────────────────────────────────────────────────

function buildApproachSteps(target) {
  const steps = [];
  if (target.system) steps.push(`${target.system} system`);
  if (target.body)   steps.push(`Go to ${target.body}`);
  if (target.zone && target.zone !== target.body) {
    steps.push(`Zone: ${target.zone}`);
  }
  steps.push(`Local coords: ${fmt(target.x)}, ${fmt(target.y)}, ${fmt(target.z)}`);
  return steps;
}

function renderGuidance(steps) {
  const el = document.getElementById('guidance-steps');
  el.innerHTML = '';
  if (!steps.length) {
    el.innerHTML = '<li data-step="—" style="color:var(--text-dim)">No target set</li>';
    return;
  }
  steps.forEach((txt, i) => {
    const li = document.createElement('li');
    li.setAttribute('data-step', i + 1);
    li.textContent = txt;
    el.appendChild(li);
  });
}

// ── Position box ──────────────────────────────────────────────────────────────

function renderPosition(pos, stale) {
  const box   = document.getElementById('position-box');
  const title = box ? box.querySelector('.guidance-title') : null;
  const el    = document.getElementById('pos-grid');

  // Dim the whole box and flag it STALE while the reading is out of date.
  if (box) box.style.opacity = stale ? '0.55' : '';
  if (title) {
    title.innerHTML = stale
      ? 'Current Position <span style="color:var(--yellow);letter-spacing:1px">STALE</span>'
      : 'Current Position';
  }

  if (!pos) {
    el.textContent = '—';
    return;
  }
  const rows = [
    ['X', fmt(pos.x)],
    ['Y', fmt(pos.y)],
    ['Z', fmt(pos.z)],
  ];
  if (pos.precision?.mode === 'cruise') rows.push(['Precision', 'Coarse estimate; fractional digits omitted']);
  if (pos.precision?.mode === 'approach') rows.push(['Precision', 'Final approach; slow down']);
  rows.push(['Read age', state.positionAgeS == null
    ? 'Unknown' : Math.max(0, state.positionAgeS).toFixed(1) + ' s']);
  if (pos.zone) rows.push(['Zone', pos.zone]);
  rows.push(['Body', pos.body ? pos.body : 'No local frame']);

  el.innerHTML = rows.map(([k, v]) =>
    `<div class="pos-item"><span class="pos-key">${k}</span><span class="pos-val">${escHtml(v)}</span></div>`
  ).join('');
}

// ── Actions ───────────────────────────────────────────────────────────────────

async function setTarget(id) {
  try {
    const r = await fetch(`/api/nav/target/${id}`, { method: 'POST' });
    if (!r.ok) {
      let msg = 'Error setting target';
      try {
        const data = await r.json();
        if (data && data.error) msg = data.error;
      } catch (_) { /* non-JSON body */ }
      setStatus(msg);
      return;
    }
    setStatus('Navigation target set');
    await pollNavState();
  } catch (e) { setStatus('Error setting target'); }
}

async function clearTarget() {
  try {
    const r = await fetch('/api/nav/target', { method: 'DELETE' });
    // An unchecked response was why this looked like a dead button: a refused
    // or errored request fell straight through to "Target cleared".
    if (!r.ok) { setStatus(`Clear failed: HTTP ${r.status}`); return; }
    state.activeTarget = null;
    renderNavPanel();
    renderWaypointList();
    setStatus('Target cleared');
  } catch (e) {
    setOnline(false);
    setStatus('Clear failed: no answer from the skill');
  }
}

async function deleteWaypoint(id) {
  const wp = state.navpoints.find(w => w.id === id);
  const name = wp ? wp.name : `#${id}`;
  if (!confirm(`Delete "${name}"?`)) return;
  try {
    const r = await fetch(`/api/navpoints/${id}`, { method: 'DELETE' });
    if (!r.ok) { setStatus(`Delete failed: HTTP ${r.status}`); return; }
    setStatus(`Deleted: ${name}`);
    await refreshWaypoints();
  } catch (e) {
    setOnline(false);
    setStatus('Delete failed: no answer from the skill');
  }
}

function startRename(id) {
  const nameEl = document.getElementById(`wp-name-${id}`);
  const editEl = document.getElementById(`wp-edit-${id}`);
  if (!nameEl || !editEl) return;
  nameEl.style.display = 'none';
  editEl.style.display = 'block';
  editEl.focus();
  editEl.select();
}

async function saveRename(id) {
  const nameEl = document.getElementById(`wp-name-${id}`);
  const editEl = document.getElementById(`wp-edit-${id}`);
  if (!nameEl || !editEl) return;
  const newName = editEl.value.trim();
  if (newName) {
    try {
      await fetch(`/api/navpoints/${id}/name`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      setStatus(`Renamed to: ${newName}`);
    } catch (e) { setStatus('Error renaming'); }
  }
  editEl.style.display = 'none';
  nameEl.style.display = '';
  await refreshWaypoints();
}

function cancelRename(id) {
  const nameEl = document.getElementById(`wp-name-${id}`);
  const editEl = document.getElementById(`wp-edit-${id}`);
  if (!nameEl || !editEl) return;
  editEl.style.display = 'none';
  nameEl.style.display = '';
}

// ── Utility ───────────────────────────────────────────────────────────────────

function setNavRow(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

// Distance readout with an optional green arrival tag, styled like the STALE
// tag on the position box. ARRIVED takes precedence over APPROACHING; each tag
// shows only while its threshold is enabled (greater than zero).
function renderDistance(distanceKm) {
  const el = document.getElementById('nav-distance');
  if (!el) return;
  const p = state.currentPos?.precision;
  const matches = p && p.target_id === state.activeTarget?.id;
  const coarse = matches && p.mode === 'cruise';
  const slow = matches && p.slow_down_advisory;
  el.innerHTML = escHtml((coarse ? 'Approximately ' : '') + formatDist(distanceKm))
    + (coarse ? ' · COARSE' : arrivalTagHtml(distanceKm * 1000))
    + (slow ? ' · Slow down for final approach' : '');
}

function arrivalTagHtml(distM) {
  if (state.arrivalStopM > 0 && distM <= state.arrivalStopM) {
    return ' <span style="color:var(--green);letter-spacing:1px">ARRIVED</span>';
  }
  if (state.arrivalAlertM > 0 && distM <= state.arrivalAlertM) {
    return ' <span style="color:var(--green);letter-spacing:1px">APPROACHING</span>';
  }
  return '';
}

let statusTimer = null;

function setStatus(msg) {
  const el = document.getElementById('status-bar');
  if (!el) return;
  el.textContent = msg;
  // One timer, not one per call: overlapping timers used to reset the bar
  // three seconds after the FIRST of a burst of messages, clipping the rest.
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { el.textContent = idleStatus(); }, 3000);
}

// What the bar falls back to when nothing is happening. Never "Ready" while the
// skill is unreachable: the whole point is that the page stops claiming health
// it cannot verify.
function idleStatus() {
  return state.online ? 'Ready' : OFFLINE_MSG;
}

function setOnline(online) {
  if (state.online === online) return;
  state.online = online;
  document.body.classList.toggle('hud-offline', !online);
  const el = document.getElementById('status-bar');
  if (el) el.textContent = idleStatus();
}

function fmt(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
