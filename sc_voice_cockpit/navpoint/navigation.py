"""
navigation.py — Bearing and direction calculations for SC NavPoint
Author: Mallachi

Declared scope: short-range surface navigation in a body-fixed local frame
(back to a cave, wreck, or outpost on the same planet or moon). The flat
Cartesian math here is intentional for that range. Far targets on a curved
surface therefore yield chord distance and through-the-planet bearings; that is
accepted for the declared scope, not a defect to fix. Positions from different
bodies live in different local frames and must never be mixed; callers enforce
that (this module never sees the body and does no cross-body check itself).
"""

import math


class LocalFrameUnavailable(ValueError):
    """Raised where a local tangent basis cannot be built (body centre or pole)."""


def local_basis(
    position: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Return (up, north, east) unit vectors at a body-fixed surface position.

    The body frame's origin is the body centre and its +Z axis is the spin axis;
    both were measured on 2026-07-26 (computed radius 295.196 km against the
    overlay's own Radius of 295000 m on Daymar, and a stationary surface point
    whose system-frame Z never moved while X and Y swept through the rotation).

    Args:
        position: Body-frame (x, y, z) in metres.

    Returns:
        (up, north, east). up is radially outward, north is the tangent
        component of the spin axis, and east completes the pair so that a
        bearing of 90 degrees is the direction of increasing longitude.

    Raises:
        LocalFrameUnavailable: At the body centre, or at a pole where north is
            undefined.
    """
    x, y, z = position
    r = math.sqrt(x * x + y * y + z * z)
    if r == 0.0:
        raise LocalFrameUnavailable("no local basis at the body centre")
    up = (x / r, y / r, z / r)

    # north = the +Z axis with its "up" component removed, then normalised.
    nx, ny, nz = -up[2] * up[0], -up[2] * up[1], 1.0 - up[2] * up[2]
    n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n_len < 1e-9:
        raise LocalFrameUnavailable("north is undefined at a pole")
    north = (nx / n_len, ny / n_len, nz / n_len)

    east = (
        north[1] * up[2] - north[2] * up[1],
        north[2] * up[0] - north[0] * up[2],
        north[0] * up[1] - north[1] * up[0],
    )
    return up, north, east


def _bearing_of(
    direction: tuple[float, float, float],
    position: tuple[float, float, float],
) -> float:
    """Compass bearing of a body-frame direction, seen from a surface position.

    Raises:
        LocalFrameUnavailable: If the basis is unavailable, or the direction is
            straight up or down and so has no bearing.
    """
    up, north, east = local_basis(position)
    d_up = sum(d * u for d, u in zip(direction, up))
    tangent = tuple(d - d_up * u for d, u in zip(direction, up))
    if math.sqrt(sum(t * t for t in tangent)) < 1e-9:
        raise LocalFrameUnavailable("direction is vertical, bearing undefined")
    t_north = sum(t * n for t, n in zip(tangent, north))
    t_east = sum(t * e for t, e in zip(tangent, east))
    return math.degrees(math.atan2(t_east, t_north)) % 360.0


def surface_bearing(
    from_pos: tuple[float, float, float],
    to_pos: tuple[float, float, float],
) -> float:
    """True compass bearing from one body-frame position to another.

    Unlike the flat-frame angle in calculate_bearing, this is referenced to
    local north at from_pos, so it is directly comparable with the heading that
    camdir_to_bearing produces.

    Raises:
        LocalFrameUnavailable: At the body centre, at a pole, or when the target
            is directly overhead or underfoot.
    """
    return _bearing_of(tuple(t - f for t, f in zip(to_pos, from_pos)), from_pos)


def camdir_view_vector(camdir: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert an overlay CamDir triple to a unit view direction in body axes.

    CamDir is Euler angles in DEGREES in the body-fixed frame, applied about
    fixed axes in the order Y, X, Z, with the first component negated. Solved
    2026-07-26 from three 13-frame surface sweeps on Daymar: fitted at one site
    and applied unchanged at the other two, reproducing the in-game compass to
    0.5 to 1.7 degrees of scatter. See Devlog for the derivation and for the
    three models this replaced.

    Args:
        camdir: The raw (c0, c1, c2) from the overlay's CamDir line.

    Returns:
        Unit view direction in body-frame coordinates.
    """
    a = math.radians(-camdir[0])
    b = math.radians(camdir[1])
    c = math.radians(camdir[2])
    sa, ca = math.sin(a), math.cos(a)
    sb, cb = math.sin(b), math.cos(b)
    sc, cc = math.sin(c), math.cos(c)
    # Rz(c) . Rx(b) . Ry(a) applied to the +X axis.
    return (
        ca * cc - sa * sb * sc,
        ca * sc + sa * sb * cc,
        -sa * cb,
    )


def camdir_orientation(
    camdir: tuple[float, float, float],
) -> tuple[
    tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
]:
    """Full camera orientation from CamDir: (forward, right, up).

    The same rotation that camdir_view_vector evaluates, but keeping all three
    axes. Forward and up are the rotated +X and +Z axes. Screen-right is
    forward crossed with up, hence the rotated -Y axis. Using +Y mirrored
    horizontal guidance during the Bloom return test.
    """
    a = math.radians(-camdir[0])
    b = math.radians(camdir[1])
    c = math.radians(camdir[2])
    sa, ca = math.sin(a), math.cos(a)
    sb, cb = math.sin(b), math.cos(b)
    sc, cc = math.sin(c), math.cos(c)
    forward = (ca * cc - sa * sb * sc, ca * sc + sa * sb * cc, -sa * cb)
    right = (cb * sc, -cb * cc, -sb)
    up = (sa * cc + ca * sb * sc, sa * sc - ca * sb * cc, ca * cb)
    return forward, right, up


def relative_deflection(
    camdir: tuple[float, float, float],
    from_pos: tuple[float, float, float],
    to_pos: tuple[float, float, float],
) -> dict:
    """Where a target sits relative to where the camera points.

    Needs no compass and no local vertical, so it works in the system frame of
    open space as well as on a surface: it only compares two directions
    expressed in the same frame. Yaw is positive to the right, pitch positive
    upward, both in degrees, and total is the true angle between the two
    directions.

    Args:
        camdir: The overlay CamDir triple.
        from_pos: Observer position, in the same frame as to_pos.
        to_pos: Target position.

    Returns:
        Dict with yaw_offset_deg, pitch_offset_deg, total_offset_deg and
        distance_m.

    Raises:
        LocalFrameUnavailable: If the target coincides with the observer, where
            no direction exists.
    """
    delta = tuple(t - f for t, f in zip(to_pos, from_pos))
    distance = math.sqrt(sum(d * d for d in delta))
    if distance < 1e-6:
        raise LocalFrameUnavailable("target coincides with the observer")
    target = tuple(d / distance for d in delta)

    forward, right, up = camdir_orientation(camdir)
    f_comp = sum(t * v for t, v in zip(target, forward))
    r_comp = sum(t * v for t, v in zip(target, right))
    u_comp = sum(t * v for t, v in zip(target, up))

    return {
        "yaw_offset_deg": math.degrees(math.atan2(r_comp, f_comp)),
        "pitch_offset_deg": math.degrees(math.asin(max(-1.0, min(1.0, u_comp)))),
        "total_offset_deg": math.degrees(math.acos(max(-1.0, min(1.0, f_comp)))),
        "distance_m": distance,
    }


def system_camdir_orientation(
    camdir: tuple[float, float, float],
) -> tuple[
    tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
]:
    """Camera basis in the star system's frame: (forward, right, up).

    CamDir is (pitch, roll, yaw), measured on 2026-07-28 by FLYING each vector
    and watching which coordinate moved, which is a stronger instrument than
    reading the numbers and interpreting them:

        CamDir (0, 0, 0)   -> flying forward increased Y only  -> forward is +Y
        CamDir (90, 0, 0)  -> nose pitched up                  -> pitch, + is up
        CamDir (0, 0, -90) -> yawed right, facing east         -> yaw, + is LEFT
        CamDir (0, 90, 0)  -> rolled right                     -> roll, + is right

    Note the yaw sign: yawing RIGHT reads NEGATIVE, so the yaw angle used below
    is the negation of the raw component. Two independent readings agreed on
    that ("looking east makes camdir 0 0 -90", then the same value again after
    the axis names were sorted out), which is why it is trusted; the middle
    reports that disagreed had pitch/roll/yaw in the wrong slots.

    The system frame is left-handed with +X east, +Y north, +Z up, origin at the
    star's centre. Facing north therefore puts east on your right, which is the
    handedness the same session's screenshot confirms: the star sat left of the
    crosshair exactly where a negative X component says it should.

    This is NOT `camdir_orientation`. That one was fitted in the body-fixed
    frame on a planet surface and validated there against the in-game compass,
    but only its FORWARD vector was ever tested, because a compass bearing is a
    forward direction. Applied out here it returns forward +X where the truth is
    +Y, with right and forward swapped: a mirror, not a rotation, which is why
    no single azimuth constant ever fixed it.

    Roll rotates right and up about forward, which the ring needs: the bubble
    is drawn on a screen that banks with the ship. A wrong roll sign leaves the
    total offset exact and mirrors the bubble as the ship rolls, so it would
    show up in flight and nowhere else.

    Args:
        camdir: The raw (pitch, roll, yaw) triple from the overlay's CamDir
            line, in degrees.

    Returns:
        (forward, right, up) as unit vectors in system coordinates.
    """
    pitch = math.radians(camdir[0])
    roll = math.radians(camdir[1])
    # Negated: a right yaw reads negative, so this is the angle turned RIGHT
    # from north, which is what the geometry below is written in terms of.
    yaw = math.radians(-camdir[2])
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)

    forward = (cp * sy, cp * cy, sp)
    # Level right and up, before roll: facing north, right is east.
    right0 = (cy, -sy, 0.0)
    up0 = (-sy * sp, -cy * sp, cp)

    right = tuple(r * cr - u * sr for r, u in zip(right0, up0))
    up = tuple(r * sr + u * cr for r, u in zip(right0, up0))
    return forward, right, up


