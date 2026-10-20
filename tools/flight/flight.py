"""Flight trace tools. Usage: python -I tools/flight/flight.py <command> ...

  print FILE [--type NetIn,Digest] [--limit N]     pretty-print one .nvfr trace
  align FILE FILE [FILE ...]                       line up 2 to 4 machines (clock offsets, lost/duplicated messages)
  diverge FILE FILE [...] [--json] [--check]       first divergence plus causal chain (--check: exit 1 if one is found)
  report FILE FILE [...] -o report.html [--shrink shrunk.json]
  fixture FILE FILE [...] -o fixture.json         build a replay fixture from the traces
  shrink fixture.json --replay-cmd "exe {fixture}" -o shrunk.json [--max-runs N] [--max-seconds S]
  gentest shrunk.json --name NAME [--out DIR]     emit a regression test (JSON fixture + cmake add_test)
  regress DIR --replay-cmd "exe {fixture}"        run every generated regression fixture
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nvfr  # noqa: E402
import analyze  # noqa: E402
import shrink  # noqa: E402
import gentest  # noqa: E402
import report  # noqa: E402
import regress  # noqa: E402

DEFAULT_REGRESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regress")


def _load_all(paths):
    out = []
    for p in paths:
        try:
            out.append(nvfr.load(p))
        except (OSError, nvfr.NvfrError) as ex:
            raise SystemExit("cannot read %s: %s" % (p, ex))
    if len(out) < 2:
        raise SystemExit("need at least 2 traces (one per machine)")
    return out


def _json_div(d):
    if d is None:
        return None
    d = dict(d)
    chain = d.pop("chain")
    d["chain"] = {"window_us": chain["window_us"], "notes": chain["notes"],
                  "records": [{"slot": e["slot"], "aligned_us": e["aligned_us"], "role": e["role"], "name": e["rec"]["name"],
                               "what": nvfr.describe(e["rec"])} for e in chain["records"]]}
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("print"); p.add_argument("file"); p.add_argument("--type"); p.add_argument("--limit", type=int)
    p = sub.add_parser("align"); p.add_argument("files", nargs="+")
    p = sub.add_parser("diverge"); p.add_argument("files", nargs="+"); p.add_argument("--json", action="store_true"); p.add_argument("--check", action="store_true")
    p = sub.add_parser("report"); p.add_argument("files", nargs="+"); p.add_argument("-o", required=True); p.add_argument("--shrink")
    p = sub.add_parser("fixture"); p.add_argument("files", nargs="+"); p.add_argument("-o", required=True)
    p = sub.add_parser("shrink"); p.add_argument("fixture"); p.add_argument("--replay-cmd", required=True); p.add_argument("-o", required=True)
    p.add_argument("--max-runs", type=int, default=2000); p.add_argument("--max-seconds", type=float); p.add_argument("--tmpdir")
    p = sub.add_parser("gentest"); p.add_argument("shrunk"); p.add_argument("--name", required=True); p.add_argument("--out", default=DEFAULT_REGRESS)
    p = sub.add_parser("regress"); p.add_argument("dir"); p.add_argument("--replay-cmd", required=True); p.add_argument("--tmpdir")
    a = ap.parse_args(argv)

    if a.cmd == "print":
        try:
            tr = nvfr.load(a.file)
        except (OSError, nvfr.NvfrError) as ex:
            raise SystemExit("cannot read %s: %s" % (a.file, ex))
        types = {t.strip().lower() for t in a.type.split(",")} if a.type else None
        print(nvfr.pretty(tr, types, a.limit))
        return 0
    if a.cmd == "align":
        print(analyze.format_alignment(analyze.align(_load_all(a.files))))
        return 0
    if a.cmd == "diverge":
        al = analyze.align(_load_all(a.files))
        d = analyze.find_divergence(al)
        if a.json:
            print(json.dumps(_json_div(d), indent=1, sort_keys=True, default=str))
        else:
            print(analyze.format_alignment(al))
            print(analyze.format_divergence(al, d))
        return 1 if (a.check and d) else 0
    if a.cmd == "report":
        sh = None
        if a.shrink:
            with open(a.shrink) as f:
                sh = json.load(f)
        html = report.render(_load_all(a.files), sh)
        with open(a.o, "w", encoding="utf-8") as f:
            f.write(html)
        print("wrote " + a.o)
        return 0
    if a.cmd == "fixture":
        fx = shrink.fixture_from_traces(_load_all(a.files))
        with open(a.o, "w") as f:
            json.dump(fx, f, indent=1)
        print("wrote %s (%d steps, %d skipped)" % (a.o, len(fx["steps"]), fx["source"]["skipped_steps"]))
        return 0
    if a.cmd == "shrink":
        with open(a.fixture) as f:
            fx = json.load(f)
        try:
            out = shrink.shrink_fixture(fx, shrink.CommandOracle(a.replay_cmd, tmpdir=a.tmpdir), a.max_runs, a.max_seconds)
        except shrink.OracleError as ex:
            print("shrink failed: %s" % ex)
            return 2
        with open(a.o, "w") as f:
            json.dump(out, f, indent=1)
        s = out["shrunk"]
        print("shrunk %d -> %d steps in %d replays%s; failure: %s" % (
            s["original_steps"], s["reduced_steps"], s["oracle_runs"], "" if s["complete"] else " (budget hit, may not be minimal)", s["signature"]))
        return 0
    if a.cmd == "gentest":
        with open(a.shrunk) as f:
            fx = json.load(f)
        for path in gentest.generate(fx, a.name, a.out):
            print("wrote " + path)
        return 0
    if a.cmd == "regress":
        return regress.main([a.dir, "--replay-cmd", a.replay_cmd] + (["--tmpdir", a.tmpdir] if a.tmpdir else []))
    return 2


if __name__ == "__main__":
    sys.exit(main())
