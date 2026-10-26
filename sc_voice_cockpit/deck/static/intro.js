// Intro screen: starfield drift, the manufacturer emblem draws itself, then a tap
// triggers a quantum-jump warp into the deck. Runs once per page load.
(() => {
  const intro = document.getElementById("intro");
  if (!intro) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- starfield ----------
  const cv = document.getElementById("introStars");
  const ctx = cv.getContext("2d");
  let w = 0, h = 0, dpr = 1;
  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = intro.clientWidth; h = intro.clientHeight;
    cv.width = w * dpr; cv.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  resize();
  window.addEventListener("resize", resize);

  // Stars live in 3D: x/y in [-1, 1], z in (0, 1]; projected from the centre.
  const stars = Array.from({ length: 320 }, () => ({ x: Math.random() * 2 - 1, y: Math.random() * 2 - 1, z: Math.random() }));
  const accent = () => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#4fb3ff";
  let speed = 0.0016;          // drift; ramps up during the warp
  let warpT0 = null;
  let running = true;

  function frame(now) {
    if (!running) return;
    const cx = w / 2, cy = h / 2, scale = Math.max(w, h) * 0.6;
    const warping = warpT0 !== null;
    if (warping) speed = Math.min(0.09, 0.0016 + ((now - warpT0) / 1000) ** 2 * 0.25);
    ctx.clearRect(0, 0, w, h);
    const col = accent();
    for (const s of stars) {
      const pz = s.z;
      s.z -= speed;
      if (s.z <= 0.02) { s.x = Math.random() * 2 - 1; s.y = Math.random() * 2 - 1; s.z = 1; continue; }
      const x = cx + (s.x / s.z) * scale * 0.5, y = cy + (s.y / s.z) * scale * 0.5;
      if (x < -50 || x > w + 50 || y < -50 || y > h + 50) { s.z = 0; continue; }
      const px = cx + (s.x / pz) * scale * 0.5, py = cy + (s.y / pz) * scale * 0.5;
      const bright = Math.min(1, (1 - s.z) * 1.4);
      ctx.strokeStyle = warping && bright > 0.5 ? col : `rgba(255,255,255,${bright})`;
      ctx.lineWidth = Math.max(0.6, (1 - s.z) * (warping ? 2.4 : 1.8));
      ctx.beginPath();
      ctx.moveTo(px, py);
      // During the warp, stretch each star back along its path into a streak.
      ctx.lineTo(warping ? x + (x - px) * 2 : x + 0.1, warping ? y + (y - py) * 2 : y + 0.1);
      ctx.stroke();
    }
    requestAnimationFrame(frame);
  }
  if (!reduced) requestAnimationFrame(frame);

  // ---------- emblem draws itself ----------
  // Stroked parts trace in; solid parts fade in after.
  requestAnimationFrame(() => {
    const parts = intro.querySelectorAll(".intro-emblem svg *");
    let i = 0;
    for (const el of parts) {
      if (typeof el.getTotalLength !== "function") continue;
      const solid = el.getAttribute("stroke") === "none" || el.getAttribute("fill") === "currentColor";
      const delay = 0.35 + i++ * 0.08;
      if (solid) {
        el.style.opacity = "0";
        el.style.transition = `opacity .5s ease ${delay + 0.5}s`;
        requestAnimationFrame(() => (el.style.opacity = el.getAttribute("opacity") || "1"));
      } else {
        const len = el.getTotalLength();
        el.style.strokeDasharray = `${len}`;
        el.style.strokeDashoffset = `${len}`;
        el.style.transition = `stroke-dashoffset 1.1s cubic-bezier(.6, 0, .2, 1) ${delay}s`;
        requestAnimationFrame(() => (el.style.strokeDashoffset = "0"));
      }
    }
  });

  // Manufacturer line under the title.
  for (const m of intro.querySelectorAll(".js-intro-maker")) m.textContent = document.documentElement.dataset.maker || "";

  // ---------- tap to continue ----------
  let leaving = false;
  function go() {
    if (leaving) return;
    leaving = true;
    if (navigator.vibrate) navigator.vibrate([15, 40, 30]);
    // Touch devices go fullscreen where the browser allows it (not iPhone Safari).
    if (navigator.maxTouchPoints > 0 && !document.fullscreenElement && document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(() => {});
    }
    if (reduced) { finish(); return; }
    warpT0 = performance.now();
    intro.classList.add("warping");
    setTimeout(() => intro.classList.add("flash"), 750);
    setTimeout(() => intro.classList.add("out"), 950);
    setTimeout(finish, 1450);
  }
  function finish() {
    running = false;
    intro.classList.add("gone");
    window.removeEventListener("resize", resize);
  }
  intro.addEventListener("pointerup", go);
  intro.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") go(); });
  intro.focus?.();
})();
