"""Read-only ZIP64/CryXmlB extractor with an independent RFC 8878 decoder.

Python 3.11 stdlib only. Format knowledge: RFC 8878, xxHash specification,
and unp4k (CryXmlB). No third-party implementation code is vendored.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import time
import zlib
from datetime import datetime, timezone

EXTRACTOR_VERSION = 2
TARGETS = ("data/libs/config/defaultprofile.xml", "data/localization/english/global.ini")
MAX_RESOURCE = 64 * 1024 * 1024

class DecodeError(ValueError):
    """Corrupt, unsupported, or out-of-bounds resource."""

def _need(condition, message):
    if not condition:
        raise DecodeError(message)

class _ReverseBits:
    """Reverse bitstream, table-sized reads from preloaded 64-bit words."""
    def __init__(self, data):
        _need(bool(data) and data[-1] != 0, "Invalid zstd bitstream end marker")
        self.words = [int.from_bytes(data[i:i+8], "little") for i in range(0, len(data), 8)]
        self.pos = (len(data)-1)*8 + data[-1].bit_length()-1

    def peek(self, n):
        take = min(n, self.pos)
        p = self.pos - take
        i, b = divmod(p, 64)
        v = self.words[i] >> b
        if b + take > 64:
            v |= self.words[i+1] << (64-b)
        return (v & ((1 << take)-1)) << (n-take)

    def read(self, n):
        _need(n <= self.pos, "Truncated zstd bitstream")
        v = self.peek(n)
        self.pos -= n
        return v

def _fse_table(probs, log):
    size = 1 << log
    _need(sum(abs(x) for x in probs) == size, "Invalid FSE probability total")
    symbols = [0]*size
    high = size-1
    nxt = []
    for s, count in enumerate(probs):
        nxt.append(1 if count == -1 else count)
        if count == -1:
            symbols[high] = s
            high -= 1
    p = 0
    step = (size >> 1) + (size >> 3) + 3
    for s, count in enumerate(probs):
        for _ in range(max(0, count)):
            symbols[p] = s
            p = (p + step) & (size-1)
            while p > high:
                p = (p + step) & (size-1)
    _need(p == 0, "Invalid FSE symbol spread")
    table = []
    for s in symbols:
        k = nxt[s]
        _need(k > 0, "Invalid FSE state")
        bits = log - (k.bit_length()-1)
        table.append((s, bits, (k << bits)-size))
        nxt[s] += 1
    return log, table

def _read_fse(data, max_log, max_symbol):
    # Forward probability headers are bounded to 256 symbols.
    value = int.from_bytes(data[:512], "little")
    available = min(len(data), 512)*8
    p = 0
    def bits(n):
        nonlocal p
        _need(p+n <= available, "Truncated FSE distribution")
        v = (value >> p) & ((1 << n)-1)
        p += n
        return v
    log = bits(4)+5
    _need(log <= max_log, "FSE accuracy log exceeds limit")
    remaining = 1 << log
    probs = []
    while remaining:
        _need(len(probs) <= max_symbol, "Too many FSE symbols")
        bound = remaining+1
        n = bound.bit_length()
        threshold = (1 << n)-1-bound
        small = bits(n-1)
        if small < threshold:
            count = small
        else:
            count = small + (bits(1) << (n-1))
            if count >= (1 << (n-1)):
                count -= threshold
        count -= 1
        probs.append(count)
        remaining -= abs(count)
        _need(remaining >= 0, "Invalid FSE distribution")
        if count == 0:
            while True:
                run = bits(2)
                _need(len(probs)+run <= max_symbol+1, "FSE zero run exceeds alphabet")
                probs.extend([0]*run)
                if run != 3:
                    break
    _need(sum(count != 0 for count in probs) >= 2, "FSE distribution requires at least two symbols")
    return _fse_table(probs, log), (p+7)//8

def _huffman(data):
    _need(bool(data), "Missing Huffman tree")
    header = data[0]
    if header >= 128:
        count = header-127
        consumed = 1+(count+1)//2
        _need(len(data) >= consumed, "Truncated Huffman weights")
        weights = [(data[1+i//2] >> (0 if i & 1 else 4)) & 15 for i in range(count)]
    else:
        consumed = 1+header
        _need(header > 0 and len(data) >= consumed, "Truncated compressed Huffman weights")
        (log, table), n = _read_fse(data[1:consumed], 6, 12)
        stream = _ReverseBits(data[1+n:consumed])
        states = [stream.read(log), stream.read(log)]
        weights = []
        turn = 0
        while True:
            s, nb, base = table[states[turn]]
            weights.append(s)
            _need(len(weights) < 255, "Too many Huffman weights")
            if nb > stream.pos:
                weights.append(table[states[1-turn]][0])
                break
            states[turn] = base+stream.read(nb)
            turn = 1-turn
    _need(all(0 <= w <= 11 for w in weights), "Invalid Huffman weight")
    total = sum(1 << (w-1) for w in weights if w)
    _need(total > 0, "Empty Huffman tree")
    log = total.bit_length()
    _need(log <= 11, "Huffman depth exceeds limit")
    rest = (1 << log)-total
    _need(rest > 0 and rest & (rest-1) == 0, "Invalid final Huffman weight")
    weights.append(rest.bit_length())
    _need(len(weights) <= 256, "Huffman alphabet exceeds limit")
    table = []
    for w in range(1, log+1):
        for symbol, weight in enumerate(weights):
            if weight == w:
                table.extend([(symbol, log+1-w)]*(1 << (w-1)))
    _need(len(table) == 1 << log, "Incomplete Huffman tree")
    return (log, table), consumed

def _huff_stream(data, count, tree):
    stream = _ReverseBits(data)
    log, table = tree
    out = bytearray(count)
    for i in range(count):
        s, n = table[stream.peek(log)]
        _need(n <= stream.pos, "Truncated Huffman literals")
        stream.pos -= n
        out[i] = s
        if i & 16383 == 16383:
            time.sleep(0)
    _need(stream.pos == 0, "Extra Huffman bits")
    return out

_LL_BITS = [0]*16+[1]*4+[2,2,3,3,4,6,7,8,9,10,11,12,13,14,15,16]
_LL_BASE = list(range(16))+[16,18,20,22,24,28,32,40,48,64,128,256,512,1024,2048,4096,8192,16384,32768,65536]
_ML_BITS = [0]*32+[1]*4+[2,2,3,3,4,4,5,7,8,9,10,11,12,13,14,15,16]
_ML_BASE = list(range(3,35))+[35,37,39,41,43,47,51,59,67,83,99,131,259,515,1027,2051,4099,8195,16387,32771,65539]
_LL_PROBS = [4,3]+[2]*11+[1]*3+[2]*9+[3,2]+[1]*5+[-1]*4
_ML_PROBS = [1,4,3]+[2]*6+[1]*37+[-1]*7
_OF_PROBS = [1]*6+[2]*3+[1]*15+[-1]*5

class _ZstdFrame:
    def __init__(self, output, start, limit, window, coverage):
        self.output, self.start, self.limit, self.window = output, start, limit, window
        self.huff = None
        self.tables = [None]*3
        self.reps = [1,4,8]
        self.coverage = coverage

    def mark(self, key):
        if self.coverage is not None:
            self.coverage[key] = self.coverage.get(key, 0)+1

    def compressed(self, data):
        _need(bool(data), "Missing literals header")
        kind, fmt = data[0] & 3, (data[0] >> 2) & 3
        self.mark("literals_"+str(kind))
        if kind < 2:
            hs = 1 if fmt in (0,2) else 2 if fmt == 1 else 3
            _need(len(data) >= hs, "Truncated literals header")
            regen = int.from_bytes(data[:hs], "little") >> (3 if hs == 1 else 4)
            cs = regen if kind == 0 else 1
            _need(regen <= min(131072, self.limit-len(self.output)), "Literals exceed output limit")
            _need(hs+cs < len(data), "Truncated literals or sequences")
            literals = data[hs:hs+cs] if kind == 0 else data[hs:hs+1]*regen
        else:
            hs = 3 if fmt < 2 else fmt+2
            _need(len(data) >= hs, "Truncated literals header")
            bits = 10 if fmt < 2 else 14 if fmt == 2 else 18
            h = int.from_bytes(data[:hs], "little") >> 4
            regen, cs = h & ((1 << bits)-1), h >> bits
            _need(regen <= min(131072, self.limit-len(self.output)), "Literals exceed output limit")
            _need(hs+cs < len(data), "Truncated compressed literals")
            content = data[hs:hs+cs]
            if kind == 2:
                self.huff, used = _huffman(content)
                content = content[used:]
            _need(self.huff is not None, "Treeless literals without a previous Huffman tree")
            streams = 1 if fmt == 0 else 4
            self.mark("huffman_"+str(streams))
            if streams == 1:
                literals = _huff_stream(content, regen, self.huff)
            else:
                _need(len(content) >= 6 and regen >= 6, "Invalid Huffman jump table")
                sizes = list(struct.unpack_from("<HHH", content))
                sizes.append(len(content)-6-sum(sizes))
                _need(min(sizes) > 0, "Invalid Huffman stream sizes")
                n = (regen+3)//4
                literals = bytearray()
                p = 6
                for i, size in enumerate(sizes):
                    literals.extend(_huff_stream(content[p:p+size], n if i < 3 else regen-3*n, self.huff))
                    p += size
        seq = data[hs+cs:]
        count, p = seq[0], 1
        if count == 0:
            _need(len(seq) == 1, "Trailing zero-sequence bytes")
            self.output.extend(literals)
            return
        if count >= 128:
            n = 2 if count == 255 else 1
            _need(len(seq) >= p+n, "Truncated sequence count")
            count = int.from_bytes(seq[p:p+n], "little")+(0x7f00 if count == 255 else (count-128)*256)
            p += n
        _need(count > 0 and p < len(seq), "Missing sequence modes")
        modes = seq[p]
        p += 1
        _need(modes & 3 == 0, "Reserved sequence mode bits")
        for i, (shift, max_log, max_symbol, probs, log) in enumerate([
                (6,9,35,_LL_PROBS,6), (4,8,31,_OF_PROBS,5), (2,9,52,_ML_PROBS,6)]):
            mode = (modes >> shift) & 3
            self.mark("sequence_mode_"+str(mode))
            if mode == 0:
                self.tables[i] = _fse_table(probs, log)
            elif mode == 1:
                _need(p < len(seq) and seq[p] <= max_symbol, "Invalid RLE sequence symbol")
                self.tables[i] = (0, [(seq[p], 0, 0)])
                p += 1
            elif mode == 2:
                self.tables[i], used = _read_fse(seq[p:], max_log, max_symbol)
                p += used
            else:
                _need(self.tables[i] is not None, "Repeat sequence table without previous table")
        stream = _ReverseBits(seq[p:])
        states = [stream.read(t[0]) for t in self.tables]
        tables = [t[1] for t in self.tables]
        lp = 0
        out = self.output
        block_start = len(out)
        for i in range(count):
            ll, of, ml = [tables[j][states[j]] for j in range(3)]
            offset_value = (1 << of[0])+stream.read(of[0])
            match = _ML_BASE[ml[0]]+stream.read(_ML_BITS[ml[0]])
            length = _LL_BASE[ll[0]]+stream.read(_LL_BITS[ll[0]])
            if offset_value > 3:
                offset = offset_value-3
                self.reps = [offset, *self.reps[:2]]
            else:
                rep = offset_value-1+(length == 0)
                self.mark("repeat_offset")
                if rep == 3:
                    offset = self.reps[0]-1
                    self.reps = [offset, *self.reps[:2]]
                else:
                    offset = self.reps.pop(rep)
                    self.reps.insert(0, offset)
            _need(lp+length <= len(literals), "Sequence exceeds literals")
            _need(len(out)+length+match <= self.limit and len(out)+length+match-block_start <= 131072,
                  "Sequence exceeds output limit")
            out.extend(literals[lp:lp+length])
            lp += length
            _need(0 < offset <= min(len(out)-self.start, self.window), "Invalid match offset or missing dictionary")
            # Slice copies handle nonoverlap and overlapping periodic matches.
            take = min(offset, match)
            pattern = out[len(out)-offset:len(out)-offset+take]
            if match > take:
                pattern = (pattern*((match+take-1)//take))[:match]
            out.extend(pattern)
            if i+1 < count:
                for j in (0,2,1):
                    _, n, base = (ll, of, ml)[j]
                    states[j] = base+stream.read(n)
            if i & 2047 == 2047:
                time.sleep(0)
        _need(stream.pos == 0, "Extra sequence bits")
        _need(len(out)+len(literals)-lp <= self.limit and len(out)+len(literals)-lp-block_start <= 131072,
              "Final literals exceed output limit")
        out.extend(literals[lp:])

_MASK = (1 << 64)-1
_PRIMES = (11400714785074694791,14029467366897019727,1609587929392839161,9650029242287828579,2870177450012600261)

def xxhash64(data):
    """xxHash64, seed zero; low 32 bits form the frame checksum."""
    p1,p2,p3,p4,p5 = _PRIMES
    def rot(x, n):
        return ((x << n) | (x >> (64-n))) & _MASK
    def rnd(a, b):
        return rot((a+b*p2) & _MASK,31)*p1 & _MASK
    n, pos = len(data), 0
    if n >= 32:
        lanes = [(p1+p2) & _MASK,p2,0,(-p1) & _MASK]
        while pos+32 <= n:
            vals = struct.unpack_from("<QQQQ",data,pos)
            for i in range(4):
                lanes[i] = rnd(lanes[i], vals[i])
            pos += 32
            if pos & 65535 == 0:
                time.sleep(0)
        h = sum(rot(v,r) for v,r in zip(lanes,(1,7,12,18))) & _MASK
        for v in lanes:
            h = ((h ^ rnd(0,v))*p1+p4) & _MASK
    else:
        h = p5
    h = (h+n) & _MASK
    while pos+8 <= n:
        h = (rot(h ^ rnd(0,struct.unpack_from("<Q",data,pos)[0]),27)*p1+p4) & _MASK
        pos += 8
    if pos+4 <= n:
        h = (rot(h ^ (struct.unpack_from("<I",data,pos)[0]*p1 & _MASK),23)*p2+p3) & _MASK
        pos += 4
    for b in data[pos:]:
        h = rot(h ^ (b*p5 & _MASK),11)*p1 & _MASK
    h ^= h >> 33
    h = h*p2 & _MASK
    h ^= h >> 29
    h = h*p3 & _MASK
    return h ^ (h >> 32)

def zstd_decompress(data, expected_size, *, coverage=None):
    """Concatenated RFC 8878 frames, bounded by mandatory ZIP entry size."""
    _need(isinstance(expected_size,int) and 0 <= expected_size <= MAX_RESOURCE, "Invalid expected zstd size")
    out = bytearray()
    p = 0
    def take(n):
        nonlocal p
        _need(n >= 0 and p+n <= len(data), "Truncated zstd frame")
        b = data[p:p+n]
        p += n
        return b
    _need(bool(data), "Missing zstd frame")
    while p < len(data):
        magic = int.from_bytes(take(4), "little")
        _need(magic & 0xfffffff0 != 0x184d2a50, "Skippable zstd frames are unsupported")
        _need(magic == 0xfd2fb528, "Unknown zstd frame magic")
        desc = take(1)[0]
        _need(not desc & 8, "Reserved zstd frame bit")
        single = bool(desc & 32)
        if not single:
            wd = take(1)[0]
            base = 1 << (10+(wd >> 3))
            window = base+(base >> 3)*(wd & 7)
        did_size = (0,1,2,4)[desc & 3]
        did = int.from_bytes(take(did_size), "little")
        _need(did == 0, "Zstd dictionaries are unsupported")
        fcs_size = (1 if single else 0,2,4,8)[desc >> 6]
        fcs = int.from_bytes(take(fcs_size), "little") if fcs_size else None
        if fcs_size == 2:
            fcs += 256
        start = len(out)
        _need(fcs is None or fcs <= expected_size-start, "Declared frame size exceeds ZIP size")
        if single:
            window = fcs
        limit = expected_size if fcs is None else start+fcs
        frame = _ZstdFrame(out, start, limit, window, coverage)
        last = False
        while not last:
            header = int.from_bytes(take(3), "little")
            last, kind, size = bool(header & 1), (header >> 1) & 3, header >> 3
            _need(size <= min(131072,window) or size == 0, "Zstd block exceeds window/block limit")
            frame.mark("block_"+str(kind))
            if kind in (0,1):
                _need(len(out)+size <= limit, "Zstd block exceeds output size")
                raw = take(size if kind == 0 else 1)
                out.extend(raw if kind == 0 else raw*size)
            elif kind == 2:
                frame.compressed(take(size))
            else:
                raise DecodeError("Reserved zstd block type")
            time.sleep(0)
        _need(fcs is None or len(out)-start == fcs, "Zstd frame content size mismatch")
        if desc & 4:
            checksum = int.from_bytes(take(4), "little")
            _need(xxhash64(memoryview(out)[start:]) & 0xffffffff == checksum, "Zstd content checksum mismatch")
    _need(len(out) == expected_size, "Zstd ZIP uncompressed size mismatch")
    return bytes(out)


def decode_cryxml(data):
    _need(len(data) >= 44 and data[:8] == b'CryXmlB\0', 'Not CryXmlB')
    endian = '<' if struct.unpack_from('<I',data,8)[0] == len(data) else '>'
    def u32(p): return struct.unpack_from(endian+'I',data,p)[0]
    def u16(p): return struct.unpack_from(endian+'H',data,p)[0]
    _need(u32(8) == len(data), 'Invalid CryXmlB length')
    no,nc,ao,ac,co,cc,so,sl = struct.unpack_from(endian+'8I',data,12)
    for off, size in [(no,nc*28),(ao,ac*8),(co,cc*4),(so,sl)]:
        _need(off >= 44 and off+size <= len(data), 'Invalid CryXmlB table bounds')
    def string(off):
        _need(off < sl, 'Invalid CryXmlB string offset')
        end = data.find(b'\0',so+off,so+sl)
        _need(end >= so+off, 'Unterminated CryXmlB string')
        return data[so+off:end].decode('utf-8',errors='replace')
    nodes = []
    for i in range(nc):
        p = no+i*28
        na,nchild,ai,ci = u16(p+8),u16(p+10),u32(p+16),u32(p+20)
        _need(ai+na <= ac and ci+nchild <= cc, 'Invalid CryXmlB node bounds')
        attrs = {string(u32(ao+(ai+j)*8)):string(u32(ao+(ai+j)*8+4)) for j in range(na)}
        children = [u32(co+(ci+j)*4) for j in range(nchild)]
        _need(all(c < nc for c in children), 'Invalid CryXmlB child')
        nodes.append(dict(tag=string(u32(p)),attrs=attrs,children=children))
    return dict(nodes=nodes,root=0)


def parse_ini(text):
    labels = {}
    for line in text.lstrip('\ufeff').splitlines():
        if '=' not in line or re.match(r'^\s*[;#\[]',line): continue
        key,val = line.split('=',1)
        labels[re.sub(r',P$','',key.strip(),flags=re.I).lower()] = val.strip()
    return labels


def index_profile(tree, labels):
    nodes = tree['nodes']
    def clean(v):
        return re.sub(r'\s+',' ',re.sub(r'<[^>]+>','',str(v or '')).replace('\\n',' ')).strip()
    def label(v):
        if not v: return ''
        return clean((labels.get(re.sub(r',P$','',v[1:],flags=re.I).lower()) or v) if v.startswith('@') else v)
    activation = {n['attrs']['name']:n['attrs'] for n in nodes if n['tag'].lower() == 'activationmode' and n['attrs'].get('name')}
    devices = ('keyboard','mouse','gamepad','joystick')
    fields = {'activationMode','onPress','onHold','onRelease','multiTap','multiTapBlock','pressTriggerThreshold','releaseTriggerThreshold','releaseTriggerDelay','holdTriggerDelay','retriggerable','activationMap'}
    def bind(action,dev,value,extra):
        if value is None: return
        for token in str(value).split('|'):
            token = token.strip()
            if not re.sub(r'^(kb|mo|gp|js)\d+_','',token,flags=re.I).strip(): continue
            mode = extra.get('activationMode') or action['activationMode'] or ''
            data = dict(input=token,activationMode=mode)
            for k,v in {**activation.get(mode,{}),**extra}.items():
                if k in fields: data[k] = v
            if data not in action[dev]: action[dev].append(data)
    actions = []
    for m in nodes:
        if m['tag'].lower() != 'actionmap': continue
        for ident in m['children']:
            n = nodes[ident]
            if n['tag'].lower() != 'action': continue
            ma,na = m['attrs'],n['attrs']
            a = dict(map=ma.get('name',''),mapLabel=label(ma.get('UILabel') or ma.get('uiLabel') or ma.get('label')) or ma.get('name',''),
                     name=na.get('name',''),label=label(na.get('UILabel') or na.get('uiLabel') or na.get('label')) or na.get('name',''),
                     description=label(na.get('UIDescription') or na.get('uiDescription') or na.get('description')),
                     activationMode=na.get('activationMode',''),**{d:[] for d in devices})
            for dev in devices: bind(a,dev,na.get(dev),na)
            def child_inputs(ident,device='',inherited=None,depth=0):
                _need(depth <= 16, 'Device nesting too deep')
                c = nodes[ident]
                attrs = c['attrs']
                e = {**(inherited or {}),**attrs}
                tag = c['tag'].lower()
                d = tag if tag in devices else device
                if 'input' in attrs:
                    t = (attrs.get('type') or attrs.get('device') or '').lower()
                    s = attrs['input']
                    slot = d or (t if t in devices else '') or ('mouse' if re.match(r'^mo\d+_',s,re.I) else 'gamepad' if re.match(r'^gp\d+_',s,re.I) else 'joystick' if re.match(r'^js\d+_',s,re.I) else 'keyboard')
                    bind(a,slot,s,e)
                for dev in devices: bind(a,dev,attrs.get(dev),e)
                for cid in c['children']: child_inputs(cid,d,e,depth+1)
            for cid in n['children']: child_inputs(cid,'',na)
            actions.append(a)
    return actions


def _read_at(f, offset, size):
    _need(offset >= 0 and size >= 0, 'Invalid archive read bounds')
    f.seek(offset)
    data = f.read(size)
    _need(len(data) == size, 'Unexpected end of archive')
    return data


def find_entries(f, size):
    tail_len = min(size,65557+20)
    tail = _read_at(f,size-tail_len,tail_len)
    ep = tail.rfind(b'PK\x05\x06')
    _need(ep >= 0 and ep+22 <= len(tail), 'ZIP end record missing')
    length,off = struct.unpack_from('<II',tail,ep+12)
    if ep >= 20 and tail[ep-20:ep-16] == b'PK\x06\x07':
        zipoff = struct.unpack_from('<Q',tail,ep-12)[0]
        z = _read_at(f,zipoff,56)
        _need(z[:4] == b'PK\x06\x06', 'ZIP64 record missing')
        length,off = struct.unpack_from('<QQ',z,40)
    _need(off+length <= size, 'Invalid central directory bounds')
    pos,base,block = off,-1,b''
    def window(n):
        nonlocal base,block
        _need(n <= off+length-pos, 'Truncated directory entry')
        if pos < base or pos+n > base+len(block):
            base = pos
            block = _read_at(f,pos,min(4*1024*1024,off+length-pos))
        _need(pos+n <= base+len(block), 'Truncated directory entry')
        return pos-base
    entries = {}
    while pos < off+length:
        p = window(46)
        _need(block[p:p+4] == b'PK\x01\x02','Invalid directory entry')
        nl,xl,cl = struct.unpack_from('<HHH',block,p+28)
        n = 46+nl+xl+cl
        p = window(n)
        name = block[p+46:p+46+nl].decode('utf-8',errors='replace').replace('\\','/').lower()
        if name in TARGETS:
            flags,method = struct.unpack_from('<HH',block,p+8)
            comp,uncomp = struct.unpack_from('<II',block,p+20)
            lho = struct.unpack_from('<I',block,p+42)[0]
            entry = dict(name=name,flags=flags,method=method,comp=comp,uncomp=uncomp,lho=lho)
            q,end = p+46+nl,p+46+nl+xl
            while q+4 <= end:
                typ,l = struct.unpack_from('<HH',block,q)
                if typ == 1:
                    _need(q+4+l <= end,'Truncated ZIP64 extra field')
                    z = q+4
                    for key in ('uncomp','comp','lho'):
                        if entry[key] == 0xffffffff:
                            _need(z+8 <= q+4+l,'Truncated ZIP64 value')
                            entry[key] = struct.unpack_from('<Q',block,z)[0]
                            z += 8
                q += 4+l  # Proprietary trailing extra records need not be standard ZIP fields.
            entries[name] = entry
            if len(entries) == len(TARGETS): break
        pos += n
    _need(len(entries) == len(TARGETS),'Required game resources missing')
    return entries


def decode_entry(f, entry, size):
    _need(not entry['flags'] & 1,'Encrypted resource is unsupported')
    lh = _read_at(f,entry['lho'],30)
    _need(lh[:4] == b'PK\x03\x04','Invalid local header')
    nl,xl = struct.unpack_from('<HH',lh,26)
    off = entry['lho']+30+nl+xl
    comp,uncomp = entry['comp'],entry['uncomp']
    _need(0 <= comp <= 32*1024*1024 and 0 <= uncomp <= MAX_RESOURCE and off+comp <= size,'Resource bounds exceeded')
    raw = _read_at(f,off,comp)
    method = entry['method']
    if method == 100:
        data = zstd_decompress(raw,uncomp)
    elif method == 8:
        obj = zlib.decompressobj(-15)
        data = obj.decompress(raw,uncomp+1)
        _need(obj.eof and not obj.unconsumed_tail and not obj.unused_data,'Invalid or overlong deflate resource')
    elif method == 0:
        data = raw
    else:
        raise DecodeError('Unsupported resource compression method')
    _need(len(data) == uncomp,'Resource length mismatch')
    return data


def atomic_json(file, data):
    file = Path(file)
    file.parent.mkdir(parents=True,exist_ok=True)
    fd,name = tempfile.mkstemp(prefix=file.name+'.',suffix='.tmp',dir=file.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,separators=(',',':'),ensure_ascii=False)
        os.replace(name,file)
    finally:
        if os.path.exists(name): os.unlink(name)


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp,timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')


def extract(game, out):
    game,out = Path(game),Path(out)
    started = time.perf_counter()
    manifest = json.loads((game/'build_manifest.id').read_text(encoding='utf-8-sig'))
    build = manifest.get('Data',manifest)
    with (game/'Data.p4k').open('rb') as f:
        stat = os.fstat(f.fileno())
        key = dict(p4kSize=stat.st_size,p4kMtime=_iso(stat.st_mtime))
        def result(noop, raw):
            return dict(noop=noop,seconds=time.perf_counter()-started,actions=len(raw['actions']),bytes=out.stat().st_size,metadata=raw['metadata'])
        try:
            old = json.loads(out.read_text(encoding='utf-8-sig'))
            meta = old['metadata']
            if meta.get('buildId') == build['BuildId'] and meta.get('extractorVersion') == EXTRACTOR_VERSION and all(meta.get(k) == v for k,v in key.items()) and old.get('actions'):
                return result(True,old)
        except (OSError,ValueError,KeyError,TypeError): pass
        cache = Path(str(out)+'.entries.json')
        entries = None
        try:
            c = json.loads(cache.read_text(encoding='utf-8'))
            if all(c.get(k) == v for k,v in key.items()) and all(t in c['entries'] for t in TARGETS): entries = c['entries']
        except (OSError,ValueError,KeyError,TypeError): pass
        if entries is None:
            entries = find_entries(f,stat.st_size)
            atomic_json(cache,dict(**key,entries=entries))
        tree = decode_cryxml(decode_entry(f,entries[TARGETS[0]],stat.st_size))
        labels = parse_ini(decode_entry(f,entries[TARGETS[1]],stat.st_size).decode('utf-8',errors='replace'))
        actions = index_profile(tree,labels)
        _need(bool(actions),'No actions decoded')
        after = os.fstat(f.fileno())
        _need((stat.st_size,stat.st_mtime_ns) == (after.st_size,after.st_mtime_ns),'Game archive changed during extraction')
        metadata = dict(buildId=build['BuildId'],version=build.get('Version'),branch=build.get('Branch'),**key,
                        extractedAt=_iso(time.time()),extractorVersion=EXTRACTOR_VERSION)
        raw = dict(metadata=metadata,actions=actions)
        atomic_json(out,raw)
        return result(False,raw)
