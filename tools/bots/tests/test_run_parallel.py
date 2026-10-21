"""Game-free tests of tools/bots/run_parallel.py: one runner per two-copy pair, run at the same time, one combined table.

The runners here are tiny scripts (real subprocesses, no game, no pipe) that behave as their command line says, and the
real run_fight.py on the fake world. Each check_* method takes a mutant name: None runs the real code and must pass; a
mutant name runs a broken copy and the check must then fail with an AssertionError (a test_twin_* test requires that).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest import mock

HERE = Path(__file__).resolve().parent
BOTS = HERE.parent
REPO = BOTS.parent.parent
sys.path.insert(0, str(BOTS))
sys.path.insert(0, str(REPO / "tools" / "runner"))

import pairs  # noqa: E402
import run_parallel as rp  # noqa: E402

GIB = 2 ** 30

FAKE_RUNNER = r'''
import argparse, json, os, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("--instances"); ap.add_argument("--outdir"); ap.add_argument("--fake", action="store_true")
ap.add_argument("--mode-for", default="")      # "2=fail,4=hang": the mode of the pair whose host instance is 2 / 4; others pass
ap.add_argument("--sleep", type=float, default=0.0)
a, _ = ap.parse_known_args()
ids = a.instances.split(",")
modes = dict(x.split("=") for x in a.mode_for.split(",") if x)
mode = modes.get(ids[0], "pass")
os.makedirs(a.outdir, exist_ok=True)
t0 = time.time()
for n in range(3):
    print("tick", n, "of", ids[0], flush=True)
    time.sleep(a.sleep / 3)
def summary(ok, reason=""):
    json.dump({"ok": ok, "results": [{"playbook": "fake_pb", "ok": ok, "reason": reason}]}, open(os.path.join(a.outdir, "summary.json"), "w"))
if mode == "pass":
    summary(True)
elif mode == "fail":                      # reason only in summary.json
    summary(False, "summary says the client lost a hit")
    sys.exit(1)
elif mode == "failnosummary":             # reason only in the log
    print("FAIL  fake_pb  4 steps  the log says the host double counted", flush=True)
    sys.exit(1)
elif mode == "exit0notok":                # exit code 0 but the summary is not ok
    summary(False, "quiet failure")
elif mode == "setup":
    print("cannot reach the game instances: cannot open pipe nvcoop-" + ids[0], file=sys.stderr, flush=True)
    sys.exit(2)
elif mode == "blocked":
    print("BLOCKED: 1 required bridge method(s) missing in this build: coop.owner", flush=True)
    sys.exit(3)
elif mode == "crash":
    sys.exit(139)
elif mode == "hang":
    time.sleep(4)
    summary(True)
json.dump({"instances": a.instances, "outdir": a.outdir, "pair": os.environ.get("NVCOOP_PAIR"),
           "env_instances": os.environ.get("NVCOOP_INSTANCES"), "t0": t0, "t1": time.time(), "fake": a.fake},
          open(os.path.join(a.outdir, "argv.json"), "w"))
'''


# ------------------------------------------------------------------ broken copies (mutants)
REAL_RUN_ALL = rp.run_all
REAL_VALIDATE = pairs.validate_pairs
REAL_PREFLIGHT = rp.preflight


def _run_all_no_timeout(plans, **kw):
    kw["timeout_s"] = 0.0
    return REAL_RUN_ALL(plans, **kw)


def _run_all_serial(plans, **kw):
    """Runs one pair after the other."""
    out: List = []
    for p in plans:
        out += REAL_RUN_ALL([p], **kw)
    return out


def _classify_exit_code_only(run):
    """Ignores the summary and the log: the reason of a failure is lost."""
    run.status = "PASS" if run.rc == 0 else "FAIL"
    run.reasons = []


def _classify_ignores_exit(run):
    """Everything that ends is a PASS."""
    run.status, run.reasons = "PASS", []


def _validate_without_dup(pair_list, cfgs):
    return [p for p in REAL_VALIDATE(pair_list, cfgs) if "share co-op port" not in p]


MUTANTS = {
    "ids_constant": [(pairs, "pair_ids", lambda k: (0, 1))],
    "outdir_shared": [(rp, "pair_outdir", lambda root, k: root)],
    "unprefixed": [(rp, "prefixed", lambda name, line: line.rstrip())],
    "exit_always_ok": [(rp, "combine_exit", lambda runs: 0)],
    "exit_first_only": [(rp, "combine_exit", lambda runs: 0 if runs and runs[0].status == "PASS" else 1)],
    "no_summary": [(rp, "read_summary", lambda outdir: None)],
    "no_log_fallback": [(rp, "FAIL_LINE", __import__("re").compile(r"(?!)"))],
    "exit_code_only": [(rp, "classify", _classify_exit_code_only)],
    "ignores_exit": [(rp, "classify", _classify_ignores_exit)],
    "no_timeout": [(rp, "run_all", _run_all_no_timeout)],
    "serial": [(rp, "run_all", _run_all_serial)],
    "plan_unchecked": [(rp, "plan_problems", lambda plans: [])],
    "port_constant": [(pairs, "port_of", lambda k, base=pairs.PORT_BASE: int(base))],
    "ini_dup_port_unchecked": [(pairs, "validate_pairs", _validate_without_dup)],
    "ram_unchecked": [(pairs, "ram_problem", lambda *a, **k: None)],
    "passthrough_unchecked": [(rp, "passthrough_problems", lambda extra: [])],
    "lease_unchecked": [(rp, "preflight", lambda plans, **kw: REAL_PREFLIGHT(plans, **{**kw, "check_lease": False}))],
}


@contextlib.contextmanager
def mutant(name: Optional[str]):
    with contextlib.ExitStack() as st:
        for obj, attr, new in MUTANTS[name] if name else []:
            st.enter_context(mock.patch.object(obj, attr, new))
        yield


def good_cfg(i: int) -> Dict[str, Optional[str]]:
    """What provision --pairs --ini coop.enable=1 writes for instance i."""
    return {"present": "1", "enable": "1", "role": pairs.role_of(i), "port": str(pairs.port_of(pairs.pair_of(i))),
            "host": "127.0.0.1", "listen": "127.0.0.1", "instanceid": str(i), "bridge": str(i), "pipe": rf"\\.\pipe\nvcoop-{i}"}


class Parallel(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.runner = self.tmp / "fake_runner.py"
        self.runner.write_text(FAKE_RUNNER, encoding="utf-8")
        self.outroot = str(self.tmp / "out")

    def go(self, args: List[str], extra: Optional[List[str]] = None, **kw):
        """main() in-process. Returns (exit code, stdout, stderr). The preflight reads fake instance data, never the real one."""
        out, err = io.StringIO(), io.StringIO()
        pk = {"read_cfg": lambda i: good_cfg(i), "free_ram": lambda: 64 * GIB, "is_up": lambda i: True, "held": lambda i: None}
        pk.update(kw.pop("preflight_kw", {}))
        argv = ["--runner", str(self.runner), "--outdir", self.outroot, *args] + (["--"] + extra if extra is not None else [])
        with contextlib.redirect_stderr(err):
            code = rp.main(argv, out=out, preflight_kw=pk, **kw)
        return code, out.getvalue(), err.getvalue()

    def argv_json(self, k: int) -> dict:
        return json.loads((Path(self.outroot) / f"pair{k}" / "argv.json").read_text())

    # ---------------------------------------------------------------- every pair gets its own instances, folder, env
    def check_pairs_isolated(self, mut):
        with mutant(mut):
            code, out, err = self.go(["--pairs", "3"], ["--sleep", "0.1"])
            self.assertEqual(code, 0, out + err)
            seen_out = set()
            for k in range(3):
                a = self.argv_json(k)
                self.assertEqual(a["instances"], f"{2 * k},{2 * k + 1}", f"pair {k} gets instances 2k,2k+1")
                self.assertEqual((a["pair"], a["env_instances"]), (str(k), a["instances"]))
                seen_out.add(a["outdir"])
                self.assertEqual(os.path.normcase(os.path.abspath(a["outdir"])),
                                 os.path.normcase(os.path.abspath(os.path.join(self.outroot, f"pair{k}"))))
            self.assertEqual(len(seen_out), 3, "three different output folders")
            self.assertEqual(out.count("PASS"), 3)
            self.assertIn("3/3 pairs passed", out)
            self.assertIn("27961", out)

    def test_pairs_isolated(self):
        self.check_pairs_isolated(None)

    def test_twin_ids_constant(self):
        with self.assertRaises(AssertionError):
            self.check_pairs_isolated("ids_constant")

    def test_twin_outdir_shared(self):
        with self.assertRaises(AssertionError):
            self.check_pairs_isolated("outdir_shared")

    # ---------------------------------------------------------------- the combined table: every kind of result
    MODES = "2=fail,4=failnosummary,6=setup,8=blocked,10=crash,12=hang,14=exit0notok"

    def check_table(self, mut):
        with mutant(mut):
            code, out, err = self.go(["--pairs", "8", "--timeout", "1.5"], ["--mode-for", self.MODES])
            self.assertEqual(code, 1, "a failed pair fails the whole run")
            lines = out.splitlines()
            head = next(n for n, ln in enumerate(lines) if ln.startswith("PAIR "))
            rows = {ln.split()[0]: ln for ln in lines[head + 1:head + 9]}
            want = {"pair0": "PASS", "pair1": "FAIL", "pair2": "FAIL", "pair3": "SETUP", "pair4": "BLOCKED", "pair5": "CRASH",
                    "pair6": "TIMEOUT", "pair7": "FAIL"}
            for pair, status in want.items():
                self.assertIn(pair, rows, out)
                self.assertEqual(rows[pair].split()[3], status, f"{pair}: {rows[pair]}")
            self.assertIn("1/8 pairs passed", out)
            # each failure has its reason, from the summary or from the log
            self.assertIn("fake_pb: summary says the client lost a hit", out)
            self.assertIn("fake_pb: the log says the host double counted", out)
            self.assertIn("cannot reach the game instances", out.split("failures:")[1])
            self.assertIn("BLOCKED: 1 required bridge method", out.split("failures:")[1])
            self.assertIn("exit code 139", out)
            self.assertIn("no result after", out)
            self.assertIn("quiet failure", out)
            saved = json.loads((Path(self.outroot) / "parallel_summary.json").read_text())
            self.assertFalse(saved["ok"])
            self.assertEqual([p["status"] for p in saved["pairs"]], list(want.values()))

    def test_table(self):
        self.check_table(None)

    def test_twin_no_summary(self):
        with self.assertRaises(AssertionError):
            self.check_table("no_summary")

    def test_twin_no_log_fallback(self):
        with self.assertRaises(AssertionError):
            self.check_table("no_log_fallback")

    def test_twin_exit_code_only(self):
        with self.assertRaises(AssertionError):
            self.check_table("exit_code_only")

    def test_twin_ignores_exit(self):
        with self.assertRaises(AssertionError):
            self.check_table("ignores_exit")

    def test_twin_no_timeout(self):
        with self.assertRaises(AssertionError):
            self.check_table("no_timeout")

    def check_exit_codes(self, mut):
        with mutant(mut):
            for modes, want in (("", 0), ("0=fail", 1), ("2=fail", 1), ("2=hang", 1), ("2=crash", 1), ("0=setup", 2),
                                ("2=blocked", 3), ("0=setup,2=blocked", 3), ("0=setup,2=fail", 1)):
                code, out, err = self.go(["--pairs", "2", "--timeout", "1"], ["--mode-for", modes])
                self.assertEqual(code, want, f"modes {modes!r}: exit {code}\n{out}")

    def test_exit_codes(self):
        self.check_exit_codes(None)

    def test_twin_exit_always_ok(self):
        with self.assertRaises(AssertionError):
            self.check_exit_codes("exit_always_ok")

    def test_twin_exit_first_pair_only(self):
        with self.assertRaises(AssertionError):
            self.check_exit_codes("exit_first_only")

    # ---------------------------------------------------------------- the log: every line has its pair prefix
    def check_prefix(self, mut):
        with mutant(mut):
            code, out, err = self.go(["--pairs", "3"], ["--sleep", "0.3"])
            self.assertEqual(code, 0, out + err)
            ticks = [ln for ln in out.splitlines() if "tick" in ln]
            self.assertEqual(len(ticks), 9, "3 ticks from each of 3 runners")
            for k in range(3):
                mine = [ln for ln in ticks if ln.startswith(f"[pair{k}] ")]
                self.assertEqual(len(mine), 3, f"pair {k}: {ticks}")
                self.assertTrue(all(f"of {2 * k}" in ln for ln in mine), "a line is prefixed with the pair that printed it")

    def test_prefix(self):
        self.check_prefix(None)

    def test_twin_unprefixed(self):
        with self.assertRaises(AssertionError):
            self.check_prefix("unprefixed")

    # ---------------------------------------------------------------- the pairs really run at the same time
    def check_concurrent(self, mut):
        with mutant(mut):
            code, out, err = self.go(["--pairs", "3"], ["--sleep", "1.5"])
            self.assertEqual(code, 0, out + err)
            runs = [self.argv_json(k) for k in range(3)]
            self.assertLess(max(r["t0"] for r in runs), min(r["t1"] for r in runs), "all three were running at one moment")

    def test_concurrent(self):
        self.check_concurrent(None)

    def test_twin_serial(self):
        with self.assertRaises(AssertionError):
            self.check_concurrent("serial")

    # ---------------------------------------------------------------- refusals before anything starts
    def never_spawn(self, cmd, env):
        raise AssertionError("a runner was started although the checks refuse")

    def check_refusals(self, mut):
        """Each case must exit 2 with a message and start no runner."""
        cases = [
            ("two pairs on one port (ini)", ["--pairs", "3"],
             {"read_cfg": lambda i: {**good_cfg(i), "port": "27960"}}, "share co-op port"),
            ("RAM", ["--pairs", "3"], {"free_ram": lambda: 3 * GIB, "is_up": lambda i: False}, "refusing 3 pair(s)"),
            ("RAM, many pairs", ["--pairs", "50"], {"free_ram": lambda: 8 * GIB, "is_up": lambda i: False}, "at most"),
            ("leased", ["--pairs", "2"], {"held": lambda i: {"job": "vms01"} if i == 3 else None}, "instance 3 is leased"),
            ("client elsewhere", ["--pairs", "2"], {"read_cfg": lambda i: {**good_cfg(i), "host": "10.1.2.3"} if i == 1 else good_cfg(i)},
             "not this PC"),
        ]
        with mutant(mut):
            for name, args, kw, text in cases:
                code, out, err = self.go(args, ["--sleep", "0"], preflight_kw=kw, spawn=self.never_spawn)
                self.assertEqual(code, 2, f"{name}: exit {code}\n{out}{err}")
                self.assertIn(text, err, name)
                self.assertIn("refusing to start", err)
                self.assertFalse(os.path.exists(os.path.join(self.outroot, "pair0")), f"{name}: no output folder is made")

    def test_refusals(self):
        self.check_refusals(None)

    def test_twin_ini_dup_port_unchecked(self):
        with self.assertRaises(AssertionError):
            self.check_refusals("ini_dup_port_unchecked")

    def test_twin_ram_unchecked(self):
        with self.assertRaises(AssertionError):
            self.check_refusals("ram_unchecked")

    def test_twin_lease_unchecked(self):
        with self.assertRaises(AssertionError):
            self.check_refusals("lease_unchecked")

    def test_overrides_let_it_through(self):
        # --no-ram-check and --no-lease-check each lift only their own refusal
        kw = {"free_ram": lambda: 3 * GIB, "is_up": lambda i: False, "held": lambda i: {"job": "x"}}
        code, out, err = self.go(["--pairs", "2", "--dry-run"], ["--sleep", "0"], preflight_kw=kw)
        self.assertEqual(code, 2)
        code, out, err = self.go(["--pairs", "2", "--dry-run", "--no-ram-check", "--no-lease-check"], ["--sleep", "0"], preflight_kw=kw)
        self.assertEqual(code, 0, err)
        self.assertIn("dry run: checks passed", out)

    def check_plan_checked(self, mut):
        """Two pairs given the same port by the planner are caught even before the ini files are read."""
        with mutant(mut):
            code, out, err = self.go(["--pairs", "3", "--dry-run"], preflight_kw={"read_cfg": lambda i: good_cfg(i)})
            self.assertEqual(code, 2, out + err)
            self.assertIn("same co-op port", err)

    def test_plan_checked(self):
        with mock.patch.object(pairs, "port_of", lambda k, base=pairs.PORT_BASE: int(base)):
            self.check_plan_checked(None)

    def test_twin_plan_unchecked(self):
        with mock.patch.object(pairs, "port_of", lambda k, base=pairs.PORT_BASE: int(base)):
            with self.assertRaises(AssertionError):
                self.check_plan_checked("plan_unchecked")

    def test_plan_problems_unit(self):
        mk = lambda k, h, c, port, od: rp.PairPlan(k, h, c, port, od, [])   # noqa: E731
        self.assertEqual(rp.plan_problems([mk(0, 0, 1, 1, "a"), mk(1, 2, 3, 2, "b")]), [])
        self.assertEqual(len(rp.plan_problems([mk(0, 0, 1, 1, "a"), mk(1, 2, 3, 1, "b")])), 1)
        self.assertEqual(len(rp.plan_problems([mk(0, 0, 1, 1, "a"), mk(1, 2, 3, 2, "a")])), 1)
        self.assertEqual(len(rp.plan_problems([mk(0, 0, 1, 1, "a"), mk(1, 1, 3, 2, "b")])), 1)

    def check_passthrough(self, mut):
        with mutant(mut):
            for bad in (["--instances", "0,1"], ["--outdir=x"], ["--pool"], ["--chaos", "bad_wifi"], ["--chaos-rpc", "r.json"]):
                code, out, err = self.go(["--pairs", "2"], bad, spawn=self.never_spawn)
                self.assertEqual(code, 2, f"{bad}: exit {code}")
                self.assertIn(bad[0].split("=")[0], err)

    def test_passthrough(self):
        self.check_passthrough(None)

    def test_twin_passthrough_unchecked(self):
        with self.assertRaises(AssertionError):
            self.check_passthrough("passthrough_unchecked")

    def test_bad_arguments(self):
        for args in ([], ["--only", "1,1"], ["--only", "-1"], ["--only", "x"], ["--pairs", "2", "--port-base", "65535"],
                     ["--pairs", "2", "--runner", str(self.tmp / "missing.py")]):
            code, out, err = self.go(args, spawn=self.never_spawn)   # a later --runner overrides the one that go() adds
            self.assertEqual(code, 2, f"{args}: {out}{err}")

    def test_only_selects_pairs(self):
        code, out, err = self.go(["--only", "2,0"], ["--sleep", "0"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.argv_json(2)["instances"], "4,5")
        self.assertFalse((Path(self.outroot) / "pair1").exists())

    def test_runner_that_cannot_start_is_a_crash_row(self):
        def spawn(cmd, env):
            raise OSError("no such interpreter")
        code, out, err = self.go(["--pairs", "2"], ["--sleep", "0"], spawn=spawn)
        self.assertEqual(code, 1)
        self.assertEqual(out.split("failures:")[1].count("(CRASH)"), 2)
        self.assertIn("cannot start the runner: no such interpreter", out)

    def test_interrupt_kills_runners(self):
        killed = []

        class Proc:
            stdout = iter(())
            returncode = None

            def poll(self):
                return None

            def kill(self):
                killed.append(1)

        sleeps = []

        def sleep(s):
            sleeps.append(s)
            if len(sleeps) > 3:
                raise KeyboardInterrupt

        plans = rp.plan_pairs([0, 1], str(self.runner), self.outroot, pairs.PORT_BASE, [], True)
        with self.assertRaises(KeyboardInterrupt):
            rp.run_all(plans, out=io.StringIO(), spawn=lambda c, e: Proc(), sleep=sleep)
        self.assertEqual(len(killed), 2, "Ctrl-C must not leave a runner behind")

    # ---------------------------------------------------------------- the real runner on the fake world
    def test_real_run_fight_fake_three_pairs(self):
        code, out, err = self.go(["--pairs", "3", "--fake", "--runner", str(BOTS / "run_fight.py")], ["--which", "fight"], preflight_kw={"read_cfg": None})
        self.assertEqual(code, 0, out + err)
        self.assertIn("3/3 pairs passed", out)
        self.assertEqual(out.count("[pair0] PASS"), 1)

    def test_real_run_fight_fake_bug_fails_every_pair_with_reason(self):
        code, out, err = self.go(["--pairs", "3", "--fake", "--runner", str(BOTS / "run_fight.py")], ["--which", "fight", "--bug", "double_damage"], preflight_kw={"read_cfg": None})
        self.assertEqual(code, 1, out + err)
        self.assertIn("0/3 pairs passed", out)
        self.assertEqual(out.split("failures:")[1].count("a hit was applied twice"), 3)

    def test_script_entry_under_isolated_mode(self):
        p = subprocess.run([sys.executable, "-I", str(BOTS / "run_parallel.py"), "--pairs", "2", "--fake", "--outdir", self.outroot,
                            "--", "--which", "fight"], capture_output=True, text=True, errors="replace")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("2/2 pairs passed", p.stdout)
        p = subprocess.run([sys.executable, "-I", str(BOTS / "run_parallel.py"), "--pairs", "2", "--dry-run", "--fake", "--outdir", self.outroot],
                           capture_output=True, text=True, errors="replace")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--instances 2,3", p.stdout)


if __name__ == "__main__":
    unittest.main()
