"""Deterministic local reader for the r_displayinfo overlay.

Replaces the Vision AI call as the position source. Every confabulation
incident in this skill's history is a language model reading digits, so this
path emits characters it can prove from pixels and refuses everything else.

THE MODEL, all of it measured (LocalOcrPlan.md, Phase 1):

    observed = G * colour + H * background

  G  glyph coverage, measured on a black backdrop where `H * background`
     vanishes. Every instance of a character is bit-identical.
  H  what the background still transmits through the glyph and its one-pixel
     diagonal shadow: `(1 - G) * (1 - shift_down_right(G))`. A 2x2 maximum
     expansion incorrectly darkens the horizontal and vertical neighbours.
     See OCR_BACKGROUND_VALIDATION.md for real-pixel replay evidence.
  background is UNKNOWN and solved per cell per channel by least squares, which
  is what makes the reader background-agnostic.

Geometry, all measured: character pitch exactly 7.5 px, row pitch exactly 13,
glyph body 8 px tall, block right-aligned with its last ink column at screen
width minus 7. A line's left edge snaps to a whole pixel and characters then
advance by 7.5 px, so every glyph sits at sub-pixel phase 0.0 or 0.5.

The ordinary pass keeps conservative clipping guards. If it fails on brightness,
the runtime can retry coordinates with read_bright_coordinates; exact spacing,
small-mark ambiguity checks and scanner validation remain mandatory. Neural OCR
is not used as a coordinate authority. See OCR_BACKGROUND_VALIDATION.md for
measured coverage and remaining limits.

Rows are identified by their LABEL and then classified by their CONTENT, never
by row index: which lines appear changes with context (in a base, on foot, in a
ship, seated), though the spacing never does.

Credit: Mallachi
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Geometry. Measured on 5120x1440 frames and expressed relative to the RIGHT
# edge, because the block is right-aligned and its left edge moves with the
# screen width.
# ---------------------------------------------------------------------------
BLOCK_TOP = 6
# 88 cells at 7.5 px. WIDENED FROM 600/80 on 2026-07-31, because 600/7.5 is
# exactly 80 and an 81-character line therefore lost its LEADING character in
# silence: a real deep-space capture decoded as `one: SolarSystem_...`, which
# fails the `^Zone:` anchor and refuses a frame that was otherwise perfect. The
# longest line the skill can need is about 86 characters (`Zone: SolarSystem_`
# plus a 12-digit id plus three 16-character km tokens).
# Measured over 43 frames across 9 background regimes: 84 to 92 cells all gain
# 4 trusted rows among the kinds the skill reads, and lose 1 telemetry row that
# it does not. 96 gives that gain back and 104 loses 8, because every extra cell
# lands on scenery to the left of the text and can refuse. 88 sits in the middle
# of that plateau rather than at its edge.
BLOCK_WIDTH = 660
RIGHT_MARGIN = 7

PITCH = 7.5
CELL_WIDTH = 8
ROW_PITCH = 13
ROW_HEIGHT = 13
PHASES = 2
MASK = 1
CELLS_PER_ROW = 88
MAX_ROWS = 12

# Rows the position path actually reads. Everything it needs is CamDir plus the
# Zone stack down to Root, and rows 8 and beyond belong to a SECOND PITCH CLASS
# (6.595 px) the reader never fits, so they refuse in every capture and cost
# only time. Measured live on 2026-07-31 with the game running: 12 rows 1795 ms,
# 10 rows 1303 ms, 8 rows 1033 ms. Not lower than 8: the line set shifts with
# context and an elevator capture the same night ran the stack to row 5 (ship,
# transit carriage, hangar, SolarSystem, Root), so 8 is margin, not slack.
POSITION_ROWS = 8

# Reads between forced refits of the vertical origin. The overlay does not move
# while the resolution holds, and the fit is about 300 ms of a 870 ms read, but
# a cached fit that is never revalidated is a stale fact waiting to be believed.
ALIGNMENT_REVALIDATE = 20

COLOURS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "orange": (255, 164, 0),
    "yellow": (239, 230, 139),
}

# ---------------------------------------------------------------------------
# Confidence floor, CALIBRATED not chosen, and BOUNDED ABOVE.
#
# Over 359 known-correct cells the worst residual was 12.69 (p99 12.47) and the
# smallest margin 13.69. An exhaustive known-bad reconstruction (28234 wrong
# candidates over 680 cells) puts the nearest wrong candidate at 17.94.
#
# Keep the conservative floor after the shadow correction. The historical
# wrong-candidate bounds above were measured with the old transmission model;
# they are NOT a proof for the corrected model or arbitrary scene textures.
# Raising this floor needs fresh adverse-input calibration, not just more
# accepted frames. See OCR_BACKGROUND_VALIDATION.md.
#
# REFUSE_MARGIN changed zero decisions across those 28234 known-bad and 680
# known-good samples: unexercised defence in depth, kept deliberately.
# ---------------------------------------------------------------------------
REFUSE_COST = 15.0
REFUSE_MARGIN = 8.0
REFUSED = "?"

# ---------------------------------------------------------------------------
# The four guards, each calibrated against measured data (LocalOcrPlan item 1).
# ---------------------------------------------------------------------------

# 1. Conservative ordinary-pass precondition. Historically the two
#    gamma breaches read spread 9.0 and 7.1, indistinguishable from blank.
#    Across 20 real frames plus gamma variants the worst SAFE row reaches 5.91
#    per cent and the best UNSAFE row never drops below 29.41, so 15 sits in a
#    23-point empty gap. Rejects 9 of 153 real screenshots.
#    The coordinate-only brightness retry can recover some such captures when
#    their shadow/glyph pixels survive; this statistic alone cannot prove loss.
CLIP_LEVEL = 250
ROW_CLIP_REJECT = 0.15

# 2. Ink contradiction: a cell that decodes blank while containing ink. The
#    threshold is HYBRID, not absolute, and the hybrid form is essential. A flat
#    50 misfires on 14.6 per cent of decoder-accepted blanks over a structured
#    background (measured inside the Nyx glacier ring), which would refuse most
#    rows in most of space; the relative term lifts the threshold above ordinary
#    background variation. K in 4 to 8 behaves identically: 6/6 real breaches
#    caught, 0 false positives on both smooth and structured populations.
#    HONEST LIMIT: on a structured row the threshold rises above the spread a
#    DIMMED glyph would show, so this guard loses the dimming class exactly
#    where backgrounds are busy. It is not the primary defence there; cost is.
INK_ABSOLUTE = 50.0
INK_RELATIVE_K = 6.0

# 3. Flat-white, the third layer, closing the one full-clip case the other two
#    miss. Real captured background never achieves a bit-flat interior: across
#    159 blank cells on a blown sky the spread never fell below 9, because
#    compression, quantisation and gradients always leave variation. Only
#    genuine clipping produces perfect flatness, so a spread floor of 5 selects
#    for "flat because DESTROYED" rather than "flat because bright". Fired 0
#    times in 1980 real blank cells, including 1440 structured ones.
FLAT_SPREAD_MAX = 5.0
FLAT_MEAN_MIN = 250.0
FLAT_ABOVE_BACKGROUND = 40.0

# 4. Per-cell clipped fraction, the companion the row statistic cannot replace.
#    Clipping ONE cell to 100 per cent (simulated bloom behind a character)
#    leaves the row at 3.09 per cent and the frame at 2.17, both comfortably
#    safe, while that cell decodes as an accepted wrong blank. Separation is
#    clean: 0 per cent at baseline, 94 to 100 per cent where the failure starts.
CELL_CLIP_REFUSE = 0.50

# Small marks can be hidden by a structured backdrop even when the nearest
# constant-background digit wins with a large margin. Veto a coordinate glyph
# if a minus/decimal can also explain its pixels with <=2 RGB levels RMS error
# under ANY achievable background. This is an ambiguity veto, never a repair.
# Zero vetoes on the 824 successful saved frames in the September replay.
SMALL_MARK_RMSE = 2.0

_RECOVERY_TOKEN = r"[+-]?\d+(?:\.\d+)?(?:km|m)"
_RECOVERY_TAIL = re.compile(rf" {_RECOVERY_TOKEN} {_RECOVERY_TOKEN} {_RECOVERY_TOKEN}$")
_RECOVERY_HEADER = re.compile(r"Zone: (Root|SolarSystem_(\d+)) Pos:")

# Lines recognised by their label. The label is only the first stage, and it is
# never enough on its own: `overlay_fields` then matches each consumer field
# against a FULL-line shape, because `trusted` does not mean "contains a
# payload" (a row rendered as `Zone: Root Pos:` with the coordinates absent
# comes back trusted at cost 0.00, and an opaque occluder over part of the block
# decodes as confident spaces, truncating a coordinate while leaving the row
# trusted). Two to four rows per frame also share `kind == "zone"`, so the ship
# zone, `OOC_`, `SolarSystem_` and `Root` are told apart by content, not label.
#
# The label is SEARCHED FOR, never anchored at position 0, for the same reason
# `overlay_fields._largest_component` searches: the block is right-aligned, so
# every row carries a blank left margin, and a background star inside that
# margin decodes as a phantom glyph. `? CamDir: 179   0  133 FOV: 55 ...` failed
# a `^CamDir:` anchor, came back with `kind` None, and a CamDir row with no kind
# cannot be cleared by `ooc_hypothesis_refuted`, so the whole capture refused.
# Observed 2026-08-01 at P5 L2 on 3 of 15 consecutive frames as the star drifted
# through the block. Searching is the more permissive form, so it was measured
# rather than argued: over all 552 frames of the capture corpus, 6609 decoded
# rows, it moved 410 rows from NO kind to their own and 0 rows from one kind to
# a different kind. 9 of the 19 sessions were losing rows this way, so P5 L2 is
# where it was caught, not where it was confined.
#
# ORDER IS SAFETY-BEARING, and only became so when this started searching. Under
# anchoring the order was inert: no label is a prefix of another and a row has
# one position 0, so at most one entry could ever match. Searching means a row
# carrying two labels resolves by LIST PRIORITY, so whichever entry sits first
# wins.
#
# `zone` therefore goes FIRST, because it is the only kind that does not clear
# `overlay_fields.ooc_hypothesis_refuted`: every other kind is in
# `_NOT_ZONE_KINDS` and short-circuits the gate to cleared. Putting the sole
# gate-BLOCKING label anywhere but first is the fail-open direction, and
# `camdir` held that slot purely by inheritance from the anchored version.
# A constructed row proves the risk was real rather than theoretical:
# `Zone: OOC_Pyro_1_?? Pos: 1.0km 2.0km 3.0km CamDir: 179   0  133` classifies
# as `camdir` under the old order and clears a gate that exists to stop a
# system-frame waypoint being stored on a rotating body. Verified by negative
# control: restoring the old order fails 5 tests in `test_label_order_priority`,
# including the one asserting the row still BLOCKS the space branch.
#
# This costs nothing to fix: 0 of the 6609 decoded rows in the corpus carry two
# labels, so no observed behaviour changes. It only removes a shape the reader
# had no defence against.
LABELS: list[tuple[str, str]] = [
    ("zone", r"Zone:"),
    ("camdir", r"CamDir:"),
    ("session", r"Session:"),
    ("shard", r"ShardId:"),
    ("server", r"Server:"),
]


def row_kind(text: str) -> str | None:
    """Which overlay line this decoded row is, or None when no label is in it.

    Args:
        text: A decoded row, already stripped of its padding.

    Returns:
        The label kind, or None.
    """
    return next((k for k, pattern in LABELS if re.search(pattern, text)), None)


def shift(array: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Shift a 2-D array, filling with zeros."""
    out = np.zeros_like(array)
    height, width = array.shape
    ys = slice(max(dy, 0), height + min(dy, 0))
    yd = slice(max(-dy, 0), height + min(-dy, 0))
    xs = slice(max(dx, 0), width + min(dx, 0))
    xd = slice(max(-dx, 0), width + min(-dx, 0))
    out[ys, xs] = array[yd, xd]
    return out


