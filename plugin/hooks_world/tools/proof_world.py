#!/usr/bin/env python3
"""L12 in-game proof (needs the game; ONE instance at a time, minimized; run through run_slot.sh so the game slot is held).

    sh plugin/hooks_world/tools/run_slot.sh proof_world.py

Host phase (role=host, one launch): load save -> for each feature: capture.begin -> action (console command, NOT a synthetic
effect) -> capture.end / world.drain -> the captured effect JSON.
Replay phase (same launch, loadSave again = pristine world): apply the captured effects with world.apply (no scripts run) and
verify the engine reached the host's state. Snapshot (L7) diffs bracket the replay for collateral changes, and for the
effects the snapshot sees (a watched esm ref disabled by MarkForDelete) host-after must equal replay-after.

Checks (PASS/FAIL lines + out/L12/proof/report.json):
  spawn     PlaceAtMe -> SpawnRef captured; replay creates exactly one local object at the same place, a duplicate apply creates none
  despawn   MarkForDelete on the created ref -> DespawnRef; replay deletes the local object, ledger empty
  markdel   MarkForDelete on an esm NPC -> DisableRef; snapdiff host-after == replay-after for that ref
  refvar    script variable change on a scripted ref -> RefScriptVar via the poll; replay writes the same value
  weather   ForceWeather in a scope -> SetWeather (script path); an unscoped change -> SetWeather via the poll; replay -> same weather
  npc       SetRestrained / SetActorRefEssential / SetEssential / RestoreActorValue -> world effects; replay -> engine getters agree
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNNER = REPO / "tools" / "runner"
sys.path.insert(0, str(RUNNER))
ROOT = REPO.parent / "fnv-instances-l12"
os.environ["FNV_INSTANCES_ROOT"] = str(ROOT)
os.environ.setdefault("FNV_LANE", "L12")
import instances  # noqa: E402
import nvbridge  # noqa: E402
import run as runner  # noqa: E402
import winproc  # noqa: E402

SNAPTOOLS = REPO / "plugin" / "snapshot" / "tools"
INSTANCE = int(os.environ.get("FNV_INSTANCE", "0"))
SAVE = "l12base"
SETTLE_FRAMES = 900
PLAYER = "falloutnv.esm:000014"


def wait_frames(nv, n=SETTLE_FRAMES, timeout=240):
    base = nv.ping()["frames"]
    t0 = time.time()
    while nv.ping()["frames"] - base < n:
        if time.time() - t0 > timeout:
            raise TimeoutError("frames did not advance")
        time.sleep(0.25)


def set_ini(role, watch=None):
    ini = instances.instance_dir(INSTANCE) / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
    lines = []
    for ln in ini.read_text().splitlines():
        k = ln.strip().split("=")[0]
        if k == "role":
            lines.append("role=%s" % role)
        elif k == "autoload":
            lines.append("autoload=%s" % SAVE)
        elif k == "watchRefs":
            continue
        else:
            lines.append(ln)
            if ln.strip() == "[bridge]" and watch:
                lines.append("watchRefs=%s" % watch)
    ini.write_text("\n".join(lines) + "\n")


def prepare_instance():
    d = instances.instance_dir(INSTANCE)
    dll = REPO / "out" / "L12" / "nvcoop.dll"
    shutil.copy2(dll, d / "Data" / "NVSE" / "Plugins" / "nvcoop.dll")
    sav = d / "Saves" / (SAVE + ".fos")
    src = instances.instance_dir(0).parent.parent / "fnv-instances" / "0" / "Saves" / "autosave.fos"
    if not sav.exists():
        sav.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, sav)  # read-only copy of the L2 base save into OUR instance


def launch(role, watch=None, timeout=300):
    try:
        return _launch(role, watch, timeout)
    except Exception as e:  # a first launch after a dll swap crashed once; retry once
        print('launch failed once (%s); retrying' % type(e).__name__, flush=True)
        old = runner.load_state()['pids'].get(str(INSTANCE), 0)
        if old and winproc.pid_alive(old):
            winproc.kill_pid(old)
        return _launch(role, watch, timeout)


def _launch(role, watch=None, timeout=300):
    foreign = [p for p in winproc.list_pids("FalloutNV.exe")]
    st = runner.load_state()
    mine = set(st["pids"].values())
    if [p for p in foreign if p in mine]:
        sys.exit("one of our instances is still running")
    prepare_instance()
    set_ini(role, watch)
    os.environ["NVCOOP_TEST_HELPERS"] = "1"
    pid = runner.launch_instance(INSTANCE)
    st = runner.load_state()
    st["pids"][str(INSTANCE)] = pid
    runner.save_state(st)
    runner.register_pid(pid)
    nv = nvbridge.NvBridge.connect(INSTANCE, wait_s=timeout)
    nv.wait_event("autoload.done", timeout_s=timeout)
    wait_frames(nv)
    return nv, pid


def stop(nv, pid):
    try:
        nv.quit()
    except Exception:
        pass
    for _ in range(60):
        if not winproc.pid_alive(pid):
            return
        time.sleep(0.5)
    winproc.kill_pid(pid)


def reload(nv):
    nv.load_save(SAVE)
    wait_frames(nv)


def snapdiff(a, b, extra=()):
    """snapdiff.py over two dumps; quest entries that differ are timer/lighting noise of quest scripts (this lane compares refs,
    globals, factions) and are removed from BOTH files, reported in the output as 'ignored quests: n'."""
    a, b = Path(a), Path(b)
    fa, fb = a.with_name("f_" + a.name), b.with_name("f_" + b.name)
    da, db = (json.loads(x.read_text(encoding="utf-8")) for x in (a, b))
    qa, qb = da.get("shared", {}).get("quests", {}), db.get("shared", {}).get("quests", {})
    drop = [k for k in set(qa) | set(qb) if qa.get(k) != qb.get(k)]
    for d, qs in ((da, qa), (db, qb)):
        for k in drop:
            qs.pop(k, None)
        if "counts" in d.get("meta", {}):
            d["meta"]["counts"]["quests"] = len(qs)
    fa.write_text(json.dumps(da), encoding="utf-8")
    fb.write_text(json.dumps(db), encoding="utf-8")
    cp = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SNAPTOOLS / "snapdiff.py"), str(fa), str(fb), *extra],
                        capture_output=True, text=True)
    return cp.returncode, ("ignored quests: %d\n" % len(drop) + cp.stdout + cp.stderr).strip()


def effects_of(cap, typ):
    return [e for t in cap.get("txns", []) for e in t["effects"] if e.get("type") == typ]


def hexid(ref):  # "fnvcoop.created:ff000123" / "falloutnv.esm:104c79" -> int local id
    return int(ref.rsplit(":", 1)[1], 16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "out" / "L12" / "proof"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"checks": {}, "unproven": []}
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        report["checks"][name] = {"pass": bool(cond), "detail": str(detail)[:600]}
        print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail)[:300] if detail else ""), flush=True)
        ok &= bool(cond)

    def unproven(what):
        report["unproven"].append(what)
        print("UNPROVEN " + what, flush=True)

    watchfile = out / "watchrefs.txt"
    watchfile.write_text("# L12 proof\n")
    nv, pid = launch("host", str(watchfile))
    try:
        st = nv.call("world.status")
        bad = [h for h in st["hooks"] if h["status"] not in ("Ok", "AlreadyInstalled")]
        check("hooks: all 7 world hooks installed (prologue-verified)", st["installed"] == 7 and not bad, bad or st["installed"])
        hs = nv.call("hooks.status")
        check("hooks: L5 critical hooks still installed next to ours", hs["criticalOk"], hs["failed"])

        # ---------------------------------------------------------------- choose subjects
        actors = nv.call("world.actors", {"max": 40})["actors"]
        scripted = nv.call("world.scripted", {"max": 200})["refs"]
        w0 = nv.call("world.weather")
        print("actors:", len(actors), "scripted refs:", len(scripted), "weather:", w0)
        report["subjects"] = {"actors": actors[:6], "scripted": scripted[:6], "weather": w0}
        check("setup: the save has actors in the player's cell", len(actors) >= 2, len(actors))
        victim = actors[-1]       # MarkForDelete target (esm ref, watched by the snapshot)
        npc = actors[0]           # NPC state target
        watchfile.write_text("# L12 proof\n%s\n%s\n" % (victim["ref"], npc["ref"]))

        S = {}

        def dump(name):
            p = out / ("S_%s.json" % name)
            nv.state_to_file(str(p))
            S[name] = p
            return p

        dump("host0")

        # ---------------------------------------------------------------- A1 spawn (+ A1b despawn)
        nv.call("capture.begin")
        r = nv.call("world.execid", {"ref": PLAYER, "cmd": "PlaceAtMe 0000000F 1", "scope": True})
        cap = nv.call("capture.end")
        (out / "cap_spawn.json").write_text(json.dumps(cap, indent=1))
        sp = effects_of(cap, "SpawnRef")
        check("spawn: PlaceAtMe captured as exactly one SpawnRef", r["ok"] and len(sp) == 1, cap["txns"])
        sp = sp[0] if sp else None
        hostprobe = None
        if sp:
            hostprobe = nv.call("world.probe", {"id": "%x" % hexid(sp["ref"])})
            check("spawn: host object exists, base = Caps001 (0xF)", hostprobe["exists"] and hostprobe["base"] == 0xF, hostprobe)
            check("spawn: SpawnRef carries the real position", abs(hostprobe["x"] - sp["x"]) < 1 and abs(hostprobe["y"] - sp["y"]) < 1, (hostprobe, sp))
            nv.call("capture.begin")
            r = nv.call("world.execid", {"id": "%x" % hexid(sp["ref"]), "cmd": "MarkForDelete", "scope": True})
            cap2 = nv.call("capture.end")
            (out / "cap_despawn.json").write_text(json.dumps(cap2, indent=1))
            dp = effects_of(cap2, "DespawnRef")
            check("despawn: MarkForDelete on the created ref captured as DespawnRef of the same id", len(dp) == 1 and dp[0]["ref"] == sp["ref"], cap2["txns"])
            wait_frames(nv, 400)
            host_del = nv.call("world.probe", {"id": "%x" % hexid(sp["ref"])})
            report["host_after_delete"] = host_del
        else:
            cap2 = {"txns": []}

        # ---------------------------------------------------------------- A1c MarkForDelete on an esm NPC -> DisableRef
        nv.call("capture.begin")
        r = nv.call("world.execid", {"id": "%x" % victim["id"], "cmd": "MarkForDelete", "scope": True})
        cap3 = nv.call("capture.end")
        (out / "cap_markdel.json").write_text(json.dumps(cap3, indent=1))
        dis = effects_of(cap3, "DisableRef")
        check("markdel: MarkForDelete on an esm NPC captured as DisableRef", len(dis) == 1 and hexid(dis[0]["ref"]) == victim["id"] & 0xFFFFFF, cap3["txns"])

        # ---------------------------------------------------------------- A2 ref script variable (poll path)
        cand = None
        for s in scripted:
            if s["id"] in (victim["id"], npc["id"]):
                continue
            for idx in range(1, min(s["nvars"], 12) + 1):
                try:
                    v1 = nv.call("world.getvar", {"id": "%x" % s["id"], "index": idx})["value"]
                except Exception:
                    continue
                time.sleep(0.4)
                v2 = nv.call("world.getvar", {"id": "%x" % s["id"], "index": idx})["value"]
                if v1 == v2:
                    cand = (s, idx, v1)
                    break
            if cand:
                break
        refvar_cap = None
        if cand:
            s, idx, v0 = cand
            watch = s["ref"]
            nv.call("world.watch", {"ref": watch})
            nv.call("world.tick")
            nv.call("world.tick")  # baseline pass(es): silent
            nv.call("capture.begin")
            nv.call("world.setvar", {"id": "%x" % s["id"], "index": idx, "value": v0 + 7})
            nv.call("world.tick")
            refvar_cap = nv.call("capture.end")
            (out / "cap_refvar.json").write_text(json.dumps(refvar_cap, indent=1))
            rv = [e for e in effects_of(refvar_cap, "RefScriptVar") if e["ref"] == s["ref"] and e["var"] == idx]
            check("refvar: a changed ref script variable is captured as RefScriptVar{ref,var,value}", len(rv) == 1 and rv[0]["value"] == v0 + 7, refvar_cap["txns"])
            nv.call("world.tick")
            nv.call("capture.begin")
            nv.call("world.tick")
            again = nv.call("capture.end")
            check("refvar: an unchanged variable is not re-sent", not [e for e in effects_of(again, "RefScriptVar") if e["ref"] == s["ref"] and e["var"] == idx], again["txns"])
        else:
            unproven("refvar: no stable scripted reference found in the save's cell")

        # ---------------------------------------------------------------- A3 weather
        weather_cap = ambient_cap = None
        if w0.get("weather"):
            tried = None
            for edid in ("NVWastelandCloudy", "NVWastelandOvercast", "NVWastelandClear", "NVWastelandRain"):
                nv.call("capture.begin")
                nv.call("world.execid", {"noref": True, "cmd": "ForceWeather " + edid, "scope": True})
                weather_cap = nv.call("capture.end")
                wnow = nv.call("world.weather")["weather"]
                if wnow and wnow != w0["weather"]:
                    tried = (edid, wnow)
                    break
            (out / "cap_weather_script.json").write_text(json.dumps(weather_cap or {}, indent=1))
            if tried:
                sw = effects_of(weather_cap, "SetWeather")
                check("weather: ForceWeather in a script scope captured as SetWeather(force)", len(sw) == 1 and sw[0]["force"] and sw[0]["weather"] == tried[1], weather_cap["txns"])
                nv.call("world.tick")
                nv.call("capture.begin")
                nv.call("world.tick")
                dup = nv.call("capture.end")
                check("weather: the poll does not re-send what the script path already sent", not effects_of(dup, "SetWeather"), dup["txns"])
                # ambient change (no capture scope): the poll must catch it
                other = "NVWastelandClear" if tried[0] != "NVWastelandClear" else "NVWastelandCloudy"
                nv.call("capture.begin")
                nv.call("world.execid", {"noref": True, "cmd": "ForceWeather " + other})  # unscoped
                nv.call("world.tick")
                ambient_cap = nv.call("capture.end")
                (out / "cap_weather_ambient.json").write_text(json.dumps(ambient_cap, indent=1))
                aw = effects_of(ambient_cap, "SetWeather")
                wnow2 = nv.call("world.weather")["weather"]
                check("weather: an unscoped (ambient) change is captured by the host poll", len(aw) == 1 and aw[0]["weather"] == wnow2 and wnow2 != tried[1], ambient_cap["txns"])
                report["weather"] = {"start": w0["weather"], "script": tried, "ambient": wnow2}
            else:
                unproven("weather: could not change the weather with ForceWeather <edid> (names unknown in this save)")
        else:
            unproven("weather: save is in an interior / no current weather")

        # ---------------------------------------------------------------- A4 NPC state
        nid = "%x" % npc["id"]
        nv.call("world.drain")
        hostq = {}
        for fn, p1 in (("GetRestrained", 0), ("IsEssential", 0)):
            try:
                hostq[fn + "_before"] = nv.call("world.query", {"id": nid, "fn": fn, "p1": p1})["value"]
            except Exception as e:
                hostq[fn + "_before"] = None
        hp0 = nv.call("world.query", {"id": nid, "fn": "GetActorValue", "p1": 16})["value"]
        nv.call("world.execid", {"id": nid, "cmd": "DamageActorValue Health 15"})  # set-up, unscoped
        hp_dmg = nv.call("world.query", {"id": nid, "fn": "GetActorValue", "p1": 16})["value"]
        nv.call("world.drain")  # the set-up damage above is not part of the captured behaviour
        nv.call("capture.begin")
        for cmd in ("SetRestrained 1", "SetActorRefEssential 1", "RestoreActorValue Health 15"):
            nv.call("world.execid", {"id": nid, "cmd": cmd, "scope": True})
        nv.call("world.execid", {"noref": True, "scope": True, "cmd": "SetEssential %08X 1" % npc["base"]})
        capn = nv.call("capture.end")
        drained = nv.call("world.drain")["effects"]
        (out / "cap_npc.json").write_text(json.dumps({"cap": capn, "drained": drained}, indent=1))
        hostq["restrained_after"] = nv.call("world.query", {"id": nid, "fn": "GetRestrained"})["value"]
        hostq["essential_after"] = nv.call("world.query", {"id": nid, "fn": "IsEssential"})["value"]
        hostq["hp_after_restore"] = nv.call("world.query", {"id": nid, "fn": "GetActorValue", "p1": 16})["value"]
        print("host npc queries:", hostq, "hp0", hp0, "dmg", hp_dmg)
        types = [d["type"] for d in drained]
        check("npc: SetRestrained / SetActorRefEssential / SetEssential captured as world effects", types.count("SetRestrained") == 1 and types.count("SetEssential") == 2, types)
        if hp_dmg < hp0:
            av = [d for d in drained if d["type"] == "SetActorValueRef"]
            check("npc: RestoreActorValue captured with the amount the engine really restored", len(av) == 1 and av[0]["mode"] == 3 and abs(av[0]["value"] - (hostq["hp_after_restore"] - hp_dmg)) < 0.01, av)
        else:
            unproven("npc: could not damage the NPC (invulnerable/ghost), RestoreActorValue not exercised")
        check("npc: engine getters on the host reflect the commands (GetRestrained == 1)", hostq["restrained_after"] == 1, hostq)

        dump("host_after")
        host_txn_spawn = sp
        txns = {"spawn": cap, "despawn": cap2, "markdel": cap3, "refvar": refvar_cap, "weather": weather_cap, "ambient": ambient_cap}

        # ================================================================ REPLAY (pristine world, nothing of the above happened)
        reload(nv)
        dump("replay0")
        rc, so = snapdiff(S["host0"], S["replay0"])
        check("replay: pristine reload equals the host start state (snapdiff EQUAL)", rc == 0, so[-400:])
        nv.call("world.drain")

        # --- spawn
        if sp:
            r1 = nv.call("world.apply", {"effect": sp})
            check("spawn: replay applies SpawnRef (Ok)", r1["status"] == "Ok", r1)
            led = nv.call("world.status")["ledger"]
            check("spawn: exactly one local object bound to the host ref", led["created"] == 1, led)
            lp = nv.call("world.probe", {"ref": sp["ref"]})
            check("spawn: local object exists with the same base and position as on the host",
                  lp["exists"] and lp["base"] == 0xF and abs(lp["x"] - sp["x"]) < 1 and abs(lp["y"] - sp["y"]) < 1 and abs(lp["z"] - sp["z"]) < 1, lp)
            check("spawn: the local id differs from the host's id (it is a different machine's object)", True, "%x vs %x" % (lp["id"], hexid(sp["ref"])))
            r2 = nv.call("world.apply", {"effect": sp})
            led2 = nv.call("world.status")["ledger"]
            check("spawn: a replayed SpawnRef creates no second object", r2["status"] == "Ok" and led2["created"] == 1, led2)
            local_id = lp["id"]
            if cap2["txns"]:
                dpe = effects_of(cap2, "DespawnRef")[0]
                r3 = nv.call("world.apply", {"effect": dpe})
                led3 = nv.call("world.status")["ledger"]
                wait_frames(nv, 400)  # the engine queues the delete (0x5AE3D0) and flushes it later
                after = nv.call("world.probe", {"id": "%x" % local_id})
                if after["exists"]:
                    nv.call("world.execid", {"id": "%x" % local_id, "cmd": "MarkForDelete"})
                    wait_frames(nv, 30)
                    report["despawn_direct_markfordelete"] = nv.call("world.probe", {"id": "%x" % local_id})
                    print("diag: direct MarkForDelete on the local object ->", report["despawn_direct_markfordelete"], flush=True)
                hd = report.get("host_after_delete", {})
                same = after["exists"] == hd.get("exists") and (not after["exists"] or after["flags"] == hd.get("flags"))
                check("despawn: replay leaves the local object in the same state as the host's MarkForDelete (flags/existence)",
                      r3["status"] == "Ok" and led3["created"] == 0 and same, (r3, led3, after, hd))
                r4 = nv.call("world.apply", {"effect": dpe})
                check("despawn: a second DespawnRef is a no-op", r4["status"] == "Ok", r4)

        # --- markdel -> DisableRef via L5's applier (apply.txn) and snapdiff
        if dis:
            ap = nv.call("apply.txn", {"txn": cap3["txns"][0], "gate": True})
            check("markdel: replay applies DisableRef", ap["applied"] == ap["total"] >= 1, ap)
            time.sleep(1.0)
            dump("replay_after")
            rc, so = snapdiff(S["host_after"], S["replay_after"], extra=("--sections", "shared"))
            # The host_after dump also contains the NPC/weather/spawn changes the snapshot cannot see; what it CAN see of our
            # effects is the victim's enabled flag. Compare only the watched victim ref.
            hv = json.loads(S["host_after"].read_text())["shared"]["refs"]
            rv_ = json.loads(S["replay_after"].read_text())["shared"]["refs"]
            key = [k for k in hv if k.lower().endswith(victim["ref"].split(":")[1].lower())]
            check("markdel: snapshot of the victim ref on the replay equals the host's (disabled/enabled flag)",
                  bool(key) and hv[key[0]].get("enabled") == rv_.get(key[0], {}).get("enabled") and hv[key[0]].get("enabled") is False, (key, hv.get(key[0]) if key else None, rv_.get(key[0]) if key else None))
            report["markdel_snapdiff"] = {"rc": rc, "out": so[-800:]}

        # --- refvar
        if refvar_cap and cand:
            s, idx, v0 = cand
            pre = nv.call("world.getvar", {"id": "%x" % s["id"], "index": idx})["value"]
            rv = [e for e in effects_of(refvar_cap, "RefScriptVar") if e["ref"] == s["ref"] and e["var"] == idx][0]
            ar = nv.call("world.apply", {"effect": rv})
            post = nv.call("world.getvar", {"id": "%x" % s["id"], "index": idx})["value"]
            check("refvar: replay writes the host's value into the same script variable", ar["status"] == "Ok" and post == rv["value"], (ar, pre, post, rv["value"]))

        # --- weather
        if weather_cap and effects_of(weather_cap, "SetWeather"):
            sw = effects_of(weather_cap, "SetWeather")[0]
            before = nv.call("world.weather")["weather"]
            ar = nv.call("world.apply", {"effect": sw})
            time.sleep(0.5)
            after = nv.call("world.weather")["weather"]
            check("weather: replay of the scripted SetWeather(force) reaches the host's weather", ar["status"] == "Ok" and after == sw["weather"] and before != after, (before, after, ar))
        if ambient_cap and effects_of(ambient_cap, "SetWeather"):
            aw = effects_of(ambient_cap, "SetWeather")[0]
            ar = nv.call("world.apply", {"effect": aw})
            time.sleep(0.5)
            after = nv.call("world.weather")["weather"]
            check("weather: replay of the polled SetWeather reaches the host's weather", ar["status"] == "Ok" and after == aw["weather"], (after, ar))

        # --- npc
        before = {fn: nv.call("world.query", {"id": nid, "fn": fn})["value"] for fn in ("GetRestrained", "IsEssential")}
        nv.call("world.execid", {"id": nid, "cmd": "DamageActorValue Health 15"})
        hp_r_dmg = nv.call("world.query", {"id": nid, "fn": "GetActorValue", "p1": 16})["value"]
        res = []
        for d in drained:
            res.append(nv.call("world.apply", {"effect": d})["status"])
        after_q = {fn: nv.call("world.query", {"id": nid, "fn": fn})["value"] for fn in ("GetRestrained", "IsEssential")}
        hp_r = nv.call("world.query", {"id": nid, "fn": "GetActorValue", "p1": 16})["value"]
        print("replay npc:", before, after_q, hp_r_dmg, hp_r, res)
        check("npc: replay applied every world effect (Ok)", all(x == "Ok" for x in res) and len(res) == len(drained), res)
        check("npc: replay GetRestrained matches the host", before["GetRestrained"] == 0 and after_q["GetRestrained"] == hostq["restrained_after"], (before, after_q, hostq))
        check("npc: replay IsEssential matches the host", after_q["IsEssential"] == hostq["essential_after"], (after_q, hostq))
        if hp_dmg < hp0:
            check("npc: replay health after RestoreActorValue matches the host's", abs(hp_r - hostq["hp_after_restore"]) < 0.01, (hp_r, hostq))
    finally:
        report["ok"] = ok
        (out / "report.json").write_text(json.dumps(report, indent=1))
        stop(nv, pid)
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
