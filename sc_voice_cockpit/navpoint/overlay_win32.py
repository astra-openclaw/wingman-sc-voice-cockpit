"""overlay_win32.py: Win32 primitives for the NavPoint ring overlay.
Author: Mallachi

Vendored rather than imported from Wingman's own `hud_server.platform.win32`.
That module is core code: it imports core services (`services.printr`,
`api.enums`) and it differs between the 3.1.2 dev tree and the 2.1.1 build that
actually ships, so a skill reaching into it takes on a dependency it cannot
verify. The window mechanics here follow its proven shape (a `WS_POPUP` window
with `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW |
WS_EX_NOACTIVATE`, a private message pump, and topmost re-assertion).

The compositing deliberately does NOT follow it. The core blits through a
magenta colour key, which can only express fully opaque or fully absent pixels;
the ring is antialiased and carries a soft glow, and a colour key would fringe
every curved edge. This uses `UpdateLayeredWindow` with premultiplied per-pixel
alpha instead, which is the only path that renders a translucent glow correctly
over a moving game image.

Nothing here draws: callers hand in finished premultiplied BGRA bytes.
"""

from __future__ import annotations

import atexit
import ctypes
import uuid
import logging
import sys
from ctypes import wintypes


logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# ── Constants ──────────────────────────────────────────────────────────────── #

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

PM_REMOVE = 0x0001

BI_RGB = 0
DIB_RGB_COLORS = 0

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

MONITORINFOF_PRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2

# Fallback dimensions used only when the monitor APIs return nothing at all.
_FALLBACK_MONITOR = (0, 0, 1920, 1080)


if IS_WINDOWS:
    # Fresh WinDLL instances so the argtypes set below cannot collide with any
    # other module that also pokes at windll.
    user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
else:  # pragma: no cover - the overlay is Windows-only, the import is not
    user32 = gdi32 = kernel32 = None


if ctypes.sizeof(ctypes.c_void_p) == 8:
    WPARAM = ctypes.c_uint64
    LPARAM = ctypes.c_int64
    LRESULT = ctypes.c_int64
else:  # pragma: no cover - 32-bit hosts are not a supported target
    WPARAM = ctypes.c_uint
    LPARAM = ctypes.c_long
    LRESULT = ctypes.c_long

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", ctypes.c_uint),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    LPARAM,
)


def _declare_prototypes() -> None:
    """Pin argument and return types.

    Return types matter more than argument types here: ctypes defaults every
    restype to a 32-bit int, which silently truncates the 64-bit handles that
    CreateWindowExW, GetDC and CreateDIBSection hand back.
    """
    user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(POINT),
        ctypes.POINTER(SIZE),
        wintypes.HDC,
        ctypes.POINTER(POINT),
        wintypes.COLORREF,
        ctypes.POINTER(BLENDFUNCTION),
        wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.c_void_p]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
    user32.DispatchMessageW.restype = LRESULT
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        MONITORENUMPROC,
        LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


if IS_WINDOWS:
    _declare_prototypes()
    HWND_TOPMOST = wintypes.HWND(-1)
else:  # pragma: no cover
    HWND_TOPMOST = None


# ── Window class ───────────────────────────────────────────────────────────── #


def _wnd_proc(hwnd, msg, wparam, lparam):
    try:
        return user32.DefWindowProcW(hwnd, msg, WPARAM(wparam), LPARAM(lparam))
    except Exception:  # pragma: no cover - a raising wndproc would crash the host
        return 0


# Wingman re-executes modules during config reload. Native window classes live
# until process exit, beyond the lifetime of a module object. Each execution
# gets its own namespace; atexit's process-owned reference retains the callback
# even after unload/GC. Never accept an existing class with an unknown WNDPROC.
_CLASS_NAMESPACE = uuid.uuid4().hex
_WND_PROC_CALLBACK = WNDPROC(_wnd_proc) if IS_WINDOWS else None
if _WND_PROC_CALLBACK is not None:
    atexit.register(lambda callback=_WND_PROC_CALLBACK: None)
_REGISTERED_CLASSES: set[str] = set()


def _class_name(base: str) -> str:
    return f"{base}_{_CLASS_NAMESPACE}"


def ensure_window_class(class_name: str) -> bool:
    """Register once per module execution, using its unique native name."""
    if not IS_WINDOWS:
        return False
    if class_name in _REGISTERED_CLASSES:
        return True
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _WND_PROC_CALLBACK
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = _class_name(class_name)
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        logger.warning(
            "RegisterClassExW failed for %s (error %d)",
            class_name,
            ctypes.get_last_error(),
        )
        return False
    _REGISTERED_CLASSES.add(class_name)
    return True


# ── Monitors ───────────────────────────────────────────────────────────────── #


def get_game_monitor_rect() -> tuple[int, int, int, int] | None:
    """Locate the game's monitor through documented Win32 APIs, without focus/input.

    Prefer the foreground title match used by the specialist capture reader;
    FindWindow covers the normal game title while another app is foreground.
    MonitorFromWindow also follows a moved/minimized game's last window rectangle.
    Absence, invalid geometry and API errors return None for the configured fallback.
    """
    if not IS_WINDOWS:
        return None
    try:
        hwnd = user32.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(512)
        if hwnd:
            user32.GetWindowTextW(hwnd, title, len(title))
        if not hwnd or "star citizen" not in title.value.lower():
            hwnd = user32.FindWindowW(None, "Star Citizen")
        if not hwnd:
            return None
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        r = info.rcMonitor
        if r.right <= r.left or r.bottom <= r.top:
            return None
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        # A monitor hotplug or a disappearing window must not kill the ring thread.
        return None