def system_deflection(
    camdir: tuple[float, float, float],
    from_pos: tuple[float, float, float],
    to_pos: tuple[float, float, float],
) -> dict:
    """Where a target sits relative to the camera, in the system frame.

    Identical in shape to `relative_deflection`, and identical in method; the
    only difference is which convention turns CamDir into a camera basis. Two
    functions rather than a flag because the two frames genuinely disagree, and
    a single function with a mode is one wrong argument away from the confident
    wrong direction this whole gate exists to prevent.

    Raises:
        LocalFrameUnavailable: If the target coincides with the observer.
    """
    delta = tuple(t - f for t, f in zip(to_pos, from_pos))
    distance = math.sqrt(sum(d * d for d in delta))
    if distance < 1e-6:
        raise LocalFrameUnavailable("target coincides with the observer")
    target = tuple(d / distance for d in delta)

    forward, right, up = system_camdir_orientation(camdir)
    f_comp = sum(t * v for t, v in zip(target, forward))
    r_comp = sum(t * v for t, v in zip(target, right))
    u_comp = sum(t * v for t, v in zip(target, up))

    return {
        "yaw_offset_deg": math.degrees(math.atan2(r_comp, f_comp)),
        "pitch_offset_deg": math.degrees(math.asin(max(-1.0, min(1.0, u_comp)))),
        "total_offset_deg": math.degrees(math.acos(max(-1.0, min(1.0, f_comp)))),
        "distance_m": distance,
    }


