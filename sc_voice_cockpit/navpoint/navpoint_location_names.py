"""File-backed display labels; canonical frame IDs remain navigation identities."""
import json
import re
from pathlib import Path
from functools import cache

@cache
def _frames():
    """Load runtime data only when needed, never during a catalog import probe."""
    return json.loads(Path(__file__).with_name('frame_names.json').read_text(encoding='utf-8'))['frames']

def frame_info(name):
    return _frames().get(str(name).casefold())

def canonical_zone(name):
    # Only repair the fixed OOC prefix, and only for a known full body name.
    if re.match(r'^[O0Q]{2}C_Stanton_',name):
        candidate='OOC'+name[3:]
        info=frame_info(candidate)
        if info and info['kind']=='planet':return info['canonical_name']
    return name

def normalize_rows(texts):
    def replace(match):
        return match[1]+canonical_zone(match[2])+match[3]
    return [re.sub(r'^(Zone:\s*)(\S+)(\s+Pos:)',replace,' '.join(t.split())) for t in texts]

def display_name(frame_id='',fallback=''):
    info=frame_info(frame_id) or frame_info(fallback)
    return info['label'] if info else fallback or frame_id
