"""1.1 server contracts; no PC, Core HTTP or real input."""
import asyncio
import ctypes
import gc
import importlib.util
from pathlib import Path
import sys
import time
import weakref
import atexit
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import pytest
from test_deck_runtime import server, client

ROOT = Path(__file__).resolve().parents[1]


def add(server, name="Synthetic"):
    return server._db.add_navpoint(name,"synthetic","Daymar",1000,0,295000)


def test_revision_changes_for_all_writers_not_position(server):
    first = asyncio.run(server.nav_snapshot())
    wp = add(server)
    added = asyncio.run(server.nav_snapshot())
    assert first["navpoints_revision"] != added["navpoints_revision"]
    server.set_position({"x":0,"y":0,"z":295000,"body":"Daymar","captured_at":time.time()})
    pos = asyncio.run(server.nav_snapshot())
    assert pos["navpoints_revision"] == added["navpoints_revision"]
    server.set_active_target(wp)
    server._db.rename_navpoint(wp.id,"Renamed")
    renamed = asyncio.run(server.nav_snapshot())
    assert renamed["navpoints_revision"] != pos["navpoints_revision"]
    assert renamed["active_target"]["name"] == "Renamed"
    server._db.update_notes(wp.id,"Notes")
    notes = asyncio.run(server.nav_snapshot())
    assert notes["navpoints_revision"] != renamed["navpoints_revision"]
    assert notes["active_target"]["notes"] == "Notes"
    notified = []
    server._on_target_change = notified.append
    server._db.delete_navpoint(wp.id)
    deleted = asyncio.run(server.nav_snapshot())
    assert deleted["active_target"] is None and notified == [None]
    assert deleted["navpoints_revision"] == first["navpoints_revision"]


def test_nav_revision_is_pushed(server):
    with client(server) as c:
        with c.websocket_connect("/ws?t="+server.pairing_code,headers={"Origin":"http://10.0.0.20", "Host":"10.0.0.20"}) as ws:
            assert ws.receive_json()["t"] == "hello"
            # Wait for a genuinely periodic push, not a test-injected message.
            first = ws.receive_json()
            assert first["t"] == "nav" and first["state"]["navpoints_revision"]
            wp = add(server)
            start = time.monotonic()
            while True:
                msg = ws.receive_json()
                if msg["t"] == "nav" and msg["state"]["navpoints_revision"] != first["state"]["navpoints_revision"]:break
            assert time.monotonic()-start < 2


@pytest.mark.parametrize("path,method,body",[
    ("/api/navpoints","GET",None),
    ("/api/nav/state","GET",None),
    ("/api/nav/target/{id}","POST",None),
    ("/api/nav/target","DELETE",None),
    ("/api/navpoints/{id}/name","PUT",{"name":"New Name"}),
    ("/api/navpoints/{id}/notes","PUT",{"notes":"Updated"}),
    ("/api/navpoints/{id}","DELETE",None),
])
def test_nav_api_code_auth_without_cookies_and_origin(server,path,method,body):
    wp = add(server); path=path.format(id=wp.id)
    def call(token="",origin="http://10.0.0.20",site="same-origin"):
        # New client per request: no cookie may hide a missing t.
        c = client(server)
        return c.request(method,path+token,json=body,headers={"Origin":origin,"Sec-Fetch-Site":site})
    assert call().status_code == 403
    if method != "GET":
        assert call("?t="+server.pairing_code,"http://evil.example").status_code == 403
        assert call("?t="+server.pairing_code,site="cross-site").status_code == 403
    assert call("?t="+server.pairing_code).status_code == 200


def test_new_assets_exact_allowlist_and_durable_link(server):
    assert server.pairing_url == "http://10.0.0.20:0/?t="+server.pairing_code
    assert "?share=" in server.lan_url  # old sessions retained
    c = client(server)
    for asset in ("navdesk.js","navdesk.css"):
        assert c.get("/"+asset).status_code == 200
        assert c.get("/"+asset+".bak?t="+server.pairing_code).status_code == 404
    server._lan_host = ""
    assert server.pairing_url == server.url+"/?t="+server.pairing_code


