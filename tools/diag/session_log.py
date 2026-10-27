#!/usr/bin/env python
"""Always-on session logger for the running game copies. Read only: no input, no game-state changes.

    python -I tools/diag/session_log.py --auto            # what `run.py up` starts: watches every copy until all are gone
    python -I tools/diag/session_log.py --seconds 120     # fixed length

Writes out/diag/sessions/<timestamp>/ (path also in out/diag/sessions/LATEST.txt):
  timeline.jsonl   one row per copy per second: engine frame time, CPU %, memory, bridge latency, menu, window state, plugin frame counter
  system.jsonl     one row per second: free RAM, commit, CPU, top processes by RAM (every 10 s)
  ingame.jsonl     every 5 s: snapshot of the plugin's read-only status methods per copy, plus the counters that moved fastest
  events.jsonl     anomalies: slow_frames, frozen_frames, bridge_unresponsive, hung_window, modal_menu, low_ram, process_exit
  dumps/NNN_<reason>_i<k>.json   for every anomaly: busy threads with resolved function names, thread list, top processes, log tails, display ini
  summary.md       written at the end: per copy frame time stats, every anomaly with its top function, what to read first
"""
import argparse, collections, ctypes, json, os, subprocess, sys, time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path[:0] = [HERE, os.path.join(ROOT, 'tools'), os.path.join(ROOT, 'tools', 'runner')]

import live_log as L   # noqa: E402  (process modules, thread sampler, export resolver, in-game snapshot helpers)

k32, u32, psapi = L.k32, L.u32, L.psapi
u32.IsHungAppWindow.argtypes = [wintypes.HWND]
u32.IsIconic.argtypes = [wintypes.HWND]
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
k32.GetSystemTimes.argtypes = [ctypes.c_void_p] * 3


class MEMSTAT(ctypes.Structure):
    _fields_ = [('len', wintypes.DWORD), ('load', wintypes.DWORD)] + [(n, ctypes.c_ulonglong) for n in ('phys', 'availphys', 'pf', 'availpf', 'virt', 'availvirt', 'ext')]


def sys_mem():
    m = MEMSTAT(); m.len = ctypes.sizeof(MEMSTAT)
    k32.GlobalMemoryStatusEx(ctypes.byref(m))
    gb = 1 << 30
    return {'free_gb': round(m.availphys / gb, 2), 'total_gb': round(m.phys / gb, 1), 'commit_gb': round((m.pf - m.availpf) / gb, 1), 'load_pct': m.load}


def ft64(f):
    return f.dwHighDateTime << 32 | f.dwLowDateTime


class CpuTotal:
    def __init__(self):
        self.prev = self.read()

    def read(self):
        i, k, u = (wintypes.FILETIME() for _ in range(3))
        k32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
        return ft64(i), ft64(k) + ft64(u)

    def pct(self):
        cur = self.read()
        di, dt = cur[0] - self.prev[0], cur[1] - self.prev[1]
        self.prev = cur
        return round(100 * (1 - di / dt)) if dt else 0


def windows_of(pid):
    found = []
    EP = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(h):
            found.append(h)
        return True
    u32.EnumWindows(EP(cb), 0)
    return found


def window_state(pid):
    ws = windows_of(pid)
    if not ws:
        return {'window': False}
    h = ws[0]
    r = wintypes.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    fg = wintypes.DWORD()
    u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(fg))
    return {'window': True, 'iconic': bool(u32.IsIconic(h)), 'hung': bool(u32.IsHungAppWindow(h)), 'fg': fg.value == pid,
            'rect': [r.left, r.top, r.right - r.left, r.bottom - r.top]}


def top_procs(n=8):
    try:
        out = subprocess.run(['pwsh', '-NoProfile', '-Command',
                              "Get-Process | Group Name | % { [pscustomobject]@{n=$_.Name;c=$_.Count;ws=[int](($_.Group|Measure WS -Sum).Sum/1MB);pm=[int](($_.Group|Measure PM -Sum).Sum/1MB)} } | sort ws -desc | select -First %d | ConvertTo-Json -Compress" % n],
                             capture_output=True, text=True, timeout=20).stdout
        return json.loads(out)
    except Exception as e:
        return [{'error': str(e)[:80]}]


