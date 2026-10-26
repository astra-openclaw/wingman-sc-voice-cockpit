"""Ring monitor-following qualification using only mocked Win32 APIs."""
import atexit
import ctypes
import importlib.util
from pathlib import Path
import sys
from unittest.mock import MagicMock
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def win32(monkeypatch):
    monkeypatch.setattr(ctypes,'WINFUNCTYPE',ctypes.CFUNCTYPE,raising=False)
    dlls={n:MagicMock() for n in ('user32.dll','gdi32.dll','kernel32.dll')}
    monkeypatch.setattr(ctypes,'WinDLL',lambda n,**kw:dlls[n],raising=False)
    spec=importlib.util.spec_from_file_location('synthetic_ring_win32',ROOT/'sc_voice_cockpit/navpoint/overlay_win32.py')
    module=importlib.util.module_from_spec(spec)
    registered=[]
    with monkeypatch.context() as m:
        m.setattr(sys,'platform','win32')
        real=atexit.register
        m.setattr(atexit,'register',lambda fn:(registered.append(fn),real(fn))[1])
        spec.loader.exec_module(module)
    yield module
    for fn in registered:atexit.unregister(fn)

def set_window(win,title='Star Citizen',hwnd=0x123456789,monitor=0x987654321,rect=(0,0,5120,1440)):
    u=win.user32
    u.GetForegroundWindow.return_value=hwnd
    def text(handle,buffer,size):buffer.value=title;return len(title)
    u.GetWindowTextW.side_effect=text
    u.FindWindowW.return_value=0
    u.MonitorFromWindow.return_value=monitor
    def info(handle,ptr):
        assert handle==monitor
        r=ptr._obj.rcMonitor;r.left,r.top,r.right,r.bottom=rect
        return 1
    u.GetMonitorInfoW.side_effect=info
    return u

def load_ring(monkeypatch,win):
    from sc_voice_cockpit import navpoint
    monkeypatch.setattr(navpoint,'overlay_win32',win,raising=False)
    monkeypatch.setitem(sys.modules,'sc_voice_cockpit.navpoint.overlay_win32',win)
    spec=importlib.util.spec_from_file_location('synthetic_ring_renderer',ROOT/'sc_voice_cockpit/navpoint/ring_overlay.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

def test_foreground_game_monitor_overrides_configured_second_monitor(monkeypatch,win32):
    u=set_window(win32,title='STAR CITIZEN - LIVE')
    ring=load_ring(monkeypatch,win32).RingOverlay(size=220,display=2)
    fallback=MagicMock(return_value=(5120,0,1280,720))
    monkeypatch.setattr(win32,'get_monitor_rect',fallback)
    assert ring._placement()==(2450,0)
    u.MonitorFromWindow.assert_called_once_with(0x123456789,2)
    u.FindWindowW.assert_not_called();fallback.assert_not_called()
    assert u.MonitorFromWindow.restype is ctypes.wintypes.HMONITOR

def test_background_game_findwindow_and_negative_monitor_origin(monkeypatch,win32):
    u=set_window(win32,title='Other application',rect=(-2560,-100,0,1340))
    u.FindWindowW.return_value=0x222222222
    assert win32.get_game_monitor_rect()==(-2560,-100,2560,1440)
    u.FindWindowW.assert_called_once_with(None,'Star Citizen')
    u.MonitorFromWindow.assert_called_once_with(0x222222222,2)

@pytest.mark.parametrize('failure',['no-window','no-monitor','info-failed','invalid-rect','exception'])
def test_missing_game_or_api_failure_uses_display_without_raising(monkeypatch,win32,failure):
    u=set_window(win32)
    if failure=='no-window':
        u.GetForegroundWindow.return_value=0
    elif failure=='no-monitor':u.MonitorFromWindow.return_value=0
    elif failure=='info-failed':u.GetMonitorInfoW.side_effect=lambda *a:0
    elif failure=='invalid-rect':set_window(win32,rect=(0,0,0,720))
    else:u.MonitorFromWindow.side_effect=OSError('synthetic failure')
    assert win32.get_game_monitor_rect() is None
    fallback=MagicMock(return_value=(5120,100,1280,720))
    monkeypatch.setattr(win32,'get_monitor_rect',fallback)
    ring=load_ring(monkeypatch,win32).RingOverlay(size=220,display=2)
    assert ring._placement()==(5650,100)
    fallback.assert_called_once_with(2)

def test_even_fallback_failure_does_not_escape_overlay_placement(monkeypatch,win32):
    monkeypatch.setattr(win32,'get_game_monitor_rect',lambda:None)
    monkeypatch.setattr(win32,'get_monitor_rect',MagicMock(side_effect=RuntimeError('monitor hotplug')))
    ring=load_ring(monkeypatch,win32).RingOverlay(size=220,display=2)
    assert ring._placement()==(850,0)

def test_reshow_repositions_without_waiting_for_periodic_interval(monkeypatch,win32):
    module=load_ring(monkeypatch,win32);ring=module.RingOverlay()
    renderer=MagicMock();window=MagicMock();window.ok=True
    monkeypatch.setattr(module,'RingRenderer',lambda size:renderer)
    monkeypatch.setattr(win32,'LayeredWindow',lambda *a:window)
    monkeypatch.setattr(win32,'pump_messages',lambda:None)
    monkeypatch.setattr(module.time,'monotonic',lambda:0.)
    ring._placement=MagicMock(side_effect=[(0,0),(10,0),(20,0)])
    ring._active=True;shown=[]
    ring._wake.wait=MagicMock()
    def show():
        shown.append(True)
        if len(shown)==1:ring._active=False
        else:ring._stop.set()
    window.show.side_effect=show
    window.hide.side_effect=lambda:setattr(ring,'_active',True)
    ring._run()
    assert ring._placement.call_count==3
    assert [call.args[-2:] for call in window.update.call_args_list]==[(10,0),(20,0)]
    window.destroy.assert_called_once()
    assert ring.failed_reason is None
