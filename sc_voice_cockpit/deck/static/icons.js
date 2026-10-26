// Line icons for the deck, drawn on a 48x48 grid with currentColor.
// Strokes use the theme's --icon-stroke width; `fill="currentColor"` parts are solid accents.
window.DECK_ICONS = {
  ship: `<path d="M24 5 30 21 42 28v4l-13-2-2 11h-6l-2-11-13 2v-4l12-7Z"/><path d="M24 13v9" opacity=".6"/>`,

  "flight-ready": `<path d="M24 9 28.5 21 37 26v3l-9.5-1.5L26 35h-4l-1.5-7.5L11 29v-3l8.5-5Z"/>
    <path d="M9 39a19 19 0 0 1-3-6M39 39a19 19 0 0 0 3-6"/><path d="M17 43h14"/>
    <circle cx="24" cy="18" r="1.6" fill="currentColor" stroke="none"/>`,

  power: `<path d="M24 6v15"/><path d="M15.5 12.5a14 14 0 1 0 17 0"/>
    <circle cx="24" cy="27" r="3" fill="currentColor" stroke="none" opacity=".35"/>`,

  gear: `<path d="M8 10h32l-5 6H13Z"/><path d="M24 16v12"/><path d="m24 28-9 7m9-7 9 7"/>
    <path d="M10 38h10M28 38h10"/><circle cx="15" cy="35" r="1.5" fill="currentColor" stroke="none"/><circle cx="33" cy="35" r="1.5" fill="currentColor" stroke="none"/>`,

  "request-landing": `<ellipse cx="24" cy="37" rx="17" ry="5"/><path d="M19 37h10M24 34v6" opacity=".6"/>
    <path d="M24 7v18m-6-6 6 6 6-6"/><path d="M33 9a8 8 0 0 1 5 5M34 4a13 13 0 0 1 9 9"/>`,

  vtol: `<path d="M6 18h36l-7 7H13Z"/><path d="M17 25v4M31 25v4"/>
    <path d="M15 32v9M19 32v6M29 32v6M33 32v9" opacity=".75"/><path d="M24 8v6m-3-3 3-3 3 3"/>`,

  lights: `<path d="M16 13a11 11 0 0 0 0 22Z"/><path d="M16 13v22"/>
    <path d="m23 14 18-5M23 24h19M23 34l18 5"/>`,

  "quantum-mode": `<circle cx="24" cy="24" r="4" fill="currentColor" stroke="none"/>
    <ellipse cx="24" cy="24" rx="19" ry="7" transform="rotate(-25 24 24)"/>
    <ellipse cx="24" cy="24" rx="19" ry="7" transform="rotate(35 24 24)" opacity=".7"/>
    <circle cx="24" cy="24" r="10" stroke-dasharray="2 4" opacity=".55"/>`,

  "quantum-jump": `<path d="M30 12 42 24 30 36"/><path d="M6 24h30"/>
    <path d="M10 16h16M10 32h16" opacity=".7"/><path d="M4 20h6M4 28h6M16 12h6M16 36h6" opacity=".4"/>`,

  "exit-seat": `<path d="M12 6v21a5 5 0 0 0 5 5h11"/><path d="M12 14h5v12h9"/><path d="M18 32v10M26 32v10M14 42h16"/>
    <path d="M30 18h13m-5-5 5 5-5 5"/>`,

  decoy: `<circle cx="24" cy="24" r="4" fill="currentColor" stroke="none"/>
    <path d="M24 6v8M24 34v8M6 24h8M34 24h8M11.3 11.3l5.6 5.6M31.1 31.1l5.6 5.6M11.3 36.7l5.6-5.6M31.1 16.9l5.6-5.6"/>
    <circle cx="36" cy="10" r="1.6" fill="currentColor" stroke="none"/><circle cx="10" cy="38" r="1.6" fill="currentColor" stroke="none"/>`,

  noise: `<path d="M4 24c3 0 3-12 6-12s3 24 6 24 3-24 6-24 3 24 6 24 3-24 6-24 3 12 6 12"/>
    <path d="M4 40h40M4 8h40" opacity=".35"/>`,

  "power-weapons": `<circle cx="24" cy="24" r="13"/><circle cx="24" cy="24" r="2.5" fill="currentColor" stroke="none"/>
    <path d="M24 4v10M24 34v10M4 24h10M34 24h10"/>`,

  "power-engines": `<path d="M14 6h20l-4 15H18Z"/><path d="M16 12h16" opacity=".5"/>
    <path d="M18 26c0 7 6 10 6 17 0-7 6-10 6-17"/><path d="M21 26c0 4 3 6 3 10 0-4 3-6 3-10" opacity=".6"/>`,

  "power-shields": `<path d="M24 5 39 10v11c0 11-6.5 18-15 22-8.5-4-15-11-15-22V10Z"/>
    <path d="M24 13 32 16v6c0 6-3.5 10-8 12.5-4.5-2.5-8-6.5-8-12.5v-6Z" opacity=".55"/>`,

  "power-reset": `<path d="M38 20A15 15 0 0 0 11 14"/><path d="M10 6v8h8"/>
    <path d="M10 28a15 15 0 0 0 27 6"/><path d="M38 42v-8h-8"/>
    <circle cx="24" cy="24" r="3" fill="currentColor" stroke="none"/>`,

  mining: `<path d="m28 18 10 5-3 17H23l-4-12Z"/><path d="m28 18-3 10 10 12M25 28l-6 0" opacity=".55"/>
    <path d="M5 8 21 22"/><path d="M5 14l6-6" opacity=".6"/><circle cx="21" cy="22" r="2" fill="currentColor" stroke="none"/>`,

  // Mining laser: an emitter firing a beam into a rock.
  laser: `<path d="M5 33 15 23l6 6-10 10Z"/><path d="M18 26 31 13" stroke-width="4" opacity=".55"/>
    <path d="m33 7 9 5-2 11-9 2-5-8Z"/><circle cx="31" cy="13" r="2.4" fill="currentColor" stroke="none"/>`,

  // Mining module: a consumable canister.
  module: `<rect x="15" y="10" width="18" height="30" rx="4"/><path d="M19 6h10M24 6v4"/>
    <path d="M15 20h18M15 30h18" opacity=".55"/><circle cx="24" cy="25" r="2.4" fill="currentColor" stroke="none"/>`,

  scanning: `<circle cx="24" cy="24" r="18"/><circle cx="24" cy="24" r="10" opacity=".55"/>
    <path d="M24 24 37 11"/><path d="M24 6v4M24 38v4M6 24h4M38 24h4" opacity=".55"/>
    <circle cx="31" cy="29" r="2" fill="currentColor" stroke="none"/><circle cx="17" cy="16" r="1.5" fill="currentColor" stroke="none"/>`,

  turret: `<path d="M8 38h32"/><path d="M13 38a11 11 0 0 1 22 0"/><path d="M19 30h6"/>
    <path d="m27 28 13-12M29 31l13-12"/><path d="M14 42h20" opacity=".5"/>`,

  doors: `<rect x="7" y="6" width="34" height="36" rx="1.5"/><path d="M24 6v36"/>
    <path d="m17 20-4 4 4 4M31 20l4 4-4 4"/><path d="M7 42h34" stroke-width="4" opacity=".45"/>`,

  mobiglas: `<path d="M24 7 38 15v16l-14 8-14-8V15Z"/><path d="M24 15l7 4v8l-7 4-7-4v-8Z" opacity=".6"/>
    <path d="M10 15l14 8 14-8M24 23v16" opacity=".35"/>`,

  starmap: `<circle cx="22" cy="24" r="9"/><ellipse cx="22" cy="24" rx="19" ry="6" transform="rotate(-18 22 24)"/>
    <circle cx="40" cy="12" r="2" fill="currentColor" stroke="none"/><circle cx="7" cy="38" r="1.5" fill="currentColor" stroke="none"/>
    <circle cx="38" cy="38" r="1" fill="currentColor" stroke="none"/>`,

  inventory: `<path d="M24 5 41 14v20L24 43 7 34V14Z"/><path d="M7 14l17 9 17-9M24 23v20"/>
    <path d="m15.5 9.5 17 9v7" opacity=".55"/>`,

  contacts: `<circle cx="18" cy="16" r="6"/><path d="M6 40c0-8 5-13 12-13s12 5 12 13"/>
    <circle cx="34" cy="14" r="4" opacity=".6"/><path d="M33 23c6 0 10 4 10 11" opacity=".6"/>`,

  camera: `<circle cx="24" cy="18" r="5"/><path d="M15 38c0-7 4-11 9-11s9 4 9 11"/>
    <path d="M5 13V5h8M35 5h8v8M43 35v8h-8M13 43H5v-8"/>`,

  escape: `<path d="M20 8H9v32h11"/><path d="M19 24h22m-7-7 7 7-7 7"/>`,

  speed: `<path d="M8 34a17 17 0 1 1 32 0"/><path d="M24 34 33 19"/><circle cx="24" cy="34" r="3" fill="currentColor" stroke="none"/>
    <path d="M12 26h3M16 17l2 2M24 13v3M32 17l-2 2M36 26h-3" opacity=".6"/>`,

  target: `<path d="M6 16V6h10M32 6h10v10M42 32v10H32M16 42H6V32"/><circle cx="24" cy="24" r="7"/>
    <circle cx="24" cy="24" r="2" fill="currentColor" stroke="none"/>`,

  missile: `<path d="M34 6c5 0 8 3 8 8L22 34l-8-8Z"/><path d="m14 26-6 2 2-8 8-2M22 34l-2 6 8-2 2-8" opacity=".7"/>
    <path d="m10 38-4 4M14 40l-2 2M8 34l-2 2" opacity=".55"/>`,

  salvage: `<path d="M8 40 20 28"/><path d="M20 28 34 14l4 4-14 14Z"/><path d="m34 14 4-8 4 4-8 4"/>
    <path d="M26 40h14M30 34h10" opacity=".55"/>`,

  eye: `<path d="M4 24s7-12 20-12 20 12 20 12-7 12-20 12S4 24 4 24Z"/><circle cx="24" cy="24" r="6"/>
    <circle cx="24" cy="24" r="2" fill="currentColor" stroke="none"/>`,

  lock: `<rect x="10" y="22" width="28" height="20" rx="3"/><path d="M16 22v-6a8 8 0 0 1 16 0v6"/>
    <circle cx="24" cy="31" r="2.5" fill="currentColor" stroke="none"/><path d="M24 33v4"/>`,

  unlock: `<rect x="10" y="22" width="28" height="20" rx="3"/><path d="M16 22v-6a8 8 0 0 1 15.5-2.8"/>
    <circle cx="24" cy="31" r="2.5" fill="currentColor" stroke="none"/><path d="M24 33v4"/>`,

  emote: `<circle cx="24" cy="24" r="18"/><path d="M16 29c2 4 5 6 8 6s6-2 8-6"/>
    <circle cx="18" cy="19" r="2" fill="currentColor" stroke="none"/><circle cx="30" cy="19" r="2" fill="currentColor" stroke="none"/>`,

  walk: `<circle cx="27" cy="8" r="4"/><path d="m24 15-6 12 8 5 2 12M24 15l6 8 7 2M18 27l-6 14M21 20l-8 3-2 7"/>`,

  drop: `<path d="M24 6v20m-7-7 7 7 7-7"/><path d="M10 30v10h28V30"/><path d="M16 36h16" opacity=".55"/>`,

  "chev-up": `<path d="m12 30 12-12 12 12"/>`,
  "chev-down": `<path d="m12 18 12 12 12-12"/>`,
  "chev-max": `<path d="M10 8h28"/><path d="m12 26 12-12 12 12M12 38l12-12 12 12"/>`,
  "chev-min": `<path d="M10 40h28"/><path d="m12 10 12 12 12-12M12 22l12 12 12-12"/>`,
  "chev-left": `<path d="M30 10 16 24l14 14"/>`,
  "chev-right": `<path d="m18 10 14 14-14 14"/>`,
  chat: `<path d="M7 9h34v24H20l-9 7v-7H7Z"/><circle cx="17" cy="21" r="1.8" fill="currentColor" stroke="none"/><circle cx="24" cy="21" r="1.8" fill="currentColor" stroke="none"/><circle cx="31" cy="21" r="1.8" fill="currentColor" stroke="none"/>`,
  party: `<circle cx="17" cy="17" r="6"/><circle cx="32" cy="18" r="5"/><path d="M5 38c0-7 5-12 12-12s12 5 12 12M29 27c6 0 11 4 11 11"/>`,
  sliders: `<path d="M12 6v36M24 6v36M36 6v36"/><rect x="8" y="12" width="8" height="6" rx="1.5" fill="currentColor" stroke="none"/><rect x="20" y="28" width="8" height="6" rx="1.5" fill="currentColor" stroke="none"/><rect x="32" y="18" width="8" height="6" rx="1.5" fill="currentColor" stroke="none"/>`,
  grid: `<rect x="7" y="7" width="14" height="14" rx="2"/><rect x="27" y="7" width="14" height="14" rx="2"/><rect x="7" y="27" width="14" height="14" rx="2"/><path d="M27 27h6v6h-6zM35 35h6v6h-6z" fill="currentColor" stroke="none"/>`,
  mic: `<rect x="17" y="5" width="14" height="24" rx="7"/><path d="M10 22a14 14 0 0 0 28 0M24 36v7M17 43h14"/>
    <path d="M17 15h6M17 21h6" opacity=".55"/>`,

  swap: `<path d="M8 16h28m-6-6 6 6-6 6M40 32H12m6-6-6 6 6 6"/>`,

  help: `<circle cx="24" cy="24" r="17"/><path d="M18.5 19a5.5 5.5 0 1 1 8 4.9c-1.8.9-2.5 2-2.5 3.6V29"/>
    <circle cx="24" cy="35" r="1.8" fill="currentColor" stroke="none"/>`,

  default: `<path d="M24 5 40 14v20L24 43 8 34V14Z"/><circle cx="24" cy="24" r="5" fill="currentColor" stroke="none" opacity=".6"/>`,
};

window.deckIcon = function deckIcon(name) {
  const body = window.DECK_ICONS[name] || window.DECK_ICONS.default;
  return `<svg viewBox="0 0 48 48" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
};