def tail(path, n, filt=None):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - 65536))
            lines = f.read().decode('utf-8', 'replace').splitlines()
        if filt:
            lines = [x for x in lines if filt(x)]
        return [x[:220] for x in lines[-n:]]
    except Exception as e:
        return ['(unreadable: %s)' % str(e)[:60]]


def thread_table(pid):
    try:
        out = subprocess.run(['pwsh', '-NoProfile', '-Command',
                              "(Get-Process -Id %d).Threads | select -First 60 Id,ThreadState,WaitReason,@{n='cpu_ms';e={[int]$_.TotalProcessorTime.TotalMilliseconds}} | ConvertTo-Json -Compress" % pid],
                             capture_output=True, text=True, timeout=20).stdout
        return json.loads(out)
    except Exception:
        return []


class Inst:
    def __init__(self, idx, pid, inst_dir):
        self.idx, self.pid, self.dir = idx, pid, inst_dir
        self.proc = L.Proc(pid)
        self.nv = None
        self.prev_cpu = None
        self.slow = self.stall = self.berr = 0
        self.menu_since = None
        self.last_frames = None
        self.last_dump = {}
        self.gone = False
        self.dts = []
        self.hist = []
        self.prev_flat = None
        self.prev_time = 0.0
        self.dead_methods = set()
        self.pings = []

    def connect(self):
        if self.nv is None:
            try:
                from nvbridge import NvBridge
                self.nv = NvBridge.connect(instance=self.idx, wait_s=0.2)
            except Exception:
                self.nv = None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--auto', action='store_true', help='run until every copy has been gone for 25 s')
    ap.add_argument('--seconds', type=float, default=0)
    ap.add_argument('--slow-ms', type=float, default=100, help='fallback when the plugin frame counter is unavailable')
    ap.add_argument('--min-fps', type=float, default=15, help='below this (plugin frame counter) for 5 s with no menu open = slow_frames')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    import winproc, run, instances
    pidf = os.path.join(ROOT, 'runs', 'diag.pid')
    try:
        old = int(open(pidf).read().strip())
        if old != os.getpid() and winproc.pid_alive(old):
            print('session logger already running (pid %d)' % old)
            return 0
    except Exception:
        pass
    os.makedirs(os.path.dirname(pidf), exist_ok=True)
    open(pidf, 'w').write(str(os.getpid()))

    stamp = time.strftime('%Y%m%d-%H%M%S')
    out = a.out or os.path.join(ROOT, 'out', 'diag', 'sessions', stamp)
    os.makedirs(os.path.join(out, 'dumps'), exist_ok=True)
    open(os.path.join(ROOT, 'out', 'diag', 'sessions', 'LATEST.txt'), 'w').write(out)
    tl, sy, ig, ev = (open(os.path.join(out, n), 'a', encoding='utf-8') for n in ('timeline.jsonl', 'system.jsonl', 'ingame.jsonl', 'events.jsonl'))
    print('session log:', out, flush=True)

    insts = {}
    events = []
    cpu = CpuTotal()
    dump_n = [0]
    t0 = time.time()
    last_seen = time.time()
    low_ram_since = [None]
    last_top = [0.0]

    def event(t, kind, i, detail, dump=None):
        e = {'t': round(t, 1), 'kind': kind, 'instance': i, 'detail': detail}
        if dump:
            e['dump'] = os.path.basename(dump['file']); e['top'] = dump.get('top')
        events.append(e)
        ev.write(json.dumps(e) + '\n'); ev.flush()
        print('!! t=%.0fs %s i%s: %s%s' % (t, kind, i, detail, ('  -> ' + str(dump.get('top'))) if dump and dump.get('top') else ''), flush=True)

    def dump(t, inst, reason, detail=''):
        key = reason
        if t - inst.last_dump.get(key, -999) < 30:
            return None
        inst.last_dump[key] = t
        dump_n[0] += 1
        info = {'t': round(t, 1), 'reason': reason, 'detail': detail, 'instance': inst.idx, 'pid': inst.pid, 'system': sys_mem(), 'window': window_state(inst.pid)}
        top = None
        if not inst.gone and winproc.pid_alive(inst.pid):
            tids = L.thread_ids(inst.pid)
            before = {x: L.thread_cpu_ms(x) for x in tids}
            time.sleep(0.5)
            busy = sorted(((L.thread_cpu_ms(x) - before[x], x) for x in tids if before.get(x) is not None and L.thread_cpu_ms(x) is not None), reverse=True)
            busy = [(ms, x) for ms, x in busy if ms > 20][:3]
            inst.proc.refresh_modules()
            hot = {}
            if busy:
                res = L.sample_threads(inst.proc, [x for _, x in busy], 120)
                for ms, x in busy:
                    tot = sum(res[x].values()) or 1
                    agg = collections.Counter()
                    for ip, cnt in res[x].items():
                        agg[inst.proc.where(ip)] += cnt
                    hot[str(x)] = {'cpu_ms_in_0.5s': round(ms), 'top': [{'fn': fn, 'pct': round(100 * c / tot)} for fn, c in agg.most_common(6)]}
                first = hot[str(busy[0][1])]['top'][0]
                top = '%s %d%%' % (first['fn'], first['pct'])
            info['hot_threads'] = hot
            info['threads'] = thread_table(inst.pid)
        info['top_processes'] = top_procs()
        info['logs'] = {'nvcoop.log': tail(os.path.join(inst.dir, 'Data', 'NVSE', 'Plugins', 'nvcoop.log'), 25),
                        'nvse.log': tail(os.path.join(inst.dir, 'nvse.log'), 8),
                        'falloutnv_error.log': tail(os.path.join(inst.dir, 'falloutnv_error.log'), 10, lambda x: x.strip()),
                        'nvcoop.log warnings': tail(os.path.join(inst.dir, 'Data', 'NVSE', 'Plugins', 'nvcoop.log'), 12, lambda x: any(w in x.upper() for w in ('WARN', 'ERR', 'FAIL', 'CRASH', 'REFUSED')))}
        try:
            prefs = open(os.path.join(inst.dir, 'FalloutPrefs.ini'), encoding='latin-1').read().splitlines()
            info['display_ini'] = [x for x in prefs if x.split('=')[0] in ('bFull Screen', 'iSize W', 'iSize H', 'iMultiSample', 'iPresentInterval', 'bTopMostWindow')]
            info['nvcoop_ini'] = tail(os.path.join(inst.dir, 'Data', 'NVSE', 'Plugins', 'nvcoop.ini'), 40)
        except Exception:
            pass
        if inst.nv:
            try:
                info['ingame'] = L.collect_ingame(inst.nv, inst.dead_methods)
            except Exception as ex:
                info['ingame_error'] = str(ex)[:100]
        path = os.path.join(out, 'dumps', '%03d_%s_i%d.json' % (dump_n[0], reason, inst.idx))
        json.dump(info, open(path, 'w'), indent=1)
        return {'file': path, 'top': top}

    try:
        while True:
            tick = time.time()
            t = tick - t0
            if a.seconds and t > a.seconds:
                break
            st = {}
            try:
                st = run.load_state()['pids']
            except Exception:
                pass
            for k, pid in list(st.items()):
                idx = int(k)
                if idx not in insts and winproc.pid_alive(pid):
                    insts[idx] = Inst(idx, pid, str(instances.instance_dir(idx)))
                    event(t, 'copy_seen', idx, 'pid %d' % pid)
                elif idx in insts and insts[idx].pid != pid and winproc.pid_alive(pid):
                    insts[idx] = Inst(idx, pid, str(instances.instance_dir(idx)))
                    event(t, 'copy_restarted', idx, 'pid %d' % pid)
            alive_any = False
            sm = sys_mem()
            row_sys = {'t': round(t, 1), **sm, 'cpu_pct': cpu.pct()}
            if tick - last_top[0] > 10:
                row_sys['top'] = top_procs(6); last_top[0] = tick
            sy.write(json.dumps(row_sys) + '\n'); sy.flush()
            if sm['free_gb'] < 0.6:
                low_ram_since[0] = low_ram_since[0] or t
                if t - low_ram_since[0] > 5 and insts:
                    i0 = next(iter(insts.values()))
                    d = dump(t, i0, 'low_ram', 'free %.2f GB, commit %.1f GB' % (sm['free_gb'], sm['commit_gb']))
                    if d:
                        event(t, 'low_ram', i0.idx, 'free %.2f GB, commit %.1f GB' % (sm['free_gb'], sm['commit_gb']), d)
            else:
                low_ram_since[0] = None
            for inst in list(insts.values()):
                if inst.gone:
                    continue
                if not winproc.pid_alive(inst.pid):
                    inst.gone = True
                    d = dump(t, inst, 'process_exit', 'pid %d is gone' % inst.pid)
                    event(t, 'process_exit', inst.idx, 'pid %d is gone (crash or closed)' % inst.pid, d)
                    continue
                alive_any = True
                row = {'t': round(t, 1), 'i': inst.idx, 'pid': inst.pid}
                c, e, kk, uu = (wintypes.FILETIME() for _ in range(4))
                k32.GetProcessTimes(inst.proc.h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kk), ctypes.byref(uu))
                cpu_s = (ft64(kk) + ft64(uu)) / 1e7
                if inst.prev_cpu is not None:
                    row['cpu_pct'] = round(100 * (cpu_s - inst.prev_cpu[0]) / max(tick - inst.prev_cpu[1], 1e-3))
                inst.prev_cpu = (cpu_s, tick)
                mem = ctypes.create_string_buffer(80)
                ctypes.cast(mem, ctypes.POINTER(wintypes.DWORD))[0] = 80
                psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
                if psapi.GetProcessMemoryInfo(inst.proc.h, mem, 80):
                    row['ws_mb'] = round(int.from_bytes(mem.raw[16:24], 'little') / 1048576)
                    row['commit_mb'] = round(int.from_bytes(mem.raw[56:64], 'little') / 1048576)
                w = window_state(inst.pid)
                row.update({k2: v for k2, v in w.items() if k2 != 'window' or not v})
                if w.get('hung'):
                    d = dump(t, inst, 'hung_window', 'IsHungAppWindow')
                    if d:
                        event(t, 'hung_window', inst.idx, 'the window is not pumping messages', d)
                inst.connect()
                if inst.nv:
                    try:
                        p0 = time.perf_counter()
                        inst.nv.ping()
                        lat = (time.perf_counter() - p0) * 1000
                        row['ping_ms'] = round(lat)
                        vals = sorted(inst.nv.eval('GetSecondsPassed') for _ in range(3))
                        dt = vals[1]
                        row['dt_ms'] = round(dt * 1000, 1)
                        menu = inst.nv.call('bot.menu', {}, timeout_s=3)
                        row['menu'] = menu.get('kind'); row['boxes'] = menu.get('msgCount')
                        cs = inst.nv.call('coop.status', {}, timeout_s=3)
                        fr = cs.get('counters', {}).get('frames')
                        row['frames'] = fr; row['lastSeq'] = cs.get('lastSeq')
                        inst.berr = 0
                        inst.dts.append(dt)
                        # fps from the plugin's own frame counter: GetSecondsPassed goes stale and is not a frame time (measured)
                        inst.hist.append((t, fr))
                        del inst.hist[:-6]
                        fps = None
                        if fr is not None and len(inst.hist) >= 4 and inst.hist[-1][0] > inst.hist[-4][0] and inst.hist[-4][1] is not None:
                            fps = (inst.hist[-1][1] - inst.hist[-4][1]) / (inst.hist[-1][0] - inst.hist[-4][0])
                        row['fps'] = None if fps is None else round(fps, 1)
                        slow = (fps < a.min_fps) if fps is not None else (dt * 1000 > a.slow_ms)
                        inst.slow = inst.slow + 1 if (slow and row['menu'] in (None, 'none')) else 0
                        if inst.slow >= 5:
                            d = dump(t, inst, 'slow_frames', 'fps %s' % row['fps'])
                            if d:
                                event(t, 'slow_frames', inst.idx, 'only %s fps for 5+ s with no menu open' % row['fps'], d)
                        inst.stall = inst.stall + 1 if (fr is not None and fr == inst.last_frames) else 0
                        inst.last_frames = fr
                        if inst.stall >= 3:
                            d = dump(t, inst, 'frozen_frames', 'plugin frame counter stuck at %s' % fr)
                            if d:
                                event(t, 'frozen_frames', inst.idx, 'plugin frame counter has not moved for 3+ s', d)
                        if row['menu'] not in (None, 'none'):
                            inst.menu_since = inst.menu_since or t
                            if t - inst.menu_since > 15:
                                d = dump(t, inst, 'modal_menu', 'menu %s open for %.0f s (boxes %s)' % (row['menu'], t - inst.menu_since, row['boxes']))
                                if d:
                                    event(t, 'modal_menu', inst.idx, 'menu %s open for 15+ s, boxes queued %s: input is being eaten' % (row['menu'], row['boxes']), d)
                        else:
                            inst.menu_since = None
                        if int(t) % 5 == 0:
                            snap = L.collect_ingame(inst.nv, inst.dead_methods)
                            flat = L.flatten(snap)
                            mv = []
                            if inst.prev_flat is not None:
                                dtm = max(tick - inst.prev_time, 1e-3)
                                mv = sorted(((abs(flat[k2] - inst.prev_flat[k2]) / dtm, k2) for k2 in flat if k2 in inst.prev_flat and flat[k2] != inst.prev_flat[k2]
                                             and not k2.startswith('eval.') and 'atMs' not in k2 and 'Ms' not in k2.split('.')[-1]), reverse=True)[:12]
                            inst.prev_flat, inst.prev_time = flat, tick
                            ig.write(json.dumps({'t': round(t, 1), 'i': inst.idx, 'moving': [{'k': k2, 'per_s': round(r, 1)} for r, k2 in mv], 'snapshot': snap}) + '\n'); ig.flush()
                    except Exception as ex:
                        inst.berr += 1
                        row['bridge_err'] = str(ex)[:90]
                        if inst.berr >= 3:
                            d = dump(t, inst, 'bridge_unresponsive', str(ex)[:120])
                            if d:
                                event(t, 'bridge_unresponsive', inst.idx, '%d bridge calls in a row failed: %s' % (inst.berr, str(ex)[:90]), d)
                tl.write(json.dumps(row) + '\n'); tl.flush()
            if alive_any:
                last_seen = time.time()
            elif a.auto and time.time() - last_seen > 25 and (insts or time.time() - t0 > 120):
                break
            time.sleep(max(0.0, 1.0 - (time.time() - tick)))
    except KeyboardInterrupt:
        pass
    finally:
        lines = ['# Session log %s' % stamp, '', 'Folder: `%s`' % out, 'Length: %.0f s' % (time.time() - t0), '']
        for idx, inst in sorted(insts.items()):
            d = sorted(inst.dts)
            lines.append('## Copy %d (pid %d)%s' % (idx, inst.pid, ' - exited during the session' if inst.gone else ''))
            if d:
                lines.append('- engine frame time: median %.0f ms (~%.0f fps), p95 %.0f ms, max %.0f ms over %d samples' % (
                    d[len(d) // 2] * 1000, 1 / max(d[len(d) // 2], 1e-3), d[int(len(d) * .95)] * 1000, d[-1] * 1000, len(d)))
            else:
                lines.append('- no bridge samples (the plugin pipe was never reachable)')
        lines += ['', '## Anomalies (%d)' % len(events), '']
        for e in events:
            lines.append('- t=%ss **%s** copy %s: %s%s' % (e['t'], e['kind'], e['instance'], e['detail'], ('  \n  top: `%s`  dump: `dumps/%s`' % (e.get('top'), e.get('dump'))) if e.get('dump') else ''))
        if not events:
            lines.append('- none')
        lines += ['', 'Read first: the first anomaly above, then its dump (hot thread function names, log tails, top processes, display settings).']
        open(os.path.join(out, 'summary.md'), 'w', encoding='utf-8').write('\n'.join(lines))
        print('\n'.join(lines), flush=True)
        try:
            if open(pidf).read().strip() == str(os.getpid()):
                os.remove(pidf)
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