def transmission(glyph: np.ndarray) -> np.ndarray:
    """H, the background's remaining visibility after the drop shadow."""
    coverage = np.clip(glyph, 0.0, 1.0)
    return (1.0 - coverage) * (1.0 - shift(coverage, 1, 1))


def match(patch: np.ndarray, glyph: np.ndarray, trans: np.ndarray,
          colour: np.ndarray) -> float:
    """Residual after solving this cell's background under a candidate glyph.

    Args:
        patch: (h, w, 3) observed pixels, outer columns already masked off.
        glyph: (h, w) coverage in 0..1.
        trans: (h, w) background transmission in 0..1.
        colour: (3,) text colour.

    Returns:
        Root mean squared residual in 0..255 units; lower is better.
    """
    denominator = float((trans * trans).sum())
    if denominator <= 1e-9:
        return float("inf")
    total = 0.0
    for channel in range(3):
        observed = patch[:, :, channel]
        ink = colour[channel] * glyph
        background = float(((observed - ink) * trans).sum() / denominator)
        background = min(max(background, 0.0), 255.0)
        residual = observed - (ink + background * trans)
        total += float((residual * residual).sum())
    return float(np.sqrt(total / (patch[:, :, 0].size * 3)))


def block_columns(width: int) -> tuple[int, int]:
    """The block's (left, right) pixel columns for a frame of this width."""
    right = width - RIGHT_MARGIN + 1
    return max(right - BLOCK_WIDTH, 0), right


