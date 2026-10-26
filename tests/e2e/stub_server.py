#!/usr/bin/env python3
"""Loopback-only stdlib protocol reference, NOT a production deck server.

No Wingman/game access. Received inputs are recorded, never executed. --selftest
runs the real Chrome harness with an ephemeral port, then stops all resources.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import struct
import sys
import threading
from urllib.parse import urlparse, parse_qs

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from sc_voice_cockpit.deck_pack import plan_pack
from sc_voice_cockpit.deck_pack.planner import MACROS


def make_plan():
    from sample_index import synthetic_index
    index=synthetic_index()
    # Public brief names with inert synthetic steps: render the full Commands panel
    # without loading or exposing the private command snapshot.
    commands=[{'name':name,'actions':[{'wait':.1}]} for name in MACROS]
    return plan_pack(index,None,[],commands,{}, {'emotes':True,'skill_name':'SC_VoiceCockpit'})


class Stub(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,static=None,token='test-pair-code'):
        super().__init__(address,Handler)
        self.plan=make_plan();self.token=token;self.events=[];self.clients=[];self.states={};self.lock=threading.RLock()
        fork=ROOT/'sc_voice_cockpit/deck/static'
        self.static=static or (fork if fork.is_dir() else ROOT/'upstream/tablet-deck/public')
        self.static_files={p.name:p for p in self.static.iterdir() if p.is_file()}
    def publish(self,msg):
        if msg.get('t')=='state':self.states[msg['group']]=bool(msg['on'])
        with self.lock:
            for client in list(self.clients):
                try:client.frame(msg)
                except OSError:pass


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def output(self,data,status=200,mime='application/json',headers=None):
        body=json.dumps(data).encode() if mime=='application/json' else data.encode() if isinstance(data,str) else data
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
    def authenticated(self,query):
        return query.get('t',[''])[0]==self.server.token or 'stub_session=paired' in self.headers.get('Cookie','')
    def do_GET(self):
        u=urlparse(self.path);q=parse_qs(u.query);path=u.path
        if path.startswith('/__test/'):
            if path=='/__test/events':self.output({'events':self.server.events});return
            if path=='/__test/manifest':self.output(self.server.plan['deck']);return
        if path=='/ws':
            if not self.authenticated(q):self.output({},403);return
            return self.upgrade()
        if path in ('/','/nav') and q.get('share',[''])[0]==self.server.token:
            self.send_response(303);self.send_header('Location',path);self.send_header('Content-Length','0')
            self.send_header('Set-Cookie','stub_session=paired; Path=/; HttpOnly; SameSite=Strict');self.end_headers();return
        if path=='/nav':
            if not self.authenticated(q):self.output({},403);return
            self.output('<!doctype html><title>Navigation</title><h1>NAV</h1><a href="/">Tablet Deck</a>',mime='text/html');return
        if path in ('/layout.json','/layouts.json','/voice.json','/links.json'):
            if not self.authenticated(q):self.output({},403);return
            phone=q.get('device',[''])[0]=='phone'
            files=self.server.plan['deck']['layouts']['files']
            selected=q.get('layout',[''])[0] or ('hud-phone.json' if phone else 'hud.json')
            if path=='/layout.json':
                self.output(files.get(selected,files['hud.json']),headers={'Set-Cookie':'stub_session=paired; Path=/; HttpOnly; SameSite=Strict'});return
            if path=='/layouts.json':
                self.output({'layouts':[{'file':n,'name':l['name']} for n,l in files.items() if not l.get('canvas') or (l['canvas'][1]<500)==phone], 'pcChoice':'hud-phone.json' if phone else 'hud.json'});return
            if path=='/links.json':self.output({'links':['NAV Dashboard']});return
            self.output({'on':True,'ready':True,'state':'ready','commands':[[p,c['name']] for c in self.server.plan['commands_to_add'] for p in c.get('instant_activation',[])]});return
        name='index.html' if path=='/' else path[1:]
        if name not in self.server.static_files:self.output({},404);return
        import mimetypes
        self.output(self.server.static_files[name].read_bytes(),mime=mimetypes.guess_type(name)[0] or 'application/octet-stream')
    def do_POST(self):
        if urlparse(self.path).path!='/__test/push':self.output({},404);return
        msg=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        self.server.publish(msg);self.output({'ok':True})
    def frame(self,obj):
        body=json.dumps(obj).encode();header=bytes([0x81,len(body)]) if len(body)<126 else bytes([0x81,126])+struct.pack('!H',len(body))
        with self.frame_lock:self.wfile.write(header+body);self.wfile.flush()
    def upgrade(self):
        key=self.headers.get('Sec-WebSocket-Key','')
        accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
        self.frame_lock=threading.Lock()
        with self.server.lock:self.server.clients.append(self)
        self.frame({'t':'hello','voice':True,'voiceStatus':{'state':'ready'},'states':self.server.states})
        try:
            while True:
                h=self.rfile.read(2)
                if len(h)<2 or h[0]&15==8:break
                length=h[1]&127
                if length==126:length=struct.unpack('!H',self.rfile.read(2))[0]
                elif length==127:length=struct.unpack('!Q',self.rfile.read(8))[0]
                if length>65536:break
                mask=self.rfile.read(4) if h[1]&128 else None
                body=self.rfile.read(length)
                if mask:body=bytes(v^mask[i%4] for i,v in enumerate(body))
                if h[0]&15!=1:continue
                msg=json.loads(body)
                with self.server.lock:self.server.events.append(msg)
                if msg['t']=='voice':self.server.publish({'t':'listening','on':bool(msg.get('on'))})
                if msg['t']=='reset-states':self.server.states.clear();self.server.publish({'t':'states','states':{}})
        except (OSError,ValueError):pass
        finally:
            with self.server.lock:self.server.clients.remove(self)
            self.close_connection=True


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=0);parser.add_argument('--static',type=Path)
    parser.add_argument('--token',default='test-pair-code');parser.add_argument('--selftest',action='store_true')
    parser.add_argument('--artifacts',type=Path);args=parser.parse_args()
    with Stub(('127.0.0.1',args.port),args.static,args.token) as server:
        base='http://localhost:'+str(server.server_port)
        print('Reference server:',base,flush=True)
        if args.selftest:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                from run_e2e import run
                run(base,args.token,args.artifacts)
            finally:server.shutdown();thread.join(5)
        else:
            try:server.serve_forever()
            except KeyboardInterrupt:pass

if __name__=='__main__':main()
