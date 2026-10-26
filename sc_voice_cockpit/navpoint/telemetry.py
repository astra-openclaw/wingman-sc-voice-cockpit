"""The observation model: what one capture attempt IS, and how state carries
forward when the next attempt fails.

Wave 2 of `Supercalifragilistic.md` asks for the internal, immutable
observation type the rest of the reader will eventually be built around,
before any producer, epoch, or lane exists to fill it in. This module answers
section 7's "observation contract" and section 12's "temporal policy" as pure
data and pure functions, matching `coordinate_stack.py`: no Wingman runtime,
no OCR, no database, no logging, no threading, no clock reads. Every function
here takes `now` from its caller.

The hazard this file exists to prevent is confusion between "a good answer",
"the last good answer", and "no answer" (`Supercalifragilistic.md` sections 5
and 7). Section 12.4 states it exactly: a refused frame must NEVER refresh the
timestamp on the retained fix, and only an authoritative observation may be
saved, replace the nav fix, update distance, drive routing, or contribute to
arrival. `transition()` is the one place a new `Observation` is derived from
an old one plus a new attempt, and it is the one place both rules are
enforced: `Observation.__post_init__` refuses to construct an instance where
a temporal pass coexists with a failed hard gate. That guard runs on every
path through the public constructor, including `dataclasses.replace`; it
does not defend against `object.__setattr__` reaching around `__post_init__`
on an already-built instance, so read it as a strong constructor-time
guarantee, not an unconditional one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping

from sc_voice_cockpit.navpoint.coordinate_stack import CoordinateStack


# --------------------------------------------------------------------------- #
# Source lane (section 5: "Structural scan" / "Fast scan").
# --------------------------------------------------------------------------- #

LANE_STRUCTURAL = "structural"
LANE_FAST = "fast"

_LANES = frozenset({LANE_STRUCTURAL, LANE_FAST})

# --------------------------------------------------------------------------- #
# Freshness (section 5's "Authoritative observation" / "Stale observation",
# plus the "nothing accepted yet" case a fresh producer starts in).
# --------------------------------------------------------------------------- #

FRESHNESS_FRESH_AUTHORITATIVE = "fresh_authoritative"
FRESHNESS_STALE_RETAINED = "stale_retained"
FRESHNESS_NONE_YET = "none_yet"

# --------------------------------------------------------------------------- #
# Outcome codes (section 7). Ten states the plan requires to stay impossible
# to confuse with one another. Each is its own constant on purpose:
# collapsing any two into a shared string is exactly the ambiguity section 7
# forbids, and `test_every_outcome_constant_is_distinct` proves the set has
# ten members.
# --------------------------------------------------------------------------- #

OUTCOME_NO_CAPTURE_ATTEMPTED = "no_capture_attempted"
OUTCOME_CAPTURE_IN_PROGRESS = "capture_in_progress"
OUTCOME_FRESH_AUTHORITATIVE_FIX = "fresh_authoritative_fix"
OUTCOME_STALE_RETAINED_FIX = "stale_retained_fix"
OUTCOME_DISPLAY_ONLY_INTERPOLATION = "display_only_interpolation"
OUTCOME_OVERLAY_ABSENT = "overlay_absent"
OUTCOME_UNUSABLE_IMAGE = "unusable_image"
OUTCOME_DECODE_REFUSAL = "decode_refusal"
OUTCOME_PARSER_OR_SCANNER_REJECTION = "parser_or_scanner_rejection"
OUTCOME_INCOMPATIBLE_FRAME_TRANSITION = "structurally_incompatible_frame_transition"

# The single-frame hard-gate failures an Observation's `hard_gate_result` may
# carry (section 12.1 steps 1-6). Reusing the OUTCOME_* strings rather than a
# parallel set: a hard-gate failure IS one of the ten outcomes, so a second
# vocabulary for the same four states would be the collapsing section 7
# forbids, just moved one field over.
_HARD_GATE_FAILURES = frozenset({
    OUTCOME_OVERLAY_ABSENT,
    OUTCOME_UNUSABLE_IMAGE,
    OUTCOME_DECODE_REFUSAL,
    OUTCOME_PARSER_OR_SCANNER_REJECTION,
})

HARD_GATE_PASSED = "hard_gate_passed"

# `temporal_result` values (section 12.1 step 7). TEMPORAL_NOT_EVALUATED is
# the state between steps 6 and 7: the hard gate already failed, so step 7
# never ran. A temporal rejection reuses OUTCOME_INCOMPATIBLE_FRAME_TRANSITION
# rather than inventing an eleventh string: whether the veto fires because the
# alignment epoch changed or because continuity/velocity says this attempt is
# not a comparable frame, the result the rest of the reader cares about is
# identical -- invalidate velocity history and require reacquisition.
TEMPORAL_NOT_EVALUATED = "temporal_not_evaluated"
TEMPORAL_PASSED = "temporal_passed"

# Every outcome a REFUSED (non-accepted) attempt may end in. Used by
# `current_status` to surface the specific refusal immediately after it
# happens, before the retained observation's own freshness has had a chance
# to explain anything.
_REFUSAL_OUTCOMES = frozenset(_HARD_GATE_FAILURES | {OUTCOME_INCOMPATIBLE_FRAME_TRANSITION})


@dataclass(frozen=True)
class AlignmentEpoch:
    """The current screen resolution, block geometry, vertical origin, and row
    mapping (section 5). A resolution change, structural re-scan, or cache
    invalidation starts a new epoch.

    Frozen and built only of hashable fields, so two epochs compare equal
    (and share a hash) exactly when every one of those four things agrees.
    That equality IS the identity check sections 4.2 and 12.3 require before
    any coordinate from two observations may be compared as if they shared a
    frame: a plain `==` between two `AlignmentEpoch` instances already
    compares every field, so there is no partial-field shortcut for a caller
    to reach for by mistake.

    Attributes:
        screen_resolution: (width, height) in pixels.
        block_geometry: (left, top, width, height) of the captured
            `r_displayinfo 2` block, in screen pixels.
        vertical_origin: The fitted row-zero pixel offset for this alignment.
        row_mapping: Semantic role name to physical row index, e.g.
            `(("camdir", 0), ("root", 7))`. A tuple of pairs rather than a
            dict so the epoch stays hashable; row identity is semantic per
            section 3 ("Rows are identified semantically. Physical line
            numbers are not identities"), so `row_mapping` is what makes a
            physical index meaningful, not a substitute for it.
    """

    screen_resolution: tuple[int, int]
    block_geometry: tuple[int, int, int, int]
    vertical_origin: float
    row_mapping: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Observation:
    """One capture attempt's complete, immutable record.

    Plays two roles depending on `freshness`. While `freshness` is None, this
    is just the raw record of one attempt -- accepted or refused -- and is not
    yet the producer's retained state. Once `transition()` has processed it,
    `freshness` is `FRESHNESS_FRESH_AUTHORITATIVE` or `FRESHNESS_STALE_RETAINED`
    and the instance IS the retained state a consumer may read.

    `__post_init__` enforces the section-5 and 12.1 safety rules at
    construction time, rather than leaving them as conventions a caller could
    forget. These guarantees hold for every instance built through the public
    constructor or `dataclasses.replace`; none of them defend against
    `object.__setattr__` on an already-built instance.

    - `freshness` can only be non-None when both `hard_gate_result ==
      HARD_GATE_PASSED` and `temporal_result == TEMPORAL_PASSED`. A refused or
      temporally-rejected attempt therefore cannot be constructed with a
      non-None freshness, so `is_authoritative` and `freshness_state` cannot
      be fooled by a caller who forgot to check an outcome first.
    - `temporal_result` can only be `TEMPORAL_PASSED` or
      `OUTCOME_INCOMPATIBLE_FRAME_TRANSITION` when `hard_gate_result ==
      HARD_GATE_PASSED`; otherwise it must be `TEMPORAL_NOT_EVALUATED`. A
      temporal pass can therefore never coexist with a failed hard gate,
      which is section 12.1's "may reject, may never rescue" enforced at
      construction time rather than left as an undocumented convention.
    - `stack` may be non-None only when both gates passed. A refusal or
      rejection that somehow carried a stack would be exactly the ambiguity
      between "a good answer" and "no answer" section 7 exists to prevent.

    Attributes:
        capture_seq: Monotonically increasing capture sequence number.
        wall_clock_ts: Wall-clock timestamp (seconds), for presentation only.
        monotonic_ts: Monotonic timestamp (seconds), for age and velocity.
            Caller-supplied; this module never reads a clock.
        epoch: The AlignmentEpoch this attempt was decoded under.
        lane: LANE_STRUCTURAL or LANE_FAST.
        stack: The accepted CoordinateStack, or the accepted Root/Solar-only
            subset a fast-lane capture produces (a stack with no `nested`
            rows and no `candidate_depth` is already how `coordinate_stack`
            represents that). None when the attempt did not accept a stack.
        cam_dir: CamDir in degrees when present and trusted, else None.
        hard_gate_result: HARD_GATE_PASSED, or the specific OUTCOME_* failure
            (section 12.1 steps 1-6).
        temporal_result: TEMPORAL_NOT_EVALUATED, TEMPORAL_PASSED, or
            OUTCOME_INCOMPATIBLE_FRAME_TRANSITION (section 12.1 step 7).
        freshness: None while this is just an attempt record.
            FRESHNESS_FRESH_AUTHORITATIVE or FRESHNESS_STALE_RETAINED once
            `transition()` has made it the retained state.
        reason_code: A stable refusal or reacquisition reason code, for logs
            and tests. Free-form beyond stability, so a specific gate can
            report a more precise code than `hard_gate_result` or
            `temporal_result` alone carry.
        diagnostics: Diagnostic measurements already produced by the reader.
            Never exposed to the model (section 4.3). Coerced to a read-only
            `MappingProxyType` over a fresh copy in `__post_init__`, so
            neither the caller's original mapping nor `observation.diagnostics`
            itself can mutate a "frozen" observation's diagnostics after the
            fact.
    """

    capture_seq: int
    wall_clock_ts: float
    monotonic_ts: float
    epoch: AlignmentEpoch
    lane: str
    stack: CoordinateStack | None
    cam_dir: tuple[float, float, float] | None
    hard_gate_result: str
    temporal_result: str
    reason_code: str
    freshness: str | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A caller-supplied dict left mutable would let anyone holding a
        # reference to it -- either the caller's own copy or
        # `observation.diagnostics` itself -- change a "frozen" observation's
        # diagnostics after construction. Copying before wrapping also means
        # the caller mutating THEIR dict afterward cannot reach back in.
        object.__setattr__(
            self, "diagnostics", MappingProxyType(dict(self.diagnostics))
        )
        if self.lane not in _LANES:
            raise ValueError(f"not a valid lane: {self.lane!r}")
        if self.hard_gate_result != HARD_GATE_PASSED and (
            self.hard_gate_result not in _HARD_GATE_FAILURES
        ):
            raise ValueError(
                f"not a valid hard_gate_result: {self.hard_gate_result!r}"
            )

        if self.hard_gate_result != HARD_GATE_PASSED:
            # Section 12.1: steps 1-6 failed, so step 7 never ran. Encoding
            # this as a required value (never TEMPORAL_PASSED, never
            # OUTCOME_INCOMPATIBLE_FRAME_TRANSITION) is what stops a temporal
            # rescue of a hard-gate failure from being constructed through
            # this constructor.
            if self.temporal_result != TEMPORAL_NOT_EVALUATED:
                raise ValueError(
                    "temporal_result must be TEMPORAL_NOT_EVALUATED when "
                    "hard_gate_result did not pass"
                )
            if self.stack is not None:
                raise ValueError("a failed hard gate must not carry a stack")
            if self.freshness is not None:
                raise ValueError("a failed hard gate can never be authoritative")
            return

        if self.temporal_result not in (TEMPORAL_PASSED, OUTCOME_INCOMPATIBLE_FRAME_TRANSITION):
            raise ValueError(
                f"not a valid temporal_result after a passed hard gate: "
                f"{self.temporal_result!r}"
            )
        if self.temporal_result == OUTCOME_INCOMPATIBLE_FRAME_TRANSITION:
            if self.stack is not None:
                raise ValueError("a temporal rejection must not carry a stack")
            if self.freshness is not None:
                raise ValueError("a temporal rejection can never be authoritative")
            return

        # temporal_result == TEMPORAL_PASSED: this attempt is itself an
        # accepted fix, the only case allowed to carry a stack or a
        # non-None freshness.
        if self.stack is None:
            raise ValueError("an accepted fix must carry a stack")
        if self.freshness not in (None, FRESHNESS_FRESH_AUTHORITATIVE, FRESHNESS_STALE_RETAINED):
            raise ValueError(f"not a valid freshness: {self.freshness!r}")


def outcome_of(observation: Observation) -> str:
    """The single OUTCOME_* classification of one `Observation`.

    Computed rather than stored, so it can never drift out of sync with
    `hard_gate_result`, `temporal_result`, and `freshness`.
    """
    if observation.hard_gate_result != HARD_GATE_PASSED:
        return observation.hard_gate_result
    if observation.temporal_result != TEMPORAL_PASSED:
        return OUTCOME_INCOMPATIBLE_FRAME_TRANSITION
    if observation.freshness == FRESHNESS_STALE_RETAINED:
        return OUTCOME_STALE_RETAINED_FIX
    return OUTCOME_FRESH_AUTHORITATIVE_FIX


def age_s(observation: Observation, now: float) -> float:
    """Seconds since `observation.monotonic_ts`, using the caller's `now`.

    Does not clamp a negative result: a caller feeding a `now` earlier than
    the capture has a bug worth seeing, not a bug worth hiding.
    """
    return now - observation.monotonic_ts


def freshness_state(
    observation: Observation | None, now: float, stale_bound_s: float
) -> str:
    """What freshness `observation` should be treated as right now.

    Distinct from `observation.freshness`, which is a snapshot set the last
    time `transition()` ran. This recomputes from elapsed time alone, so a
    producer that has stalled outright (no new attempts at all, accepted or
    refused) is still caught: age past `stale_bound_s` demotes a
    FRESH_AUTHORITATIVE snapshot to stale even though nothing has actively
    failed since.

    Args:
        observation: The retained observation, or None if nothing has ever
            been accepted.
        now: Monotonic time of this evaluation.
        stale_bound_s: The measured, cadence-derived staleness bound. Section
            8 declines to hard-code a revalidation interval; the caller
            supplies whatever value the ledger has measured.
    """
    if observation is None or observation.freshness is None:
        return FRESHNESS_NONE_YET
    if observation.freshness == FRESHNESS_STALE_RETAINED:
        return FRESHNESS_STALE_RETAINED
    if age_s(observation, now) > stale_bound_s:
        return FRESHNESS_STALE_RETAINED
    return FRESHNESS_FRESH_AUTHORITATIVE


def is_authoritative(
    observation: Observation | None, now: float, stale_bound_s: float
) -> bool:
    """Whether `observation` may currently be saved, replace the navigation
    fix, update an authoritative distance, drive routing, or contribute to
    arrival (section 5).

    The one predicate any consumer should call before treating a value as
    real. Delegates to `freshness_state` rather than reading
    `observation.freshness` directly, so it agrees with `freshness_state` BY
    CONSTRUCTION: a producer that has stalled outright (no new attempts at
    all since `observation` was captured) revokes authority here the same way
    `freshness_state` reports it as stale, rather than reporting `True`
    forever on a snapshot nobody ever re-evaluated. A `STALE_RETAINED`
    observation fails this even though it was accepted once: Wave 6's exit
    gate requires stale data to never reach authoritative consumers.

    Args:
        observation: The retained observation, or None if nothing has ever
            been accepted.
        now: Monotonic time of this evaluation.
        stale_bound_s: See `freshness_state`.
    """
    return freshness_state(observation, now, stale_bound_s) == FRESHNESS_FRESH_AUTHORITATIVE


def transition(
    previous: Observation | None, attempt: Observation, now: float
) -> Observation | None:
    """The one place a new retained state is derived from an old one.

    Encodes section 12.4 as code rather than convention: a REFUSED attempt
    (including a temporal rejection) never refreshes the retained
    observation's timestamp and can never itself become authoritative. It can
    only ever result in keeping the previous observation, now tagged stale,
    or -- if there was no previous observation -- in producing nothing at
    all. It can never manufacture a position.

    Args:
        previous: The last `Observation` this producer retained (its
            `freshness` is not None), or None if nothing has ever been
            accepted.
        attempt: The newest capture/decode attempt. Must be an UNRETAINED
            record: `attempt.freshness` must be None. Feeding a previously
            retained `Observation` back in here as if it were a new attempt
            is a caller error, not a legitimate re-evaluation -- it would
            otherwise re-tag a stale, possibly very old observation fresh
            again using its own original timestamps, which is exactly the
            "refresh a retained timestamp without a real new capture"
            failure section 12.4 forbids. That case raises instead of
            silently succeeding.
        now: Present only for signature symmetry with the other functions in
            this module and for callers who want to log the transition
            moment; this function does not compare `now` against anything,
            since a refusal must not touch a timestamp regardless of when it
            happened. Age-based staleness is `freshness_state`'s job, not
            this one's.

    Returns:
        A new `Observation` (with `freshness` set to
        `FRESHNESS_FRESH_AUTHORITATIVE`) if `attempt` passed both the hard
        gate and the temporal check. Otherwise `previous` with `freshness`
        forced to `FRESHNESS_STALE_RETAINED` and every other field,
        including both timestamps, unchanged. None if `attempt` was refused
        and there was no previous observation to retain.

    Raises:
        ValueError: If `attempt.freshness` is not None.
    """
    del now  # See docstring: age-based staleness lives in `freshness_state`.
    if attempt.freshness is not None:
        raise ValueError(
            "attempt must be an unretained record (freshness=None); got "
            f"{attempt.freshness!r}. Do not feed a previously retained "
            "Observation back into transition() as a new attempt."
        )
    if (
        attempt.hard_gate_result == HARD_GATE_PASSED
        and attempt.temporal_result == TEMPORAL_PASSED
    ):
        return replace(attempt, freshness=FRESHNESS_FRESH_AUTHORITATIVE)
    if previous is None:
        return None
    return replace(previous, freshness=FRESHNESS_STALE_RETAINED)


@dataclass(frozen=True)
class DisplayEstimate:
    """An interpolated value for visual smoothing ONLY (sections 5 and 12.4).

    Deliberately NOT an `Observation` and sharing none of its fields beyond
    the coordinates themselves: an `isinstance` check is enough to keep this
    out of marking, routing, distance, or arrival code by construction. There
    is no `freshness` or gate-result field here to check or forget to check,
    because the type itself carries none of the fields authority depends on.

    Attributes:
        position_m: The interpolated (x, y, z) position, in the SAME frame as
            the two observations it was interpolated between. Never compare
            this against a value from a different alignment epoch or stack
            identity (sections 4.2 and 12.3).
        wall_clock_ts: Wall-clock timestamp, for presentation only.
    """

    position_m: tuple[float, float, float]
    wall_clock_ts: float

    @property
    def outcome(self) -> str:
        """Always OUTCOME_DISPLAY_ONLY_INTERPOLATION.

        A read-only property, not a field: there is no constructor argument
        that could set a different outcome here.
        """
        return OUTCOME_DISPLAY_ONLY_INTERPOLATION


def current_status(
    latest: Observation | None,
    capture_in_progress: bool,
    last_refusal_outcome: str | None,
    now: float,
    stale_bound_s: float,
) -> str:
    """The single OUTCOME_* constant a consumer (HUD, ring) should show now.

    The one function that answers section 7's "must make it impossible to
    confuse" requirement across producer lifecycle, freshness, and refusal
    all at once, so no consumer independently reimplements this triage and
    risks conflating two of the ten states. When a retained observation
    exists, this reclassifies its freshness by elapsed time and then asks
    `outcome_of` for the final answer, rather than re-deciding
    fresh-vs-stale here in a second code path that could drift from
    `outcome_of`'s.

    Args:
        latest: The producer's retained observation, or None.
        capture_in_progress: Whether a capture/decode is currently running.
        last_refusal_outcome: The most recent refused attempt's
            `outcome_of()` value, or None if the last attempt was accepted or
            none has ever completed. Surfaces the specific refusal only while
            there is still no retained observation at all; once one exists,
            its own freshness explains the status instead.
        now: Monotonic time of this evaluation.
        stale_bound_s: See `freshness_state`.
    """
    if capture_in_progress:
        return OUTCOME_CAPTURE_IN_PROGRESS
    if latest is None:
        if last_refusal_outcome in _REFUSAL_OUTCOMES:
            return last_refusal_outcome
        return OUTCOME_NO_CAPTURE_ATTEMPTED
    reclassified = replace(latest, freshness=freshness_state(latest, now, stale_bound_s))
    return outcome_of(reclassified)
