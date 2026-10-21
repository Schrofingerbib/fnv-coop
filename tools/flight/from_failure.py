"""Turn an in-game sync failure into an offline repro that runs in seconds.

  python -I tools/flight/from_failure.py [PATH ...] [options]

PATH is any of: a failure.json, a FAIL-* directory, a run_fight out dir (every failure.json below it), two or more .nvfr files, or
nothing (the newest failure.json under out/). For every failure it
  1. finds the host and client recordings (.nvfr) named by failure.json (they live in each game instance's nvcoop_flight folder),
  2. replays them offline through the flight analyzer (trace_replay.py) and says whether the failure reproduces:
     digest mismatch, divergence, stuck sync, or nothing in the recordings,
  3. cross-checks that with what the bots saw in the live status (digest counters, repairs, host/client sequence lag),
  4. shrinks the recordings to the few records that still show the same failure (delta debugging, a few seconds),
  5. writes a regression case, tools/flight/regress/<name>.json, that `python -I tools/flight/regress.py tools/flight/regress`
     replays in a second without a game or a replay binary.
Exit code: 0 every failure reproduced, 1 at least one did not reproduce in its recordings, 2 recordings missing or unreadable.

The case pins the recorded evidence: it passes ("expect": converges false) while the recordings still show the failure. A recording
cannot change when the plugin is fixed, so the proof that a fix works is a fresh run: run this tool on the new failure dir, or on the
two new .nvfr files, and expect "not reproduced". Use --expect-converges to write the case the other way round (fails until the
analyzer stops seeing the failure in the recordings you give it).
Options: --instances DIR (default ../fnv-instances or $FNV_INSTANCES_ROOT), --out DIR, --name NAME, --no-shrink, --no-write,
--max-seconds S (shrink budget, default 30), --max-runs N, --json, --report FILE.html.
"""
import argparse
import glob
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import nvfr as N  # noqa: E402
import analyze as A  # noqa: E402
import shrink as S  # noqa: E402
import gentest as G  # noqa: E402
import report as R  # noqa: E402
import trace_replay as TR  # noqa: E402

DEFAULT_OUT = os.path.join(HERE, "regress")
GUESS_WINDOW_S = 600           # a dump with no recorded path is guessed from the newest recording written this long before the failure
DROP_ORDER = ("NetIn", "NetOut", "Marker", "Ownership", "TxnCaptured", "Digest", "TxnApplied", "Meta")
MARGINS_MS = (1000, 5000, 20000, 60000)


def instances_root(arg=None):
    return arg or os.environ.get("FNV_INSTANCES_ROOT") or os.path.join(os.path.dirname(ROOT), "fnv-instances")


# ---------------------------------------------------------------- finding failures and recordings

def find_failures(paths):
    """-> list of failure.json paths, newest first. No paths: the single newest one under out/."""
    found = []
    for p in paths:
        if os.path.isfile(p):
            found.append(p)
        elif os.path.isfile(os.path.join(p, "failure.json")):
            found.append(os.path.join(p, "failure.json"))
        elif os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                if "failure.json" in fns:
                    found.append(os.path.join(dp, "failure.json"))
    if not paths:   # the newest run_fight failure; fall back to any bot failure under out/ (fake-world test runs write failure.json too, but with no real recordings)
        found = glob.glob(os.path.join(ROOT, "out", "fight*", "**", "failure.json"), recursive=True) or glob.glob(os.path.join(ROOT, "out", "**", "failure.json"), recursive=True)
    found.sort(key=lambda f: (-os.path.getmtime(f), f))
    return found[:1] if not paths else found


def resolve_dump(rel, instance, fail_dir, root):
    """Where a recorded flight_dump path lives on disk, or None. The bots record it relative to the game folder."""
    if not rel:
        return None
    rel = str(rel).replace("\\", "/")
    cands = [rel] if os.path.isabs(rel) else []
    if instance is not None:
        cands.append(os.path.join(root, str(instance), rel))
    cands += [os.path.join(fail_dir, os.path.basename(rel)), rel]
    for c in cands:
        if os.path.isfile(c):
            return c
    base = os.path.basename(rel)
    hits = glob.glob(os.path.join(root, "*", "nvcoop_flight", base))
    return hits[0] if hits else None