def test_overlay_module_reexecution_retains_native_callback(monkeypatch):
    # Full module execution twice, with a Win32-like registry rejecting names
    # already registered. Native callbacks must remain callable after module GC.
    monkeypatch.setattr(ctypes,"WINFUNCTYPE",ctypes.CFUNCTYPE,raising=False)
    monkeypatch.setattr(ctypes,"get_last_error",lambda:1410,raising=False)
    dlls = {name: MagicMock() for name in ("user32.dll","gdi32.dll","kernel32.dll")}
    monkeypatch.setattr(ctypes,"WinDLL",lambda name,**kw:dlls[name],raising=False)
    u,g,k = (dlls[name] for name in ("user32.dll","gdi32.dll","kernel32.dll"))
    registered = set(); windows = []
    def register(ptr):
        name=ptr._obj.lpszClassName
        if name in registered:return 0
        registered.add(name);return 1
    u.RegisterClassExW.side_effect=register
    def create(*args):
        assert args[1] in registered
        windows.append(args[1]);return len(windows)
    u.CreateWindowExW.side_effect=create
    u.DefWindowProcW.return_value=17;u.GetDC.return_value=2
    k.GetModuleHandleW.return_value=3;g.CreateCompatibleDC.return_value=4
    callbacks=[];weak=[]
    # Avoid platform-dependent imports outside this narrow execution block.
    with monkeypatch.context() as patch:
        patch.setattr(sys,"platform","win32")
        for n in range(2):
            spec=importlib.util.spec_from_file_location("synthetic_win32_"+str(n),ROOT/"sc_voice_cockpit/navpoint/overlay_win32.py")
            module=importlib.util.module_from_spec(spec)
            # Track real atexit registrations so this test can remove its two
            # synthetic callbacks without changing production ownership.
            real=atexit.register
            def retain(fn):
                callbacks.append(fn);return real(fn)
            with patch.context() as p:
                p.setattr(atexit,"register",retain);spec.loader.exec_module(module)
            window=module.LayeredWindow("SyntheticClass","Synthetic title")
            assert window.ok
            assert module.ensure_window_class("SyntheticClass")
            weak.append(weakref.ref(module._WND_PROC_CALLBACK))
            window.destroy();window.destroy()
            del window,module
    try:
        gc.collect()
        assert len(registered)==2 and len(set(windows))==2
        assert u.RegisterClassExW.call_count==2
        assert u.DestroyWindow.call_count==2  # idempotent destroy on unload
        assert all(ref() is not None and ref()(1,0,0,0)==17 for ref in weak)
    finally:
        for fn in callbacks:atexit.unregister(fn)


def test_drift_summary_logs_count_and_bounded_sample():
    # Reuse the existing isolated public Skill adapter, no genuine Core import.
    with patch.object(sys,"path",[str(ROOT/"tests/keybindings"),*sys.path]):
        from test_behavior import adapter
    obj=adapter().SC_Keybindings()
    obj.log=NS(info=MagicMock())
    rows=[{"name":"Synthetic "+str(n),"verified":False} for n in range(80)]
    obj._log_unverifiable(rows)
    assert obj.log.info.call_count == 1
    line=obj.log.info.call_args.args[0]
    assert "80 commands" in line and "Synthetic 2" in line and "Synthetic 3" not in line
    obj._log_unverifiable([{"name":"OK","verified":True}])
    assert obj.log.info.call_count == 1


def test_every_layout_uses_shared_desk_no_focus_taking_controls():
    import json
    from sc_voice_cockpit.deck_pack import plan_pack
    from sample_index import synthetic_index
    raw=synthetic_index()
    plan=plan_pack(raw,None,[],[],{},{"emotes":True})
    buttons=plan["deck"]["buttons"]
    assert buttons["nav-mark"]["nav"] == "mark_location" and not buttons["nav-mark"]["guard"]
    assert buttons["nav-clear"]["nav"] == "clear_target" and not buttons["nav-clear"]["guard"]
    assert "nav-dashboard" not in buttons and "nav-hud" not in buttons
    for layout in plan["deck"]["layouts"]["files"].values():
        if "canvas" in layout:
            assert layout["regions"][1]["panels"][-2:] == ["nav","waypoints"]
            assert layout["panels"]["nav"]["navdesk"] == "nav"
            assert layout["panels"]["waypoints"]["navdesk"] == "waypoints"
        else:assert layout["pages"][-1]["navdesk"] == "both"
