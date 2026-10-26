"""The coordinate stack: what a waypoint's address IS, and which frame routes it.

Every earlier routing rule in this skill asked a question about a NAME. Is the
zone called `OOC_*`? Is it row two? Does its scale look like a planet? Each of
those was refuted in turn (`ledger/REFUTED.md`), and each failed the same way:
the overlay's zone names are written by whichever CIG team owns the content, so
a name is an opaque token that a patch may rename and OCR may mangle. This
module therefore holds NO name logic at all. Every `raw_name` and `raw` value
below can be deleted or scrambled without changing a single routing decision,
and `test_coordinate_stack_router` proves it by randomising them.

What replaces the name is position in the stack. The overlay prints the zone
stack from the innermost container outward, always ending
`... -> SolarSystem_<id> -> Root`, so "the row immediately before SolarSystem"
identifies the outermost local container by STRUCTURE rather than by spelling.
Root is the canonical long-range address; the local candidate is used only
inside a strict envelope, and only once live paired captures prove the candidate
picks the frame we think it picks (see `LOCAL_TRACKING_ENABLED`).

Pure by design: no Wingman runtime, no OCR, no database, no logging. Everything
here is a value object or a total function, so the whole routing state machine
is testable without the game running.

Credit: Mallachi
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass


SCHEMA_VERSION = 1

# THE ABSOLUTE LOCAL-TRACKING CEILING, a product invariant and not a tunable.
#
# The largest OM1-to-OM2 separation in the game is about 1.5 Mm, and the user
# chose twice that so an orbital-marker pair is comfortably inside one envelope.
# Above it, a local container's frame cannot be trusted to still describe the
# same place: a rotating body's frame drifts at 137.6 m/s (measured on Pyro 3),
# so a stale local coordinate goes wrong at a rate no distance check can catch.
# Deliberately NOT a YAML property: a user who raised it would silently buy back
# the 2026-07-11 stale-waypoint failure.
LOCAL_TRACKING_MAX_M = 3_000_000.0

# THE EVIDENCE GATE. "The outermost row before SolarSystem is the body/area
# frame" is an implementation hypothesis, not a measured identity. It has never
# been checked against paired captures across two different ships, a landed ship
# against standing outside it, or a jump point, and if it selects a MOVABLE
# container (a ship hull) the skill would track a waypoint that flies away with
# whoever owns that ship.
#
# So the whole local path ships OFF. `select_route` still implements it, the
# tests still prove every boundary, and flipping this to True is the only change
# the rollout needs once `COORDINATE_STACK_IMPLEMENTATION_PLAN.md`'s paired
# capture matrix passes. Until then every schema-1 waypoint routes on Root,
# which is correct everywhere and merely coarse up close.
LOCAL_TRACKING_ENABLED = False

# MIRROR of scanner._SYSTEM_CROSSCHECK_REL / _SYSTEM_CROSSCHECK_MIN_M. Kept
# local so this module stays free of the Vision path's PIL and mss imports, and
# pinned to the originals by `test_coordinate_stack_fields` so the two cannot
# drift. See the same arrangement for `overlay_fields._KNOWN_SYSTEMS`.
SOLAR_ROOT_CROSSCHECK_REL = 1e-9
SOLAR_ROOT_CROSSCHECK_MIN_M = 1.0

MODE_ROOT_APPROACH = "ROOT_APPROACH"
MODE_LOCAL_TRACKING = "LOCAL_TRACKING"
MODE_REFUSED = "REFUSED"

# Stable reason codes. Logged and asserted on, so treat them as an interface:
# the wording of a message may change freely, these strings may not.
REASON_NO_TARGET_STACK = "no_target_stack"
REASON_NO_CURRENT_STACK = "no_current_stack"
REASON_ROOT_AT_OR_BEYOND_LIMIT = "root_at_or_beyond_limit"
REASON_NO_LOCAL_CANDIDATE = "no_local_candidate"
REASON_LOCAL_AT_OR_BEYOND_LIMIT = "local_at_or_beyond_limit"
REASON_LOCAL_DISABLED = "local_tracking_disabled"
REASON_LOCAL_IN_RANGE = "local_in_range"


@dataclass(frozen=True)
class StackRow:
    """One `Zone: ... Pos: ...` row, normalised to metres.

    Frozen because a stack is a record of one capture that already happened; a
    consumer able to edit it could make a stored waypoint and its own log
    disagree about where it is.

    Attributes:
        x_m: Metres along x, converted from whatever unit the row displayed.
        y_m: As above.
        z_m: As above.
        raw_name: The zone name as decoded, DIAGNOSTIC ONLY. May contain the
            reader's `?` refusal marker. Nothing routes on it.
        raw: The whole decoded line, diagnostic only, for the same reason.
        depth: Position among the nested rows above SolarSystem, counted from
            the innermost row on screen (0) outward. None on the Root and Solar
            anchors, which are not nested rows.
    """

    x_m: float
    y_m: float
    z_m: float
    raw_name: str = ""
    raw: str = ""
    depth: int | None = None

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.x_m, self.y_m, self.z_m)


@dataclass(frozen=True)
class CoordinateStack:
    """A full accepted capture: Root, Solar, and every nested row above them.

    Attributes:
        root: The `Zone: Root Pos:` triple. The canonical long-range address.
        solar: The `Zone: SolarSystem_<id> Pos:` triple.
        nested: Every nested row that parsed, in observed screen order, so
            `nested[0]` is the innermost (the seat, room or ship hull) and the
            LAST element is the outermost pre-Solar row whenever the whole stack
            parsed.
        candidate_depth: The depth of the row immediately before SolarSystem,
            set only when that row itself parsed cleanly.

    Why `candidate_depth` exists rather than just taking `nested[-1]`: when the
    outermost row's coordinates refuse and a deeper one reads fine, `nested[-1]`
    is a DEEPER row, and silently routing on it is the row-index rule that was
    killed on 2026-08-01 for keying waypoints to furniture. Recording the
    candidate's depth explicitly makes that substitution impossible; when the
    candidate refused, there is simply no candidate and Root takes the route.
    """

    root: StackRow
    solar: StackRow
    nested: tuple[StackRow, ...] = ()
    candidate_depth: int | None = None

    @property
    def local_candidate(self) -> StackRow | None:
        """The outermost pre-Solar row, or None when it did not read cleanly."""
        if self.candidate_depth is None:
            return None
        for row in self.nested:
            if row.depth == self.candidate_depth:
                return row
        return None


# A displayed coordinate component: optional sign, digits, optional decimals,
# then a MANDATORY unit suffix. MIRROR of scanner._LENGTH_RE, pinned to it by
# `test_coordinate_stack_fields`, and kept local for the same import-hygiene
# reason as the crosscheck constants above.
_LENGTH_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)(km|m)$", re.IGNORECASE)


def token_to_m(token) -> float | None:
    """One displayed Pos component in metres, or None if it is not one.

    The unit suffix is mandatory and never assumed. The overlay picks the unit
    per axis by magnitude and mixes them inside one line, so a dropped "k" is a
    silent factor-of-1000 error that still looks like a plausible coordinate.
    """
    if token is None:
        return None
    match = _LENGTH_RE.match(str(token).strip())
    if match is None:
        return None
    value = float(match.group(1))
    return value * 1000.0 if match.group(2).lower() == "km" else value


def distance_m(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Euclidean distance in metres between two triples in the SAME frame.

    Comparing across frames is meaningless, so no caller may reach this without
    having established that both sides came from the same row kind.

    `math.dist` rather than a hand-rolled sum of squares: the naive form
    OVERFLOWS on an extreme coordinate, because the intermediate square exceeds
    the float range long before the distance would. A corrupt `coordinate_stack`
    column is enough to reach that, and this function runs on the poll thread,
    where an exception is a dead navigation loop rather than a visible error.
    `math.dist` is built on `hypot`, which is scaled to avoid it.
    """
    return math.dist(a, b)


