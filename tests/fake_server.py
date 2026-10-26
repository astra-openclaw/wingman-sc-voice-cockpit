"""Real production server and runtime with inert Wingman/input facades; local only."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse
from sc_voice_cockpit.deck.runtime import DeckRuntime
from sc_voice_cockpit.deck_pack import plan_pack
from sc_voice_cockpit.navpoint.database import NavPointDatabase
from sc_voice_cockpit.web.server import CockpitServer

class ProbeServer(CockpitServer):
    """Probe endpoints exist ONLY in this test subclass, never in the payload."""
    async def _protect(self,request,call_next):
        if request.url.path.startswith("/__test/"):
            if not request.client or request.client.host!="127.0.0.1":return JSONResponse({},status_code=403)
            return await call_next(request)
        return await super()._protect(request,call_next)

    def _create_app(self):
        app=super()._create_app()
        @app.get("/__test/events")
        async def events():return {"events":self.events,"invocations":self.invocations,"speech":self.speech}
        @app.get("/__test/manifest")
        async def manifest():return self.deck.manifest
        @app.get("/__test/setup-card",response_class=HTMLResponse)
        async def setup_card():
            from html import escape
            from sc_voice_cockpit.deck.setup import SETUP_CARD
            return '<html><body style="background:#101923;color:#f1f5fa;font:24px system-ui;max-width:760px;margin:90px auto;line-height:1.6"><h1>SC Voice-Cockpit</h1><h2>First-run setup</h2><p>'+escape(SETUP_CARD)+'</p><small>Synthetic preview of the startup card text. The real card appears in Wingman.</small></body></html>'
        @app.post("/__test/not-set-up")
        async def not_set_up():
            from sc_voice_cockpit.deck_pack.planner import walk
            for b in walk(self.deck.manifest):
                if b.get("id")=="top-chat":b["not_set_up"]=True
            self.deck.broadcast({"t":"reload"})
            return {"ok":True}
        @app.post("/__test/no-key")
        async def no_key():
            from sc_voice_cockpit.deck_pack.planner import walk
            self.deck.manifest["commands"]["Emote Wave"]["no_key"]=True
            for b in walk(self.deck.manifest):
                if b.get("command")=="Emote Wave":b["no_key"]=True
            self.deck.broadcast({"t":"reload"})
            return {"ok":True}
        @app.post("/__test/push")
        async def push(request:Request):
            msg=await request.json()
            if msg.get("t")=="state":
                # State injection goes through the actual silent sync implementation.
                name="__probe_sync"
                self.deck.manifest["commands"][name]={"effects":[{"group":msg["group"],"set":msg["on"]}]}
                self.deck.sync(name)
            else:self.deck.broadcast(msg)
            return {"ok":True}
        @app.post("/__test/nav")
        async def nav_probe(request:Request):
            data = await request.json()
            if data.get("external"):
                self._db.add_navpoint("External Survey", "synthetic", "Daymar", 1200, 0, 295000)
                self.notify_update()
            if data.get("fresh"):
                self.set_position({"x":100., "y":0., "z":295000., "body":"Daymar",
                    "active_frame":"planet", "frame_id":"daymar", "camdir":data.get("camdir",[0,0,0]),
                    "planet_rotation":{"frame_id":"daymar", "angle":0., "valid":True},
                    "captured_at":time.time()})
            if data.get("stale"):
                self.set_position({**(self._current_position or {}), "captured_at":time.time()-60})
            return {"ok":True}
        return app

class Keyboard:
    def press(self,k):pass
    def release(self,k):pass

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--port",type=int,default=0);ap.add_argument("--selftest",action="store_true");ap.add_argument("--nav-selftest",action="store_true");ap.add_argument("--showcase",action="store_true");ap.add_argument("--artifacts",type=Path);args=ap.parse_args()
    from sample_index import synthetic_index
    raw=synthetic_index()
    from sc_voice_cockpit.deck_pack.planner import MACROS
    commands=[{"name":name,"actions":[{"wait":.1}]} for name in MACROS]
    from sc_voice_cockpit.deck_pack.planner import key_steps,sync_step
    for name,key in (("VTOL On","ralt+z"),("VTOL Off","lalt+z")):
        commands.append({"name":name,"actions":key_steps(key)+[sync_step(name,"SC_VoiceCockpit")]})
    profile='<ActionMaps><actionmap name="spaceship_movement"><action name="v_vtol_on"><rebind input="kb1_ralt+z"/></action><action name="v_vtol_off"><rebind input="kb1_lalt+z"/></action></actionmap></ActionMaps>'
    plan=plan_pack(raw,profile,[],commands,{},{"emotes":True,"skill_name":"SC_VoiceCockpit"})
    by_name={c["name"]:c for c in commands+plan["commands_to_add"]}
    wing=NS(name="Test Pilot",conversation=NS(add_note=None),commands=NS(get=lambda n:by_name.get(n)))
    skill=NS(wingman=wing,log=NS(warning=lambda *a,**k:None),deck_setting=lambda n,d:"off" if n=="deck_feedback" else d,
             deck_nav=None,_arm=lambda line:None)
    async def add_note(n):pass
    async def nav(n):pass
    wing.conversation.add_note=add_note;skill.deck_nav=nav
    speech=[]
    async def speak(line,**kwargs):speech.append(line);return True
    wing.tts=NS(speak=speak);wing.conversation.show=AsyncMock()
    deck=DeckRuntime(skill,plan["deck"],focus=lambda:True,keyboard=Keyboard())
    invocations=[]
    async def invoke(name,args):
        invocations.append(args["command_name"])
        deck.sync(args["command_name"])
        return NS(response="OK",instant_response="")
    wing.tools=NS(invoke=invoke)
    deck.mic=lambda on:deck.broadcast({"t":"listening","on":on})
    events=[]
    original=deck.receive
    def receive(client,msg):
        events.append(msg)
        original(client,msg)
    deck.receive=receive
    with tempfile.TemporaryDirectory(prefix="cockpit-fake-server-") as temp:
        folder=Path(temp);db=NavPointDatabase(str(folder))
        server=ProbeServer(db,deck,folder,port=args.port,share_on_lan=False)
        server.events=events;server.invocations=invocations;server.speech=speech
        if args.showcase:
            # README showcase: invented, believable player waypoints (no real player data).
            db.add_navpoint("Hidden Cave Entrance", "synthetic", "Daymar", 1100, 0, 295000, notes="Bring a flashlight")
            db.add_navpoint("Crashed Cutter", "synthetic", "Daymar", 2600, 400, 295000)
            db.add_navpoint("Mining Spot: Quantanium", "synthetic", "Daymar", -1800, 900, 295000)
            db.add_navpoint("Quiet Landing Pad", "synthetic", "Hurston", 3100, 0, 295000)
        else:
            db.add_navpoint("Survey Alpha", "synthetic", "Daymar", 1100, 0, 295000, notes="Synthetic survey site")
            db.add_navpoint("Survey Beta", "synthetic", "Daymar", 2100, 0, 295000)
            db.add_navpoint("Far Survey", "synthetic", "Hurston", 3100, 0, 295000)
        async def nav(action):
            if action == "clear_target": server.set_active_target(None)
            elif action == "mark_location":
                db.add_navpoint("Marked Survey", "synthetic", "Daymar", 100, 0, 295000)
                server.notify_update()
        skill.deck_nav = nav
        token=server.pairing_code
        server._access_token=token
        deck.start()
        assert server.start()
        base=server.url
        try:
            if args.showcase:
                sys.path.insert(0,str(ROOT/"tests/e2e"))
                from showcase_e2e import run as run_showcase
                run_showcase(base,token,args.artifacts)
            elif args.selftest or args.nav_selftest:
                sys.path.insert(0,str(ROOT/"tests/e2e"))
                from run_e2e import run
                if args.selftest: run(base,token,args.artifacts)
                from navdesk_e2e import run as run_navdesk
                run_navdesk(base, token, args.artifacts)
                if args.selftest:
                    from lights_e2e import run as run_lights
                    run_lights(base,token,args.artifacts)
                    from setup_e2e import run as run_setup
                    run_setup(base,token,args.artifacts)
            else:
                print(base,flush=True)
                while True:time.sleep(.2)
        finally:
            deck.stop();server.stop()
if __name__=="__main__":main()
