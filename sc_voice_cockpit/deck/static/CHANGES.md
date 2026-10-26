# 1.2.1 — 2026-10-26

- Show spoken tablet reactions and emote feedback once in Wingman's HUD chat at the speech hook.
- Add the live Show tablet reactions in the HUD setting, on by default; respect Wingman's HUD switches.
- Keep ordinary voice replies, silent taps, feedback modes, client chat and conversation notes unchanged.

# 1.2.0 — 2026-10-26

- First public GPL-3.0 release, generic player-facing text and full attribution.
- Deck setup is opt-in for fresh players; earlier pack installs retain setup.
- Not-set-up buttons are inert; the startup card explains consent and backups.
- Official hash-verified OpenCV Windows binaries ship in the reproducible ZIP.

# Tablet Deck front-end changes

Baseline: upstream/tablet-deck/public at the commit in PROVENANCE.md.
No upstream source, fonts, external resources or unrelated styles were changed.

## 1.1.1

- index.html adds Fix a light beside Reset Lights in settings. app.js lists exactly
  the current layout's stateful groups and live ON/OFF switches, sends only
  set-state, and updates from server broadcasts. Reset Lights remains unchanged.
- deck.css uses theme tokens for this section and switch states, explicit text,
  44px-or-larger targets, large text, high contrast and existing reduced-motion
  transitions. The phone section spans the settings grid with responsive columns.
- app.js labels unavailable emotes No key set and displays the server notice.
  No-key text is inside the existing button label, not an extra competing column.
- i18n.js covers English, Spanish, French and German for new labels and notices.
- Pair selection remains entirely on the server; browser buttons still send tap.
- No server-auth, NavPoint, fonts or external resource changes.

## 1.1.0

- Fix round 1: app.js anchors the NAV micro-label below the measured connection
  text, with a downward 44px hit area bounded before header controls; grid flow
  reserves its height. navdesk.css inherits connection case/tracking. Desk/sheet
  headings reuse hud-header markup and typography with static positioning;
  their zoom follows the canvas so their rendered type size also matches.
  navdesk.js encodes waypoint ids in API paths. app.js removes only the saved
  pairing `t` query parameter after successful authentication/storage.

- navdesk.js (new): shared NAV / WAYPOINTS renderer for canvas and grid, using
  NavPoint's existing REST API and once-a-second nav push. API requests include
  the pairing code as `t`, including same-origin mutations. A content revision
  refreshes the list after changes from any source; retries are limited to one
  list request per second, except explicit user actions/layout initialization.
- NAV: target/frame/arrival, themed direction ring, distance/direction/elevation/
  bearing, route and compact current position. It only draws server-permitted,
  fresh direction; stale/missing/unknown data are labelled, including read age.
  A disconnected/silent push stream loses direction after 3.5 seconds. Scroll
  position is retained on pushes. Mark/Clear use existing manifest NAV dispatch;
  Reset Lights uses the existing reset path. No new game input handlers.
- WAYPOINTS: body filter, scrollable API rows (name, frame/location, coordinates,
  date), active-target label, tap-to-target. More opens a themed sheet with Set
  Target, Rename, Notes and Delete; Delete requires a separate Confirm Delete.
  Saves have an explicit pending state. The sheet traps keyboard focus, supports
  Escape and returns focus to the opener; all user data use text nodes.
- navdesk.css (new): theme tokens and existing fonts only, thin lines and
  // HEADERS, meaningful blue/red action edges, native theme case/weight,
  large-text/high-contrast/no-lights support. Desk controls stay >=44px.
  Phone ring/readouts use a compact two-column instrument; all three action
  buttons share one row. Guidance/list scroll inside the panel; actions stay reachable. Content
  remains below the night layer; the sheet is at 10, like the theme picker,
  above night (9) and below toast (20)/intro (50). Reduced motion disables
  compass animation and transitions.
- index.html: load the two exact-allowlisted desk assets; replace the /nav link
  with a button that opens the NAV panel. Retain the status second line.
- app.js: pass nav pushes/connection state to the desk; add grid mount and
  status-to-NAV hooks. Ignore /nav opened messages instead of navigating.
  Remove no other opened-message behavior. Size the status hitbox using the
  actual canvas scale; retain its position beneath connection status.
- canvas.js: mount the desk for navdesk panels, add showPanel, and counter canvas
  scaling for real 44px controls. Re-measure on resize while preserving panel
  selection. All existing canvas button/hold/gesture behavior is unchanged.
- i18n.js: add Spanish, French and German entries for all new desk UI text.
  User-authored waypoint names/notes and in-game voice phrases are not translated.
- Layout production is confined to deck_pack/planner.py::_panels. NAV/WAYPOINTS
  replace the four-button NAV grid in the right region of all twelve canvases;
  the thirteenth, tabbed grid layout gets both in its NAV page. The manifest
  retains Mark/Clear/Reset definitions but no Show HUD/Open Nav Dashboard.
- The PC-only /nav HTML/JS/CSS and route bodies are unchanged. Its existing link
  back to the deck remains; the voice HUD tool remains available.

## 1.0.0 history

- index.html added a /nav link and NAV status; app.js placed it below the
  connection status, rendered nav state and handled opened-/nav navigation.
  Version 1.1 replaces that link/navigation behavior above.
- app.js selects ws/wss from the page scheme and tries the initial layout load
  with an HttpOnly cookie, even without a saved pairing code.
- No editor/update routes are served. Other baseline assets remain unmodified.
