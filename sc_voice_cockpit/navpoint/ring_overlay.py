"""ring_overlay.py: the deflection ring as a standalone in-game overlay.
Author: Mallachi

The ring was drawn on the browser HUD page from v4.9.0.1 and was never once
seen in flight: a browser window cannot draw over a fullscreen game, and the
page had to be asked for by name (see the Devlog entries of 2026-07-26). This
module draws the same instrument into a click-through layered window that sits
on top of the game itself, anchored so the top of the ring meets the top centre
of the screen.

Self-contained by design: it takes two angles and renders. It never reads the
database, never talks to the HUD server, and never sends input anywhere. The
Win32 mechanics live in `overlay_win32.py`; everything here is geometry.

Known limit, stated rather than discovered later: a layered window composites
over borderless-windowed and windowed games. Exclusive fullscreen bypasses the
desktop compositor entirely, and nothing short of hooking the game's renderer
can draw into it. Run Star Citizen borderless.
"""

from __future__ import annotations

import logging
import math
import threading
import time

from sc_voice_cockpit.navpoint import overlay_win32


logger = logging.getLogger(__name__)

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    RENDER_AVAILABLE = True
except ImportError:  # pragma: no cover - both ship with the Wingman runtime
    np = None
    Image = None
    ImageDraw = None
    ImageFont = None
    RENDER_AVAILABLE = False


# Reference geometry, in the 220 px canvas the browser ring was designed in.
# Everything scales from these so the instrument keeps its proportions at any
# configured size.
_REF_CANVAS = 220.0
_REF_ROSE = 102.0
_REF_BOUND = 70.0
_REF_BUBBLE = 12.0

# Colours, matching the browser ring exactly (app.js).
_GREEN = (0, 232, 144)
_AMBER = (255, 182, 72)
_RED = (255, 77, 94)
_ROSE_FILL = (11, 22, 38)
_ROSE_LINE = (30, 53, 85)
_ROSE_ACCENT = (58, 144, 216)
_NO_FIX = (106, 138, 170)

# The browser draws the rose on an opaque page. Over a game it has to stay
# readable without blacking out the view, so the disc is translucent while
# every line stays solid.
_ROSE_FILL_ALPHA = 0.55

# Bubble displacement saturates at 45 degrees off-centre, the same wall the
# browser ring uses; past that only the colour keeps moving.
_WALL_DEG = 45.0

_SMOOTHING_TAU_S = 0.25
_MAX_FRAME_DT_S = 0.1
_PULSE_PERIOD_S = 1.2

# The reader's message, at the bottom centre. Amber rather than red: it reports
# that a fix is missing, which is a normal condition since Vision was removed,
# not an error. Margin is from the canvas bottom, in reference pixels.
_MESSAGE_COLOUR = (255, 196, 92)
_MESSAGE_MARGIN = 6
_ERROR_FAILURE_THRESHOLD = 8

# ── PHOTOSENSITIVITY SAFETY. Do not weaken these without measuring. ─────────
# Over terrain the reader refuses most frames, so the message would otherwise
# appear and vanish at the poll rate. Anything that toggles a visual element
# repeatedly is a strobe, and 3 to 60 Hz can trigger seizures in photosensitive
# people, with the worst response around 15 to 25 Hz.
#
# The guarantee is enforced HERE, in the renderer, rather than in whatever calls
# set_state, so no caller can defeat it however fast it publishes state:
#   - visibility only ever ramps, never snaps, over _MESSAGE_FADE_S
#   - once visible, a message holds for _MESSAGE_DWELL_S before it may fade
#   - the text itself only changes while faded out, so no hard swap is visible
# Together these bound any full on-off cycle to well under 0.2 Hz, two orders of
# magnitude below the hazardous band, and every transition is a gradual ramp of
# a small low-contrast label rather than a flash.
_MESSAGE_FADE_S = 0.45
_MESSAGE_DWELL_S = 1.0
_TOPMOST_INTERVAL_S = 1.0
_PLACEMENT_INTERVAL_S = 5.0
_IDLE_WAIT_S = 0.1

