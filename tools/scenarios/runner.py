"""Scenario runner: jump to a scenario on warm instances, run its steps and checks.

The runner is duck-typed over the bridge object (exec / eval / call / wait_stage / load_save / save_game), so the tests drive it
with an in-memory fake and real runs hand it nvbridge.NvBridge objects from the pool lease (tools/pool/client.py).

Jump = (reload the start save only when plan_jump says so) -> frames -> test.quest.reset(ids) -> setup steps. On a warm instance
with a clean world that is a few quest-reset calls and some console lines: seconds, not a 60-90 s save load.
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools" / "runner"))
import scenlib  # noqa: E402

PARSE_ERRORS = ("could not parse", "error line", "unknown", "invalid")   # same list as the L7 sweep
MUTANT = os.environ.get("SCEN_MUTANT", "")   # test-only: "noreload" | "passall" | "firsthost" | "nodirty"


class ScenarioError(Exception):
    pass


class StateStore:
    """Per-instance memory of what is loaded and what the last scenario dirtied. Keyed by (instance, generation) so a
    restarted game (new generation) is treated as empty."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, st):
        self.data[key] = st
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")

    @staticmethod
    def key(instance, generation):
        return "%s@%s" % (instance, generation)


class Runner:
    def __init__(self, bridges, store=None, keys=None, capabilities=None, sleep=time.sleep, now=time.monotonic,
                 frames=90, log=print, order=None, poll_s=0.25):
        self.nvs = list(bridges)
        self.store = store or StateStore()
        self.keys = list(keys) if keys else [StateStore.key(i, 0) for i in range(len(self.nvs))]
        self.caps = set(capabilities) if capabilities is not None else set(scenlib.PRESENT)
        self.sleep, self.now, self.frames, self.log, self.poll_s = sleep, now, frames, log, poll_s
        self.order = order or scenlib.load_order()

    # ---- state of the (shared-world) scenario: stored on the host's key
    def _state(self):
        return self.store.get(self.keys[0])

    def _same_world(self):
        """All players must share the same loaded state; any differing key forces a reload."""
        sts = [self.store.get(k) for k in self.keys]
        return sts[0] if all(s == sts[0] for s in sts) else None

    # ---- one action on one bridge
    def _do(self, nv, act, res):
        kind = act[0]
        if kind == "exec":
            r = nv.exec(act[1])
            out = (r.get("output") or "") if isinstance(r, dict) else ""
            low = out.lower()
            if (isinstance(r, dict) and r.get("ok") is False) or any(p in low for p in PARSE_ERRORS):
                raise ScenarioError("%s -> %s" % (act[1], out.strip()[:120] or r))
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
        expr, cmp_, value, agg, tol, timeout = scenlib.check_expr(st, res)
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
                vals = [self.nvs[i].eval(expr) for i in idx]
            if scenlib.compare(vals, cmp_, value, agg, tol) or MUTANT == "passall":
                return {"status": "pass", "values": vals, "expr": expr}
            if self.now() - t0 >= timeout:
                return {"status": "fail", "values": vals, "expr": expr,
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
        for k in self.keys:
            self.store.put(k, new)
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
        for k in self.keys:
            self.store.put(k, st)
        return {"scenario": sc["name"], "status": status, "jump": {k: j[k] for k in ("reload", "reasons", "seconds")},
                "steps": steps, "seconds": round(self.now() - t0, 2)}


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
