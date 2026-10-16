#!/usr/bin/env python3
"""One-shot launch probe (L2): start instance 0 minimized and log, every 50 ms, the state of its windows
(visible / iconic / foreground) so we can tell whether a window ever shows un-minimized.

    python -I tools/runner/launchprobe.py [--direct] [--seconds 30]

Holds the game slot for the duration; kills the game at the end. --direct launches FalloutNV.exe instead of
nvse_loader.exe (then NVSE/nvcoop are not injected).
"""
import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instances  # noqa: E402
import run  # noqa: E402
import winproc  # noqa: E402

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.IsIconic.argtypes = [wintypes.HWND]
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.GetForegroundWindow.restype = wintypes.HWND
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of(pids):
    out = []

    def cb(h, _):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value in pids and u32.IsWindowVisible(h):
            n = u32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(h, buf, n + 1)
            out.append((h, p.value, buf.value, bool(u32.IsIconic(h))))
        return True
    u32.EnumWindows(EnumProc(cb), 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--direct", action="store_true")
    ap.add_argument("--seconds", type=float, default=30)
    a = ap.parse_args()
    if winproc.list_pids("FalloutNV.exe"):
        print("FalloutNV.exe already running; abort", file=sys.stderr)
        return 2
    print(run.slot_acquire("L2", ttl=300, wait=1800))
    mine = set()
    try:
        d = instances.instance_dir(0)
        exe = d / ("FalloutNV.exe" if a.direct else "nvse_loader.exe")
        env = dict(__import__("os").environ)
        env.update(run.STEAM_ENV)
        env["NVCOOP_INSTANCE"] = "0"
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7
        preexisting = set(winproc.list_pids("FalloutNV.exe"))
        t0 = time.time()
        p = subprocess.Popen([str(exe)], cwd=str(d), env=env, startupinfo=si)
        print(f"launched {exe.name} pid {p.pid}")
        seen_state = {}
        events = []
        while time.time() - t0 < a.seconds:
            before = set(winproc.list_pids("FalloutNV.exe")) - preexisting
            mine |= before
            pids = before | {p.pid}
            fg = u32.GetForegroundWindow()
            for h, pid, title, iconic in windows_of(pids):
                st = ("iconic" if iconic else "NORMAL") + (" FOREGROUND" if h == fg else "")
                if seen_state.get(h) != st:
                    seen_state[h] = st
                    events.append(f"+{time.time() - t0:5.2f}s pid {pid} hwnd {h:#x} '{title}' -> {st}")
                    print(events[-1], flush=True)
            time.sleep(0.05)
        print("game pids at end:", winproc.list_pids("FalloutNV.exe"))
        print("pipe nvcoop-0 present:", Path(r"\\.\pipe\nvcoop-0").exists())
        nlog = d / "nvse.log"
        for f in (d / "nvse.log", d / "nvse_steam_loader.log", d / "Data/NVSE/Plugins/nvcoop.log"):
            print(f.name, "exists" if f.exists() else "missing")
    finally:
        for pid in sorted(mine):
            if pid in winproc.list_pids("FalloutNV.exe"):
                winproc.kill_pid(pid)
        time.sleep(2)
        print("left running (ours):", [p for p in winproc.list_pids("FalloutNV.exe") if p in mine])
        print(run.slot_release("L2"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