def solar_root_agree(root: StackRow, solar: StackRow) -> bool:
    """Whether the two anchors carry the same numbers, within tolerance.

    A CONFABULATION TRIPWIRE, never independent verification. Both lines print
    identical digits and sit 13 px apart in one background regime, so a
    background-driven misread can hit both the same way; this has been observed
    (`ledger/REFUTED.md`, "Root agreeing with SolarSystem"). Agreement therefore
    proves nothing on its own, while DISAGREEMENT proves corruption, which is
    the direction worth acting on.

    Relative with an absolute floor, because system-frame values run to 1e10 m
    where the printed 0.1 m resolution is already near what the digits express.
    """
    worst = max(abs(r - s) for r, s in zip(root.xyz, solar.xyz))
    allowed = max(
        SOLAR_ROOT_CROSSCHECK_MIN_M,
        max(abs(v) for v in solar.xyz) * SOLAR_ROOT_CROSSCHECK_REL,
    )
    return worst <= allowed


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #

def _row_to_obj(row: StackRow, with_depth: bool) -> dict:
    obj: dict = {"x_m": row.x_m, "y_m": row.y_m, "z_m": row.z_m}
    if with_depth:
        obj["depth"] = row.depth
    obj["raw_name"] = row.raw_name
    obj["raw"] = row.raw
    return obj