# Thresholds for "is the overlay on screen at all". COSMETIC ONLY: this decides
# which message the ring shows and can never reach the payload path, so the bar
# is "separates on real data and stays simple".
#
# Validated 2026-07-31 against 60 overlay-present blocks and 20 frames captured
# live with `r_displayinfo 0`, which is a REAL negative class rather than the
# cropped-scenery proxy the first version used: 57/60 present, 20/20 absent, and
# zero false positives at every threshold from 0.05 to 0.3. The three misses are
# one fully blown block (caught earlier by the capture precondition, so it never
# reaches this) and two login crops too short to hold row structure.
#
# Neither measure works alone: a blown sky is over 50 per cent near-white with no
# text in it, and a smooth bright gradient fools a naive periodicity term.
INK_PRESENT_MIN = 0.005
ROW_PERIODICITY_MIN = 0.2


def overlay_ink_fraction(frame: np.ndarray) -> float:
    """Fraction of the block that is bright in ALL channels, as glyphs are."""
    left, right = block_columns(frame.shape[1])
    block = frame[:BLOCK_TOP + ROW_PITCH * MAX_ROWS + ROW_HEIGHT, left:right]
    if block.size == 0:
        return 0.0
    return float((block.min(axis=2) > 200).mean())


# Lags that are NOT multiples of the row pitch. Text correlates strongly at 13
# and weakly between; anything smooth correlates strongly at every lag.
_OFF_PEAK_LAGS = (6, 7, 8, 18, 19, 20)


def overlay_row_periodicity(frame: np.ndarray) -> float:
    """How strongly the block's bright pixels stack into rows at ROW_PITCH.

    Measured as the autocorrelation of the bright-pixel row profile AT the pitch
    minus its mean at off-pitch lags. The contrast is the whole point: a single
    lag is not a periodicity measure at all, because any SMOOTH profile
    correlates strongly with itself at every lag. A bright diagonal streak across
    the block produces a smooth ramp down the rows and scored 0.763 under the
    single-lag form, indistinguishable from real text, which is how it was found
    (a live overlay-off capture on 2026-07-31 that read as GLARE). Real text
    peaks at the pitch and dips between; a ramp is flat across lags and cancels.

    Normalised by its own zero-lag, so this is a SHAPE measure, indifferent to
    how bright the scene is.
    """
    left, right = block_columns(frame.shape[1])
    block = frame[:BLOCK_TOP + ROW_PITCH * MAX_ROWS + ROW_HEIGHT, left:right]
    if block.shape[0] <= max(_OFF_PEAK_LAGS) or block.size == 0:
        return 0.0
    profile = (block.min(axis=2) > 200).sum(axis=1).astype(np.float64)
    profile -= profile.mean()
    zero = float((profile * profile).sum())
    if zero <= 0.0:
        return 0.0

    def lag(offset: int) -> float:
        return float((profile[:-offset] * profile[offset:]).sum()) / zero

    off_peak = sum(lag(n) for n in _OFF_PEAK_LAGS) / len(_OFF_PEAK_LAGS)
    return lag(ROW_PITCH) - off_peak


def overlay_is_present(frame: np.ndarray) -> bool:
    """Whether the r_displayinfo overlay appears to be on screen at all.

    Distinguishes "the player has not enabled the overlay" from "the overlay is
    there and the scene is too bright to read it", which need different actions
    from the player. Wrong either way costs only the wrong message.
    """
    return (overlay_ink_fraction(frame) > INK_PRESENT_MIN
            and overlay_row_periodicity(frame) > ROW_PERIODICITY_MIN)


def block_region(monitor: dict) -> dict:
    """The mss grab region covering just the overlay block.

    All the reader's geometry is measured from the RIGHT edge, so a frame
    containing only the block decodes identically to the full screen. Grabbing
    667x166 instead of 5120x1440 costs 4.3 ms rather than 104 ms, measured, and
    changes no pixel the reader looks at. `test_block_grab_equivalence` pins the
    identical-decode claim so this stays an optimisation and never a shortcut.

    Args:
        monitor: An mss monitor dict, with `left`, `top`, `width`, `height`.

    Returns:
        An mss region dict for `sct.grab`.
    """
    width = BLOCK_WIDTH + RIGHT_MARGIN
    return {
        "left": monitor["left"] + monitor["width"] - width,
        "top": monitor["top"],
        "width": width,
        "height": BLOCK_TOP + ROW_PITCH * MAX_ROWS + ROW_HEIGHT,
    }


def capture_is_usable(frame: np.ndarray, rows: int = MAX_ROWS) -> tuple[bool, str]:
    """Flag a frame too saturated for the ordinary reader's policy.

    Computed on the FIXED, hardcoded block geometry and never on the reader's
    own fitted origin. Under gamma 0.5 the alignment fit itself jumps from y0=6
    to y0=2, because extreme saturation confuses the alignment search too, so a
    guard that consumes fitted output inherits the failure it exists to catch.

    Returns:
        (usable, reason). Reason is empty when usable.
    """
    left, right = block_columns(frame.shape[1])
    for index in range(rows):
        top = BLOCK_TOP + index * ROW_PITCH
        band = frame[top:top + ROW_HEIGHT, left:right]
        if band.shape[0] < ROW_HEIGHT or band.size == 0:
            break
        clipped = float(np.mean(np.any(band >= CLIP_LEVEL, axis=2)))
        if clipped >= ROW_CLIP_REJECT:
            return False, f"row {index} is {clipped * 100:.1f} per cent clipped"
    return True, ""