def guess_dump(instance, failure_time, root):
    """The newest recording of this instance written shortly before the failure was saved, or None."""
    if instance is None:
        return None
    files = glob.glob(os.path.join(root, str(instance), "nvcoop_flight", "*.nvfr"))
    near = [(os.path.getmtime(f), f) for f in files if 0 <= failure_time - os.path.getmtime(f) <= GUESS_WINDOW_S or abs(failure_time - os.path.getmtime(f)) < 5]
    return max(near)[1] if near else None


def collect(failure_json, root):
    """Read failure.json and locate each machine's recording. -> dict with the manifest and a 'machines' list of
    {name, role, instance, path, how, error}."""
    fail_dir = os.path.dirname(failure_json)
    with open(failure_json, encoding="utf-8") as f:
        fj = json.load(f)
    t_fail = os.path.getmtime(failure_json)
    machines = []
    for m in fj.get("machines", []):
        info = {"name": m.get("name"), "role": m.get("role"), "instance": m.get("instance"), "path": None, "how": None, "error": None}
        rel = (m.get("flight_dump") or {}).get("path")
        p = resolve_dump(rel, m.get("instance"), fail_dir, root)
        if p:
            info["path"], info["how"] = p, "recorded"
        else:
            g = guess_dump(m.get("instance"), t_fail, root)
            if g:
                info["path"], info["how"] = g, "guessed from file time"
            else:
                info["error"] = m.get("flight_dump_error") or ("recording not found: %s" % rel if rel else "no flight_dump in failure.json")
        machines.append(info)
    return {"manifest": fj, "machines": machines}


def load_machines(machines):
    """Read the located recordings. -> (traces, raw) where raw is [(slot, header_bytes, [record bytes])]. Unreadable ones get info['error']."""
    traces, raw, slots = [], [], set()
    for m in machines:
        if not m["path"]:
            continue
        try:
            with open(m["path"], "rb") as f:
                data = f.read()
            hdr, recs, h = TR.split_nvfr(data)
            tr = N.loads(data, os.path.basename(m["path"]))
        except (OSError, N.NvfrError) as ex:
            m["error"] = "cannot read %s: %s" % (os.path.basename(m["path"]), ex)
            continue
        if h["slot"] in slots:
            m["error"] = "slot %d is recorded twice (two files from the same machine?)" % h["slot"]
            continue
        slots.add(h["slot"])
        m["slot"], m["records"], m["dropped"] = h["slot"], len(tr["records"]), h["records_dropped"]
        traces.append(tr)
        raw.append((h["slot"], hdr, recs))
    return traces, raw


# ---------------------------------------------------------------- what the live status said

def status_evidence(manifest):
    """Pull the sync symptoms out of the bot's status dumps: digest mismatches, repairs, host-to-client sequence lag, lost links."""
    ev = {"digest_mismatch": 0, "digest_ok": 0, "repairs": 0, "effects_repaired": 0, "lag": [], "disconnected": [], "desync_keys": [], "symptoms": []}
    for m in manifest.get("machines", []):
        st = m.get("status") or {}
        name = m.get("name")
        dg = (st.get("desyncs") or {}).get("digests") or {}
        ev["digest_mismatch"] += int(dg.get("mismatch") or 0)
        ev["digest_ok"] += int(dg.get("ok") or 0)
        ev["repairs"] += int(dg.get("repairs") or 0)
        ev["effects_repaired"] += int(dg.get("effectsRepaired") or 0)
        for d in (st.get("desyncs") or {}).get("desyncs") or []:
            for e in (d.get("entries") or [])[:5]:
                k = "%s: host %s / client %s" % (e.get("key"), e.get("host"), e.get("client"))
                if k not in ev["desync_keys"] and len(ev["desync_keys"]) < 8:
                    ev["desync_keys"].append(k)
        host = st.get("host") if isinstance(st.get("host"), dict) else {}
        if host:
            last = host.get("lastSeq") or 0
            for p in host.get("peers") or []:
                if (p.get("acked") or 0) < last:
                    ev["lag"].append("slot %s acked %s of %s" % (p.get("slot"), p.get("acked"), last))
            if host.get("allSynced") is False and not ev["lag"]:
                ev["lag"].append("host reports allSynced=false")
        if m.get("role") != "host" and st.get("connected") in (0, False):
            ev["disconnected"].append(name)
    if ev["digest_mismatch"]:
        ev["symptoms"].append("digest mismatch x%d" % ev["digest_mismatch"])
    if ev["repairs"]:
        ev["symptoms"].append("%d repair(s), %d effect(s) repaired" % (ev["repairs"], ev["effects_repaired"]))
    if ev["lag"]:
        ev["symptoms"].append("sync lag: " + "; ".join(ev["lag"]))
    if ev["disconnected"]:
        ev["symptoms"].append("not connected: " + ", ".join(ev["disconnected"]))
    return ev


