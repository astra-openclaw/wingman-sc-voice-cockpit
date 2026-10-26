"""Regression cases found while reviewing the cancelled implementation."""
import asyncio
import threading
import time
from unittest.mock import AsyncMock
import pytest
from starlette.websockets import WebSocketDisconnect
from test_deck_runtime import runtime, server, client


def test_typed_pairing_authenticates_plain_nav_link(server):
    c=client(server)
    response=c.get("/layout.json?t="+server.pairing_code)
    assert response.status_code==200 and "HttpOnly" in response.headers["set-cookie"]
    assert c.get("/nav").status_code==200
    assert c.get("/nav/static/app.js").status_code==200
    with c.websocket_connect("ws://10.0.0.20/ws",headers={"Origin":"http://10.0.0.20"}) as ws:
        assert ws.receive_json()["t"]=="hello"


def test_heartbeat_expiry_releases_keys_and_microphone(server):
    d=server.deck
    mic=[];d.mic=mic.append
    server.heartbeat_timeout=.15
    # Use a one-key hold so the actual AltGr delay does not consume the lease.
    d.manifest["buttons"]["look"]["hold"]["input"]="kb1_comma"
    with client(server) as c:
        with c.websocket_connect("ws://10.0.0.20/ws?t="+server.pairing_code,headers={"Origin":"http://10.0.0.20"}) as ws:
            assert ws.receive_json()["t"]=="hello"
            ws.send_json({"t":"down","id":"look"})
            ws.send_json({"t":"voice","on":True})
            with pytest.raises(WebSocketDisconnect):
                while True:ws.receive_json()
    assert not d.clients and not d.held
    assert d.keyboard.events==[("down",","),("up",",")]
    assert mic==[True,False]


def test_unload_closes_input_admission():
    d=runtime();d.clients["a"]=None;d.stop()
    d.receive("a",{"t":"down","id":"look"})
    d.hold("a","look",d.manifest["buttons"]["look"])
    assert not d.keyboard.events and d.jobs.qsize()==1  # stop sentinel only


def test_nav_is_available_when_other_desktop_app_focused():
    d=runtime();d.clients["a"]=None;d.focus=lambda:False
    asyncio.run(d._execute("a","nav",d.manifest["buttons"]["nav"]))
    d.skill.deck_nav.assert_awaited_once_with("clear_target")


def test_disabled_deck_does_not_dispatch(server):
    d=server.deck
    d.skill.deck_setting=lambda n,v:False if n=="deck_enabled" else v
    assert client(server).get("/").headers["location"]=="/nav"
    with pytest.raises(WebSocketDisconnect):
        with client(server).websocket_connect("/ws?t="+server.pairing_code,headers={"Origin":"http://10.0.0.20"}):pass
    d.receive("a",{"t":"tap","id":"gear"})
    assert d.jobs.empty()


def test_feedback_survives_provider_failure():
    d=runtime();f=d.feedback
    d.skill.deck_setting=lambda n,v:"speak" if n=="deck_feedback" else v
    d.skill.wingman.tts.speak=AsyncMock(side_effect=[OSError("offline"),None])
    f.start()
    try:
        with f.condition:
            f.pending=[d.manifest["buttons"]["gear"]];f.due=0;f.condition.notify()
        deadline=time.monotonic()+2
        while d.skill.wingman.tts.speak.await_count<1 and time.monotonic()<deadline:time.sleep(.01)
        assert f.thread.is_alive()
        with f.condition:
            f.pending=[d.manifest["buttons"]["gear"]];f.due=0;f.condition.notify()
        deadline=time.monotonic()+2
        while d.skill.wingman.tts.speak.await_count<2 and time.monotonic()<deadline:time.sleep(.01)
        assert d.skill.wingman.tts.speak.await_count==2
    finally:f.stop()


def test_unprepared_layout_is_renderable_and_can_connect_for_reload(server):
    server.deck.manifest={}
    c=client(server)
    layout=c.get('/layout.json?t='+server.pairing_code).json()
    assert layout['pages'][0]['buttons']==[]
    with c.websocket_connect('ws://10.0.0.20/ws',headers={'Origin':'http://10.0.0.20'}) as ws:
        assert ws.receive_json()['t']=='hello'
        server.deck.broadcast({'t':'reload'})
        assert ws.receive_json()['t']=='reload'


def test_feedback_stop_cancels_inflight_generation():
    d=runtime();started=threading.Event();cancelled=threading.Event()
    d.skill.deck_setting=lambda n,v:'ai' if n=='deck_feedback' else v
    async def generate(*args,**kwargs):
        started.set()
        try:await asyncio.sleep(60)
        finally:cancelled.set()
    d.skill.wingman.ai.generate=generate
    f=d.feedback;f.start()
    with f.condition:
        f.pending=[d.manifest['buttons']['gear']];f.due=0;f.condition.notify()
    assert started.wait(2)
    f.stop()
    assert cancelled.is_set() and not f.thread.is_alive()
    d.skill.wingman.tts.speak.assert_not_awaited()


def test_new_tablet_sees_already_listening_microphone(server):
    server.deck.listening=True
    with client(server).websocket_connect('ws://10.0.0.20/ws?t='+server.pairing_code,headers={'Origin':'http://10.0.0.20'}) as ws:
        assert ws.receive_json()['t']=='hello'
        assert ws.receive_json()=={'t':'listening','on':True}


