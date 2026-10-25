"""Extra benchmark phases: area change, input-walk probe, playbooks (fight, loot) and the bad-network matrix through the chaos relay.

Used by coop_bench.py. Everything here talks to the two running copies through the bridge or runs the repo's own tools as subprocesses.
"""
import json, math, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tools" / "runner"), str(ROOT / "tools" / "bots")]

INTERIOR = "GSDocMitchellHouse"        # a small interior next to the start of the save
EXTERIOR = ("WastelandNV", -18, 0)      # the exterior cell the save starts in (cow = center on world)
NET_PROFILES = ["lan", "good_wifi", "bad_wifi", "mobile_tether", "transatlantic", "hell"]
HOST_PORT, RELAY_PORT = 27960, 27961


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def sleep_until(end):
    while time.time() < end:
        time.sleep(0.25)


# ------------------------------------------------------------------ input-walk probe
def input_probe(bots, log):
    """Does real movement input move a player at all? (the step walk bypasses input; this tells whether input walking works on this setup)"""
    out = {}
    for i, b in enumerate(bots):
        try:
            p0 = [float(v) for v in b.pos()]
            r = b.goto_pos(p0[0] + 150, p0[1], p0[2], timeout_s=10, mode="input")
            p1 = [float(v) for v in b.pos()]
            out[f"copy{i}"] = {"ok": bool(r.ok), "moved_units": round(dist(p0, p1)), "reason": str(getattr(r, "reason", "") or "")[:70]}
        except Exception as e:   # noqa: BLE001
            out[f"copy{i}"] = {"ok": False, "error": str(e)[:80]}
    log(f"input probe: {out}")
    return out


# ------------------------------------------------------------------ arena: find a flat open cell where input walking works, and move both copies there
CANDIDATE_CELLS = [(-18, 0), (-17, -2), (-19, 2), (-16, 1), (-20, -1), (-15, -3), (-14, 0), (-12, 2), (-21, 3), (-17, 4), (-13, -5), (-22, -2),
                   (-10, 0), (-9, 4), (-11, -3), (-8, 6)]


def probe_cell(bot, cell, log, leg=250):
    """Teleport the bot to the exterior cell (cow) and take four short walks with REAL movement input (+x, -x, +y, -y). Returns what happened."""
    out = {"cell": list(cell), "legs_ok": 0, "legs": 4, "moved": [], "pos": None}
    try:
        bot.nv.exec("cow WastelandNV %d %d" % cell)
        bot.dismiss_messages(quiet_s=2, max_s=15, max_n=6)
        time.sleep(4)
        out["pos"] = [round(float(v)) for v in bot.pos()]
        for dx, dy in ((leg, 0), (-leg, 0), (0, leg), (0, -leg)):
            p = [float(v) for v in bot.pos()]
            r = bot.goto_pos(p[0] + dx, p[1] + dy, p[2], timeout_s=8, mode="input")
            q = [float(v) for v in bot.pos()]
            out["moved"].append(round(dist(p, q)))
            out["legs_ok"] += 1 if r.ok else 0
    except Exception as e:   # noqa: BLE001
        out["error"] = str(e)[:100]
    log(f"arena probe {tuple(cell)}: {out['legs_ok']}/4 legs, moved {out['moved']}, at {out['pos']}")
    return out


def find_origin(bots, log, cells=None, stop_at=4):
    """Try candidate cells with the host bot until one lets all four input legs arrive. Returns (best probe, every probe)."""
    probes = []
    for cell in (cells or CANDIDATE_CELLS):
        pr = probe_cell(bots[0], cell, log)
        probes.append(pr)
        if pr["legs_ok"] >= stop_at:
            break
    best = max(probes, key=lambda p: (p["legs_ok"], sum(p["moved"])))
    return best, probes


def goto_arena(ctx_arena, bots, log):
    """Move both copies into the arena cell, the client a little beside the host. No-op when the arena has no cell (they stay where they are)."""
    if not ctx_arena.cell or not ctx_arena.origin:
        return {"skipped": "the arena has no cell and origin (run tools/bench/find_arena.py)"}
    ws, cx, cy = ctx_arena.cell
    x, y, z = ctx_arena.origin
    for i, b in enumerate(bots):
        b.nv.exec("cow %s %d %d" % (ws, cx, cy))
        b.dismiss_messages(quiet_s=2, max_s=15, max_n=6)
    time.sleep(4)
    for i, b in enumerate(bots):
        r = b.goto_pos(x, y + 90 * i, z, timeout_s=10, mode="teleport")
        if not r.ok:
            log(f"arena: copy {i} did not reach the origin: {getattr(r, 'reason', '')}")
    time.sleep(3)
    return {"cell": [ws, cx, cy], "origin": [x, y, z]}


