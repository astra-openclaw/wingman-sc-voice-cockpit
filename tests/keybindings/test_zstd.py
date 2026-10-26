"""Synthetic zstd interoperability and hostile-input checks; CLI is test-only."""
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import time
import unittest
from sc_voice_cockpit.keybindings.p4k_reader import zstd_decompress, xxhash64, DecodeError

class ZstdTests(unittest.TestCase):
    def test_xxhash_vectors(self):
        self.assertEqual(xxhash64(b''),0xef46db3751d8e999)
        self.assertEqual(xxhash64(b'a'),0xd24ec4f1a98c6e5b)
        self.assertEqual(xxhash64(b'abc'),0x44bc2cf5ad770999)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd CLI required only for interoperability tests')
    def test_cli_matrix(self):
        rng = random.Random(8878)
        corpus = {
            'empty':b'', 'repetitive':b'abcd'*80000,
            'binary':bytes(range(256))*1200,
            'random':rng.randbytes(270000),
            'text':('Synthetic flight controls: pilot ship safely, land, scan, repeat.\n'*5000).encode(),
            # Non-uniform, nonrepeating symbols force Huffman literal streams.
            'weighted':bytes(rng.choices(range(120),weights=range(120,0,-1),k=300000)),
            'mixed':b''.join(rng.randbytes(180)+b'synthetic-prefix'*rng.randrange(10,40) for _ in range(900))}
        levels = [('1',['-1']),('3',['-3']),('9',['-9']),('19',['-19']),('22',['--ultra','-22']),('long',['-3','--long'])]
        self.coverage = {}; results=[]
        with tempfile.TemporaryDirectory(prefix='sc-kb-public-zstd-') as d:
            for name,raw in corpus.items():
                path=Path(d)/'source';path.write_bytes(raw)
                for level,args in levels:
                    for checksum in (False,True):
                        with self.subTest(corpus=name,level=level,checksum=checksum):
                            compressed=subprocess.run(['/usr/bin/zstd','-q','-c',*args,'--check' if checksum else '--no-check',str(path)],check=True,capture_output=True).stdout
                            start=time.perf_counter()
                            actual=zstd_decompress(compressed,len(raw),coverage=self.coverage)
                            self.assertEqual(actual,raw)
                            results.append(dict(corpus=name,level=level,checksum=checksum,seconds=round(time.perf_counter()-start,6)))
            # Concatenated frames reset offsets and entropy tables, and have per-frame checksums.
            a=subprocess.run(['/usr/bin/zstd','-q','-c'],input=b'one'*100,check=True,capture_output=True).stdout
            b=subprocess.run(['/usr/bin/zstd','-q','-c'],input=b'two'*100,check=True,capture_output=True).stdout
            self.assertEqual(zstd_decompress(a+b,600),b'one'*100+b'two'*100)
            # Frame without content size (stdin).
            c=subprocess.run(['/usr/bin/zstd','-q','-c'],input=b'unknown size'*100,check=True,capture_output=True).stdout
            self.assertEqual(zstd_decompress(c,1200),b'unknown size'*100)
        report=dict(cases=len(results),matrix=results,coverage=self.coverage)
        Path(__file__).with_name('zstd-matrix.json').write_text(json.dumps(report,indent=2), encoding="utf-8")

    def test_raw_rle_and_literals_rle(self):
        magic=b'\x28\xb5\x2f\xfd'
        frame=lambda size,block:magic+bytes([32,size])+block
        self.assertEqual(zstd_decompress(frame(5,(5*8+1).to_bytes(3,'little')+b'hello'),5),b'hello')
        self.assertEqual(zstd_decompress(frame(7,(7*8+3).to_bytes(3,'little')+b'x'),7),b'x'*7)
        # Compressed block, RLE literals, zero sequences.
        payload=bytes([5*8+1])+b'Q\0'
        self.assertEqual(zstd_decompress(frame(5,(len(payload)*8+5).to_bytes(3,'little')+payload),5),b'Q'*5)

    def test_sequence_rle_and_header_variants(self):
        magic=b'\x28\xb5\x2f\xfd'
        # One literal followed by a length-three repeat match; all RLE tables.
        payload=b'\x08a\x01\x54\x01\x00\x00\x01'
        frame=magic+b'\x80\x00'+(4).to_bytes(4,'little')+(len(payload)*8+5).to_bytes(3,'little')+payload
        coverage={};self.assertEqual(zstd_decompress(frame,4,coverage=coverage),b'aaaa')
        self.assertEqual(coverage['sequence_mode_1'],3)
        if shutil.which('zstd'):
            reference=subprocess.run(['/usr/bin/zstd','-q','-d','-c'],input=frame,capture_output=True,check=True).stdout
            self.assertEqual(reference,b'aaaa')
        # Eight-byte content size, legal explicit zero dictionary ID, reserved-bit rejection.
        f=magic+b'\xe1\x00'+(5).to_bytes(8,'little')+b'\x29\0\0hello'
        self.assertEqual(zstd_decompress(f,5),b'hello')
        # Repeat/treeless modes require a previous table in this frame.
        for payload in (b'\x08a\x01\xfc\x01',b'\x53\x00\x00\x01\x00'):
            frame=magic+b'\x80\x00'+(10).to_bytes(4,'little')+(len(payload)*8+5).to_bytes(3,'little')+payload
            with self.assertRaises(DecodeError):zstd_decompress(frame,10)

    def test_malformed(self):
        good=b'\x28\xb5\x2f\xfd\x20\x05\x29\0\0hello'
        for p in range(len(good)):
            with self.subTest(truncated_at=p),self.assertRaises(DecodeError): zstd_decompress(good[:p],5)
        for bad,size in [(b'\x50\x2a\x4d\x18',0),(b'garbage',0),(good,4),(good,6),
                         (b'\x28\xb5\x2f\xfd\x21\x03\x05',5),
                         (b'\x28\xb5\x2f\xfd\x28',0),
                         (b'\x28\xb5\x2f\xfd\x20\x05\x07\0\0',5)]:
            with self.subTest(bad=bad),self.assertRaises(DecodeError): zstd_decompress(bad,size)
        checked=bytearray(good);checked[4]|=4;checked.extend(b'\0'*4)
        with self.assertRaisesRegex(DecodeError,'checksum'): zstd_decompress(checked,5)
        # Every truncation of a compressed frame must raise ValueError, never IndexError/struct.error.
        if shutil.which('zstd'):
            c=subprocess.run(['/usr/bin/zstd','-q','-c'],input=(b'abc def '*500),check=True,capture_output=True).stdout
            for p in range(len(c)):
                with self.subTest(compressed_cut=p),self.assertRaises(DecodeError): zstd_decompress(c[:p],4000)

class RealDataTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('SC_KB_REAL_DATA'),'Optional private samples not configured')
    def test_real_samples(self):
        base=Path(os.environ['SC_KB_REAL_DATA']); results=[]
        for p in base.glob('*.zst'):
            expected=p.with_suffix('.expected').read_bytes();coverage={};times=[]
            for _ in range(3):
                start=time.perf_counter();actual=zstd_decompress(p.read_bytes(),len(expected),coverage=coverage)
                times.append(time.perf_counter()-start);self.assertEqual(actual,expected)
            results.append(dict(name=p.name,compressed=p.stat().st_size,uncompressed=len(expected),seconds=times,coverage=coverage))
        if not results:self.skipTest('Optional zstd samples absent')
        (Path(tempfile.gettempdir())/'sc-kb-public-real-results.json').write_text(json.dumps(results,indent=2), encoding="utf-8")
