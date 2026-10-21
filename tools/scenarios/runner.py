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
import prepare_save  # noqa: E402

try:
    import nvbridge   # tools/runner: RpcError (the game refused a call; a lost game raises ConnectionLost, never caught here)
    _REFUSED = (nvbridge.RpcError,)
except Exception:   # noqa: BLE001 - not importable off Windows
    _REFUSED = ()

PARSE_ERRORS = ("could not parse", "error line", "unknown", "invalid")   # same list as the L7 sweep
# test-only: "noreload" | "passall" | "firsthost" | "nodirty" | "oldkey" | "wholedict" | "noreread" | "inplace"
#            | "noprepared" | "longquiet" | "fullwait" | "nostall" | "hairtrigger" | "latesuccess" | "nodesyncs"
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
                 frames=90, log=print, order=None, poll_s=0.25, prepared=None):
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
        # Prepared saves (prepare_save.py): None = use them when the runner knows its game processes (keys given) and the file is there;
        # False = never; or a PreparedSaves-like object with pick(instance, save) -> (name or None, why).
        if prepared is None and keys and not os.environ.get("NVCOOP_NO_PREPARED"):
            prepared = prepare_save.PreparedSaves()
        self.prepared = prepared or None
        self.instance_ids = [self._bridge_instance(nv) if self._bridge_instance(nv) is not None else self._instance_of(k, i)
                             for i, (nv, k) in enumerate(zip(self.nvs, self.keys))]

    @staticmethod
    def _bridge_instance(nv):
        """The pool instance id in the pipe name of a real bridge (\\.\pipe\nvcoop-<n>), else None. Callers that key their runner by
        position (the playbook adapter does) would otherwise look up prepared saves of instance 0 and 1 for pair 1 (instances 2 and 3)."""
        if MUTANT == "nobridgeid":   # test-only: trust the key only
            return None
        m = re.search(r"nvcoop-(\d+)$", str(getattr(nv, "name", "") or ""))
        return int(m.group(1)) if m else None

    @staticmethod
    def _instance_of(key, index):
        """The pool instance id in a key "instance@process"; the position of the bridge when the key says none."""
        head = str(key).split("@", 1)[0]
        return int(head) if head.isdigit() else index

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
        # In a co-op session story state only moves forward (stage = max): an in-place quest reset on the host changes its world
        # but not the session's shared state, so the guests get "healed" back to the old stages. Reload instead: a load starts a
        # new world epoch and everyone rejoins from it (two-copy fight test 2026-10-21: VCG00 stage 0 on the host, 100 on the guest).
        if not plan["reload"] and scenlib.reset_ids(sc, self.order) and self._in_coop():
            plan = {"reload": True, "reasons": list(plan.get("reasons") or []) + ["co-op session: quest resets need a reload"]}
        save = sc["start"]["save"]
        coop_sync = None
        loaded = None
        if plan["reload"]:
            loaded = self._load_start_save(sc)
            self.sleep(self.frames / 60.0)
            # Every client reloaded its own save, so it rejoins the co-op session. Setup steps the host runs before a client has
            # finished that are applied to a game that is still loading, and the load then overwrites them (seen in the two-copy
            # fight test 2026-10-21: the geckos the host enabled stayed disabled on the guest). Wait until the session is synced.
            coop_sync = self._wait_coop_synced()
        # A prepared save has its boxes pressed away already: a short quiet window is only a safety net for a stray notice.
        all_prepared = bool(loaded) and all(x["prepared"] for x in loaded) and MUTANT != "longquiet"
        boxes = self._clear_load_messages(quiet_s=prepare_save.PREPARED_QUIET_S if all_prepared else prepare_save.PLAIN_QUIET_S)             if plan["reload"] else None
        ids = scenlib.reset_ids(sc, self.order)
        prev = set((st or {}).get("quest_hex") or [])
        # quests left changed by the previous scenario are reset too (in-place jumps must not inherit stage/var state)
        ids = sorted(set(ids) | (prev if not plan["reload"] else set()))
        for nv in self.nvs:
            nv.call("test.quest.reset", {"quests": ids})
        if ids and self._in_coop():
            # The reset changed every machine's world outside the session, and story state only moves forward (stage = max),
            # so the host's shared state still has the old stages and would heal the guests back to them. Re-dump the host world as
            # a new epoch (coop.rebaseline, test builds only): the guests rebase onto the reset state.
            coop_sync = dict(coop_sync or {}, rebaseline=self._rebaseline_host())
            coop_sync["afterRebaseline"] = self._wait_coop_synced()
        out = []
        for s in sc.get("setup") or []:
            rec = self._step(sc, s, res)
            out.append(rec)
            if rec["status"] in ("error", "blocked"):
                break
        new = {"loaded": True, "save": save, "dirty": sorted(fp["dirty"]), "failed": False, "quest_hex": ids, "scenario": sc["name"]}
        self.store.put_many({k: new for k in self.keys})
        return {"reload": plan["reload"], "reasons": plan["reasons"], "seconds": round(self.now() - t0, 2), "setup": out,
                "reset": ids, "coopSync": coop_sync, "loadMessages": boxes, "loadedSaves": loaded}

    def _load_start_save(self, sc):
        """Load the scenario's start save on every instance, the prepared one where the instance has one (prepare_save.py) and it
        works. -> [{"instance", "save" (what was loaded), "prepared", "why"}], so a run log says which save each copy came from."""
        save = sc["start"]["save"]
        wanted = (sc.get("start") or {}).get("prepared", True)
        out = []
        for k, nv in enumerate(self.nvs):
            name, why = None, "prepared saves are off"
            if self.prepared and not wanted:
                why = "scenario sets start.prepared false"
            elif self.prepared and MUTANT != "noprepared":
                name, why = self.prepared.pick(self.instance_ids[k], save)
            used = save
            if name:
                try:
                    nv.load_save(name)
                    used = name
                except _REFUSED as e:   # the game refused the prepared save: the plain one still works
                    name, why = None, "prepared save refused (%s); loaded %s" % (str(e)[:80], save)
            if not name:
                nv.load_save(save)
            out.append({"instance": self.instance_ids[k], "save": used, "prepared": bool(name), "why": why})
        return out

    def _clear_load_messages(self, quiet_s=prepare_save.PLAIN_QUIET_S, max_s=150.0, poll_s=0.5):
        """After a reload: press away the message boxes a load raises, until none has shown on any instance for `quiet_s`.
        With the pre-order packs and all five DLC a load raises 14 (4 pack notices, then each DLC's intro and level-cap note) in waves
        over about 50 s, with 12-16 s gaps (measured 2026-10-21). An open box keeps the game in menu mode and every bot input is lost,
        so a fight that starts under one shoots nothing. Returns {pressed, seconds, quiet} or None without bot.menu (older builds)."""
        t0 = last = self.now()
        pressed = 0
        while True:
            seen = False
            for nv in self.nvs:
                try:
                    kind = nv.call("bot.menu").get("kind")
                except Exception:  # noqa: BLE001 - no bot module ([dev] bots=0) or a fake bridge
                    return None if not pressed else {"pressed": pressed, "seconds": round(self.now() - t0, 2), "quiet": False}
                if kind == "message":
                    seen = True
                    try:
                        nv.call("bot.menu.press", {"button": 0})
                        pressed += 1
                    except Exception:  # noqa: BLE001 - a box that is closing refuses the press: try again next poll
                        pass
            now = self.now()
            if seen:
                last = now
            if now - last >= quiet_s or now - t0 >= max_s:
                return {"pressed": pressed, "seconds": round(now - t0, 2), "quiet": now - last >= quiet_s}
            self.sleep(poll_s)

    def _rebaseline_host(self):
        for nv in self.nvs:
            try:
                if nv.call("coop.status").get("role") == "host":
                    return nv.call("coop.rebaseline")
            except Exception as e:  # noqa: BLE001 - an older build without the method: report it, the jump goes on
                return {"ok": False, "error": str(e)}
        return None

    def _in_coop(self):
        """True when these instances form a co-op session (a host and at least one client)."""
        if len(self.nvs) < 2:
            return False
        try:
            roles = [nv.call("coop.status").get("role") for nv in self.nvs]
        except Exception:  # noqa: BLE001 - no co-op module
            return False
        return "host" in roles and "client" in roles

    def _wait_coop_synced(self, timeout_s=180.0, poll_s=0.5, digest_timeout_s=60.0, stall_s=30.0, digest_stall_s=20.0):
        """After a reload: wait until every co-op client has rejoined and applied everything the host committed, then until the
        host has judged one desync digest as ok after that (a rejoin may be healed first: those repairs belong to the jump, not to
        the test that runs next, and are reported here as settleRepairs). A no-op (None) when the instances are not in a co-op
        session (no coop.status method, or role off).

        Event-driven: it returns the moment the condition holds. It also gives up early, with a reason, when nothing moves:
        a client that rejected the join, or client progress (joined, applied, baseline, connection) that stood still for stall_s,
        or (digest part, _wait_digest_ok) no digest, stale digests, or the same mismatch again and again. timeout_s and
        digest_timeout_s stay as the outer limits. The result has synced (bool) and, when false, reason."""
        if len(self.nvs) < 2:
            return None
        t0 = self.now()
        last = {}
        sig, sig_at = None, t0
        while True:
            try:
                sts = [nv.call("coop.status") for nv in self.nvs]
            except Exception:  # noqa: BLE001 - no co-op module on these instances
                return None
            hosts = [s for s in sts if s.get("role") == "host"]
            clients = [s for s in sts if s.get("role") == "client"]
            if not hosts or not clients:
                return None
            head = (hosts[0].get("host") or {}).get("lastSeq", 0)
            cl = [c.get("client") or {} for c in clients]
            conn = [(c.get("transport") or {}).get("connected", True) for c in clients]
            # the plugin names it "syncing" (client.baselineActive()); older fakes and notes say baselineActive
            syncing = [bool(x.get("baselineActive", x.get("syncing"))) for x in cl]
            ready = all(x.get("joined") and not sy and x.get("applied", -1) >= head and cn for x, sy, cn in zip(cl, syncing, conn))
            last = {"head": head, "applied": [x.get("applied") for x in cl]}
            now = self.now()
            if ready:
                self.sleep(1.0)  # let the last applied effects reach the engine
                return dict(last, synced=True, seconds=round(self.now() - t0, 2), **self._wait_digest_ok(digest_timeout_s, poll_s, digest_stall_s))
            rej = [x for x in cl if x.get("rejected")]
            if rej:
                return dict(last, synced=False, seconds=round(now - t0, 2),
                            reason="client rejected the join: %s" % (rej[0].get("rejectText") or rej[0].get("rejectReason") or "no text"))
            # progress = anything on the client side that changed; the host head moving alone is not progress for a stuck client
            cur = (tuple(x.get("applied") for x in cl), tuple(bool(x.get("joined")) for x in cl), tuple(syncing), tuple(conn))
            if cur != sig:
                sig, sig_at = cur, now
            elif now - sig_at >= stall_s and MUTANT != "nostall":
                why = []
                for n, (x, sy, cn) in enumerate(zip(cl, syncing, conn)):
                    if not cn:
                        why.append("client %d: transport not connected" % n)
                    elif not x.get("joined"):
                        why.append("client %d: not joined" % n)
                    elif sy:
                        why.append("client %d: baseline still running" % n)
                    elif x.get("applied", -1) < head:
                        why.append("client %d: applied %s of %s" % (n, x.get("applied"), head))
                return dict(last, synced=False, seconds=round(now - t0, 2),
                            reason="no client progress for %.0f s (%s)" % (now - sig_at, "; ".join(why) or "unknown"))
            if now - t0 > timeout_s:
                return dict(last, synced=False, seconds=round(now - t0, 2), reason="timeout after %.0f s" % timeout_s)
            self.sleep(poll_s)

    @staticmethod
    def _new_desync_entries(desyncs, since_id, limit=8):
        """The mismatching entries of the desync reports newer than since_id, newest first: [{id, key, host, client, repaired}]."""
        out = []
        for r in sorted((desyncs or {}).get("desyncs") or [], key=lambda r: r.get("id", 0), reverse=True):
            if r.get("id", 0) <= since_id:
                break
            for e in r.get("entries") or []:
                out.append({"id": r.get("id"), "key": e.get("key"), "host": e.get("host"), "client": e.get("client"),
                            "repaired": bool(r.get("repaired"))})
        return out[:limit]

    def _wait_digest_ok(self, timeout_s, poll_s, stall_s=20.0, repeat_limit=3, stale_limit=3):
        """Wait until the host's desync digests (coop.status desyncs.digests) count one more 'ok' than now; return the moment they do.
        Gives up early, with settleReason and the mismatching entries (desyncEntries, from coop.status desyncs.desyncs), when:
          * the same mismatch comes back repeat_limit times (the same keys with the same host and client values: the repair is not working),
          * stale_limit digests were stale (the host cannot judge them),
          * no digest arrived for stall_s (the client does not send, or the link is down).
        Rate-limited digests are normal and count as arrived. timeout_s is the outer limit."""
        host = None
        for nv in self.nvs:
            try:
                if nv.call("coop.status").get("role") == "host":
                    host = nv
                    break
            except Exception:  # noqa: BLE001
                return {}
        if host is None:
            return {}

        def status():
            return host.call("coop.status").get("desyncs") or {}

        try:
            ds0 = status()
        except Exception:  # noqa: BLE001
            return {}
        d0 = ds0.get("digests") or {}
        if not d0:
            return {}
        base_id = max([r.get("id", 0) for r in ds0.get("desyncs") or []] or [0])
        t0 = self.now()
        rx, rx_at = d0.get("received", 0), t0
        mismatch, repeats, prev_sig = d0.get("mismatch", 0), 0, None
        limit = 1 if MUTANT == "hairtrigger" else repeat_limit

        def failed(reason, ds):
            d = ds.get("digests") or {}
            out = {"digestOk": False, "settleReason": reason, "settleRepairs": d.get("repairs", 0) - d0.get("repairs", 0),
                   "settleSeconds": round(self.now() - t0, 2),
                   "digestDelta": {k: d.get(k, 0) - d0.get(k, 0) for k in ("received", "ok", "mismatch", "stale", "rateLimited")}}
            if MUTANT != "nodesyncs":
                out["desyncEntries"] = self._new_desync_entries(ds, base_id)
            return out

        ds = ds0
        while self.now() - t0 <= timeout_s:
            ds = status()
            d = ds.get("digests") or {}
            now = self.now()
            if d.get("ok", 0) > d0.get("ok", 0):
                if MUTANT == "latesuccess":   # test-only: notice the success only when the whole timeout has passed
                    self.sleep(max(0.0, timeout_s - (self.now() - t0)))
                return {"digestOk": True, "settleReason": "ok", "settleRepairs": d.get("repairs", 0) - d0.get("repairs", 0),
                        "settleSeconds": round(self.now() - t0, 2)}
            if MUTANT != "fullwait":
                if d.get("received", 0) != rx:
                    rx, rx_at = d.get("received", 0), now
                grown = d.get("mismatch", 0) - mismatch
                if grown > 0:
                    mismatch = d.get("mismatch", 0)
                    newest = self._new_desync_entries(ds, base_id, limit=1000)
                    top = max([e["id"] for e in newest] or [0])
                    sig = frozenset((e["key"], e["host"], e["client"]) for e in newest if e["id"] == top)
                    repeats = repeats + grown if sig == prev_sig else 1
                    prev_sig = sig
                    if repeats >= limit:
                        return failed("same mismatch %d times in a row and the repair did not fix it (%d repairs so far)" % (
                            repeats, d.get("repairs", 0) - d0.get("repairs", 0)), ds)
                if d.get("stale", 0) - d0.get("stale", 0) >= stale_limit:
                    return failed("%d stale digests: the host cannot judge them" % (d.get("stale", 0) - d0.get("stale", 0)), ds)
                if now - rx_at >= stall_s:
                    return failed("no digest arrived for %.0f s" % (now - rx_at), ds)
            self.sleep(poll_s)
        return failed("no ok digest within %.0f s" % timeout_s, ds)

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
        return {"scenario": sc["name"], "status": status, "jump": {k: j[k] for k in ("reload", "reasons", "seconds", "loadedSaves", "coopSync", "loadMessages")},
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