class OverlayReader:
    """Decodes the r_displayinfo overlay from a full-screen RGB frame."""

    def __init__(self, glyph_file: str | Path) -> None:
        """Load the glyph bank.

        Args:
            glyph_file: Path to the npz template bank, keyed `<char>|<phase>`.
        """
        self._glyph_file = Path(glyph_file)
        self._brightness_reader: _CoordinateBrightnessReader | None = None
        self._header_reader: _HeaderBackgroundReader | None = None
        data = np.load(self._glyph_file)
        self.glyph = {
            (key.split("|")[0], int(key.split("|")[1])): data[key]
            for key in data.files
        }
        # A blank cell is a real outcome, not the least-bad glyph. Modelling it
        # as zero coverage and full transmission lets the SAME least-squares fit
        # explain it as pure background, on any backdrop.
        for phase in range(PHASES):
            self.glyph.setdefault(
                (" ", phase), np.zeros((ROW_HEIGHT, CELL_WIDTH), dtype=np.float64)
            )
        self.trans = {key: transmission(g) for key, g in self.glyph.items()}
        self._stack_templates()
        self._fit_cache: tuple[tuple[int, ...], int] | None = None
        self._fit_uses = 0
        # INSTRUMENTATION, written by `read` and read by nothing in this module.
        # The three of them are what distinguishes a reader working from a stale
        # cached origin from one refitting on every frame, and `read` cannot
        # return them without breaking its three-value contract with callers.
        self.last_from_cache: bool | None = None
        self.last_refit: bool | None = None
        self.last_fit_uses: int | None = None
        # Written by `decode_row` for its own call, harvested by `_decode_block`
        # for the variant that won. Same reason as the three above: the return
        # signature is a contract with about forty call sites.
        self.last_refused_cells: list[dict] = []

    def _stack_templates(self) -> None:
        """Group the templates by phase into arrays, masked once.

        Every candidate for a cell is scored in ONE vectorised pass rather than
        one numpy call per template. The per-template Python loop cost 17.3 s to
        read ten rows, which is slower than the Vision call it replaces; stacked
        it is about 40x faster for the same arithmetic. Decisions are unchanged:
        the two forms agree on every character and to about 1e-13 on cost.
        """
        self._chars: dict[int, list[str]] = {}
        self._glyphs: dict[int, np.ndarray] = {}
        self._transs: dict[int, np.ndarray] = {}
        self._hh: dict[int, np.ndarray] = {}
        self._gh: dict[int, np.ndarray] = {}
        inner = slice(MASK, CELL_WIDTH - MASK)
        for phase in range(PHASES):
            keys = sorted(k for k in self.glyph if k[1] == phase)
            self._chars[phase] = [char for char, _ in keys]
            glyphs = np.stack([self.glyph[k][:, inner] for k in keys])
            transs = np.stack([self.trans[k][:, inner] for k in keys])
            self._glyphs[phase] = glyphs
            self._transs[phase] = transs
            self._hh[phase] = (transs * transs).sum(axis=(1, 2))
            self._gh[phase] = (glyphs * transs).sum(axis=(1, 2))

    def cell_costs(self, core: np.ndarray, phase: int,
                   colour: np.ndarray) -> np.ndarray:
        """Residual of every candidate glyph for one cell, same model as `match`.

        Args:
            core: (h, w, 3) observed pixels, outer columns already masked off.
            phase: The cell's sub-pixel phase, 0 or 1.
            colour: (3,) text colour.

        Returns:
            (T,) residuals aligned with `self._chars[phase]`.
        """
        glyphs = self._glyphs[phase]
        transs = self._transs[phase]
        denom = self._hh[phase]
        pixels = core.reshape(-1, 3)
        # Least-squares background per template per channel, then clipped to the
        # physically achievable range exactly as the scalar form does.
        projected = transs.reshape(transs.shape[0], -1) @ pixels
        background = (projected - self._gh[phase][:, None] * colour[None, :])
        with np.errstate(divide="ignore", invalid="ignore"):
            background = background / denom[:, None]
        np.clip(background, 0.0, 255.0, out=background)
        model = (glyphs[..., None] * colour[None, None, None, :]
                 + transs[..., None] * background[:, None, None, :])
        residual = core[None] - model
        costs = np.sqrt((residual * residual).sum(axis=(1, 2, 3)) / pixels.size)
        # A template that transmits no background anywhere cannot solve for one.
        costs[denom <= 1e-9] = np.inf
        return costs

    def _batch_cell_costs(self, cores: np.ndarray, phases: np.ndarray,
                          colour: np.ndarray) -> list[np.ndarray]:
        """Score a row with the same clipped least-squares model as cell_costs.

        Project all cells of a phase together, avoiding thousands of tiny array
        allocations per capture. Keep the scalar hook for custom glyph models.
        """
        if getattr(self.cell_costs, "__func__", None) is not OverlayReader.cell_costs:
            return [self.cell_costs(core, int(phase), colour)
                    for core, phase in zip(cores, phases)]
        # PNG arrays are commonly uint8; squared sums must not overflow before
        # they meet the floating-point glyph templates.
        cores = np.asarray(cores, dtype=np.float64)
        result = [None] * len(cores)
        pixel_count = cores.shape[1] * cores.shape[2]
        for phase in range(PHASES):
            indexes = np.flatnonzero(phases == phase)
            if not len(indexes):
                continue
            pixels = cores[indexes].reshape(-1, pixel_count, 3)
            flat = pixels.transpose(0, 2, 1).reshape(-1, pixel_count)
            g = self._glyphs[phase].reshape(-1, pixel_count)
            h = self._transs[phase].reshape(-1, pixel_count)
            pg = (flat @ g.T).reshape(len(indexes), 3, -1).transpose(0, 2, 1)
            ph = (flat @ h.T).reshape(len(indexes), 3, -1).transpose(0, 2, 1)
            hh, gh = self._hh[phase], self._gh[phase]
            with np.errstate(divide="ignore", invalid="ignore"):
                bg = np.clip((ph - gh[None, :, None] * colour)
                             / hh[None, :, None], 0, 255)
            # Expanded squared residual, including the clipped background.
            sse = (np.sum(pixels * pixels, axis=(1, 2))[:, None]
                   + np.sum(g * g, axis=1)[None, :] * np.sum(colour * colour)
                   + hh[None, :] * np.sum(bg * bg, axis=2)
                   + 2 * gh[None, :] * np.sum(bg * colour, axis=2)
                   - 2 * np.sum(pg * colour, axis=2) - 2 * np.sum(ph * bg, axis=2))
            costs = np.sqrt(np.maximum(sse, 0) / (pixel_count * 3))
            costs[:, hh <= 1e-9] = np.inf
            for index, cost in zip(indexes, costs):
                result[index] = cost
        return result

    # -- geometry ---------------------------------------------------------- #

    def cell_boxes(self, right: float, offset: float, count: int):
        """Yield (x0, phase) for cells counted leftward from the right edge."""
        for cell in range(count):
            left = right + offset - (cell + 1) * PITCH
            x0 = int(np.floor(left))
            if x0 < 0:
                return
            yield x0, int(round((left - x0) * PHASES)) % PHASES

    # -- per-cell measurements, template-free ------------------------------ #

    @staticmethod
    def _cell_stats(core: np.ndarray) -> tuple[float, float, float]:
        """Spread, mean and clipped fraction of one cell's interior.

        Spread is measured WITHIN each channel and maxed over the three. Across
        the channels is wrong and silently certifies nothing: a flat coloured
        wall (the red interior reads R 96, G 2) scores 94 on its own, so every
        cell in the block reads as inked.
        """
        per_channel = core.max(axis=(0, 1)) - core.min(axis=(0, 1))
        spread = float(np.max(per_channel))
        mean = float(np.mean(core))
        clipped = float(np.mean(np.any(core >= CLIP_LEVEL, axis=2)))
        return spread, mean, clipped

    @staticmethod
    def _background_level(band: np.ndarray, left: int, right: int) -> float:
        """The row's own background level: mean of its per-channel medians."""
        strip = band[:, left:right]
        if strip.size == 0:
            return 0.0
        return float(np.mean(np.median(strip.reshape(-1, 3), axis=0)))

    # -- decoding ---------------------------------------------------------- #

    def decode_row(self, band: np.ndarray, right: float, offset: float,
                   colour: np.ndarray, count: int, background: float | None = None):
        """Decode one text row.

        Args:
            band: (ROW_HEIGHT, width, 3) slice of the frame.
            right: Right edge of the block in pixels.
            offset: Sub-pixel offset, 0.0 or 0.5.
            colour: (3,) candidate text colour.
            count: How many cells to read, counting leftward.
            background: The row's background level, or None to skip the
                guards. The alignment search passes None, because it only needs
                a comparable cost and runs before any row is trusted.

        Returns:
            (text, mean residual, per-cell margins). Why each cell refused is
            left on `self.last_refused_cells` rather than returned, so every
            existing caller keeps the three-value contract it was written
            against.
        """
        chars: list[str] = []
        costs: list[float] = []
        margins: list[float] = []
        # Why each refused cell refused, for the trace. Recorded only on a real
        # decode: `fit_alignment` calls this 72 times a frame with no background
        # and throws every one of those cells away with the candidate alignment
        # that produced it, so recording them would be pure cost.
        records: list[dict] = []
        self.last_refused_cells = records
        cores, phases = [], []
        for x0, phase in self.cell_boxes(right, offset, count):
            patch = band[:, x0:x0 + CELL_WIDTH]
            if patch.shape[1] < CELL_WIDTH or patch.shape[0] < ROW_HEIGHT:
                break
            cores.append(patch[:, MASK:CELL_WIDTH - MASK])
            phases.append(phase)
        if not cores:
            return "", float("inf"), []
        data = np.stack(cores)
        ranked_cells = self._batch_cell_costs(data, np.asarray(phases), colour)
        for index, (ranked, phase) in enumerate(zip(ranked_cells, phases)):
            order = np.argsort(ranked, kind="stable")
            cost = float(ranked[order[0]])
            margin = float(ranked[order[1]] - cost) if order.size > 1 else 0.0
            # Expanded sums can round near a boundary or tie. Resolve those
            # with the original residual calculation before making a decision.
            if (abs(cost - REFUSE_COST) < 1e-4
                    or abs(margin - REFUSE_MARGIN) < 1e-4 or margin < 1e-4):
                ranked = self.cell_costs(data[index], phase, colour)
                order = np.argsort(ranked, kind="stable")
                cost = float(ranked[order[0]])
                margin = float(ranked[order[1]] - cost) if order.size > 1 else 0.0
            # Refuse rather than guess. Every confabulation incident in this
            # skill's history is a reader emitting a digit it did not have.
            if cost > REFUSE_COST or margin < REFUSE_MARGIN:
                chars.append(REFUSED)
                if background is not None:
                    records.append(
                        self._refusal_record(index, phase, order, ranked,
                                             cost, margin)
                    )
            else:
                chars.append(self._chars[phase][order[0]])
            costs.append(cost)
            margins.append(margin)
        if background is not None:
            if self._cell_stats is OverlayReader._cell_stats:
                spread = np.max(data.max(axis=(1, 2)) - data.min(axis=(1, 2)), axis=1)
                means = np.mean(data, axis=(1, 2, 3))
                clipped = np.mean(np.any(data >= CLIP_LEVEL, axis=3), axis=(1, 2))
                stats = list(zip(spread, means, clipped))
            else:
                stats = [self._cell_stats(core) for core in data]
            self._apply_cell_guards(chars, stats, background)
        return "".join(reversed(chars)), float(np.mean(costs)), margins

    def _refusal_record(self, index: int, phase: int, order: np.ndarray,
                        ranked: np.ndarray, cost: float, margin: float) -> dict:
        """Why one cell refused, and what it nearly was.

        THE DISJUNCTION IS THE POINT. A cell refuses when its cost exceeds
        `REFUSE_COST` OR its margin falls below `REFUSE_MARGIN`, and the two
        mean opposite things. High cost means nothing in the glyph bank matched,
        which points at a render change, a missing template, or a scale the bank
        was not built for. Low margin means two glyphs matched almost equally,
        which points at genuine ambiguity between similar characters at this
        size. The fix for one is not the fix for the other, and until this
        record existed the log said which cells refused and never which side of
        the disjunction fired.

        Read-only with respect to the decode: it is called from inside the
        refusal branch with that branch's own values, so it cannot disagree with
        the decision it explains and cannot change it either.

        Args:
            index: The cell's position, counting leftward from the block's right
                edge, as `cell_boxes` yields them. The decoded text is emitted
                reversed, so cell 0 is the LAST character of the row.
            phase: The cell's sub-pixel phase, which selects the glyph bank.
            order: The candidate indices, sorted by ascending cost.
            ranked: The per-candidate costs, in bank order.
            cost: The winning candidate's cost.
            margin: The runner-up's lead over it.

        Returns:
            The record, ready for the trace.
        """
        chars = self._chars[phase]
        over_cost = cost > REFUSE_COST
        under_margin = margin < REFUSE_MARGIN
        second = int(order[1]) if order.size > 1 else None
        return {
            "i": index,
            "cost": cost,
            "margin": margin,
            "trip": ("both" if over_cost and under_margin
                     else "cost" if over_cost else "margin"),
            "top1": chars[int(order[0])],
            "top1_cost": cost,
            "top2": chars[second] if second is not None else "",
            "top2_cost": float(ranked[second]) if second is not None else None,
        }

    @staticmethod
    def _apply_cell_guards(chars: list[str], stats: list[tuple[float, float, float]],
                           background: float) -> None:
        """Turn cells the pixels contradict into refusals, in place.

        Three of the four guards live here. They exist because the confidence
        floor answers "which glyph explains these pixels best", which a blank
        cell can win outright: an occluder, a clipped highlight or a dimmed
        glyph all produce cells that decode as confident spaces.
        """
        blank_spreads = [
            spread for (spread, _, _), char in zip(stats, chars) if char == " "
        ]
        threshold = INK_ABSOLUTE
        if blank_spreads:
            threshold = max(
                INK_ABSOLUTE, INK_RELATIVE_K * float(np.median(blank_spreads))
            )
        for index, (spread, mean, clipped) in enumerate(stats):
            if clipped > CELL_CLIP_REFUSE:
                chars[index] = REFUSED
                continue
            if chars[index] != " ":
                continue
            if spread > threshold:
                chars[index] = REFUSED
            elif (spread < FLAT_SPREAD_MAX and mean >= FLAT_MEAN_MIN
                    and mean >= background + FLAT_ABOVE_BACKGROUND):
                chars[index] = REFUSED

    def fit_alignment(self, frame: np.ndarray) -> tuple[int, float, str]:
        """Find the block's vertical origin, sub-pixel offset and text colour.

        Fitted, never assumed: the vertical origin is not identical between
        captures, and a wrong sub-pixel offset silently halves accuracy. The
        capture precondition deliberately does NOT use this, since extreme
        saturation moves the fit itself.
        """
        _, right = block_columns(frame.shape[1])
        best = (float("inf"), BLOCK_TOP, 0.0, "white")
        for y0 in range(2, 14):
            band = frame[y0:y0 + ROW_HEIGHT]
            if band.shape[0] < ROW_HEIGHT:
                continue
            for offset in (0.0, 0.5):
                for name, rgb in COLOURS.items():
                    colour = np.asarray(rgb, dtype=np.float64)
                    _, cost, _ = self.decode_row(band, right, offset, colour, 40)
                    if cost < best[0]:
                        best = (cost, y0, offset, name)
        return best[1], best[2], best[3]

    def invalidate_alignment(self) -> None:
        """Drop the cached vertical origin, forcing a refit on the next read."""
        self._fit_cache = None
        self._fit_uses = 0

    def _aligned_origin(self, frame: np.ndarray) -> tuple[int, bool]:
        """The block's vertical origin, refitted only when the cache is stale.

        `fit_alignment` searches 12 origins by 2 offsets by 3 colours, which is
        about 300 ms of the roughly 870 ms a full read costs, and it returns the
        SAME origin on frame after frame because the overlay does not move. It
        does move on a resolution change, so the frame shape is part of the key,
        and it is refitted every `ALIGNMENT_REVALIDATE` reads regardless so a
        cached fit can never be arbitrarily old.

        Returns:
            (origin, from_cache). `from_cache` lets the caller refit once if the
            cached origin turns out to decode nothing.
        """
        key = frame.shape
        if (self._fit_cache is not None and self._fit_cache[0] == key
                and self._fit_uses < ALIGNMENT_REVALIDATE):
            self._fit_uses += 1
            return self._fit_cache[1], True
        y0, _, _ = self.fit_alignment(frame)
        self._fit_cache = (key, y0)
        self._fit_uses = 0
        return y0, False

    def read(self, frame: np.ndarray, rows: int = MAX_ROWS):
        """Decode the whole block. Returns (y origin, offset, per-row dicts)."""
        y0, from_cache = self._aligned_origin(frame)
        out = self._decode_block(frame, y0, rows)
        refit = False
        # SELF-HEALING, and the reason the cache is safe. If the overlay moved
        # without the resolution changing, a stale origin decodes the gaps
        # between rows and nothing is trusted. That is indistinguishable from a
        # genuinely unreadable scene by cost alone, so rather than guess, refit
        # once and keep whichever attempt actually read something.
        if from_cache and not any(row["trusted"] for row in out):
            self.invalidate_alignment()
            previous_origin = y0
            y0, _ = self._aligned_origin(frame)
            # The same immutable frame at the same origin has the same native
            # result. Keep the refit, but avoid repeating its expensive decode.
            # Custom block decoders retain their original invocation contract.
            if (y0 != previous_origin or getattr(self._decode_block, "__func__", None)
                    is not OverlayReader._decode_block):
                out = self._decode_block(frame, y0, rows)
            refit = True
        self.last_from_cache = from_cache
        self.last_refit = refit
        self.last_fit_uses = self._fit_uses
        return y0, 0.0, out

    def read_bright_coordinates(self, frame: np.ndarray, rows: int = POSITION_ROWS):
        """Retry coordinates using glyph evidence on a bright background.

        Call only after the ordinary capture fails, and ALWAYS pass the rows
        through overlay_fields.capture_payload and the scanner. Exact spacing
        and the small-mark veto are required: clipping can erase whole cells.
        Keep a separate alignment cache so a failed retry cannot move the
        ordinary reader's fit. Heading spacing is padded, so these rows must
        never authorize a heading.
        """
        if self._brightness_reader is None:
            self._brightness_reader = _CoordinateBrightnessReader(self._glyph_file)
        y0, offset, decoded = self._brightness_reader.read(frame, rows)
        self.last_from_cache = self._brightness_reader.last_from_cache
        self.last_refit = self._brightness_reader.last_refit
        self.last_fit_uses = self._brightness_reader.last_fit_uses
        for row in decoded:
            if row["kind"] != "zone":
                row["trusted"] = False
        return y0, offset, decoded

    @staticmethod
    def has_header_recovery_candidate(rows: list[dict]) -> bool:
        """A refused header with an already readable coordinate tail exists."""
        return any(
            "?" in row.get("text", "")
            and _RECOVERY_TAIL.search(row.get("text", "")) is not None
            and _RECOVERY_HEADER.search(row.get("text", "")) is None
            for row in rows
        )

    def recover_coordinate_headers(self, frame: np.ndarray, rows: list[dict]) -> list[dict]:
        """Recover refused anchor letters; never replace native numeric cells.

        A curved background is fitted only for the literal anchor header. Every
        confident native character, including spaces and system-id digits,
        must agree. Coordinate text is copied byte-for-byte from the native
        pass, then checked again for small-mark ambiguity: its Pos label may
        have been unreadable when that veto first ran.

        A final separator-only pass can confirm a noisy space between already
        readable header words. It cannot supply any letter or numeric character.
        """
        recovered = []
        for source in rows:
            row = dict(source)
            recovered.append(row)
            if not self.has_header_recovery_candidate([row]):
                continue
            geometry = self._header_geometry(frame, row)
            if geometry is None:
                continue
            band, _ = geometry
            if self._header_reader is None:
                self._header_reader = _HeaderBackgroundReader(self._glyph_file)
            candidates = self._header_reader._decode_block(band, 0, 1)
            if not candidates:
                continue
            candidate = candidates[0]
            match = _RECOVERY_HEADER.search(candidate["text"])
            if (match is None or candidate["offset"] != row["offset"]
                    or candidate["colour"] != row["colour"]):
                continue
            tail_length = len(candidate["text"]) - match.end()
            if tail_length <= 0:
                continue
            native_tail = row["text"][-tail_length:]
            if _RECOVERY_TAIL.fullmatch(native_tail) is None:
                continue
            header = match.group(0)
            end = len(row["text"]) - tail_length
            begin = end - len(header)
            if begin < 0:
                continue
            native_header = row["text"][begin:end]
            if any(a != b and a != REFUSED for a, b in zip(native_header, header)):
                continue
            if match.group(2):
                id_start = match.start(2) - match.start()
                if native_header[id_start:id_start + len(match.group(2))] != match.group(2):
                    continue
            text, ambiguous = self._refuse_ambiguous_coordinates(
                header + native_tail, band, block_columns(frame.shape[1])[1],
                row["offset"], np.asarray(COLOURS[row["colour"]], dtype=float)
            )
            # Old prefix refusals were resolved; numeric diagnostics survive.
            records = [r for r in row.get("refused_cells", []) if r["i"] < tail_length]
            refused = text.count(REFUSED)
            row.update(text=text, kind="zone", trusted=refused == 0,
                       refused=refused, refused_cells=records + ambiguous,
                       header_recovered=True)
        return self._recover_header_separators(frame, recovered)

    def _header_geometry(self, frame: np.ndarray, row: dict):
        """Require complete native pixels; never broadcast a truncated band."""
        if frame.ndim != 3 or frame.shape[2] != 3:
            return None
        top = row.get("y")
        if (not isinstance(top, (int, np.integer)) or top < 0
                or top + ROW_HEIGHT > frame.shape[0]
                or row.get("offset") not in (0.0, 0.5)):
            return None
        boxes = list(self.cell_boxes(block_columns(frame.shape[1])[1],
                                     row["offset"], CELLS_PER_ROW))
        if any(x < 0 or x + CELL_WIDTH > frame.shape[1] for x, _ in boxes):
            return None
        return frame[top:top + ROW_HEIGHT], boxes

    def _recover_header_separators(self, frame: np.ndarray, rows: list[dict]) -> list[dict]:
        """Confirm header spaces only when all nonblank glyphs contradict RGB.

        Sharp scenery can make the ordinary blank-cell fit expensive. Here all
        header letters, punctuation and ID digits must already match; the only
        permitted change is '?' to a literal header space. Coordinate text and
        its separators remain byte-for-byte native. This intentionally cannot
        use physical uniqueness to repair a mixed or missing anchor letter.
        """
        recovered = []
        for source in rows:
            row = dict(source)
            recovered.append(row)
            text = row.get("text", "")
            tail_match = _RECOVERY_TAIL.search(text)
            if REFUSED not in text or tail_match is None or row.get("colour") != "white":
                continue
            tail = tail_match.group(0)
            end = len(text) - len(tail)
            identifier = re.search(r"_(\d+)[ ?]Pos:$", text[:end])
            label = f"SolarSystem_{identifier.group(1)}" if identifier else "Root"
            header = f"Zone: {label} Pos:"
            begin = end - len(header)
            if begin < 0:
                continue
            native_header = text[begin:end]
            if (REFUSED not in native_header
                    or any(a != b and not (a == REFUSED and b == " ")
                           for a, b in zip(native_header, header))):
                continue
            geometry = self._header_geometry(frame, row)
            if geometry is None:
                continue
            band, boxes = geometry
            for absolute, char in enumerate(native_header, begin):
                if char != REFUSED:
                    continue
                index = len(text) - 1 - absolute
                record = next((r for r in row.get("refused_cells", []) if r["i"] == index), None)
                if record is None or record.get("top1") != " " or not 0 <= index < len(boxes):
                    break
                x, phase = boxes[index]
                core = band[:, x + MASK:x + CELL_WIDTH - MASK]
                if core.shape != (ROW_HEIGHT, CELL_WIDTH - 2 * MASK, 3):
                    break
                lower = self._glyphs[phase][..., None] * 255.0
                upper = lower + self._transs[phase][..., None] * 255.0
                violation = np.maximum(lower - core, 0) + np.maximum(core - upper, 0)
                residual = np.sqrt(np.mean(violation * violation, axis=(1, 2, 3)))
                if any(self._chars[phase][i] != " "
                       for i in np.flatnonzero(residual <= SMALL_MARK_RMSE)):
                    break
            else:
                text, ambiguous = self._refuse_ambiguous_coordinates(
                    header + tail, band, block_columns(frame.shape[1])[1],
                    row["offset"], np.asarray(COLOURS["white"], dtype=float)
                )
                records = [r for r in row.get("refused_cells", []) if r["i"] < len(tail)]
                row.update(text=text, kind="zone", trusted=REFUSED not in text,
                           refused=text.count(REFUSED), refused_cells=records + ambiguous,
                           header_separator_recovered=True)
        return recovered

    def _refuse_ambiguous_coordinates(self, text: str, band: np.ndarray,
                                      right: int, offset: float,
                                      colour: np.ndarray) -> tuple[str, list[dict]]:
        """Veto glyphs also compatible with a hidden minus or decimal point.

        With G/H fixed, every physically achievable pixel lies in
        [G*colour, G*colour + H*255], even over a structured background.
        Test that interval without fitting or inventing a background. A match
        means uncertainty, so preserve the existing winner's cost and geometry
        while refusing its character. Root/Solar agreement cannot detect the
        same visual corruption in both rows.
        """
        start = text.find("Pos: ")
        if start < 0:
            return text, []
        boxes = list(self.cell_boxes(right, offset, CELLS_PER_ROW))
        chars = list(text)
        records = []
        for absolute in range(start + 5, len(text)):
            char = text[absolute]
            if char not in "0123456789.km":
                continue
            index = len(text) - 1 - absolute
            x, phase = boxes[index]
            core = band[:, x + MASK:x + CELL_WIDTH - MASK]
            for mark in "-.":
                if char == mark:
                    continue
                template = self._chars[phase].index(mark)
                lower = self._glyphs[phase][template][..., None] * colour
                upper = lower + self._transs[phase][template][..., None] * 255
                violation = np.maximum(lower - core, 0) + np.maximum(core - upper, 0)
                residual = float(np.sqrt(np.mean(violation * violation)))
                if residual > SMALL_MARK_RMSE:
                    continue
                chars[absolute] = REFUSED
                ranked = self.cell_costs(core, phase, colour)
                order = np.argsort(ranked, kind="stable")
                cost = float(ranked[order[0]])
                margin = float(ranked[order[1]] - cost)
                record = self._refusal_record(index, phase, order, ranked, cost, margin)
                record.update(trip="small_mark", alternative=mark, physical_rmse=residual)
                records.append(record)
                break
        return "".join(chars), records

    def _decode_block(self, frame: np.ndarray, y0: int, rows: int):
        """Decode `rows` rows starting at vertical origin `y0`."""
        left, right = block_columns(frame.shape[1])
        out = []
        for index in range(rows):
            top = y0 + index * ROW_PITCH
            band = frame[top:top + ROW_HEIGHT]
            if band.shape[0] < ROW_HEIGHT:
                break
            background = self._background_level(band, left, right)
            # Offset is fitted PER ROW, not once for the block. A line's
            # sub-pixel phase follows the parity of its own character count, so
            # rows of different lengths sit on different offsets; applying one
            # row's offset to the whole block decodes every mismatched row to
            # garbage while leaving the rest looking fine.
            best = None
            for row_offset in (0.0, 0.5):
                for name, rgb in COLOURS.items():
                    colour = np.asarray(rgb, dtype=np.float64)
                    text, cost, margins = self.decode_row(
                        band, right, row_offset, colour, CELLS_PER_ROW, background
                    )
                    # The records belong to the variant that WON, not to the
                    # last one tried, so they are captured with their candidate
                    # rather than read off the reader after the search ends.
                    if best is None or cost < best[1]:
                        # Guarded because this read is DIAGNOSTIC. A reader that
                        # never assigned it would otherwise raise here and turn a
                        # plain refusal into a decode error, which is the one
                        # thing a diagnostic may never do.
                        best = (text, cost, margins, name, row_offset,
                                getattr(self, "last_refused_cells", []))
            text, cost, margins, name, row_offset, refused_cells = best
            if row_kind(text) == "zone":
                text, ambiguous = self._refuse_ambiguous_coordinates(
                    text, band, right, row_offset, np.asarray(COLOURS[name], dtype=np.float64)
                )
                refused_cells = refused_cells + ambiguous
            stripped = text.strip(" _.-")
            kind = row_kind(stripped)
            # A row is trusted only if EVERY cell in it was read. One refused
            # digit inside a coordinate makes the whole coordinate unusable, so
            # this fails closed at row level rather than emitting a partial
            # number that looks complete.
            refused = text.count(REFUSED)
            # `refused_cells` records confidence and small-mark refusals, so
            # it is shorter than `refused` whenever `_apply_cell_guards` refused
            # a cell the floor had accepted. That gap is itself informative: a
            # row with refused cells the floor never saw failed on ink or
            # clipping, not on the glyph bank.
            out.append({
                "row": index, "y": top, "colour": name, "cost": cost,
                "offset": row_offset, "refused": refused,
                "trusted": refused == 0,
                "text": stripped, "kind": kind,
                "margin": float(np.median(margins)) if margins else 0.0,
                "refused_cells": refused_cells,
            })
        return out


