"""Experimental distance-sensitive precision. No capture, inference or history."""
from dataclasses import dataclass
from decimal import Decimal
import math
import re

APPROACH_DISTANCE_M=15000.0
APPROACH_ERROR_M=10.0
TOKEN=r'[+-]?[0-9]+(?:\.[0-9?]+)?(?:km|m)'
ZONE=re.compile(r'^Zone:\s*(?P<name>\S+?)\s+Pos:\s*'
                +rf'(?P<x>{TOKEN})\s*(?P<y>{TOKEN})\s*(?P<z>{TOKEN})$')
PARTS=re.compile(r'(?P<sign>[+-]?)(?P<whole>[0-9]+)(?:\.(?P<fraction>[0-9?]+))?(?P<unit>km|m)')

def _vector(values):
    if (not isinstance(values,(list,tuple)) or len(values)!=3
            or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values)):
        raise ValueError('Expected three finite coordinates in metres')
    return tuple(float(v) for v in values)

@dataclass(frozen=True)
class PrecisionTarget:
    root_m: tuple
    solar_name: str

    def __post_init__(self):
        object.__setattr__(self,'root_m',_vector(self.root_m))
        if not isinstance(self.solar_name,str) or not re.fullmatch(r'SolarSystem_[0-9]+',self.solar_name):
            raise ValueError('Target must identify its SolarSystem frame')

def _bounds(token):
    """Closed bound of the observed signed integer prefix, in metres.

    Fraction digits never affect this interval, including negative zero.
    Bounds reflect omitted precision only, not OCR confidence or game motion.
    """
    p=PARTS.fullmatch(token)
    if p is None:raise ValueError('Malformed coordinate')
    scale=Decimal(1000 if p['unit']=='km' else 1)
    whole=Decimal(p['whole'])*scale
    width=scale if p['fraction'] is not None else Decimal(0)
    return (-whole-width,-whole) if p['sign']=='-' else (whole,whole+width)

def _coarse(text):
    match=ZONE.fullmatch(' '.join(text.split()))
    if match is None:return text,None
    bounds=[_bounds(match[k]) for k in ('x','y','z')]
    values=[format((a+b)/2,'f')+'m' for a,b in bounds]
    return f"Zone: {match['name']} Pos: "+' '.join(values),(match['name'],bounds)

def _refused(reason,mode='unavailable'):
    return dict(status='refused',position=None,complete=False,reason=reason,rows=[],
                precision=dict(mode=mode,actual_accuracy_verified=False))

def _parse_approach(texts, same_prefix_cell):
    """Relax only Root/Solar fractional agreement; retain both observed values."""
    from sc_voice_cockpit.navpoint.learned_fields import parse_proposal, camera_values
    from sc_voice_cockpit.navpoint import coordinate_stack as cs
    from sc_voice_cockpit.navpoint.overlay_fields import stack_from_rows
    from sc_voice_cockpit.navpoint.scanner import NavPointScanner
    result=parse_proposal(texts)
    if result['reason']!='exact_root_solar_disagreement' or not same_prefix_cell:
        return result
    stack,reason=stack_from_rows(result['rows'])
    if stack is None or not within_approach_error(stack.root.xyz,stack.solar.xyz):
        return result
    cameras=[camera_values(t) for t in texts]
    cameras=[c for c in cameras if c is not None]
    if len(cameras)!=1:
        result['reason']='missing_or_ambiguous_facing'
        return result
    scanner=NavPointScanner()
    position=scanner.parse_payload(dict(local_frame=False,coordinate_stack=cs.to_dict(stack)))
    if position is None:
        result['reason']=scanner.last_reject_reason or 'scanner_refused'
        return result
    position['camdir']=cameras[0]
    result.update(status='unverified_proposal',position=position,complete=True,reason='')
    return result

def parse_with_precision(texts, target=None):
    """One frame, optional full-precision target. Never retry or fill fractions.

    Cruise uses cell centres only as representatives; explicit bounds accompany
    the payload. Approach retains full observed precision and strict structure,
    allowing at most 10 m between Root/Solar observed vectors in the same cell.
    A 10 m requirement is an evaluation contract, not guaranteed by row agreement.
    """
    from sc_voice_cockpit.navpoint.learned_fields import parse_proposal
    precision=dict(mode='full',actual_accuracy_verified=False)
    if target is None:
        result=parse_proposal(texts)
    else:
        if not isinstance(target,PrecisionTarget):raise ValueError('Invalid target')
        coarse=[];anchors={}
        for text in texts:
            normalized,entry=_coarse(text)
            coarse.append(normalized)
            if entry is not None:anchors.setdefault(entry[0],[]).append(entry[1])
        roots=anchors.get('Root',[])
        solars=[(name,bounds) for name,values in anchors.items()
                if re.fullmatch(r'SolarSystem_[0-9]+',name) for bounds in values]
        if len(roots)!=1 or len(solars)!=1:
            return _refused('precision_missing_or_duplicate_anchor')
        if solars[0][0]!=target.solar_name:
            return _refused('precision_target_system_mismatch')
        box=roots[0]
        # Using the minimum possible distance prevents a late precision switch.
        lower=math.sqrt(sum(max(float(a)-v,0.,v-float(b))**2 for (a,b),v in zip(box,target.root_m)))
        upper=math.sqrt(sum(max(abs(float(a)-v),abs(float(b)-v))**2 for (a,b),v in zip(box,target.root_m)))
        near=lower<APPROACH_DISTANCE_M
        precision.update(mode='approach' if near else 'cruise',
                         distance_lower_bound_m=lower,distance_upper_bound_m=upper,
                         required_position_error_m=APPROACH_ERROR_M if near else None,
                         slow_down_advisory=near,
                         uncertainty_scope='omitted decimal precision only; excludes OCR error, target error and motion')
        if near:
            result=_parse_approach(texts,box==solars[0][1])
        else:
            result=parse_proposal(coarse)
            precision.update(root_bounds_m=[[float(a),float(b)] for a,b in box],
                             root_quantization_radius_m=math.sqrt(sum(float((b-a)/2)**2 for a,b in box)),
                             representative='midpoint of observed integer-prefix cell; discarded fractions remain unknown')
    result['precision']=precision
    if result.get('position') is not None:
        result['position']['precision']=precision
    # Preserve source text separately from the explicitly quantized representation.
    for row,raw in zip(result['rows'],texts):row['raw_model_text']=raw
    return result

def within_approach_error(actual, expected):
    """Compare a read with independent truth: 10 m total Euclidean error."""
    return math.dist(_vector(actual),_vector(expected))<=APPROACH_ERROR_M