# Direction in the SYSTEM frame was wrong, and the cause is now known.
#
# Measured 2026-07-26 against an in-game marker whose true bearing was known: we
# reported yaw +108.7 deg where the truth was -18.8. The cause, found 2026-07-28,
# is that `camdir_orientation` (fitted on a planet surface, and validated there
# only on its FORWARD vector, because a compass bearing is a forward direction)
# returns forward and right SWAPPED in the system frame. A mirror, not a
# rotation, which is why no azimuth constant ever fixed it.
#
# `system_camdir_orientation` replaces it out here, built from three vectors
# flown and verified by watching which coordinate moved. Two independent
# out-of-sample checks agree with it: the same evening's screenshot reproduces
# the game's own readout (15.00 Gm, azimuth -73.21, star to the LEFT of the
# crosshair) and the July marker resolves to -18.7 against its recorded -18.8.
#
# The flag below stays False regardless until the ring is seen pointing at a
# real waypoint in flight. Two calculations agreeing is not a third thing
# observed, and this flag's entire purpose is to refuse a confident direction
# that has not been watched working.
#
# Until it is solved, guidance reports DISTANCE ONLY. A confidently wrong
# direction is worse than none: it sent the user flying the wrong way. Flip this
# to True once the calibration is verified against known directions.
SPACE_DIRECTION_CALIBRATED = False


