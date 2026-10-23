#!/usr/bin/env python
"""Compare two benchmark runs with the same scoring code.

    python -I tools/bench/compare.py out/bench/<before> out/bench/<after> [--noise 5] [--md out.md]

A folder with samples.jsonl is re-scored with the current coop_bench.py (so old and new runs are judged identically); an aggregate folder
(from --runs N) is read as it is. Exit code 1 when the composite fell by more than --noise points or any category fell by more than 2x --noise.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rescore  # noqa: E402


def load(folder: Path):
    if (folder / "samples.jsonl").is_file():
        return rescore.rescore(folder)
    return json.loads((folder / "results.json").read_text(encoding="utf-8"))


def arrow(d, tol=0.5):
    if d is None:
        return "  "
    return "up" if d > tol else ("DOWN" if d < -tol else "=")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--noise", type=float, default=5.0, help="points of composite change that still count as noise (default 5; use the aggregate run's own noise)")
    ap.add_argument("--md", default="")
    a = ap.parse_args()
    b, c = load(Path(a.before)), load(Path(a.after))
    lines = [f"# Benchmark comparison", "", f"before: `{a.before}`  composite {b['composite']}", f"after:  `{a.after}`  composite {c['composite']}", ""]
    dcomp = None if b["composite"] is None or c["composite"] is None else round(c["composite"] - b["composite"], 1)
    lines.append(f"Composite change: **{dcomp:+}** points (noise threshold {a.noise})" if dcomp is not None else "Composite change: n/a")
    lines += ["", "| Category | Before | After | Change |", "|---|---|---|---|"]
    bad = False
    for k in c["categories"]:
        x, y = b["categories"].get(k), c["categories"].get(k)
        d = None if x is None or y is None else round(y - x, 1)
        if d is not None and d < -2 * a.noise:
            bad = True
        lines.append(f"| {k} | {x} | {y} | {'' if d is None else format(d, '+')} {arrow(d)} |")
    lines += ["", "| Metric | Before | After | Score before | Score after | Change |", "|---|---|---|---|---|---|"]
    for m, v in c["scores"].items():
        w = b["scores"].get(m, {})
        sb, sa = w.get("score"), v.get("score")
        d = None if sb is None or sa is None else round(sa - sb, 1)
        lines.append(f"| {m} | {w.get('value')} | {v.get('value')} | {sb} | {sa} | {'' if d is None else format(d, '+')} {arrow(d)} |")
    if dcomp is not None and dcomp < -a.noise:
        bad = True
    lines += ["", "REGRESSION" if bad else "No regression beyond the noise threshold."]
    text = "\n".join(lines)
    print(text)
    if a.md:
        Path(a.md).write_text(text, encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
