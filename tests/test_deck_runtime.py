import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from sc_voice_cockpit.deck.runtime import DeckRuntime, TABLET_COMMAND
from sc_voice_cockpit.deck.focus import game_focused
from sc_voice_cockpit.web.server import CockpitServer, pairing_code
from sc_voice_cockpit.navpoint.database import NavPointDatabase
from sc_voice_cockpit.navpoint.navpoint_ui import sharing

def manifest():
    return {"buttons":{
        "gear":{"kind":"command","command":"Gear","label":"Gear","respond":True},
        "look":{"kind":"hold","label":"Look","hold":{"input":"kb1_ralt+x"}},
        "eject":{"kind":"hold","label":"Eject","type":"slider","mode":"timed","ms":100,"hold":{"input":"kb1_x"}},
        "nav":{"kind":"nav","nav":"clear_target"},
        "reset":{"kind":"ui","ui":"reset-states"}
    },"commands":{"Gear":{"effects":[{"group":"gear","set":None}],"buttons":["gear"]}},
    "layouts":{"tablet":{"name":"Test","pages":[]},"phone":{"name":"Phone","pages":[]}}}

class Keyboard:
    def __init__(self):self.events=[]
    def press(self,key):self.events.append(("down",key))
    def release(self,key):self.events.append(("up",key))

def skill():
    wing=NS(name="Pilot",conversation=NS(user_busy=False,add_note=AsyncMock(),show=AsyncMock()),
        audio=NS(is_playing=False),tools=NS(invoke=AsyncMock(return_value=NS(response="",instant_response=""))),
        commands=NS(get=lambda n:{"responses":["Confirmed."]}),tts=NS(speak=AsyncMock()),
        config=NS(prompts=NS(backstory="Pilot voice")),ai=NS(generate=AsyncMock(return_value="Done.")))
    return NS(wingman=wing,log=NS(warning=lambda *a,**k:None),deck_setting=lambda n,d: "note" if n=="deck_feedback" else d,
              deck_nav=AsyncMock(),_arm=lambda s:None)

def runtime():
    return DeckRuntime(skill(),manifest(),focus=lambda:True,keyboard=Keyboard(),mic=lambda on:None)

def test_set_flip_reset_and_silent_fault():
    d=runtime();messages=[];d.broadcast=messages.append
    d.sync("Gear");assert d.states["gear"]
    d.sync("Gear");assert not d.states["gear"]
    d.manifest["commands"]["Set"]={"effects":[{"group":"gear","set":True}]}
    d.sync("Set");d.sync("Set");assert d.states["gear"]
    d.reset();assert d.states=={}
    d.manifest=None;assert d.sync("bad")==""

def test_heard_only_recent_voice():
    d=runtime();messages=[];d.broadcast=messages.append
    d.last_utterance=("gear down",time.monotonic())
    d.sync("Gear");assert messages[-1]["t"]=="heard"
    token=TABLET_COMMAND.set(True);d.sync("Gear");TABLET_COMMAND.reset(token)
    assert messages[-1]["t"]=="state"
    d.last_utterance=("old",time.monotonic()-20);d.sync("Gear")
    assert messages[-1]["t"]=="state"

def test_threadsafe_broadcast():
    async def run():
        d=runtime();d.loop=asyncio.get_running_loop();q=asyncio.Queue();d.clients["a"]=q
        thread=threading.Thread(target=lambda:d.sync("Gear"));thread.start();thread.join()
        assert (await asyncio.wait_for(q.get(),1))["on"] is True
    asyncio.run(run())

def test_notes_only_flushed_on_request():
    d=runtime();d.add_note("Pressed gear")
    d.skill.wingman.conversation.add_note.assert_not_awaited()
    asyncio.run(d.flush_notes());d.skill.wingman.conversation.add_note.assert_awaited_once_with("Pressed gear")

def test_focus_fail_closed():
    assert not game_focused(lambda:(123,[(123,"StarCitizen.exe")]))
    assert not game_focused(lambda:(_ for _ in ()).throw(ValueError()))
    own=(os.getpid(),os.path.basename(sys.executable))
    assert game_focused(lambda:(123,[own,(123,"StarCitizen.exe")]))
    assert not game_focused(lambda:(124,[own,(123,"StarCitizen.exe")]))