def space_guidance(
    camdir: tuple[float, float, float] | None,
    from_pos: tuple[float, float, float],
    to_pos: tuple[float, float, float],
    allow_uncalibrated: bool = False,
) -> dict:
    """Guidance toward a system-frame waypoint, in the HUD's usual dict shape.

    Open space has no north, so there is no compass bearing to report and
    horizontal_bearing_deg is deliberately absent rather than filled with a
    number that would look meaningful and not be. Where CamDir is available the
    offsets are relative to where the player is actually pointing, which is what
    aligning a quantum vector needs; without it only the distance is reported.

    Flat Cartesian is exactly right here, unlike on a curved surface: the system
    frame is a genuine Euclidean space.
    """
    dx, dy, dz = (t - f for t, f in zip(to_pos, from_pos))
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    # Both instruction keys are always present: callers format them
    # unconditionally, so omitting one on a fallback path turns a degraded
    # reading into a crash.
    result: dict = {
        "frame": "system",
        "distance_m": round(distance, 1),
        "distance_km": round(distance / 1000.0, 3),
        "turn_instruction": "Ahead",
        "elevation_instruction": "Level",
    }
    if camdir is None or not (SPACE_DIRECTION_CALIBRATED or allow_uncalibrated):
        result["turn_instruction"] = "Direction not available in open space"
        result["direction_note"] = (
            "Distance is measured and correct. Direction is withheld because the "
            "CamDir convention is not yet calibrated for the system frame: it was "
            "measured 108.7 degrees out against a known marker."
        )
        return result

    try:
        deflection = system_deflection(camdir, from_pos, to_pos)
    except LocalFrameUnavailable:
        # Standing on the waypoint: no direction exists, and none is needed.
        return result

    result["horizontal_offset_deg"] = round(deflection["yaw_offset_deg"], 1)
    result["vertical_angle_deg"] = round(deflection["pitch_offset_deg"], 1)
    result["total_offset_deg"] = round(deflection["total_offset_deg"], 1)
    if not SPACE_DIRECTION_CALIBRATED:
        # Produced only because a caller asked for the uncalibrated numbers.
        # Flagged so nothing downstream can present them as trustworthy.
        result["uncalibrated"] = True
    result["turn_instruction"] = _turn_instruction(deflection["yaw_offset_deg"])
    result["elevation_instruction"] = _elevation_instruction(
        deflection["pitch_offset_deg"]
    )
    return result


# A cached position older than this (seconds) is treated as absent: it can no
# longer refuse a cross-body target, and no instrument draws a direction from
# it. 30s is three missed polls at the 10s maximum poll interval. Defined here
# rather than in the HUD server because two consumers now enforce it, and a
# freshness rule that differs between the ring and the page is a bug waiting to
# be blamed on the game.
POSITION_FRESHNESS_S = 30.0


def _space_stack_pair(position, target):
    """Fixed Root endpoints, only in a positively identified space/session frame.

    Nested rows never select the direction. A Root bookmark addresses the saved
    point, not a moving ship or a rotating surface feature.
    """
    if (not position or not target or position.get("active_frame") != "space"
            or position.get("body") or target.get("frame") != "stack"):
        return None
    precision = position.get("precision") or {}
    if precision.get("mode") == "cruise" and precision.get("target_id") != target.get("id"):
        return None
    from sc_voice_cockpit.navpoint import coordinate_stack as cs
    try:
        current = cs.from_dict(position.get("coordinate_stack"))
        saved = cs.from_json(target.get("coordinate_stack"))
        if current is None or saved is None:
            return None
        session = current.solar.raw_name
        if (not session.startswith("SolarSystem_") or not session[len("SolarSystem_"):]
                or saved.solar.raw_name != session
                or not all(cs.solar_root_agree(s.root, s.solar) for s in (current, saved))):
            return None
        if not math.isfinite(math.dist(current.root.xyz, saved.root.xyz)):
            return None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return current, saved


