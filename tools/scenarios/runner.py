"""Scenario runner: jump to a scenario on warm instances, run its steps and checks.

The runner is duck-typed over the bridge object (exec / eval / call / wait_stage / load_save / save_game), so the tests drive it
with an in-memory fake and real runs hand it nvbridge.NvBridge objects from the pool lease (tools/pool/client.py).

Jump = (reload the start save only when plan_jump says so) -> frames -> test.quest.reset(ids) -> setup steps. On a warm instance
with a clean world that is a few quest-reset calls and some console lines: seconds, not a 60-90 s save load.
"""
import contextlib
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path

try:
    import msvcrt   # Windows: byte-range lock on the sidecar lock file
except ImportError:   # POSIX
    msvcrt = None
    import fcntl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools" / "runner"))
import scenlib  # noqa: E402

PARSE_ERRORS = ("could not parse", "error line", "unknown", "invalid")   # same list as the L7 sweep
# test-only: "noreload" | "passall" | "firsthost" | "nodirty" | "oldkey" | "wholedict" | "noreread" | "inplace"
MUTANT = os.environ.get("SCEN_MUTANT", "")


class ScenarioError(Exception):
    pass


class StateLockTimeout(TimeoutError):
    """Another process held the lock of the state file for longer than the timeout."""


def _try_lock(fd):
    """Lock byte 0 of the open file fd without waiting. Raises OSError when another handle holds the lock."""
    if msvcrt:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd):
    if msvcrt:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


class StateStore:
    """Per-game-process memory of what is loaded and what the last scenario dirtied.

    A key must name one game process, never only a slot: a key that a later game can repeat makes the next jump skip a
    needed save load, so the checks run against a fresh game at the main menu or a different world. The pool generation
    is not enough (it starts again at 1 on every pool start). Build keys with lease_keys() for pool instances.

    With a path, more than one process can use the same file (two scen.py runs, or two proof.py runs, with leases on
    different instances at the same time). So every read and every write of the file holds an exclusive lock on the
    sidecar file <path>.lock (msvcrt.locking on Windows, fcntl.flock elsewhere). get() and get_many() read the file
    again, so a plan sees what another process wrote. put() and put_many() read the file, change only their own keys,
    write a temporary file in the same folder and replace the target with it, so the target is never a partial file."""

    LOCK_TIMEOUT_S = 30.0
    LOCK_POLL_S = 0.005

    def __init__(self, path=None, lock_timeout_s=None):
        self.path = Path(path) if path else None
        self.lock_path = self.path.with_name(self.path.name + ".lock") if self.path else None
        self.lock_timeout_s = self.LOCK_TIMEOUT_S if lock_timeout_s is None else lock_timeout_s
        self.data = self._read_shared() if self.path else {}

    def get(self, key):
        return self.get_many([key])[0]

    def get_many(self, keys):
        """The entries for `keys` (None for a missing key), from one read of the file."""
        if self.path and MUTANT != "noreread":
            self.data = self._read_shared()
        return [self.data.get(k) for k in keys]

    def put(self, key, st):
        self.put_many({key: st})

    def put_many(self, items):
        """Store the entries of the dict `items`. With a path: lock, read the file, change only these keys, then
        replace the file atomically. Keys that other processes wrote stay as they are on disk."""
        if not self.path:
            self.data.update(items)
            return
        with self._lock():
            # mutant "wholedict": the old write of the whole in-memory dict, which loses newer keys of other processes
            data = dict(self.data) if MUTANT == "wholedict" else self._load()
            data.update(items)
            self._commit(data)
            self.data = data

    # ---- file access (the caller of _load and _commit holds the lock)
    @contextlib.contextmanager
    def _lock(self):
        """Exclusive cross-process lock on <path>.lock. Retries for lock_timeout_s, then raises StateLockTimeout."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            deadline = time.monotonic() + self.lock_timeout_s
            while True:
                try:
                    _try_lock(fd)
                    break
                except OSError as e:
                    if time.monotonic() >= deadline:
                        raise StateLockTimeout(
                            "state file %s: another process held the lock file %s for more than %.1f s (%s). Make sure "
                            "that no scen.py or proof.py process is stuck, then try again."
                            % (self.path, self.lock_path, self.lock_timeout_s, e)) from None
                    time.sleep(self.LOCK_POLL_S)
            try:
                yield
            finally:
                _unlock(fd)
        finally:
            os.close(fd)

    def _read_shared(self):
        if not self.path.parent.is_dir():   # nothing stored yet; do not make folders only to read
            return {}
        with self._lock():
            return self._load()

    def _load(self):
        """The file as a dict. A missing file, invalid JSON or a value that is not an object gives {}."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _commit(self, data):
        """Write data to a temporary file in the same folder, then replace the target with it in one step. No fsync: the
        state only describes live game processes, and a power loss ends those too (new boot ids, so new keys)."""
        text = json.dumps(data, indent=1, sort_keys=True)
        if MUTANT == "inplace":   # test-only: the old write into the target itself
            self.path.write_text(text, encoding="utf-8")
            return
        tmp = self.path.with_name("%s.%d.%s.tmp" % (self.path.name, os.getpid(), secrets.token_hex(4)))
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            self._replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _replace(self, src, dst):
        """os.replace with a bounded retry. On Windows, it fails while another program (for example a virus scanner)
        has the target open. The tests replace this method to simulate a failure between the write and the replace."""
        deadline = time.monotonic() + min(self.lock_timeout_s, 5.0)
        while True:
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    @staticmethod
    def key(instance, process):
        """instance@process. `process` must identify one game process (see lease_keys)."""
        return "%s@%s" % (instance, process)

    @staticmethod
    def lease_keys(lease):
        """One key per lease instance, host first, from the id the pool gives each game process (client.Lease.boot).
        The same live game gets the same key on every lease, so a later run still jumps in place. A new game process
        gets a new key, also after a pool restart. When the pool reports no id (an older pool), the keys are new for
        this call, so the first jump reloads the save."""
        if MUTANT == "oldkey":   # test-only: the old instance@generation key, which repeats after a pool restart
            gens = getattr(lease, "_gens", {})
            return [StateStore.key(i, gens.get(i, 0)) for i in lease.instances]
        boot = getattr(lease, "boot", None)
        unknown = "unknown-" + secrets.token_hex(6)
        out = []
        for i in lease.instances:
            b = boot(i) if callable(boot) else ""
            out.append(StateStore.key(i, b or unknown))
        return out


