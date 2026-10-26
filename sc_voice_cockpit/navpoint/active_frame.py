"""Select active planetary frames by explicit Zone naming rules, never scale."""
from decimal import Decimal, InvalidOperation
import math
import re
from sc_voice_cockpit.navpoint.navpoint_location_names import normalize_rows, frame_info, display_name

from sc_voice_cockpit.navpoint.distance_precision import ZONE, PARTS, _bounds, PrecisionTarget, parse_with_precision

HEADING=re.compile(r'^Zone:\s*(\S+)\s+Pos:')
PLANET=re.compile(r'^Planet:\s*([A-Za-z0-9_-]+)(?:\s|$)')

def zone_signature(texts):
    texts=normalize_rows(texts)
    names=[]
    for raw in texts:
        text=' '.join(raw.split())
        match=HEADING.match(text)
        if match:
            names.append(match[1])
        elif 'Zone:' in text or not text.startswith(('CamDir:','- CamDir:')):
            return None
    if not names or names[-1]!='Root' or names.count('Root')!=1:return None
    solars=[i for i,n in enumerate(names) if re.fullmatch(r'SolarSystem_[0-9]+',n)]
    if solars!=[len(names)-2]:return None
    if any('?' in n for n in names):return None
    return tuple(names)

def _planet_identity(token,zone_name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',token):raise ValueError('Invalid planet identity')
    parts=token.split('_')
    body=parts[-1] if token.startswith('OOC_') and len(parts)>=4 else token
    system=parts[1] if token.startswith('OOC_') and len(parts)>=4 else ''
    if not system:
        for candidate in ('Stanton','Pyro','Nyx'):
            if token.casefold().startswith(candidate.casefold()):system=candidate;break
    return dict(kind='planet',zone_name=zone_name,planet_id=token.casefold(),body=body,system=system,planet_label=token)

def resolve_zone_identity(texts):
    """Current policy: fresh upper Zone names only; lower status is irrelevant.

    Recognized planetary names may move down when another local container is
    present. Unknown names do not become planets merely by occupying row three.
    """
    signature=zone_signature(texts)
    if signature is None:return None,'unreadable_frame_stack'
    active=signature[:-2]
    sites=[n for n in active if frame_info(n) and frame_info(n)['kind']=='site']
    if len(sites)>1:return None,'ambiguous_site_zone'
    if sites:return _site_identity(sites[0]),''
    planets=[n for n in active if re.fullmatch(r'pyro[1-6](?:[a-f])?',n,re.IGNORECASE) and frame_info(n)
             or re.fullmatch(r'OOC_Stanton_[1-4][a-z]?_[A-Za-z][A-Za-z0-9_]*',n)]
    if len(planets)==1:return _planet_identity(planets[0],planets[0]),''
    if planets:return None,'ambiguous_planet_zone'
    def known_container(name):
        return (re.fullmatch(r'(?:RSI|DRAK|AEGS|ANVL|MISC|ARGO|CRUS|ORIG|CNOU|TMBL|ESPR)_[A-Za-z0-9_]+_[0-9]+',name)
                or re.fullmatch(r'JumpTunnelHost_[0-9]+',name)
                or re.fullmatch(r'(?:keeger_segment|glaciemring_segment(?:_[A-Za-z0-9_-]+)?)',name))
    if all(known_container(n) for n in active):return dict(kind='space'),''
    return None,'unknown_zone_name'

def _site_identity(name):
    info=frame_info(name)
    if not info or info['kind']!='site':raise ValueError('Unknown site frame')
    return dict(kind='site',zone_name=name,planet_id=name.casefold(),body=name.casefold(),
                system=info['system'],planet_label=name)

def resolve_identity(texts,metadata):
    """Legacy lower-label resolver for offline comparison, not used by capture."""
    signature=zone_signature(texts)
    if signature is None:return None,'unreadable_frame_stack'
    lines=[' '.join(t.split()) for t in metadata]
    if any('INVALID LOCATION' in t.upper() for t in lines):return None,'no_game_world'
    planets=[m[1] for t in lines if (m:=PLANET.match(t))]
    no_planet=any(t=='No Current Planet' for t in lines)
    if len(planets)>1:return None,'ambiguous_planet_label'
    active=signature[:-2]
    if planets:
        matches=[n for n in active if n.casefold()==planets[0].casefold()]
        if len(matches)==1 and not no_planet:
            return _planet_identity(planets[0],matches[0]),''
        if matches:return None,'contradictory_planet_evidence'
    locations=[t.split(':',1)[1].strip() for t in lines if re.match(r'^Current player location\s*:',t)]
    explicit_space=any(re.fullmatch(r'[A-Za-z0-9_]*(?:SolarSystem|Star)',t)
                       or t.startswith('AsteroidClusterBase ') for t in locations)
    if no_planet or (planets and explicit_space):return dict(kind='space'),''
    return None,'planet_identity_unresolved'

def target_snapshot(target):
    if target is None:return None
    def get(key,default=None):
        return target.get(key,default) if isinstance(target,dict) else getattr(target,key,default)
    return {k:get(k) for k in ('id','frame','frame_id','body','system','x','y','z','coordinate_stack')}

def same_planet(position,target):
    if not position or not target or (target.get('frame') or 'local')!='local':return False
    body=position.get('body') or ''; other=target.get('body') or ''
    if not body or body.casefold()!=other.casefold():return False
    a=position.get('frame_id') or position.get('planet_id');b=target.get('frame_id')
    if a and b and a.casefold()!=b.casefold():return False
    a=position.get('system');b=target.get('system')
    return not(a and b and a.casefold()!=b.casefold())

def _target_xyz(target):
    values=tuple(target.get(k) for k in ('x','y','z'))
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in values):
        raise ValueError('Invalid target coordinates')
    return values