def get_monitor_rect(index: int = 1) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) for a 1-based monitor index.

    Out-of-range indexes fall back to the last monitor rather than raising: an
    overlay on the wrong screen is recoverable, a crashed skill is not.
    """
    if not IS_WINDOWS:
        return _FALLBACK_MONITOR

    monitors: list[tuple[int, int, int, int]] = []

    def _callback(hmonitor, hdc, lprect, lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    try:
        callback = MONITORENUMPROC(_callback)
        user32.EnumDisplayMonitors(None, None, callback, 0)
    except OSError as e:
        logger.warning("EnumDisplayMonitors failed: %s", e)

    if not monitors:
        return _FALLBACK_MONITOR
    i = max(1, int(index)) - 1
    return monitors[i] if i < len(monitors) else monitors[-1]


# ── The window itself ──────────────────────────────────────────────────────── #


class LayeredWindow:
    """A click-through, always-on-top, per-pixel-alpha window.

    One `UpdateLayeredWindow` call sets position, size and pixels together, so
    there is no window-moved-but-not-yet-redrawn frame to tear on.
    """

    def __init__(self, class_name: str, title: str) -> None:
        self._hwnd = None
        self._screen_dc = None
        self._mem_dc = None
        self._bitmap = None
        self._old_bitmap = None
        self._bits = None
        self._dib_size: tuple[int, int] = (0, 0)
        self._visible = False

        if not IS_WINDOWS or not ensure_window_class(class_name):
            return

        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )
        hwnd = user32.CreateWindowExW(
            ex_style,
            _class_name(class_name),
            title,
            WS_POPUP,
            0,
            0,
            1,
            1,
            None,
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            logger.warning(
                "CreateWindowExW failed for %s (error %d)",
                class_name,
                ctypes.get_last_error(),
            )
            return

        self._hwnd = hwnd
        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)

    @property
    def ok(self) -> bool:
        return bool(self._hwnd and self._screen_dc and self._mem_dc)

    @property
    def visible(self) -> bool:
        return self._visible

    def _ensure_dib(self, width: int, height: int) -> bool:
        if self._dib_size == (width, height) and self._bits:
            return True

        self._release_dib()

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # negative = top-down rows
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            self._mem_dc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        if not bitmap or not bits:
            logger.warning("CreateDIBSection failed (%dx%d)", width, height)
            return False

        self._bitmap = bitmap
        self._bits = bits
        self._old_bitmap = gdi32.SelectObject(self._mem_dc, bitmap)
        self._dib_size = (width, height)
        return True

    def _release_dib(self) -> None:
        if self._old_bitmap and self._mem_dc:
            gdi32.SelectObject(self._mem_dc, self._old_bitmap)
            self._old_bitmap = None
        if self._bitmap:
            gdi32.DeleteObject(self._bitmap)
            self._bitmap = None
        self._bits = None
        self._dib_size = (0, 0)

    def update(self, bgra: bytes, width: int, height: int, x: int, y: int) -> bool:
        """Push one frame. `bgra` must be premultiplied, top-down, 4 bytes/px."""
        if not self.ok or not self._ensure_dib(width, height):
            return False
        expected = width * height * 4
        if len(bgra) != expected:
            logger.warning(
                "Frame size mismatch: %d bytes for %dx%d", len(bgra), width, height
            )
            return False

        ctypes.memmove(self._bits, bgra, expected)

        pt_dst = POINT(int(x), int(y))
        size = SIZE(width, height)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(
            self._hwnd,
            self._screen_dc,
            ctypes.byref(pt_dst),
            ctypes.byref(size),
            self._mem_dc,
            ctypes.byref(pt_src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )
        if not ok:
            logger.debug("UpdateLayeredWindow failed (%d)", ctypes.get_last_error())
        return bool(ok)

    def show(self) -> None:
        if not self.ok or self._visible:
            return
        user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
        self.force_on_top()
        self._visible = True

    def hide(self) -> None:
        if not self.ok or not self._visible:
            return
        user32.ShowWindow(self._hwnd, SW_HIDE)
        self._visible = False

    def force_on_top(self) -> None:
        """Re-assert topmost.

        A game going fullscreen, or any other topmost window appearing, can
        push the overlay down the Z order; nothing tells us when, so callers
        re-assert on a timer. SWP_NOACTIVATE keeps focus in the game.
        """
        if not self.ok:
            return
        user32.SetWindowPos(
            self._hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def destroy(self) -> None:
        if not IS_WINDOWS:
            return
        self._release_dib()
        if self._mem_dc:
            gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = None
        if self._screen_dc:
            user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        self._visible = False


def pump_messages() -> None:
    """Drain this thread's message queue.

    A window whose queue is never drained is marked unresponsive by Windows,
    which is enough to make the compositor stop trusting it.
    """
    if not IS_WINDOWS:
        return
    msg = MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
