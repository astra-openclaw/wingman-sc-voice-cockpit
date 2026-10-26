import copy
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from sc_voice_cockpit.keybindings import p4k_reader as p


def cryxml(endian='<'):
    nodes=[('profile',{},[1]),('actionmap',{'name':'flight','UILabel':'@label'},[2]),
           ('action',{'name':'test','activationMode':'press'},[3]),('keyboard',{},[4]),('inputdata',{'input':'x'},[])]
    strings={}
    text=bytearray()
    for tag,attrs,_ in nodes:
        for s in [tag,*attrs.keys(),*attrs.values()]:
            if s not in strings:strings[s]=len(text);text.extend(s.encode()+b'\0')
    nc,ac,cc=len(nodes),sum(len(n[1]) for n in nodes),sum(len(n[2]) for n in nodes)
    no=44;ao=no+28*nc;co=ao+8*ac;so=co+4*cc
    b=bytearray(so+len(text));b[:8]=b'CryXmlB\0'
    struct.pack_into(endian+'9I',b,8,len(b),no,nc,ao,ac,co,cc,so,len(text))
    ai=ci=0
    for i,(tag,attrs,children) in enumerate(nodes):
        pos=no+i*28
        struct.pack_into(endian+'I',b,pos,strings[tag]);struct.pack_into(endian+'HH',b,pos+8,len(attrs),len(children))
        struct.pack_into(endian+'II',b,pos+16,ai,ci)
        for k,v in attrs.items():struct.pack_into(endian+'II',b,ao+ai*8,strings[k],strings[v]);ai+=1
        for c in children:struct.pack_into(endian+'I',b,co+ci*4,c);ci+=1
    b[so:]=text
    return bytes(b)


def archive(entries,method=0,zip64=True):
    parts=[];cd=[];off=0
    for name,data in entries:
        nb=name.encode();lh=bytearray(30);struct.pack_into('<I',lh,0,0x04034b50);struct.pack_into('<HH',lh,26,len(nb),0)
        if method==8:
            import zlib
            obj=zlib.compressobj(wbits=-15);raw=obj.compress(data)+obj.flush()
        elif method==100:
            raw=subprocess.run(['zstd','-q','-c'],input=data,check=True,capture_output=True).stdout
        else:raw=data
        extra=struct.pack('<HHQQQHHI',1,24,len(data),len(raw),off,0xcafe,999,0) if zip64 else b''
        c=bytearray(46);struct.pack_into('<I',c,0,0x02014b50);struct.pack_into('<HH',c,8,0,method)
        struct.pack_into('<II',c,20,0xffffffff if zip64 else len(raw),0xffffffff if zip64 else len(data))
        struct.pack_into('<HHH',c,28,len(nb),len(extra),0);struct.pack_into('<I',c,42,0xffffffff if zip64 else off)
        cd.extend([bytes(c),nb,extra]);parts.extend([bytes(lh),nb,raw]);off+=30+len(nb)+len(raw)
    directory=b''.join(cd);end=bytearray(22);struct.pack_into('<I',end,0,0x06054b50)
    if zip64:
        z=bytearray(56);struct.pack_into('<IQ',z,0,0x06064b50,44);struct.pack_into('<QQQ',z,32,len(entries),len(directory),off)
        loc=struct.pack('<IIQI',0x07064b50,0,off+len(directory),1)
        struct.pack_into('<II',end,12,0xffffffff,0xffffffff)
        tail=bytes(z)+loc+end
    else:
        struct.pack_into('<II',end,12,len(directory),off);tail=bytes(end)
    return b''.join(parts)+directory+tail+b'proprietary trailer'