def test_hold_release_up_disconnect_stop():
    d=runtime();d.clients["a"]=None;d.clients["b"]=None
    d.hold("a","look",d.manifest["buttons"]["look"])
    d.hold("b","look",d.manifest["buttons"]["look"])
    d.release("a","look");assert not any(e[0]=="up" for e in d.keyboard.events)
    d.disconnect("b")
    assert d.keyboard.events==[("down","alt gr"),("down","x"),("up","x"),("up","alt gr")]
    d.hold("a","look",d.manifest["buttons"]["look"]);d.stop();assert not d.held

def test_blocked_hold_changes_nothing():
    d=runtime();d.clients["a"]=None;d.focus=lambda:False;messages=[];d.broadcast=messages.append
    d.hold("a","look",d.manifest["buttons"]["look"])
    assert not d.held and not d.keyboard.events and not d.states and messages[0]["t"]=="blocked"

def test_queue_is_serial_off_loop_and_stops():
    d=runtime();d.clients["a"]=None;order=[];ids=[]
    async def invoke(*args):
        ids.append(threading.get_ident());order.append("start")
        await asyncio.sleep(.03);d.sync("Gear");order.append("end")
        return NS(response="",instant_response="")
    d.skill.wingman.tools.invoke=invoke
    main=threading.get_ident();d.start()
    for _ in range(3):d.receive("a",{"t":"tap","id":"gear"})
    until=time.monotonic()+2
    while len(order)<6 and time.monotonic()<until:time.sleep(.02)
    assert d.stop()
    assert order==["start","end"]*3 and all(i!=main for i in ids)
    assert d.states["gear"] is True

def test_confirmed_slider_bounded_hold_and_unconfirmed_tap_ignored():
    d=runtime();d.clients["a"]=None
    asyncio.run(d._execute("a","eject",d.manifest["buttons"]["eject"]))
    assert d.keyboard.events==[("down","x"),("up","x")]
    d.receive("a",{"t":"tap","id":"look"});assert d.jobs.empty()

def test_focus_rechecked_at_execution():
    d=runtime();d.clients["a"]=None;d.focus=lambda:False
    asyncio.run(d._execute("a","gear",d.manifest["buttons"]["gear"]))
    d.skill.wingman.tools.invoke.assert_not_awaited();assert not d.states

def test_mic_ownership_and_disconnect():
    d=runtime();calls=[];d.mic=calls.append
    d.receive("a",{"t":"voice","on":True});d.receive("b",{"t":"voice","on":False});d.disconnect("a")
    assert calls==[True,False]

def test_mic_http_only_loopback_named_core_routes():
    d=runtime();d.mic=d._mic
    response=NS(status=200,__enter__=lambda s:s,__exit__=lambda *a:None)
    from unittest.mock import MagicMock
    with patch("urllib.request.urlopen",return_value=MagicMock(__enter__=lambda s:NS(status=200))) as call:
        d._mic(True);d._mic(False)
        urls=[c.args[0].full_url for c in call.call_args_list]
        assert urls==["http://127.0.0.1:49111/start-recording-for-wingman?wingman_name=Pilot",
                      "http://127.0.0.1:49111/stop-recording-for-wingman?wingman_name=Pilot"]
        assert all(c.args[0].method=="POST" for c in call.call_args_list)

def test_feedback_coalesces_repeat_and_fallback():
    d=runtime();d.skill.deck_setting=lambda n,v:"ai" if n=="deck_feedback" else v
    f=d.feedback;b=d.manifest["buttons"]["gear"];f.submit(b);f.submit(b)
    assert len(f.pending)==1
    d.skill.wingman.ai.generate=AsyncMock(side_effect=ValueError())
    asyncio.run(f.deliver(f.pending))
    d.skill.wingman.tts.speak.assert_awaited_once_with("Confirmed.",interrupt=False)
    d.skill.wingman.conversation.show.assert_awaited_once()
    assert "Tablet feedback" in d.notes[-1]
    assert not f.speaking

