"""V8 OCR with identity from fresh upper Zone names; no lower-panel gate."""
import hashlib
import json
import sys
import time
from pathlib import Path
import numpy as np

from sc_voice_cockpit.navpoint import active_frame
from sc_voice_cockpit.navpoint.specialist_reader import SpecialistReader, prepare_band, decode_ascii, ALPHABET_SHA256

MODEL_SHA256='0c775c270fda57437b34af99a1e0e8609a4e86dcbd3265201fde061b5aac0a39'

class FrameAwareReader(SpecialistReader):
    capture_height = 175
    def __init__(self,model_dir=None):
        vendor = Path(__file__).parent / '_vendor'
        if vendor.is_dir() and str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        import onnxruntime as ort
        folder=Path(model_dir) if model_dir else Path(__file__).parent/'models/specialist_v8'
        model=folder/'model.onnx';alphabet=folder/'characters.json'
        if hashlib.sha256(model.read_bytes()).hexdigest()!=MODEL_SHA256:raise ValueError('V8 model hash mismatch')
        if hashlib.sha256(alphabet.read_bytes()).hexdigest()!=ALPHABET_SHA256:raise ValueError('V8 alphabet hash mismatch')
        self.characters=json.loads(alphabet.read_text(encoding='utf-8'))
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(model),options,providers=['CPUExecutionProvider'])
        self.input_name=self.session.get_inputs()[0].name
        from sc_voice_cockpit.navpoint.planet_rotation import PlanetRotationTracker
        self.planet_rotation_tracker=PlanetRotationTracker()
        from sc_voice_cockpit.navpoint.site_rotation import SiteRotationTracker
        self.site_rotation_tracker=SiteRotationTracker()

    @staticmethod
    def capture_is_usable(frame):
        valid=frame.ndim==3 and frame.shape[2]==3 and frame.shape[1]==675 and frame.shape[0]>=175
        return valid,'' if valid else 'specialist_geometry'

    def _recognize_band(self,frame,index):
        top=6+13*index
        bgr=np.asarray(frame[top:top+13],dtype=np.uint8)[:,:,::-1]
        prediction=self.session.run(None,{self.input_name:prepare_band(bgr)})[0]
        return decode_ascii(prediction,self.characters)[0]

    def read(self,frame,count=8):
        captured_at=time.time()
        if not self.capture_is_usable(frame)[0]:raise ValueError('Unsupported capture geometry')
        primary=[];rows=[]
        limit=min(8,(frame.shape[0]-6)//13)
        for index in range(min(count,8,limit)):
            text=self._recognize_band(frame,index);primary.append(text)
            rows.append(dict(row=index,text=text,raw_model_text=text,trusted='?' not in text,
                             refused=text.count('?'),kind='zone' if text.startswith('Zone:') else 'camdir'))
            heading=active_frame.HEADING.match(' '.join(text.split()))
            if heading and heading[1]=='Root':break
        identity,reason=active_frame.resolve_zone_identity(primary)
        if rows:
            rows[0]['frame_identity']=identity
            rows[0]['frame_identity_reason']=reason
            rows[0]['identity_from_cache']=False
            rows[0]['captured_at']=captured_at
        return 6,0.0,rows

    def capture_for_target(self,rows,target=None,full_precision=False):
        identity=rows[0].get('frame_identity') if rows else None
        if identity is None:
            return None,(rows[0].get('frame_identity_reason') or 'planet_identity_unresolved') if rows else 'no_rows'
        primary=[r['raw_model_text'] for r in rows if not r.get('metadata')]
        payload,reason=active_frame.build_payload(primary,identity,target,full_precision)
        tracker=getattr(self,'planet_rotation_tracker',None)
        if tracker is not None:
            if identity.get('kind')!='planet':
                tracker.reset()
            elif payload is not None:
                from sc_voice_cockpit.navpoint.planet_rotation import pair_from_rows
                from sc_voice_cockpit.navpoint.navpoint_location_names import normalize_rows
                pair=pair_from_rows(normalize_rows(primary),identity)
                if pair is not None:
                    key,local,solar=pair
                    rotation=tracker.observe(key,rows[0].get('captured_at',time.time()),local,solar)
                    if rotation is not None:payload['planet_rotation']=rotation
        tracker=getattr(self,'site_rotation_tracker',None)
        if tracker is not None:
            if identity.get('kind')!='site':
                tracker.reset()
            elif payload is not None:
                from sc_voice_cockpit.navpoint.site_rotation import pair_from_rows
                from sc_voice_cockpit.navpoint.navpoint_location_names import normalize_rows
                pair=pair_from_rows(normalize_rows(primary),identity)
                if pair is not None:
                    key,local,solar=pair
                    rotation=tracker.observe(key,rows[0].get('captured_at',time.time()),local,solar)
                    if rotation is not None:payload['site_rotation']=rotation
        return payload,reason

    def capture_payload(self,rows):
        return self.capture_for_target(rows)
