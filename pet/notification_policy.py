"""Side-effect-free notification/voice/reaction gating and safe fullscreen detection."""
from datetime import datetime
import os
import sys
import time

from .config import validate


def detect_fullscreen():
    """Check only the foreground window against its monitor on Windows.

    Desktop/shell surfaces and this assistant's own process are excluded. No
    processes are scanned, no shell is invoked, and API failures return False.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetDesktopWindow.argtypes = []
        user32.GetDesktopWindow.restype = wintypes.HWND
        user32.GetShellWindow.argtypes = []
        user32.GetShellWindow.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        window = user32.GetForegroundWindow()
        if (not window or window in (user32.GetDesktopWindow(), user32.GetShellWindow())
                or not user32.IsWindowVisible(window) or user32.IsIconic(window)):
            return False
        name = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(window, name, len(name)):
            return False
        if name.value in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
            return False
        process_id = wintypes.DWORD()
        if (not user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
                or process_id.value == os.getpid()):
            return False
        rectangle = wintypes.RECT()
        if not user32.GetWindowRect(window, ctypes.byref(rectangle)):
            return False
        monitor = user32.MonitorFromWindow(window, 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        screen = info.rcMonitor
        return (rectangle.left <= screen.left and rectangle.top <= screen.top
                and rectangle.right >= screen.right and rectangle.bottom >= screen.bottom)
    except Exception:
        return False


class NotificationPolicy:
    def __init__(self, settings, fullscreen_detector=None, wall_clock=time.time):
        validate(settings)
        # Keep the caller's mapping live so preferences take effect immediately.
        self.settings = settings
        self.fullscreen_detector = (detect_fullscreen if fullscreen_detector is None
                                    else fullscreen_detector)
        self.wall_clock = wall_clock

    def reasons(self):
        cfg = validate(self.settings)
        result = []
        if cfg["dnd"]:
            result.append("手动免打扰")
        if cfg["quiet_hours_enabled"]:
            local = datetime.fromtimestamp(self.wall_clock())
            current = local.hour * 60 + local.minute
            start = int(cfg["quiet_start"][:2]) * 60 + int(cfg["quiet_start"][3:])
            end = int(cfg["quiet_end"][:2]) * 60 + int(cfg["quiet_end"][3:])
            quiet = (start == end or (start <= current < end if start < end
                                     else current >= start or current < end))
            if quiet:
                result.append("夜间免打扰")
        if cfg["fullscreen_dnd"]:
            try:
                fullscreen = bool(self.fullscreen_detector())
            except Exception:
                fullscreen = False
            if fullscreen:
                result.append("全屏免打扰")
        return result

    def is_dnd(self):
        return bool(self.reasons())

    def can_notify(self):
        return not self.is_dnd()

    def can_voice(self):
        cfg = validate(self.settings)
        return cfg["voice_enabled"] and not cfg["mute"] and not self.is_dnd()

    def can_react(self, explicit=True):
        return bool(explicit) or not self.is_dnd()
