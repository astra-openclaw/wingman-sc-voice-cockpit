"""Foreground identity via Win32 + self-checked Toolhelp snapshot; fail closed."""
import ctypes
import os
import sys

def game_focused(probe=None):
    try:
        if probe is not None:
            pid, entries=probe()
        else:
            if sys.platform!="win32": return False
            from ..keybindings.self_heal import PROCESSENTRY32W
            user=ctypes.WinDLL("user32",use_last_error=True)
            kernel=ctypes.WinDLL("kernel32",use_last_error=True)
            user.GetForegroundWindow.restype=ctypes.c_void_p
            user.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
            user.GetWindowThreadProcessId.restype=ctypes.c_uint32
            window=user.GetForegroundWindow()
            if not window:return False
            value=ctypes.c_uint32()
            if not user.GetWindowThreadProcessId(window,ctypes.byref(value)):return False
            pid=value.value
            kernel.CreateToolhelp32Snapshot.argtypes=[ctypes.c_uint32,ctypes.c_uint32]
            kernel.CreateToolhelp32Snapshot.restype=ctypes.c_void_p
            kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            for name in ("Process32FirstW","Process32NextW"):
                f=getattr(kernel,name)
                f.argtypes=[ctypes.c_void_p,ctypes.POINTER(PROCESSENTRY32W)]
                f.restype=ctypes.c_int32
            handle=kernel.CreateToolhelp32Snapshot(2,0)
            if handle in (None,ctypes.c_void_p(-1).value):return False
            try:
                row=PROCESSENTRY32W();row.dwSize=ctypes.sizeof(row);entries=[]
                if not kernel.Process32FirstW(handle,ctypes.byref(row)):return False
                while True:
                    entries.append((row.th32ProcessID, bytes(row.szExeFile).decode("utf-16-le").split("\0",1)[0]))
                    if not kernel.Process32NextW(handle,ctypes.byref(row)):
                        if ctypes.get_last_error()!=18:return False
                        break
            finally:kernel.CloseHandle(handle)
        own=any(p==os.getpid() and n.casefold()==os.path.basename(sys.executable).casefold() for p,n in entries)
        return own and any(p==pid and n.casefold()=="starcitizen.exe" for p,n in entries)
    except Exception:return False