def stack_space_guidance(position, target, position_age_s):
    """Space guidance from the same Euler camera basis used by planetary flight.

    Space needs no fitted planet angle. The legacy system/test convention stays
    gated; current active-frame reads explicitly choose this path instead.
    """
    if (position_age_s is None or not math.isfinite(position_age_s)
            or not 0 <= position_age_s <= POSITION_FRESHNESS_S):
        return None
    pair = _space_stack_pair(position, target)
    if pair is None:
        return None
    current, saved = pair
    delta = tuple(t-f for f,t in zip(current.root.xyz, saved.root.xyz))
    distance = math.hypot(*delta)
    result = dict(frame="stack", distance_m=distance, distance_km=distance/1000,
                  precision=position.get("precision") or {},
                  turn_instruction="Direction unavailable", elevation_instruction="Level")
    camdir = position.get("camdir")
    if (distance < 1e-6 or not isinstance(camdir, (tuple, list)) or len(camdir) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in camdir)):
        return result
    from sc_voice_cockpit.navpoint.planet_rotation import local_camera_basis
    forward, right, up = local_camera_basis(camdir, 0.0)
    f, r, u = (sum(d*v for d,v in zip(delta,axis))/distance
               for axis in (forward,right,up))
    yaw = math.degrees(math.atan2(r, f))
    pitch = math.degrees(math.asin(max(-1., min(1., u))))
    result.update(horizontal_offset_deg=yaw, vertical_angle_deg=pitch,
                  total_offset_deg=math.degrees(math.acos(max(-1., min(1., f)))),
                  turn_instruction=_turn_instruction(yaw),
                  elevation_instruction=_elevation_instruction(pitch))
    return result


