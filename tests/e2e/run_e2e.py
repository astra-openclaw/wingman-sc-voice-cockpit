#!/usr/bin/env python3
"""Real headless Chrome/CDP acceptance against any fake-facade deck server.

Dependency: websocket-client in a throwaway venv (unpinned). The target must
expose the three loopback-only test-probe routes documented in PROTOCOL.md.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import signal
from pathlib import Path
import subprocess
import socket
import tempfile
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


def request(base,path,obj=None):
    req=Request(base+path,data=json.dumps(obj).encode() if obj is not None else None,
                headers={'Content-Type':'application/json'} if obj is not None else {})
    with urlopen(req,timeout=5) as response:return json.load(response)


class Chrome:
    def __enter__(self):
        try:return self.start()
        except BaseException:
            self.__exit__()
            raise
    def start(self):
        import websocket
        self.temp=tempfile.TemporaryDirectory(prefix='sc-voice-cockpit-chrome-')
        self.proc=subprocess.Popen(['/usr/bin/google-chrome','--headless=new','--disable-gpu','--no-sandbox','--disable-dev-shm-usage','--no-first-run',
             '--no-default-browser-check','--disable-extensions','--disable-background-networking','--disable-component-update','--no-proxy-server','--remote-debugging-port=0','--remote-allow-origins=http://localhost',
             '--user-data-dir='+self.temp.name,'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        active=Path(self.temp.name)/'DevToolsActivePort'
        self.wait(lambda:active.exists(),10)
        port=active.read_text().splitlines()[0]
        target=next(t for t in request('http://127.0.0.1:'+port,'/json/list') if t.get('type')=='page' and t.get('url')=='about:blank')
        debug=urlparse(target['webSocketDebuggerUrl'])
        direct=socket.create_connection((debug.hostname,debug.port),timeout=8)
        self.ws=websocket.create_connection(target['webSocketDebuggerUrl'],origin='http://localhost',timeout=55,socket=direct)
        # websocket-client retains the supplied socket's 8s connect timeout.
        # Set the read deadline explicitly; fresh Chrome navigation can take 25s.
        self.ws.settimeout(55)
        self.counter=0;self.call('Page.enable');self.call('Runtime.enable')
        self.call('Network.enable')
        self.call('Network.setBlockedURLs',{'urls':['*fonts.googleapis.com*','*fonts.gstatic.com*']})
        self.call('Emulation.setDeviceMetricsOverride',{'width':1200,'height':900,'deviceScaleFactor':1,'mobile':False})
        self.call('Emulation.setEmulatedMedia',{'features':[{'name':'prefers-reduced-motion','value':'reduce'}]})
        return self
    def __exit__(self,*args):
        if hasattr(self,'ws'):
            try:self.call('Browser.close')
            except Exception:pass  # Browser.close may close CDP before its response.
            self.ws.close()
        if hasattr(self,'proc'):
            # Wait for graceful Browser.close before terminating its own process
            # group. The launcher may exit before renderer/profile writers do.
            try:self.proc.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid,signal.SIGTERM)
                try:self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid,signal.SIGKILL);self.proc.wait(5)
            def live_children():
                for entry in Path('/proc').iterdir():
                    if not entry.name.isdigit():continue
                    try:fields=(entry/'stat').read_text().rsplit(')',1)[1].split()
                    except (OSError,IndexError):continue
                    if fields[0]!='Z' and int(fields[2])==self.proc.pid:return True
                return False
            if live_children():
                try:os.killpg(self.proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                self.wait(lambda:not live_children(),5)
        if hasattr(self,'temp'):self.temp.cleanup()
    @staticmethod
    def wait(fn,timeout=8):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            value=fn()
            if value:return value
            time.sleep(.05)
        raise AssertionError('Timed out waiting for browser/server condition')
    def call(self,method,params=None):
        self.counter+=1;ident=self.counter;self.ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
        while True:
            msg=json.loads(self.ws.recv())
            if msg.get('id')==ident:
                if msg.get('error'):raise AssertionError(msg['error'])
                return msg.get('result',{})
    def js(self,expr):
        result=self.call('Runtime.evaluate',{'expression':expr,'returnByValue':True,'awaitPromise':True})
        if 'exceptionDetails' in result:raise AssertionError(result['exceptionDetails'])
        return result.get('result',{}).get('value')
    def navigate(self,url):
        self.call('Page.navigate',{'url':url});self.wait(lambda:self.js("document.readyState==='complete'"))
    def click(self,selector):
        point=self.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)}); if(!e)return null;const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]}})()")
        if not point:raise AssertionError('Missing browser control '+selector)
        self.pointer(point,True);self.pointer(point,False)
    def pointer(self,point,down):
        self.call('Input.dispatchMouseEvent',{'type':'mousePressed' if down else 'mouseReleased','x':point[0],'y':point[1],'button':'left','clickCount':1})


def run(base,token,artifacts=None):
    results=[]
    def passed(name):results.append(name);print(f'{name:34} PASS',flush=True)
    manifest=request(base,'/__test/manifest')
    def events():return request(base,'/__test/events')['events']
    def push(msg):request(base,'/__test/push',msg)
    with Chrome() as browser:
        b=browser
        # A clean profile begins with pairing, never a shared real browser login.
        b.navigate(base+'/')
        b.wait(lambda:b.js("!!document.querySelector('#code')"))
        if b.js("!!document.querySelector('#intro') && !document.querySelector('#intro').classList.contains('gone')"):
            b.click('#intro')
            b.wait(lambda:b.js("document.querySelector('#intro').classList.contains('gone')"))
        b.js('document.querySelector("#code").value='+json.dumps(token))
        b.click('#pair button')
        b.wait(lambda:b.js("document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
        if b.js("!!document.querySelector('#intro') && !document.querySelector('#intro').classList.contains('gone')"):b.click('#intro')
        b.wait(lambda:b.js("!document.querySelector('#intro') || document.querySelector('#intro').classList.contains('gone')"))
        b.wait(lambda:b.js("document.querySelectorAll('.hud-btn').length>5"));passed('typed pairing + rendered deck')
        def capture(name):
            if artifacts:
                artifacts.mkdir(parents=True,exist_ok=True)
                shot=b.call('Page.captureScreenshot',{'format':'png'})['data']
                (artifacts/(name+'.png')).write_bytes(base64.b64decode(shot))
        capture('tablet-deck')
        # State on the first visible gear toggle, sent by the server, not local JS.
        state_button=b.js("(()=>{let e=document.querySelector('[data-state-group=gear]');return e?e.getAttribute('aria-label'):null})()")
        assert state_button,'Gear control not rendered'
        selector='[data-state-group="gear"]'
        before=len(events());b.click(selector)
        gear_ids={key for key,value in manifest['buttons'].items() if value.get('kind')=='command' and (value.get('state') or {}).get('group')=='gear'}
        b.wait(lambda:any(e.get('t')=='tap' and e.get('id') in gear_ids for e in events()[before:]));passed('command tap received by server')
        push({'t':'state','group':'gear','on':True})
        b.wait(lambda:b.js("document.querySelector('[data-state-group=gear]').classList.contains('fx-on')"))
        push({'t':'state','group':'gear','on':False})
        b.wait(lambda:not b.js("document.querySelector('[data-state-group=gear]').classList.contains('fx-on')"));passed('server sync lights on/off')
        # Find a hold panel by paging the original UI, preserving its event handlers.
        holds={x['label'] for x in manifest['buttons'].values() if x['kind']=='hold' and x.get('mode')=='hold' and (x.get('hold') or {}).get('action')=='v_view_look_behind'}
        found=None
        for _ in range(20):
            found=b.js('(()=>{const labels='+json.dumps(sorted(holds))+";for(const e of document.querySelectorAll('.hud-btn')){if(labels.includes(e.getAttribute('aria-label'))){let r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]}}return null})()")
            if found:break
            b.click('[data-region="left"] .next')
        assert found,'Hold button not reachable'
        before=len(events());b.pointer(found,True)
        b.wait(lambda:any(e.get('t')=='down' for e in events()[before:]));b.pointer(found,False)
        b.wait(lambda:any(e.get('t')=='up' for e in events()[before:]))
        sequence=[e['t'] for e in events()[before:] if e.get('t') in ('down','up')]
        assert sequence==['down','up'],sequence
        held_events=[e for e in events()[before:] if e.get('t') in ('down','up')]
        assert held_events[0]['id']==held_events[1]['id']
        assert manifest['buttons'][held_events[0]['id']]['kind']=='hold'
        passed('hold received down then up')
        push({'t':'blocked'})
        b.wait(lambda:b.js("document.querySelector('#toast').textContent.includes('not focused')"))
        push({'t':'heard','text':'test flight ready'})
        b.wait(lambda:b.js("document.querySelector('#toast').textContent.includes('test flight ready')"));passed('blocked + heard toasts')
        point=b.js("(()=>{let e=[...document.querySelectorAll('.js-talk')].find(e=>e.getBoundingClientRect().width>0),r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()")
        before=len(events());b.pointer(point,True)
        b.wait(lambda:any(e.get('t')=='voice' and e.get('on') for e in events()[before:]))
        b.wait(lambda:b.js("[...document.querySelectorAll('.js-talk')].some(e=>e.classList.contains('listening'))"))
        b.pointer(point,False)
        b.wait(lambda:any(e.get('t')=='voice' and e.get('on') is False for e in events()[before:]))
        b.wait(lambda:not b.js("[...document.querySelectorAll('.js-talk')].some(e=>e.classList.contains('listening'))"));passed('mic down/up + listening')
        b.navigate(base+'/nav')
        back=b.js("[...document.querySelectorAll('a')].find(e=>new URL(e.href).pathname==='/')?.getAttribute('href')")
        assert back,'NAV page missing link back'
        b.click('a[href='+json.dumps(back)+']')
        b.wait(lambda:b.js("location.pathname==='/'"));passed('NAV page + return link')
        # Clear local pairing, then exercise the HttpOnly share-link cookie path.
        b.js("localStorage.clear(); sessionStorage.clear()")
        b.call('Network.clearBrowserCookies')
        b.navigate(base+'/?'+urlencode({'share':token}))
        b.wait(lambda:b.js("document.body.classList.contains('canvas-mode') && !document.body.classList.contains('pairing') && !document.body.classList.contains('offline')"))
        cookies=b.call('Network.getCookies',{'urls':[base+'/']})['cookies']
        assert any(c.get('httpOnly') for c in cookies),'Share did not set HttpOnly cookie'
        passed('share link HttpOnly cookie pairing')
        # Reflow the actual phone frontend, then reach the added Commands/NAV panels
        # through the original pager controls. No private commands are loaded.
        b.call('Emulation.setDeviceMetricsOverride',{'width':852,'height':393,'deviceScaleFactor':1,'mobile':True})
        b.navigate(base+'/?device=phone')
        b.wait(lambda:b.js("document.documentElement.dataset.device==='phone' && document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
        if b.js("!document.querySelector('#intro').classList.contains('gone')"):
            b.click('#intro');b.wait(lambda:b.js("document.querySelector('#intro').classList.contains('gone')"))
        capture('phone-deck')
        for label,minimum in [('Launch Sequence',10)]:
            found=False
            for _ in range(24):
                found=b.js('!![...document.querySelectorAll(\'.hud-btn\')].find(e=>e.getAttribute(\'aria-label\')==='+json.dumps(label)+')')
                if found:break
                b.click('[data-region="right"] .next')
            assert found,'Added panel not reachable: '+label
            bounds=b.js("(()=>{const r=document.querySelector('[data-region=right]').getBoundingClientRect();return [...document.querySelectorAll('[data-region=right] .hud-grid .hud-btn')].map(e=>{const b=e.getBoundingClientRect();return {w:b.width,h:b.height,inside:b.x>=r.x-1&&b.y>=r.y-1&&b.right<=r.right+1&&b.bottom<=r.bottom+1}})})()")
            assert len(bounds)>=minimum,(label,len(bounds))
            assert all(x['inside'] and x['w']>=44 and x['h']>=44 for x in bounds),(label,bounds)
            capture('phone-astra' if label=='Launch Sequence' else 'phone-nav')
        passed('phone + Commands panel bounds')
    print(f'{len(results)} browser checks passed; Chrome stopped.')
    return results


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--token',default='test-pair-code');p.add_argument('--artifacts',type=Path);args=p.parse_args()
    try:run(args.base.rstrip('/'),args.token,args.artifacts)
    except Exception as e:print('E2E FAIL:',str(e),flush=True);raise SystemExit(1)
if __name__=='__main__':main()
