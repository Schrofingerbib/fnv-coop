#!/usr/bin/env python
"""Live diagnostic logger for one running game copy. Read only: it never sends input and never changes game state.

    python -I tools/diag/live_log.py --instance 0 --seconds 60 --out out/diag/run1.jsonl [--label windowed640]

Every --every seconds it writes one JSON line:
  t, label, pid, cpu_pct (of one core), ws_mb, fg (is the game the foreground window),
  dt (engine frame time from the bridge: GetSecondsPassed median), menu (bot.menu kind), frames_per_s (plugin hub counter),
  hot: for each busy thread, the top functions it was executing (module!export+offset) from a poor man's sampler
       (pause the thread for a moment, read EIP, resume; 32-bit game from 64-bit Python via Wow64 calls),
  log_new: new lines of nvcoop.log since the last sample (WARN / ERR / FAIL only, capped).
The summary at the end names the dominant function per hot thread, which is the first thing to read.
"""
import argparse, collections, ctypes, json, os, struct, sys, time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..'), os.path.join(HERE, '..', 'runner')]

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)
u32 = ctypes.WinDLL('user32', use_last_error=True)
for fn, at in (('OpenProcess', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]), ('OpenThread', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
               ('Wow64SuspendThread', [wintypes.HANDLE]), ('ResumeThread', [wintypes.HANDLE]), ('Wow64GetThreadContext', [wintypes.HANDLE, ctypes.c_void_p]),
               ('CloseHandle', [wintypes.HANDLE]), ('GetProcessTimes', [wintypes.HANDLE] + [ctypes.c_void_p] * 4),
               ('GetThreadTimes', [wintypes.HANDLE] + [ctypes.c_void_p] * 4)):
    getattr(k32, fn).argtypes = at
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenThread.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Thread32First.argtypes = k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.Wow64SuspendThread.restype = wintypes.DWORD
psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


class CTX(ctypes.Structure):
    _fields_ = [('ContextFlags', wintypes.DWORD)] + [(n, wintypes.DWORD) for n in ('Dr0', 'Dr1', 'Dr2', 'Dr3', 'Dr6', 'Dr7')] + \
        [('FloatSave', ctypes.c_byte * 112)] + [(n, wintypes.DWORD) for n in ('SegGs', 'SegFs', 'SegEs', 'SegDs', 'Edi', 'Esi', 'Ebx', 'Edx', 'Ecx', 'Eax', 'Ebp', 'Eip', 'SegCs', 'EFlags', 'Esp', 'SegSs')] + \
        [('Ext', ctypes.c_byte * 512)]


class MI(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('size', wintypes.DWORD), ('entry', ctypes.c_void_p)]


_export_cache = {}


def exports(path):
    if path in _export_cache:
        return _export_cache[path]
    out = []
    try:
        d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        nsec = struct.unpack_from('<H', d, pe + 6)[0]
        opt = pe + 24
        magic = struct.unpack_from('<H', d, opt)[0]
        dd = opt + (96 if magic == 0x10b else 112)
        erva, esz = struct.unpack_from('<II', d, dd)
        so = opt + struct.unpack_from('<H', d, pe + 20)[0]
        secs = [struct.unpack_from('<8sIIII', d, so + i * 40) for i in range(nsec)]

        def off(rva):
            for _, vs, va, rs, rp in secs:
                if va <= rva < va + max(vs, rs):
                    return rva - va + rp
            return None
        if erva:
            eo = off(erva)
            nn = struct.unpack_from('<I', d, eo + 24)[0]
            af, an, ao = struct.unpack_from('<III', d, eo + 28)
            for i in range(nn):
                nr = struct.unpack_from('<I', d, off(an) + i * 4)[0]
                name = d[off(nr):d.index(b'\0', off(nr))].decode()
                ordn = struct.unpack_from('<H', d, off(ao) + i * 2)[0]
                out.append((struct.unpack_from('<I', d, off(af) + ordn * 4)[0], name))
    except Exception:
        pass
    out.sort()
    _export_cache[path] = out
    return out