def to_dict(stack: CoordinateStack) -> dict:
    """The serialisable form, shaped as the implementation plan specifies."""
    return {
        "schema": SCHEMA_VERSION,
        "root": _row_to_obj(stack.root, with_depth=False),
        "solar": _row_to_obj(stack.solar, with_depth=False),
        "nested": [_row_to_obj(row, with_depth=True) for row in stack.nested],
        "candidate_depth": stack.candidate_depth,
    }


def to_json(stack: CoordinateStack) -> str:
    """Compact, deterministic JSON for the database column.

    `sort_keys` and no whitespace, so the same stack always serialises to the
    same bytes and two rows can be compared as text.
    """
    return json.dumps(to_dict(stack), separators=(",", ":"), sort_keys=True)


def _row_from_obj(obj, depth_required: bool) -> StackRow | None:
    if not isinstance(obj, dict):
        return None
    coords = []
    for key in ("x_m", "y_m", "z_m"):
        value = obj.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(float(value)):
            return None
        coords.append(float(value))
    depth = obj.get("depth")
    if depth_required:
        if isinstance(depth, bool) or not isinstance(depth, int):
            return None
    elif depth is not None:
        return None
    raw_name = obj.get("raw_name", "")
    raw = obj.get("raw", "")
    if not isinstance(raw_name, str) or not isinstance(raw, str):
        return None
    return StackRow(
        x_m=coords[0],
        y_m=coords[1],
        z_m=coords[2],
        raw_name=raw_name,
        raw=raw,
        depth=depth if depth_required else None,
    )


def from_dict(obj) -> CoordinateStack | None:
    """Rebuild a stack, or None for anything this version cannot vouch for.

    FAILS CLOSED on every anomaly, including an unknown future `schema` value: a
    newer writer may mean something different by the same field names, and
    guessing would route a waypoint from a contract we have not read. The
    database row survives a None here; only navigation refuses.
    """
    if not isinstance(obj, dict):
        return None
    if obj.get("schema") != SCHEMA_VERSION:
        return None
    root = _row_from_obj(obj.get("root"), depth_required=False)
    solar = _row_from_obj(obj.get("solar"), depth_required=False)
    if root is None or solar is None:
        return None
    raw_nested = obj.get("nested", [])
    if not isinstance(raw_nested, list):
        return None
    nested: list[StackRow] = []
    for item in raw_nested:
        row = _row_from_obj(item, depth_required=True)
        if row is None:
            return None
        nested.append(row)
    depths = [row.depth for row in nested]
    if len(set(depths)) != len(depths):
        return None
    candidate_depth = obj.get("candidate_depth")
    if candidate_depth is not None:
        if isinstance(candidate_depth, bool) or not isinstance(candidate_depth, int):
            return None
        if candidate_depth not in depths:
            # A candidate pointing at a row that is not here would resolve to
            # None at route time and silently look like "no candidate", which
            # hides a corrupt record instead of refusing it.
            return None
    return CoordinateStack(
        root=root,
        solar=solar,
        nested=tuple(nested),
        candidate_depth=candidate_depth,
    )