def test_feedback_ai_word_limit():
    d=runtime();d.skill.deck_setting=lambda n,v:"ai"
    d.skill.wingman.ai.generate=AsyncMock(return_value="one "*25)
    asyncio.run(d.feedback.deliver([d.manifest["buttons"]["gear"]]))
    assert len(d.skill.wingman.tts.speak.call_args.args[0].split())==14

def test_feedback_busy_drops_speech_keeps_note(monkeypatch):
    d=runtime();d.skill.wingman.conversation.user_busy=True
    d.add_note("Pressed Gear")
    # Deadline is bounded; advancing clock makes the busy-drop test instantaneous.
    times=iter([0,9]);monkeypatch.setattr("sc_voice_cockpit.deck.runtime.time",NS(monotonic=lambda:next(times)))
    asyncio.run(d.feedback.deliver([d.manifest["buttons"]["gear"]]))
    d.skill.wingman.tts.speak.assert_not_awaited();assert list(d.notes)==["Pressed Gear"]

@pytest.fixture
def server(tmp_path,monkeypatch):
    monkeypatch.setattr(sharing,"get_lan_ip",lambda:"10.0.0.20")
    deck=runtime();db=NavPointDatabase(str(tmp_path))
    return CockpitServer(db,deck,tmp_path,port=0,share_on_lan=True,access_token="s"*24)

def client(server,peer="10.0.0.50",host="10.0.0.20"):
    return TestClient(server.served_app,base_url="http://"+host,client=(peer,50000),follow_redirects=False)

def test_pairing_persistence_and_lockout(server,tmp_path):
    c=client(server)
    assert c.get("/").status_code==200
    assert c.get("/layout.json").status_code==403
    for i in range(4):assert c.get("/layout.json?t=bad").status_code==403
    assert c.get("/layout.json?t=bad").status_code==429
    assert c.get("/layout.json?t="+server.pairing_code).status_code==429
    assert pairing_code(tmp_path)==server.pairing_code

def test_share_cookie_is_same_session_for_nav_and_deck(server):
    c=client(server);r=c.get("/?share="+"s"*24)
    assert r.status_code==303 and "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"]
    assert c.get("/layout.json").status_code==200
    nav=c.get("/nav");assert nav.status_code==200 and 'id="deck-link"' in nav.text
    assert c.get("/nav/static/app.js").status_code==200
    assert c.get("/api/nav/state").status_code==200

@pytest.mark.parametrize("peer,host",[
    ("8.8.8.8","10.0.0.20"),("10.0.0.50","attacker.example"),
    ("10.0.0.50","10.0.0.20.attacker"),("127.0.0.1","attacker.example")])
def test_bad_sources_hosts(server,peer,host):
    assert client(server,peer,host).get("/?share="+"s"*24).status_code==400

def test_source_spoof_does_not_bypass_pairing(server):
    assert client(server,host="127.0.0.1").get("/api/nav/state").status_code==403

def test_origin_and_allowlist(server):
    c=client(server);token="?t="+server.pairing_code
    assert c.delete("/api/nav/target"+token).status_code==403
    assert c.delete("/api/nav/target"+token,headers={"Origin":"http://10.0.0.20"}).status_code==200
    assert c.delete("/api/nav/target"+token,headers={"Origin":"http://10.0.0.20","Sec-Fetch-Site":"cross-site"}).status_code==403
    for path in ("/editor.html","/../main.py","/nav/static/../app.py","/main.py"):
        assert c.get(path+token).status_code==404

def test_websocket_protocol_and_origin(server):
    with client(server) as c:
        with c.websocket_connect("/ws?t="+server.pairing_code,headers={"Origin":"http://10.0.0.20","Host":"10.0.0.20"}) as ws:
            assert ws.receive_json()["t"]=="hello"
            ws.send_json({"t":"reset-states"})
            while True:
                msg=ws.receive_json()
                if msg["t"]=="states":break
            assert msg["states"]=={}
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws?t="+server.pairing_code,headers={"Origin":"http://evil.example"}):pass

def test_port_freed_and_conflict_nonfatal(server):
    assert server.start()
    port=server._port
    try:
        with socket.socket() as s:
            with pytest.raises(OSError):s.bind(("0.0.0.0",port))
    finally:server.stop()
    with socket.socket() as s:
        s.bind(("0.0.0.0",port));s.listen()
        server._port=port
        assert server.start() is False
