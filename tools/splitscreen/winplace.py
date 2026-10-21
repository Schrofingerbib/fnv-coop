"""Place the game windows: borderless, exactly on their cell, never taking the focus.

All Windows calls go through `WindowApi`, so the placement logic is tested with a fake. `Win32Api` is the real one (ctypes).

What the placer does to a window of a game process (once per window, then only when it drifts):
  1. removes the caption, frame, system menu and min/max boxes (borderless) and the extended edges;
  2. restores it when it is minimized (SW_SHOWNOACTIVATE: no activation);
  3. SetWindowPos to the cell rectangle with SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_NOZORDER.
The game re-creates its window after the splash and the engine calls SetWindowPos itself at start-up, so `Keeper` keeps checking and puts the
window back when it moved; it never touches the z-order and never activates a window (the player's keyboard focus stays where it is).
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_mut  # noqa: E402
from layout import Rect  # noqa: E402

WS_CAPTION, WS_THICKFRAME, WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_POPUP, WS_BORDER, WS_DLGFRAME = (
    0x00C00000, 0x00040000, 0x00080000, 0x00020000, 0x00010000, 0x80000000, 0x00800000, 0x00400000)
WS_EX_DLGMODALFRAME, WS_EX_CLIENTEDGE, WS_EX_STATICEDGE, WS_EX_WINDOWEDGE = 0x1, 0x200, 0x20000, 0x100
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x4, 0x10, 0x20, 0x40
SW_SHOWNOACTIVATE = 4

STYLE_REMOVE = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_BORDER | WS_DLGFRAME
EXSTYLE_REMOVE = WS_EX_DLGMODALFRAME | WS_EX_CLIENTEDGE | WS_EX_STATICEDGE | WS_EX_WINDOWEDGE


def borderless_style(style: int) -> int:
    if split_mut.on("keep_caption"):
        return style
    return (style & ~STYLE_REMOVE) | WS_POPUP


def borderless_exstyle(ex: int) -> int:
    return ex & ~EXSTYLE_REMOVE


def is_borderless(style: int) -> bool:
    return (style & (WS_CAPTION | WS_THICKFRAME)) == 0


class WindowApi:
    """What the placer needs from Windows."""

    def windows_of_pid(self, pid: int) -> List[int]: raise NotImplementedError
    def style(self, h: int) -> int: raise NotImplementedError
    def exstyle(self, h: int) -> int: raise NotImplementedError
    def set_style(self, h: int, style: int, ex: int) -> None: raise NotImplementedError
    def rect(self, h: int) -> Rect: raise NotImplementedError          # window rectangle, screen coordinates
    def client(self, h: int) -> Tuple[int, int]: raise NotImplementedError  # client width, height
    def iconic(self, h: int) -> bool: raise NotImplementedError
    def show_noactivate(self, h: int) -> None: raise NotImplementedError
    def set_pos(self, h: int, r: Rect, flags: int) -> bool: raise NotImplementedError
    def foreground(self) -> int: raise NotImplementedError


def game_window(api: WindowApi, pid: int) -> Optional[int]:
    """The main window of a game process: the visible top-level window with the largest area."""
    best, area = None, -1
    for h in api.windows_of_pid(pid):
        r = api.rect(h)
        a = r.w * r.h
        if a > area:
            best, area = h, a
    return best


def place(api: WindowApi, h: int, cell: Rect) -> Rect:
    """Make window h borderless and put it on `cell`. Returns the rectangle it ended up with."""
    st, ex = api.style(h), api.exstyle(h)
    nst, nex = borderless_style(st), borderless_exstyle(ex)
    if (nst, nex) != (st, ex):
        api.set_style(h, nst, nex)
    if api.iconic(h) and not split_mut.on("no_restore"):
        api.show_noactivate(h)
    flags = SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_NOZORDER | SWP_SHOWWINDOW
    if split_mut.on("activate_on_place"):
        flags &= ~SWP_NOACTIVATE
    api.set_pos(h, cell, flags)
    return api.rect(h)


def placed_ok(api: WindowApi, h: int, cell: Rect) -> bool:
    return api.rect(h) == cell and is_borderless(api.style(h)) and not api.iconic(h) and api.client(h) == (cell.w, cell.h)


class Keeper:
    """Keeps every game window on its cell. step() is one pass; run() loops until `stop()` is true.

    targets: {pid: cell}. A pid whose window does not exist yet is skipped (the game creates it after the splash)."""

    def __init__(self, api: WindowApi, targets: Dict[int, Rect], log: Callable[[str], None] = print):
        self.api, self.targets, self.log = api, dict(targets), log
        self.placed: Dict[int, int] = {}      # pid -> hwnd it was last placed as
        self.moves = 0

    def step(self) -> Dict[int, bool]:
        """Returns {pid: True when the window is on its cell now}."""
        ok: Dict[int, bool] = {}
        for pid, cell in self.targets.items():
            h = game_window(self.api, pid)
            if h is None:
                ok[pid] = False
                continue
            if split_mut.on("trust_first_place") and self.placed.get(pid) == h:
                ok[pid] = True
                continue
            if not placed_ok(self.api, h, cell):
                place(self.api, h, cell)
                self.moves += 1
                if self.placed.get(pid) != h:
                    self.log(f"window of pid {pid}: placed at {cell.w}x{cell.h}+{cell.x}+{cell.y}")
                self.placed[pid] = h
            ok[pid] = placed_ok(self.api, h, cell)
        return ok

    def run(self, stop: Callable[[], bool], interval: float = 0.5, until_all_ok_s: Optional[float] = None,
            timeout_s: Optional[float] = None) -> bool:
        """Loop until stop(). With until_all_ok_s: return True once every window has been on its cell for that many seconds
        (False when stop() or timeout_s comes first)."""
        stable_since: Optional[float] = None
        t0 = time.monotonic()
        while not stop():
            if timeout_s is not None and time.monotonic() - t0 > timeout_s:
                return False
            ok = self.step()
            if until_all_ok_s is not None:
                if ok and all(ok.values()):
                    stable_since = stable_since or time.monotonic()
                    if time.monotonic() - stable_since >= until_all_ok_s:
                        return True
                else:
                    stable_since = None
            time.sleep(interval)
        return False


class Win32Api(WindowApi):
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows only")
        u = ctypes.WinDLL("user32", use_last_error=True)
        self.u = u
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u.SetWindowLongW.restype = ctypes.c_long
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetForegroundWindow.restype = wintypes.HWND

    def windows_of_pid(self, pid: int) -> List[int]:
        out: List[int] = []
        proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(h, _):
            p = wintypes.DWORD()
            self.u.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid and self.u.GetWindow(h, 4) is None:   # GW_OWNER == 4: top-level windows without an owner only
                if self.u.IsWindowVisible(h) or self.u.IsIconic(h):
                    out.append(h)
            return True
        self.u.EnumWindows(proc_t(cb), 0)
        return out

    def style(self, h: int) -> int:
        return self.u.GetWindowLongW(h, -16) & 0xFFFFFFFF

    def exstyle(self, h: int) -> int:
        return self.u.GetWindowLongW(h, -20) & 0xFFFFFFFF

    def set_style(self, h: int, style: int, ex: int) -> None:
        def s32(v: int) -> int:
            return v - (1 << 32) if v >= (1 << 31) else v
        self.u.SetWindowLongW(h, -16, s32(style))
        self.u.SetWindowLongW(h, -20, s32(ex))

    def rect(self, h: int) -> Rect:
        r = wintypes.RECT()
        self.u.GetWindowRect(h, ctypes.byref(r))
        return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)

    def client(self, h: int) -> Tuple[int, int]:
        r = wintypes.RECT()
        self.u.GetClientRect(h, ctypes.byref(r))
        return r.right - r.left, r.bottom - r.top

    def iconic(self, h: int) -> bool:
        return bool(self.u.IsIconic(h))

    def show_noactivate(self, h: int) -> None:
        self.u.ShowWindow(h, SW_SHOWNOACTIVATE)

    def set_pos(self, h: int, r: Rect, flags: int) -> bool:
        return bool(self.u.SetWindowPos(h, None, r.x, r.y, r.w, r.h, flags))

    def foreground(self) -> int:
        return int(self.u.GetForegroundWindow() or 0)
