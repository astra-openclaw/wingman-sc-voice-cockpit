"""Validate structured OCR positions for SC NavPoint. Author: Mallachi."""

import logging
import re

from sc_voice_cockpit.navpoint import coordinate_stack


logger = logging.getLogger(__name__)

# A Pos component: optional sign, digits, optional decimals, then a REQUIRED
# unit suffix. The suffix is mandatory by design: a missing one is the 1000x
# error mode, so an unsuffixed token is rejected rather than assumed to be
# metres. See Devlog 2026-07-26 (per-axis unit autoscaling).
_LENGTH_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)(km|m)$", re.IGNORECASE)

# Maximum allowed disagreement between the OOC Pos reading and the CamPos
# reading of the same point, when both are present. The OOC line prints 4
# decimals of km (0.1 m resolution), so anything past ~1 m means one of the two
# reads is wrong and the payload is not trustworthy.
_COORD_CROSSCHECK_TOLERANCE_M = 1.0

# The SolarSystem and Root lines carry the same numbers, so they must agree.
# System-frame values run to 1e10 m, where the printed 0.1 m resolution is
# already near the limit of what the digits express, hence a relative bound with
# an absolute floor rather than the flat metre used for the surface pair.
_SYSTEM_CROSSCHECK_REL = 1e-9
_SYSTEM_CROSSCHECK_MIN_M = 1.0

# THE NO-GAME-WORLD FLOOR. At the SC main menu the overlay still decodes a
# complete, well-formed stack, but every long-range vector it carries sits a
# few metres from the origin: measured 1.740 m on 5/5 frames across two login
# sessions. The smallest observed REAL Root magnitude is 1.3889e10 m over 647
# accepted stacks across 23 sessions. 1e6 m sits in the justified range
# [1e4, 1e9], where every value behaves identically on the evidence held:
# below 1e4 a corrupted menu row could exceed it (9.99km = 9990 m is the
# worst parseable corruption), and above 1e9 is within one decade of the
# smallest real capture. LOW is the safe direction inside that band, because
# the lower endpoint is exactly known while the upper is a sample minimum
# from one player, two systems. DO NOT RAISE without new evidence.
_MIN_WORLD_ROOT_M = 1_000_000.0

# Systems CIG has shipped, lowercased. A real OOC body_token always leads with
# one of these (System_designator_Body), so a local-frame payload whose token
# does not is unsupported and is rejected. Extend when CIG ships a
# new system.
_KNOWN_SYSTEMS = frozenset({"stanton", "pyro", "nyx"})


