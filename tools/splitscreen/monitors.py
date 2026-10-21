"""Monitor discovery (ctypes, Windows) and the choice of one monitor for the split-screen layout.

The process is made DPI aware first, so every rectangle is in physical pixels, the same unit SetWindowPos and the game's iSize use.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout import Rect  # noqa: E402


class Monitor:
    def __init__(self, index: int, rect: Rect, work: Rect, primary: bool, name: str = ""):
        self.index, self.rect, self.work, self.primary, self.name = index, rect, work, primary, name

    def as_dict(self) -> dict:
        return {"index": self.index, "rect": self.rect.as_dict(), "work": self.work.as_dict(), "primary": self.primary, "name": self.name}

    def __repr__(self) -> str:
        return f"Monitor({self.index}, {self.rect.w}x{self.rect.h}+{self.rect.x}+{self.rect.y}{', primary' if self.primary else ''})"


def make_dpi_aware() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def list_monitors() -> List[Monitor]:
    """All attached monitors, primary first, numbered from 1 in that order."""
    if os.name != "nt":
        return []
    make_dpi_aware()

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    found = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rc, _lp):
        mi = MonitorInfo()
        mi.cbSize = ctypes.sizeof(mi)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r, w = mi.rcMonitor, mi.rcWork
            found.append((bool(mi.dwFlags & 1), mi.szDevice, Rect(r.left, r.top, r.right - r.left, r.bottom - r.top),
                          Rect(w.left, w.top, w.right - w.left, w.bottom - w.top)))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    found.sort(key=lambda t: (not t[0], t[2].x, t[2].y))
    return [Monitor(i + 1, rect, work, primary, name) for i, (primary, name, rect, work) in enumerate(found)]


def choose(monitors: List[Monitor], spec: Optional[str]) -> Monitor:
    """spec: None or "primary" -> the primary monitor; "1", "2", ... -> that monitor (the numbering of list_monitors)."""
    if not monitors:
        raise ValueError("no monitor found")
    if spec in (None, "", "primary"):
        for m in monitors:
            if m.primary:
                return m
        return monitors[0]
    try:
        n = int(spec)
    except ValueError:
        raise ValueError(f"monitor must be 'primary' or a number 1..{len(monitors)}, not {spec!r}") from None
    for m in monitors:
        if m.index == n:
            return m
    raise ValueError(f"monitor {n} does not exist (found {len(monitors)})")


if __name__ == "__main__":
    for m in list_monitors():
        print(m)