def _coordinates(tokens,target=None):
    boxes=[_bounds(t) for t in tokens]
    if not all(math.isfinite(float(v)) for box in boxes for v in box):
        raise ValueError('Nonfinite coordinate bounds')
    precision=dict(mode='full',actual_accuracy_verified=False)
    if target is not None:
        lower=math.sqrt(sum(max(float(a)-v,0.,v-float(b))**2 for (a,b),v in zip(boxes,target)))
        near=lower<15000
        precision.update(mode='approach' if near else 'cruise',distance_lower_bound_m=lower,
                         required_position_error_m=10 if near else None,slow_down_advisory=near,
                         uncertainty_scope='omitted digits only; excludes OCR error, saved target error and motion')
        if not near:
            xyz=[float((a+b)/2) for a,b in boxes]
            precision.update(position_bounds_m=[[float(a),float(b)] for a,b in boxes],
                             quantization_radius_m=math.sqrt(sum(float((b-a)/2)**2 for a,b in boxes)))
            return xyz,precision
    xyz=[]
    for token in tokens:
        p=PARTS.fullmatch(token)
        number=token[:-len(p['unit'])]
        xyz.append(float(Decimal(number)*(1000 if p['unit']=='km' else 1)))
    if not all(math.isfinite(v) for v in xyz):raise ValueError('Nonfinite position')
    return xyz,precision