def ring_deflection(
    position: dict | None,
    target: dict | None,
    position_age_s: float | None,
    freshness_s: float = POSITION_FRESHNESS_S,
    allow_uncalibrated_space: bool = False,
) -> tuple[float, float] | None:
    """Yaw and pitch, in degrees, for the deflection ring's bubble.

    Returns None wherever a bubble would be a claim we cannot support: no
    target, no position, a stale reading, mismatched frames, or the system
    frame whose direction convention is still uncalibrated. The ring's no-fix
    face is the correct output there. A bubble pointing somewhere invented is
    the failure mode this whole skill has paid for twice.

    Args:
        position: A capture in the shape `scanner.parse_payload` returns.
        target: A waypoint in the shape `NavPointDatabase.navpoint_to_dict`
            returns.
        position_age_s: Seconds since the capture, or None when unstamped.
        freshness_s: Maximum age a reading may have and still be believed.
        allow_uncalibrated_space: Draw a system-frame bubble from the
            UNCALIBRATED direction convention. For the calibration flight only:
            the numbers it produces are known to be wrong (measured 108.7
            degrees out on 2026-07-26) and exist to be compared against a known
            direction, never to be flown on.

    Returns:
        (yaw_deg, pitch_deg), yaw positive to the right and pitch positive
        upward, or None when no direction may be drawn.
    """
    if not position or not target:
        return None
    precision = position.get("precision") or {}
    if precision.get("mode") == "cruise" and precision.get("target_id") != target.get("id"):
        return None
    if position_age_s is None or position_age_s > freshness_s:
        return None
    if target.get("frame") == "stack":
        if position.get("body"):
            return None
        if not allow_uncalibrated_space:
            guidance = stack_space_guidance(position, target, position_age_s)
            if guidance and "horizontal_offset_deg" in guidance:
                return guidance["horizontal_offset_deg"], guidance["vertical_angle_deg"]
            return None
        # The corrected system basis can now be compared with a real saved
        # Root waypoint during calibration. Ordinary guidance remains gated:
        # neither a local candidate nor a generic space flag proves that CamDir
        # and Root share axes in the current flight context.
        if (not math.isfinite(position_age_s) or position_age_s < 0
                or not math.isfinite(freshness_s) or freshness_s < 0):
            return None
        from sc_voice_cockpit.navpoint import coordinate_stack as cs

        try:
            current_stack = cs.from_dict(position.get("coordinate_stack"))
            target_stack = cs.from_json(target.get("coordinate_stack"))
        except (TypeError, ValueError, OverflowError):
            # A malformed saved coordinate cannot escape onto the ring thread.
            return None
        if current_stack is None or target_stack is None:
            return None
        if not all(cs.solar_root_agree(s.root, s.solar)
                   for s in (current_stack, target_stack)):
            return None
        camdir = position.get("camdir")
        if (not isinstance(camdir, (list, tuple)) or len(camdir) != 3
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) for v in camdir)):
            return None
        here, there = current_stack.root.xyz, target_stack.root.xyz
        squared_distance = sum((t - f) * (t - f) for f, t in zip(here, there))
        if not math.isfinite(squared_distance) or squared_distance < 1e-12:
            return None
        guidance = space_guidance(camdir, here, there, allow_uncalibrated=True)
        if "horizontal_offset_deg" not in guidance:
            return None
        return guidance["horizontal_offset_deg"], guidance["vertical_angle_deg"]
    if any(position.get(k) is None for k in ("x", "y", "z")):
        return None
    if any(target.get(k) is None for k in ("x", "y", "z")):
        return None

    here = (position["x"], position["y"], position["z"])
    there = (target["x"], target["y"], target["z"])

    if target.get("frame") == "system" or position.get("system_frame"):
        # Both sides must be in the system frame, and in the SAME one: the
        # frame is per session, so a waypoint from another session's frame is
        # not addressable from here.
        if target.get("frame") != "system" or not position.get("system_frame"):
            return None
        here_id = position.get("frame_id") or ""
        there_id = target.get("frame_id") or ""
        if here_id and there_id and here_id != there_id:
            return None
        guidance = space_guidance(
            position.get("camdir"), here, there, allow_uncalibrated_space
        )
        if "horizontal_offset_deg" not in guidance:
            # Uncalibrated system-frame direction, withheld on purpose.
            return None
        return guidance["horizontal_offset_deg"], guidance["vertical_angle_deg"]

    pos_body = position.get("body") or ""
    target_body = target.get("body") or ""
    if not pos_body or not target_body or pos_body.lower() != target_body.lower():
        return None

    if position.get("active_frame") in ("planet", "site"):
        from sc_voice_cockpit.navpoint.active_frame import same_planet
        if not same_planet(position, target):
            return None
        camdir = position.get("camdir")
        if not isinstance(camdir, (list, tuple)) or len(camdir) != 3:
            return None
        try:
            if position.get("active_frame") == "planet":
                rotation = position.get("planet_rotation") or {}
                angle = rotation.get("angle")
                if (rotation.get("valid") is not True
                        or rotation.get("frame_id") != position.get("frame_id")
                        or isinstance(angle, bool) or not isinstance(angle, (int, float))
                        or not math.isfinite(angle)):
                    return None
                from sc_voice_cockpit.navpoint.planet_rotation import local_camera_basis
                forward, right, up = local_camera_basis(camdir, angle)
            else:
                from sc_voice_cockpit.navpoint.site_rotation import validated_rotation, site_camera_basis
                rotation = validated_rotation(position.get('site_rotation'),position.get('frame_id'))
                if rotation is None:
                    return None
                forward, right, up = site_camera_basis(camdir,rotation)
            delta = tuple(t-f for f,t in zip(here,there))
            distance = math.sqrt(sum(v*v for v in delta))
            if distance < 1e-6:return None
            f,r,u = (sum(d*v for d,v in zip(delta,axis))/distance for axis in (forward,right,up))
            return math.degrees(math.atan2(r,f)), math.degrees(math.asin(max(-1.,min(1.,u))))
        except (LocalFrameUnavailable, ValueError, TypeError, OverflowError):
            return None

    # Untyped legacy captures provide no measured camera-to-frame transform.
    return None


