#!/usr/bin/env python
"""Which file paths is the main thread asking Windows about (ZwQueryAttributesFile / ZwCreateFile / ZwOpenFile)? Read only.

    python -I tools/diag/file_probe.py --instance 0 --seconds 15

Pauses the busiest thread for a moment per sample; when it is inside one of those calls it scans the stack for the OBJECT_ATTRIBUTES argument
(Length 0x18, ObjectName pointer) and reads the UNICODE_STRING path. Prints the paths by count, with the game or plugin frame that made the call.
"""
import argparse, collections, ctypes, os, sys, time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', 'runner')]
import live_log as L  # noqa: E402
import stack_who as S  # noqa: E402

k32 = L.k32
WANT = ('ZwQueryAttributesFile', 'ZwCreateFile', 'ZwOpenFile', 'ZwQueryFullAttributesFile', 'NtQueryAttributesFile', 'NtCreateFile', 'NtOpenFile')


def u32le(b, i):
    return int.from_bytes(b[i:i + 4], 'little')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--instance', type=int, default=0)
    ap.add_argument('--seconds', type=float, default=15)
    a = ap.parse_args()
    import run, winproc
    pid = run.load_state()['pids'].get(str(a.instance)) or (winproc.list_pids('FalloutNV.exe') or [None])[0]
    proc = L.Proc(pid)
    tids = L.thread_ids(pid)
    before = {t: L.thread_cpu_ms(t) for t in tids}
    time.sleep(2)
    busy = sorted(((L.thread_cpu_ms(t) - before[t], t) for t in tids if before.get(t) is not None and L.thread_cpu_ms(t) is not None), reverse=True)
    tid = busy[0][1]
    proc.refresh_modules()
    h = k32.OpenThread(0x0002 | 0x0008 | 0x0010 | 0x0040, False, tid)
    paths = collections.Counter()
    via = collections.defaultdict(collections.Counter)
    n = hits = 0
    t_end = time.time() + a.seconds
    while time.time() < t_end:
        if k32.Wow64SuspendThread(h) == 0xFFFFFFFF:
            continue
        c = L.CTX(); c.ContextFlags = 0x10001 | 0x10002
        ok = k32.Wow64GetThreadContext(h, ctypes.byref(c))
        stack = S.read(proc.h, c.Esp, 2048) if ok else None
        k32.ResumeThread(h)
        if not ok or not stack:
            continue
        n += 1
        top = proc.where(c.Eip)
        if not any(w in top for w in WANT):
            time.sleep(0.001)
            continue
        found = None
        for i in range(0, 24):
            p = u32le(stack, i * 4)
            oa = S.read(proc.h, p, 24) if 0x10000 < p < 0x7FFF0000 else None
            if oa and u32le(oa, 0) == 0x18:
                us_ptr = u32le(oa, 8)
                us = S.read(proc.h, us_ptr, 8) if 0x10000 < us_ptr < 0x7FFF0000 else None
                if us:
                    ln, buf = int.from_bytes(us[0:2], 'little'), u32le(us, 4)
                    if 0 < ln < 1040 and 0x10000 < buf < 0x7FFF0000:
                        s = S.read(proc.h, buf, ln)
                        if s:
                            found = s.decode('utf-16-le', 'replace')
                            break
        if not found:
            continue
        hits += 1
        paths[found] += 1
        for i in range(0, 1200):
            v = u32le(stack, i * 4) if i * 4 + 4 <= len(stack) else 0
            if not v:
                break
        # first game/plugin frame after a CALL
        deep = S.read(proc.h, c.Esp, 6000) or stack
        for i in range(len(deep) // 4):
            v = u32le(deep, i * 4)
            loc = proc.where(v)
            if ('falloutnv' in loc.lower() or 'nvcoop' in loc.lower()) and S.after_call(proc.h, v):
                via[found][loc] += 1
                break
        time.sleep(0.001)
    print('thread %d, samples %d, caught inside a file call %d' % (tid, n, hits))
    for p, cnt in paths.most_common(15):
        print('%4d  %s' % (cnt, p))
        for loc, cc in via[p].most_common(2):
            print('        from %s (%d)' % (loc, cc))
    return 0


if __name__ == '__main__':
    sys.exit(main())