class _CoordinateBrightnessReader(OverlayReader):
    """Coordinate-only fallback; spacing/ambiguity checks replace clip vetoes.

    Saturation of scenery is not itself loss of glyph information: black
    shadows can remain readable against white. Keep the measured cost/margin
    floor and ink contradiction guard. Fully erased marks become extra spaces
    and must be refused by the exact coordinate/anchor layout in overlay_fields.
    """

    @staticmethod
    def _apply_cell_guards(chars: list[str], stats: list[tuple[float, float, float]],
                           background: float) -> None:
        blank_spreads = [s[0] for s, char in zip(stats, chars) if char == " "]
        threshold = INK_ABSOLUTE
        if blank_spreads:
            threshold = max(threshold, INK_RELATIVE_K * float(np.median(blank_spreads)))
        for index, (spread, _, _) in enumerate(stats):
            if chars[index] == " " and spread > threshold:
                chars[index] = REFUSED


class _HeaderBackgroundReader(_CoordinateBrightnessReader):
    """Curved-background proposals for refused header letters, never coordinates."""

    def _stack_templates(self) -> None:
        super()._stack_templates()
        y, x = np.mgrid[-1:1:13j, -1:1:6j]
        self._design = np.stack([t.ravel() for t in (np.ones_like(x), x, y, x*y, x*x, y*y)], axis=1)
        self._inverse = {}
        self._inverse_g = {}
        for phase in range(PHASES):
            weighted = self._transs[phase].reshape(-1, 78, 1) * self._design[None]
            inverse = np.linalg.pinv(weighted, rcond=1e-8)
            self._inverse[phase] = inverse
            self._inverse_g[phase] = np.einsum("tdp,tp->td", inverse, self._glyphs[phase].reshape(-1, 78))

    def cell_costs(self, core: np.ndarray, phase: int, colour: np.ndarray) -> np.ndarray:
        pixels = core.reshape(78, 3)
        coefficients = np.einsum("tdp,pc->tdc", self._inverse[phase], pixels)
        coefficients -= self._inverse_g[phase][:, :, None] * colour[None, None, :]
        background = np.einsum("pd,tdc->tpc", self._design, coefficients).reshape(-1, 13, 6, 3)
        background = np.clip(background, 0, 255)
        model = self._glyphs[phase][..., None] * colour + self._transs[phase][..., None] * background
        return np.sqrt(np.mean((core - model)**2, axis=(1, 2, 3)))

    def _batch_cell_costs(self, cores: np.ndarray, phases: np.ndarray,
                          colour: np.ndarray) -> list[np.ndarray]:
        """Batch the existing polynomial fit; retain clipping and full search."""
        if getattr(self.cell_costs, "__func__", None) is not _HeaderBackgroundReader.cell_costs:
            return super()._batch_cell_costs(cores, phases, colour)
        result = [None] * len(cores)
        pixel_count = cores.shape[1] * cores.shape[2]
        for phase in range(PHASES):
            indexes = np.flatnonzero(phases == phase)
            if not len(indexes):
                continue
            pixels = cores[indexes].reshape(-1, pixel_count, 3)
            flat = pixels.transpose(0, 2, 1).reshape(-1, pixel_count)
            templates, dimensions, _ = self._inverse[phase].shape
            coefficients = (flat @ self._inverse[phase].reshape(-1, pixel_count).T)
            coefficients = coefficients.reshape(len(indexes), 3, templates, dimensions).transpose(0, 2, 3, 1)
            coefficients -= self._inverse_g[phase][None, :, :, None] * colour
            background = (coefficients.transpose(0, 1, 3, 2).reshape(-1, dimensions)
                          @ self._design.T)
            background = background.reshape(len(indexes), templates, 3, pixel_count).transpose(0, 1, 3, 2)
            np.clip(background, 0, 255, out=background)
            g = self._glyphs[phase].reshape(-1, pixel_count)
            h = self._transs[phase].reshape(-1, pixel_count)
            # Reuse this private temporary for the model and residual; the
            # arithmetic and candidate search are unchanged.
            background *= h[None, :, :, None]
            background += g[None, :, :, None] * colour
            np.subtract(pixels[:, None], background, out=background)
            np.square(background, out=background)
            costs = np.sqrt(np.mean(background, axis=(2, 3)))
            for index, cost in zip(indexes, costs):
                result[index] = cost
        return result