def agreement(ev, cl):
    """One line on whether the recordings explain the live symptoms."""
    classes = {i["class"] for i in cl["items"] if i["class"] in TR.FAILURE_CLASSES}
    if not ev["symptoms"]:
        return "the live status showed no sync symptoms" + ("" if cl["found"] else " and neither do the recordings")
    if not cl["found"]:
        return "live symptoms (%s) are NOT visible in the recordings (ring too short, or digests not recorded)" % "; ".join(ev["symptoms"])
    bits = []
    if ev["digest_mismatch"]:
        bits.append("digest mismatch in status %s in recordings" % ("and" if "digest-mismatch" in classes else "but not"))
    if ev["lag"] or ev["disconnected"]:
        bits.append("sync lag in status %s in recordings" % ("and" if "stuck-sync" in classes else "but not"))
    return "; ".join(bits) or "the recordings show a failure of a different kind than the status counters"


# ---------------------------------------------------------------- fixture and shrink

def build_fixture(raw, traces, source):
    """One independent step per record, machine by machine in file order. at_ms is the aligned time (reference clock, from the first record)."""
    a = A.align(traces)
    by_slot = {t["header"]["slot"]: t for t in traces}
    headers = {}
    steps = []
    for slot, hdr, recs in raw:
        headers[str(slot)] = hdr.hex()
        for r, b in zip(by_slot[slot]["records"], recs):
            steps.append({"op": "rec", "slot": slot, "ty": r["name"], "at_ms": round((a.t(slot, r) - a.t0) / 1000.0, 3), "hex": b.hex()})
    return {"version": 1, "format": TR.FORMAT, "config": {"clients": max(1, len(raw) - 1)}, "headers": headers, "steps": steps, "source": source}


def _with_steps(fx, steps):
    f = dict(fx)
    f["steps"] = steps
    return f


def reduce_fixture(fx, primary, max_runs, max_seconds):
    """Shrink in three cheap-to-expensive stages, each kept only if the SAME failure signature remains:
    a time window around the first failure, whole record types, then delta debugging over the rest."""
    t0 = time.time()
    base = TR.oracle(fx)
    sig = S.signature(base)
    if sig is None:
        raise S.OracleError("the fixture does not reproduce the failure")
    stages = []

    def same(cand):
        r = TR.oracle(cand)
        return bool(r.get("diverged")) and S.signature(r) == sig

    cur = fx
    at = primary["at_ms"]
    for m in MARGINS_MS:
        keep = [s for s in cur["steps"] if abs(s["at_ms"] - at) <= m or s["ty"] == "Meta"]
        if len(keep) < len(cur["steps"]) and same(_with_steps(cur, keep)):
            stages.append("window +-%dms: %d -> %d records" % (m, len(cur["steps"]), len(keep)))
            cur = _with_steps(cur, keep)
            break
        if len(keep) >= len(cur["steps"]):
            break
    for ty in DROP_ORDER:
        keep = [s for s in cur["steps"] if s["ty"] != ty]
        if len(keep) < len(cur["steps"]) and same(_with_steps(cur, keep)):
            stages.append("dropped all %s (%d records)" % (ty, len(cur["steps"]) - len(keep)))
            cur = _with_steps(cur, keep)
    left = max(1.0, max_seconds - (time.time() - t0)) if max_seconds else None
    out = S.shrink_fixture(cur, TR.oracle, max_runs=max_runs, max_seconds=left)
    out["shrunk"]["original_steps"] = len(fx["steps"])
    out["shrunk"]["stages"] = stages
    out["shrunk"]["seconds"] = round(time.time() - t0, 2)
    return out


# ---------------------------------------------------------------- one failure

def case_name(run, fail, explicit=None):
    if explicit:
        return G.safe_name(explicit)
    tag = hashlib.sha1(("%s/%s" % (run, fail)).encode()).hexdigest()[:8]
    return "%s_%s" % (G.safe_name("%s_%s" % (run, fail))[:38].rstrip("_"), tag)


def _source(manifest, machines, run, fail):
    return {"run": run, "failure": fail, "playbook": manifest.get("playbook"), "step": manifest.get("step"), "kind": manifest.get("kind"),
            "reason": manifest.get("reason"), "recordings": [{"name": m["name"], "role": m["role"], "slot": m.get("slot"), "file": os.path.basename(m["path"]),
                                                              "records": m.get("records"), "dropped": m.get("dropped")} for m in machines if m.get("slot") is not None]}


