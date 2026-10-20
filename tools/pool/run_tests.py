"""Game-free tests for the warm pool + a mutation twin per invariant. Prints ALL OK when everything holds.

    python -I tools/pool/run_tests.py

A check takes a mutant name (or None) and raises AssertionError when an invariant is violated.
Real run: every check must pass with mutant=None. Twin run: each (check, mutant) pair must FAIL.
No game is launched; the only OS things touched are a loopback socket, a temp dir (also holding a fake game dir and
an instance provisioned from it), the gameslot CLI under a temp GAMESLOT_DIR and (read only) the owner's INIs.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "runner"))
sys.path.insert(0, str(REPO / "tools" / "runner" / "tests"))
import backend as be  # noqa: E402
import core  # noqa: E402
import lowcost  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def mk(size=2, mutant=None, **kw):
    b, s, c = be.FakeBackend(), be.FakeSlots(), Clock()
    tmp = Path(tempfile.mkdtemp(prefix="pooltest-"))
    p = core.Pool(b, s, size=size, sync=True, clock=c, mutant=mutant, state_path=tmp / "state.json", **kw)
    return p, b, s, c, tmp


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    return False


# ------------------------------------------------------------------------------------------------ pool policy
def check_exclusive(m):
    p, b, *_ = mk(2, m)
    p.up()
    l1, l2 = p.lease("a"), p.lease("b")
    assert l1["instances"] == [0] and l2["instances"] == [1], (l1, l2)
    assert raises(core.PoolBusy, p.lease, "c", 1, 900, 0.2), "third lease must wait/fail while both are leased"
    p.release(l1["id"])
    assert p.lease("c")["instances"] == [0]
    p.down()


def check_pair_lease_order(m):
    p, *_ = mk(2, m)
    p.up()
    assert p.lease("x", n=2)["instances"] == [0, 1], "host (lowest id) first"
    assert raises(core.PoolError, p.lease, "y", 3), "more than the pool size is an error"
    p.down()


def check_size_clamped(m):
    assert mk(5, m)[0].size == 2 and mk(0, m)[0].size == 1


def check_recycle_after_k(m):
    p, b, *_ = mk(1, m, recycle_after=3)
    p.up()
    for _ in range(3):
        p.release(p.lease("t")["id"])
    assert b.starts == [0, 0], f"expected one recycle after 3 tests, starts={b.starts}"
    assert p.insts[0].gen == 2 and p.insts[0].tests == 0
    p.down()


def check_dirty_recycles(m):
    p, b, *_ = mk(1, m)
    p.up()
    L = p.lease("t")
    p.mark_dirty(L["id"], 0, "loaded foreign save")
    p.release(L["id"])
    assert b.starts == [0, 0], "dirty instance must be restarted"
    L = p.lease("t")
    p.release(L["id"])
    assert b.starts == [0, 0], "clean release must NOT restart"
    p.down()


def check_expiry(m):
    p, b, s, c, _ = mk(1, m)
    p.up()
    L = p.lease("t", ttl_s=10)
    c.t += 5
    p.tick()
    assert p.leases, "lease must survive before its ttl"
    c.t += 6
    p.tick()
    assert not p.leases and p.stats["expired"] == 1, "expired lease must be reaped"
    assert b.starts == [0, 0], "an expired lease's instance is state-unknown: recycle"
    assert b.unlocked, "bridge lock must be force-released"
    p.down()


def check_renew(m):
    p, b, s, c, _ = mk(1, m)
    p.up()
    L = p.lease("t", ttl_s=10)
    c.t += 8
    assert p.renew(L["id"])
    c.t += 8
    p.tick()
    assert p.leases, "renewed lease must live"
    p.down()


def check_kill_only_own(m):
    p, b, *_ = mk(2, m)
    p.up()
    p.down()
    assert b.foreign == {900001, 900002}, f"foreign game processes were killed: {b.foreign}"
    assert not b.procs, "own processes must be gone"


def check_reap_previous_only_recorded(m):
    p, b, s, c, tmp = mk(1, m)
    p.up()
    pid = p.insts[0].pid
    # simulate a daemon crash: a new Pool over the same fake machine finds the recorded pid
    p2 = core.Pool(b, be.FakeSlots(), size=1, sync=True, clock=c, state_path=tmp / "state.json", mutant=m)
    assert pid in p2.reap_previous() and 900001 in b.foreign
    assert pid not in b.procs


class _VanishingState:
    """A state file that the previous daemon deletes between our exists() and unlink() (the up-right-after-down race)."""
    def __init__(self):
        self.deleted = False

    def exists(self):
        return True

    def read_text(self):
        return json.dumps({"pids": {}})

    def unlink(self, missing_ok=False):
        if not missing_ok:
            raise FileNotFoundError("state.json was deleted by the previous daemon")
        self.deleted = True


def check_reap_state_vanishes(m):
    b, c = be.FakeBackend(), Clock()
    st = _VanishingState()
    p = core.Pool(b, be.FakeSlots(), size=1, sync=True, clock=c, state_path=st, mutant=m)
    try:
        killed = p.reap_previous()
    except FileNotFoundError as e:
        raise AssertionError(f"pool up crashed when the old daemon removed its state file first: {e}")
    assert killed == [] and st.deleted, (killed, st.deleted)


def check_crash_restart(m):
    p, b, *_ = mk(1, m)
    p.up()
    b.crash(0)
    p.tick()
    assert p.insts[0].state == "warm" and p.insts[0].gen == 2, p.insts[0].view()
    L = p.lease("t")
    b.crash(0)
    p.tick()
    g = p.leases[L["id"]]["gens"]["0"]
    assert p.insts[0].gen != g and p.insts[0].state == "leased", "lessee must be able to see the restart"
    p.down()


def check_dead_instance_never_leased(m):
    p, b, *_ = mk(1, m)
    p.up()
    b.crash(0)
    L = p.lease("t")
    assert b.alive(p.insts[0].pid) and L["gens"]["0"] == 2, "a dead warm instance must be restarted before it is handed out"
    p.down()


def check_unresponsive_restart(m):
    p, b, *_ = mk(1, m)
    p.up()
    b.unresponsive.add(0)
    b.fail_start.clear()
    old = p.insts[0].pid

    def heal():  # the restart brings a working bridge
        b.unresponsive.discard(0)
    orig = b.start
    b.start = lambda i: (heal(), orig(i))[1]
    p.tick()
    assert p.insts[0].pid != old and p.insts[0].gen == 2, "unresponsive bridge => restart"
    p.down()


def check_memory_growth(m):
    p, b, *_ = mk(1, m, ws_growth_mb=500)
    p.up()
    b.grow(0, 1200 + 400)
    p.tick()
    assert b.starts == [0], "400 MB growth is under the 500 MB limit"
    b.grow(0, 1200 + 600)
    p.tick()
    assert b.starts == [0, 0], "600 MB growth must recycle"
    p.down()


def check_start_failure(m):
    p, b, *_ = mk(1, m)
    b.fail_start.add(0)
    assert raises(core.PoolError, p.up), "up must fail loudly when an instance will not start"
    assert not b.procs, "failed start must not leave a process behind"


def check_failed_start_stops_lease(m):
    p, b, *_ = mk(1, m, max_start_fails=2)
    p.up()
    b.fail_start.add(0)
    b.crash(0)
    p.tick()
    p.tick()
    assert raises(core.PoolError, p.lease, "t", 1, 900, 0.3), "lease must error, not hang, when nothing can start"


def check_slot_renew(m):
    p, b, s, c, _ = mk(1, m, slot_ttl_s=300)
    p.up()
    c.t += 150
    p.tick()
    assert s.renewed == 1, "game slot lease must be renewed before it expires"
    p.down()
    assert s.held == [], "slots freed on down"


def check_pids_registered(m):
    p, b, s, *_ = mk(2, m)
    p.up()
    assert sorted(s.pids) == sorted(i.pid for i in p.insts.values()), "every launched pid goes to gameslot register-pid"
    p.down()


# ------------------------------------------------------------------------------------------------ low-cost INIs
def _inst_dir():
    d = Path(tempfile.mkdtemp(prefix="lowcost-"))
    (d / "Fallout.ini").write_text("[General]\r\nuGridsToLoad=5\r\nbAlwaysActive=0\r\n[Audio]\r\nbEnableAudio=1\r\n", encoding="latin-1")
    (d / "FalloutPrefs.ini").write_text("[Display]\r\niSize W=1920\r\niSize H=1080\r\n", encoding="latin-1")
    return d


def check_lowcost_applies(m):
    d = _inst_dir()
    lowcost.apply(d)
    fo = (d / "Fallout.ini").read_text(encoding="latin-1")
    pr = (d / "FalloutPrefs.ini").read_text(encoding="latin-1")
    import instances
    g = lambda t, s, k: instances.ini_get(t, s, k)  # noqa: E731
    assert g(pr, "Display", "iSize W") == "640" and g(pr, "Display", "iSize H") == "480"
    assert g(fo, "General", "iFPSClamp") == "30" and g(fo, "General", "bUseMyGamesDirectory") == "0"
    assert g(fo, "General", "uGridsToLoad") == "5", "uGridsToLoad must stay unless --grids is given"
    assert g(fo, "Audio", "bEnableAudio") == "1", "audio stays on unless --audio-off is given"
    assert g(pr, "Display", "iShadowFilter") == "0"
    before = (fo, pr)
    lowcost.apply(d)
    assert before == ((d / "Fallout.ini").read_text(encoding="latin-1"), (d / "FalloutPrefs.ini").read_text(encoding="latin-1")), "idempotent"
    lowcost.apply(d, grids=3, audio_off=True)
    fo = (d / "Fallout.ini").read_text(encoding="latin-1")
    assert g(fo, "General", "uGridsToLoad") == "3" and g(fo, "Audio", "bEnableAudio") == "0"
    assert raises(ValueError, lowcost.rows, 4), "even grid counts are refused"


def check_lowcost_guard_hardlink(m):
    d = _inst_dir()
    src = Path(tempfile.mkdtemp(prefix="lowcost-src-")) / "Fallout.ini"
    src.write_text("[General]\r\n", encoding="latin-1")
    (d / "Fallout.ini").unlink()
    os.link(src, d / "Fallout.ini")
    try:
        lowcost.apply(d, check=(m != "no_guard"))
    except lowcost.UnsafeTarget:
        return
    assert False, "must refuse a hard-linked INI (writing would change the file it is linked to)"


def check_lowcost_guard_mygames(m):
    root = Path(tempfile.mkdtemp(prefix="lowcost-")) / "Documents" / "My Games" / "FalloutNV"
    root.mkdir(parents=True)
    (root / "Fallout.ini").write_text("[General]\r\n", encoding="latin-1")
    (root / "FalloutPrefs.ini").write_text("[Display]\r\n", encoding="latin-1")
    try:
        lowcost.apply(root, check=(m != "no_guard"))
    except lowcost.UnsafeTarget:
        return
    assert False, "must refuse the owner's My Games folder"


def check_owner_inis_untouched(m):
    mg = Path.home() / "Documents" / "My Games" / "FalloutNV"
    files = [mg / "Fallout.ini", mg / "FalloutPrefs.ini"]
    h = lambda: [hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else "" for f in files]  # noqa: E731
    before = h()
    d = _inst_dir()
    lowcost.apply(d)
    lowcost.restore(d)
    lowcost.render_doc()
    lowcost.verify()
    assert h() == before, "owner INIs changed"


# ------------------------------------------------------------------------------------------------ low-cost off: restore
@contextlib.contextmanager
def _env(**kv):
    """Set environment variables for the block, then put back the old values (RealBackend() also sets FNV_LANE)."""
    old = {k: os.environ.get(k) for k in set(kv) | {"FNV_LANE"}}
    os.environ.update(kv)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _IniBackend(be.FakeBackend):
    """Fake processes, but prepare() is RealBackend.prepare(): the real INI step of every pool start."""

    def __init__(self, real):
        super().__init__()
        self.real = real

    def prepare(self, i: int) -> None:
        super().prepare(i)
        self.real.prepare(i)


def _snapshot(d: Path):
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def check_nolowcost_restores_provisioned_inis(m):
    """`pool up` (low-cost), `down`, `pool up --no-lowcost`: the instance must be back to the provisioned INIs."""
    import instances
    tmp = Path(tempfile.mkdtemp(prefix="poolini-"))
    g = tmp / "game"
    (g / "Data").mkdir(parents=True)
    (g / "FalloutNV.exe").write_bytes(b"MZ" + b"exe" * 100)
    (g / "Fallout_default.ini").write_text("[General]\r\nuGridsToLoad=5\r\n[Display]\r\niFPSClamp=0\r\n", encoding="latin-1")
    (g / "Data" / "FalloutNV.esm").write_bytes(b"TES4" + b"x" * 100)
    with _env(FNV_GAME_DIR=str(g), FNV_INSTANCES_ROOT=str(tmp / "instances")):
        instances.provision(0, appid=None)
        d = instances.instance_dir(0)
        fresh = _snapshot(d)
        names = (lowcost.FO, lowcost.PR)
        for lc_on, label in ((True, "lowcost"), (False, "normal")):
            real = be.RealBackend(lowcost=lc_on, mutant=m)
            p = core.Pool(_IniBackend(real), be.FakeSlots(available=(0,)), size=1, sync=True, clock=Clock(),
                          state_path=tmp / f"state-{label}.json")
            p.up()
            now = _snapshot(d)
            if lc_on:
                assert all(now[n] != fresh[n] for n in names), "the low-cost start must change both INIs"
                assert instances.ini_get(now[lowcost.FO].decode("latin-1"), "General", "iFPSClamp") == "30"
            else:
                changed = sorted(k for k in fresh if now.get(k) != fresh[k]) + sorted(set(now) - set(fresh))
                assert not changed, f"`up --no-lowcost` left non-provisioned files after a low-cost run: {changed}"
            p.down()


def check_restore_guard_hardlink(m):
    for name in (lowcost.FO, lowcost.PR):
        d = _inst_dir()
        src = Path(tempfile.mkdtemp(prefix="lowcost-src-")) / name
        src.write_bytes(b"[General]\r\nlinked=1\r\n")
        (d / name).unlink()
        os.link(src, d / name)
        before = _snapshot(d)
        try:
            lowcost.restore(d, check=(m != "no_guard"))
        except lowcost.UnsafeTarget:
            assert src.read_bytes() == b"[General]\r\nlinked=1\r\n", "refused restore wrote through the link"
            assert _snapshot(d) == before, "refused restore must write neither INI"
            continue
        assert False, f"restore must refuse a hard-linked {name} (writing would change the file it is linked to)"


def check_restore_guard_mygames(m):
    root = Path(tempfile.mkdtemp(prefix="lowcost-")) / "Documents" / "My Games" / "FalloutNV"
    root.mkdir(parents=True)
    (root / "Fallout.ini").write_text("[General]\r\n", encoding="latin-1")
    (root / "FalloutPrefs.ini").write_text("[Display]\r\n", encoding="latin-1")
    try:
        lowcost.restore(root, check=(m != "no_guard"))
    except lowcost.UnsafeTarget:
        return
    assert False, "restore must refuse the owner's My Games folder"


def check_doc_lists_every_key(m):
    doc = (REPO / "docs" / "tools" / "T4a-pool.md").read_text(encoding="utf-8")
    keys = [r.key for r in lowcost.PROFILE]
    if m == "doc_missing":
        keys.append("bNotDocumented")
    missing = [k for k in keys if f"`{k}`" not in doc]
    assert not missing, f"docs/tools/T4a-pool.md does not document: {missing}"


# ------------------------------------------------------------------------------------------------ daemon + client + slots
def check_daemon_client(m):
    import secrets
    import client
    import daemon
    from fake_server import FakeServer
    tmp = Path(tempfile.mkdtemp(prefix="poold-"))
    os.environ["POOL_DIR"] = str(tmp)
    b, s = be.FakeBackend(), be.FakeSlots(available=(90, 91))   # pipe nvcoop-90: never collides with a real game
    pool = core.Pool(b, s, size=2, sync=True, state_path=tmp / "state.json", mutant=m)
    pool.up()
    srv = daemon.Server(pool, secrets.token_hex(4))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    (tmp / "pool.json").write_text(json.dumps({"port": srv.server_address[1], "token": srv.token}))
    fake = FakeServer(90).start()
    try:
        with client.lease("t1", n=1, ttl_s=60) as L:
            assert L.instances == [90]
            nv = L.bridge()
            assert nv.ping()["pong"] and nv.lease_info()["held"], "bridge must hold the lock.* lease"
            assert [x["state"] for x in client.status()["instances"]] == ["leased", "warm"]
            L.dirty(90, "test")
        assert client.status()["leases"] == []
        assert b.starts.count(90) == 2, "dirty instance recycled through the daemon path"
        assert fake.lease is None, "bridge lock released with the lease"
        try:
            with client.lease("t2", n=1):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert b.starts.count(90) + b.starts.count(91) >= 3, "exception in the lessee marks the instance dirty"
        assert raises(client.PoolError, client.acquire, "x", 3, 5, 1), "n above the pool size is an error"
        L2 = client.acquire("t3", n=2)
        assert L2.instances == [90, 91]
        b.crash(90)
        pool.tick()
        assert raises(client.PoolError, L2.bridge, 90), "restart under a lease must be reported to the lessee"
        L2.release()
        bad = client.request  # wrong token is rejected
        (tmp / "pool.json").write_text(json.dumps({"port": srv.server_address[1], "token": "nope"}))
        assert raises(client.PoolError, bad, "status")
    finally:
        fake.stop()
        srv.shutdown()
        os.environ.pop("POOL_DIR", None)
        pool.down()


def check_client_not_running(m):
    import client
    os.environ["POOL_DIR"] = tempfile.mkdtemp(prefix="poolnone-")
    try:
        assert raises(client.PoolNotRunning, client.status)
    finally:
        os.environ.pop("POOL_DIR", None)


def check_real_slots_cli(m):
    tmp = tempfile.mkdtemp(prefix="gslots-")
    Path(tmp, "gameslot_config.json").write_text(json.dumps({"slots": 2, "min_free_gb": 0}))
    os.environ["GAMESLOT_DIR"] = tmp
    try:
        s = be.RealSlots(owner="pooltest", ttl_s=60, wait_s=2)
        ids = s.acquire(2)
        assert sorted(ids) == [0, 1], ids
        s.renew(2)
        try:
            be.RealSlots(owner="other", ttl_s=60, wait_s=0).acquire(1)
            assert False, "second owner must not get a slot while the pool holds both"
        except RuntimeError:
            pass
        s.release()
        assert be.RealSlots(owner="other", ttl_s=60, wait_s=0).acquire(1) == [0]
    finally:
        os.environ.pop("GAMESLOT_DIR", None)


REAL = [check_exclusive, check_pair_lease_order, check_size_clamped, check_recycle_after_k, check_dirty_recycles,
        check_expiry, check_renew, check_kill_only_own, check_reap_previous_only_recorded, check_reap_state_vanishes, check_crash_restart,
        check_dead_instance_never_leased, check_unresponsive_restart, check_memory_growth, check_start_failure,
        check_failed_start_stops_lease, check_slot_renew, check_pids_registered, check_lowcost_applies,
        check_lowcost_guard_hardlink, check_lowcost_guard_mygames, check_owner_inis_untouched,
        check_nolowcost_restores_provisioned_inis, check_restore_guard_hardlink, check_restore_guard_mygames,
        check_doc_lists_every_key, check_daemon_client, check_client_not_running, check_real_slots_cli]

TWINS = [(check_exclusive, "double_lease"), (check_recycle_after_k, "no_recycle_k"), (check_dirty_recycles, "no_dirty_recycle"),
         (check_expiry, "no_expire"), (check_kill_only_own, "kill_foreign"), (check_lowcost_guard_hardlink, "no_guard"),
         (check_lowcost_guard_mygames, "no_guard"), (check_nolowcost_restores_provisioned_inis, "no_restore"),
         (check_restore_guard_hardlink, "no_guard"), (check_restore_guard_mygames, "no_guard"),
         (check_doc_lists_every_key, "doc_missing"), (check_daemon_client, "no_dirty_recycle"),
         (check_reap_state_vanishes, "reap_unlink_strict")]


def main() -> int:
    bad = []
    n = 0
    for fn in REAL:
        n += 1
        try:
            fn(None)
            print("ok   ", fn.__name__)
        except Exception:  # noqa: BLE001
            bad.append(fn.__name__)
            print("FAIL ", fn.__name__)
            traceback.print_exc()
    for fn, mut in TWINS:
        n += 1
        try:
            fn(mut)
        except AssertionError:
            print("ok    twin %s[%s] caught" % (fn.__name__, mut))
        except Exception:  # noqa: BLE001  (a crash is not a caught defect)
            bad.append(f"{fn.__name__}[{mut}]")
            print("FAIL  twin %s[%s] crashed instead of failing an assertion" % (fn.__name__, mut))
            traceback.print_exc()
        else:
            bad.append(f"{fn.__name__}[{mut}]")
            print("FAIL  twin %s[%s] NOT caught" % (fn.__name__, mut))
    print(f"\n{n} checks, {len(bad)} failed")
    if bad:
        print("FAILED:", ", ".join(bad))
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
