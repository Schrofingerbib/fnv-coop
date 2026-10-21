#!/usr/bin/env python3
"""Run one playbook runner per two-copy pair at the same time, on one PC (throughput x pairs).

Pair k is instances 2k (host) and 2k+1 (client), joined on their own co-op port (tools/runner/pairs.py). Set the copies up once:

    python -I tools/runner/run.py provision --pairs 3 --ini coop.enable=1 --ini dev.bots=1 --ini dev.animprobe=1
    python -I tools/runner/run.py up --pairs 3                       # refuses when the free RAM cannot hold the copies

Then, instead of one run_fight.py per pair by hand:

    python -I tools/bots/run_parallel.py --pairs 3                                  # run_fight.py on each pair, all four tests
    python -I tools/bots/run_parallel.py --pairs 3 -- --which fight,handoff         # arguments after -- go to every runner
    python -I tools/bots/run_parallel.py --only 1,2 --runner tools/bots/run_coop.py -- --playbook goodsprings_opening
    python -I tools/bots/run_parallel.py --pairs 2 --dry-run                        # checks + the commands, starts nothing

Each runner gets `--instances 2k,2k+1 --outdir <outdir>/pair<k>`; its output streams here with a [pair<k>] prefix. At the end one table
shows PASS / FAIL (or SETUP, BLOCKED, TIMEOUT, CRASH) per pair with the reason of each failure; <outdir>/parallel_summary.json has it too.

Before it starts anything it refuses (exit 2) when: two pairs would use one co-op port or one instance (the nvcoop.ini files of the
instances are read: wrong role, wrong port, client not on this PC, co-op off), the copies that are not running yet do not fit the free
RAM (0.7 GB each), or another job holds a lease on an instance. --fake skips these checks and passes --fake to every runner (a game-free
run on the fake world: `--pairs 3 --fake -- --which fight --bug double_damage` must FAIL).

Not passed through (this tool sets or cannot share them): --instances --outdir --pool, and the chaos options (one relay is shared).
Exit code: 0 all pairs pass, 1 a pair failed / timed out / crashed, 3 a pair was BLOCKED, 2 setup problem (here or in a runner).
This tool never launches a game, never takes the game slot and never touches a pipe itself; the runners it starts open the pipes.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Deque, Dict, IO, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUNNER_DIR = os.path.join(REPO, "tools", "runner")
if RUNNER_DIR not in sys.path:
    sys.path.insert(0, RUNNER_DIR)

import pairs  # noqa: E402

DEFAULT_RUNNER = os.path.join(HERE, "run_fight.py")
TAIL_LINES = 400
# Options the runner command gets from this tool, or that cannot be shared between pairs.
FORBIDDEN_PASSTHROUGH = {
    "--instances": "this tool gives each pair its own instances",
    "--outdir": "this tool gives each pair its own output folder",
    "--pool": "a pool lease would hand out instances without regard to the pairs",
    "--chaos": "one chaos relay is shared by every pair",
    "--chaos-seed": "one chaos relay is shared by every pair",
    "--chaos-rpc": "one chaos relay is shared by every pair",
}
FAIL_LINE = re.compile(r"^FAIL\s+(\S+)\s+\d+ steps?\s*(.*)$")


# ---------------------------------------------------------------- the plan
class PairPlan:
    def __init__(self, k: int, host: int, client: int, port: int, outdir: str, cmd: List[str]):
        self.k, self.host, self.client, self.port, self.outdir, self.cmd = k, host, client, port, outdir, cmd

    @property
    def name(self) -> str:
        return f"pair{self.k}"

    @property
    def ids(self) -> str:
        return f"{self.host},{self.client}"


def pair_outdir(outroot: str, k: int) -> str:
    return os.path.join(outroot, f"pair{k}")


def runner_cmd(runner: str, ids: str, outdir: str, extra: Sequence[str], fake: bool, python: Optional[str] = None) -> List[str]:
    """-X utf8 and -u: the output is a pipe, so without them it would be buffered and in the ANSI code page."""
    cmd = [python or sys.executable, "-I", "-X", "utf8", "-u", runner, "--instances", ids, "--outdir", outdir]
    if fake:
        cmd.append("--fake")
    return cmd + list(extra)


def plan_pairs(pair_list: Sequence[int], runner: str, outroot: str, port_base: int, extra: Sequence[str], fake: bool) -> List[PairPlan]:
    out = []
    for k in pair_list:
        h, c = pairs.pair_ids(k)
        od = pair_outdir(outroot, k)
        out.append(PairPlan(k, h, c, pairs.port_of(k, port_base), od, runner_cmd(runner, f"{h},{c}", od, extra, fake)))
    return out


def plan_problems(plans: Sequence[PairPlan]) -> List[str]:
    """Problems in the plan itself: two pairs on one port, one instance or one output folder."""
    problems: List[str] = []
    for what, key in (("co-op port", lambda p: p.port), ("output folder", lambda p: os.path.normcase(os.path.abspath(p.outdir)))):
        seen: Dict[Any, PairPlan] = {}
        for p in plans:
            if key(p) in seen:
                problems.append(f"{p.name} and {seen[key(p)].name} would use the same {what} ({key(p)})")
            seen.setdefault(key(p), p)
    owner: Dict[int, PairPlan] = {}
    for p in plans:
        for i in (p.host, p.client):
            if i in owner:
                problems.append(f"{p.name} and {owner[i].name} would both use instance {i}")
            owner.setdefault(i, p)
    return problems


def passthrough_problems(extra: Sequence[str]) -> List[str]:
    out = []
    for tok in extra:
        opt = tok.split("=", 1)[0]
        if opt in FORBIDDEN_PASSTHROUGH:
            out.append(f"{opt} after --: {FORBIDDEN_PASSTHROUGH[opt]}")
    return out


def preflight(plans: Sequence[PairPlan], *, fake: bool, check_ram: bool, check_lease: bool,
              read_cfg: Callable[[int], Dict[str, Optional[str]]] = pairs.read_instance_config,
              free_ram: Callable[[], int] = pairs.free_ram_bytes, is_up: Callable[[int], bool] = pairs.pipe_up,
              held: Optional[Callable[[int], Optional[dict]]] = None, copy_gb: float = pairs.COPY_GB,
              reserve_gb: float = pairs.RESERVE_GB) -> List[str]:
    """Everything that can be checked before a runner starts. Empty list: go."""
    problems = plan_problems(plans)
    if fake:
        return problems
    ids = [i for p in plans for i in (p.host, p.client)]
    problems += pairs.validate_pairs([p.k for p in plans], {i: read_cfg(i) for i in ids})
    if check_ram:
        why = pairs.ram_problem(len(plans), pairs.count_up(ids, is_up), free_ram(), copy_gb, reserve_gb)
        if why:
            problems.append(why)
    if check_lease:
        if held is None:
            import jobqueue
            held = jobqueue.holder
        for i in ids:
            h = held(i)
            if h:
                problems.append(f"instance {i} is leased by another job ({h.get('job') or h}); wait for it or use --no-lease-check")
    return problems


# ---------------------------------------------------------------- running
class PairRun:
    def __init__(self, plan: PairPlan):
        self.plan = plan
        self.proc: Any = None
        self.thread: Optional[threading.Thread] = None
        self.tail: Deque[str] = collections.deque(maxlen=TAIL_LINES)
        self.t0 = 0.0
        self.t1 = 0.0
        self.rc: Optional[int] = None
        self.timed_out = False
        self.error = ""
        self.status = ""
        self.reasons: List[str] = []

    @property
    def seconds(self) -> float:
        return max(0.0, self.t1 - self.t0)


def default_spawn(cmd: List[str], env: Dict[str, str]) -> "subprocess.Popen[str]":
    return subprocess.Popen(cmd, env=env, cwd=REPO, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)


def prefixed(name: str, line: str) -> str:
    return f"[{name}] {line.rstrip()}"


def _pump(run: PairRun, out: IO[str], lock: threading.Lock) -> None:
    try:
        for line in run.proc.stdout:
            text = line.rstrip("\r\n")
            run.tail.append(text)
            with lock:
                print(prefixed(run.plan.name, text), file=out, flush=True)
    except (OSError, ValueError):
        pass


def pair_env(plan: PairPlan) -> Dict[str, str]:
    env = dict(os.environ)
    env["NVCOOP_PAIR"] = str(plan.k)
    env["NVCOOP_INSTANCES"] = plan.ids
    return env


def read_summary(outdir: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(outdir, "summary.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def classify(run: PairRun) -> None:
    """Set run.status and run.reasons from the exit code, the runner's summary.json and the tail of its output."""
    rc = run.rc
    summ = read_summary(run.plan.outdir)
    reasons: List[str] = []
    if summ:
        for r in summ.get("results") or []:
            if isinstance(r, dict) and not r.get("ok", True):
                reasons.append(f"{r.get('playbook', '?')}: {r.get('reason') or 'failed'}")
    if not reasons:
        for line in run.tail:
            m = FAIL_LINE.match(line.strip())
            if m:
                reasons.append(f"{m.group(1)}: {m.group(2) or 'failed'}")
    if run.error:
        run.status, run.reasons = "CRASH", [run.error]
    elif run.timed_out:
        run.status, run.reasons = "TIMEOUT", [f"no result after {run.seconds:.0f} s, the runner was killed"] + reasons
    elif rc == 0 and not (summ and summ.get("ok") is False):
        run.status, run.reasons = "PASS", []
    elif rc == 0:
        run.status, run.reasons = "FAIL", reasons or ["exit code 0 but summary.json says ok=false"]
    else:
        run.status = {1: "FAIL", 2: "SETUP", 3: "BLOCKED"}.get(rc if rc is not None else -1, "CRASH")
        if not reasons:
            keep = [ln.strip() for ln in run.tail if ln.strip()]
            hot = [ln for ln in keep if re.search(r"cannot reach|BLOCKED|error|Traceback|unknown|missing", ln, re.I)]
            reasons = (hot or keep)[-2:] or [f"exit code {rc} and no output"]
        if run.status == "CRASH":
            reasons = [f"exit code {rc}"] + reasons
        run.reasons = reasons


