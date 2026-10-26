"""Ephemeral planetary camera calibration. Never changes saved coordinates.

Fit paired local/solar XY positions to a translating origin and constant Z spin.
Only a well-conditioned, independently checked short-term fit may drive the dot.
This model is not applied to space or arbitrary mining-site containers.
"""
import math
import queue
import threading
import numpy as np

# User-selected tolerance for calibration position residuals, in metres.
CALIBRATION_ERROR_LIMIT_M = 50.0


def local_camera_basis(camdir, angle):
    """Rz(yaw) Ry(roll) Rx(pitch), then solar-to-planet rotation."""
    pitch, roll, yaw = map(math.radians, camdir)
    sx,cx=math.sin(pitch),math.cos(pitch)
    sy,cy=math.sin(roll),math.cos(roll)
    sz,cz=math.sin(yaw),math.cos(yaw)
    right=(cz*cy,sz*cy,-sy)
    forward=(cz*sy*sx-sz*cx,sz*sy*sx+cz*cx,cy*sx)
    up=(cz*sy*cx+sz*sx,sz*sy*cx-cz*sx,cy*cx)
    c,s=math.cos(angle),math.sin(angle)
    def local(v):return (c*v[0]+s*v[1],-s*v[0]+c*v[1],v[2])
    return local(forward),local(right),local(up)


def fit_rotation(samples):
    """Bounded robust fit, using the final fifth as a prediction check."""
    a=np.asarray(samples,dtype=float)
    if a.ndim!=2 or a.shape[1]!=5 or len(a)<20 or not np.isfinite(a).all():return None
    if np.any(np.diff(a[:,0])<=0) or a[-1,0]-a[0,0]<30:return None
    epoch=float(a[0,0]);span=float(a[-1,0]-epoch)
    t=(a[:,0]-epoch)/span;local=a[:,1:3];origin=a[0,3:5];world=a[:,3:5]-origin
    n=int(len(a)*.8)
    def evaluate(p):
        angle=p[2]+p[3]*t;c=np.cos(angle);s=np.sin(angle)
        x=c*local[:,0]-s*local[:,1];y=s*local[:,0]+c*local[:,1]
        residual=np.column_stack((p[0]+x,p[1]+y))-world
        jac=np.zeros((len(a),2,4));jac[:,0,0]=1;jac[:,1,1]=1
        jac[:,0,2]=-y;jac[:,1,2]=x
        jac[:,:,3]=jac[:,:,2]*t[:,None]
        return residual,jac
    best=None
    for theta in np.linspace(-math.pi,math.pi,8,endpoint=False):
        c,s=math.cos(theta),math.sin(theta)
        p=np.array([-c*local[0,0]+s*local[0,1],-s*local[0,0]-c*local[0,1],theta,0.])
        for _ in range(45):
            residual,jac=evaluate(p)
            weights=np.minimum(1.,2./np.maximum(np.linalg.norm(residual[:n],axis=1),1e-9))
            j=jac[:n].reshape(-1,4);r=residual[:n].ravel();w=np.repeat(np.sqrt(weights),2)
            step=np.linalg.lstsq(j*w[:,None],-r*w,rcond=None)[0]
            # Keep a poor starting angle from taking an unbounded spin step.
            factor=min(1.,.5/max(abs(step[2]),abs(step[3]),1e-9))
            p+=step*factor
            if np.linalg.norm(step*factor)<1e-7:break
        residual,jac=evaluate(p);errors=np.linalg.norm(residual,axis=1)
        score=float(np.median(errors[:n]))
        if best is None or score<best[0]:best=(score,p,errors,jac)
    score,p,errors,jac=best
    inliers=errors[:n]<=CALIBRATION_ERROR_LIMIT_M
    if (score>CALIBRATION_ERROR_LIMIT_M or np.mean(inliers)<.9
            or np.percentile(errors[n:],90)>CALIBRATION_ERROR_LIMIT_M):return None
    if abs(p[3]/span)>.01:return None
    j=jac[:n][inliers].reshape(-1,4)
    # Angle must be observable independently of the fitted center.
    try:
        _,singular,v=np.linalg.svd(j,full_matrices=False)
        if singular[-1]<1e-7:return None
        covariance=(v.T/(singular**2))@v * max(1.,score)**2
        projection=np.array([0.,0.,1.,1.])
        uncertainty=math.sqrt(max(0.,float(projection@covariance@projection)))
    except (ValueError,np.linalg.LinAlgError):return None
    if uncertainty>math.radians(1):return None
    return dict(epoch=epoch,angle=float(p[2]),rate=float(p[3]/span),
                center=(origin+p[:2]).tolist(),checked_at=float(a[-1,0]),
                uncertainty=uncertainty,residual_m=score)