def ring_distance(position: dict | None, target: dict | None,
                  position_age_s: float | None) -> str:
    """Range is independent of camera calibration and status notices."""
    if target and target.get("frame") == "stack":
        guidance = stack_space_guidance(position, target, position_age_s)
        return format_distance(guidance["distance_km"]) if guidance else ""
    from sc_voice_cockpit.navpoint.active_frame import same_planet
    if (not position or not target or position_age_s is None
            or not math.isfinite(position_age_s)
            or not 0 <= position_age_s <= POSITION_FRESHNESS_S
            or not same_planet(position, target)):
        return ""
    precision = position.get("precision") or {}
    if precision.get("mode") == "cruise" and precision.get("target_id") != target.get("id"):
        return ""
    try:
        distance = math.dist([position[k] for k in ("x", "y", "z")],
                             [target[k] for k in ("x", "y", "z")])
    except (KeyError, TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(distance):
        return ""
    return format_distance(distance / 1000)


def ring_hold_key(position: dict | None, target: dict | None):
    """Scope display-only retention to one target and matching frame."""
    if target and target.get("frame") == "stack":
        pair = _space_stack_pair(position, target)
        if pair is None or math.dist(pair[0].root.xyz, pair[1].root.xyz) < 1e-6:
            return None
        return ("space", pair[0].solar.raw_name, target.get("id"),
                target.get("name"), pair[1].root.xyz)
    from sc_voice_cockpit.navpoint.active_frame import same_planet
    if (not position or not target
            or position.get("active_frame") not in ("planet", "site")
            or not same_planet(position, target)):
        return None
    try:
        here = tuple(position[k] for k in ("x", "y", "z"))
        there = tuple(target[k] for k in ("x", "y", "z"))
        if (not all(math.isfinite(v) for v in (*here, *there))
                or math.dist(here, there) < 1e-6):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return (position.get("active_frame"), position.get("frame_id"),
            position.get("system"),
            *(target.get(k) for k in ("id", "name", "frame", "frame_id", "x", "y", "z")))


def ring_status(
    position: dict | None,
    target: dict | None,
    position_age_s: float | None,
    reader_state: str = "",
    freshness_s: float = POSITION_FRESHNESS_S,
    allow_uncalibrated_space: bool = False,
) -> tuple[tuple[float, float] | None, str]:
    """Decide the ring direction and status from the last successful capture.

    Failed reads retain a recent, valid deflection and label it HELD with its
    capture age. ring_deflection still enforces freshness, coordinate-frame and
    direction-calibration gates. When no valid deflection exists, show the
    failure reason. Holding a display never makes a new fix or arrival event.
    """
    from sc_voice_cockpit.navpoint.overlay_fields import RING_ACQUIRING, RING_NO_BEARING

    deflection = ring_deflection(
        position, target, position_age_s, freshness_s, allow_uncalibrated_space
    )
    if not target:
        # Not navigating anywhere, so there is nothing the player needs told.
        return deflection, ""
    if reader_state:
        if deflection is not None:
            # A held fix is display-only; no failed poll enters arrival logic.
            # The age and frame gates in ring_deflection still apply.
            return deflection, f"HELD {max(0, int(position_age_s))}s"
        return None, reader_state
    if deflection is None:
        # WHY there is no bubble decides what to say, and getting this wrong is
        # a quiet lie. In open space the direction convention is uncalibrated,
        # so a bearing is not late, it is unavailable, and no amount of waiting
        # produces one. Everything else here (aged-out fix, frame mismatch)
        # really does recover on its own.
        if target.get("frame") == "stack":
            # Same answer as the system frame, for the same reason, and said
            # rather than left as a silent blank ring: the distance is known and
            # the direction is not, and no amount of waiting produces one, so
            # ACQUIRING here would promise a fix that cannot arrive. The MODE
            # (ROOT APPROACH against LOCAL) goes to the HUD page, which has room
            # for thirteen characters and a ring 220 px wide does not.
            return None, RING_NO_BEARING
        if (
            not SPACE_DIRECTION_CALIBRATED
            and not allow_uncalibrated_space
            and target.get("frame") == "system"
            and position
            and position.get("system_frame")
        ):
            return None, RING_NO_BEARING
        return None, RING_ACQUIRING
    precision = (position or {}).get("precision") or {}
    if precision.get("target_id") == target.get("id"):
        if precision.get("mode") == "cruise":
            return deflection, "COARSE"
        if precision.get("slow_down_advisory"):
            return deflection, "SLOW DOWN"
    return deflection, ""


def camdir_to_bearing(
    camdir: tuple[float, float, float],
    position: tuple[float, float, float],
) -> float:
    """Compass heading the player is facing, from CamDir and body-frame position.

    Position is required, not incidental: CamDir is referenced to the body's
    axes rather than to the local horizon, so the same triple means a different
    compass bearing at a different latitude and longitude.

    Raises:
        LocalFrameUnavailable: At the body centre, at a pole, or when looking
            straight up or down.
    """
    return _bearing_of(camdir_view_vector(camdir), position)


def calculate_bearing(
    from_pos: tuple[float, float, float],
    to_pos: tuple[float, float, float],
    current_heading: float = 0.0,
    use_local_frame: bool = False,
) -> dict:
    """Calculate navigation bearing from current position to target.

    Star Citizen uses a right-handed coordinate system where X/Z define the
    horizontal plane and Y is the vertical axis. Heading is measured in degrees.
    Both positions must belong to the same body-fixed local frame (short-range
    surface navigation); mixing frames from different bodies is the caller's
    responsibility to prevent.

    Args:
        from_pos: Current (x, y, z) position in game units.
        to_pos: Target (x, y, z) position in game units.
        current_heading: Player's current heading in degrees (0 = north).
        use_local_frame: When True, the horizontal bearing is a TRUE compass
            bearing referenced to local north at from_pos, which is the only
            form comparable with a heading from camdir_to_bearing. Falls back to
            the flat-frame angle where no basis exists (body centre, pole, or a
            target directly overhead). Off by default so the long-standing
            flat-frame behaviour, and the callers and tests that rely on it,
            are unchanged.

    Returns:
        Dict with bearing data for the HUD.
    """
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - from_pos[1]
    dz = to_pos[2] - from_pos[2]

    distance = math.sqrt(dx**2 + dy**2 + dz**2)
    distance_km = distance / 1000.0

    # Horizontal bearing: atan2(X, Z) maps to compass degrees
    horizontal_bearing = math.degrees(math.atan2(dx, dz)) % 360
    if use_local_frame:
        try:
            horizontal_bearing = surface_bearing(from_pos, to_pos)
        except LocalFrameUnavailable:
            pass

    # Offset from current heading: negative = turn left, positive = turn right
    horizontal_offset = ((horizontal_bearing - current_heading + 180) % 360) - 180

    # Vertical (elevation) angle
    horiz_dist = math.sqrt(dx**2 + dz**2)
    if horiz_dist > 0:
        vertical_angle = math.degrees(math.atan2(dy, horiz_dist))
    else:
        vertical_angle = 90.0 if dy > 0 else -90.0

    return {
        "distance_m": round(distance, 1),
        "distance_km": round(distance_km, 3),
        "horizontal_bearing_deg": round(horizontal_bearing, 1),
        "horizontal_offset_deg": round(horizontal_offset, 1),
        "vertical_angle_deg": round(vertical_angle, 1),
        "direction_label": _bearing_to_label(horizontal_bearing),
        "turn_instruction": _turn_instruction(horizontal_offset),
        "elevation_instruction": _elevation_instruction(vertical_angle),
    }


def _bearing_to_label(bearing: float) -> str:
    """Convert bearing in degrees to a cardinal/intercardinal direction."""
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((bearing + 22.5) / 45) % 8
    return directions[idx]


def _turn_instruction(offset_deg: float) -> str:
    """Generate a turn instruction from heading offset."""
    if abs(offset_deg) <= 5:
        return "Ahead"
    side = "right" if offset_deg > 0 else "left"
    abs_offset = abs(offset_deg)
    if abs_offset > 150:
        return "Turn around"
    if abs_offset > 90:
        return f"Hard {side}"
    if abs_offset > 45:
        return f"Turn {side}"
    return f"Bear {side} {abs_offset:.0f}°"


def _elevation_instruction(angle_deg: float) -> str:
    """Generate an elevation instruction from vertical angle."""
    if abs(angle_deg) <= 5:
        return "Level"
    direction = "up" if angle_deg > 0 else "down"
    return f"Pitch {direction} {abs(angle_deg):.0f}°"


def format_distance(distance_km: float) -> str:
    """Format distance for display."""
    if distance_km >= 1_000_000:
        return f"{distance_km / 1_000_000:.2f} Gm"
    if distance_km >= 1_000:
        return f"{distance_km / 1_000:.1f} Mm"
    if distance_km >= 1:
        return f"{distance_km:.1f} km"
    return f"{distance_km * 1000:.0f} m"


def format_spoken_distance(distance_km: float) -> str:
    """Use display precision, but explicit decimal and unit words for speech."""
    number, symbol = format_distance(distance_km).split()
    unit = {"m": "meter", "km": "kilometer", "Mm": "megameter", "Gm": "gigameter"}[symbol]
    if float(number) != 1:
        unit += "s"
    if "." in number:
        whole, fraction = number.split(".")
        fraction = fraction.rstrip("0")
        number = whole + (" point " + " ".join(fraction) if fraction else "")
    return f"{number} {unit}"
