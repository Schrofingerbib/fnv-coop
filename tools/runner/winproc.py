"""Windows process helpers for the runner (ctypes only). No cross-process handle manipulation:
the single-instance mutex is released by the nvcoop plugin itself (nvcoop.ini multiInstance=1)."""
from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from typing import List

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


def pid_alive(pid: int) -> bool:
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def list_pids(image_name: str) -> List[int]:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    pids = []
    for ln in out.splitlines():
        parts = [p.strip('"') for p in ln.split('","')]
        if len(parts) > 1 and parts[0].strip('"').lower() == image_name.lower():
            pids.append(int(parts[1]))
    return pids


def kill_pid(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)


def minimize_windows(pid: int, timeout_s: float = 60.0, settle_s: float = 3.0) -> int:
    """Minimize every top-level window of pid WITHOUT activating it (SW_SHOWMINNOACTIVE=7 via
    ShowWindowAsync). Never touches the foreground or sends input. Keeps re-minimizing for settle_s
    after the last new window appears (the game recreates its window after the splash)."""
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    seen = set()
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]

    def cb(hwnd, _):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd) and not u32.IsIconic(hwnd):
            u32.ShowWindowAsync(hwnd, 7)
            seen.add(hwnd)
        return True
    proc = EnumProc(cb)
    t0 = last = time.time()
    while time.time() - t0 < timeout_s and pid_alive(pid):
        n = len(seen)
        u32.EnumWindows(proc, 0)
        if len(seen) != n:
            last = time.time()
        elif seen and time.time() - last > settle_s:
            break
        time.sleep(0.1)
    return len(seen)


def proc_stats(pid: int) -> dict:
    """Working set (MB) and cumulative CPU seconds of pid (ctypes, no deps)."""
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return {}
    try:
        pm = PMC()
        pm.cb = ctypes.sizeof(pm)
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(h, ctypes.byref(pm), pm.cb)
        ft = [wintypes.FILETIME() for _ in range(4)]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft])
        t = lambda f: ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7  # noqa: E731
        return {"ws_mb": round(pm.WorkingSetSize / 2**20), "cpu_s": round(t(ft[2]) + t(ft[3]), 2)}
    finally:
        k32.CloseHandle(h)


def system_memory() -> dict:
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(m)
    k32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"total_gb": round(m.ullTotalPhys / 2**30, 1), "avail_gb": round(m.ullAvailPhys / 2**30, 1),
            "load_pct": m.dwMemoryLoad}
