# 1.2.1 — 2026-10-26

- Show spoken tablet reactions and emote feedback once in Wingman's HUD chat at the speech hook.
- Add the live Show tablet reactions in the HUD setting, on by default; respect Wingman's HUD switches.
- Keep ordinary voice replies, silent taps, feedback modes, client chat and conversation notes unchanged.

# 1.2.0 — 2026-10-26

- First public GPL-3.0 release, generic player-facing text and full attribution.
- Deck setup is opt-in for fresh players; earlier pack installs retain setup.
- Not-set-up buttons are inert; the startup card explains consent and backups.
- Official hash-verified OpenCV Windows binaries ship in the reproducible ZIP.

# Changelog

## 1.1.1 — 2026-10-26

- Follow the Star Citizen window's monitor for the ring overlay, including re-shows and monitor moves.
- Keep display as a fallback only; apply display/size settings live through ring-only stop/start.
- Derive absolute on/off command pairs from verified action identities across all layouts.
- Choose the explicit command from the remembered light at dispatch; fall back once only if missing.
- Add layout-scoped Fix a light switches and the public correct_tablet_light tool; neither presses keys.
- Migrate exactly eleven pack-owned numpad/Page Up emotes to collision-free single-modifier plain keys.
- Exclude numpad and extended navigation keys from all new allocations.
- Verify all 34 emotes against the profile, refuse unbound tablet emotes, and speak no-key notices.
- Say emote sent, not done; remind once per Wingman session that emotes play only on foot.
- Preserve user edits, CRLF and backups; defer profile writes while SC runs and roll back failed saves.

## 1.1.0 — 2026-10-26

- Integrate NAV guidance and waypoint management into every deck layout.
- Add themed compass, honest stale/unknown readouts, route/position and body filter.
- Add rename, notes and confirmed deletion; refresh lists from any writer via WS revision.
- Remove focus-taking HUD/dashboard buttons from the deck; retain the PC voice HUD.
- Use durable pairing-code QR links without removing legacy share sessions.
- Make overlay window classes safe across module reloads and retain native callbacks.
- Summarize unverifiable drift entries once per check.
- Preserve installed 1.0 commands and binds; add portable browser/private-upgrade qualification.

## 1.0.0 — 2026-10-26

- Combine independently namespaced keybinding and NavPoint ports into one v3 skill.
- Add authenticated Tablet Deck UI, NAV panel/status, two-way command-light sync,
  serialized tablet commands, focus-checked holds, PC mic hold and queued feedback.
- Integrate deterministic deck-pack planning, guarded bind restoration, command
  save retry, drift manifests and trailing-sync-aware self-heal.
- Preserve the NavPoint token and one listener on port 7869; navigation moves to /nav.
- Add dry-run migration/command rewiring, reproducible release and portable qualification.
- Keep old skills and SetRouteAI independent; no deploy or live-PC verification.