class Runner:
    def __init__(self, bridges, store=None, keys=None, capabilities=None, sleep=time.sleep, now=time.monotonic,
                 frames=90, log=print, order=None, poll_s=0.25):
        self.nvs = list(bridges)
        self.store = store or StateStore()
        if keys:
            self.keys = list(keys)
        elif MUTANT == "oldkey":
            self.keys = [StateStore.key(i, 0) for i in range(len(self.nvs))]
        else:
            # No keys given: nothing says which game process is behind the bridges, so the state is private to this
            # Runner. Jumps after the first one still go in place; a persistent store never lends this Runner old state.
            run = "run-" + secrets.token_hex(6)
            self.keys = [StateStore.key(i, run) for i in range(len(self.nvs))]
        self.caps = set(capabilities) if capabilities is not None else set(scenlib.PRESENT)
        self.sleep, self.now, self.frames, self.log, self.poll_s = sleep, now, frames, log, poll_s
        self.order = order or scenlib.load_order()

    # ---- state of the (shared-world) scenario: stored on the host's key
    def _state(self):
        return self.store.get(self.keys[0])

    def _same_world(self):
        """All players must share the same loaded state; any differing key forces a reload. The store reads its file
        again here, so the plan sees what another process wrote."""
        sts = self.store.get_many(self.keys)
        return sts[0] if all(s == sts[0] for s in sts) else None

    # ---- one action on one bridge
    def _do(self, nv, act, res):
        kind = act[0]
        if kind == "exec":
            # ("exec", line) or ("exec", line, ref): ref is the calling reference (exec {cmd, ref}); the game's console
            # has no "<hex>.Func" form
            ref = act[2] if len(act) > 2 else None
            r = nv.exec(act[1]) if ref is None else nv.exec(act[1], ref=ref)
            out = (r.get("output") or "") if isinstance(r, dict) else ""
            low = out.lower()
            if (isinstance(r, dict) and r.get("ok") is False) or any(p in low for p in PARSE_ERRORS):
                raise ScenarioError("%s%s -> %s" % (act[1], "" if ref is None else " (ref %s)" % ref, out.strip()[:120] or r))
        elif kind == "call":
            nv.call(act[1], act[2])
        elif kind == "stage":
            try:
                nv.wait_stage(act[1], act[2], 8000)
            except Exception:   # noqa: BLE001  (a quest script may legitimately move the stage on, as in the L7 sweep)
                pass
        elif kind == "wait":
            self.sleep(act[1])
        elif kind == "load":
            nv.load_save(act[1])
            self.sleep(self.frames / 60.0)
        elif kind == "save":
            nv.save_game(act[1], overwrite=True)
        else:
            raise ScenarioError("bad action %r" % (act,))

    # ---- checks
    def _check(self, sc, st, res):
        idx = scenlib.step_target(st, sc)
        expr, cmp_, value, agg, tol, timeout, ref = scenlib.check_expr(st, res)
        if expr is None and st["kind"] not in ("owner", "spawn_count"):
            raise ScenarioError("check kind %s has no expression" % st["kind"])
        if expr is None:
            expr = "call:%s" % st["kind"]
        t0 = self.now()
        while True:
            if st["kind"] == "owner":
                vals = [float(self.nvs[i].call("coop.owner", {"ref": res.hex(st["ref"])})["owner"]) for i in idx]
            elif st["kind"] == "spawn_count":
                vals = [float(self.nvs[i].call(scenlib.CAPABILITIES["ref_count"]["method"], {"base": res.hex(st["base"])})["count"]) for i in idx]
            else:
                vals = [self.nvs[i].eval(expr) if ref is None else self.nvs[i].eval(expr, ref=ref) for i in idx]
            shown = expr if ref is None else "%s (ref %s)" % (expr, ref)
            if scenlib.compare(vals, cmp_, value, agg, tol) or MUTANT == "passall":
                return {"status": "pass", "values": vals, "expr": shown}
            if self.now() - t0 >= timeout:
                return {"status": "fail", "values": vals, "expr": shown,
                        "detail": "%s %s %s%s (agg %s) not met after %.1fs; values per player %s" % (
                            st["kind"], cmp_, value, " +-%s" % tol if tol else "", agg, timeout, vals)}
            self.sleep(self.poll_s)

    # ---- one step
    def _step(self, sc, st, res):
        rec = {"id": st["id"], "op": st["op"], "status": "pass"}
        miss = sorted(scenlib.needs_of(st) - self.caps)
        if miss:
            rec.update(status="blocked", detail="needs capability %s (not available: %s)" % (
                ", ".join(miss), "; ".join("%s=%s" % (m, scenlib.CAPABILITIES[m].get("method", "?")) for m in miss)))
            return rec
        try:
            if st["op"] == "check":
                rec.update(self._check(sc, st, res))
            else:
                acts = scenlib.step_actions(st, res)
                idx = scenlib.step_target(st, sc)
                if MUTANT == "firsthost":
                    idx = [0]
                def one(i):
                    for a in acts:
                        self._do(self.nvs[i], a, res)
                if st.get("parallel") and len(idx) > 1:
                    errs = []

                    def guarded(i):
                        try:
                            one(i)
                        except Exception as ex:   # noqa: BLE001
                            errs.append(ex)
                    ths = [threading.Thread(target=guarded, args=(i,)) for i in idx]
                    for t in ths:
                        t.start()
                    for t in ths:
                        t.join()
                    if errs:
                        raise errs[0]
                else:
                    for i in idx:
                        one(i)
                if st["op"] in ("moveto", "spawn", "recruit", "setstage"):
                    self.sleep(0.2)
        except ScenarioError as e:
            rec.update(status="error", detail=str(e))
        except Exception as e:   # noqa: BLE001
            rec.update(status="error", detail="%s: %s" % (type(e).__name__, str(e)[:200]))
        return rec

    # ---- jump
    def jump(self, sc, force_reload=False):
        """Bring every instance into the scenario's starting state. Returns {"reload", "reasons", "seconds", "setup": [...]}."""
        t0 = self.now()
        res = scenlib.Resolver(sc, self.order)
        fp = scenlib.footprint(sc, order=self.order)
        st = self._same_world()
        plan = scenlib.plan_jump(st, sc, fp, force_reload)
        if MUTANT == "noreload":
            plan = {"reload": False, "reasons": []}
        save = sc["start"]["save"]
        if plan["reload"]:
            for nv in self.nvs:
                nv.load_save(save)
            self.sleep(self.frames / 60.0)
        ids = scenlib.reset_ids(sc, self.order)
        prev = set((st or {}).get("quest_hex") or [])
        # quests left changed by the previous scenario are reset too (in-place jumps must not inherit stage/var state)
        ids = sorted(set(ids) | (prev if not plan["reload"] else set()))
        for nv in self.nvs:
            nv.call("test.quest.reset", {"quests": ids})
        out = []
        for s in sc.get("setup") or []:
            rec = self._step(sc, s, res)
            out.append(rec)
            if rec["status"] in ("error", "blocked"):
                break
        new = {"loaded": True, "save": save, "dirty": sorted(fp["dirty"]), "failed": False, "quest_hex": ids, "scenario": sc["name"]}
        self.store.put_many({k: new for k in self.keys})
        return {"reload": plan["reload"], "reasons": plan["reasons"], "seconds": round(self.now() - t0, 2), "setup": out,
                "reset": ids}

    # ---- run
    def run(self, sc, force_reload=False):
        t0 = self.now()
        res = scenlib.Resolver(sc, self.order)
        errs = scenlib.validate(sc)
        if errs:
            return {"scenario": sc.get("name"), "status": "invalid", "errors": errs, "steps": [], "seconds": 0.0}
        j = self.jump(sc, force_reload)
        steps = list(j["setup"])
        bad_setup = [r for r in steps if r["status"] in ("error",)]
        if not bad_setup:
            blocked_by = None
            for s in sc["steps"]:
                if blocked_by:
                    # what follows a step we could not run is undefined: report it as blocked instead of guessing
                    steps.append({"id": s["id"], "op": s["op"], "status": "blocked",
                                  "detail": "skipped: depends on blocked step %s" % blocked_by})
                    continue
                rec = self._step(sc, s, res)
                steps.append(rec)
                if rec["status"] == "blocked":
                    blocked_by = s["id"]
        fp = scenlib.footprint(sc, order=self.order)
        stats = [r["status"] for r in steps]
        if "fail" in stats:
            status = "fail"
        elif "error" in stats:
            status = "error"
        elif "blocked" in stats:
            status = "partial"
        else:
            status = "pass"
        if MUTANT == "nodirty":
            fp = {"dirty": set(), "touched": fp["touched"], "quests": fp["quests"]}
        st = {"loaded": True, "save": sc["start"]["save"], "dirty": sorted(fp["dirty"]),
              "failed": status in ("fail", "error"), "quest_hex": j["reset"], "scenario": sc["name"]}
        self.store.put_many({k: st for k in self.keys})
        return {"scenario": sc["name"], "status": status, "jump": {k: j[k] for k in ("reload", "reasons", "seconds")},
                "steps": steps, "seconds": round(self.now() - t0, 2)}


def for_lease(lease, store_path, **kw):
    """Runner over every instance of a pool lease (tools/pool/client.py), host first, with its state in the file
    store_path keyed by game process (StateStore.lease_keys). kw goes to Runner."""
    return Runner([lease.bridge(i) for i in lease.instances], StateStore(store_path), StateStore.lease_keys(lease), **kw)


def summarize(results):
    lines = []
    for r in results:
        j = r.get("jump") or {}
        lines.append("%-8s %-28s %6.1fs  jump:%s" % (r["status"].upper(), r["scenario"], r["seconds"],
                                                      "reload" if j.get("reload") else "in-place"))
        for s in r.get("steps", []):
            if s["status"] != "pass":
                lines.append("    %s %s: %s" % (s["status"], s["id"], s.get("detail", "")))
        for e in r.get("errors", []):
            lines.append("    invalid: %s" % e)
    return "\n".join(lines)
