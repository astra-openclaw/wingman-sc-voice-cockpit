"""
main.py — SC NavPoint skill for Wingman AI
Author: Mallachi
Version: 4.10.2.1 (targets Star Citizen 4.10.2; live validation pending)

Mark and navigate to custom waypoints in Star Citizen using r_displayinfo 2
OCR. A waypoint belongs to a coordinate frame: the body-fixed local frame of a
planet or moon surface, or the star system's own frame for a point in open
space. The two are never mixed, since a coordinate means a different place in
each, so navigation across frames (and across bodies within the local frame) is
refused. Voice commands drop, list, and navigate to named positions.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import math
import os
import re
import sys
import threading
import time
from typing import TYPE_CHECKING

from api.enums import LogSource, LogType
from api.interface import WingmanInitializationError
from skills.skill_base import Skill, tool

current_dir = os.path.dirname(os.path.abspath(__file__))


from sc_voice_cockpit.navpoint.database import NavPoint, NavPointDatabase  # noqa: E402
from sc_voice_cockpit.navpoint.navigation import (  # noqa: E402
    LocalFrameUnavailable,
    calculate_bearing,
    format_distance,
    format_spoken_distance,
    ring_deflection,
    ring_hold_key,
    ring_status,
    ring_distance,
    space_guidance,
    stack_space_guidance,
)
from sc_voice_cockpit.navpoint import coordinate_stack  # noqa: E402
from sc_voice_cockpit.navpoint import active_frame  # noqa: E402
from sc_voice_cockpit.navpoint.navpoint_location_names import display_name
from sc_voice_cockpit.navpoint import overlay_fields  # noqa: E402
from sc_voice_cockpit.navpoint import overlay_reader  # noqa: E402
from sc_voice_cockpit.navpoint.scanner import NavPointScanner  # noqa: E402

if TYPE_CHECKING:
    from wingmen.wingman_context import WingmanContext


logger = logging.getLogger(__name__)

# Marking and navigation each read one fresh image with no recovery.
MARK_CAPTURE_ATTEMPTS = 1


# When the poll log is moved aside. Two files on disk worst case, so 16 MB.
# Not optional now that the log is the only instrument this project has: at
# `poll_interval: 1` a session writes a line per second per successful capture
# and up to nine per refusal.
POLL_LOG_MAX_BYTES = 8 * 1024 * 1024

# Ceiling on the per-cell refusal detail written for ONE refused capture. The
# two refusals on record carried about four refused cells across two relevant
# rows, so the cap is never reached in the ordinary case; it exists so that a
# catastrophically bad frame, where most of a row refuses, cannot write hundreds
# of lines and bury the events worth reading.
MAX_CELL_TRACE_LINES = 40

# Whether a refused capture's pixels are kept next to the log. ON since
# 2026-08-02, the user's call, for the next live session: it writes local PNGs
# of a screen region. It dumps the 667x175 overlay crop ONLY, never the whole
# screen, and nothing is ever transmitted anywhere. The point is that a per-cell
# classifier failure is ultimately a question about pixels, and the user is the
# only source of live frames: with a corpus on disk the OCR work iterates
# offline instead of costing a flown session per hypothesis.
SAVE_REFUSED_FRAMES = True

# How many refused-frame PNGs survive. Enforced BEFORE each write, so the cap is
# a real bound on how much disk this can ever take rather than a bound it
# briefly exceeds.
REFUSED_FRAME_KEEP = 20

# Serialises a capture when the skill was built without `__init__`, which is how
# the tests build it and how any harness driving the capture path directly will.
# A single module-wide lock is the right fallback: what it protects is one
# reader and one alignment cache, and an instance that never ran `__init__` has
# no lock of its own to use instead.
_FALLBACK_CAPTURE_LOCK = threading.Lock()

# The poll loop writes from its own thread and every tool call writes from the
# caller's, so two writers can reach the rotation together. Without this, both
# could pass the size check and both call `os.replace`, and the second would
# move a nearly empty fresh file over `.1` and destroy the generation the first
# had just preserved. Rare, and precisely the evidence loss that rotation was
# chosen over a size cap to prevent.
_POLL_LOG_LOCK = threading.Lock()

# A PER-CALLER CHANNEL for the outcome of the capture THAT caller asked for.
#
# The lock in `_capture_position_local` releases when that function returns,
# which is BEFORE `asyncio.to_thread` marshals the result back to the awaiting
# coroutine. The poll thread fires every second unattended, so between those two
# moments a whole second capture can finish and overwrite `self._last_outcome`,
# `self._scanner.last_reject_reason` and the one-shot badge latches. A caller
# reading them afterwards could be handed the OTHER capture's kind, and with it
# the console-command hint for an overlay it had just finished reading.
#
# A ContextVar and not an argument, because both capture seams have signatures
# and return types pinned by tests: the inner one must stay
# `(attempts) -> dict | None` and synchronous, the outer must stay a coroutine
# returning `dict | None`, and `CaptureOutcome` may not grow a field.
# `asyncio.to_thread` copies the caller's context into the worker thread, so the
# list put here before the call is the SAME list object the worker appends to.
#
# Module scope, not class scope: `test_capture_outcome.py` counts
# `_capture_lock` occurrences inside `inspect.getsource(main.SC_NavPoint)`, and
# a helper defined in the class body would sit inside that text.
_CAPTURE_SINK: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "navpoint_capture_sink", default=None
)
_FULL_PRECISION = contextvars.ContextVar("navpoint_full_precision", default=False)

# Holds one capture's trace lines, warnings and frame dumps until it is known
# whether its refusal repeats the previous one. A context variable, like the
# sink above, so a capture on another thread can never land in this one's list.
_TRACE_BUFFER: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "navpoint_trace_buffer", default=None
)


def _refusal_signature(outcome) -> tuple | None:
    """What makes two refusals "the same", or None for a capture that worked.

    Kind plus the named cause, never the measurements: ink, timings and row
    text vary frame to frame while the reason the player sees stays put.
    """
    if outcome is None or getattr(outcome, "position", None) is not None:
        return None
    cause = (getattr(outcome, "gate", "") or getattr(outcome, "reject_reason", "")
             or getattr(outcome, "detail", ""))
    return (getattr(outcome, "kind", ""), cause)


@contextlib.contextmanager
def _full_precision_capture():
    token = _FULL_PRECISION.set(True)
    try:
        yield
    finally:
        _FULL_PRECISION.reset(token)


@contextlib.contextmanager
def _capture_sink():
    """Open a sink for one caller's capture, and close it again afterwards.

    Yields:
        The list the capture appends its final `CaptureOutcome` to. Empty when
        the capture never reached the code that fills it, which is why every
        reader falls back to the shared field.
    """
    sink: list = []
    token = _CAPTURE_SINK.set(sink)
    try:
        yield sink
    finally:
        # Reset explicitly rather than relying on the context going out of
        # scope: a tool call's context is the caller's, and a leaked sink would
        # hand the NEXT caller this one's outcome, which is the defect itself.
        _CAPTURE_SINK.reset(token)


def _publish_outcome(outcome: "overlay_fields.CaptureOutcome") -> None:
    """Hand this capture's outcome to whoever asked for it, if anyone did.

    Called from inside the capture lock, at the moment the outcome becomes
    final. Silent when no sink is open, which is the poll thread's ordinary
    case: it captures unattended and reads the shared field afterwards.

    A list append cannot raise, which is the property that matters here: a
    diagnostic must never be able to break a capture.
    """
    sink = _CAPTURE_SINK.get()
    if sink is not None:
        sink.append(outcome)


def _sink_outcome(fallback):
    """This caller's OWN capture outcome, or the shared field when it has none.

    THE FALLBACK IS REQUIRED, not sloppiness. Eight existing tests replace the
    capture seams with doubles that never reach `_publish_outcome`, so the sink
    stays empty and the shared field is the only answer available. It is
    unreachable in production, where the real capture always publishes.

    Args:
        fallback: What to return when this caller's capture filled no sink,
            normally `self._last_outcome`.

    Returns:
        The `CaptureOutcome` this caller's capture ended on, or `fallback`.
    """
    sink = _CAPTURE_SINK.get()
    return sink[-1] if sink else fallback


def _rotate_poll_log(path: str) -> None:
    """Move the poll log aside once it passes the cap, keeping one generation.

    ROTATE, NEVER TRUNCATE. This file is the only record of why a capture
    refused, and a cap that overwrote or truncated it would destroy the very
    evidence it exists to collect. `os.replace` is atomic, so a reader holding
    the old path keeps reading a whole file.

    Swallows its own failures rather than letting the caller's `try` do it: a
    rotation that could not happen must still not cost the line that was about
    to be written.

    Args:
        path: The live log file. Missing is normal on the first write.
    """
    try:
        # The check and the move are one decision, so they are taken together.
        with _POLL_LOG_LOCK:
            if os.path.getsize(path) < POLL_LOG_MAX_BYTES:
                return
            os.replace(path, f"{path}.1")
    except Exception:
        pass


def _fmt_cost(value) -> str:
    """A decode cost or margin as a trace field, two decimals when it is a number.

    A stubbed row carries no cost at all, and a diagnostic that raised on the
    missing key would take the capture down with it.
    """
    return f"{value:.2f}" if isinstance(value, (int, float)) else str(value)


def _fmt_fraction(value) -> str:
    """An ink or periodicity measurement as a trace field, five decimals.

    `?` rather than a raised exception when the measurement could not be taken,
    because these now travel on the outcome's metrics instead of being computed
    inline at the trace site, and a diagnostic that could not be measured must
    still not cost the line it was going to appear on.
    """
    return f"{value:.5f}" if isinstance(value, (int, float)) else "?"


def _fmt_int(value) -> str:
    """A count as a trace field, or `?` when it was never measured."""
    return str(value) if isinstance(value, int) else "?"


class SC_NavPoint(Skill):
    # Versions mirror the Star Citizen patch this skill is qualified against.
    # Within-patch updates append a dotted suffix (4.8.0.1, 4.8.0.2, …).
    # See skills/Versioning.md.
    VERSION = "4.10.2.1"
    SC_TARGET_VERSION = "4.10.2"

    def __init__(self, config, settings, wingman: "WingmanContext") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self._db: NavPointDatabase | None = None
        self._scanner: NavPointScanner | None = None
        self._ui_server = None
        self._ui_window = None
        self._ring_overlay = None
        # What the reader should say about the most recent read; "" means it read
        # cleanly. Since Vision was removed this is the only way a refusal is
        # ever visible to the player.
        self._reader_state = ""
        # What the RING should say about the most recent capture. Not the same
        # question as the field above: that one is the reader's per-attempt
        # verdict, written before the scanner has validated anything, so it is
        # still clean on every path where validation is what refused. This one
        # is decided after the whole capture is, and it is what the ring reads.
        self._last_ring_state = ""
        # How the last capture ended, in full. The ring can only carry five
        # strings and a `None` return carries nothing at all, so this is what
        # the tools ask when they need to tell the player WHICH refusal it was.
        self._last_outcome: overlay_fields.CaptureOutcome | None = None
        # One capture at a time: the poll thread and a spoken command can both
        # reach the capture path, and a single reader with a mutable alignment
        # cache sits underneath them.
        self._capture_lock = threading.Lock()
        self._reader = None
        self._active_target: NavPoint | None = None
        # The last capture, kept here so the ring can be refreshed without
        # reaching into the HUD server's private state.
        self._last_position: dict | None = None
        self._nav_task: asyncio.Task | None = None
        # Polling runs on its own thread with its own event loop; see
        # _start_nav_polling for why a task on the caller's loop cannot work.
        self._poll_thread: threading.Thread | None = None
        self._poll_stop: threading.Event | None = None
        self._calibration_thread: threading.Thread | None = None
        self._calibration_stop: threading.Event | None = None
        # Arrival-alert progress, reset at the start and end of every activation.
        self._arrival_alert_done: bool = False
        self._arrival_stop_streak: int = 0
        # New server_id seen once but not yet confirmed by a second capture; the
        # wipe only fires when two consecutive captures agree (see
        # _handle_server_change).
        self._pending_server_change: str | None = None
        # Consecutive well-formed captures so far agreeing on that new server.
        self._pending_server_count: int = 0
        # One-time badge guard for an unreadable server token.
        self._server_shape_badge_shown: bool = False
        # One-time badge guard for an overlay read rejected as implausible.
        self._implausible_badge_shown: bool = False
        # One-time badge guard for a coordinate whose unit suffix was unreadable.
        self._units_badge_shown: bool = False
        # One-time badge guard for the two position readings disagreeing.
        self._mismatch_badge_shown: bool = False
        # One-time badge guard for the SolarSystem/Root lines disagreeing.
        self._frame_badge_shown: bool = False
        # One-time badge guard for the uncalibrated space-direction test mode.
        self._space_test_badge_shown: bool = False

    # Every custom property this skill declares, in `default_config.yaml`
    # order. `test_custom_property_contract` pins this tuple to that file in
    # both directions, so adding a property without validating it, or deleting
    # one from the YAML while code still reads it, fails a test.
    #
    # Deleting an id from `default_config.yaml` is the dangerous direction and
    # it is not this tuple's job to stop it: a saved Wingman config persists
    # only `id` and `value` and rehydrates `name`/`property_type` by merging
    # over the skill defaults, so removing the default orphans the saved row
    # and kills core startup for the WHOLE wingman, not just this skill.
    _CUSTOM_PROPERTY_IDS = (
        "display",
        "arrival_alert_distance",
        "arrival_stop_distance",
        "overlay_size",
    )

    async def validate(self) -> list[WingmanInitializationError]:
        """Surface a misconfigured property at startup rather than mid-flight.

        Walks EVERY declared property, not just `display`. This is safe because
        the loader deep-merges the wingman's saved config over the skill's
        `default_config.yaml` and merges property lists BY ID
        (`ConfigManager.__merge_list`), so a declared property always survives
        the merge with at least its default value. A property can therefore be
        reported missing here only when it is genuinely undeclared.

        Values are still retrieved just in time by the individual getters,
        which keep their own clamping and defaults; this adds a startup check
        and changes no value.
        """
        errors = await super().validate()
        for property_id in self._CUSTOM_PROPERTY_IDS:
            self.retrieve_custom_property_value(property_id, errors)
        return errors

    def _get_display(self) -> int:
        errors: list[WingmanInitializationError] = []
        val = self.retrieve_custom_property_value("display", errors)
        return int(val) if val else 1

    async def prepare(self) -> None:
        await super().prepare()

        self._trace_build_identity()

        db_dir = self.get_generated_files_dir()
        self._db = NavPointDatabase(db_dir)
        self._scanner = NavPointScanner()

        if hasattr(self, "_start_cockpit_web"):
            await self._start_cockpit_web()
        else:
            # Preserve the leaf NavPoint contract for its own offline suite.
            # SC_VoiceCockpit always supplies the single-server hook above.
            try:
                from sc_voice_cockpit.navpoint.navpoint_ui.app import NavPointServer
                from sc_voice_cockpit.navpoint.navpoint_ui.sharing import dashboard_token
                from sc_voice_cockpit.navpoint.navpoint_ui.window import NavPointWindow
                self._ui_server = NavPointServer(
                    db=self._db, port=7869,
                    arrival_alert_m=self._get_arrival_alert_m(),
                    arrival_stop_m=self._get_arrival_stop_m(),
                    on_target_change=self._on_hud_target_change,
                    share_on_lan=True, access_token=dashboard_token(db_dir))
                self._ui_server.start()
                self._ui_window = NavPointWindow(url=self._ui_server.url)
                self.wingman.run_in_thread(self._announce_ui_startup,self._ui_server)
            except ImportError as e:
                logger.warning("NavPoint HUD unavailable: %s",e)

        self._restart_ring_overlay()
        self._start_background_calibration()

    def _restart_ring_overlay(self) -> None:
        """Reuse the ring-only stop/start path, without touching capture or the server."""
        # A second prepare() without an unload would otherwise leave the first
        # overlay's thread and window alive with nothing left pointing at them.
        if self._ring_overlay:
            self._ring_overlay.stop()
            self._ring_overlay = None

        if self._get_overlay_enabled():
            try:
                from sc_voice_cockpit.navpoint.ring_overlay import RingOverlay  # noqa: E402

                self._ring_overlay = RingOverlay(
                    size=self._get_overlay_size(),
                    display=self._get_display(),
                )
                if not self._ring_overlay.start():
                    self.printr.print(
                        "[NavPoint] The in-game ring overlay could not start "
                        f"({self._ring_overlay.failed_reason}); the ring is still "
                        "on the HUD page.",
                        color=LogType.WARNING,
                    )
            except ImportError as e:
                logger.warning("NavPoint ring overlay unavailable: %s", e)
                self._ring_overlay = None

    async def _announce_ui_startup(self, server) -> None:
        """Match Accountant's compact card after Wingman's startup UI is ready."""
        # Accountant uses the same delay to avoid broadcasts during UI loading.
        await asyncio.sleep(15)
        if self._ui_server is not server:
            return  # Unloaded or replaced while the announcement was waiting.

        from sc_voice_cockpit.navpoint.navpoint_ui.sharing import qr_png_base64
        lan_url = server.lan_url
        qr = qr_png_base64(lan_url) if lan_url else None
        instruction = "Scan with your phone, or open:" if qr else "Open on this computer:"
        message = f"NavPoint dashboard ready.\n{instruction}\n{server.url}"
        await self.printr.print_async(
            message,
            color=LogType.POSITIVE,
            source=LogSource.WINGMAN,
            source_name=self.wingman.name if self.wingman else "SC_NavPoint",
            skill_name=self.name,
            additional_data={"image_base64": qr} if qr else None,
        )

    async def unload(self) -> None:
        self._stop_nav_polling()
        self._stop_background_calibration()
        if self._ring_overlay:
            try:
                self._ring_overlay.stop()
            except OSError:
                pass
            self._ring_overlay = None
        if self._ui_window:
            try:
                self._ui_window.close()
            except OSError:
                pass
            self._ui_window = None
        if self._ui_server:
            try:
                self._ui_server.stop()
            except OSError:
                pass
            self._ui_server = None
        await super().unload()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _dashboard_url(self):
        return getattr(self._ui_server,"url","") if self._ui_server else ""

    def _local_reader(self):
        """Load V8 once; a load failure or refused image never switches readers."""
        self._reader_backend = "specialist_v8_active_frame"
        reader = getattr(self, "_reader", None)
        if reader is None:
            try:
                from sc_voice_cockpit.navpoint.frame_reader import FrameAwareReader
                reader = FrameAwareReader()
            except Exception as exc:  # pragma: no cover - environment specific
                logging.getLogger(__name__).warning(
                    "NavPoint local reader unavailable: %s", exc
                )
                reader = False
            self._reader = reader
        return reader or None

    def _capture_position_local(self, attempts: int = 1, *,
                                calibration_only: bool = False,
                                stop_event=None) -> dict | None:
        """Capture once; a refused image ends this call without recovery.

        ``attempts`` remains accepted for older callers but cannot enable
        retries. The polling loop independently starts the next fresh capture.
        """
        # ONE capture at a time. The poll loop runs on its own thread and a
        # spoken `mark_location` runs on the caller's, so the two can already
        # arrive here together; underneath sits a single OverlayReader whose
        # alignment cache is mutable state refitted on demand, plus the two
        # verdict fields: `self._reader_state`, the reader's own per-attempt
        # answer, and `self._last_ring_state`, which is what the RING reads and
        # is decided only after the scanner has validated the payload.
        # Interleaving them would let one capture's refusal describe the other's
        # frame.
        with getattr(self, "_capture_lock", None) or _FALLBACK_CAPTURE_LOCK:
            if calibration_only and (
                getattr(self, "_active_target", None) is not None
                or (stop_event is not None and stop_event.is_set())
            ):
                return None
            # Inside the lock, not before it. Outside, a second thread arriving
            # while this one is mid-capture would restamp the clock and every
            # duration this capture reports would be measured from the OTHER
            # capture's start. The wait for the lock is deliberately not counted:
            # it is time this capture spent not looking at the screen.
            self._capture_started_at = time.perf_counter()
            reader = self._local_reader()
            if calibration_only:
                if (reader is None or not self._scanner
                        or not hasattr(reader, "planet_rotation_tracker")
                        or (stop_event is not None and stop_event.is_set())):
                    return None
                # Read through the same model/tracker, but don't publish a fix,
                # ring failure, arrival, or server-change action while idle.
                previous_reader_state = getattr(self, "_reader_state", "")
                try:
                    return self._quiet_capture_attempt(reader).position
                finally:
                    self._reader_state = previous_reader_state
            if reader is None or not self._scanner:
                # Not a property of the frame, so looking again cannot change it.
                self._trace_capture(
                    f"REFUSED: reader={'ok' if reader else 'unavailable'} "
                    f"scanner={'ok' if self._scanner else 'unavailable'}"
                )
                self._last_outcome = overlay_fields.CaptureOutcome(
                    kind=overlay_fields.KIND_NO_READER,
                    # Not the reader's verdict: the reader never ran, so that
                    # field still holds whatever the LAST capture made of a
                    # frame this one never looked at.
                    ring_state=overlay_fields.RING_NO_SIGNAL,
                    detail=(
                        f"reader={'ok' if reader else 'unavailable'} "
                        f"scanner={'ok' if self._scanner else 'unavailable'}"
                    ),
                )
                self._last_ring_state = self._last_outcome.ring_state
                _publish_outcome(self._last_outcome)
                return None
            outcome = self._quiet_capture_attempt(reader)
            self._last_outcome = outcome
            self._last_ring_state = outcome.ring_state
            _publish_outcome(outcome)
            return outcome.position

    def _capture_attempt(
        self, reader, attempt: int, attempts: int
    ) -> "overlay_fields.CaptureOutcome":
        """One grab and one decode, with every guard applied exactly as before.

        Grabs ONLY the overlay block (about 4 ms against 104 ms for the whole
        screen; all the reader's geometry is right-edge relative, so the crop
        decodes identically). Sets `self._reader_state` to the READER's verdict
        on this frame, which is not what the ring shows: the ring reads
        `self._last_ring_state`, mapped from the final kind through
        `overlay_fields.ring_for_outcome` once the scanner has validated the
        payload. The two differ on every path where VALIDATION is what refused.
        Nor is the ring the only channel that reports a refusal; the tool result
        carries `capture_kind` and its own message. The caller retries, so the
        verdict that survives is the last attempt's, which is the one the
        player's mark rests on.

        Args:
            reader: The loaded `OverlayReader`.
            attempt: Which attempt this is, counting from one.
            attempts: How many the caller will make at most. Only the last one
                writes the refusal trace, so a capture stays ONE event in the
                log whether it looked once or five times and a grep for REFUSED
                still counts refused captures rather than refused frames.

        Returns:
            A `CaptureOutcome` naming how this attempt ended. Its `position` is
            None whenever the frame could not be read, which is the same signal
            the caller has always acted on; everything else on it is what used
            to be thrown away at each of the return points below.
        """
        final = attempt == attempts
        started = time.perf_counter()
        capture_target = active_frame.target_snapshot(getattr(self, "_active_target", None))
        full_precision = _FULL_PRECISION.get()
        try:
            # Imported here, not at module scope: the skill must still load in
            # an environment missing them, and the Vision path already proved
            # that a hard import at load time takes the whole skill down.
            import numpy as np
            from PIL import Image
            from mss import mss

            with mss() as sct:
                monitor = sct.monitors[
                    min(self._get_display(), len(sct.monitors) - 1)
                ]
                region = getattr(reader, "capture_region", overlay_reader.block_region)(monitor)
                captured_at = time.time()
                raw = sct.grab(region)
                if not getattr(reader, "capture_still_valid", lambda: True)():
                    raise RuntimeError("Game foreground changed during capture")
                frame = np.asarray(
                    Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                ).astype(float)
        except Exception as exc:  # pragma: no cover - environment specific
            self._capture_warning("NavPoint screen grab failed: %s", exc)
            if final:
                self._trace_capture(
                    f"REFUSED: screen grab failed on display "
                    f"{self._get_display()}: {type(exc).__name__}: {exc} "
                    f"[{self._capture_ms()} attempts={attempts}]"
                )
            self._reader_state = overlay_fields.RING_NO_SIGNAL
            detail = f"{type(exc).__name__}: {exc}"
            self._trace_attempt(
                attempt, attempts, overlay_fields.KIND_NO_GRAB, started,
                f"error={type(exc).__name__!r}",
            )
            # No frame, so `overlay_present` and `frame_usable` stay None: the
            # questions were never asked, which is not the same as answered no.
            return overlay_fields.CaptureOutcome(
                kind=overlay_fields.KIND_NO_GRAB,
                ring_state=self._reader_state,
                attempts=attempt,
                metrics=self._attempt_metrics(started),
                detail=detail,
            )

        usable, unusable_reason = getattr(reader, "capture_is_usable", overlay_reader.capture_is_usable)(frame)
        present = overlay_reader.overlay_is_present(frame)
        rows = []
        payload = None
        capture_reason = ""
        decode_error = ""
        if usable:
            try:
                y0, _, rows = reader.read(frame, overlay_reader.POSITION_ROWS)
                if hasattr(reader, "capture_for_target"):
                    payload, capture_reason = reader.capture_for_target(rows, capture_target, full_precision)
                    if (payload and not full_precision
                            and payload.get("precision", {}).get("mode") != "full"
                            and capture_target != active_frame.target_snapshot(getattr(self, "_active_target", None))):
                        payload, capture_reason = None, "target_changed_during_capture"
                else:
                    payload, capture_reason = getattr(reader, "capture_payload", overlay_fields.capture_payload)(rows)
            except Exception as exc:  # pragma: no cover
                self._capture_warning("NavPoint decode failed: %s", exc)
                decode_error = f"{type(exc).__name__}: {exc}"
            else:
                # Outside the try on purpose: a diagnostic that raised inside it
                # would be reported as a decode error that never happened.
                self._trace_alignment(reader, y0)
        # No second decoder, header repair or recovery runs on a refused image.
        self._reader_state = overlay_fields.ring_state(rows, payload, present, usable)
        if payload is None:
            # Name the gate that refused. Without this the mark path fails
            # silently: the skill's `logger` output never reaches
            # wingman-core.log (see `_poll_trace`), so a refusal here used to
            # cost a live probe session to explain. The geometry goes in the
            # line too, because a capture aimed at the wrong region looks
            # exactly like an overlay the player never switched on.
            gate = ""
            if decode_error:
                kind, detail = overlay_fields.KIND_DECODE_ERROR, decode_error
                trace_detail = f"error={decode_error!r}"
                cause = f"decode raised {decode_error}"
            elif not usable:
                kind = overlay_fields.KIND_UNUSABLE
                detail = unusable_reason or ""
                trace_detail = f"unusable={unusable_reason!r}"
                cause = f"frame unusable ({unusable_reason or 'no reason given'})"
            elif not present:
                kind, detail, trace_detail = overlay_fields.KIND_ABSENT, "", ""
                cause = "overlay not on screen"
            else:
                # The reason the capture ITSELF produced, so the walk runs
                # once rather than twice. `_refusal_gate` remains the fallback
                # and keeps its own guard: it is the only producer of the
                # unnamed-gate note, which `_capture_error` is built to handle.
                gate = capture_reason or self._refusal_gate(rows)
                kind, detail = overlay_fields.KIND_GATE, gate
                trace_detail = f"gate={gate!r}"
                cause = f"overlay read but no payload from {len(rows)} row(s): {gate}"
            # Measured before the trace, not at the return, because the ink and
            # periodicity on the REFUSED line now come from here. The frame goes
            # in only on the final attempt: those two are the only measurements
            # on this path that touch every pixel, and the earlier attempts'
            # copies would be paid for and never written.
            metrics = self._attempt_metrics(
                started, reader, rows, frame if final else None
            )
            if final:
                # The duration goes BEFORE `attempts=`, which stays the last
                # field in the block: the closing `attempts=N]` is grepped as one
                # string, so a field appended after it would break the search
                # this line exists to serve.
                self._trace_capture(
                    f"REFUSED: {cause} [present={present} usable={usable} "
                    f"rows={len(rows)} region={region} "
                    f"ink={_fmt_fraction(metrics.get('ink'))} "
                    f"periodicity={_fmt_fraction(metrics.get('periodicity'))} "
                    f"{self._capture_ms()} attempts={attempts}]"
                )
                self._trace_rows(rows)
                self._trace_cells(rows)
                self._dump_refused_frame(frame, kind)
            self._trace_attempt(attempt, attempts, kind, started, trace_detail)
            return overlay_fields.CaptureOutcome(
                kind=kind,
                overlay_present=present,
                frame_usable=usable,
                ring_state=self._reader_state,
                gate=gate,
                attempts=attempt,
                metrics=metrics,
                detail=detail,
            )

        position = self._scanner.parse_payload(payload)
        reason = getattr(self._scanner, "last_reject_reason", None)
        if position is None:
            # A refusal with no reason is its own outcome. `parse_payload`
            # returns None with the reason still unset when a local-frame
            # payload carries no readable coordinates at all, and a consumer
            # that switched on the reason alone could not tell that from a
            # capture nobody attempted.
            kind = (
                overlay_fields.KIND_REJECT if reason
                else overlay_fields.KIND_UNEXPLAINED
            )
            metrics = self._attempt_metrics(
                started, reader, rows, frame if final else None
            )
            if final:
                # The count sits OUTSIDE the parenthesis the reason is in,
                # because that clause is what months of existing logs are
                # grepped for.
                self._trace_capture(
                    "REFUSED: parse_payload rejected the read (reason="
                    f"{reason!r}) [{self._capture_ms()} attempts={attempts}]"
                )
                self._dump_refused_frame(frame, kind)
            self._trace_attempt(
                attempt,
                attempts,
                kind,
                started,
                f"reason={reason!r}" if reason else "",
            )
            return overlay_fields.CaptureOutcome(
                kind=kind,
                overlay_present=present,
                frame_usable=usable,
                # The reader's verdict is RING_OK here: it read the frame and
                # produced a payload, and the SCANNER is what refused. Left as
                # it is, the ring shows a clean face through a capture that
                # produced nothing.
                ring_state=overlay_fields.ring_for_outcome(kind, self._reader_state),
                reject_reason=reason or "",
                attempts=attempt,
                metrics=metrics,
            )

        # A truthy payload with neither a body nor a system frame is the space
        # branch reporting that the overlay read cleanly and there is genuinely
        # no frame here, which a station interior produces. It is NOT a
        # rejection, whatever `last_reject_reason` happens to hold: that field
        # can be set on this very branch and still return a payload, so a
        # consumer that read the reason without first checking for a payload
        # would report a safety refusal that never happened.
        position["captured_at"] = captured_at
        framed = bool(
            position.get("body")
            or position.get("system_frame")
            or position.get("coordinate_stack")
        )
        kind = overlay_fields.KIND_OK if framed else overlay_fields.KIND_NO_FRAME
        self._trace_attempt(attempt, attempts, kind, started)
        return overlay_fields.CaptureOutcome(
            kind=kind,
            position=position,
            overlay_present=present,
            frame_usable=usable,
            # KIND_NO_FRAME reaches here with a clean reader verdict too: the
            # overlay read perfectly and there is genuinely no frame to report.
            ring_state=overlay_fields.ring_for_outcome(kind, self._reader_state),
            attempts=attempt,
            # The frame goes in unconditionally here: a success ends the capture,
            # so this is measured once however many attempts it took, and it is
            # the control group the refusals have never had. `ink=0.0525` on
            # four refusals is a number with nothing to compare against.
            metrics=self._attempt_metrics(started, reader, rows, frame),
        )

    async def _capture_position(self, attempts: int = 1) -> dict | None:
        """Read one fresh frame on a worker thread and validate its position.

        The selected local reader runs once. A refusal returns None; no recovery,
        fallback reader or repeated image is used. Capture time is recorded at
        the screenshot boundary and preserved through parsing and publication.
        The legacy attempts argument cannot enable retries.
        """
        if not self._scanner:
            # Recorded rather than returned bare, so a tool asking what went
            # wrong gets an answer here too instead of a stale one from the
            # previous capture.
            no_reader = overlay_fields.CaptureOutcome(
                kind=overlay_fields.KIND_NO_READER,
                ring_state=overlay_fields.RING_NO_SIGNAL,
                detail="scanner=unavailable",
            )
            self._last_outcome = no_reader
            self._last_ring_state = no_reader.ring_state
            # Published like every other capture, so this fast path holds the
            # same per-caller invariant as the rest: a caller reads ITS OWN
            # outcome out of its sink. Two concurrent callers would agree about
            # a scanner that is built once, so this is not a live misreport; an
            # invariant that holds everywhere except one path is the cost.
            _publish_outcome(no_reader)
            return None
        pos_data = await asyncio.to_thread(self._capture_position_local, attempts)
        # THIS caller's own capture, not whatever finished most recently. The
        # lock released before the line above resumed, so an unattended poll
        # tick can already have overwritten every shared field below.
        outcome = _sink_outcome(None)
        if not pos_data:
            # `reject_reason` defaults to "" and is set from `last_reject_reason`
            # on the KIND_REJECT path, so the rejection badges are reached by exactly
            # the same reasons as before. The scanner read stays as the fallback
            # for callers whose capture filled no sink.
            reason = (
                (getattr(outcome, "reject_reason", "") or None)
                if outcome is not None
                else getattr(self._scanner, "last_reject_reason", None)
            )
            if reason == "implausible_body" and not self._implausible_badge_shown:
                self._implausible_badge_shown = True
                self.printr.print(
                    "[NavPoint] The overlay's body name did not pass the "
                    "plausibility check, so the capture was discarded. Nothing "
                    "was saved. There is no action here: looking again produces "
                    "the same reading and the same refusal.",
                    color=LogType.WARNING,
                )
            elif reason == "unreadable_units" and not self._units_badge_shown:
                self._units_badge_shown = True
                self.printr.print(
                    "[NavPoint] Could not read the unit (m or km) on a coordinate, "
                    "so the capture was refused rather than risk a waypoint 1000x "
                    "off. Retry with the overlay clearly visible.",
                    color=LogType.WARNING,
                )
            elif reason == "frame_mismatch" and not self._frame_badge_shown:
                self._frame_badge_shown = True
                self.printr.print(
                    "[NavPoint] The overlay's SolarSystem and Root lines did not "
                    "match, so the capture was discarded rather than risk a "
                    "waypoint in the wrong place.",
                    color=LogType.WARNING,
                )
            elif reason == "coord_mismatch" and not self._mismatch_badge_shown:
                self._mismatch_badge_shown = True
                self.printr.print(
                    "[NavPoint] The overlay's two position readings disagreed, so "
                    "the capture was discarded. Retry with a clear, still view of "
                    "the overlay.",
                    color=LogType.WARNING,
                )
            return pos_data

        # Stamp the capture time so freshness checks (voice cross-body refusal
        # and the HUD bearing) can reject a position too old to trust.
        pos_data.setdefault("captured_at", time.time())

        # Camera angles become target-relative offsets only after calibration.
        # The old conversion assumed local axes and produced a false compass HDG.
        pos_data.pop("heading", None)

        # A proven server change wipes the previous session's waypoints. Runs on
        # every capture path, including the poll loop.
        server_id = pos_data.get("server_id", "")
        if server_id:
            self._handle_server_change(server_id)

        self._log_capture(pos_data, outcome)
        return pos_data

    def _log_capture(self, pos_data: dict, outcome=None) -> None:
        """Append the raw numbers of a successful capture to the poll log.

        The system-frame direction convention is still unsolved, and solving it
        needs (CamDir, known direction) pairs from real flight. Recording every
        capture means an ordinary session produces that data instead of needing
        a special build later. Cheap: one line per capture in a local file.

        THE SUCCESS BASELINE RIDES ON THIS LINE, which is why it grew four
        fields and no new lines. Every ink and periodicity figure in the log's
        history was measured on a refusal, so they are four numbers with no
        control group and no amount of further refusals makes them diagnostic.
        The same measurements from a capture that WORKED are what say whether
        `ink=0.0525` is the discriminator or a constant, and whether 6 trusted
        rows of 8 is normal or already degraded.

        Args:
            pos_data: The capture that succeeded.
            outcome: THAT capture's own outcome. The shared field is the
                fallback, and it can already belong to a poll tick that landed
                while this caller was being handed its result, which would put
                another capture's numbers on this capture's line.
        """
        try:
            frame = self._capture_frame(pos_data)
            camdir = pos_data.get("camdir")
            if outcome is None:
                outcome = getattr(self, "_last_outcome", None)
            metrics = getattr(outcome, "metrics", None)
            metrics = metrics or {}
            # The caller's OWN wait, measured inside the lock. `_capture_ms()`
            # reads the live stamp, which another capture may already have
            # restamped by the time this line is written, always reporting a
            # shorter time than the caller actually waited. Integer formatting
            # is mandatory: this field stays LAST and is grepped as `ms=\d+$`.
            capture_ms = metrics.get("capture_ms")
            elapsed = (
                f"ms={capture_ms:.0f}" if capture_ms is not None
                else self._capture_ms()
            )
            self._poll_trace(
                "capture "
                f"frame={frame} "
                f"pos=({pos_data.get('x')}, {pos_data.get('y')}, {pos_data.get('z')}) "
                f"camdir={tuple(camdir) if camdir else None} "
                f"body={pos_data.get('body', '')!r} zone={pos_data.get('zone', '')!r} "
                f"frame_id={pos_data.get('frame_id', '')!r} "
                f"ink={_fmt_fraction(metrics.get('ink'))} "
                f"periodicity={_fmt_fraction(metrics.get('periodicity'))} "
                f"trusted={_fmt_int(metrics.get('trusted'))}/"
                f"{_fmt_int(metrics.get('rows'))} "
                f"cost={_fmt_cost(metrics.get('cost'))} "
                f"{elapsed}",
                chat=False,
            )
        except Exception as e:  # noqa: BLE001 - diagnostics never break a capture
            logger.debug("Capture logging failed: %s", e)


    @staticmethod
    def _capture_frame(pos_data: dict | None) -> str | None:
        """Which coordinate frame a capture is in: 'local', 'system', or None.

        None means the capture proved nothing (no frame readable), which must
        never be treated as agreement with whatever the target happens to be.
        """
        if not pos_data:
            return None
        if pos_data.get("body"):
            return "local"
        if pos_data.get("system_frame"):
            return "system"
        if pos_data.get("coordinate_stack"):
            # LAST, so a capture that also carries a legacy frame keeps
            # reporting that frame and every waypoint stored before schema 1
            # behaves exactly as it did. The stack is consulted for routing by
            # `_stack_route`, which is reached from the TARGET's frame, not
            # from this one.
            return "stack"
        return None

    # THE ONLY PLACE THIS INSTRUCTION IS PRODUCED, and it is reachable from one
    # kind: KIND_ABSENT, which means `overlay_is_present` actually returned
    # False. It used to appear on three refusals, two of which had just READ the
    # overlay, so the assistant told the player to switch on something visibly
    # already on. `default_config.yaml` orders any hint relayed word for word,
    # which makes a wrong hint a script the model reads out rather than a nuance.
    #
    # The skill never types it. The player does, from their own console.
    _OVERLAY_HINT = (
        "Relay this to the player word for word: Open the Star Citizen "
        "console with the tilde key (~) and type: r_displayinfo 2. This "
        "skill cannot enable it for you; the player must type it manually."
    )

    # Four gates ask the player for the same thing, and that is not an oversight
    # in the table below: `no_system_line`, `refused_above`, `no_coordinate` and
    # `no_root` all mean parts of a visible overlay did not decode, and the one
    # lever the player holds over that is what is rendered behind the block. The
    # MESSAGES still differ, because each carries its own gate's clause.
    _REDUCE_SCENERY = (
        "Point away from bright or busy scenery behind the overlay block, or "
        "hold still, then ask again."
    )

    # WHAT THE PLAYER IS TOLD, per kind, in one table for all three tools.
    #
    # Every refusal here is TERMINAL. There is no second reader to pick it up,
    # so the text is the whole of what the player gets, and three rules govern
    # all of it:
    #
    #   1. Say what happened, in one clause, with no instrument-fault language.
    #      A refusal is the reader correctly declining, and "malfunctioning"
    #      sends the player hunting a fault that does not exist. Same reasoning
    #      already recorded for the ring strings in `overlay_fields`.
    #   2. Give exactly ONE action, or say plainly that there is none. "Try
    #      again" on a repeatable refusal is a lie, and three of these kinds
    #      repeat by construction.
    #   3. Say that nothing was saved. Every one of these paths stores nothing,
    #      and the player should never have to infer that.
    #
    # The empty action on `absent` is deliberate: its action IS the hint, which
    # travels in its own field to be relayed verbatim. The empty ones on `gate`
    # and `reject` are filled per gate and per reason below, because neither
    # kind is one situation: `body_scale` and `no_root` want opposite things
    # from the player, and so do `implausible_body` and `coord_mismatch`.
    _CAPTURE_MESSAGES: dict[str, tuple[str, str]] = {
        overlay_fields.KIND_NO_READER: (
            "The overlay reader could not be loaded, so no position could be "
            "read.",
            "There is no action here: this is an installation problem with the "
            "skill, not something to change in game.",
        ),
        overlay_fields.KIND_NO_GRAB: (
            "The screen could not be captured on display {display}.",
            "Check the NavPoint display setting if you have more than one "
            "monitor.",
        ),
        overlay_fields.KIND_UNUSABLE: (
            "The overlay was too washed out on this frame to read safely.",
            "Look at darker ground, or away from the sun, then ask again.",
        ),
        overlay_fields.KIND_ABSENT: (
            "The position overlay is not on screen.",
            "",
        ),
        overlay_fields.KIND_DECODE_ERROR: (
            "The overlay reader failed on this frame.",
            "Ask again. If it repeats, the poll log carries the detail.",
        ),
        overlay_fields.KIND_GATE: (
            "The overlay was on screen and was read, but the position could not "
            "be confirmed safely: {clause}.",
            "",
        ),
        overlay_fields.KIND_REJECT: (
            "The overlay was read but the position failed a safety check and "
            "was discarded rather than risk a waypoint in the wrong place: "
            "{clause}.",
            "",
        ),
        overlay_fields.KIND_UNEXPLAINED: (
            "The overlay was read but carried no usable coordinates.",
            "There is no action here: this is not a place a waypoint can be "
            "marked.",
        ),
        overlay_fields.KIND_NO_FRAME: (
            "No usable coordinate frame here: neither a planet or moon local "
            "frame nor a readable open-space position. A space station or "
            "asteroid base interior looks like this.",
            "There is no action here: move outside the station.",
        ),
    }

    _GATE_ACTIONS: dict[str, str] = {
        "unknown_zone_name": "This location needs a NavPoint mapping update.",
        overlay_fields.GATE_NO_SYSTEM_LINE: _REDUCE_SCENERY,
        overlay_fields.GATE_REFUSED_ABOVE: _REDUCE_SCENERY,
        overlay_fields.GATE_NO_COORDINATE: _REDUCE_SCENERY,
        overlay_fields.GATE_NO_ROOT: _REDUCE_SCENERY,
        # Not a reading problem and not fixed by looking harder: the skill will
        # not store a system-frame point while the player stands on a body,
        # because that coordinate is correct at capture and wrong seconds later.
        overlay_fields.GATE_BODY_SCALE: (
            "There is no action here: this is a refusal by design on a body "
            "surface, and looking again produces the same reading."
        ),
    }

    # A refusal the frame quality caused, which a steadier second look can fix.
    _HOLD_STILL = "Hold still with a clear view of the overlay, then ask again."

    # The scanner's reasons, as clauses a player can act on. The ids themselves
    # are log vocabulary and mean nothing to anyone in a cockpit.
    _REJECT_CLAUSES: dict[str, str] = {
        "implausible_body": "the body name did not pass the plausibility check",
        "unreadable_units": (
            "the unit on a coordinate could not be read, and a dropped k is a "
            "thousandfold error"
        ),
        "frame_mismatch": "the SolarSystem and Root lines did not agree",
        "coord_mismatch": "the two position readings disagreed",
        "no_game_world": (
            "the reading sits at the game's non-gameplay origin, not a place "
            "in the world"
        ),
    }

    # NOT ONE ACTION FOR THE WHOLE KIND. The reasons divide into two
    # groups, and telling the player the wrong one costs them either a waypoint
    # or a pointless repetition:
    #
    #   - Three are FRAME QUALITY. A steadier, clearer second look genuinely can
    #     produce a different reading, so asking again is honest.
    #   - `implausible_body` and `no_game_world` are properties of the LOCATION,
    #     not of the frame. The same token, or the same non-gameplay origin,
    #     reads the same way every time and fails the same gate every time
    #     (this is the open `pyro3` item, and every capture at the SC main
    #     menu), so "ask again" would send the player round a loop that cannot
    #     terminate. Their chat badges already say there is no action; a tool
    #     result contradicting the badge is worse than either message alone,
    #     because the player gets both.
    _REJECT_ACTIONS: dict[str, str] = {
        "implausible_body": (
            "There is no action here: looking again produces the same reading "
            "and the same refusal."
        ),
        "unreadable_units": _HOLD_STILL,
        "frame_mismatch": _HOLD_STILL,
        "coord_mismatch": _HOLD_STILL,
        "no_game_world": (
            "There is no action here: this is not a place in the game world, "
            "and looking again produces the same reading."
        ),
    }

    def _capture_error(self, outcome, kind: str = "") -> dict:
        """What to tell the player about a capture that produced nothing.

        ONE table, called from all three tools, because three copies of a
        refusal message drift and two of them already had: `mark_location` told
        the player to switch on an overlay it had just finished READING, and
        `update_position` said something different again with no hint at all.

        Args:
            outcome: The `CaptureOutcome` the capture ended on, or None when a
                caller reached here without one.
            kind: A kind the CALLER can prove from what it holds, which wins
                over the outcome's. The no-frame branch of `mark_location` knows
                the returned position carries no frame; that is a stronger fact
                than a field that may have come from an earlier capture.

        Returns:
            The tool result body: the message, the kind that produced it, and a
            `hint` if and only if the overlay was genuinely not on screen.
        """
        kind = kind or getattr(outcome, "kind", "") or overlay_fields.KIND_UNEXPLAINED
        what, action = self._CAPTURE_MESSAGES.get(
            kind, self._CAPTURE_MESSAGES[overlay_fields.KIND_UNEXPLAINED]
        )
        if kind == overlay_fields.KIND_NO_GRAB:
            what = what.format(display=self._get_display())
        elif kind == overlay_fields.KIND_GATE:
            gate_id, _, clause = (getattr(outcome, "gate", "") or "").partition(": ")
            # ONLY a real gate id may be partitioned off. `_refusal_gate` also
            # returns "gate unknown (ValueError: bad row)" when the diagnostic
            # walk raises, and that string carries its own ": " between the
            # exception type and its message; partitioning it handed the player
            # the message half as if it were a gate's clause, which read as a
            # truncated fragment ending in a stray paren. Anything unrecognised
            # is an unnamed gate, the same as "no gate named".
            if gate_id not in self._GATE_ACTIONS:
                gate_id, clause = "", ""
            if gate_id == "unknown_zone_name":
                clause = "this location's coordinate frame is not supported yet"
            what = what.format(clause=clause or "no gate named itself")
            action = self._GATE_ACTIONS.get(gate_id, self._REDUCE_SCENERY)
        elif kind == overlay_fields.KIND_REJECT:
            reason = getattr(outcome, "reject_reason", "") or ""
            what = what.format(
                clause=self._REJECT_CLAUSES.get(
                    reason, "one of the coordinate checks did not pass"
                )
            )
            # An unrecognised reason is treated as a frame-quality failure,
            # which is the safe default: it asks for one more look rather than
            # telling the player nothing can be done about something that might
            # be recoverable.
            action = self._REJECT_ACTIONS.get(reason, self._HOLD_STILL)
        result = {
            "error": " ".join([what, "Nothing was saved."] + ([action] if action else [])),
            "capture_kind": kind,
        }
        if kind == overlay_fields.KIND_ABSENT:
            result["hint"] = self._OVERLAY_HINT
        return result

    # A real Star Citizen server token looks like
    # pub-euw1b-sc-alpha-490-12232296-game-82. The Vision model misreads this long
    # string: on 2026-07-26 it produced "pub-euw1b-5c-alpha-490-12286454-game-142"
    # (s read as 5) and "pub-euw1b-12269732_070" (structure lost), and two
    # agreeing misreads were enough to DELETE six waypoints. A token that does not
    # match this shape is treated as unreadable and can never trigger a wipe.
    # Failing closed here means at worst stale waypoints, never destroyed ones.
    _SERVER_ID_RE = re.compile(
        r"^pub-[a-z0-9]+-sc-alpha-\d+-\d+-game-\d+$", re.IGNORECASE
    )

    # Consecutive captures that must agree on a new server before wiping. Two was
    # not enough: the same misread twice in a row "confirmed" garbage.
    _SERVER_CHANGE_CONFIRMATIONS = 3

    def _handle_server_change(self, server_id: str) -> None:
        """Wipe stale waypoints and clear navigation on a proven server change.

        "Proven" now requires BOTH a well-formed token and
        _SERVER_CHANGE_CONFIRMATIONS consecutive captures agreeing on it. Two
        agreeing captures were not enough: on 2026-07-26 the model misread the
        server line the same way twice and six waypoints were deleted mid-session
        (second such incident). A malformed read is treated as unreadable and
        cannot trigger anything.

        Safe to call from inside the poll loop: clearing the active target
        makes the loop exit on its next iteration (it already guards on
        `self._active_target is None`).
        """
        if not self._db:
            return

        if not self._SERVER_ID_RE.match(server_id.strip()):
            # Unreadable, not "different". Never let a garbled read delete data.
            self._pending_server_change = None
            self._pending_server_count = 0
            if not getattr(self, "_server_shape_badge_shown", False):
                self._server_shape_badge_shown = True
                self.printr.print(
                    "[NavPoint] The server line could not be read cleanly, so "
                    "waypoints were left alone. Session tracking is degraded "
                    "until it reads clearly again.",
                    color=LogType.WARNING,
                )
            return

        stored = self._db.get_stored_server_id()
        if not stored or stored == server_id:
            # No stored session server yet, or still on the same server: nothing
            # to confirm, and any earlier pending change is now moot.
            self._pending_server_change = None
            self._pending_server_count = 0
            return

        if server_id != self._pending_server_change:
            # First capture reporting this new server: start counting.
            self._pending_server_change = server_id
            self._pending_server_count = 1
            return

        self._pending_server_count = getattr(self, "_pending_server_count", 1) + 1
        if self._pending_server_count < self._SERVER_CHANGE_CONFIRMATIONS:
            return

        # Enough consecutive well-formed captures agree: proven change.
        wiped = self._db.wipe_on_server_change(server_id)
        self._pending_server_change = None
        self._pending_server_count = 0
        if wiped <= 0:
            return

        self.printr.print(
            f"[NavPoint] Server changed - {wiped} waypoint(s) from the previous "
            "session cleared.",
            color=LogType.WARNING,
        )
        self._active_target = None
        self._reset_arrival_state()
        self._stop_nav_polling()
        if self._ui_server:
            self._ui_server.set_active_target(None)
            self._ui_server.notify_update()
        self._sync_ring()

    # ------------------------------------------------------------------ #
    # Auto-polling
    # ------------------------------------------------------------------ #

    _POLL_INTERVAL_DEFAULT = 0.0

    def _get_poll_interval(self) -> float:
        """Capture continuously; no user-configurable delay between reads."""
        return 0.0

    def _get_arrival_alert_m(self) -> float:
        """Read arrival_alert_distance (meters); None/non-numeric -> 1000, negative -> 0 (disabled)."""
        errors: list[WingmanInitializationError] = []
        val = self.retrieve_custom_property_value("arrival_alert_distance", errors)
        if val is None:
            return 1000.0
        try:
            alert_m = float(val)
        except (TypeError, ValueError):
            return 1000.0
        return alert_m if alert_m >= 0 else 0.0

    def _get_arrival_stop_m(self) -> float:
        """Read arrival_stop_distance (meters); None/non-numeric -> 250, negative -> 0 (disabled)."""
        errors: list[WingmanInitializationError] = []
        val = self.retrieve_custom_property_value("arrival_stop_distance", errors)
        if val is None:
            return 250.0
        try:
            stop_m = float(val)
        except (TypeError, ValueError):
            return 250.0
        return stop_m if stop_m >= 0 else 0.0

    _OVERLAY_SIZE_DEFAULT = 220  # the size the ring was designed at

    def _get_overlay_enabled(self) -> bool:
        """Always on. The setting was removed on 2026-07-31 (user's call).

        The ring IS the instrument: with Vision gone it is the only channel that
        reports why a fix is missing, so a switch that turns it off turns the
        skill's own error reporting off with it. Kept as a method rather than
        inlined so the start-up path still reads as a decision, and so a future
        reason to withhold the overlay has somewhere to live.
        """
        return True

    def _get_overlay_size(self) -> int:
        """Read overlay_size in pixels (clamped to 120–600)."""
        errors: list[WingmanInitializationError] = []
        val = self.retrieve_custom_property_value("overlay_size", errors)
        if val is None:
            return self._OVERLAY_SIZE_DEFAULT
        try:
            return max(120, min(600, int(val)))
        except (TypeError, ValueError):
            return self._OVERLAY_SIZE_DEFAULT

    def _get_space_direction_test(self) -> bool:
        """Calibration experiments are available only to development harnesses."""
        return False

    def _sync_ring(self) -> None:
        """Push the current target and fix to the in-game ring.

        One call site's worth of logic in one place: every path that changes
        the target or the position ends here, so the overlay can never disagree
        with the skill about what is being navigated to. Deciding what the ring
        may draw is `navigation.ring_deflection`'s job, not this method's.
        """
        overlay = getattr(self, "_ring_overlay", None)
        if not overlay:
            return
        target = self._active_target
        if target is None:
            overlay.clear()
            return
        position = getattr(self, "_last_position", None)
        age_s = None
        if position is not None:
            captured_at = position.get("captured_at")
            if captured_at is not None:
                age_s = time.time() - captured_at
        target_dict = self._db.navpoint_to_dict(target) if self._db else None
        if target_dict is not None and target_dict.get("frame") == "stack":
            # The HUD summary intentionally omits the stack. The ring and its
            # calibration trace need the saved Root anchors for navigation.
            target_dict["coordinate_stack"] = getattr(target, "coordinate_stack", None)
        space_test = self._get_space_direction_test()
        try:
            deflection, message = ring_status(
                position,
                target_dict,
                age_s,
                getattr(self, "_last_ring_state", ""),
                allow_uncalibrated_space=space_test,
            )
        except LocalFrameUnavailable:
            deflection, message = None, getattr(self, "_last_ring_state", "")
        if (
            deflection is not None
            and space_test
            and target_dict
            and target_dict.get("frame") in ("system", "stack")
        ):
            self._log_space_test(position, target_dict, deflection)
        hold_key = ring_hold_key(position, target_dict)
        distance = ring_distance(position, target_dict, age_s)
        if hold_key is not None:
            overlay.set_state(
                True, deflection, message, hold_key=hold_key,
                read_failed=bool(getattr(self, "_last_ring_state", "")),
                captured_at=(position or {}).get("captured_at"),
                distance=distance,
            )
        else:
            if distance:
                overlay.set_state(True, deflection, message, distance=distance)
            else:
                overlay.set_state(True, deflection, message)

    def _log_space_test(self, position: dict, target: dict, deflection: tuple) -> None:
        """Record one uncalibrated system-frame bubble, and warn once.

        The line carries everything needed to solve the convention afterwards:
        where the camera pointed, where the player was, where the waypoint is,
        and what the current convention made of it.
        """
        camdir = position.get("camdir")
        here = tuple(position.get(k) for k in ("x", "y", "z"))
        there = tuple(target.get(k) for k in ("x", "y", "z"))
        if target.get("frame") == "stack":
            current_stack = coordinate_stack.from_dict(position.get("coordinate_stack"))
            target_stack = coordinate_stack.from_json(target.get("coordinate_stack"))
            if current_stack is not None and target_stack is not None:
                here, there = current_stack.root.xyz, target_stack.root.xyz
        self._poll_trace(
            "SPACE TEST (UNCALIBRATED) "
            f"camdir={tuple(camdir) if camdir else None} "
            f"from={here} to={there} "
            f"yaw={deflection[0]:.2f} pitch={deflection[1]:.2f}",
            chat=False,
        )
        if not getattr(self, "_space_test_badge_shown", False):
            self._space_test_badge_shown = True
            self.printr.print(
                "[NavPoint] Space direction TEST MODE is on. The ring is drawing "
                "an unverified direction from the corrected camera convention. "
                "Do not rely on it yet; it is on screen to be compared "
                "against a direction you already know.",
                color=LogType.WARNING,
            )

    def _set_position(self, pos_data: dict) -> None:
        """Record a capture and fan it out to the HUD page and the ring."""
        self._last_position = pos_data
        if self._ui_server:
            self._ui_server.set_position(pos_data)
        self._sync_ring()

    def _reset_arrival_state(self) -> None:
        """Clear arrival-alert progress at the start or end of a navigation activation."""
        self._arrival_alert_done = False
        self._arrival_stop_streak = 0
        self._slowdown_target_id = None

    async def _speak_navigation_message(self, message: str) -> None:
        """Keep navigation speech queued behind any current Wingman speech."""
        await self.wingman.tts.speak(message, interrupt=False)

    def _queue_navigation_message(self, message: str) -> None:
        """Queue speech through Wingman's supported v3 thread runner."""
        self.wingman.run_in_thread(self._speak_navigation_message, message)

    async def _maybe_announce_slowdown(self, target, position) -> None:
        precision = position.get("precision") or {}
        stamp = position.get("captured_at")
        if (precision.get("mode") != "approach" or not precision.get("slow_down_advisory")
                or precision.get("target_id") != target.id
                or getattr(self, "_slowdown_target_id", None) == target.id
                or stamp is None or not math.isfinite(stamp) or not 0 <= time.time() - stamp <= 2.0
                or getattr(self, "_active_target", None) is not target):
            return
        self._slowdown_target_id = target.id
        message = "Slow down for final approach."
        if self.wingman:
            self._queue_navigation_message(message)
        await self.printr.print_async(f"[NavPoint] {message}", color=LogType.INFO)

    def _on_hud_target_change(self, navpoint: "NavPoint | None") -> None:
        """Adopt a target change the player made on the HUD page.

        The page used to be a read-only mirror with buttons: its Clear, its
        Navigate and its delete only ever moved the server object's own copy of
        the target, while THIS object kept navigating. Clearing on the page left
        the poll loop capturing every few seconds, the ring guiding and the
        arrival announcement armed, which is why Clear looked like it did
        nothing.

        Called on the HUD server's thread, so it touches nothing that needs an
        event loop: polling starts and stops through a threading.Event, and the
        ring publishes under its own lock.
        """
        self._active_target = navpoint
        self._reset_arrival_state()
        if navpoint is None:
            self._stop_nav_polling()
        else:
            self._start_nav_polling()
        self._sync_ring()


    def _poll_trace(self, message: str, chat: bool = True) -> None:
        """Append a poll-loop event to navpoint_poll.log, and mirror to chat.

        Deliberately two channels. The skill's `logger` output does not reach
        wingman-core.log at all, which is why the 4.9.0.19 instrumentation was
        invisible and the test that used it proved nothing. A file write cannot
        be silently swallowed by logging configuration, and printr is the
        channel already proven to land in the log ("Auto-position tracking
        started" arrives that way).

        The file is rotated, not capped: past `POLL_LOG_MAX_BYTES` the live log
        becomes `navpoint_poll.log.1` and a fresh one starts. Anyone handing a
        session over must send BOTH files when a `.1` exists.

        Args:
            message: The line to record.
            chat: Mirror to the chat log as well. Per-capture diagnostics pass
                False: they fire every few seconds and would bury the events
                worth reading, while the file keeps them all.
        """
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            path = os.path.join(self.get_generated_files_dir(), "navpoint_poll.log")
            _rotate_poll_log(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"{stamp} {message}" + chr(10))
        except Exception:
            pass
        if not chat:
            return
        try:
            self.printr.print(f"[NavPoint] {message}", color=LogType.INFO,
                              server_only=True)
        except Exception:
            pass

    def _trace_capture(self, message: str) -> None:
        """Record one capture-path decision in navpoint_poll.log.

        Always file-only. A capture runs on every poll tick, so mirroring these
        to chat would bury the events worth reading, which is the split
        `_poll_trace` already draws. Kept as its own method so the capture path
        cannot accidentally acquire a chat badge per tick, and so every line it
        writes carries the same prefix to grep for.

        Args:
            message: The decision to record, without the `capture ` prefix.
        """
        buffer = _TRACE_BUFFER.get()
        if buffer is not None:
            buffer.append(("trace", message))
            return
        self._poll_trace(f"capture {message}", chat=False)

    def _capture_warning(self, message: str, exc: BaseException) -> None:
        """A capture-path warning for wingman-core.log, held with the trace."""
        buffer = _TRACE_BUFFER.get()
        if buffer is not None:
            buffer.append(("warning", message, exc))
            return
        logging.getLogger(__name__).warning(message, exc)

    def _quiet_capture_attempt(self, reader) -> "overlay_fields.CaptureOutcome":
        """One `_capture_attempt`, logged only if it does not repeat the last refusal.

        The idle calibration and the poll loop capture once a second, so a
        refusal that persists (game not focused, overlay off) used to write the
        same warning and trace every second: 11,388 Core-log lines on
        2026-10-23. The first refusal of a kind is still written in full, rows
        and frame dump included; its repeats are counted, and the count is
        written once the cause changes or a capture succeeds.
        """
        buffer: list = []
        token = _TRACE_BUFFER.set(buffer)
        outcome = None
        try:
            outcome = self._capture_attempt(reader, 1, 1)
            return outcome
        finally:
            _TRACE_BUFFER.reset(token)
            self._flush_capture_trace(buffer, _refusal_signature(outcome))

    def _flush_capture_trace(self, buffer: list, signature: tuple | None) -> None:
        previous = getattr(self, "_last_refusal", None)
        if signature is not None and signature == previous:
            self._refusal_repeats = getattr(self, "_refusal_repeats", 0) + 1
            return
        repeats = getattr(self, "_refusal_repeats", 0)
        if repeats:
            self._trace_capture(
                f"previous refusal repeated {repeats} more time(s), not logged: "
                f"kind={previous[0]} cause={previous[1]!r}"
            )
        self._last_refusal = signature
        self._refusal_repeats = 0
        for entry in buffer:
            if entry[0] == "trace":
                self._trace_capture(entry[1])
            elif entry[0] == "warning":
                logging.getLogger(__name__).warning(entry[1], entry[2])
            else:
                self._dump_refused_frame(entry[1], entry[2])

    @staticmethod
    def _file_stamp(name: str) -> str:
        """Size and mtime of one shipped file, for the build identity line.

        Size and mtime rather than a content hash: both are already on the
        inode, and hashing a 90 KB glyph bank on every activation is not free
        for a question ("is this the build I think it is") that a byte count and
        a timestamp answer just as well.
        """
        try:
            info = os.stat(os.path.join(current_dir, name))
        except OSError:
            return "missing"
        stamp = time.strftime("%Y-%m-%dT%H:%M", time.localtime(info.st_mtime))
        return f"{info.st_size}b@{stamp}"

    def _trace_build_identity(self) -> None:
        """Record which build just activated, once per `prepare()`.

        Without this line the log cannot tell "the skill never ran" from "the
        skill ran and had nothing to say", which is exactly the ambiguity that
        left the three 2026-08-01 13:28 failures unresolvable: they wrote
        nothing at all, and neither reading can be ruled out after the fact. A
        session whose log has no BUILD line is now a session the skill never
        activated in.

        Diagnostics only, so every part of it fails quietly: the geometry is
        gathered separately from the file stamps, because a machine that cannot
        enumerate its monitors should still say which build it was running.
        """
        try:
            parts = [
                f"BUILD version={self.VERSION}",
                f"sc={self.SC_TARGET_VERSION}",
            ]
            for name in ("main.py", "frame_reader.py", "models/specialist_v8/model.onnx"):
                parts.append(f"{name}={self._file_stamp(name)}")
            parts.append(f"display={self._get_display()}")
            parts.append(f"poll={self._get_poll_interval()}s")
            try:
                from mss import mss

                with mss() as sct:
                    monitor = sct.monitors[
                        min(self._get_display(), len(sct.monitors) - 1)
                    ]
                    parts.append(
                        f"monitor={monitor.get('width')}x{monitor.get('height')}"
                    )
                    parts.append(f"region={overlay_reader.block_region(monitor)}")
            except Exception as exc:
                # A machine that cannot enumerate its monitors should still say
                # which build it was running.
                parts.append(f"monitor=unavailable ({type(exc).__name__})")
            self._poll_trace(" ".join(parts), chat=False)
        except Exception as exc:
            # Diagnostics never break an activation.
            logger.debug("Build identity line failed: %s", exc)

    def _attempt_metrics(self, started: float, reader=None, rows=None,
                         frame=None) -> dict:
        """What one attempt measured, for its outcome to carry out with it.

        The reader's alignment provenance is read through getattr because a
        stubbed reader carries none of it, and a missing diagnostic must never
        be the reason a capture fails. The whole body is wrapped for the same
        reason: this runs at four return points including the successful one,
        and it is the only new diagnostic on the path that could otherwise
        propagate. Every other one already swallows its own failures.

        The frame measurements come FIRST, ahead of the row ones, because a row
        that refuses to be measured must not also cost the two numbers taken
        straight from the pixels.

        Args:
            started: `time.perf_counter()` as the attempt began.
            reader: The reader that decoded this frame, when one ran.
            rows: The decoded rows, when the decode got that far.
            frame: The grabbed crop, when its ink and row periodicity are worth
                the two passes over it. Omitted on the attempts whose numbers
                nothing would read.

        Returns:
            The measurements, or as many of them as could be taken.
        """
        metrics: dict = {}
        try:
            metrics["ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            # The whole capture's duration, not this attempt's, taken HERE
            # because every call to this method happens inside the capture lock
            # and the stamp is therefore still this capture's. Read after the
            # lock releases it can already have been restamped by another
            # capture, which reports a SHORTER time than the caller waited.
            metrics["capture_ms"] = round(
                (time.perf_counter() - getattr(self, "_capture_started_at", started))
                * 1000.0,
                1,
            )
            if frame is not None:
                metrics["ink"] = overlay_reader.overlay_ink_fraction(frame)
                metrics["periodicity"] = overlay_reader.overlay_row_periodicity(
                    frame
                )
            if rows is not None:
                metrics["rows"] = len(rows)
                metrics["trusted"] = sum(1 for row in rows if row.get("trusted"))
                costs = [
                    row.get("cost") for row in rows
                    if isinstance(row.get("cost"), (int, float))
                ]
                if costs:
                    metrics["cost"] = sum(costs) / len(costs)
            if reader is not None:
                metrics["from_cache"] = getattr(reader, "last_from_cache", None)
                metrics["refit"] = getattr(reader, "last_refit", None)
                metrics["fit_uses"] = getattr(reader, "last_fit_uses", None)
        except Exception as exc:
            logger.debug("Attempt metrics failed: %s", exc)
        return metrics

    def _capture_ms(self) -> str:
        """Milliseconds since the whole capture began, as a ready trace field.

        Capture-level, not attempt-level: what the player waits for is every
        attempt plus the pauses between them, and that total is what the retry
        policy will be judged on. `?` when the capture start was never recorded,
        which is only reachable by calling an attempt directly.
        """
        started = getattr(self, "_capture_started_at", None)
        if started is None:
            return "ms=?"
        return f"ms={(time.perf_counter() - started) * 1000.0:.0f}"

    def _trace_attempt(
        self,
        attempt: int,
        attempts: int,
        outcome: str,
        started: float,
        detail: str = "",
    ) -> None:
        """Record the single attempt's outcome, duration and rejection detail.

        Legacy attempt fields remain in the trace format for older log readers.
        Runtime captures always publish attempt 1/1.
        """
        elapsed = (time.perf_counter() - started) * 1000.0
        parts = [f"attempt {attempt}/{attempts}", f"outcome={outcome}"]
        if detail:
            parts.append(detail)
        parts.append(f"ms={elapsed:.0f}")
        self._trace_capture(" ".join(parts))

    def _trace_alignment(self, reader, y0) -> None:
        """Record where the block was read from, and whether that origin was fresh.

        `read` cannot return this without breaking its three-value contract, so
        it leaves the provenance on the reader instead. Read through getattr
        because a stubbed reader has none of it, and a missing diagnostic must
        never be an error.

        In the observed failures nothing decodes as trusted, so the self-heal
        refit MUST be firing on every read, which doubles the decode work and is
        a live candidate for the unexplained per-attempt cost. A
        `from_cache=True refit=True` pair on every line confirms that; anything
        else refutes it.
        """
        self._trace_capture(
            f"align y0={y0} "
            f"from_cache={getattr(reader, 'last_from_cache', None)} "
            f"refit={getattr(reader, 'last_refit', None)} "
            f"fit_uses={getattr(reader, 'last_fit_uses', None)}"
        )

    def _refusal_gate(self, rows: list) -> str:
        """Which `overlay_fields` gate refused these rows, for the trace line.

        INSTRUMENTATION, and wrapped because of it. The gate walk runs a second
        time here purely to name itself, and a diagnostic that raised would turn
        a plain refusal into a decode error, changing the very outcome the trace
        exists to explain. The walk parses real overlay text, so it can raise on
        a row shape it was not built for.

        Args:
            rows: The per-row dicts from `OverlayReader.read`.

        Returns:
            `"<gate_id>: <clause>"`, or a note saying why no gate could be named.
        """
        try:
            return overlay_fields.capture_refusal_reason(rows) or "no gate named"
        except Exception as exc:
            return f"gate unknown ({type(exc).__name__}: {exc})"

    def _trace_rows(self, rows: list) -> None:
        """Record what the reader actually saw, one line per decoded row.

        The gate name alone says which question was answered no, not what the
        answer was read from. The `?` characters are the whole point: they mark
        the cells the reader refused, so the row text shows at a glance whether
        a name, a unit or a whole coordinate was lost. Never truncated, because
        a row is 88 cells and the cut-off part is exactly where the answer hides.

        The six fields after the text are all ALREADY COMPUTED by
        `_decode_block` and were being discarded at the point of writing the row
        to a file. They answer what the text alone cannot: how close the row was
        to being trusted (one bad cell is a different problem from twenty),
        whether the whole row is marginal or one cell is an outlier, and whether
        the colour variant that won changed between a working and a failing
        scene. They go AFTER `text=` because months of logs are grepped for the
        first four fields as one contiguous string.

        Args:
            rows: The per-row dicts from `OverlayReader.read`. Empty is a no-op,
                which is the case where the frame never reached the decoder.
        """
        for index, row in enumerate(rows):
            # The reader's own row number, so a line here can be counted off
            # against the block on screen; the position in the list is only the
            # fallback for a row that never came from the reader.
            self._trace_capture(
                f"row[{row.get('row', index)}] kind={row.get('kind')!r} "
                f"trusted={row.get('trusted')} text={row.get('text')!r} "
                f"refused={row.get('refused')} cost={_fmt_cost(row.get('cost'))} "
                f"margin={_fmt_cost(row.get('margin'))} "
                f"colour={row.get('colour')!r} offset={row.get('offset')} "
                f"y={row.get('y')}"
            )

    def _trace_cells(self, rows: list) -> None:
        """Record WHICH of the two refusal conditions lost each refused cell.

        THE ITEM THAT DECIDES THE OCR FIX. A cell refuses when its decode cost
        exceeds the floor OR its margin over the runner-up falls below it, and
        the two point in opposite directions: high cost means nothing in the
        glyph bank matched, low margin means two glyphs matched almost equally.
        One is a bank or render problem and the other is genuine ambiguity at
        this size. The row dump says a row lost four cells; only this says which
        side fired, on which characters, at which column positions.

        Restricted to `zone` and `camdir` rows because those are the only rows
        the skill reads: the FPS row and the percentile bar refuse constantly by
        design and their cells are noise that would dilute the evidence. Capped
        at `MAX_CELL_TRACE_LINES`, so a frame where most of a row refuses cannot
        write hundreds of lines.

        Wrapped, like every other diagnostic on this path. The records come from
        row dicts a stub may not carry, and a measurement must never be the
        reason a capture ends differently.

        Args:
            rows: The per-row dicts from `OverlayReader.read`.
        """
        try:
            written = 0
            for index, row in enumerate(rows):
                if row.get("kind") not in ("zone", "camdir"):
                    continue
                for cell in row.get("refused_cells") or ():
                    if written >= MAX_CELL_TRACE_LINES:
                        return
                    written += 1
                    self._trace_capture(
                        f"cell row={row.get('row', index)} i={cell.get('i')} "
                        f"cost={_fmt_cost(cell.get('cost'))} "
                        f"margin={_fmt_cost(cell.get('margin'))} "
                        f"trip={cell.get('trip')} "
                        f"top1={cell.get('top1')!r}@"
                        f"{_fmt_cost(cell.get('top1_cost'))} "
                        f"top2={cell.get('top2')!r}@"
                        f"{_fmt_cost(cell.get('top2_cost'))}"
                    )
        except Exception as exc:  # noqa: BLE001 - diagnostics never break a capture
            logger.debug("Cell diagnostics failed: %s", exc)

    def _dump_refused_frame(self, frame, kind: str) -> None:
        """Keep the pixels of a refused capture, when the switch is on.

        A per-cell classifier failure is ultimately a question about pixels, and
        the user is the only source of live frames. With a corpus on disk the
        OCR work iterates offline; without one every hypothesis costs a flown
        session, and their time is the scarcest thing in this project.

        ON since 2026-08-02, and only the user turns it on or off. The 667x175
        overlay crop only, never the whole screen. Written locally and
        transmitted nowhere.
        The cap is applied BEFORE the write rather than after, so it bounds what
        this can ever take rather than what it settles back down to. And the
        whole thing is swallowed: a dump that could not happen must never change
        a capture outcome.

        Args:
            frame: The grabbed crop, as the decoder received it.
            kind: The KIND_* id that refused, so the filename says what the
                pixels are an example of.
        """
        buffer = _TRACE_BUFFER.get()
        if buffer is not None:
            buffer.append(("dump", frame, kind))
            return
        if not SAVE_REFUSED_FRAMES:
            return
        try:
            import numpy as np
            from PIL import Image

            folder = os.path.join(self.get_generated_files_dir(), "refused")
            os.makedirs(folder, exist_ok=True)
            existing = sorted(
                name for name in os.listdir(folder) if name.endswith(".png")
            )
            for name in existing[:max(0, len(existing) - REFUSED_FRAME_KEEP + 1)]:
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
            stamp = time.strftime("%Y-%m-%d_%H%M%S")
            path = os.path.join(folder, f"{stamp}_{kind}.png")
            serial = 1
            while os.path.exists(path):
                # Two refusals inside one second would otherwise overwrite each
                # other, and at `poll_interval: 1` that is an ordinary event.
                serial += 1
                path = os.path.join(folder, f"{stamp}_{kind}_{serial}.png")
            Image.fromarray(
                np.clip(np.asarray(frame), 0.0, 255.0).astype(np.uint8)
            ).save(path)
        except Exception as exc:  # noqa: BLE001 - diagnostics never break a capture
            logger.debug("Refused frame dump failed: %s", exc)

    def _start_background_calibration(self) -> None:
        """Keep the shared frame calibration warm while no target is selected."""
        thread = getattr(self, "_calibration_thread", None)
        if thread is not None and thread.is_alive():
            return
        stop = threading.Event()
        self._calibration_stop = stop

        def run():
            ready = False
            while not stop.is_set():
                if getattr(self, "_active_target", None) is None:
                    try:
                        # The foreground check and capture lock are shared with
                        # navigation. One idle sample per second is sufficient
                        # for a tracker that admits samples at one-second gaps.
                        with _capture_sink(), _full_precision_capture():
                            position = self._capture_position_local(
                                calibration_only=True, stop_event=stop)
                        now_ready = any((position or {}).get(key, {}).get("valid")
                                        for key in ("planet_rotation", "site_rotation"))
                        if now_ready and not ready:
                            self._poll_trace("background calibration ready", chat=False)
                        ready = now_ready
                    except Exception as exc:
                        logger.debug("Background calibration capture failed: %s", exc)
                stop.wait(1.0)

        self._calibration_thread = threading.Thread(
            target=run, name="navpoint-calibration", daemon=True)
        self._calibration_thread.start()
        self._poll_trace("background calibration started (idle captures)", chat=False)

    def _stop_background_calibration(self) -> None:
        stop = getattr(self, "_calibration_stop", None)
        if stop is not None:
            stop.set()
        thread = getattr(self, "_calibration_thread", None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if thread is None or not thread.is_alive():
            self._calibration_thread = None

    def _poll_stopping(self) -> bool:
        """True once a stop has been requested. Missing event means 'not stopping'.

        getattr rather than a plain attribute so harnesses that build the skill
        with __new__ (bypassing __init__) can still drive the loop directly.
        """
        event = getattr(self, "_poll_stop", None)
        return bool(event is not None and event.is_set())

    def _start_nav_polling(self) -> None:
        """Start position polling on a DEDICATED THREAD with its own event loop.

        Not asyncio.create_task: a task created inside a tool call belongs to the
        event loop running that call, and Wingman does not keep that loop alive
        afterwards. The coroutine therefore reached its first `await` and was
        silently abandoned, which is why auto-tracking, the arrival heads-up and
        the auto-stop had never once worked. Proven 2026-07-26 by a trace that
        logged "poll loop ENTERED" and never the tick after the first sleep.

        This is the same fix sc_log_reader already needed for the same cause: own
        thread, own loop, so nothing outside can stop it.
        """
        self._stop_nav_polling()
        interval = self._get_poll_interval()
        self._poll_stop = threading.Event()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._nav_polling_loop(interval))
                self._poll_trace("poll loop returned normally")
            except Exception as e:
                self._poll_trace(f"poll thread DIED: {type(e).__name__}: {e}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        self._poll_thread = threading.Thread(
            target=_runner, name="navpoint-poll", daemon=True
        )
        self._poll_thread.start()
        self._poll_trace(f"poll THREAD started (interval {interval}s)")
        self.printr.print(
            "[NavPoint] Auto-position tracking started — continuous fresh captures",
            color=LogType.INFO,
            server_only=True,
        )

    def _stop_nav_polling(self) -> None:
        """Ask the polling thread to stop; never block the caller waiting for it."""
        event = getattr(self, "_poll_stop", None)
        if event is not None:
            event.set()
        thread = getattr(self, "_poll_thread", None)
        if thread is not None and thread.is_alive():
            # A capture in flight can take seconds; the thread is a daemon and
            # checks the stop flag each iteration, so it is left to finish.
            self._poll_trace("poll stop requested")
        self._poll_thread = None
        # Legacy task handle, kept so an older in-flight task still gets cancelled.
        if getattr(self, "_nav_task", None) is not None:
            task = self._nav_task
            if not task.done():
                task.cancel()
            self._nav_task = None

    async def _nav_polling_loop(self, interval: float) -> None:
        """Capture sequentially without a rate cap; interval is legacy-only."""
        alert_m = self._get_arrival_alert_m()
        stop_m = self._get_arrival_stop_m()
        # Poll health, logged on failure so a silently dying loop is visible.
        polls_ok = 0
        polls_failed = 0
        # If this line never appears, the task was created but never scheduled,
        # which means the event loop it was created on did not survive the tool
        # call that started it.
        self._poll_trace("poll loop ENTERED")
        try:
            while self._active_target is not None and not self._poll_stopping():
                # Yield for cancellation, without delaying the next fresh frame.
                await asyncio.sleep(0)
                if self._poll_stopping():
                    self._poll_trace("stop requested, loop exiting")
                    break
                if polls_ok + polls_failed == 0:
                    self._poll_trace("first poll tick (loop is alive)")

                if self._active_target is None:
                    self._poll_trace("target cleared, loop exiting")
                    break

                try:
                    with _capture_sink():
                        pos_data = await self._capture_position()
                except Exception as e:
                    polls_failed += 1
                    self._poll_trace(
                        f"capture RAISED ({polls_ok} ok / {polls_failed} failed): "
                        f"{type(e).__name__}: {e}"
                    )
                    self._last_ring_state = overlay_fields.RING_NO_SIGNAL
                    self._sync_ring()
                    await asyncio.sleep(.25)
                    continue

                usable_fix = bool(pos_data) and (
                    all(k in pos_data for k in ("x", "y", "z"))
                    or pos_data.get("coordinate_stack")
                )
                if not usable_fix:
                    polls_failed += 1
                    reason = getattr(self._scanner, "last_reject_reason", None)
                    self._poll_trace(
                        f"no usable position ({polls_ok} ok / {polls_failed} failed), "
                        f"reject reason={reason!r}"
                    )
                    # The ring must still be told. Without this the message set
                    # by the read never reaches the overlay, because every other
                    # path to _sync_ring runs only on SUCCESS, and a refusal
                    # would be silent everywhere: exactly the thing that removing
                    # Vision made the ring responsible for saying out loud.
                    self._sync_ring()
                    outcome = getattr(self, "_last_outcome", None)
                    if getattr(outcome, "kind", None) in (
                        overlay_fields.KIND_NO_GRAB, overlay_fields.KIND_NO_READER
                    ):
                        # No image was read. Avoid spinning while the game or
                        # reader is unavailable; OCR refusals remain uncapped.
                        await asyncio.sleep(.25)
                    continue

                polls_ok += 1
                if polls_ok == 1 or polls_ok % 12 == 0:
                    self._poll_trace(f"position updated ({polls_ok} ok / {polls_failed} failed)")

                # Feed the HUD and the ring whenever coordinates are present, so
                # they can draw position or a no-local-frame state regardless of
                # body.
                self._set_position(pos_data)

                # Re-read the target AFTER the capture: it takes seconds, and
                # stop_navigation or a delete can clear it in the meantime. Every
                # use below would otherwise dereference None. This raced from the
                # day it was written but could not fire while the loop was dead
                # (see the 4.9.0.19 entry); the first live run that actually
                # polled crashed here within a minute.
                target = self._active_target
                if target is None:
                    self._poll_trace("target cleared during capture, loop exiting")
                    break

                await self._maybe_announce_slowdown(target, pos_data)
                if (pos_data.get("precision") or {}).get("mode") == "cruise":
                    # Coarse cells may guide the approach, but never prove arrival.
                    continue

                if getattr(target, "frame", "") == "stack":
                    decision = self._stack_route(target, pos_data)
                    if decision.mode == coordinate_stack.MODE_REFUSED:
                        self._poll_trace(
                            f"stack route refused ({decision.reason})", chat=False
                        )
                        continue
                    arrival_distance = decision.distance_m
                    if not decision.allow_arrival:
                        # Confirmed open space can arrive at a fixed Root point.
                        # Reuse the same frame/session/anchor/freshness checks as
                        # the ring; camera angles are not required for distance.
                        stack_target = self._db.navpoint_to_dict(target)
                        stack_target["coordinate_stack"] = target.coordinate_stack
                        stamp = pos_data.get("captured_at")
                        space = stack_space_guidance(
                            pos_data, stack_target,
                            time.time() - stamp if stamp is not None else None,
                        )
                        if space is None:
                            continue
                        arrival_distance = space["distance_m"]
                    if await self._announce_arrival(
                        target, arrival_distance, alert_m, stop_m
                    ):
                        return
                    continue

                pos_body = pos_data.get("body", "")
                if not active_frame.same_planet(pos_data, active_frame.target_snapshot(target)):
                    # No local frame, or a different body: never emit a bearing
                    # across frames.
                    logger.debug(
                        "Nav poll: no bearing (position body %r vs target body %r)",
                        pos_body,
                        target.body,
                    )
                    continue

                bearing = calculate_bearing(
                    from_pos=(pos_data["x"], pos_data["y"], pos_data["z"]),
                    to_pos=(target.x, target.y, target.z),
                    current_heading=pos_data.get("heading", 0.0),
                use_local_frame=True,
                )
                logger.debug(
                    "Nav poll update: %s, %s",
                    target.name,
                    format_distance(bearing["distance_km"]),
                )

                if await self._announce_arrival(
                    target, bearing["distance_km"] * 1000.0, alert_m, stop_m
                ):
                    return
        except asyncio.CancelledError:
            pass  # Normal shutdown

    @staticmethod
    def _stack_route(target: NavPoint, pos_data: dict | None):
        """The route for a schema-1 waypoint, from the two stacks alone.

        ONE selector for both `navigate_to` and the poll loop, so activation and
        tracking cannot disagree about which frame is live. Everything it needs
        is on the stored row and on the capture; nothing here reads a zone name,
        a body, a frame id or a server id.
        """
        if (pos_data or {}).get("body"):
            return coordinate_stack.RouteDecision(coordinate_stack.MODE_REFUSED, "planet_frame_active")
        return coordinate_stack.select_route(
            coordinate_stack.from_json(getattr(target, "coordinate_stack", None)),
            coordinate_stack.from_dict((pos_data or {}).get("coordinate_stack") or {}),
        )

    # What the player is told about a stack route, per mode. ROOT APPROACH says
    # coarse out loud: for a target on a rotating surface the saved Root position
    # is deliberately stale within a body-sized envelope, so a distance quoted
    # without that word would read as a precision it does not have.
    _STACK_MODE_LABEL = {
        coordinate_stack.MODE_ROOT_APPROACH: "ROOT APPROACH",
        coordinate_stack.MODE_LOCAL_TRACKING: "LOCAL",
    }

    def _build_stack_guidance(self, target: NavPoint, decision) -> str:
        """One sentence of guidance for a stack route, carrying no coordinates."""
        distance = format_spoken_distance((decision.distance_m or 0.0) / 1000.0)
        if decision.mode == coordinate_stack.MODE_LOCAL_TRACKING:
            return f"{target.name} is {distance} away."
        return (
            f"{target.name} is roughly {distance} away. This is a coarse "
            "approach figure and no direction is available yet; close the "
            "distance and ask again."
        )

    async def _announce_arrival(
        self, target: NavPoint, dist_m: float, alert_m: float, stop_m: float
    ) -> bool:
        """The two-stage arrival policy, for whichever route produced the distance.

        Shared rather than duplicated per route: an arrival rule that differed
        between the legacy path and the stack path would be two rules to keep in
        step, and the debounce is the part that stops a single flickering read
        announcing a landing.

        The caller decides WHETHER a distance may drive arrival at all. For a
        stack waypoint that is either `RouteDecision.allow_arrival` or a fresh,
        matching open-space Root address. Ambiguous and planetary Root routes
        cannot announce arrival.

        Returns:
            True when navigation completed and the loop must stop.
        """
        # Stage 1: one-shot approach heads-up. Fires at most once per
        # activation. When the target is already inside the stop zone, mark it
        # done without announcing so the imminent arrival does not double up.
        if alert_m > 0 and not self._arrival_alert_done and dist_m <= alert_m:
            self._arrival_alert_done = True
            if not (stop_m > 0 and dist_m <= stop_m):
                msg = (
                    f"Approaching: {format_spoken_distance(dist_m / 1000.0)}."
                )
                if self.wingman:
                    self._queue_navigation_message(msg)
                self.printr.print(f"[NavPoint] {msg}", color=LogType.INFO)

        # Stage 2: arrival auto-stop, confirmed by two consecutive in-zone
        # polls. An out-of-zone poll resets the streak; a poll that produced no
        # usable distance never reaches here, so it neither counts nor resets.
        if stop_m > 0:
            if dist_m <= stop_m:
                self._arrival_stop_streak += 1
                if self._arrival_stop_streak >= 2:
                    msg = "Arrived."
                    if self.wingman:
                        self._queue_navigation_message(msg)
                    self.printr.print(f"[NavPoint] {msg}", color=LogType.POSITIVE)
                    self._active_target = None
                    self._stop_nav_polling()
                    if self._ui_server:
                        self._ui_server.set_active_target(None)
                    self._sync_ring()
                    return True
            else:
                self._arrival_stop_streak = 0
        return False

    def _navpoint_to_result_dict(self, np: NavPoint) -> dict:
        """Tool-result view of a waypoint, deliberately WITHOUT raw coordinates.

        Everything returned here is read by the conversation model and can end
        up spoken. Raw XYZ is useless to a listener and actively harmful: a
        system-frame coordinate is an eleven-digit number, and reading three of
        them aloud made the TTS switch language mid-sentence (2026-07-26). The
        HUD gets full coordinates through database.navpoint_to_dict, which is a
        separate serialiser for a display that can actually use them.
        """
        return {
            "id": np.id,
            "name": np.name,
            "server_id": np.server_id,
            "body": np.body,
            "display_location": display_name(np.frame_id, np.body),
            "system": np.system,
            "zone": np.zone,
            "frame": np.frame,
            "timestamp": np.timestamp,
        }

    _LOCATION_NAME_RE = re.compile(r"^Location (\d+)", re.IGNORECASE)

    def _next_location_number(self) -> int:
        """Next global 'Location N' index: 1 + the highest surviving N.

        Scanned across every stored name on every body. The guarantee is
        anti-collision, not a monotonic counter: because it always lands past
        the surviving maximum, deleting a middle-numbered waypoint never lets a
        later mark reuse that number and duplicate a live name. Deleting the
        current-highest waypoint DOES free its number for reuse, since the scan
        keeps no memory of retired indices. Returns 1 when nothing matches.
        """
        highest = 0
        for np in self._db.get_navpoints():
            match = self._LOCATION_NAME_RE.match(np.name)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def _build_guidance(self, target: NavPoint, bearing: dict) -> str:
        """Build a concise navigation guidance string."""
        dist_str = format_spoken_distance(bearing["distance_km"])
        if (bearing.get("precision") or {}).get("mode") == "cruise":
            dist_str = "Approximately " + dist_str + " (coarse)"
        turn = bearing["turn_instruction"]
        elev = bearing["elevation_instruction"]
        parts = [dist_str, turn]
        if elev != "Level":
            parts.append(elev)
        if (bearing.get("precision") or {}).get("slow_down_advisory"):
            parts.append("Slow down for final approach")
        return " | ".join(parts)

    @staticmethod
    def _active_guidance(position, target, bearing):
        """Publish only direction fields approved by the camera ring."""
        bearing["precision"] = position.get("precision") or {}
        for key in ("horizontal_bearing_deg", "direction_label", "horizontal_offset_deg", "vertical_angle_deg"):
            bearing.pop(key, None)
        from sc_voice_cockpit.navpoint.navigation import _turn_instruction, _elevation_instruction
        offsets = ring_deflection(position, active_frame.target_snapshot(target), 0)
        if offsets is None:
            bearing.update(turn_instruction="Direction unavailable", elevation_instruction="Level")
        else:
            bearing.update(horizontal_offset_deg=offsets[0], vertical_angle_deg=offsets[1],
                           turn_instruction=_turn_instruction(offsets[0]),
                           elevation_instruction=_elevation_instruction(offsets[1]))
        return bearing

    def _build_approach_guidance(self, target: NavPoint) -> str:
        """High-level guidance when current position is unknown."""
        steps = []
        if target.system:
            steps.append(f"System: {target.system}")
        if target.body:
            steps.append(f"Go to {target.body}")
        if target.zone and target.zone != target.body:
            steps.append(f"Zone: {target.zone}")
        steps.append(f"Local coords: {target.x:.0f}, {target.y:.0f}, {target.z:.0f}")
        return " → ".join(steps)

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #

    async def execute_tool(self, tool_name, parameters, benchmark):
        """Speak navigation confirmation verbatim, without an LLM recap."""
        result, speech = await super().execute_tool(tool_name, parameters, benchmark)
        if tool_name != "navigate_to" or speech:
            return result, speech
        try:
            data = json.loads(result)
        except (ValueError, TypeError):
            return result, "Navigation could not be started."
        if not isinstance(data, dict):
            return result, "Navigation could not be started."
        if data.get("success"):
            name = (data.get("target") or {}).get("name")
            return result, f"Navigating to {name}." if name else "Navigation started."
        # Refusals must still state the real cause and any required action.
        speech = " ".join(str(data[key]) for key in ("error", "hint") if data.get(key))
        return result, speech or "Navigation could not be started."

    @tool(
        description=(
            "Save the player's CURRENT position as a named waypoint in the "
            "NavPoint database. This is an ACTION tool that creates a persistent "
            "waypoint, and it is the ONLY correct tool when the player says "
            "'mark location', 'mark my location', 'drop a waypoint', 'save my "
            "position', 'place a nav marker', 'drop a pin', 'remember this spot', "
            "or similar. Never answer such requests by only reporting where the "
            "player is (for example via a game-state tool): reporting a location "
            "does not mark anything. Captures and analyzes the screen to extract "
            "coordinates from the r_displayinfo overlay. Works on a planet or moon "
            "surface AND in open space. After saving, say the coordinates were "
            "logged, name the location, and ask for a name if not provided. Never "
            "read coordinate numbers aloud."
        ),
        wait_response=True,
    )
    async def mark_location(self, name: str = "") -> str:
        """
        Mark current position as a named waypoint.

        Args:
            name: Name for the waypoint. Leave empty to use a default name like 'Location 1'.
        """
        if not self._db:
            return json.dumps({"error": "NavPoint skill not initialized"})

        # The one path where a refused frame is unrecoverable: the player asked
        # once and is waiting. Nothing else here asks for more than one look.
        with _full_precision_capture(), _capture_sink() as sink:
            pos_data = await self._capture_position(attempts=MARK_CAPTURE_ATTEMPTS)
        outcome = sink[-1] if sink else getattr(self, "_last_outcome", None)
        if not pos_data:
            return json.dumps(self._capture_error(outcome))

        if (pos_data.get("precision") or {}).get("mode") == "cruise":
            return json.dumps({"error": "A coarse navigation read cannot be saved as a precise waypoint."})

        # A COORDINATE STACK WINS WHENEVER ONE WAS CAPTURED. It is the frame
        # this skill marks in now: Root is the address, and no zone name, row
        # index, prefix or magnitude takes part in storing it. The two legacy
        # frames below still exist for reading waypoints stored before schema 1
        # and are never written again once a stack is available.
        stack = coordinate_stack.from_dict(pos_data.get("coordinate_stack") or {})
        if stack is not None and not pos_data.get("body"):
            return json.dumps(self._mark_stack(stack, pos_data, name))

        # Two frames can be marked. A body-fixed one on a surface, and the star
        # system's own frame in open space: a point not attached to a rotating
        # body keeps a stable system-frame address (measured 2026-07-26). The
        # OOC gate still decides which, so this widens what can be captured
        # without weakening the gate that catches a stale-body misread.
        is_space = not pos_data.get("body") and pos_data.get("system_frame")

        if not pos_data.get("body") and not is_space:
            # The overlay was ON SCREEN and READ; there is simply no frame at
            # this location. Telling the player to enable it here was the
            # clearest case of the wrong hint, and the model relays hints
            # verbatim, so it was read out as an instruction.
            return json.dumps(
                self._capture_error(outcome, overlay_fields.KIND_NO_FRAME)
            )

        auto_named = not name
        if auto_named:
            where = display_name(pos_data.get("frame_id", ""), pos_data["body"]) if not is_space else (
                pos_data.get("location") or "deep space"
            )
            name = f"Location {self._next_location_number()}, {where}"

        navpoint = self._db.add_navpoint(
            name=name,
            server_id=pos_data.get("server_id", ""),
            body="" if is_space else pos_data["body"],
            x=pos_data["x"],
            y=pos_data["y"],
            z=pos_data["z"],
            system=pos_data.get("system", ""),
            zone=pos_data.get("zone", ""),
            heading=pos_data.get("heading", 0.0),
            frame="system" if is_space else "local",
            frame_id=pos_data.get("frame_id", "") if is_space else pos_data.get("planet_id", ""),
        )

        location_label = (
            pos_data.get("display_location")
            or pos_data.get("zone")
            or pos_data.get("body")
            or pos_data.get("system")
            or "unknown location"
        )

        # Notify the HUD
        if self._ui_server:
            self._ui_server.notify_update()

        return json.dumps({
            "success": True,
            "navpoint": self._navpoint_to_result_dict(navpoint),
            "location_label": location_label,
            "message": f"Waypoint '{name}' saved at {location_label}.",
            "coordinates": "logged",
            "speak_hint": (
                "Confirm the waypoint was saved and name the location. Say that "
                "the coordinates have been logged; do NOT read out any numbers."
            ),
            "ask_for_name": auto_named,
        })

    def _mark_stack(self, stack, pos_data: dict, name: str) -> dict:
        """Store one accepted coordinate stack as a schema-1 waypoint.

        `x/y/z` MIRROR Root. That is a compatibility behaviour, recorded rather
        than left to be discovered: the HUD page, the listing and the existing
        serialiser all read `x/y/z` on every row, and a stack row with zeroes
        there would draw at the origin.

        `server_id` is stored as whatever the capture carried, which for this
        reader is always empty. The reader deliberately never emits one, because
        it is DETERMINISTIC on identical pixels, so the "three consecutive
        captures agreeing" wipe rule measures nothing but elapsed time when it
        is fed from here. An empty server_id can never make both sides of
        `wipe_on_server_change`'s comparison non-empty, so marking a stack
        waypoint cannot delete anything.

        Args:
            stack: The validated `CoordinateStack` from this capture.
            pos_data: The full capture, for the optional display fields.
            name: The player's name for the waypoint, or "" to auto-name it.

        Returns:
            The tool result dict, without coordinates of any kind.
        """
        auto_named = not name
        if auto_named:
            # No zone text goes into the name. It is OCR output that may carry
            # the reader's `?` marker, and a waypoint the player has to read
            # back later should not be named after a mangled token.
            where = pos_data.get("body") or pos_data.get("location") or ""
            number = self._next_location_number()
            name = f"Location {number}, {where}" if where else f"Location {number}"

        navpoint = self._db.add_navpoint(
            name=name,
            server_id=pos_data.get("server_id", ""),
            body="",
            x=stack.root.x_m,
            y=stack.root.y_m,
            z=stack.root.z_m,
            system="",
            zone="",
            heading=pos_data.get("heading", 0.0),
            frame="stack",
            coordinate_schema=coordinate_stack.SCHEMA_VERSION,
            root_x=stack.root.x_m,
            root_y=stack.root.y_m,
            root_z=stack.root.z_m,
            coordinate_stack=coordinate_stack.to_json(stack),
        )

        if self._ui_server:
            self._ui_server.notify_update()

        location_label = pos_data.get("body") or pos_data.get("location") or "here"
        return {
            "success": True,
            "navpoint": self._navpoint_to_result_dict(navpoint),
            "location_label": location_label,
            "message": f"Waypoint '{name}' saved.",
            "coordinates": "logged",
            "speak_hint": (
                "Confirm the waypoint was saved. Say that the coordinates have "
                "been logged; do NOT read out any numbers, labels or zone names."
            ),
            "ask_for_name": auto_named,
        }

    @tool(
        description=(
            "Open the NavPoint HUD in the browser. Shows all saved waypoints and the navigation compass. "
            "Call when player says 'show navigation HUD', 'open waypoints', 'show nav HUD', "
            "'open navpoint', or similar."
        )
    )
    def show_navpoint_hud(self) -> str:
        """Open the NavPoint HUD in the default browser."""
        if not self._ui_window:
            return json.dumps({"error": "NavPoint HUD not available — missing dependencies"})
        self._ui_window.open()
        url = self._dashboard_url()
        return json.dumps({"success": True, "url": url, "message": "NavPoint HUD opened."})

    @tool(
        description=(
            "List all stored navigation waypoints. Can filter by body (planet or moon). "
            "Call when player says 'list my waypoints', 'show saved locations', "
            "'what waypoints do I have', or similar."
        )
    )
    def list_navpoints(self, body: str = "") -> str:
        """
        List stored waypoints.

        Args:
            body: Filter by body (planet or moon). Leave empty to list all bodies.
        """
        if not self._db:
            return json.dumps({"error": "NavPoint skill not initialized"})

        navpoints = self._db.get_navpoints(body=body or None)
        if not navpoints:
            msg = "No waypoints saved"
            if body:
                msg += f" on {body}"
            return json.dumps({"navpoints": [], "message": msg})

        return json.dumps({
            "navpoints": [self._navpoint_to_result_dict(n) for n in navpoints],
            "count": len(navpoints),
            "bodies": self._db.get_distinct_bodies(),
        })

    @tool(
        description=(
            "Set a stored waypoint as the active navigation target and show direction guidance. "
            "Also captures current position to calculate bearing if possible. "
            "Call when player says 'navigate to [name]', 'guide me to [name]', "
            "'how do I get to [name]', 'set destination to [name]', or similar."
        ),
        summarize=False,
        wait_response=False,
    )
    async def navigate_to(self, name: str) -> str:
        """
        Set a stored waypoint as the active navigation target.

        Args:
            name: Name (or partial name) of the waypoint to navigate to.
        """
        if not self._db:
            return json.dumps({"error": "NavPoint skill not initialized"})

        navpoint = self._db.find_navpoint_by_name(name)
        if not navpoint:
            matches = self._db.search_navpoints(name)
            if not matches:
                return json.dumps({
                    "error": f"Waypoint '{name}' not found.",
                    "hint": "Use 'list waypoints' to see all saved locations.",
                })
            navpoint = matches[0]

        # Capture position first so a proven cross-body target is refused before
        # any target is set or polling starts.
        with _full_precision_capture(), _capture_sink() as sink:
            pos_data = await self._capture_position()
        outcome = sink[-1] if sink else getattr(self, "_last_outcome", None)
        pos_body = pos_data.get("body", "") if pos_data else ""
        pos_frame = self._capture_frame(pos_data)
        target_frame = getattr(navpoint, "frame", "local")

        # A CAPTURE THAT PROVED NOTHING IS NOT A DEFERRED STATE. Both frame
        # gates below key on a TRUTHY `pos_frame`, and `_capture_frame` returns
        # None exactly when nothing was established, so a total capture failure
        # walked past both of them, past the frame-id gate, set the target,
        # started the poll thread and reported "bearing tracking begins once a
        # local frame is available" as though it were an ordinary wait. A
        # station interior did the same: it returns a payload with no frame in
        # it. Neither can begin tracking, and both must say so.
        if pos_frame is None:
            # A payload that came back frameless proves its own kind, whatever
            # the outcome field happens to hold.
            proven = overlay_fields.KIND_NO_FRAME if pos_data else ""
            return json.dumps(
                self._capture_error(outcome, proven) | {"target_not_set": True}
            )

        # A SCHEMA-1 WAYPOINT ROUTES ON ITS STACK, and on nothing else. None of
        # the frame, body and frame-id gates below apply to it: they compare
        # names and per-session ids, and a stack waypoint is addressed by Root,
        # which survives a renamed zone and a new SolarSystem id alike.
        stack_decision = None
        if target_frame == "stack":
            stack_decision = self._stack_route(navpoint, pos_data)
            if stack_decision.mode == coordinate_stack.MODE_REFUSED:
                return json.dumps({
                    "error": (
                        f"'{navpoint.name}' cannot be routed from here: this "
                        "capture carries no usable coordinate stack."
                    ),
                    "reason": stack_decision.reason,
                    "target_not_set": True,
                })

        # A coordinate means a different place in each frame, so mixing them
        # would produce a confident and completely wrong bearing. Refuse before
        # any target is set or polling starts.
        if stack_decision is None and pos_frame and pos_frame != target_frame:
            if target_frame == "system":
                message = (
                    f"'{navpoint.name}' is a deep-space waypoint and you are on "
                    f"{pos_body}; its coordinates only mean anything in open "
                    "space. Leave the surface first."
                )
            else:
                message = (
                    f"'{navpoint.name}' is on {navpoint.body} and you are in open "
                    "space; surface coordinates only mean anything in that body's "
                    f"own frame. Travel to {navpoint.body} first."
                )
            return json.dumps({
                "error": message,
                "target_frame": target_frame,
                "current_frame": pos_frame,
                "target_body": navpoint.body,
                "current_body": pos_body,
            })

        if (stack_decision is None and pos_frame == "local"
                and not active_frame.same_planet(pos_data, active_frame.target_snapshot(navpoint))):
            # Proven different body: refuse, leave target and polling untouched.
            return json.dumps({
                "error": (
                    f"Waypoint is on {navpoint.body}, you are on {pos_body}; "
                    "cross-body navigation is not supported, travel to "
                    f"{navpoint.body} first."
                ),
                "target_body": navpoint.body,
                "current_body": pos_body,
            })

        # Same frame TYPE is not enough for space: the system frame is per
        # session, so a waypoint recorded under a different SolarSystem zone id
        # is not addressable from here. Schema-1 rows are exempt, and that
        # exemption is the point: Root is not per session, so a stack waypoint
        # stays addressable after the opaque suffix changes.
        if stack_decision is None and pos_frame == "system":
            here_id = pos_data.get("frame_id", "")
            there_id = getattr(navpoint, "frame_id", "")
            if here_id and there_id and here_id != there_id:
                return json.dumps({
                    "error": (
                        f"'{navpoint.name}' was marked in a different system frame "
                        "than the one you are in now, so its coordinates no longer "
                        "point anywhere meaningful."
                    ),
                    "target_frame_id": there_id,
                    "current_frame_id": here_id,
                })

        self._active_target = navpoint
        self._reset_arrival_state()

        if self._ui_server:
            self._ui_server.set_active_target(navpoint)

        result: dict = {
            "success": True,
            "target": self._navpoint_to_result_dict(navpoint),
        }

        if stack_decision is not None:
            result["mode"] = self._STACK_MODE_LABEL.get(
                stack_decision.mode, stack_decision.mode
            )
            result["route_reason"] = stack_decision.reason
            result["guidance"] = self._build_stack_guidance(navpoint, stack_decision)
            stack_target = self._db.navpoint_to_dict(navpoint)
            stack_target["coordinate_stack"] = navpoint.coordinate_stack
            captured_at = pos_data.get("captured_at")
            stack_bearing = stack_space_guidance(
                pos_data, stack_target,
                time.time() - captured_at if captured_at is not None else None,
            )
            if stack_bearing is not None:
                result["bearing"] = stack_bearing
                result["guidance"] = self._build_guidance(navpoint, stack_bearing)
            elif not stack_decision.allow_direction:
                result["note"] = (
                    "Distance only. A direction needs the local frame, which is "
                    "unavailable at this range."
                )
            self._set_position(pos_data)
        elif pos_frame == "system" and all(k in pos_data for k in ("x", "y", "z")):
            # Open space: no north exists, so guidance is distance plus offsets
            # relative to where the player is actually pointing.
            bearing = space_guidance(
                pos_data.get("camdir"),
                (pos_data["x"], pos_data["y"], pos_data["z"]),
                (navpoint.x, navpoint.y, navpoint.z),
            )
            result["bearing"] = bearing
            result["guidance"] = self._build_guidance(navpoint, bearing)
            self._set_position(pos_data)
        elif pos_body and all(k in pos_data for k in ("x", "y", "z")):
            # Same planetary frame; camera-relative guidance matches the ring.
            bearing = calculate_bearing(
                from_pos=(pos_data["x"], pos_data["y"], pos_data["z"]),
                to_pos=(navpoint.x, navpoint.y, navpoint.z),
                current_heading=pos_data.get("heading", 0.0),
                use_local_frame=True,
            )
            bearing = self._active_guidance(pos_data, navpoint, bearing)
            result["bearing"] = bearing
            result["guidance"] = self._build_guidance(navpoint, bearing)
            # Standing inside the alert zone at activation: the tool result
            # already reports the distance, so suppress the next poll's heads-up.
            # Stop logic is intentionally left to the poll loop's two-poll check.
            alert_m = self._get_arrival_alert_m()
            if alert_m > 0 and bearing["distance_km"] * 1000.0 <= alert_m:
                self._arrival_alert_done = True
            self._set_position(pos_data)
        else:
            # No local frame yet: set the target with approach guidance only.
            result["guidance"] = self._build_approach_guidance(navpoint)
            result["note"] = (
                f"Bearing tracking begins once a local frame on {navpoint.body} "
                "is available."
            )
            # No usable fix, but the target is live: the ring comes up showing
            # its no-fix face rather than staying dark and looking broken.
            self._sync_ring()

        # Start sequential fresh captures without a fixed polling cap.
        self._start_nav_polling()
        result["tracking"] = "Auto-tracking active: continuously reading fresh frames."

        # The native ring works independently; the dashboard is opened by the
        # player using its chat link or an explicit show_navpoint_hud request.
        hud_url = self._dashboard_url()
        if hud_url:
            result["hud_url"] = hud_url
            result["hud_note"] = (
                "The saved-locations dashboard is available from the link in "
                "Wingman's chat. Open it when needed; navigation does not "
                "open the browser automatically."
            )
        overlay = getattr(self, "_ring_overlay", None)
        if overlay and overlay.failed_reason is None:
            result["overlay_note"] = (
                "The deflection ring is drawn over the game at the top centre of "
                "the screen. It needs Star Citizen in borderless windowed mode: "
                "exclusive fullscreen bypasses the desktop compositor and nothing "
                "can draw over it."
            )

        return json.dumps(result)

    @tool(
        description=(
            "Refresh current position and update bearing to the active navigation target. "
            "Call when player says 'update position', 'refresh bearing', 'where am I', "
            "'how far', or 'how far to target'."
        ),
        wait_response=True,
    )
    async def update_position(self) -> str:
        """Capture current position and refresh navigation bearing."""
        if not self._db:
            return json.dumps({"error": "NavPoint skill not initialized"})

        with _capture_sink() as sink:
            pos_data = await self._capture_position()
        outcome = sink[-1] if sink else getattr(self, "_last_outcome", None)
        if not pos_data:
            return json.dumps(self._capture_error(outcome))
        # A TRUTHY payload with no frame in it is a station interior, and this
        # tool used to report it as a position: `x`, `y` and `z` all null,
        # dressed as a successful reading. `mark_location` and `navigate_to`
        # both refuse it; this one reported a fix that does not exist. The kind
        # is passed explicitly because the returned payload PROVES it, which is
        # stronger than a field that may hold an earlier capture's verdict.
        if self._capture_frame(pos_data) is None:
            return json.dumps(
                self._capture_error(outcome, overlay_fields.KIND_NO_FRAME)
            )

        pos_body = pos_data.get("body", "")
        result: dict = {
            "position": {
                "x": pos_data.get("x"),
                "y": pos_data.get("y"),
                "z": pos_data.get("z"),
                "zone": pos_data.get("zone", ""),
                "body": pos_body,
            }
        }

        if self._active_target and all(k in pos_data for k in ("x", "y", "z")):
            np = self._active_target
            result["active_target"] = np.name
            if active_frame.same_planet(pos_data, active_frame.target_snapshot(np)):
                bearing = calculate_bearing(
                    from_pos=(pos_data["x"], pos_data["y"], pos_data["z"]),
                    to_pos=(np.x, np.y, np.z),
                    current_heading=pos_data.get("heading", 0.0),
                use_local_frame=True,
                )
                bearing = self._active_guidance(pos_data, np, bearing)
                result["bearing"] = bearing
                result["guidance"] = self._build_guidance(np, bearing)
            elif not pos_body:
                result["note"] = (
                    "No local frame present, so a bearing to "
                    f"{np.name} on {np.body} is unavailable here."
                )
            else:
                result["note"] = (
                    f"Target {np.name} is on {np.body}, you are on {pos_body}; "
                    "no cross-body bearing."
                )

            self._set_position(pos_data)

        return json.dumps(result)

    @tool(
        description=(
            "Stop active navigation and cancel auto-tracking. "
            "Call when player says 'stop navigation', 'cancel navigation', 'stop tracking', "
            "'stop following me', or 'clear destination'."
        )
    )
    def stop_navigation(self) -> str:
        """Stop the active navigation target and cancel auto-tracking."""
        if not self._active_target:
            return json.dumps({"message": "No active navigation target."})

        name = self._active_target.name
        self._active_target = None
        self._reset_arrival_state()
        self._stop_nav_polling()

        if self._ui_server:
            self._ui_server.set_active_target(None)
        self._sync_ring()

        return json.dumps({
            "success": True,
            "message": f"Navigation to '{name}' stopped. Auto-tracking cancelled.",
        })

    @tool(
        description=(
            "Delete a saved waypoint by name. "
            "Call when player says 'delete waypoint [name]', 'remove [name]', "
            "'delete location [name]', or similar."
        )
    )
    def delete_navpoint(self, name: str) -> str:
        """
        Delete a stored waypoint.

        Args:
            name: Exact or partial name of the waypoint to delete.
        """
        if not self._db:
            return json.dumps({"error": "NavPoint skill not initialized"})

        navpoint = self._db.find_navpoint_by_name(name)
        if not navpoint:
            matches = self._db.search_navpoints(name)
            if not matches:
                return json.dumps({"error": f"Waypoint '{name}' not found."})
            navpoint = matches[0]

        self._db.delete_navpoint(navpoint.id)
        if self._active_target and self._active_target.id == navpoint.id:
            self._active_target = None
            self._reset_arrival_state()
            self._stop_nav_polling()
            if self._ui_server:
                self._ui_server.set_active_target(None)
            self._sync_ring()

        if self._ui_server:
            self._ui_server.notify_update()

        return json.dumps({"success": True, "message": f"Waypoint '{navpoint.name}' deleted."})

    @tool(
        description=(
            "Rename a stored waypoint. "
            "Call when player says 'rename [old] to [new]', 'name this waypoint [name]', "
            "'call it [name]', or 'label this location [name]'."
        )
    )
    def rename_navpoint(self, old_name: str, new_name: str) -> str:
        """
        Rename a stored waypoint.

        Args:
            old_name: Current name of the waypoint.
            new_name: New name to assign.
        """
        if not self._db:
            return json.dumps({"error": "NavPoint skill not initialized"})

        navpoint = self._db.find_navpoint_by_name(old_name)
        if not navpoint:
            matches = self._db.search_navpoints(old_name)
            if not matches:
                return json.dumps({"error": f"Waypoint '{old_name}' not found."})
            navpoint = matches[0]

        self._db.rename_navpoint(navpoint.id, new_name)
        if self._active_target and self._active_target.id == navpoint.id:
            self._active_target = self._db.find_navpoint_by_id(navpoint.id)

        if self._ui_server:
            self._ui_server.notify_update()

        return json.dumps({
            "success": True,
            "message": f"Waypoint renamed from '{navpoint.name}' to '{new_name}'.",
        })