class NavPointScanner:
    """Validate OCR payloads without screen capture or model-response parsing.

    Active V8 frames and structured legacy replay fixtures share the coordinate
    checks. Only the live reader establishes which frame is active.
    """

    def __init__(self) -> None:
        self.last_reject_reason: str | None = None

    @staticmethod
    def _norm_str(val) -> str:
        """Normalize a payload value to a string, mapping null-like values to ''."""
        return str(val) if val and str(val).lower() not in ("null", "none") else ""

    @staticmethod
    def _parse_length_m(token) -> float | None:
        """Convert a raw overlay Pos component to metres.

        The overlay picks a unit per component by magnitude, so one line can read
        `9810.08m -174.2880km 238.0511km`. The unit suffix is mandatory here: an
        unsuffixed token is rejected rather than assumed, because a dropped "k"
        is a silent factor-of-1000 error that still looks like a plausible
        coordinate.

        Args:
            token: The raw string as displayed, e.g. "-174.2880km".

        Returns:
            The value in metres, or None if the token is missing, malformed, or
            carries no recognised unit suffix.
        """
        if token is None:
            return None
        m = _LENGTH_RE.match(str(token).strip())
        if m is None:
            return None
        value = float(m.group(1))
        return value * 1000.0 if m.group(2).lower() == "km" else value

    def _validated_stack(self, data: dict):
        """The schema-1 stack from a payload, validated, or the refusal.

        Runs BEFORE the legacy local/system interpretation, so a payload
        carrying a stack this version cannot vouch for is refused outright
        rather than quietly navigated through its legacy fields.

        Returns:
            (stack, ok). `ok` is False when a stack was offered and refused; the
            caller must then return None. A payload with no stack at all is
            (None, True), which is every legacy capture and every existing test.
        """
        raw = data.get("coordinate_stack")
        if raw is None:
            return None, True

        stack = coordinate_stack.from_dict(raw)
        if stack is None:
            logger.warning("Rejected extraction: unreadable coordinate stack.")
            self.last_reject_reason = "stack_malformed"
            return None, False

        # The same tripwire the space branch applies to system_pos_raw against
        # root_pos_raw, applied to the stack's own two anchors. A tripwire, never
        # independent proof: both lines can be misread the same way by one
        # background regime.
        if not coordinate_stack.solar_root_agree(stack.root, stack.solar):
            logger.warning(
                "Rejected extraction: the coordinate stack's SolarSystem and "
                "Root rows disagree."
            )
            self.last_reject_reason = "frame_mismatch"
            return None, False

        return stack, True

    @staticmethod
    def _camdir_from(data: dict) -> tuple[float, float, float] | None:
        """Parse the CamDir triple, or None when the line was absent."""
        camdir = data.get("camdir")
        if isinstance(camdir, (list, tuple)) and len(camdir) == 3:
            try:
                return tuple(float(c) for c in camdir)
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def _coords_from_pos_raw(cls, raw) -> dict | None:
        """Convert a three-element pos_raw list to metres.

        Args:
            raw: The pos_raw value from the payload, expected to be a sequence of
                three unit-suffixed strings.

        Returns:
            A dict with x/y/z in metres, or None if the value is not a
            three-element sequence or any component has no readable unit.
        """
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            return None
        values = [cls._parse_length_m(token) for token in raw]
        if any(v is None for v in values):
            return None
        return {"x": values[0], "y": values[1], "z": values[2]}

    @staticmethod
    def _coords_from_campos(data: dict) -> dict | None:
        """Read the optional CamPos x/y/z triple, already in metres.

        Returns None unless all three are present and numeric, since a partial
        triple cannot be cross-checked or stored.
        """
        coords: dict = {}
        for key in ("x", "y", "z"):
            val = data.get(key)
            if val is None:
                continue
            try:
                coords[key] = float(val)
            except (TypeError, ValueError):
                continue
        return coords if len(coords) == 3 else None

    def parse_payload(self, data: dict) -> dict | None:
        """Validate a reader payload; return a position or a named refusal.

        Preserves explicit coordinate units, active-frame identity, matching
        SolarSystem/Root anchors, and the non-gameplay origin gate.
        """
        self.last_reject_reason = None
        if not isinstance(data, dict) or "error" in data:
            self.last_reject_reason = "invalid_payload"
            return None

        if "active_frame_schema" in data:
            from sc_voice_cockpit.navpoint.active_frame import validate_planet_payload
            try:
                return validate_planet_payload(data)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                self.last_reject_reason = "invalid_active_planet_frame"
                return None

        # The schema-1 branch, ahead of every legacy interpretation below. It
        # decides nothing about routing; it establishes whether the stack this
        # payload carries may be trusted, and refuses the whole payload when it
        # may not. `stack_dict` is then attached to whichever result the legacy
        # branches build, so one capture produces one record.
        stack, stack_ok = self._validated_stack(data)
        if not stack_ok:
            return None
        stack_dict = coordinate_stack.to_dict(stack) if stack is not None else None

        # THE NO-GAME-WORLD GATE, stack half. Every guard above this line
        # validates SHAPE (units, agreement, echoes); none of them asks
        # whether the shape describes a place in the game. At the SC main
        # menu the overlay decodes a complete, valid stack sitting a few
        # metres from the origin, so a payload can clear every gate above
        # and still describe no game world at all.
        #
        # Checked here, on the stack alone, because `_validated_stack` just
        # above already proved `stack.root` and `stack.solar` AGREE (or
        # refused the payload as `frame_mismatch` before this line was ever
        # reached). A magnitude verdict is only meaningful once the two
        # numbers behind it are known to agree; a disagreeing pair is
        # evidence that one of them is wrong, not evidence about where the
        # player is, so it must be reported as the corruption it is
        # (`frame_mismatch`) rather than relabelled `no_game_world`. The
        # LEGACY half of this same gate (root_pos_raw/system_pos_raw) is
        # therefore checked further down, after ITS OWN agreement crosscheck,
        # not here: that pair has not been vetted yet at this point.
        #
        # A legacy OOC payload with no stack and no root/system fields is
        # invisible to either half, since it offers no long-range vector to
        # check. That is not a hole: the menu has no OOC_ row to produce such
        # a payload from, so this gate is unreachable from there.
        if stack is not None and any(
            coordinate_stack.distance_m((0.0, 0.0, 0.0), vec) < _MIN_WORLD_ROOT_M
            for vec in (stack.root.xyz,)
        ):
            logger.warning(
                "Rejected extraction: the coordinate stack's Root sits "
                "inside the no-game-world floor (%.1f m), which is where "
                "the SC main menu reads.",
                _MIN_WORLD_ROOT_M,
            )
            self.last_reject_reason = "no_game_world"
            return None

        def with_stack(result: dict) -> dict:
            if "precision" in data:
                result["precision"] = data["precision"]
            if "active_frame" in data:
                result["active_frame"] = data["active_frame"]
            if "camdir" in data and self._camdir_from(data) is not None:
                result["camdir"] = self._camdir_from(data)
            if stack_dict is None:
                return result
            result["coordinate_stack"] = stack_dict
            result["stack_schema"] = coordinate_stack.SCHEMA_VERSION
            # Root fills x/y/z ONLY when the legacy branches left them empty,
            # which is exactly the capture that has no legacy frame at all.
            # `setdefault` and not assignment: an OOC capture's x/y/z are
            # BODY-frame metres, and overwriting them with a system-frame Root
            # position would put every surface waypoint at the wrong end of a
            # ten-digit number. Everything downstream, the HUD page, the ring's
            # freshness check and the poll loop's usable-fix test, reads x/y/z,
            # so a stack capture has to carry them or it looks like no fix.
            for key, value in zip(("x", "y", "z"), stack.root.xyz):
                result.setdefault(key, value)
            return result

        if not data.get("local_frame"):
            # Open space: no body-fixed frame, so body stays empty and every
            # body-keyed guard downstream still refuses. The SYSTEM frame is
            # usable here though: a point not attached to a rotating body has a
            # stable system-frame address (measured 2026-07-26, byte-identical
            # over five minutes with the ship parked). See Devlog.
            result = {
                "local_frame": False,
                "body": "",
                "zone": "",
                "system": "",
                "location": self._norm_str(data.get("location")),
                "server_id": self._norm_str(data.get("server_id")),
            }
            system = self._coords_from_pos_raw(data.get("system_pos_raw"))
            if system is None:
                if data.get("system_pos_raw") is not None:
                    logger.warning(
                        "System-frame coordinates unreadable: %r",
                        data.get("system_pos_raw"),
                    )
                    self.last_reject_reason = "unreadable_units"
                return with_stack(result)

            # The Root zone line duplicates the SolarSystem line digit for digit
            # in every capture observed so far, so requiring the two to agree is
            # a deterministic tripwire a confabulating model cannot pass by luck.
            # It validates the payload itself rather than blacklisting known-bad
            # values, which is stronger than the canary check.
            root = self._coords_from_pos_raw(data.get("root_pos_raw"))
            if root is not None:
                worst = max(abs(system[k] - root[k]) for k in ("x", "y", "z"))
                allowed = max(
                    _SYSTEM_CROSSCHECK_MIN_M,
                    max(abs(system[k]) for k in ("x", "y", "z")) * _SYSTEM_CROSSCHECK_REL,
                )
                if worst > allowed:
                    logger.warning(
                        "Rejected space capture: SolarSystem and Root lines "
                        "disagree by %.1f m (allowed %.1f).",
                        worst,
                        allowed,
                    )
                    self.last_reject_reason = "frame_mismatch"
                    return None

            # THE NO-GAME-WORLD GATE, legacy half. Reached only once `system`
            # (and `root`, if it was offered) are known to AGREE: the crosscheck
            # above already returned on a disagreement. Checking magnitude on a
            # pair proven consistent, rather than on either number alone before
            # that proof, is what keeps a corrupted-but-large-vs-tiny pair
            # reported as `frame_mismatch` instead of the weaker `no_game_world`
            # claim. `root` is included precisely because it may be ABSENT
            # (tolerated above) while still present and agreeing; either way,
            # every vector this branch is about to trust gets checked.
            world_vectors = [(system["x"], system["y"], system["z"])]
            if root is not None:
                world_vectors.append((root["x"], root["y"], root["z"]))
            if any(
                coordinate_stack.distance_m((0.0, 0.0, 0.0), vec) < _MIN_WORLD_ROOT_M
                for vec in world_vectors
            ):
                logger.warning(
                    "Rejected extraction: the SolarSystem/Root position sits "
                    "inside the no-game-world floor (%.1f m), which is where "
                    "the SC main menu reads.",
                    _MIN_WORLD_ROOT_M,
                )
                self.last_reject_reason = "no_game_world"
                return None

            result["system_frame"] = True
            result["frame_id"] = self._norm_str(data.get("system_zone_id"))
            result.update(system)
            # Facing matters just as much out here: without it the guidance can
            # only report distance, which is what the first live run showed
            # ("heading data is not available for this system frame").
            camdir = self._camdir_from(data)
            if camdir is not None:
                result["camdir"] = camdir
            return with_stack(result)

        # Deterministic plausibility gate: a real OOC token is always
        # System_designator_Body, so it must split into at least two `_`
        # segments and lead with a known system. This intentionally rejects
        # single-token bodies (the old lenient split allowed them, letting a
        # confabulated single word through as body == system). See the
        # 2026-07-22 Nyx-hangar confabulation incident (Devlog.md v4.9.0.5).
        token = self._norm_str(data.get("body_token"))
        parts = token.split("_") if token else []
        if len(parts) < 2 or parts[0].lower() not in _KNOWN_SYSTEMS:
            logger.warning(
                "Rejected extraction: implausible body_token %r (expected "
                "System_designator_Body leading with a known system).",
                token,
            )
            self.last_reject_reason = "implausible_body"
            return None

        # Local frame: coordinates are mandatory, else the capture is unusable.
        #
        # Two independent readings may be available. pos_raw comes from the OOC
        # zone Pos line and is PRIMARY, because that line is present in every
        # local frame; x/y/z come from CamPos Planet Zone, which SC 4.9 omits on
        # a planet surface (Devlog 2026-07-26). When both are present they are
        # cross-checked against each other, which is free evidence that the model
        # read the screen instead of inventing numbers.
        primary = self._coords_from_pos_raw(data.get("pos_raw"))
        campos = self._coords_from_campos(data)

        if primary is not None and campos is not None:
            worst = max(abs(primary[k] - campos[k]) for k in ("x", "y", "z"))
            if worst > _COORD_CROSSCHECK_TOLERANCE_M:
                logger.warning(
                    "Rejected extraction: OOC Pos and CamPos disagree by %.3f m "
                    "(OOC %r, CamPos %r); one of the two reads is wrong.",
                    worst,
                    primary,
                    campos,
                )
                self.last_reject_reason = "coord_mismatch"
                return None

        coords = primary if primary is not None else campos
        if coords is None:
            if data.get("pos_raw") is not None:
                # Tokens were offered but at least one carried no readable unit,
                # which is the factor-of-1000 failure mode. Refuse loudly rather
                # than assume metres.
                logger.warning(
                    "Rejected extraction: pos_raw %r has a component with no "
                    "readable unit suffix.",
                    data.get("pos_raw"),
                )
                self.last_reject_reason = "unreadable_units"
            else:
                logger.info("Extraction incomplete: no usable coordinates")
            return None

        # Split derived above; system is the first `_` segment, body the last.
        result: dict = {
            "local_frame": True,
            "body": parts[-1],
            "system": parts[0],
            "zone": self._norm_str(data.get("zone")),
            "location": self._norm_str(data.get("location")),
            "server_id": self._norm_str(data.get("server_id")),
        }
        result.update(coords)

        # CamDir is stored raw as a 3-float tuple; the heading conversion is parked
        # until in-game semantics are verified. Absent when the line is missing.
        camdir = self._camdir_from(data)
        if camdir is not None:
            result["camdir"] = camdir

        for key in ("altitude", "radius"):
            val = data.get(key)
            if val is not None:
                try:
                    result[key] = float(val)
                except (TypeError, ValueError):
                    pass

        return with_stack(result)
