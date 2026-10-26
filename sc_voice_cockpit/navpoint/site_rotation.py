"""Transient rigid site-to-solar calibration, measured from paired positions.

Sites can be tilted in all three axes. Never infer their camera orientation
from a name or the pilot's aim, and never persist SolarSystem coordinates.
"""
import math
import numpy as np

from sc_voice_cockpit.navpoint.planet_rotation import CALIBRATION_ERROR_LIMIT_M, local_camera_basis


def fit_rotation(samples):
    """Fit a proper 3D rotation; reserve the final fifth for prediction checks."""
    a = np.asarray(samples, dtype=float)
    if (a.ndim != 2 or a.shape[1] != 7 or len(a) < 12
            or not np.isfinite(a).all() or np.any(np.diff(a[:,0]) <= 0)):
        return None
    n = int(len(a)*.8)
    local, world = a[:,1:4], a[:,4:7]
    lc, wc = local[:n].mean(axis=0), world[:n].mean(axis=0)
    centered = local[:n]-lc
    try:
        spread = np.linalg.svd(centered, compute_uv=False)
        # Straight-line motion cannot establish rotation about that line.
        if spread[1] < 10:
            return None
        u, _, vt = np.linalg.svd(centered.T @ (world[:n]-wc))
        rotation = vt.T @ np.diag([1.,1.,np.linalg.det(vt.T @ u.T)]) @ u.T
        errors = np.linalg.norm((local-lc) @ rotation.T + wc - world, axis=1)
        if np.max(errors) > CALIBRATION_ERROR_LIMIT_M:
            return None
        # Weakest rotation axis is constrained by the other two spatial axes.
        # A one-metre floor prevents quantization noise from implying certainty.
        noise = max(1.,float(np.sqrt(np.mean(errors[:n]**2))))
        uncertainty = noise / math.hypot(spread[1],spread[2])
        if uncertainty > math.radians(1):
            return None
    except (ValueError, ArithmeticError, np.linalg.LinAlgError):
        return None
    return dict(matrix=rotation.tolist(), local_origin=lc.tolist(), solar_origin=wc.tolist(),
                checked_at=float(a[-1,0]), residual_m=float(np.max(errors[:n])),
                check_error_m=float(np.max(errors[n:])), uncertainty=uncertainty)


def validated_rotation(value, frame_id):
    """Reject malformed, reflected or mismatched runtime calibration metadata."""
    if (not isinstance(value,dict) or value.get('valid') is not True
            or value.get('frame_id') != frame_id):
        return None
    try:
        matrix = np.asarray(value.get('matrix'),dtype=float)
        if (matrix.shape != (3,3) or not np.isfinite(matrix).all()
                or not np.allclose(matrix.T @ matrix,np.eye(3),atol=1e-7,rtol=0)
                or not math.isclose(float(np.linalg.det(matrix)),1.,abs_tol=1e-7)):
            return None
    except (ValueError,TypeError,OverflowError,np.linalg.LinAlgError):
        return None
    return dict(frame_id=frame_id,matrix=matrix.tolist(),valid=True)


def site_camera_basis(camdir, rotation):
    # The fit maps local -> solar; its transpose maps camera axes back to local.
    inverse = np.asarray(rotation['matrix'],dtype=float).T
    return tuple(inverse @ axis for axis in local_camera_basis(camdir,0.))


class SiteRotationTracker:
    """Bounded fit using existing captures; no extra OCR or persisted session data."""
    def __init__(self):
        self.reset()

    def reset(self,key=None):
        self.key = key
        self.samples = []
        self.model = None
        self.last_seen = None
        self.last_fit = -math.inf

    def observe(self,key,timestamp,local,solar):
        values = np.asarray([timestamp,*local,*solar],dtype=float)
        if values.shape != (7,) or not np.isfinite(values).all():
            return None
        if (key != self.key or self.last_seen is not None
                and (timestamp < self.last_seen or timestamp-self.last_seen > 30)):
            self.reset(key)
        if self.last_seen is not None and timestamp <= self.last_seen:
            return None
        self.last_seen = timestamp
        # Stationary repeats add no geometric information. Keep the motion that
        # established orientation, while validating every fresh pair below.
        added = (not self.samples or timestamp-self.samples[-1][0] >= .5
                 and math.dist(local,self.samples[-1][1:4]) >= 1.)
        if added:
            self.samples.append(tuple(values))
            self.samples = self.samples[-180:]
            if len(self.samples) >= 12 and timestamp-self.last_fit >= 1:
                self.last_fit = timestamp
                model = fit_rotation(self.samples)
                if model is not None:
                    self.model = model
        model = self.model
        if model is None:
            return None
        predicted = (np.asarray(model['solar_origin']) + np.asarray(model['matrix'])
                     @ (np.asarray(local)-model['local_origin']))
        if np.linalg.norm(predicted-np.asarray(solar)) > CALIBRATION_ERROR_LIMIT_M:
            return None
        return dict(frame_id=key[0],matrix=model['matrix'],valid=True)


def pair_from_rows(texts,identity):
    """Full-precision pair; bad solar rows block direction, not local distance."""
    from sc_voice_cockpit.navpoint.distance_precision import ZONE
    from sc_voice_cockpit.navpoint.active_frame import _coordinates
    rows = [ZONE.fullmatch(' '.join(t.split())) for t in texts]
    rows = [r for r in rows if r]
    local = [r for r in rows if r['name']==identity['zone_name']]
    solar = [r for r in rows if r['name'].startswith('SolarSystem_')]
    roots = [r for r in rows if r['name']=='Root']
    if len(local)!=1 or len(solar)!=1 or len(roots)!=1:
        return None
    try:
        xyz = [_coordinates([r[k] for k in ('x','y','z')])[0] for r in (local[0],solar[0],roots[0])]
    except (ValueError,ArithmeticError):
        return None
    if math.dist(xyz[1],xyz[2]) > .2:
        return None
    return (identity['planet_id'],solar[0]['name']),xyz[0],xyz[1]
