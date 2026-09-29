"""Windows single-instance scope and activation, confined to the caller's desktop."""
import ctypes
import getpass
import hashlib
import json
import os
from ctypes import wintypes

APP_TITLE = "ChickenRice · 日语音声字幕工作台"


def desktop_identity():
    if os.name != "nt":
        return getpass.getuser(), os.environ.get("DISPLAY", "default")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    name, required = ctypes.create_unicode_buffer(256), wintypes.DWORD()
    if not user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(required)):
        raise ctypes.WinError(ctypes.get_last_error())
    username, size = ctypes.create_unicode_buffer(256), wintypes.DWORD(256)
    if not ctypes.WinDLL("advapi32", use_last_error=True).GetUserNameW(username, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    session = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return username.value, session.value, name.value


def instance_lock_path(directory, identity=None):
    identity = desktop_identity() if identity is None else identity
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()[:20]
    return directory / ("desktop-" + key + ".lock")


def activate_window(pid):
    """Find only this app's main window on this desktop; restore and foreground it."""
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    handles = []

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == APP_TITLE:
                handles.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if not handles:
        return False
    hwnd = handles[0]
    if not user32.IsIconic(hwnd) and not user32.MonitorFromWindow(hwnd, 0):
        # Recover a window left outside every monitor, e.g. after unplugging a display.
        area, rect = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0):
            width = min(rect.right - rect.left, area.right - area.left)
            height = min(rect.bottom - rect.top, area.bottom - area.top)
            user32.SetWindowPos(hwnd, None, area.left + (area.right - area.left - width) // 2,
                               area.top + (area.bottom - area.top - height) // 2,
                               width, height, 0x0014)
    user32.ShowWindowAsync(hwnd, 9 if user32.IsIconic(hwnd) else 5)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True