def from_json(text) -> CoordinateStack | None:
    """`from_dict` over a JSON string. None on anything unparseable."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return from_dict(json.loads(text))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# The routing state machine
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RouteDecision:
    """What one comparison of two stacks decided, and why.

    Attributes:
        mode: MODE_ROOT_APPROACH, MODE_LOCAL_TRACKING or MODE_REFUSED.
        reason: A stable REASON_* code, for logs and tests.
        distance_m: Distance in the selected frame, or None when refused.
        current: The selected current triple, or None.
        target: The selected target triple, or None.
        allow_direction: Whether a bearing or ring bubble may be drawn from
            this decision. False outside LOCAL_TRACKING: a Root-frame direction
            would need the system-frame CamDir convention, which is measured
            108.7 degrees wrong and is gated off at
            `navigation.SPACE_DIRECTION_CALIBRATED`.
        allow_arrival: Whether arrival may be announced. Root distance is
            deliberately coarse for a rotating surface target, so arrival is
            local-only.
        clear_local: Whether any previously drawn local vector, bubble and HUD
            marker must be cleared in the same state update. True on every mode
            except LOCAL_TRACKING, so a fallback can never leave a stale
            direction on screen looking live.
    """

    mode: str
    reason: str
    distance_m: float | None = None
    current: tuple[float, float, float] | None = None
    target: tuple[float, float, float] | None = None
    allow_direction: bool = False
    allow_arrival: bool = False
    clear_local: bool = True


def _root_approach(reason: str, current: StackRow, target: StackRow,
                   distance: float) -> RouteDecision:
    return RouteDecision(
        mode=MODE_ROOT_APPROACH,
        reason=reason,
        distance_m=distance,
        current=current.xyz,
        target=target.xyz,
        allow_direction=False,
        allow_arrival=False,
        clear_local=True,
    )


def select_route(
    target: CoordinateStack | None,
    current: CoordinateStack | None,
    allow_local: bool | None = None,
) -> RouteDecision:
    """Choose the frame to navigate in, from two stacks and nothing else.

    Root is computed FIRST and unconditionally, so no local reading can
    influence a decision taken outside the envelope. Equality belongs to Root at
    both ceilings: the comparisons are strictly-less-than, so 3 Mm exactly is
    Root, in both the Root and the local test.

    There is no hysteresis band. An intermittent local candidate therefore
    toggles between modes at the boundary, which is accepted: the alternative is
    a band above 3 Mm, and the ceiling is a hard maximum rather than a
    preference.

    Args:
        target: The saved waypoint's stack, or None when it has none (every
            legacy schema-0 row).
        current: The stack from the newest accepted capture, or None.
        allow_local: Overrides `LOCAL_TRACKING_ENABLED`, for tests and for the
            eventual rollout. None means take the module default.

    Returns:
        A `RouteDecision`. Never raises, so a caller cannot turn a bad capture
        into an exception on the poll thread.
    """
    if allow_local is None:
        allow_local = LOCAL_TRACKING_ENABLED

    if target is None:
        return RouteDecision(mode=MODE_REFUSED, reason=REASON_NO_TARGET_STACK)
    if current is None:
        return RouteDecision(mode=MODE_REFUSED, reason=REASON_NO_CURRENT_STACK)

    d_root = distance_m(current.root.xyz, target.root.xyz)

    if d_root >= LOCAL_TRACKING_MAX_M:
        return _root_approach(
            REASON_ROOT_AT_OR_BEYOND_LIMIT, current.root, target.root, d_root
        )

    target_local = target.local_candidate
    current_local = current.local_candidate
    if target_local is None or current_local is None:
        return _root_approach(
            REASON_NO_LOCAL_CANDIDATE, current.root, target.root, d_root
        )

    d_local = distance_m(current_local.xyz, target_local.xyz)
    if d_local >= LOCAL_TRACKING_MAX_M:
        return _root_approach(
            REASON_LOCAL_AT_OR_BEYOND_LIMIT, current.root, target.root, d_root
        )

    if not allow_local:
        # Both distances are inside the envelope and the local frame is still
        # unproven, so Root carries the route. Reported with its own reason so
        # a log makes plain that the gate, not the geometry, chose Root.
        return _root_approach(
            REASON_LOCAL_DISABLED, current.root, target.root, d_root
        )

    return RouteDecision(
        mode=MODE_LOCAL_TRACKING,
        reason=REASON_LOCAL_IN_RANGE,
        distance_m=d_local,
        current=current_local.xyz,
        target=target_local.xyz,
        allow_direction=True,
        allow_arrival=True,
        clear_local=False,
    )