def run_all(plans: Sequence[PairPlan], *, out: IO[str], timeout_s: float = 0.0, stagger_s: float = 0.0,
            spawn: Callable[[List[str], Dict[str, str]], Any] = default_spawn, poll_s: float = 0.05,
            now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> List[PairRun]:
    """Start every pair (stagger_s apart), stream the prefixed output, enforce the timeout, and classify each result."""
    runs = [PairRun(p) for p in plans]
    lock = threading.Lock()
    pending = list(runs)
    next_start = now()
    try:
        while pending or any(r.rc is None and not r.error and r.proc is not None for r in runs):
            if pending and now() >= next_start:
                r = pending.pop(0)
                r.t0 = now()
                try:
                    r.proc = spawn(r.plan.cmd, pair_env(r.plan))
                    r.thread = threading.Thread(target=_pump, args=(r, out, lock), daemon=True)
                    r.thread.start()
                except OSError as e:
                    r.error, r.t1 = f"cannot start the runner: {e}", now()
                next_start = now() + stagger_s
            for r in runs:
                if r.proc is None or r.rc is not None:
                    continue
                rc = r.proc.poll()
                if rc is None and timeout_s > 0 and now() - r.t0 > timeout_s:
                    r.timed_out = True
                    r.proc.kill()
                    rc = r.proc.wait()
                if rc is not None:
                    r.rc, r.t1 = rc, now()
                    if r.thread:
                        r.thread.join(5.0)
            sleep(poll_s)
    finally:
        for r in runs:
            if r.proc is not None and r.rc is None:  # interrupted: never leave a runner behind
                try:
                    r.proc.kill()
                except OSError:
                    pass
    for r in runs:
        if r.proc is None and not r.error:
            r.error = "not started"
        classify(r)
    return runs


# ---------------------------------------------------------------- the report
def combine_exit(runs: Sequence[PairRun]) -> int:
    st = {r.status for r in runs}
    if not runs or st & {"FAIL", "TIMEOUT", "CRASH"}:
        return 1
    if "BLOCKED" in st:
        return 3
    if "SETUP" in st:
        return 2
    return 0


def format_table(runs: Sequence[PairRun], width: int = 100) -> str:
    rows = [("PAIR", "INSTANCES", "PORT", "RESULT", "TIME", "REASON")]
    for r in runs:
        reason = r.reasons[0] if r.reasons else ""
        if len(r.reasons) > 1:
            reason += f"  (+{len(r.reasons) - 1} more)"
        if len(reason) > width:
            reason = reason[:width - 3] + "..."
        rows.append((r.plan.name, r.plan.ids, str(r.plan.port), r.status, f"{r.seconds:.0f}s", reason))
    w = [max(len(row[c]) for row in rows) for c in range(5)]
    lines = ["  ".join(row[c].ljust(w[c]) for c in range(5)) + "  " + row[5] for row in rows]
    lines = [ln.rstrip() for ln in lines]
    passed = sum(1 for r in runs if r.status == "PASS")
    lines.append(f"{passed}/{len(runs)} pairs passed")
    detail = [r for r in runs if r.reasons]
    if detail:
        lines.append("")
        lines.append("failures:")
        for r in detail:
            for reason in r.reasons:
                lines.append(f"  {r.plan.name} ({r.status}): {reason}")
            lines.append(f"    folder: {r.plan.outdir}")
    return "\n".join(lines)


def summary_json(runs: Sequence[PairRun], extra: Sequence[str]) -> Dict[str, Any]:
    return {"ok": combine_exit(runs) == 0, "runner_args": list(extra),
            "pairs": [{"pair": r.plan.k, "host": r.plan.host, "client": r.plan.client, "port": r.plan.port, "status": r.status,
                       "exit_code": r.rc, "seconds": round(r.seconds, 1), "reasons": r.reasons, "outdir": r.plan.outdir,
                       "cmd": r.plan.cmd} for r in runs]}


def parse_pair_list(text: str) -> List[int]:
    ids = [int(x) for x in text.split(",") if x.strip() != ""]
    if not ids or any(i < 0 for i in ids) or len(set(ids)) != len(ids):
        raise ValueError(f"--only needs different pair numbers, 0 or more, such as 0,2; got {text!r}")
    return ids


def main(argv: Optional[Sequence[str]] = None, *, spawn: Callable[[List[str], Dict[str, str]], Any] = default_spawn,
         out: Optional[IO[str]] = None, preflight_kw: Optional[Dict[str, Any]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: List[str] = []
    if "--" in argv:
        k = argv.index("--")
        argv, extra = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", type=int, default=0, help="run pairs 0..N-1 (pair k = instances 2k and 2k+1)")
    ap.add_argument("--only", default="", help="run just these pairs, e.g. 1,2 (to rerun the failed ones)")
    ap.add_argument("--runner", default=DEFAULT_RUNNER, help="the playbook runner to start for each pair (default run_fight.py)")
    ap.add_argument("--outdir", default="", help="default out/parallel/<time>; each pair writes to <outdir>/pair<k>")
    ap.add_argument("--port-base", type=int, default=pairs.PORT_BASE, help="co-op port of pair 0 (as given to provision)")
    ap.add_argument("--fake", action="store_true", help="game-free: pass --fake to every runner, skip the instance checks")
    ap.add_argument("--timeout", type=float, default=1800.0, help="seconds per pair before its runner is killed (0 = none)")
    ap.add_argument("--stagger", type=float, default=0.0, help="seconds between the starts of the pairs")
    ap.add_argument("--dry-run", action="store_true", help="run the checks, print the commands, start nothing")
    ap.add_argument("--no-ram-check", action="store_true")
    ap.add_argument("--copy-gb", type=float, default=pairs.COPY_GB, help="memory of one game copy for the RAM check")
    ap.add_argument("--ram-reserve-gb", type=float, default=pairs.RESERVE_GB)
    ap.add_argument("--no-lease-check", action="store_true", help="do not refuse instances that a run.py job has leased")
    a = ap.parse_args(argv)
    out = out or sys.stdout

    try:
        pair_list = parse_pair_list(a.only) if a.only else list(range(a.pairs))
        if not pair_list:
            raise ValueError("give --pairs N (pairs 0..N-1) or --only 0,2")
        pairs.check_port_base(a.port_base, max(pair_list) + 1)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    bad = passthrough_problems(extra)
    if not os.path.isfile(a.runner):
        bad.append(f"--runner {a.runner}: no such file")
    if bad:
        for b in bad:
            print(f"error: {b}", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%S")
    outroot = a.outdir or os.path.join(REPO, "out", "parallel", stamp)
    plans = plan_pairs(pair_list, os.path.abspath(a.runner), outroot, a.port_base, extra, a.fake)
    problems = preflight(plans, fake=a.fake, check_ram=not a.no_ram_check, check_lease=not a.no_lease_check,
                         copy_gb=a.copy_gb, reserve_gb=a.ram_reserve_gb, **(preflight_kw or {}))
    if problems:
        print("refusing to start:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    for p in plans:
        print(f"{p.name}: instances {p.ids}, port {p.port}, outdir {p.outdir}", file=out)
        if a.dry_run:
            print("  " + " ".join(p.cmd), file=out)
    if a.dry_run:
        print("dry run: checks passed, nothing started", file=out)
        return 0

    os.makedirs(outroot, exist_ok=True)
    runs = run_all(plans, out=out, timeout_s=a.timeout, stagger_s=a.stagger, spawn=spawn)
    print(file=out)
    print(format_table(runs), file=out)
    with open(os.path.join(outroot, "parallel_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary_json(runs, extra), f, indent=1)
    print(f"summary: {os.path.join(outroot, 'parallel_summary.json')}", file=out)
    return combine_exit(runs)


if __name__ == "__main__":
    sys.exit(main())
