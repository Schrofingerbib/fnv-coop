"""Game slots: the ONLY way a lane may launch FalloutNV.exe. N slots (default 2, runs/gameslot_config.json {"slots": N, "min_free_gb": 3.0}).

  python -I tools/gameslot.py acquire --owner L5 --ttl 900 [--wait 1800] [--slots K]   # prints OK ...; exit 3 if busy
  python -I tools/gameslot.py release --owner L5
  python -I tools/gameslot.py status

--slots K   take K slots (K=1 is the normal sweep case). The OK line ends with `SLOTS 0,2`: those indices are YOURS;
            derive your instance ids / pipe names / UDP ports from them so concurrent lanes never collide.
(no --slots) legacy behaviour: take ALL slots = exclusive use of the machine (old single-slot callers keep working
            unchanged; they just wait until every other holder is gone).
Rules: acquire before launching, release after killing your instances. A lease expires after --ttl seconds
(default 900); an expired lease may be taken over and the taker must kill any FalloutNV.exe of the old holder.
Fairness: waiters queue FIFO (a waiter that polled within the last 60 s blocks newer acquirers), so an exclusive
request is not starved by a stream of 1-slot jobs. Exit codes: 0 ok, 3 busy (wait expired), 4 not owner.
Env GAMESLOT_DIR overrides the state directory (tests).

PIDs and strays: a launcher records every game process it starts with
  python -I tools/gameslot.py register-pid --owner L5 --pid 1234
When an EXPIRED lease is taken over, ONLY the pids recorded in that lease (and still FalloutNV.exe) are killed.
Nothing here ever kills "all FalloutNV.exe": several instances of several lanes are legal.
Old single-slot tools: while any slot lease is live a mirror lease is kept in runs/gameslot.json (owner
"slots:<owners>"), so an old-code waiter that only reads that file stays blocked instead of double-granting.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RUNS = Path(os.environ.get("GAMESLOT_DIR") or Path(__file__).resolve().parent.parent / "runs")
SLOTS_DIR = RUNS / "gameslots"
WAIT_DIR = RUNS / "gameslots" / "waiters"
LEGACY = RUNS / "gameslot.json"       # pre-N-slot lease file: if live and foreign it means "all slots held"
CONFIG = RUNS / "gameslot_config.json"
DEFAULT_SLOTS = 2
DEFAULT_MIN_FREE_GB = 3.0   # refuse NEW slots below this much free RAM (runs/gameslot_config.json: min_free_gb)
WAITER_STALE_S = 60


def max_slots():
    try:
        return max(1, int(json.loads(CONFIG.read_text())["slots"]))
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_SLOTS


def min_free_gb():
    try:
        return float(json.loads(CONFIG.read_text())["min_free_gb"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_MIN_FREE_GB


def _avail_gb():
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(m)
    ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**30


def _read(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def _game_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FalloutNV.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return [int(l.split('","')[1]) for l in out.splitlines() if l.startswith('"FalloutNV.exe"')]


def _kill_registered(pids):
    live = set(_game_pids())
    for pid in pids:
        if pid in live:  # still a FalloutNV.exe (guards against pid reuse)
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
            print("killed stale pid %d of expired lease" % pid)


def _slot_path(i):
    return SLOTS_DIR / f"slot-{i}.json"


def _live(lease, now):
    return bool(lease) and lease.get("expires", 0) > now


def _try_slot(i, owner, ttl, now):
    """Atomically take slot i if free / expired / already ours. Returns True on success."""
    SLOTS_DIR.mkdir(parents=True, exist_ok=True)
    p = _slot_path(i)
    cur = _read(p)
    if _live(cur, now) and cur["owner"] != owner:
        return False
    if cur and cur["owner"] != owner:   # expired foreign lease being taken over: kill ONLY the pids it registered
        _kill_registered(cur.get("pids", []))
    tmp = p.with_suffix(f".{os.getpid()}.{i}.tmp")
    mine = bool(cur and cur.get("owner") == owner)
    tmp.write_text(json.dumps({"owner": owner, "since": cur["since"] if mine else now, "expires": now + ttl, "slot": i,
                               "pids": cur.get("pids", []) if mine else []}))
    if cur is None:
        try:
            os.link(tmp, p)      # atomic create; fails if someone else created it first
        except FileExistsError:
            tmp.unlink()
            return False
        tmp.unlink()
        return True
    os.replace(tmp, p)
    got = _read(p)
    return bool(got) and got["owner"] == owner


def _legacy_blocks(owner, now):
    cur = _read(LEGACY)
    return _live(cur, now) and not cur.get("mirror") and cur.get("owner") != owner


def _sync_legacy(now=None):
    """Keep runs/gameslot.json as a mirror of 'some slot is held' for old single-slot code; remove it when idle."""
    now = now or time.time()
    cur = _read(LEGACY)
    if cur and not cur.get("mirror") and _live(cur, now):
        return  # a real legacy lease: not ours to touch
    held = [c for c in (_read(_slot_path(i)) for i in range(max_slots() + 8)) if _live(c, now)]
    if not held:
        if cur and cur.get("mirror"):
            LEGACY.unlink(missing_ok=True)
        return
    LEGACY.parent.mkdir(parents=True, exist_ok=True)
    LEGACY.write_text(json.dumps({"owner": "slots:" + ",".join(sorted({c["owner"] for c in held})), "mirror": True,
                                  "since": min(c["since"] for c in held), "expires": max(c["expires"] for c in held)}))


def _waiters(now, exclude_owner):
    out = []
    if WAIT_DIR.exists():
        for f in WAIT_DIR.glob("*.json"):
            w = _read(f)
            if w and now - w.get("polled", 0) < WAITER_STALE_S and w.get("owner") != exclude_owner:
                out.append(w)
    return out


def _mine(owner, now):
    return [i for i in range(max_slots()) if (lambda c: _live(c, now) and c["owner"] == owner)(_read(_slot_path(i)))]


def try_acquire(owner, ttl, want, ticket, now=None):
    """-> (ok, slots, info). want=None means ALL slots (exclusive)."""
    now = now or time.time()
    n = max_slots()
    need = n if want is None else min(want, n)
    if _legacy_blocks(owner, now):
        return False, [], "legacy lease: %s" % _read(LEGACY)
    older = [w for w in _waiters(now, owner) if w["ticket"] < ticket]
    if older:
        return False, [], "queue: %s ahead" % older[0]["owner"]
    have = _mine(owner, now)
    got = list(have)
    any_live = any(_live(_read(_slot_path(i)), now) for i in range(n))
    if len(have) < need and any_live:  # a 2nd+ concurrent game needs RAM headroom (first game / renewals never blocked)
        free = _avail_gb()
        if free < min_free_gb():
            return False, [], "low memory: %.1f GB free < %.1f GB required for a new slot" % (free, min_free_gb())
    for i in range(n):
        if len(got) >= need:
            break
        if i in got:
            continue
        if _try_slot(i, owner, ttl, now):
            got.append(i)
    for i in have:                        # renew leases we already hold (idempotent re-acquire)
        _try_slot(i, owner, ttl, now)
    if len(got) >= need:
        _sync_legacy(now)
        return True, sorted(got)[:max(need, len(have))], ""
    for i in set(got) - set(have):        # all-or-nothing: roll back what we just took
        _slot_path(i).unlink(missing_ok=True)
    _sync_legacy(now)
    holders = sorted({(_read(_slot_path(i)) or {}).get("owner", "?") for i in range(n) if _live(_read(_slot_path(i)), now)})
    return False, [], "held by %s" % ",".join(holders)


def release(owner):
    freed = 0
    for i in range(max_slots() + 8):
        c = _read(_slot_path(i))
        if c and c.get("owner") == owner:
            _slot_path(i).unlink(missing_ok=True)
            freed += 1
    cur = _read(LEGACY)
    if cur and cur.get("owner") == owner and not cur.get("mirror"):
        LEGACY.unlink(missing_ok=True)
        freed += 1
    _sync_legacy()
    return freed


def register_pid(owner, pid):
    n = 0
    for i in range(max_slots() + 8):
        c = _read(_slot_path(i))
        if c and c.get("owner") == owner and _live(c, time.time()):
            c["pids"] = sorted(set(c.get("pids", [])) | {int(pid)})
            tmp = _slot_path(i).with_suffix(f".{os.getpid()}.reg")
            tmp.write_text(json.dumps(c))
            os.replace(tmp, _slot_path(i))
            n += 1
    return n


def status(now=None):
    now = now or time.time()
    slots = []
    for i in range(max_slots()):
        c = _read(_slot_path(i))
        slots.append({"slot": i, "lease": c, "free": not _live(c, now)})
    leg = _read(LEGACY)
    held = [s["lease"] for s in slots if not s["free"]]
    # `lease`: single-slot compatible view (first live holder, else the live legacy lease)
    lease = held[0] if held else (leg if _live(leg, now) else None)
    return {"lease": lease, "expired": bool(lease and lease["expires"] < now), "max_slots": max_slots(),
            "slots": slots, "legacy": leg if _live(leg, now) else None, "running": _game_pids()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["acquire", "release", "status", "register-pid"])
    ap.add_argument("--pid", type=int)
    ap.add_argument("--owner")
    ap.add_argument("--ttl", type=int, default=900)
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--slots", type=int, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status()))
        return 0
    if not a.owner:
        ap.error("--owner required")
    if a.cmd == "register-pid":
        if not a.pid:
            ap.error("--pid required")
        n = register_pid(a.owner, a.pid)
        print("REGISTERED %d on %d lease(s)" % (a.pid, n))
        return 0 if n else 4
    if a.cmd == "release":
        n = release(a.owner)
        WAIT_DIR.mkdir(parents=True, exist_ok=True)
        (WAIT_DIR / f"{a.owner}.json").unlink(missing_ok=True)
        if n:
            print("RELEASED")
            return 0
        print("NOT OWNER: %s" % json.dumps(status()["lease"]))
        return 4
    WAIT_DIR.mkdir(parents=True, exist_ok=True)
    wf = WAIT_DIR / f"{a.owner}.json"
    old = _read(wf)
    ticket = old["ticket"] if old else time.time()
    deadline = time.time() + a.wait
    try:
        while True:
            now = time.time()
            wf.write_text(json.dumps({"owner": a.owner, "ticket": ticket, "polled": now, "slots": a.slots}))
            ok, slots, info = try_acquire(a.owner, a.ttl, a.slots, ticket, now)
            if ok:
                print("OK lease until %s; stray FalloutNV.exe pids: %s; SLOTS %s" % (
                    time.ctime(now + a.ttl), _game_pids() or "none", ",".join(map(str, slots))))
                return 0
            if time.time() >= deadline:
                print("BUSY: %s" % info)
                return 3
            time.sleep(min(10, max(0.05, float(os.environ.get("GAMESLOT_POLL", 10)))))
    finally:
        wf.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