# ------------------------------------------------------------------ area change
def area_change(samp, bots, k, log):
    """The host walks into a building (coc) and back out to the exterior (cow). Phases area_interior and area_back; the analysis measures how long
    the client's puppet of the host takes to be near the host again after the way back."""
    host = bots[0]
    info = {"ok": False}
    try:
        samp.phase[0] = "area_interior"
        info["coc"] = str(host.nv.exec(f"coc {INTERIOR}").get("output"))[:60]
        host.dismiss_messages(quiet_s=3, max_s=20, max_n=6)
        sleep_until(time.time() + 12 * k)
        samp.phase[0] = "area_back"
        info["t_back"] = round(time.time() - samp.t0, 2)
        info["cow"] = str(host.nv.exec("cow %s %d %d" % EXTERIOR).get("output"))[:60]
        host.dismiss_messages(quiet_s=3, max_s=20, max_n=6)
        sleep_until(time.time() + 18 * k)
        info["ok"] = True
    except Exception as e:   # noqa: BLE001
        info["error"] = str(e)[:100]
    log(f"area change: {info}")
    return info


# ------------------------------------------------------------------ playbooks
def run_playbook(name, out_dir, log, timeout_s=420):
    """Run one of the repo's co-op playbooks (tools/bots/run_coop.py) against the two running copies and condense its summary."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-I", str(ROOT / "tools" / "bots" / "run_coop.py"), "--playbook", name, "--instances", "0,1", "--keep-going",
           "--outdir", str(out_dir)]
    t0 = time.time()
    res = {"name": name, "ok": False, "steps": [], "seconds": 0.0}
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout_s, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        res["exit"] = r.returncode
        res["tail"] = (r.stdout or "")[-300:] + (r.stderr or "")[-200:]
    except subprocess.TimeoutExpired:
        res["error"] = f"timeout after {timeout_s} s"
    res["seconds"] = round(time.time() - t0, 1)
    found = sorted(out_dir.rglob("summary.json"))
    if found:
        try:
            d = json.loads(found[-1].read_text(encoding="utf-8"))
            for pr in d.get("results", []):
                if pr.get("playbook") == name:
                    res["ok"] = bool(pr.get("ok"))
                    res["reason"] = str(pr.get("reason") or "")[:120]
                    for s in pr.get("steps", []):
                        cd = s.get("counters_delta") or {}
                        mism = sum(int((v or {}).get("digestMismatch") or 0) for v in cd.values()) if isinstance(cd, dict) else 0
                        res["steps"].append({"name": str(s.get("name"))[:70], "ok": bool(s.get("ok")), "seconds": s.get("seconds"),
                                             "snapdiff": s.get("snapdiff"), "digest_mismatch": mism, "reason": str(s.get("reason") or "")[:90]})
        except Exception as e:   # noqa: BLE001
            res["error"] = "summary unreadable: " + str(e)[:80]
    elif "error" not in res:
        res["error"] = "no summary.json: " + res.get("tail", "")[-160:].replace("\n", " ")
    st = res["steps"]
    res["steps_total"] = len(st)
    res["steps_ok"] = sum(1 for s in st if s["ok"])
    res["pass_pct"] = round(100.0 * res["steps_ok"] / len(st), 1) if st else 0.0
    res["snapdiff_equal"] = sum(1 for s in st if s.get("snapdiff") == "equal")
    log(f"playbook {name}: {res['steps_ok']}/{res['steps_total']} steps ok in {res['seconds']} s ({res.get('reason') or res.get('error') or ''})")
    return res


# ------------------------------------------------------------------ bad-network matrix
class Relay:
    """The chaos relay (tools/chaos) between the client and the host. The client's nvcoop.ini is pointed at the relay port for the run."""
    def __init__(self, log):
        self.log, self.proc, self.info = log, None, ROOT / "out" / "bench" / "relay_info.json"

    def start(self, profile="lan"):
        self.info.parent.mkdir(parents=True, exist_ok=True)
        if self.info.exists():
            self.info.unlink()
        cmd = [sys.executable, "-I", str(ROOT / "tools" / "chaos" / "chaos.py"), "relay", "--target", f"127.0.0.1:{HOST_PORT}",
               "--listen", str(RELAY_PORT), "--profile", profile, "--seed", "7", "--info-file", str(self.info)]
        self.proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            if self.info.exists() and self.info.stat().st_size > 2:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("the chaos relay did not come up")
        self.log(f"relay up: {self.info.read_text(encoding='utf-8')[:120]}")

    def profile(self, name):
        from playbooks import chaos_adapter
        r = chaos_adapter.apply_profile(str(ROOT), str(self.info), name, 7)
        if not r.get("applied"):
            raise RuntimeError("relay profile not applied: " + str(r)[:120])
        return r

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def client_ini_path(root_inst):
    return Path(root_inst) / "1" / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"


def point_client_at_relay(ini_path, on):
    """Add (on) or remove (off) host / port keys under [coop] of the client's nvcoop.ini so it joins the relay port. Returns the original text."""
    text = ini_path.read_text(encoding="latin-1")
    lines = text.splitlines()
    out, in_coop = [], False
    for ln in lines:
        s = ln.strip()
        if s.startswith("["):
            in_coop = s.lower() == "[coop]"
        if in_coop and s.lower().startswith(("port=", "host=")):
            continue
        out.append(ln)
        if in_coop and s.lower() == "[coop]" and on:
            out.append(f"host=127.0.0.1")
            out.append(f"port={RELAY_PORT}")
    new = "\r\n".join(out) + "\r\n"
    ini_path.write_text(new, encoding="latin-1", newline="")
    return text