class ExtractorTests(unittest.TestCase):
    def test_cryxml_both_endians_ini_and_bounds(self):
        self.assertEqual(p.parse_ini('\ufefflabel,P=Flight\nother=Has=equals\n;ignore=no'),{'label':'Flight','other':'Has=equals'})
        for endian in ('<','>'):
            a=p.index_profile(p.decode_cryxml(cryxml(endian)),p.parse_ini('label,P=Flight'))
            self.assertEqual(a[0]['mapLabel'],'Flight');self.assertEqual(a[0]['keyboard'][0]['input'],'x')
        for bad in (b'bad',cryxml()[:-1]):
            with self.assertRaises(p.DecodeError):p.decode_cryxml(bad)
        bad=bytearray(cryxml());struct.pack_into('<I',bad,12,0xffffffff)
        with self.assertRaises(p.DecodeError):p.decode_cryxml(bad)

    def test_nested_timing_and_localization(self):
        tree={'nodes':[
            dict(tag='actionmap',attrs={'name':'flight'},children=[1,5]),
            dict(tag='action',attrs={'name':'v_lights_off','UILabel':'@enable','keyboard':'ralt+k|kb1_','activationMode':'press'},children=[2,4]),
            dict(tag='gamepad',attrs={'activationMode':'hold'},children=[3]),
            dict(tag='inputdata',attrs={'input':'button1'},children=[]),
            dict(tag='mouse',attrs={'input':'mouse3'},children=[]),
            dict(tag='activationmode',attrs={'name':'hold','holdTriggerDelay':'0.5'},children=[])]}
        a=p.index_profile(tree,p.parse_ini('enable=Headlights (Enable)'))[0]
        self.assertEqual(a['label'],'Headlights (Enable)');self.assertEqual(len(a['keyboard']),1)
        empty=p.index_profile(tree,p.parse_ini('enable='))[0]
        self.assertEqual(empty['label'],'@enable')
        self.assertEqual(a['gamepad'][0]['holdTriggerDelay'],'0.5');self.assertEqual(a['mouse'][0]['input'],'mouse3')
        loop=copy.deepcopy(tree);loop['nodes'][3]['children']=[3]
        with self.assertRaisesRegex(p.DecodeError,'nesting'):p.index_profile(loop,{})

    def test_zip_methods_zip64_cache_and_noop(self):
        for method in (0,8,100):
            if method==100 and not shutil.which('zstd'):continue
            for zip64 in (False,True):
                with self.subTest(method=method,zip64=zip64),tempfile.TemporaryDirectory(prefix='sc-kb-public-extract-') as d:
                    game=Path(d);out=game/'generated'/'defaults.json'
                    (game/'build_manifest.id').write_text(json.dumps({'Data':{'BuildId':'synthetic','Version':'1','Branch':'test'}}), encoding="utf-8")
                    (game/'Data.p4k').write_bytes(archive([('Data\\Libs\\Config\\defaultProfile.xml',cryxml()),('Data\\Localization\\english\\global.ini',b'label,P=Flight\n')],method,zip64))
                    first=p.extract(game,out);before=out.read_bytes()
                    self.assertFalse(first['noop']);self.assertEqual(first['actions'],1)
                    with patch.object(p,'find_entries',side_effect=AssertionError('must use cache')):
                        self.assertTrue(p.extract(game,out)['noop'])
                        old=json.loads(out.read_text(encoding="utf-8"));old['metadata']['extractorVersion']=0;out.write_text(json.dumps(old), encoding="utf-8")
                        self.assertFalse(p.extract(game,out)['noop'])
                    raw=json.loads(out.read_text(encoding="utf-8"));self.assertEqual(raw['actions'][0]['keyboard'][0]['input'],'x')
                    self.assertEqual(json.loads(before)['actions'],raw['actions'])
                    stamp=(game/'Data.p4k').stat();os.utime(game/'Data.p4k',ns=(stamp.st_atime_ns,stamp.st_mtime_ns+1000000000))
                    with patch.object(p,'find_entries',wraps=p.find_entries) as scan:p.extract(game,out);self.assertEqual(scan.call_count,1)
                    self.assertEqual(list(out.parent.glob('*.tmp')),[])

    @unittest.skipUnless(shutil.which('node'),'Node fast-path comparison optional')
    def test_node_python_synthetic_equivalence(self):
        with tempfile.TemporaryDirectory(prefix='sc-kb-public-node-') as d:
            game=Path(d);(game/'build_manifest.id').write_text('{"BuildId":"synthetic"}', encoding="utf-8")
            (game/'Data.p4k').write_bytes(archive([(p.TARGETS[0],cryxml()),(p.TARGETS[1],b'label=Flight')]))
            p.extract(game,game/'python.json')
            subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'sc_voice_cockpit/keybindings/extract_defaults.mjs'),'--game',str(game),'--out',str(game/'node.json')],check=True,capture_output=True)
            self.assertEqual(json.loads((game/'python.json').read_text(encoding="utf-8"))['actions'],json.loads((game/'node.json').read_text(encoding="utf-8"))['actions'])

class RealEquivalenceTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('SC_KB_REAL_DATA'),'Optional private data not configured')
    def test_real_index_actions_exact(self):
        folder=Path(os.environ['SC_KB_REAL_DATA']);expected=json.loads((folder/'defaults.json').read_text(encoding="utf-8"))['actions']
        if not (folder/'defaultprofile.xml.expected').exists():self.skipTest('Optional raw resource samples absent')
        actual=p.index_profile(p.decode_cryxml((folder/'defaultprofile.xml.expected').read_bytes()),p.parse_ini((folder/'global.ini.expected').read_text(encoding="utf-8")))
        self.assertTrue(actual==expected, 'Private actions differ (contents intentionally omitted)')
        print(f'Exact private extractor equivalence: {len(actual)} actions')
