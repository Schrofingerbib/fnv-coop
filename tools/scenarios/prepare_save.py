#!/usr/bin/env python3
"""Prepared saves: the scenario start save with the load message boxes already pressed away, so a jump skips about 50 s of boxes.

A load of the test save raises 14 message boxes over about 50 s (4 pre-order pack notices, then each DLC's intro and level-cap note),
because the save is older than the DLC intros. Press them away once, save the game as "nvcoop_<start save>_ready" on each copy, and
let runner.Runner.jump() load that save instead. The jump keeps its box clearing as a safety net, but waits only a few seconds
when every copy loaded a prepared save.

    python -I tools/scenarios/prepare_save.py NAME [NAME ...]    # scenario names; the start saves they name are prepared
    python -I tools/scenarios/prepare_save.py --save autosave    # or a save name directly
    python -I tools/scenarios/prepare_save.py --status [NAME ...]

It leases the pool instances like scen.py does, so run `pool up` first, or with --direct 0,1 it opens the pipes of copies started by
run.py up (the way run_fight.py --instances does; no pool needed). It never starts a game. It writes only into the Saves folder of
the leased instances (never the owner's real saves) and refuses the reserved names the bridge refuses.

Where the facts live: each instance keeps <Saves>/nvcoop_prepared.json next to the saves. An entry says which save name was written, that
the reload check passed, and the size and time of the source save. A prepared save is used only when its file is there, the reload
check passed, and the source save has not changed since (an "autosave" or "quicksave" source is exempt: the game rewrites those itself).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

MANIFEST = "nvcoop_prepared.json"
ROLLING = ("autosave", "quicksave")   # saves the game rewrites by itself: their size and time prove nothing
# test-only (SCEN_MUTANT): "noverify" | "earlysave" | "nostale" | "unverified" | "directpos"
MUTANT = os.environ.get("SCEN_MUTANT", "")
# After a load, the quiet window of the message box clearing. The plain save needs 20 s because the DLC boxes come in waves with
# gaps of 12-16 s (measured 2026-10-21). A prepared save raises none, so a few seconds only guard against a stray notice.
PLAIN_QUIET_S = 20.0
PREPARED_QUIET_S = 4.0


def ready_names(save):
    """The names a prepared save is tried under, best first. The second one is for an engine that only takes autosave* names."""
    return ["nvcoop_%s_ready" % save, "autosave_nvcoop_%s_ready" % save]


def default_saves_dir(inst):
    import instances   # tools/runner (runner.py puts it on sys.path)
    return instances.instance_dir(inst) / "Saves"


def _fingerprint(path):
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return {"size": st.st_size, "mtimeNs": st.st_mtime_ns}


class PreparedSaves:
    """Which instance holds a usable prepared save of which start save. saves_dir_of(inst) gives the Saves folder of an instance."""

    def __init__(self, saves_dir_of=None):
        self.saves_dir_of = saves_dir_of or default_saves_dir

    # ---- manifest of one instance
    def _manifest_path(self, inst):
        return Path(self.saves_dir_of(inst)) / MANIFEST

    def _read(self, inst):
        try:
            data = json.loads(self._manifest_path(inst).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"saves": {}}
        if not isinstance(data, dict) or not isinstance(data.get("saves"), dict):
            return {"saves": {}}
        return data

    def record(self, inst, save, name, verified, boxes):
        """Write the entry of one start save. The source fingerprint is taken now, so a replaced source save shows up as stale."""
        d = Path(self.saves_dir_of(inst))
        d.mkdir(parents=True, exist_ok=True)
        data = self._read(inst)
        data["saves"][save] = {"name": name, "verified": bool(verified), "boxes": boxes, "at": round(time.time()),
                               "source": _fingerprint(d / (save + ".fos"))}
        tmp = d / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, d / MANIFEST)

    def pick(self, inst, save):
        """-> (name, why). name is the prepared save to load on this instance, or None with the reason it is not used."""
        e = self._read(inst)["saves"].get(save)
        if not e:
            return None, "no prepared save of %r on instance %s" % (save, inst)
        name = e.get("name")
        d = Path(self.saves_dir_of(inst))
        if not name or not (d / (name + ".fos")).is_file():
            return None, "prepared save file %s.fos is gone" % name
        if not e.get("verified") and MUTANT != "unverified":
            return None, "prepared save %s did not pass the reload check" % name
        src = e.get("source")
        if src and save.lower() not in ROLLING and MUTANT != "nostale":
            now = _fingerprint(d / (save + ".fos"))
            if now and now != src:
                return None, "source save %r changed after %s was prepared" % (save, name)
        return name, "prepared save %s" % name

    def status(self, inst, save):
        name, why = self.pick(inst, save)
        return {"instance": inst, "save": save, "prepared": name, "why": why}


def prepare(bridges, save, store, instances=None, log=print, verify=True, quiet_s=PLAIN_QUIET_S, max_s=150.0, **runner_kw):
    """Make the prepared save of `save` on every bridge: load it, press the load boxes away until quiet, save the game as the ready
    name, then (verify) load the ready save and check that no box comes up. `store` is a PreparedSaves. instances are the instance
    ids of the bridges (default 0..n-1). Returns {"save", "ok", "perInstance": [...]}. Needs bridges with exec-less methods only:
    load_save, save_game, call (bot.menu, bot.menu.press). runner_kw goes to runner.Runner (tests pass a fake clock)."""
    import runner
    ids = list(instances) if instances is not None else list(range(len(bridges)))
    rn = runner.Runner(bridges, runner.StateStore(), log=log, prepared=False, **runner_kw)
    for nv in bridges:
        nv.load_save(save)
    rn.sleep(rn.frames / 60.0)
    boxes = {"pressed": 0, "quiet": True} if MUTANT == "earlysave" else rn._clear_load_messages(quiet_s=quiet_s, max_s=max_s)
    if boxes is None:
        raise RuntimeError("no bot.menu on these instances: cannot tell whether a message box is open, so nothing was saved "
                           "(start the games with [dev] bots=1)")
    if not boxes.get("quiet"):
        raise RuntimeError("message boxes were still coming after %.0f s (%s pressed): nothing was saved" % (max_s, boxes.get("pressed")))
    out = []
    for k, nv in enumerate(bridges):
        inst = ids[k]
        names = ready_names(save)
        saved, err = None, None
        for name in names:
            try:
                nv.save_game(name, overwrite=True)
                saved = name
                break
            except Exception as e:   # noqa: BLE001 - the next name may be the one the engine takes
                err = "%s: %s" % (type(e).__name__, str(e)[:160])
        if saved is None:
            out.append({"instance": inst, "ok": False, "error": "save_game failed for %s (%s)" % (", ".join(names), err)})
            continue
        out.append({"instance": inst, "ok": True, "name": saved})
    if verify and MUTANT != "noverify":
        for k, nv in enumerate(bridges):
            if out[k].get("ok"):
                nv.load_save(out[k]["name"])
        rn.sleep(rn.frames / 60.0)
        again = rn._clear_load_messages(quiet_s=PREPARED_QUIET_S, max_s=max_s) or {}
        clean = bool(again.get("quiet")) and not again.get("pressed")
        for k in range(len(bridges)):
            if out[k].get("ok"):
                out[k]["verified"] = clean
                if not clean:
                    out[k]["error"] = "a load of %s still raised %s message box(es)" % (out[k]["name"], again.get("pressed"))
    else:
        for r in out:
            r["verified"] = bool(r.get("ok"))
    for r in out:
        if r.get("ok"):
            store.record(r["instance"], save, r["name"], r["verified"], boxes.get("pressed"))
    ok = all(r.get("ok") and r.get("verified") for r in out)
    log("prepared %r: %s" % (save, "ok" if ok else "FAILED " + json.dumps(out)))
    return {"save": save, "ok": ok, "boxesPressed": boxes.get("pressed"), "perInstance": out}


def _direct_bridges(ids, stack, connect_fn=None):
    """One bridge per instance id on its own pipe (what run.py up / run_fight.py --instances use), no pool lease."""
    if connect_fn is None:
        sys.path.insert(0, str(REPO / "tools" / "runner"))
        from nvbridge import NvBridge
        def connect_fn(i):
            return NvBridge.connect(instance=i, wait_s=5)
    return [stack.enter_context(connect_fn(i)) for i in ids]


def main(argv=None, lease_fn=None, store=None, connect_fn=None, **kw):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="scenario names (their start saves are prepared)")
    ap.add_argument("--save", action="append", default=[], help="a save name to prepare (repeatable)")
    ap.add_argument("--status", action="store_true", help="show what is prepared, change nothing (no game needed for the file check)")
    ap.add_argument("--no-verify", action="store_true", help="skip the reload check")
    ap.add_argument("--instances", type=int, default=2, help="with --status: how many instances to look at (default 2)")
    ap.add_argument("--direct", default="", metavar="IDS",
                    help="instance ids on their own pipes, e.g. 0,1 or 2,3 (copies started by run.py up): no pool lease. Use this when "
                         "run_fight.py --instances is how you run tests")
    a = ap.parse_args(argv)
    import scenlib
    saves = list(a.save)
    for n in a.names:
        p = Path(n)
        sc = scenlib.load_scenario(p if p.suffix == ".json" else scenlib.SCEN_DIR / (n + ".json"))
        if sc["start"]["save"] not in saves:
            saves.append(sc["start"]["save"])
    if not a.names and not a.save:
        for p in scenlib.list_scenarios():
            s = scenlib.load_scenario(p)["start"]["save"]
            if s not in saves:
                saves.append(s)
    store = store or PreparedSaves()
    direct = [int(x) for x in a.direct.split(",") if x.strip()] if a.direct else []
    if a.status:
        for s in saves:
            for i in (direct or range(a.instances)):
                print(json.dumps(store.status(i, s)))
        return 0
    if direct:
        import contextlib
        try:
            with contextlib.ExitStack() as stack:
                bridges = _direct_bridges(direct, stack, connect_fn)
                bad = 0
                for s in saves:
                    r = prepare(bridges, s, store, instances=None if MUTANT == "directpos" else direct, verify=not a.no_verify, **kw)
                    print(json.dumps(r))
                    bad += not r["ok"]
                return 1 if bad else 0
        except Exception as e:   # noqa: BLE001 - a pipe that is not there (ConnectionLost) or prepare() refusing
            print("prepare_save: %s" % e)
            return 1
    sys.path.insert(0, str(REPO / "tools" / "pool"))
    sys.path.insert(0, str(REPO / "tools" / "runner"))
    try:
        from client import lease, PoolError
    except Exception as e:   # noqa: BLE001
        print("cannot import tools/pool/client.py: %s" % e)
        return 2
    try:
        with (lease_fn or lease)("prepare-save", n=2, ttl_s=1800) as L:
            bridges = [L.bridge(i) for i in L.instances]
            bad = 0
            for s in saves:
                r = prepare(bridges, s, store, instances=L.instances, verify=not a.no_verify, **kw)
                print(json.dumps(r))
                bad += not r["ok"]
            return 1 if bad else 0
    except PoolError as e:
        print("pool: %s" % e)
        return 2
    except RuntimeError as e:
        print("prepare_save: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