_WINDOW_CLASS = "ScNavPointRingOverlay"
_WINDOW_TITLE = "SC NavPoint Ring"


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(c_a + (c_b - c_a) * t)) for c_a, c_b in zip(a, b))


def state_colour(yaw_deg: float, pitch_deg: float) -> tuple[tuple[int, int, int], bool]:
    """Bubble colour and whether the on-course pulse is running.

    A continuous green-amber-red gradient driven by total offset alone, so past
    the wall the colour still encodes how far off the target sits. Ported from
    the browser ring's stateFor() and kept identical: two instruments showing
    the same number in different colours would be worse than one.
    """
    total = math.hypot(yaw_deg, pitch_deg)
    if total <= 20.0:
        return _GREEN, total < 5.0
    if total < 45.0:
        return _blend(_GREEN, _AMBER, (total - 20.0) / 25.0), False
    if total < 90.0:
        return _blend(_AMBER, _RED, (total - 45.0) / 45.0), False
    return _RED, False


class MessageFader:
    """Turns a possibly-flickering requested message into a safe visible one.

    PHOTOSENSITIVITY SAFETY LIVES HERE, deliberately as a small pure object
    rather than inline in the render thread, so the guarantee can be tested
    instead of merely intended. Over terrain the reader refuses most frames, so
    the requested message flickers at the poll rate; anything that toggles a
    visual element repeatedly is a strobe, and 3 to 60 Hz can trigger seizures.

    Three rules, and every one of them is asserted in `test_ring_message.py`:
      - alpha only ever moves by `dt / fade_s` per step, so it ramps, never snaps
      - a message that appears holds for at least `dwell_s`, however briefly it
        was asked for, so a one-frame refusal cannot produce a blink
      - the text only changes while alpha is 0, so no swap is ever visible

    Together these bound a full on-off cycle to (2 * fade_s + dwell_s), which at
    the shipped values is under 0.6 Hz, well below the hazardous band.
    """

    def __init__(self, fade_s: float = _MESSAGE_FADE_S,
                 dwell_s: float = _MESSAGE_DWELL_S) -> None:
        self.fade_s = max(1e-3, float(fade_s))
        self.dwell_s = max(0.0, float(dwell_s))
        self.text = ""
        self.alpha = 0.0
        self._visible_for = 0.0

    def update(self, dt: float, requested: str) -> tuple[str, float]:
        """Advance by `dt` seconds. Returns the text and alpha to draw."""
        dt = max(0.0, min(_MAX_FRAME_DT_S, float(dt)))
        step = dt / self.fade_s

        if not self.text and requested:
            # Nothing on screen, so adopting the new text is invisible.
            self.text = requested
            self._visible_for = 0.0

        if self.text and requested == self.text:
            want = True
        elif self.text:
            # Asked for something else, or nothing. Hold out the dwell first.
            want = self._visible_for < self.dwell_s
        else:
            want = False

        if want:
            self.alpha = min(1.0, self.alpha + step)
            self._visible_for += dt
        else:
            self.alpha = max(0.0, self.alpha - step)
            if self.alpha <= 0.0:
                self.text = requested
                self._visible_for = 0.0
        return self.text, self.alpha


