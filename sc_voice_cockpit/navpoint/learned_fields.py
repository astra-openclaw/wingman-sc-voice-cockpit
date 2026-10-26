"""Strict text adapter for the optional specialist reader candidate.

There is deliberately no fixed-glyph/background-fit confidence test here.
Structural validity remains separate from measured recognition correctness.
"""
from decimal import Decimal, InvalidOperation
import math
import re

from sc_voice_cockpit.navpoint import coordinate_stack as cs
from sc_voice_cockpit.navpoint import overlay_fields as fields
from sc_voice_cockpit.navpoint.scanner import NavPointScanner

NUMBER=r'[+-]?\d+(?:\.\d+)?'
TOKEN=NUMBER+r'(?:km|m)'
ZONE=re.compile(r'^Zone:\s*(?P<name>\S+?)\s+Pos:\s*'
                +rf'(?P<x>{TOKEN})\s*(?P<y>{TOKEN})\s*(?P<z>{TOKEN})$')
CAMERA=re.compile(rf'^CamDir:\s*({NUMBER})(?:\s+|(?=[+-]))({NUMBER})'
                  +rf'(?:\s+|(?=[+-]))({NUMBER})(?=\s+[A-Za-z]|\s*$)')


def spatial_text(raw, words):
    """Retain the recognizer's spatial groups only if all glyphs are identical."""
    text=' '.join(''.join(word) for word in words)
    return text if ''.join(text.split())==''.join(raw.split()) else None


def camera_values(text):
    """Three explicit numeric fields; a sign can delimit adjacent numbers."""
    text=' '.join(text.split())
    anchors=list(re.finditer(r'(?<!\S)CamDir:',text))
    if len(anchors)!=1:
        return None
    # Localize the literal field anchor, retaining every following character.
    # Background before the anchor cannot become a sign inside its numbers.
    match=CAMERA.match(text[anchors[0].start():])
    if match:
        values=[float(match.group(i)) for i in (1,2,3)]
        if all(math.isfinite(v) and abs(v)<=360 for v in values):
            return values
    return None


def normalize_row(text: str) -> str:
    """Normalize only whitespace around complete, explicitly unit-ended tokens."""
    text=' '.join(text.split())
    match=ZONE.fullmatch(text)
    if match:
        return f"Zone: {match['name']} Pos: {match['x']} {match['y']} {match['z']}"
    return text


def _exact_metres(match):
    return tuple(Decimal(match[k][:-2])*1000 if match[k].endswith('km')
                 else Decimal(match[k][:-1]) for k in ('x','y','z'))


def parse_proposal(texts: list[str]) -> dict:
    """Parse one frame's strings, never infer unread characters or frame history.

    A complete proposal is still unverified OCR: agreement can miss correlated
    recognition errors. Callers must score it against independent labels.
    """
    result={'status':'refused','position':None,'complete':False,'reason':'','rows':[]}
    rows=[]; anchors={}; cameras=[]; malformed_coordinate=False
    for index, raw in enumerate(texts):
        text=normalize_row(raw)
        values=camera_values(text)
        trusted='?' not in text
        if values is not None:
            cameras.append(values)
            # FOV/focal length/exposure are not part of this measurement.
            text='CamDir: '+' '.join(map(str,values))
            trusted=True
        elif text.startswith('CamDir:'):
            trusted=False
        match=ZONE.fullmatch(text)
        # A damaged local-container row must not silently vanish while the
        # global Root/SolarSystem pair remains syntactically valid.
        if values is None and match is None:
            malformed_coordinate=True
            trusted=False
        if match and (match['name']=='Root' or match['name'].startswith('SolarSystem_')):
            anchors.setdefault(match['name'],[]).append(match)
        rows.append({'row':index,'text':text,'kind':'zone' if text.startswith('Zone:')
                     else 'camdir' if text.startswith('CamDir:') else None,
                     'trusted':trusted,'refused':text.count('?'),'raw_model_text':raw})
    result['rows']=rows
    if malformed_coordinate:
        result['reason']='malformed_coordinate_row'
        return result
    try:
        stack, reason=fields.stack_from_rows(rows)
        if stack is None:
            result['reason']=reason
            return result
        roots=anchors.get('Root',[])
        solars=[m for name, matches in anchors.items() if re.fullmatch(r'SolarSystem_\d+',name)
                for m in matches]
        if len(roots)!=1 or len(solars)!=1:
            result['reason']='missing_or_duplicate_numeric_anchor'
            return result
        if _exact_metres(roots[0])!=_exact_metres(solars[0]):
            result['reason']='exact_root_solar_disagreement'
            return result
        payload={'local_frame':False,'coordinate_stack':cs.to_dict(stack)}
        if len(cameras)==1:
            payload['camdir']=cameras[0]
        scanner=NavPointScanner()
        position=scanner.parse_payload(payload)
        if position is None:
            result['reason']=scanner.last_reject_reason or 'scanner_refused'
            return result
        if len(cameras)==1:
            # The legacy scanner's stack-only return omits CamDir. These values
            # were parsed from this frame above; no frame identity is inferred.
            position['camdir']=cameras[0]
    except (ValueError,TypeError,OverflowError,InvalidOperation) as exc:
        result['reason']='invalid_coordinate: '+type(exc).__name__
        return result
    result.update(status='unverified_proposal',position=position,
                  complete=len(cameras)==1,reason='' if len(cameras)==1 else 'missing_or_ambiguous_facing')
    return result