class Proc:
    def __init__(self, pid):
        self.pid = pid
        self.h = k32.OpenProcess(0x0400 | 0x0010 | 0x1000, False, pid)
        self.mods = []
        self.refresh_modules()

    def refresh_modules(self):
        arr = (wintypes.HMODULE * 1024)()
        need = wintypes.DWORD()
        psapi.EnumProcessModulesEx(self.h, arr, ctypes.sizeof(arr), ctypes.byref(need), 3)
        mods = []
        for i in range(need.value // ctypes.sizeof(wintypes.HMODULE)):
            name = ctypes.create_unicode_buffer(260)
            psapi.GetModuleFileNameExW(self.h, arr[i], name, 260)
            mi = MI()
            psapi.GetModuleInformation(self.h, arr[i], ctypes.byref(mi), ctypes.sizeof(mi))
            path = name.value
            low = path.lower()
            if '\\windows\\system32\\' in low:   # the game is 32-bit: read the 32-bit copy, not the 64-bit one the redirector hands us
                path = path[:low.index('\\system32\\')] + '\\SysWOW64\\' + path[low.index('\\system32\\') + 10:]
            mods.append(((mi.base or 0), (mi.base or 0) + mi.size, path))
        self.mods = mods

    def where(self, addr):
        for lo, hi, path in self.mods:
            if lo <= addr < hi:
                rva = addr - lo
                best = None
                for fr, n in exports(path):
                    if fr <= rva:
                        best = (fr, n)
                    else:
                        break
                base = os.path.basename(path)
                if best and rva - best[0] < 0x4000:
                    return '%s!%s+0x%X' % (base, best[1], rva - best[0])
                return '%s+0x%X' % (base, rva)
        return 'unknown'


def ft(v):
    return (v.value if hasattr(v, 'value') else v)


def thread_cpu_ms(tid):
    h = k32.OpenThread(0x0040, False, tid)
    if not h:
        return None
    c, e, kk, uu = (wintypes.FILETIME() for _ in range(4))
    ok = k32.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kk), ctypes.byref(uu))
    k32.CloseHandle(h)
    if not ok:
        return None
    return ((kk.dwHighDateTime << 32 | kk.dwLowDateTime) + (uu.dwHighDateTime << 32 | uu.dwLowDateTime)) / 10000.0


def thread_ids(pid):
    class TE(ctypes.Structure):
        _fields_ = [('sz', wintypes.DWORD), ('us', wintypes.DWORD), ('tid', wintypes.DWORD), ('owner', wintypes.DWORD), ('bp', wintypes.LONG), ('dp', wintypes.LONG), ('fl', wintypes.DWORD)]
    snap = k32.CreateToolhelp32Snapshot(0x4, 0)
    te = TE(); te.sz = ctypes.sizeof(TE)
    out = []
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.owner == pid:
            out.append(te.tid)
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return out


def sample_threads(proc, tids, n, gap=0.004):
    hs = {t: k32.OpenThread(0x0002 | 0x0008 | 0x0010 | 0x0040, False, t) for t in tids}
    res = {t: collections.Counter() for t in tids}
    for _ in range(n):
        for t in tids:
            if not hs[t] or k32.Wow64SuspendThread(hs[t]) == 0xFFFFFFFF:
                continue
            c = CTX(); c.ContextFlags = 0x10001
            if k32.Wow64GetThreadContext(hs[t], ctypes.byref(c)):
                res[t][c.Eip] += 1
            k32.ResumeThread(hs[t])
        time.sleep(gap)
    for h in hs.values():
        if h:
            k32.CloseHandle(h)
    return res


INGAME_METHODS = ('coop.status', 'hooks.status', 'focus.status', 'anim.stats', 'avatar.status', 'actors.port', 'world.status', 'ui.status',
                  'flight.status', 'observe.stats', 'guest.stats', 'worldtime.status', 'intents.status', 'reload.state', 'bot.menu')