def process(label, run, fail, manifest, machines, opts):
    """Reproduce one failure. -> report dict. machines: located recordings (see collect)."""
    rep = {"label": label, "playbook": manifest.get("playbook"), "step": manifest.get("step"), "kind": manifest.get("kind"), "reason": manifest.get("reason"),
           "machines": [{k: m.get(k) for k in ("name", "role", "instance", "how", "error", "slot", "records", "dropped")} for m in machines]}
    for r, m in zip(rep["machines"], machines):
        r["file"] = os.path.basename(m["path"]) if m.get("path") else None
    ev = status_evidence(manifest)
    rep["status"] = ev
    traces, raw = load_machines(machines)
    for r, m in zip(rep["machines"], machines):
        r.update({k: m.get(k) for k in ("error", "slot", "records", "dropped")})
    if len(traces) < 2:
        rep["verdict"] = "no-recordings"
        rep["message"] = "need recordings from at least 2 machines; got %d (%s)" % (len(traces), "; ".join(m["error"] for m in machines if m.get("error")) or "none located")
        return rep
    t0 = time.time()
    cl = TR.classify(traces)
    rep["replay_seconds"] = round(time.time() - t0, 2)
    rep["classes"] = cl["counts"]
    rep["items"] = cl["items"][:8]
    rep["agreement"] = agreement(ev, cl)
    rep["notes"] = [w for t in traces for w in t["warnings"]] + ["%s: ring dropped %d record(s) before the dump" % (m["name"], m["dropped"]) for m in machines if m.get("dropped")]
    rep["_traces"] = traces
    if not cl["found"]:
        rep["verdict"] = "not-reproduced"
        rep["message"] = "the recordings show no digest mismatch, divergence or stuck sync" + (" (%d transient gap(s) healed by a repair)" % cl["counts"]["transient-gap"] if cl["counts"].get("transient-gap") else "")
        return rep
    rep["verdict"] = "reproduced"
    rep["primary"] = cl["primary"]
    src = _source(manifest, machines, run, fail)
    fx = build_fixture(raw, traces, src)
    fx["evidence"] = {"symptoms": ev["symptoms"], "desync_keys": ev["desync_keys"], "found": cl["primary"]}
    if opts.get("shrink", True):
        try:
            fx = reduce_fixture(fx, cl["primary"], opts.get("max_runs", 3000), opts.get("max_seconds", 30))
            rep["shrunk"] = fx["shrunk"]
        except S.OracleError as ex:
            rep["shrink_error"] = str(ex)
    else:
        fx["shrunk"] = {"signature": S.signature(TR.oracle(fx)), "original_steps": len(fx["steps"]), "reduced_steps": len(fx["steps"]), "complete": False, "stages": []}
    sig = (fx.get("shrunk") or {}).get("signature")
    expect = {"converges": bool(opts.get("expect_converges")), "bug_signature": sig, "meaning": "recorded evidence of the failure"}
    rep["records_in_case"] = len(fx["steps"])
    if opts.get("write", True):
        name = case_name(run, fail, opts.get("name"))
        rep["case"] = G.generate(fx, name, opts.get("out") or DEFAULT_OUT, expect=expect)[0]
    rep["_fixture"] = fx
    return rep


def from_failure_json(fj, opts):
    root = instances_root(opts.get("instances"))
    c = collect(fj, root)
    fail_dir = os.path.dirname(fj)
    fail = os.path.basename(fail_dir)
    run = os.path.basename(os.path.dirname(os.path.dirname(fail_dir))) if os.path.basename(os.path.dirname(fail_dir)) else ""
    return process("%s/%s" % (run, fail), run, fail, c["manifest"], c["machines"], opts)


def from_nvfr(files, opts):
    machines = [{"name": os.path.basename(f), "role": None, "instance": None, "path": f, "how": "given", "error": None} for f in files]
    manifest = {"playbook": "recordings", "step": os.path.basename(files[0]), "kind": "manual", "reason": "recordings given on the command line", "machines": []}
    run = "manual"
    fail = os.path.splitext(os.path.basename(files[0]))[0]
    return process(fail, run, fail, manifest, machines, opts)


# ---------------------------------------------------------------- output