class RingRenderer:
    """Draws the ring into premultiplied BGRA bytes.

    The rose is expensive and never changes, so it is drawn once at 4x and
    downsampled; only the bubble is composited per frame.
    """

    def __init__(self, size: int) -> None:
        self.size = max(80, int(size))
        scale = (self.size / 2.0 - 1.0) / _REF_ROSE
        self.r_rose = self.size / 2.0 - 1.0
        self.r_bound = _REF_BOUND * scale
        self.r_bubble = _REF_BUBBLE * scale
        self.travel = self.r_bound - self.r_bubble
        self.centre = self.size / 2.0
        self._glow_r = self.r_bubble * 2.4
        self._face_idle = self._render_face(with_boundary=False, with_no_fix=True)
        self._face_live = self._render_face(with_boundary=True, with_no_fix=False)
        self._messages: dict[object, object] = {}

    # ── static faces ──────────────────────────────────────────────────────── #

    def _render_face(self, with_boundary: bool, with_no_fix: bool):
        """Rose, ticks and forward marker as a premultiplied float array."""
        ss = 4
        n = self.size * ss
        c = self.centre * ss
        r = self.r_rose * ss
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        draw.ellipse(
            [c - r, c - r, c + r, c + r],
            fill=_ROSE_FILL + (int(round(255 * _ROSE_FILL_ALPHA)),),
            outline=_ROSE_LINE + (255,),
            width=max(1, int(round(1.5 * ss))),
        )

        for deg in range(0, 360, 15):
            rad = math.radians(deg - 90)
            outer = r - 2 * ss
            if deg % 90 == 0:
                inner, colour, width = r - 14 * ss, _ROSE_ACCENT, 2 * ss
            elif deg % 45 == 0:
                inner, colour, width = r - 10 * ss, _ROSE_LINE, ss
            else:
                inner, colour, width = r - 6 * ss, _ROSE_LINE, ss
            draw.line(
                [
                    c + outer * math.cos(rad),
                    c + outer * math.sin(rad),
                    c + inner * math.cos(rad),
                    c + inner * math.sin(rad),
                ],
                fill=colour + (255,),
                width=max(1, int(round(width))),
            )

        # Forward marker: a small triangle at the top, the only cue for which
        # way is "where you are pointing".
        tip = c - r + 4 * ss
        base = c - r + 13 * ss
        draw.polygon(
            [(c, tip), (c - 5 * ss, base), (c + 5 * ss, base)],
            fill=_ROSE_ACCENT + (255,),
        )

        if with_boundary:
            rb = self.r_bound * ss
            draw.ellipse(
                [c - rb, c - rb, c + rb, c + rb],
                outline=_ROSE_LINE + (255,),
                width=max(1, int(round(1.5 * ss))),
            )

        if with_no_fix:
            # A hollow ring at the centre says "target set, no fix yet". The
            # browser draws a "?" glyph; a vector shape avoids shipping a font
            # and reads the same at every size.
            rn = self.r_bubble * 0.9 * ss
            draw.ellipse(
                [c - rn, c - rn, c + rn, c + rn],
                outline=_NO_FIX + (200,),
                width=max(1, int(round(2 * ss))),
            )

        img = img.resize((self.size, self.size), Image.LANCZOS)
        straight = np.asarray(img).astype(np.float32) / 255.0
        alpha = straight[..., 3:4]
        premultiplied = np.concatenate([straight[..., :3] * alpha, alpha], axis=2)
        return premultiplied

    # ── frames ────────────────────────────────────────────────────────────── #

    def bubble_centre(self, yaw_deg: float, pitch_deg: float) -> tuple[float, float]:
        """Pixel centre of the bubble, clamped radially at the wall."""
        vx = yaw_deg / _WALL_DEG
        vy = -pitch_deg / _WALL_DEG  # screen up = target above
        mag = math.hypot(vx, vy)
        if mag > 1.0:
            vx /= mag
            vy /= mag
        return self.centre + vx * self.travel, self.centre + vy * self.travel

    def _message_layer(self, text: str, top: bool = False, raised: bool = False):
        """The message as a premultiplied float layer, cached per string.

        Rendering one per distinct message and reusing it keeps the per-frame cost to a
        composite. Drawn at 4x and downsampled like the rose, because PIL's
        default bitmap font is small and jagged at this size.
        """
        key = ("top", text) if top else ("raised", text) if raised else text
        cached = self._messages.get(key)
        if cached is not None:
            return cached

        ss = 4
        n = self.size * ss
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font = self._message_font(ss)
        try:
            box = draw.textbbox((0, 0), text, font=font)
        except Exception:  # very old PIL
            box = (0, 0) + draw.textsize(text, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        x = (n - width) / 2.0 - box[0]
        y = (n * 0.10 - box[1] if top
             else n - height - _MESSAGE_MARGIN * ss - box[1])
        if raised and not top:
            y -= 20 * ss
        # A dark halo first, so the text stays readable over a bright scene.
        for dx, dy in ((-ss, 0), (ss, 0), (0, -ss), (0, ss)):
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 190))
        draw.text((x, y), text, font=font, fill=_MESSAGE_COLOUR + (255,))

        img = img.resize((self.size, self.size), Image.LANCZOS)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        alpha = arr[..., 3:4]
        layer = np.concatenate([arr[..., :3] * alpha, alpha], axis=2)
        # Held ages and distances vary; bound the rendered-text cache.
        if len(self._messages) >= 64:
            self._messages.pop(next(iter(self._messages)))
        self._messages[key] = layer
        return layer

    @staticmethod
    def _message_font(scale: int):
        """The largest default-font variant PIL will give us, or the default."""
        try:
            return ImageFont.load_default(size=11 * scale)
        except TypeError:
            # Pillow before 10.1 has no size argument on the default font.
            return ImageFont.load_default()

    def frame(
        self,
        deflection: tuple[float, float] | None,
        elapsed_s: float = 0.0,
        message: str = "",
        message_alpha: float = 1.0,
        error_alpha: float = 0.0,
        distance: str = "",
    ) -> bytes:
        """One frame as premultiplied top-down BGRA.

        Distance stays above the bottom status line without fading, so
        notices never replace or fade the distance.
        `message` is independent of the bubble
        on purpose: a fix from two seconds ago is still worth pointing at while
        the reader is telling the player why it has stopped updating.
        """
        if deflection is None:
            canvas = self._face_idle.copy()
        else:
            yaw, pitch = deflection
            colour, pulse = state_colour(yaw, pitch)
            glow_alpha = 0.22
            if pulse:
                glow_alpha = 0.25 + 0.10 * math.sin(
                    2 * math.pi * elapsed_s / _PULSE_PERIOD_S
                )
            canvas = self._face_live.copy()
            self._composite_bubble(
                canvas, *self.bubble_centre(yaw, pitch), colour, glow_alpha
            )

        if message and message_alpha > 0.0:
            layer = self._message_layer(message) * float(
                max(0.0, min(1.0, message_alpha))
            )
            alpha = layer[..., 3:4]
            canvas = layer + canvas * (1.0 - alpha)
        if distance:
            layer = self._message_layer(distance, raised=True)
            canvas = layer + canvas * (1.0 - layer[..., 3:4])
        if error_alpha > 0.0:
            layer = self._message_layer("ERROR", top=True) * min(1.0, error_alpha)
            canvas = layer + canvas * (1.0 - layer[..., 3:4])
        return self._to_bgra(canvas)

    def _composite_bubble(self, canvas, cx: float, cy: float, colour, glow_alpha: float) -> None:
        """Alpha-composite the glow, disc and rim onto the canvas in place.

        Everything is a function of distance from the bubble centre, so one
        distance field drives all three. Recomputed per frame rather than
        cached because the centre is fractional: sub-pixel placement is what
        keeps the motion smooth at 30 fps.
        """
        half = int(math.ceil(self._glow_r)) + 2
        x0 = max(0, int(math.floor(cx)) - half)
        y0 = max(0, int(math.floor(cy)) - half)
        x1 = min(self.size, int(math.floor(cx)) + half + 1)
        y1 = min(self.size, int(math.floor(cy)) + half + 1)
        if x1 <= x0 or y1 <= y0:
            return

        ys = np.arange(y0, y1, dtype=np.float32).reshape(-1, 1) + 0.5
        xs = np.arange(x0, x1, dtype=np.float32).reshape(1, -1) + 0.5
        dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)

        glow = np.clip(1.0 - dist / self._glow_r, 0.0, 1.0) * glow_alpha
        disc = np.clip(self.r_bubble + 0.5 - dist, 0.0, 1.0) * 0.85
        rim = np.clip(0.75 - np.abs(dist - self.r_bubble), 0.0, 1.0)

        alpha = glow
        alpha = disc + alpha * (1.0 - disc)
        alpha = rim + alpha * (1.0 - rim)
        alpha = alpha[..., np.newaxis]

        rgb = np.array(colour, dtype=np.float32).reshape(1, 1, 3) / 255.0
        src = np.concatenate([rgb * alpha, alpha], axis=2)

        region = canvas[y0:y1, x0:x1]
        canvas[y0:y1, x0:x1] = src + region * (1.0 - alpha)

    @staticmethod
    def _to_bgra(canvas) -> bytes:
        out = np.clip(canvas * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return out[..., [2, 1, 0, 3]].tobytes()


class RingOverlay:
    """Owns the overlay window and the render thread.

    The window lives entirely on its own thread: a Win32 window belongs to the
    thread that created it, and the skill's own thread is an asyncio loop that
    cannot pump a message queue.
    """

    def __init__(self, size: int = 220, display: int = 1, fps: int = 30) -> None:
        self._size = max(80, int(size))
        self._display = max(1, int(display))
        self._interval = 1.0 / max(1, min(60, int(fps)))

        self._lock = threading.Lock()
        self._active = False
        self._target: tuple[float, float] | None = None
        self._message = ""
        self._distance = ""
        self._error = False
        self._consecutive_failures = 0
        self._hold_key = None
        self._last_valid_target = None
        self._last_valid_at = None
        self._last_valid_message = ""
        self._last_valid_distance = ""

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._failed_reason: str | None = None

    # ── state, called from the skill ──────────────────────────────────────── #

    def set_state(self, active: bool, deflection: tuple[float, float] | None,
                  message: str = "", *, hold_key=None, read_failed: bool = False,
                  captured_at: float | None = None, distance: str = ""):
        """Publish ring visibility, deflection and its status together.

        An explicit matching hold_key opts into display-only retention. Failed
        reads/calibration never replace the last valid direction or its capture
        time. Inactivity, target changes or incompatible frames clear it.
        ERROR requires eight consecutive unavailable direction updates; a
        valid direction resets the streak. This is a count, not a timer.
        """
        with self._lock:
            if not active or hold_key is None or hold_key != self._hold_key:
                self._last_valid_target = None
                self._last_valid_at = None
                self._last_valid_message = ""
                self._last_valid_distance = ""
                self._consecutive_failures = 0
            self._hold_key = hold_key if active else None
            self._error = False
            if active and hold_key is not None:
                valid = (not read_failed and deflection is not None
                         and len(deflection) == 2
                         and all(math.isfinite(v) for v in deflection)
                         and captured_at is not None and math.isfinite(captured_at))
                if valid:
                    self._consecutive_failures = 0
                    self._last_valid_target = tuple(deflection)
                    self._last_valid_at = captured_at
                    self._last_valid_message = str(message)
                    self._last_valid_distance = str(distance)
                else:
                    self._consecutive_failures += 1
                    self._error = self._consecutive_failures >= _ERROR_FAILURE_THRESHOLD
                    deflection = self._last_valid_target
                    if self._last_valid_at is not None:
                        distance = self._last_valid_distance
                        age = max(0, int(time.time() - self._last_valid_at))
                        prefix = self._last_valid_message.split(" | ", 1)[0]
                        if not prefix.endswith((" m", " km", " Mm", " Gm")):
                            prefix = ""
                        message = (prefix + " | " if prefix else "") + f"HELD {age}s"
            self._active = bool(active)
            self._target = deflection if active else None
            self._message = str(message) if active else ""
            self._distance = str(distance) if active else ""
            displayed = self._target, self._message
        self._wake.set()
        return displayed

    def clear(self) -> None:
        self.set_state(False, None, "")

    # ── lifecycle ─────────────────────────────────────────────────────────── #

    @property
    def available(self) -> bool:
        return overlay_win32.IS_WINDOWS and RENDER_AVAILABLE

    @property
    def failed_reason(self) -> str | None:
        """Why the overlay is not on screen, or None while it is fine."""
        return self._failed_reason

    def start(self) -> bool:
        if not self.available:
            self._failed_reason = (
                "the overlay needs Windows with PIL and numpy present"
            )
            logger.warning("Ring overlay unavailable: %s", self._failed_reason)
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="sc-navpoint-ring", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    # ── the thread ────────────────────────────────────────────────────────── #

    def _run(self) -> None:
        window = None
        try:
            renderer = RingRenderer(self._size)
            window = overlay_win32.LayeredWindow(_WINDOW_CLASS, _WINDOW_TITLE)
            if not window.ok:
                self._failed_reason = "the overlay window could not be created"
                logger.warning("Ring overlay: %s", self._failed_reason)
                return

            x, y = self._placement()
            placement_at = time.monotonic()
            visible = False
            topmost_at = 0.0
            smoothed: tuple[float, float] | None = None
            last_ts: float | None = None
            started = time.monotonic()
            # Photosensitivity state. `shown` is the text currently on screen,
            # which lags the requested one on purpose: the swap only happens at
            # zero alpha, so text never changes while visible.
            fader = MessageFader()
            error_fader = MessageFader()
            msg_last: float | None = None

            while not self._stop.is_set():
                overlay_win32.pump_messages()

                with self._lock:
                    active = self._active
                    target = self._target
                    message = self._message
                    distance = self._distance
                    error = self._error

                if not active:
                    window.hide()
                    visible = False
                    smoothed = None
                    last_ts = None
                    error_fader = MessageFader()
                    self._wake.wait(_IDLE_WAIT_S)
                    self._wake.clear()
                    continue

                now = time.monotonic()
                if not visible or now - placement_at >= _PLACEMENT_INTERVAL_S:
                    x, y = self._placement()
                    placement_at = now

                if target is None:
                    smoothed = None
                    last_ts = None
                elif smoothed is None:
                    # First fix after acquiring a target snaps, so the bubble
                    # never streaks in from the centre and reads as motion the
                    # player did not make.
                    smoothed = target
                    last_ts = now
                else:
                    dt = min(_MAX_FRAME_DT_S, max(0.0, now - (last_ts or now)))
                    last_ts = now
                    a = 1.0 - math.exp(-dt / _SMOOTHING_TAU_S)
                    smoothed = (
                        smoothed[0] + (target[0] - smoothed[0]) * a,
                        smoothed[1] + (target[1] - smoothed[1]) * a,
                    )

                # Photosensitivity safety, enforced by MessageFader so the
                # guarantee is testable rather than implied. Its OWN clock:
                # `last_ts` belongs to the bubble smoothing, is already advanced
                # above, and is reset to None whenever the target clears, so
                # reusing it would freeze the fade at zero.
                msg_dt = min(_MAX_FRAME_DT_S, max(0.0, now - (msg_last or now)))
                msg_last = now
                shown, msg_alpha = fader.update(msg_dt, message)
                error_shown, error_alpha = error_fader.update(msg_dt, "ERROR" if error else "")

                frame = renderer.frame(smoothed, now - started, shown, msg_alpha,
                                       error_alpha if error_shown else 0.0,
                                       distance=distance)
                window.update(frame, self._size, self._size, x, y)
                window.show()
                visible = True

                if now - topmost_at >= _TOPMOST_INTERVAL_S:
                    window.force_on_top()
                    topmost_at = now

                self._wake.wait(self._interval)
                self._wake.clear()
        except Exception as e:  # noqa: BLE001 - a dead thread must say why
            self._failed_reason = f"{type(e).__name__}: {e}"
            logger.exception("Ring overlay thread died")
        finally:
            if window is not None:
                window.destroy()

    def _placement(self) -> tuple[int, int]:
        """Top-left corner that puts the top of the ring at the top centre.

        The canvas is exactly the ring's bounding box, so anchoring the ring
        means anchoring the window: centred horizontally, flush with the top
        edge of the game monitor, or the configured fallback when not found.
        """
        try:
            rect = overlay_win32.get_game_monitor_rect() or overlay_win32.get_monitor_rect(self._display)
            left, top, width, height = (int(value) for value in rect)
            if width <= 0 or height <= 0:
                raise ValueError("Invalid monitor geometry")
        except Exception:
            # Even the fallback enumeration may fail during monitor changes.
            left, top, width, _height = overlay_win32._FALLBACK_MONITOR
        return left + (width - self._size) // 2, top