@pytest.mark.parametrize("raw,modifier", [
    ("rctrl", "right ctrl"), ("ralt", "alt gr"), ("rshift", "right shift"),
    ("lctrl", "ctrl"), ("lalt", "alt"), ("lshift", "shift"),
])
def test_hold_modifier_timing_and_extended_scan_codes(monkeypatch,raw,modifier):
    from types import SimpleNamespace as NS
    d=runtime();d.clients["tablet"]=None;events=[]
    d.keyboard.press=lambda key:events.append(("down",key))
    d.keyboard.release=lambda key:events.append(("up",key))
    d.keyboard.direct_event=lambda code,flags:events.append(("scan",code,flags))
    monkeypatch.setattr("sc_voice_cockpit.deck.runtime.time",NS(sleep=lambda seconds:events.append(("sleep",seconds))))
    button={'hold':{'input':'kb1_'+raw+'+w'}}
    d.hold('tablet','laser',button)
    d.release('tablet','laser')
    down=("scan",29,1) if raw=="rctrl" else ("down",modifier)
    up=("scan",29,3) if raw=="rctrl" else ("up",modifier)
    assert events==[down,("sleep",.35),("down","w"),("up","w"),("sleep",.1),up]


def test_hold_settles_each_modifier_in_combo(monkeypatch):
    from types import SimpleNamespace as NS
    d=runtime();d.clients["tablet"]=None;events=[]
    d.keyboard.press=lambda key:events.append(("down",key))
    d.keyboard.release=lambda key:events.append(("up",key))
    d.keyboard.direct_event=lambda code,flags:events.append(("scan",code,flags))
    monkeypatch.setattr("sc_voice_cockpit.deck.runtime.time",NS(sleep=lambda seconds:events.append(("sleep",seconds))))
    d.hold("tablet","combo",{"hold":{"input":"kb1_rctrl+rshift+w"}})
    d.release("tablet","combo")
    assert events==[("scan",29,1),("sleep",.35),("down","right shift"),("sleep",.35),
                    ("down","w"),("up","w"),("sleep",.1),("up","right shift"),("sleep",.1),("scan",29,3)]


@pytest.mark.parametrize("cleanup", ["up", "disconnect", "stop"])
def test_hold_settle_leaves_state_messages_free_and_cleanup_serialized(monkeypatch,cleanup):
    from types import SimpleNamespace as NS
    d=runtime();d.clients["a"]=None;d.clients["b"]=None
    settling=threading.Event();resume=threading.Event();synced=threading.Event();cleaned=threading.Event()
    def sleep(seconds):
        if seconds==.35:
            settling.set();assert resume.wait(2)
    monkeypatch.setattr("sc_voice_cockpit.deck.runtime.time",NS(sleep=sleep,monotonic=time.monotonic))
    holder=threading.Thread(target=lambda:d.hold("a","look",d.manifest["buttons"]["look"]))
    state=threading.Thread(target=lambda:(d.sync("Gear"),synced.set()))
    def finish():
        if cleanup=="up":d.receive("a",{"t":"up","id":"look"})
        elif cleanup=="disconnect":d.disconnect("a")
        else:d.stop()
        cleaned.set()
    closer=threading.Thread(target=finish)
    holder.start()
    try:
        assert settling.wait(1)
        state.start();assert synced.wait(1)
        closer.start()
        assert not cleaned.wait(.02)  # release must not overtake the in-progress down
    finally:
        resume.set()
        for worker in (holder,state,closer):
            if worker.ident is not None:worker.join(2)
    assert all(not worker.is_alive() for worker in (holder,state,closer))
    assert cleaned.is_set() and not d.held and not d.key_owners
    pressed=[key for event,key in d.keyboard.events if event=="down"]
    released=[key for event,key in d.keyboard.events if event=="up"]
    assert pressed==list(reversed(released))
    assert d.keyboard.events[-1]==("up","alt gr")


def test_disconnect_between_queued_dispatch_and_timed_hold_does_not_press():
    d=runtime();d.clients['a']=None
    reached=threading.Event();resume=threading.Event()
    def focus():
        reached.set();assert resume.wait(2);return True
    d.focus=focus
    worker=threading.Thread(target=lambda:asyncio.run(d._execute('a','eject',d.manifest['buttons']['eject'])))
    worker.start();assert reached.wait(2)
    d.disconnect('a');resume.set();worker.join(2)
    assert not worker.is_alive() and not d.keyboard.events and not d.held


@pytest.mark.parametrize("path", ["/", "/app.js"])
def test_public_pairing_attempts_obey_lockout(server,path):
    c=client(server)
    for _ in range(4):
        response=c.get(path+"?t=bad")
        assert response.status_code==200 and "set-cookie" not in response.headers
    response=c.get(path+"?t=bad")
    assert response.status_code==429 and "set-cookie" not in response.headers
    response=c.get("/?t="+server.pairing_code)
    assert response.status_code==429 and "set-cookie" not in response.headers
    assert c.get("/").status_code==200  # the plain pairing page remains public


@pytest.mark.parametrize("path", ["/", "/app.js"])
def test_public_pairing_success_sets_authenticated_cookie(server,path):
    c=client(server)
    response=c.get(path+"?t="+server.pairing_code)
    assert response.status_code==200
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    assert c.get("/layout.json").status_code==200


@pytest.mark.parametrize("source", ["receive", "execute"])
def test_open_dashboard_only_reaches_requesting_client(source):
    async def run():
        d=runtime();d.loop=asyncio.get_running_loop()
        d.clients={"a":asyncio.Queue(),"b":asyncio.Queue()}
        if source=="receive":
            await asyncio.to_thread(d.receive,"a",{"t":"open"})
        else:
            await d._execute("a","dashboard",{"kind":"nav","nav":"open_dashboard"})
        assert await asyncio.wait_for(d.clients["a"].get(),1)=={"t":"opened","name":"Nav Dashboard","url":"/nav"}
        await asyncio.sleep(0)
        assert d.clients["b"].empty() and d.clients["a"].empty()
    asyncio.run(run())