class PlanetRotationTracker:
    """One background fit at a time; no OCR retries, queues, or persisted fit."""
    def __init__(self):
        self.key=None;self.samples=[];self.model=None;self.last_fit=-math.inf
        self.worker=None;self.results=queue.SimpleQueue();self.generation=0

    def reset(self,key=None):
        self.key=key;self.samples=[];self.model=None;self.generation+=1;self.last_fit=-math.inf

    def observe(self,key,timestamp,local,solar):
        if key!=self.key:self.reset(key)
        values=np.asarray([timestamp,*local,*solar],dtype=float)
        if not np.isfinite(values).all():return None
        if self.samples and timestamp-self.samples[-1][0]>30:self.reset(key)
        while not self.results.empty():
            generation,model=self.results.get()
            if generation==self.generation:self.model=model
        # A fresh pair must also agree with the calibration before using it.
        model=self.model;rotation=None
        if model and 0<=timestamp-model['checked_at']<=15:
            angle=model['angle']+model['rate']*(timestamp-model['epoch'])
            c,s=math.cos(angle),math.sin(angle)
            predicted=np.array(model['center'])+[c*local[0]-s*local[1],s*local[0]+c*local[1]]
            if np.linalg.norm(predicted-np.asarray(solar[:2]))<=CALIBRATION_ERROR_LIMIT_M:
                rotation=dict(frame_id=key[0],angle=angle,valid=True)
        if not self.samples or timestamp-self.samples[-1][0]>=1:
            self.samples.append((timestamp,*local[:2],*solar[:2]))
            self.samples=self.samples[-180:]
        if (len(self.samples)>=20 and timestamp-self.last_fit>=5
                and (self.worker is None or not self.worker.is_alive())):
            self.last_fit=timestamp;snapshot=list(self.samples);generation=self.generation
            def fit():
                try:model=fit_rotation(snapshot)
                except (ValueError,ArithmeticError,np.linalg.LinAlgError):model=None
                self.results.put((generation,model))
            self.worker=threading.Thread(target=fit,name='navpoint-planet-rotation',daemon=True)
            self.worker.start()
        return rotation


def pair_from_rows(texts,identity):
    """Full-precision transient pair; malformed solar data only blocks bearing."""
    from sc_voice_cockpit.navpoint.distance_precision import ZONE
    from sc_voice_cockpit.navpoint.active_frame import _coordinates
    rows=[ZONE.fullmatch(' '.join(t.split())) for t in texts]
    rows=[r for r in rows if r]
    local=[r for r in rows if r['name']==identity['zone_name']]
    solar=[r for r in rows if r['name'].startswith('SolarSystem_')]
    roots=[r for r in rows if r['name']=='Root']
    if len(local)!=1 or len(solar)!=1 or len(roots)!=1:return None
    try:
        xyz=[_coordinates([r[k] for k in ('x','y','z')])[0] for r in (local[0],solar[0],roots[0])]
    except (ValueError,ArithmeticError):return None
    if math.dist(xyz[1],xyz[2])>.2 or abs(xyz[0][2]-xyz[1][2])>.2:return None
    return (identity['planet_id'],solar[0]['name']),xyz[0],xyz[1]