INGAME_EVALS = ('GetGameHour', 'Player.GetPos X', 'Player.GetPos Y', 'Player.GetPos Z', 'Player.GetAV Health', 'GetSecondsPassed')


def flatten(v, prefix=''):
    out = {}
    if isinstance(v, dict):
        for k2, x in v.items():
            out.update(flatten(x, prefix + '.' + str(k2) if prefix else str(k2)))
    elif isinstance(v, list):
        for i, x in enumerate(v[:16]):
            out.update(flatten(x, '%s[%d]' % (prefix, i)))
    elif isinstance(v, bool):
        out[prefix] = 1 if v else 0
    elif isinstance(v, (int, float)):
        out[prefix] = v
    return out


def collect_ingame(nv, dead):
    snap = {}
    for m in INGAME_METHODS:
        if m in dead:
            continue
        try:
            snap[m] = nv.call(m, {}, timeout_s=5)
        except Exception as ex:
            if 'unknown method' in str(ex):
                dead.add(m)
            else:
                snap[m] = {'error': str(ex)[:100]}
    ev = {}
    for e in INGAME_EVALS:
        try:
            ev[e] = nv.eval(e)
        except Exception:
            pass
    snap['eval'] = ev
    return snap


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--instance', type=int, default=0)
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--every', type=float, default=5)
    ap.add_argument('--samples', type=int, default=120, help='samples per hot thread per line')
    ap.add_argument('--out', default='out/diag/live_log.jsonl')
    ap.add_argument('--label', default='')
    ap.add_argument('--no-bridge', action='store_true')
    ap.add_argument('--no-ingame', action='store_true', help='skip the plugin status snapshots (OS-level sampling only)')
    a = ap.parse_args()

    import instances
    d = instances.instance_dir(a.instance)
    import winproc
    pids = winproc.list_pids('FalloutNV.exe')
    pid = None
    try:
        import run
        pid = run.load_state()['pids'].get(str(a.instance))
    except Exception:
        pass
    pid = pid or (pids[0] if pids else None)
    if not pid:
        print('no running FalloutNV.exe', file=sys.stderr)
        return 2
    nv = None
    if not a.no_bridge:
        try:
            from nvbridge import NvBridge
            nv = NvBridge.connect(instance=a.instance, wait_s=5)
        except Exception as e:
            print('bridge unavailable:', e, file=sys.stderr)
    proc = Proc(pid)
    logp = os.path.join(str(d), 'Data', 'NVSE', 'Plugins', 'nvcoop.log')
    pos = os.path.getsize(logp) if os.path.exists(logp) else 0
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    f = open(a.out, 'a', encoding='utf-8')
    ig = open(a.out.replace('.jsonl', '') + '.ingame.jsonl', 'a', encoding='utf-8')
    dead, prev_flat, prev_time = set(), None, 0.0
    t0 = time.time()
    prev_cpu = None
    summary = collections.defaultdict(collections.Counter)
    dts = []
    while time.time() - t0 < a.seconds:
        c, e, kk, uu = (wintypes.FILETIME() for _ in range(4))
        k32.GetProcessTimes(proc.h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kk), ctypes.byref(uu))
        cpu = ((kk.dwHighDateTime << 32 | kk.dwLowDateTime) + (uu.dwHighDateTime << 32 | uu.dwLowDateTime)) / 1e7
        tids = thread_ids(pid)
        before = {t: thread_cpu_ms(t) for t in tids}
        t_s = time.time()
        time.sleep(1.0)
        busy = []
        for t in tids:
            b = thread_cpu_ms(t)
            if b is not None and before.get(t) is not None and b - before[t] > 80:
                busy.append((b - before[t], t))
        busy.sort(reverse=True)
        proc.refresh_modules()
        hot = {}
        if busy:
            res = sample_threads(proc, [t for _, t in busy[:3]], a.samples)
            for ms, t in busy[:3]:
                tot = sum(res[t].values()) or 1
                agg = collections.Counter()
                for ip, cnt in res[t].items():
                    agg[proc.where(ip)] += cnt
                top = agg.most_common(4)
                hot[str(t)] = {'cpu_ms_per_s': round(ms), 'top': [{'fn': fn, 'pct': round(100 * cnt / tot)} for fn, cnt in top]}
                for fn, cnt in agg.items():
                    summary[t][fn.split('+')[0]] += cnt
        row = {'t': round(time.time() - t0, 1), 'label': a.label, 'pid': pid, 'hot': hot}
        mem = ctypes.create_string_buffer(72)
        ctypes.cast(mem, ctypes.POINTER(wintypes.DWORD))[0] = 72
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(proc.h, mem, 72):
            row['ws_mb'] = round(struct.unpack_from('<Q', mem.raw, 16)[0] / 1048576)
        fg = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(fg))
        row['fg'] = fg.value == pid
        if nv:
            try:
                vals = sorted(nv.eval('GetSecondsPassed') for _ in range(5))
                row['dt'] = round(vals[2], 4)
                dts.append(vals[2])
                row['menu'] = nv.call('bot.menu', {})['kind']
                fr0 = nv.call('coop.status', {}).get('counters', {}).get('frames')
                row['frames'] = fr0
                if not a.no_ingame:
                    snap = collect_ingame(nv, dead)
                    flat = flatten(snap)
                    now = time.time()
                    if prev_flat is not None:
                        dtm = max(now - prev_time, 1e-3)
                        mv = sorted(((abs(flat[k] - prev_flat[k]) / dtm, k) for k in flat if k in prev_flat and flat[k] != prev_flat[k]
                                     and not k.startswith('eval.') and 'atMs' not in k and 'Ms' not in k.split('.')[-1]), reverse=True)
                        row['moving'] = [{'k': k, 'per_s': round(r, 1)} for r, k in mv[:12]]
                    prev_flat, prev_time = flat, now
                    row['eval'] = snap.get('eval')
                    ig.write(json.dumps({'t': row['t'], 'snapshot': snap}) + '\n')
                    ig.flush()
            except Exception as ex:
                row['bridge_err'] = str(ex)[:80]
        if os.path.exists(logp):
            sz = os.path.getsize(logp)
            if sz > pos:
                with open(logp, 'rb') as lf:
                    lf.seek(pos)
                    txt = lf.read(sz - pos).decode('utf-8', 'replace').splitlines()
                pos = sz
                keep = [x[:200] for x in txt if any(w in x.upper() for w in ('WARN', 'ERR', 'FAIL', 'CRASH', 'REFUSED'))]
                if keep:
                    row['log_new'] = keep[:8]
        f.write(json.dumps(row) + '\n')
        f.flush()
        line = 't=%5.1fs dt=%s menu=%s ws=%sMB fg=%s' % (row['t'], row.get('dt'), row.get('menu'), row.get('ws_mb'), row['fg'])
        for t, h in hot.items():
            line += '\n    thread %s %4dms/s: %s' % (t, h['cpu_ms_per_s'], ', '.join('%s %d%%' % (x['fn'], x['pct']) for x in h['top'][:3]))
        if row.get('moving'):
            line += '\n    moving: ' + ', '.join('%s %g/s' % (x['k'], x['per_s']) for x in row['moving'][:6])
        print(line, flush=True)
        left = a.every - (time.time() - t_s)
        if left > 0:
            time.sleep(left)
    print('\nSUMMARY' + (' [%s]' % a.label if a.label else ''))
    if dts:
        dts.sort()
        print('  engine frame time median %.1f ms (~%.0f fps)' % (dts[len(dts) // 2] * 1000, 1 / max(dts[len(dts) // 2], 1e-3)))
    for t, c in summary.items():
        tot = sum(c.values())
        print('  thread %d: ' % t + ', '.join('%s %d%%' % (m, 100 * n // tot) for m, n in c.most_common(4)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
