"""Reproducible skill ZIP with verified official Windows OpenCV binaries.

Only release provenance is fixed; no application installation is changed.
The wheel cache is outside this repository. No pip installation is performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '5.0.0.93'
BINARIES = ('cv2/cv2.pyd', 'cv2/opencv_videoio_ffmpeg500_64.dll')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def vendor(cache):
    cache = cache.expanduser().resolve()
    if cache == ROOT or ROOT in cache.parents:
        raise ValueError('Wheel cache must be outside the repository')
    with urlopen(f'https://pypi.org/pypi/opencv-python/{VERSION}/json', timeout=60) as response:
        metadata = json.load(response)
    candidates = [row for row in metadata['urls'] if row['filename'].endswith('win_amd64.whl')
                  and ('cp37-abi3' in row['filename'] or 'cp311-' in row['filename'])]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one Python 3.11-compatible Windows amd64 wheel')
    row = candidates[0]
    if not row['url'].startswith('https://files.pythonhosted.org/'):
        raise ValueError('Unexpected PyPI download origin')
    cache.mkdir(parents=True, exist_ok=True)
    wheel = cache / row['filename']
    if not wheel.exists() or sha(wheel.read_bytes()) != row['digests']['sha256']:
        with urlopen(row['url'], timeout=60) as response:
            data = response.read()
        if sha(data) != row['digests']['sha256']:
            raise ValueError('PyPI wheel digest mismatch')
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            stream.write(data)
            temporary = Path(stream.name)
        temporary.replace(wheel)
    provenance = json.loads((ROOT/'sc_voice_cockpit/navpoint/_vendor/provenance.json').read_text(encoding='utf-8'))
    expected = {item['path']:item for item in provenance['files']}
    result = {}
    with zipfile.ZipFile(wheel) as archive:
        for name in BINARIES + ('cv2/LICENSE.txt', 'cv2/LICENSE-3RD-PARTY.txt'):
            data = archive.read(name)
            if sha(data) != expected[name]['sha256'] or len(data) != expected[name]['bytes']:
                raise ValueError('Vendored provenance mismatch: '+name)
            result['sc_voice_cockpit/navpoint/_vendor/'+name] = data
    return result, {'distribution':'opencv_python', 'version':VERSION,
                    'wheel':row['filename'], 'wheel_sha256':row['digests']['sha256'], 'source':row['url']}

def payload():
    package = ROOT/'sc_voice_cockpit'
    for path in sorted(package.rglob('*')):
        if not path.is_file():
            continue
        parts = path.relative_to(package).parts
        if path.is_symlink():
            raise ValueError('Symlink in payload')
        if any(part in {'__pycache__','generated_files','.pytest_cache','tests'} for part in parts):
            continue
        if path.suffix in {'.pyc','.pyd','.dll','.log'}:
            continue
        if any(part.startswith(('deck-restore-','refused')) for part in parts):
            continue
        if path.name in {'dashboard_token','deck_pairing_code','deck_manifest.json','deck_setup.json',
                         'custom_binds.json','command_manifest.json','command_steps.json','bindings_snapshot.json','navpoints.db'}:
            continue
        yield path.relative_to(ROOT).as_posix(), path.read_bytes()

def build(out, cache):
    native, receipt = vendor(cache)
    files = dict(payload())
    files.update(native)
    version = re.search(r'^version: ([\d.]+)',(ROOT/'sc_voice_cockpit/default_config.yaml').read_text(encoding='utf-8'),re.M)[1]
    out.mkdir(parents=True, exist_ok=True)
    path = out/f'SC-Voice-Cockpit-{version}.zip'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            info.create_system=3
            archive.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    (out/'PAYLOAD-SHA256.txt').write_text(''.join(f'{sha(data)}  {name}\n' for name,data in sorted(files.items())),encoding='utf-8')
    digest=sha(path.read_bytes())
    (out/'SHA256SUMS').write_text(digest+'  '+path.name+'\n',encoding='utf-8')
    (out/'OPENCV-SOURCE.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    return {'zip':str(path),'sha256':digest,'payload_files':len(files),'bytes':path.stat().st_size, 'opencv':receipt}

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'dist')
    parser.add_argument('--cache',type=Path,default=Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'sc-voice-cockpit-wheels')
    args=parser.parse_args()
    print(json.dumps(build(args.out,args.cache),indent=2))
