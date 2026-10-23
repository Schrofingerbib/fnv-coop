#!/usr/bin/env python
"""Who is calling the blocking Windows function? Read only (pauses a thread for a moment per sample).

    python -I tools/diag/stack_who.py --instance 0 --seconds 8 [--thread main|busiest]

For each sample of the chosen thread it records the function it is in (module!export) and walks the stack words for return addresses
that sit right after a CALL instruction, keeping those inside the game exe or any non-system DLL (the plugin, NVSE plugins, the AMD driver).
Prints: top functions, and for the system waits (Sleep, WaitFor...) the most common callers (module+RVA, resolved to an export if the DLL has one).
"""
import argparse, collections, ctypes, os, sys, time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', 'runner')]
import live_log as L  # noqa: E402

k32 = L.k32
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
SYSTEM = ('ntdll.dll', 'kernel32.dll', 'kernelbase.dll', 'win32u.dll', 'user32.dll', 'gdi32.dll', 'gdi32full.dll', 'msvcrt.dll', 'ucrtbase.dll', 'wow64.dll',
          'wow64cpu.dll', 'wow64win.dll', 'combase.dll', 'rpcrt4.dll', 'sechost.dll', 'msctf.dll', 'imm32.dll', 'ole32.dll', 'oleaut32.dll', 'shell32.dll')


def read(h, addr, n):
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t()
    if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) and got.value == n:
        return buf.raw
    return None


def after_call(h, ra):
    b = read(h, ra - 6, 6)
    if not b:
        return False
    return b[1] == 0xE8 or b[0:2] == b'\xff\x15' or (b[4] == 0xFF and (b[5] & 0x38) == 0x10) or (b[3] == 0xFF and (b[4] & 0x38) == 0x10)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--instance', type=int, default=0)
    ap.add_argument('--seconds', type=float, default=8)
    ap.add_argument('--thread', default='busiest')
    ap.add_argument('--depth', type=int, default=160, help='stack words to scan')
    ap.add_argument('--only', default='', help='comma list: keep only caller frames whose module name contains one of these (e.g. falloutnv,nvcoop)')
    a = ap.parse_args()
    only = [x.strip().lower() for x in a.only.split(',') if x.strip()]
    import run, winproc
    pid = run.load_state()['pids'].get(str(a.instance)) or (winproc.list_pids('FalloutNV.exe') or [None])[0]
    proc = L.Proc(pid)
    tids = L.thread_ids(pid)
    before = {t: L.thread_cpu_ms(t) for t in tids}
    time.sleep(2)
    busy = sorted(((L.thread_cpu_ms(t) - before[t], t) for t in tids if before.get(t) is not None and L.thread_cpu_ms(t) is not None), reverse=True)
    tid = busy[0][1]
    print('sampling thread %d (%.0f ms cpu in 2 s) of pid %d for %.0f s' % (tid, busy[0][0], pid, a.seconds))
    proc.refresh_modules()
    h = k32.OpenThread(0x0002 | 0x0008 | 0x0010 | 0x0040, False, tid)
    fn_c = collections.Counter()
    callers = collections.defaultdict(collections.Counter)
    n = 0
    t_end = time.time() + a.seconds
    while time.time() < t_end:
        if k32.Wow64SuspendThread(h) == 0xFFFFFFFF:
            continue
        c = L.CTX(); c.ContextFlags = 0x10001 | 0x10002
        ok = k32.Wow64GetThreadContext(h, ctypes.byref(c))
        stack = read(proc.h, c.Esp, a.depth * 4) if ok else None
        k32.ResumeThread(h)
        if not ok:
            continue
        n += 1
        top = proc.where(c.Eip)
        fn_c[top.split('+')[0]] += 1
        if stack:
            seen = set()
            for i in range(a.depth):
                v = int.from_bytes(stack[i * 4:i * 4 + 4], 'little')
                loc = proc.where(v)
                if loc == 'unknown' or loc.split('!')[0].split('+')[0].lower() in SYSTEM:
                    continue
                if only and not any(o in loc.lower() for o in only):
                    continue
                if not after_call(proc.h, v):
                    continue
                key = loc
                if key not in seen:
                    seen.add(key)
                    callers[top.split('+')[0]][key] += 1
        time.sleep(0.003)
    print('samples: %d' % n)
    for fn, cnt in fn_c.most_common(8):
        print('\n%3d%%  in %s' % (100 * cnt // max(n, 1), fn))
        for loc, cc in callers[fn].most_common(8):
            print('        called via %-52s %3d%% of those samples' % (loc, 100 * cc // cnt))
    return 0


if __name__ == '__main__':
    sys.exit(main())