def build_payload(texts,identity,target=None,full_precision=False):
    from sc_voice_cockpit.navpoint.learned_fields import camera_values
    texts=normalize_rows(texts)
    if identity is None:return None,'planet_identity_unresolved'
    signature=zone_signature(texts)
    if signature is None:return None,'unreadable_frame_stack'
    target=target_snapshot(target)
    if identity.get('kind')=='space':
        context=None
        if not full_precision and target and target.get('frame') in ('stack','system'):
            from sc_voice_cockpit.navpoint import coordinate_stack as cs
            stack=cs.from_json(target.get('coordinate_stack')) if target.get('frame')=='stack' else None
            xyz=stack.root.xyz if stack else _target_xyz(target)
            solar=stack.solar.raw_name if stack else 'SolarSystem_'+str(target.get('frame_id',''))
            context=PrecisionTarget(xyz,solar)
        result=parse_with_precision(texts,context)
        if not result['complete']:return None,result['reason']
        pos=result['position'];precision=result['precision']
        precision['target_id']=target.get('id') if context else None
        return dict(local_frame=False,coordinate_stack=pos['coordinate_stack'],camdir=pos['camdir'],
                    active_frame='space',precision=precision),''
    if identity.get('kind') not in ('planet','site'):return None,'invalid_frame_identity'
    name=identity['zone_name']
    if signature[:-2].count(name)!=1:return None,'planet_zone_not_active'
    rows=[ZONE.fullmatch(' '.join(t.split())) for t in texts]
    selected=[r for r in rows if r is not None and r['name']==name]
    cameras=[v for t in texts if (v:=camera_values(t)) is not None]
    if len(selected)!=1:return None,'planet_coordinates_unreadable'
    if len(cameras)!=1:return None,'missing_or_ambiguous_facing'
    tokens=[selected[0][k] for k in ('x','y','z')]
    matching=not full_precision and same_planet(identity,target)
    context=target if matching else None
    try:
        xyz,precision=_coordinates(tokens,_target_xyz(context) if context else None)
    except (ValueError,TypeError,InvalidOperation,OverflowError):return None,'planet_coordinates_unreadable'
    precision['target_id']=context.get('id') if context else None
    return dict(active_frame_schema=1,active_frame=identity['kind'],planet_identity=identity,
                pos_raw=tokens,camdir=cameras[0],precision=precision,precision_target=context),''

def validate_planet_payload(data):
    """Recompute selected coordinates from explicit units, never Solar/Root."""
    if data.get('active_frame_schema')!=1 or data.get('active_frame') not in ('planet','site'):raise ValueError('Unknown active frame schema')
    if any(k in data for k in ('coordinate_stack','root_pos_raw','system_pos_raw')):raise ValueError('Mixed planetary and solar payload')
    identity=data['planet_identity']
    expected=(_site_identity(identity['zone_name']) if data['active_frame']=='site'
              else _planet_identity(identity['planet_label'],identity['zone_name']))
    if identity!=expected or identity['planet_label'].casefold()!=identity['zone_name'].casefold():raise ValueError('Invalid planet identity')
    tokens=data['pos_raw']
    if not isinstance(tokens,list) or len(tokens)!=3 or not all(isinstance(t,str) and PARTS.fullmatch(t) for t in tokens):raise ValueError('Invalid coordinate tokens')
    target=data.get('precision_target')
    if target is not None and not same_planet(identity,target):raise ValueError('Target frame mismatch')
    xyz,precision=_coordinates(tokens,_target_xyz(target) if target else None)
    precision['target_id']=target.get('id') if target else None
    if precision!=data.get('precision'):raise ValueError('Inconsistent precision metadata')
    camera=data['camdir']
    if not isinstance(camera,(list,tuple)) or len(camera)!=3 or any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or abs(v)>360 for v in camera):raise ValueError('Invalid camera')
    result=dict(local_frame=True,active_frame=data['active_frame'],body=identity['body'],system=identity['system'],
                frame_id=identity['planet_id'],planet_id=identity['planet_id'],zone='',location=identity['body'],server_id='',
                x=xyz[0],y=xyz[1],z=xyz[2],camdir=list(camera),precision=precision,
                display_location=display_name(identity['planet_id'],identity['body']))
    rotation=data.get('planet_rotation')
    if data['active_frame']=='planet' and isinstance(rotation,dict):
        angle=rotation.get('angle')
        if (rotation.get('valid') is True and rotation.get('frame_id')==identity['planet_id']
                and isinstance(angle,(int,float)) and not isinstance(angle,bool) and math.isfinite(angle)):
            result['planet_rotation']=dict(frame_id=identity['planet_id'],angle=angle,valid=True)
    if data['active_frame']=='site':
        from sc_voice_cockpit.navpoint.site_rotation import validated_rotation
        rotation=validated_rotation(data.get('site_rotation'),identity['planet_id'])
        if rotation is not None:result['site_rotation']=rotation
    return result