def format_report(rep):
    out = ["== %s" % rep["label"]]
    if rep.get("step"):
        out.append("  step: %s [%s]   %s" % (rep["step"], rep.get("kind"), (rep.get("reason") or "")[:200]))
    for m in rep["machines"]:
        out.append("  %-8s %s" % (m.get("name") or "?", ("%s (%s, slot %s, %s records)" % (m["file"], m.get("how"), m.get("slot"), m.get("records"))) if m.get("file") and m.get("records") is not None
                                  else (m.get("error") or "no recording")))
    st = rep["status"]
    out.append("  live status: " + ("; ".join(st["symptoms"]) if st["symptoms"] else "no sync symptoms"))
    for k in st["desync_keys"][:3]:
        out.append("    desync entry: " + k)
    if rep["verdict"] == "no-recordings":
        out.append("  NO REPRO: " + rep["message"])
        return "\n".join(out)
    out.append("  replayed offline in %.2fs: %s" % (rep["replay_seconds"], ", ".join("%s x%d" % kv for kv in sorted(rep["classes"].items())) or "nothing found"))
    if rep["verdict"] == "reproduced":
        p = rep["primary"]
        out.append("  REPRODUCED: %s (%s) at %.1f ms, suspect slot(s) %s" % (p["class"], p["key"], p["at_ms"], p["suspects"]))
        out.append("    " + p["detail"][:300])
    else:
        out.append("  NOT REPRODUCED: " + rep["message"])
    out.append("  check: " + rep["agreement"])
    for n in rep.get("notes", [])[:3]:
        out.append("  note: " + n)
    sh = rep.get("shrunk")
    if sh:
        out.append("  shrunk %d -> %d records in %d replays, %.1fs%s" % (sh["original_steps"], sh["reduced_steps"], sh["oracle_runs"], sh.get("seconds", 0),
                                                                        "" if sh["complete"] else " (budget hit, may not be minimal)"))
        for s in sh.get("stages", []):
            out.append("    " + s)
    if rep.get("shrink_error"):
        out.append("  shrink skipped: " + rep["shrink_error"])
    if rep.get("case"):
        out.append("  wrote %s" % _shown(rep["case"]))
    return "\n".join(out)


def _shown(p):
    """A path relative to the repo when it is inside it."""
    try:
        r = os.path.relpath(p, ROOT)
        return p if r.startswith("..") else r
    except ValueError:
        return p


def clean(rep):
    return {k: v for k, v in rep.items() if not k.startswith("_")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--instances")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--name")
    ap.add_argument("--no-shrink", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--expect-converges", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=30)
    ap.add_argument("--max-runs", type=int, default=3000)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    opts = {"instances": a.instances, "out": a.out, "name": a.name, "shrink": not a.no_shrink, "write": not a.no_write,
            "expect_converges": a.expect_converges, "max_seconds": a.max_seconds, "max_runs": a.max_runs}
    reps = []
    if len(a.paths) >= 2 and all(p.lower().endswith(".nvfr") for p in a.paths):
        reps.append(from_nvfr(a.paths, opts))
    else:
        if a.paths and any(p.lower().endswith(".nvfr") for p in a.paths):
            print("give either .nvfr files (two or more) or failure paths, not both")
            return 2
        fjs = find_failures(a.paths)
        if not fjs:
            print("no failure.json found" + (" under " + ", ".join(a.paths) if a.paths else " under out/"))
            return 2
        if a.name and len(fjs) > 1:
            opts["name"] = None
        for fj in fjs:
            try:
                reps.append(from_failure_json(fj, opts))
            except (OSError, ValueError) as ex:
                reps.append({"label": fj, "verdict": "no-recordings", "message": "cannot read failure.json: %s" % ex, "machines": [],
                             "status": status_evidence({})})
    if a.json:
        print(json.dumps([clean(r) for r in reps], indent=1, default=str))
    else:
        for r in reps:
            print(format_report(r))
        if len(reps) > 1:
            tally = {}
            for r in reps:
                tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
            print("\n%d failure(s): %s" % (len(reps), ", ".join("%d %s" % (n, v) for v, n in sorted(tally.items()))))
        if any(r.get("case") for r in reps):
            print("replay the written case(s): python -I tools/flight/regress.py %s" % _shown(a.out))
    if a.report and reps and reps[0].get("_traces"):
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(R.render(reps[0]["_traces"], None))
        print("wrote " + a.report)
    if any(r["verdict"] == "no-recordings" for r in reps):
        return 2
    return 1 if any(r["verdict"] == "not-reproduced" for r in reps) else 0


if __name__ == "__main__":
    sys.exit(main())
