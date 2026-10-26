"""Turn decoded overlay rows into the payload the scanner already validates.

The seam is deliberate: `overlay_reader` owns pixels to rows, this module owns
rows to fields, and `NavPointScanner.parse_payload` owns fields to position
data. The reader emits the SAME payload shape a Vision response carries, so the
local path inherits the echo canaries, the plausibility gate, the mandatory unit
suffixes and the SolarSystem/Root agreement check unchanged. One validation
path, so a guard added for either reader cannot silently miss the other.

Everything here fails closed. `trusted` on a row means "every cell was read",
which is NOT the same as "this row contains a payload": a row rendered as
`Zone: Root Pos:` with the coordinates absent comes back trusted at cost 0.00,
and an opaque panel over part of the block decodes as confident spaces, which
truncates a coordinate mid-axis while leaving the row trusted with the right
label. Only a per-kind FULL-line shape rejects those, so every field below is
matched against the whole stripped text.

Credit: Mallachi
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np

from sc_voice_cockpit.navpoint import coordinate_stack
from sc_voice_cockpit.navpoint.coordinate_stack import CoordinateStack, StackRow
from sc_voice_cockpit.navpoint.overlay_reader import MAX_ROWS, OverlayReader, capture_is_usable


logger = logging.getLogger(__name__)

# A coordinate component as displayed: optional sign, digits, optional
# decimals, then a MANDATORY unit suffix. The overlay picks the unit per axis by
# magnitude and mixes them in one line (`9810.08m -174.2880km 238.0511km`), so a
# dropped "k" is a silent factor-of-1000 error with a plausible-looking value.
_TOKEN = r"[+-]?\d+(?:\.\d+)?(?:km|m)"
_NUMBER = r"[+-]?\d+(?:\.\d+)?"

# Full-line shapes. Each requires the complete line, including its trailing
# structure, so a truncated or empty payload cannot match. `CamDir:` is anchored
# on the `FOV:` that follows it for the same reason.
# Coordinate separators are exactly one displayed space. A missing leading
# sign or digit decodes as an EXTRA blank cell; accepting arbitrary whitespace
# can silently change BOTH Root and Solar identically and defeat their check.
# CamDir is separately padded to fixed-width fields and still needs \s+.
_OOC_RE = re.compile(
    rf"^Zone: OOC_(\S+) Pos: ({_TOKEN}) ({_TOKEN}) ({_TOKEN})$"
)
_SYSTEM_RE = re.compile(
    rf"^Zone: SolarSystem_(\d+) Pos: ({_TOKEN}) ({_TOKEN}) ({_TOKEN})$"
)
_ROOT_RE = re.compile(
    rf"^Zone: Root Pos: ({_TOKEN}) ({_TOKEN}) ({_TOKEN})$"
)
_INFO_ZONE_RE = re.compile(
    rf"^Zone: (\S+) Pos: {_TOKEN} {_TOKEN} {_TOKEN}$"
)
_CAMDIR_RE = re.compile(
    rf"^CamDir:\s+({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s+FOV:"
)

# THE COORDINATE-STACK PAIR, and they are deliberately two regexes rather than
# one. The stack rework's whole premise is that a zone NAME has no authority, so
# a `?` anywhere in the name must not contaminate coordinate cells that decoded
# perfectly. `_STACK_NAME_RE` answers "which row is this" and `_STACK_POS_RE`
# answers "what does it say", and neither can refuse on the other's evidence.
#
# Both SEARCH rather than anchor at position 0, for the reason recorded on
# `_largest_component`: the block is right-aligned, so every row carries a blank
# left margin, and a background star inside that margin decodes as a phantom
# glyph that would defeat a `^Zone:` anchor.
#
# What rejects a truncated or occluded row is the requirement of THREE tokens,
# not the `$`: two readable components never satisfy the pattern. That is the
# guard against the case recorded at the top of this module, that `trusted` does
# not mean "carries a payload". The `$` is the narrower second job of refusing
# anything trailing the third component, so a row that packed another field
# after its Pos values could not be read as a clean stack row.
_STACK_NAME_RE = re.compile(r"Zone: (?P<name>\S*) Pos:")
_STACK_POS_RE = re.compile(
    rf"Zone: \S* Pos: ({_TOKEN}) ({_TOKEN}) ({_TOKEN})$"
)

# The exact semantic Root label. Equality, never containment: `Root_Cellar` is a
# different zone, and an OCR-fragmented Root label is not the anchor either.
_ROOT_NAME = "Root"
_SOLAR_PREFIX = "SolarSystem_"

# Labels that identify a row as something an OOC line cannot be. A row whose
# label decoded confidently is answering a different question than the one the
# space-branch gate asks.
_NOT_ZONE_KINDS = frozenset({"camdir", "session", "shard", "server"})

_ZONE_LABEL_RE = re.compile(r"Zone:\s+")

# MIRROR of scanner._KNOWN_SYSTEMS. Kept local rather than imported so this
# module stays on the pixels-to-fields side of the seam and does not pull the
# Vision path's PIL and mss imports into the reader. `test_overlay_fields_gate`
# pins the two together so they cannot drift.
_KNOWN_SYSTEMS = ("stanton", "pyro", "nyx")

REFUSED = "?"


def ooc_hypothesis_refuted(kind: str | None, text: str) -> bool:
    """Can this row be PROVEN not to be an `OOC_` line?

    The space branch is a claim about absence, so an unreadable row above the
    SolarSystem line blocks it. But the gate only ever needs to know whether the
    row could be an OOC line, which is far weaker than reading the row. A ship
    zone called `Hangar_SmallFront_RestStop_Pyro` refuses today purely because
    `H` and `g` have no template, even though the cells that DID decode already
    prove it is not `OOC_...`.

    Refutation uses only characters the reader decoded confidently, treating
    `?` as a wildcard that could be anything. Fails closed: anything it cannot
    disprove is treated as a possible OOC line.

    Args:
        kind: The row's label classification from the reader, or None.
        text: The row's stripped decoded text.

    Returns:
        True when the row provably is NOT an OOC line.
    """
    if kind in _NOT_ZONE_KINDS:
        # The row's own label decoded as something else entirely.
        return True
    match = _ZONE_LABEL_RE.search(text)
    if match is None:
        # The `Zone:` label itself did not decode, so nothing here can rule the
        # row out by shape.
        return False
    rest = text[match.end():]
    if len(rest) < len("OOC_"):
        return False
    for observed, expected in zip(rest, "OOC_"):
        if observed != REFUSED and observed != expected:
            return True

    # The `OOC_` prefix survived, so test what would follow it. An OOC token is
    # `<System>_<designator>_<Body>` and the scanner's plausibility gate already
    # requires a KNOWN leading system, so a first segment that cannot spell one
    # refutes the hypothesis using a rule the payload would be judged by anyway.
    token = rest[len("OOC_"):]
    if "_" not in token:
        # The segment is not delimited, so it may simply be cut short.
        return False
    segment = token.split("_", 1)[0]
    for system in _KNOWN_SYSTEMS:
        if len(segment) == len(system) and all(
            observed == REFUSED or observed.lower() == expected
            for observed, expected in zip(segment, system)
        ):
            return False
    return True


# The FLOOR of body scale. Measured across every capture this project holds:
# ship 0.03 m to 12 m, room 220 m to 600 m, station 1.8 km, body 123 km to
# 410 km. The gap between 2 km and 100 km is empty, so 50 km sits in the middle
# of nothing rather than near a boundary.
#
# This used to be an open-ended floor, which also caught the rest-stop
# container `P5_?2` at 28,000 km, on the reasoning that it "is not open space
# either". Reversed 2026-08-01: that reading refused EVERY capture at P5 L2,
# and a Lagrange or rest-stop container does not rotate, so a system-frame
# waypoint inside one stays put. See the ceiling below.
_BODY_SCALE_M = 50_000.0

# THE CEILING IS A CHOSEN THRESHOLD, NOT A MEASURED CONSTANT. Body scale is a
# WINDOW, not a floor: a container far larger than any body is a Lagrange or
# rest-stop zone sitting in open space, and its frame does not rotate, so a
# system-frame waypoint inside it stays put. Observed bodies run 123 km to
# 410 km and observed containers 19,125 km (P5_02) to 44,707 km (P5 L2, where
# every capture was refused on 2026-08-01 until this window existed).
#
# The P5 L2 figure was recorded as 41,726 km when this window was written, which
# is its x axis rather than its largest component. Corrected to 44,707 km on
# 2026-08-01 evening from a 15 frame capture taken at the location. Nothing
# moved: both readings sit about 3x above the ceiling, and the window was
# measured holding on all 15 frames with no flicker.
#
# 15,000 km is the HIGHEST value that still clears the smallest container seen.
# High is the safe direction here, not low: raising the ceiling only ever
# refuses MORE, and refusing costs a retry while clearing wrongly costs a
# waypoint that goes stale. The upper bound of real body scale is unmeasured,
# so the margin below 19,125 km is deliberately the thing being spent, and any
# container between 15,000 km and 19,125 km will refuse until it is measured.
_CONTAINER_SCALE_M = 15_000_000.0


def _largest_component(text: str) -> str:
    """The biggest readable coordinate on a zone line, as displayed.

    Deliberately does NOT require the full line shape, unlike every parser
    above. Those decide whether to ACCEPT data, so they demand a whole clean
    line. This one decides whether to REFUSE, so a partially readable row is
    still evidence: `Zone: p?ro3 Pos: 330.7421km ???.????km 119.0037km` has an
    unreadable body name and two unreadable axes, and still says plainly that
    the player is 330 km from the container origin. Being permissive here fails
    CLOSED, which is the safe direction.

    SEARCHES for the label rather than anchoring on it. Anchoring made this
    guard fail OPEN: a background star inside the block decodes as a phantom
    glyph in the right-aligned text's left margin, `?   Zone: P5_L2 Pos: ...`
    stopped looking like a zone line, and the row silently cleared a gate built
    to stop a waypoint going stale. Observed 2026-08-01 at P5 L2, where the
    same row returned body-scale True and False on consecutive frames as the
    star drifted through its band.
    """
    if _ZONE_LABEL_RE.search(text or "") is None:
        return ""
    best, shown = 0.0, ""
    for token in re.findall(_TOKEN, text):
        value = abs(float(token[:-2] if token.endswith("km") else token[:-1]))
        if token.endswith("km"):
            value *= 1000.0
        if value > best:
            best, shown = value, token
    return shown


def _is_body_scale(text: str) -> bool:
    """Whether a zone line's own Pos puts the player at planetary distance.

    A WINDOW, not a floor. Above the ceiling the container is far bigger than
    any body and is a Lagrange or rest-stop zone in open space, whose frame
    does not rotate. Reading that as a body surface refused every capture at
    P5 L2 on 2026-08-01.
    """
    shown = _largest_component(text)
    if not shown:
        return False
    value = abs(float(shown[:-2] if shown.endswith("km") else shown[:-1]))
    if shown.endswith("km"):
        value *= 1000.0
    return _BODY_SCALE_M <= value <= _CONTAINER_SCALE_M


# THE FIVE REFUSAL GATES, as stable ids. A refusal is invisible in the field
# without them: this module's `logger` output reaches no file the player can
# read, so the id is what `main._capture_position_local` writes to
# navpoint_poll.log and what gets grepped out of that log months later. Treat
# them as an interface. The clause after the id may be reworded freely; the id
# before the colon may not.
GATE_NO_SYSTEM_LINE = "no_system_line"
GATE_REFUSED_ABOVE = "refused_above"
GATE_BODY_SCALE = "body_scale"
GATE_NO_COORDINATE = "no_coordinate"
GATE_NO_ROOT = "no_root"

# THE COORDINATE-STACK GATES, same interface rule as the five above: the id
# before the colon is grepped out of navpoint_poll.log months later and may not
# be reworded. Separate ids from the legacy gates on purpose, because the two
# parsers ask different questions of the same rows and a shared id would make a
# log unable to say which one refused.
GATE_STACK_NO_ROOT = "stack_no_root"
GATE_STACK_NO_SOLAR = "stack_no_solar"
GATE_STACK_DUPLICATE_ANCHOR = "stack_duplicate_anchor"
GATE_STACK_ORDER = "stack_order"
GATE_STACK_ROOT_UNREADABLE = "stack_root_unreadable"
GATE_STACK_SOLAR_UNREADABLE = "stack_solar_unreadable"
GATE_STACK_DISAGREE = "stack_solar_root_disagree"


# HOW A CAPTURE ENDED, as stable ids, for the same "treat them as an interface"
# reason as the gates above. There are more than a dozen distinct failure
# producers on the capture path and, until these existed, exactly one channel to
# report them: a `None` return. `NavPointScanner.last_reject_reason` is not a
# second channel, in two verified ways. It stays None when `parse_payload`
# refuses a payload carrying no coordinates at all, and it can be SET on a
# branch that still returns a payload, so any code that reads it without first
# checking whether a payload came back will misclassify. Hence KIND_UNEXPLAINED
# and KIND_NO_FRAME, the two producers that were on nobody's list.
KIND_OK = "ok"
KIND_NO_READER = "no_reader"
KIND_NO_GRAB = "no_grab"
KIND_UNUSABLE = "unusable"
KIND_ABSENT = "absent"
KIND_DECODE_ERROR = "decode_error"
KIND_GATE = "gate"
KIND_REJECT = "reject"
KIND_UNEXPLAINED = "unexplained"
KIND_NO_FRAME = "no_frame"


@dataclass(frozen=True)
class CaptureOutcome:
    """Everything one capture attempt established, including how it ended.

    Frozen because it is a record of something that already happened: a
    consumer that could edit it could make the log and the message disagree.

    There is deliberately NO `source` field. With one reader it would be a
    constant, and a constant field invites a future reader to believe there is a
    second producer. `metrics` is the useful carrier instead.

    Attributes:
        kind: One of the KIND_* ids above.
        position: The validated position data, or None.
        overlay_present: `overlay_is_present` for the frame, or None when the
            question was never asked (the grab itself failed).
        frame_usable: `capture_is_usable` for the frame, or None as above.
        ring_state: One of the RING_* strings, as the ring was left.
        gate: A GATE_* id with its clause, or "".
        reject_reason: A `last_reject_reason` value, or "".
        attempts: How many grab-and-decode attempts had been made.
        metrics: The diagnostic block, for the trace and for later analysis.
        detail: Free text for the trace. Never shown to the player.
    """

    kind: str
    position: dict | None = None
    overlay_present: bool | None = None
    frame_usable: bool | None = None
    ring_state: str = ""
    gate: str = ""
    reject_reason: str = ""
    attempts: int = 0
    metrics: dict = field(default_factory=dict)
    detail: str = ""


def payload_from_rows(rows: list[dict]) -> dict | None:
    """Turn legacy decoded rows into a structured coordinate payload.

    `NavPointScanner.parse_payload` applies the shared plausibility gate,
    mandatory unit suffixes and SolarSystem/Root agreement check. Historical
    reader replays therefore retain these checks without reimplementing them.

    What this deliberately does NOT emit:
      - `server_id`. The `Server:` line is structurally unreadable (lowercase
        `v`, `j`, `q` and `z` have no template at either phase), and more
        importantly the reader is DETERMINISTIC on identical pixels, so the
        live "three consecutive captures agreeing" rule measures nothing but
        elapsed time when this reader feeds it. A waypoint wipe must not be
        driven from here. The ShardId line is not a substitute: it carries a
        different token shape than the Server line the wipe rule was built on.
      - `x`/`y`/`z` from `CamPos Planet Zone`, which SC 4.9 omits on a surface.

    A thin wrapper over `_payload_and_reason` so that every existing caller
    keeps the one-value contract it was written against, and so there is still
    exactly one function to point at when asking what the reader decided.

    Args:
        rows: The per-row dicts from `OverlayReader.read`.

    Returns:
        A payload dict, or None when no frame could be established. Fails
        closed: three unit-suffixed tokens or nothing.
    """
    return _payload_and_reason(rows)[0]


def refusal_reason(rows: list[dict]) -> str:
    """Which gate refused these rows, as `"<gate_id>: <clause>"`.

    The empty string when a payload WAS produced, so an empty reason and a
    refusal cannot be confused.

    Deliberately re-runs the gate walk rather than caching the last result. The
    walk is a pure function of `rows`, so the id it names is the id that
    refused, and no shared state can go stale between the decision and the
    explanation. The cost is a second pass over about ten short strings, paid
    only on a refusal.

    Args:
        rows: The per-row dicts from `OverlayReader.read`.

    Returns:
        One of the GATE_* ids followed by a human clause, or "".
    """
    return _payload_and_reason(rows)[1]


def capture_payload(rows: list[dict]) -> tuple[dict | None, str]:
    """What ONE capture established, from both parsers, and what refused it.

    THE RUNTIME ENTRY POINT, and deliberately a different function from
    `payload_from_rows`. That one is the legacy name-dependent parser and keeps
    its contract exactly: roughly forty test call sites reach its guards, every
    waypoint stored before schema 1 depends on its behaviour, and its five gate
    ids are what months of `navpoint_poll.log` are grepped for. Changing what it
    returns would have rewritten the assertions that encode this project's most
    expensive lesson.

    So the DISPATCH lives here instead, composing the module's two public
    producers rather than reaching past them. The legacy payload wins when it exists,
    so an `OOC_` surface or an open-space capture behaves exactly as it did and
    simply gains a stack. When the legacy parser refuses and the stack does not,
    the stack carries the capture on its own, which is the whole point of the
    rework: `body_scale` refuses a Pyro surface outright because no `OOC_` line
    exists to route it, and the stack reads the same rows needing no name at
    all. No legacy waypoint is ever stored against a legacy gate's objection,
    because the legacy fields simply are not in a stack-only payload.

    Returns:
        (payload, reason). Reason is "" exactly when payload is not None. When
        BOTH parsers refuse, both gates are named, legacy first, so the existing
        grep keeps finding the id it has always found at the front.
    """
    payload = payload_from_rows(rows)
    if payload is not None:
        return payload, ""

    stack, stack_reason = stack_from_rows(rows)
    if stack is not None:
        # A stack-only capture. `local_frame` is False because there is no OOC
        # body token to speak of, NOT because this is open space; the frame a
        # stack waypoint lives in is decided by `coordinate_stack.select_route`
        # from the stored stack, never from this flag.
        stack_payload: dict = {
            "local_frame": False,
            "coordinate_stack": coordinate_stack.to_dict(stack),
        }
        camdir = _camdir_from_rows(rows)
        if camdir is not None:
            stack_payload["camdir"] = camdir
        return stack_payload, ""

    return None, f"{_safe_refusal_reason(rows)}; stack: {stack_reason}"


def _safe_refusal_reason(rows: list[dict]) -> str:
    """`refusal_reason`, which may never turn a refusal into an exception.

    The reason is INSTRUMENTATION. It re-walks real overlay text purely to name
    itself, so it can raise on a row shape it was not built for, and a
    diagnostic that raised here would change a plain refusal into a decode
    error: the very outcome the reason exists to explain. Same argument already
    recorded at `main._refusal_gate`, applied one layer lower now that the
    reason is produced on the capture path rather than only on the trace path.
    """
    try:
        return refusal_reason(rows) or "no gate named"
    except Exception as exc:  # noqa: BLE001 - a diagnostic never breaks a capture
        return f"gate unknown ({type(exc).__name__}: {exc})"


def capture_refusal_reason(rows: list[dict]) -> str:
    """Which gates refused a whole capture, as `capture_payload` decided it.

    The trace's counterpart to `refusal_reason`, which names only the legacy
    parser's gate and would report a refusal on every stack-only capture that
    actually succeeded.
    """
    return capture_payload(rows)[1]


def _attach_stack(payload: dict, rows: list[dict]) -> None:
    """Put the schema-1 stack on a legacy payload, when one could be built."""
    stack, _ = stack_from_rows(rows)
    if stack is not None:
        payload["coordinate_stack"] = coordinate_stack.to_dict(stack)


def _camdir_from_rows(rows: list[dict]) -> list[float] | None:
    """The CamDir triple, or None when that row was absent or unreadable.

    Shared by both parsers so a stack-only capture keeps the facing the legacy
    payload would have carried. CamDir is capture-time view orientation and is
    deliberately NOT part of the stored stack; it describes where the camera
    pointed, not where the target is.
    """
    for row in rows:
        if not row["trusted"] or row["kind"] != "camdir":
            continue
        match = _CAMDIR_RE.match(row["text"])
        if match:
            return [float(match.group(i)) for i in (1, 2, 3)]
    return None


def _payload_and_reason(rows: list[dict]) -> tuple[dict | None, str]:
    """The name-dependent parser, unchanged: `OOC_` local frame or open space.

    Kept whole rather than folded into the stack path. It still serves every
    waypoint stored before schema 1, roughly forty test call sites reach its
    guards, and its gates are the ones the field logs are grepped for. Every
    `return None` below names its gate, because the alternative measured twice
    in the field is a live probe session against a running game to find out
    which of five branches fired.

    A stack is attached to whatever payload it produces, so one capture yields
    one record. It does not change any decision taken here.

    Returns:
        (payload, reason). Reason is "" exactly when payload is not None.
    """
    ooc: tuple[str, list[str]] | None = None
    system: tuple[str, list[str]] | None = None
    root: list[str] | None = None
    innermost_zone = ""
    camdir: list[float] | None = None
    system_index: int | None = None
    refused_above = 0

    for index, row in enumerate(rows):
        if not row["trusted"]:
            # Remembered, not skipped. See the space-branch gate below. A row
            # that can be PROVEN not to be an OOC line does not block, however
            # badly the rest of it decoded: the gate asks one question, and an
            # unreadable ship or station zone name can still answer it.
            if system_index is None and not ooc_hypothesis_refuted(
                row["kind"], row["text"]
            ):
                refused_above += 1
            continue
        text = row["text"]
        if row["kind"] == "camdir":
            m = _CAMDIR_RE.match(text)
            if m:
                camdir = [float(m.group(i)) for i in (1, 2, 3)]
        elif row["kind"] == "zone":
            m = _OOC_RE.match(text)
            if m:
                ooc = (m.group(1), [m.group(2), m.group(3), m.group(4)])
                continue
            m = _SYSTEM_RE.match(text)
            if m:
                system = (m.group(1), [m.group(2), m.group(3), m.group(4)])
                system_index = index
                continue
            m = _ROOT_RE.match(text)
            if m:
                root = [m.group(1), m.group(2), m.group(3)]
                continue
            # Anything else beginning `Zone:` is informational only. Matching
            # the full shape here too keeps a truncated or fabricated line
            # (`Root_Cellar_042`) out of the stored zone name.
            m = _INFO_ZONE_RE.match(text)
            if m and not innermost_zone:
                innermost_zone = m.group(1)

    if ooc is not None:
        body_token, pos_raw = ooc
        payload: dict = {
            "local_frame": True,
            "body_token": body_token,
            "pos_raw": pos_raw,
            "zone": innermost_zone,
        }
        if camdir is not None:
            payload["camdir"] = camdir
        _attach_stack(payload, rows)
        return payload, ""

    if system is None:
        return None, f"{GATE_NO_SYSTEM_LINE}: no readable SolarSystem line"

    # THE SPACE BRANCH IS A CLAIM ABOUT ABSENCE, so it needs more than a clean
    # SolarSystem line: it needs proof that no OOC line was there to be missed.
    # The Zone stack nests inside out (ship, then the body's OOC_ zone, then
    # SolarSystem, then Root), so an OOC line always sits ABOVE the SolarSystem
    # line, and one unreadable row up there could BE it. Storing a system-frame
    # waypoint while standing on a rotating body is the 2026-07-11 failure that
    # closed the stellar frame in the first place: the coordinate would be
    # correct at capture and metres wrong seconds later.
    #
    # Refusing here costs the capture outright, since nothing runs after a
    # refusal. It is deliberately stricter than the row-level gate, which only
    # proves each row it trusts.
    if refused_above:
        logger.info(
            "Space capture refused: %d unreadable row(s) above the SolarSystem "
            "line, so the absence of an OOC line is not established.",
            refused_above,
        )
        return None, (
            f"{GATE_REFUSED_ABOVE}: {refused_above} unreadable row(s) above the "
            "SolarSystem line, so the absence of an OOC line is not established"
        )

    # A CONTAINER THE SIZE OF A WORLD MEANS YOU ARE STANDING ON ONE.
    #
    # Routing assumed a body's zone is named `OOC_*`. Pyro does not name them
    # that way: on Pyro 3 the stack is `Zone: pyro3 Pos: 330.74km 218.74km
    # 119.00km` -> SolarSystem -> Root, with no OOC line anywhere. The space
    # branch therefore concluded open space and emitted a SYSTEM-frame position
    # while the player stood on a rotating body, which is exactly the 2026-07-11
    # failure: correct at capture, hundreds of metres wrong seconds later.
    # Measured live 2026-07-31, 62 of 62 frames on Pyro 3.
    #
    # This does NOT decide the eventual local-frame rule, which touches the
    # waypoint key and the plausibility gate and is deliberately left open. It
    # only refuses the unsafe case. Scale is the discriminator because it
    # survives naming: a ship or room puts you metres from its origin, a station
    # a couple of kilometres, a planet hundreds. Nothing observed sits between
    # 2 km and 100 km.
    # Untrusted rows count too, and that is the whole point. On Pyro 3 the body
    # row decodes as `Zone: p?ro3 Pos: 330.7421km ???.????km 119.0037km`: the
    # NAME is unreadable, so the refutation gate correctly clears it as not an
    # OOC line, and the coordinate is perfectly readable and says 330 km. Skip
    # untrusted rows here and the unsafe case walks straight through the gap
    # between the two guards.
    if system_index is not None:
        for row in rows[:system_index]:
            text = row["text"]
            if _ZONE_LABEL_RE.search(text or "") is None:
                # Not a zone line, so it describes no container at all.
                continue
            if _is_body_scale(text):
                logger.info(
                    "Space capture refused: a container above the SolarSystem "
                    "line puts the player %s from its origin, which is "
                    "planetary scale, so this is a body surface and not open "
                    "space.",
                    _largest_component(text),
                )
                return None, (
                    f"{GATE_BODY_SCALE}: a container above the SolarSystem line "
                    f"reads {_largest_component(text)} from its origin, which is "
                    "planetary scale, so this is a body surface"
                )
            if not _largest_component(text):
                # A zone row whose coordinates could not be read answers
                # NEITHER question, so it cannot clear the guard. Returning
                # False here is what let a phantom glyph switch the gate off.
                logger.info(
                    "Space capture refused: a zone line above the SolarSystem "
                    "line decoded with no readable coordinate, so it can be "
                    "shown to be neither a world-anchored container nor a "
                    "body surface."
                )
                return None, (
                    f"{GATE_NO_COORDINATE}: a zone line above the SolarSystem "
                    "line decoded with no readable coordinate, so it is neither "
                    "provably a world-anchored container nor provably a body"
                )

    # THE ROOT LINE IS MANDATORY HERE, unlike in `scanner.parse_payload`, whose
    # agreement check runs only `if root is not None`. That was right for a
    # model that can silently omit a field, and it is wrong for a deterministic
    # reader: an absent Root line means an UNREADABLE Root line, which is
    # exactly the fail-closed case. Without it a payload ships with no
    # independent cross-check at all, and 3 of 16 usable frames measured on
    # 2026-07-31 were in that state. Refusing costs the capture; emitting an
    # unverified coordinate costs whatever it is wrong by.
    if root is None:
        logger.info(
            "Space capture refused: no readable Root line, so the SolarSystem "
            "coordinates carry no independent cross-check."
        )
        return None, (
            f"{GATE_NO_ROOT}: no readable Root line, so the SolarSystem "
            "coordinates carry no independent cross-check"
        )

    payload = {
        "local_frame": False,
        "system_zone_id": system[0],
        "system_pos_raw": system[1],
        "root_pos_raw": root,
    }
    if camdir is not None:
        payload["camdir"] = camdir
    _attach_stack(payload, rows)
    return payload, ""


@dataclass(frozen=True)
class _ZoneEntry:
    """One `Zone:`-labelled row, with its identity and its numbers kept apart."""

    index: int
    name: str | None
    coords: tuple[float, float, float] | None
    text: str


def _zone_entries(rows: list[dict]) -> list[_ZoneEntry]:
    """Every zone-labelled row in the window, trusted or not, in screen order.

    TRUST IS NOT CONSULTED HERE, and that is the point of the rework. `trusted`
    means "no cell in this row refused", so one `?` in a ship's name used to
    discard a row whose coordinates were perfect. The tail regex is the real
    trust test for the numbers: a refused digit, sign, decimal or unit simply
    fails to match, so the row contributes no coordinate.
    """
    entries: list[_ZoneEntry] = []
    for index, row in enumerate(rows):
        text = row.get("text") or ""
        if _ZONE_LABEL_RE.search(text) is None:
            continue
        name_match = _STACK_NAME_RE.search(text)
        pos_match = _STACK_POS_RE.search(text)
        coords = None
        if pos_match is not None:
            values = [coordinate_stack.token_to_m(pos_match.group(i)) for i in (1, 2, 3)]
            if all(v is not None for v in values):
                coords = (values[0], values[1], values[2])
        entries.append(
            _ZoneEntry(
                index=index,
                name=name_match.group("name") if name_match else None,
                coords=coords,
                text=text,
            )
        )
    return entries


def stack_from_rows(rows: list[dict]) -> tuple[CoordinateStack | None, str]:
    """Build the schema-1 coordinate stack, or name the gate that refused it.

    Semantic anchors and relative order only. No absolute row number, no zone
    name equality outside the two structural anchors, no `OOC_` prefix, no
    coordinate magnitude and no Z pass-through: every one of those is refuted in
    `ledger/REFUTED.md` and none of them appears below.

    Fails closed as a WHOLE. When Root or Solar cannot be established the entire
    payload is refused rather than salvaged, because the alternative is
    promoting a deeper row around a failed outer one, which is the row-index
    rule that keyed waypoints to furniture.

    Args:
        rows: The per-row dicts from `OverlayReader.read`, already limited to
            the opening position-row window by the caller.

    Returns:
        (stack, reason). Reason is "" exactly when stack is not None.
    """
    entries = _zone_entries(rows)

    roots = [e for e in entries if e.name == _ROOT_NAME]
    if len(roots) > 1:
        return None, (
            f"{GATE_STACK_DUPLICATE_ANCHOR}: {len(roots)} rows carry the exact "
            "Root label, so which one anchors the stack is not established"
        )
    if not roots:
        return None, (
            f"{GATE_STACK_NO_ROOT}: no row carries the exact Root label, so the "
            "stack has no long-range address"
        )
    root_entry = roots[0]

    solars = [
        e for e in entries
        if e.name is not None
        and e.name.startswith(_SOLAR_PREFIX)
        and len(e.name) > len(_SOLAR_PREFIX)
    ]
    if len(solars) > 1:
        return None, (
            f"{GATE_STACK_DUPLICATE_ANCHOR}: {len(solars)} rows carry a "
            "SolarSystem label, so the stack boundary is not established"
        )
    if not solars:
        return None, (
            f"{GATE_STACK_NO_SOLAR}: no row carries a SolarSystem label, so the "
            "boundary between nested and long-range rows is not established"
        )
    solar_entry = solars[0]

    # SolarSystem must sit DIRECTLY before Root, with no zone row between them
    # and none below Root. A zone row in either position means the stack is not
    # the shape this parser was written against, and guessing which reading was
    # intended is exactly the improvisation the plan's stop conditions forbid.
    if solar_entry.index >= root_entry.index:
        return None, (
            f"{GATE_STACK_ORDER}: the SolarSystem row does not sit above the "
            "Root row"
        )
    if any(solar_entry.index < e.index < root_entry.index for e in entries):
        return None, (
            f"{GATE_STACK_ORDER}: a zone row sits between SolarSystem and Root, "
            "so SolarSystem is not the outermost nested boundary"
        )
    if any(e.index > root_entry.index for e in entries):
        return None, (
            f"{GATE_STACK_ORDER}: a zone row sits below Root, so Root is not the "
            "outermost row"
        )

    if root_entry.coords is None:
        return None, (
            f"{GATE_STACK_ROOT_UNREADABLE}: the Root row's coordinates did not "
            "decode completely with their unit suffixes"
        )
    if solar_entry.coords is None:
        return None, (
            f"{GATE_STACK_SOLAR_UNREADABLE}: the SolarSystem row's coordinates "
            "did not decode completely with their unit suffixes"
        )

    root = StackRow(
        x_m=root_entry.coords[0],
        y_m=root_entry.coords[1],
        z_m=root_entry.coords[2],
        raw_name=_ROOT_NAME,
        raw=root_entry.text,
    )
    solar = StackRow(
        x_m=solar_entry.coords[0],
        y_m=solar_entry.coords[1],
        z_m=solar_entry.coords[2],
        raw_name=solar_entry.name or "",
        raw=solar_entry.text,
    )
    if not coordinate_stack.solar_root_agree(root, solar):
        logger.info(
            "Coordinate stack refused: the SolarSystem and Root rows disagree, "
            "so one of the two readings is corrupt."
        )
        return None, (
            f"{GATE_STACK_DISAGREE}: the SolarSystem and Root rows carry "
            "different numbers, so one of the two readings is corrupt"
        )

    # Depth counts from the innermost row on screen outward, over the zone rows
    # ABOVE SolarSystem, whether or not each one's coordinates decoded. Counting
    # only the readable ones would renumber the survivors when a middle row
    # refused, and the candidate is identified BY its depth.
    above = [e for e in entries if e.index < solar_entry.index]
    nested: list[StackRow] = []
    for depth, entry in enumerate(above):
        if entry.coords is None:
            continue
        nested.append(
            StackRow(
                x_m=entry.coords[0],
                y_m=entry.coords[1],
                z_m=entry.coords[2],
                raw_name=entry.name or "",
                raw=entry.text,
                depth=depth,
            )
        )

    candidate_depth = None
    if above and above[-1].coords is not None:
        candidate_depth = len(above) - 1

    return (
        CoordinateStack(
            root=root,
            solar=solar,
            nested=tuple(nested),
            candidate_depth=candidate_depth,
        ),
        "",
    )


# What the ring tells the player when no payload could be produced. Each string
# is tied to a DIFFERENT action, which is why there are three and not one, and
# why none of them says "malfunctioning": a refusal is the reader correctly
# declining, and instrument-fault wording sends the player hunting for a fault
# that does not exist. Decision and rationale in Devlog 2026-07-31 (night).
RING_OK = ""
# ASCII dots, not U+2026. The ring renders through PIL with a bundled font, and
# a missing glyph would show as a box on the HUD; three dots cannot fail.
RING_ACQUIRING = "ACQUIRING..."      # transient, self-corrects, do nothing
RING_GLARE = "GLARE"                 # look at darker ground
RING_NO_SIGNAL = "NO SIGNAL"         # type `r_displayinfo 2` in the console
# NOT a reader state. The overlay read perfectly and a direction still cannot be
# drawn, because the system-frame convention is uncalibrated (navigation
# .SPACE_DIRECTION_CALIBRATED). Distance is known, bearing is not, and that will
# not change by waiting, so saying ACQUIRING here would promise a fix that
# cannot arrive.
RING_NO_BEARING = "NO BEARING"
# NOT a reader state either. The overlay read perfectly and the scanner accepted
# the payload; there is simply no coordinate frame at this location, which a
# space station or asteroid base interior produces. Waiting cannot produce one,
# so ACQUIRING here would promise a fix that cannot arrive.
#
# It names the player's SITUATION rather than the instrument's internal state,
# so the action is self-evident without a second sentence, which is why `NO
# FRAME` was rejected in its favour (user decision P-1, 2026-08-02). The
# departure from the NO SIGNAL / NO BEARING pattern is deliberate: under the
# suppression rule this string is the ONLY thing on the ring during a no-frame
# capture, so its wording carries more weight than the others' did.
RING_NO_FRAME = "INDOORS"

_POSITION_KINDS = ("OOC_", "SolarSystem_")


def ring_state(rows: list[dict], payload: dict | None,
               overlay_present: bool, capture_usable: bool = True) -> str:
    """Which message the ring should show, or RING_OK when there is nothing to say.

    COSMETIC. This never gates a payload; by the time it is called the decision
    to emit or refuse has already been made by `payload_from_rows`.

    Args:
        rows: The per-row dicts from `OverlayReader.read`.
        payload: The payload, or None when the capture was refused.
        overlay_present: `OverlayReader.overlay_is_present` for this frame.
        capture_usable: `OverlayReader.capture_is_usable` for this frame. False
            means the precondition rejected it as too clipped to decode.

    Returns:
        One of the RING_* strings.
    """
    if payload is not None:
        return RING_OK
    # A frame rejected by the capture precondition is BY DEFINITION too bright,
    # so it is glare whatever the ink test makes of it. Asking `overlay_present`
    # here would be asking a question it cannot answer: a blown-out block has
    # plenty of near-white pixels and no row structure, which reads as "no
    # overlay" and would tell the player to go type a console command they have
    # already typed.
    if not capture_usable:
        return RING_GLARE
    if not overlay_present:
        return RING_NO_SIGNAL
    # The overlay IS on screen. If a position line decoded but the payload was
    # still refused, the refusal is a cross-check that has not landed yet, which
    # recovers on its own; anything else means the scene defeated the reader.
    read_a_position = any(
        row["trusted"] and any(k in (row["text"] or "") for k in _POSITION_KINDS)
        for row in rows
    )
    return RING_ACQUIRING if read_a_position else RING_GLARE


def ring_for_outcome(kind: str, reader_state: str) -> str:
    """What the ring must say once the capture is FULLY decided.

    `ring_state` above answers a narrower question: what the READER made of the
    frame it just decoded. That verdict is written before the scanner has
    validated anything, so on every path where validation is what refused the
    capture it is still RING_OK, and the ring goes on showing a clean face for a
    capture that produced nothing. This wraps it rather than replacing it,
    because for five kinds the reader's answer is the better one and `kind`
    alone cannot reproduce it: the rows-based ACQUIRING-versus-GLARE split
    exists precisely because `absent` and `unusable` want different actions.

    COSMETIC, exactly like `ring_state`. Nothing here gates a payload.

    Args:
        kind: The KIND_* id the capture ended on.
        reader_state: The reader's own verdict for the same capture, from
            `ring_state`.

    Returns:
        One of the RING_* strings.
    """
    if kind == KIND_OK:
        return RING_OK
    if kind == KIND_NO_FRAME:
        return RING_NO_FRAME
    if kind == KIND_NO_READER:
        # Reuses NO SIGNAL knowingly (user decision D-2, 2026-08-02). The
        # imperfection: NO SIGNAL is the string tied to typing `r_displayinfo
        # 2`, and that cannot help when the skill's own reader failed to load,
        # so `no_reader` and `absent` are indistinguishable on the ring.
        # Diagnosis is not lost, because the tool result still carries
        # `capture_kind: "no_reader"` and the poll log still records
        # `REFUSED: reader=unavailable`. Do not "fix" this without asking.
        return RING_NO_SIGNAL
    if kind in (KIND_REJECT, KIND_UNEXPLAINED):
        # One string for all five reject reasons (user decision D-3). The known
        # over-promise: `implausible_body` is not transient, and its badge
        # already says looking again produces the same refusal, while the ring
        # says wait. The two channels disagree for that one reason. Accepted.
        # `reader_state` is RING_OK on these paths anyway, because a payload
        # existed for the scanner to reject.
        return RING_ACQUIRING
    if kind in (
        KIND_NO_GRAB,
        KIND_UNUSABLE,
        KIND_ABSENT,
        KIND_DECODE_ERROR,
        KIND_GATE,
    ):
        # `or` rather than a bare pass-through. In production the reader always
        # has a verdict on these five, so this changes nothing that is reachable
        # today: `absent` gets NO SIGNAL from `ring_state`, `unusable` gets
        # GLARE, `no_grab` is written NO SIGNAL directly. But passing an EMPTY
        # verdict through would leave the ring silent through a refused capture,
        # which is the exact defect this function exists to remove, so the one
        # value that must never survive the mapping is "".
        return reader_state or RING_ACQUIRING
    # Fail VISIBLE, never fail silent. A kind added later with no mapping would
    # otherwise leave the ring clean through a capture that produced nothing.
    return RING_ACQUIRING


def read_legacy_payload(reader: OverlayReader, frame: np.ndarray,
                        rows: int = MAX_ROWS) -> tuple[dict | None, str]:
    """Read one frame end to end through the LEGACY parser ONLY.

    **NOT THE RUNTIME PATH, and the name says so deliberately.** It routes
    through `payload_from_rows`, so it cannot produce a schema-1 coordinate
    stack and refuses every capture the legacy name-dependent parser refuses,
    including any Pyro body surface. `main.py` calls `capture_payload`, which
    dispatches to both parsers; a caller reaching for this one instead would
    silently lose stack support with no error and no failing test.

    It survives because two real-pixel join tests pin the legacy branch end to
    end, which is coverage worth keeping. `test_reader_entry_points` pins that
    no runtime module calls it.

    Args:
        reader: The glyph matcher.
        frame: The raw captured block, unmodified.
        rows: How many rows to decode.

    Returns:
        (payload, reason). Payload is None when the capture was refused, and
        reason names why, for the caller's badge.
    """
    usable, why = capture_is_usable(frame, rows)
    if not usable:
        logger.info("Overlay capture refused: %s", why)
        return None, f"overlay_saturated ({why})"
    _, _, decoded = reader.read(frame, rows)
    payload = payload_from_rows(decoded)
    if payload is None:
        return None, "overlay_unreadable"
    return payload, ""
