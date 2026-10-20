"""Command lines of the in-game proof scripts (proof_glue.py, proof_input.py).

A bad command line must exit 2 ("could not run") with a message that names the argument, BEFORE the bridge is opened.
A missing game must also exit 2. proof_glue --other-info must be parsed and used by the dialog check.
Mutation twins: in-process copies of the scripts with a check removed must be caught by the same oracles.

No test here opens a real bridge. Every in-process main() call gets a `connect` that returns a FakeBridge, and the
two subprocess tests use instance 97, which no game uses, and are refused before any connect.
"""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
from bots import proof_args  # noqa: E402
from bots.bot import ConnectionLost  # noqa: E402

OUT = ROOT / "out" / "T6a" / "tmp" / "cli"
NO_GAME = "97"
NPC, INFO, OTHER = "FalloutNV.esm:602", "FalloutNV.esm:800", "FalloutNV.esm:801"


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_cli", ROOT / "tools" / "bots" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


glue = _load("proof_glue")
inp = _load("proof_input")


def call_main(mod, argv, mutant=None, connect_error=None):
    """One in-process main() call over a fake connector -> (exit code, stdout, stderr, instances it opened)."""
    opened = []

    def connect(instance):
        opened.append(instance)
        if connect_error is not None:
            raise connect_error
        return contextlib.nullcontext(mod.fake_bridge(mutant))

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = mod.main(argv, connect=connect)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue(), opened


def refusal_defect(mod, argv, flags):
    """None when main() refuses `argv` up front: exit 2, every name in `flags` on stderr, no bridge opened.
    Else a sentence that says what went wrong (the oracle the twins must trip)."""
    try:
        code, _, err, opened = call_main(mod, argv)
    except Exception as e:  # noqa: BLE001  a crash is a defect, not a test error
        return f"crashed instead of refusing: {type(e).__name__}: {e}"
    if opened:
        return f"opened the bridge to instance {opened} before refusing"
    if code != 2:
        return f"exit code {code}, wanted 2"
    missing = [f for f in flags if f not in err]
    if missing:
        return f"the message does not name {missing}: {err!r}"
    return None


def unreachable_defect(mod, argv):
    """None when a connector that cannot reach the game gives exit 2 and a 'could not run' message. Else the defect."""
    try:
        code, _, err, opened = call_main(mod, argv, connect_error=ConnectionLost(r"cannot open \\.\pipe\nvcoop-97"))
    except Exception as e:  # noqa: BLE001
        return f"crashed instead of exit 2: {type(e).__name__}: {e}"
    if opened != [int(NO_GAME)]:
        return f"opened {opened}, wanted [{NO_GAME}]"
    if code != 2:
        return f"exit code {code}, wanted 2"
    if "could not run" not in err or NO_GAME not in err:
        return f"the message does not say why: {err!r}"
    return None


def common(name):
    return ["--instance", NO_GAME, "--out", str(OUT / name)]


# case -> (argv, the option names the message must contain)
GLUE_BAD = {
    "missing_npc": (["--info", INFO], ["--npc"]),
    "missing_info": (["--npc", NPC], ["--info"]),
    "missing_both": ([], ["--npc", "--info"]),
    "empty_npc": (["--npc", "", "--info", INFO], ["--npc"]),
    "bad_info": (["--npc", NPC, "--info", "GREETING"], ["--info"]),
    "bad_base": (["--npc", NPC, "--info", INFO, "--base", "SunnySmilesREF"], ["--base"]),
    "bad_other_info": (["--npc", NPC, "--info", INFO, "--other-info", "Sunny line 2"], ["--other-info"]),
    "same_other_info": (["--npc", NPC, "--info", INFO, "--other-info", "FalloutNV.esm:0800"], ["--other-info", "--info"]),
}
INPUT_BAD = {
    "bad_target": (["--target", "SunnySmilesREF"], ["--target"]),
    "bad_door": (["--door", "door 1"], ["--door"]),
}


def _only(keep):
    return {k: v for k, v in glue.REQUIRED.items() if k in keep}


# twin -> (patcher that makes the in-process broken copy, the case that must catch it)
GLUE_TWINS = {
    "no_validation": (lambda: mock.patch.object(glue, "check_args", lambda a: []), "missing_npc"),
    "npc_not_required": (lambda: mock.patch.object(glue, "REQUIRED", _only({"info"})), "missing_npc"),
    "info_not_required": (lambda: mock.patch.object(glue, "REQUIRED", _only({"npc"})), "missing_info"),
    "forms_unchecked": (lambda: mock.patch.object(glue, "FORMS", ()), "bad_base"),
    "same_info_allowed": (lambda: mock.patch.object(
        glue, "check_args", lambda a: proof_args.problems(a, glue.REQUIRED, glue.FORMS)), "same_other_info"),
}
INPUT_TWINS = {
    "no_validation": (lambda: mock.patch.object(inp, "check_args", lambda a: []), "bad_target"),
    "forms_unchecked": (lambda: mock.patch.object(inp, "FORMS", ()), "bad_target"),
}


class RequiredArguments(unittest.TestCase):
    def test_glue_refuses_each_bad_command_line_up_front(self):
        for case, (argv, flags) in GLUE_BAD.items():
            with self.subTest(case=case):
                self.assertIsNone(refusal_defect(glue, argv + common(case), flags))

    def test_input_refuses_each_bad_command_line_up_front(self):
        for case, (argv, flags) in INPUT_BAD.items():
            with self.subTest(case=case):
                self.assertIsNone(refusal_defect(inp, argv + common(case), flags))

    def test_good_command_lines_run_over_the_fake(self):
        code, out, err, opened = call_main(glue, ["--npc", NPC, "--info", INFO] + common("glue_ok.json"))
        self.assertEqual((code, opened), (0, [int(NO_GAME)]), out + err)
        self.assertIn("PROOF OK", out)
        code, out, err, opened = call_main(inp, ["--target", "FalloutNV.esm:603", "--door", "601"] + common("input_ok.json"))
        self.assertEqual((code, opened), (0, [int(NO_GAME)]), out + err)

    def test_no_game_is_exit_2_not_a_failed_proof(self):
        self.assertIsNone(unreachable_defect(glue, ["--npc", NPC, "--info", INFO] + common("glue_nogame.json")))
        self.assertIsNone(unreachable_defect(inp, common("input_nogame.json")))

    def test_the_command_line_exit_code_is_2(self):
        """Through sys.exit(main()) in a real interpreter. Refused before any connect, so no pipe is opened."""
        for script, argv, flag in (("proof_glue.py", ["--info", INFO], "--npc"),
                                   ("proof_input.py", ["--target", "SunnySmilesREF"], "--target")):
            with self.subTest(script=script):
                p = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "bots" / script)] + argv + common(script + ".json"),
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn(flag, p.stderr)
                self.assertNotIn("PROOF", p.stdout)


class OtherInfo(unittest.TestCase):
    def rows(self, name):
        return {r["check"]: r for r in json.loads((OUT / name).read_text(encoding="utf-8"))}

    def test_parsed(self):
        self.assertEqual(glue.parser().parse_args(["--other-info", OTHER]).other_info, OTHER)
        self.assertIn("--other-info", glue.parser().format_help())

    def test_used_by_the_dialog_check(self):
        code, out, err, _ = call_main(glue, ["--npc", NPC, "--info", INFO, "--other-info", OTHER] + common("other_ok.json"))
        self.assertEqual(code, 0, out + err)
        d = self.rows("other_ok.json")["dialog"]
        self.assertEqual((d["result"], d["other_info"], d["other_offered"]), ("PASS", OTHER, False))

    def test_the_fake_self_test_uses_it(self):
        code, out, err, _ = call_main(glue, ["--fake", "--out", str(OUT / "other_fake.json")])
        self.assertEqual(code, 0, out + err)
        d = self.rows("other_fake.json")["dialog"]
        self.assertEqual((d["result"], d["other_info"], d["other_offered"]), ("PASS", OTHER, False))

    def test_a_dialog_list_that_offers_everything_fails_only_with_it(self):
        """Twin: the fake mutant dialog_lists_all. Without --other-info it slips through, so the option is what catches it."""
        code, out, err, _ = call_main(glue, ["--npc", NPC, "--info", INFO, "--other-info", OTHER] + common("other_mut.json"),
                                      mutant="dialog_lists_all")
        self.assertEqual(code, 1, out + err)
        d = self.rows("other_mut.json")["dialog"]
        self.assertEqual((d["result"], d["other_offered"]), ("FAIL", True))
        code, out, err, _ = call_main(glue, ["--npc", NPC, "--info", INFO] + common("other_mut_off.json"), mutant="dialog_lists_all")
        self.assertEqual(self.rows("other_mut_off.json")["dialog"]["result"], "PASS", out + err)


class MutationTwins(unittest.TestCase):
    """Each in-process broken copy must trip the oracle on its case, and the healthy scripts must not."""

    def _caught(self, mod, twins, cases):
        for twin, (patcher, case) in twins.items():
            argv, flags = cases[case]
            with self.subTest(twin=twin):
                self.assertIsNone(refusal_defect(mod, argv + common(case), flags), "the healthy script must refuse " + case)
                with patcher():
                    defect = refusal_defect(mod, argv + common(f"{twin}.json"), flags)
                self.assertIsNotNone(defect, f"twin {twin} slipped through case {case}")

    def test_glue_twins_are_caught(self):
        self._caught(glue, GLUE_TWINS, GLUE_BAD)

    def test_input_twins_are_caught(self):
        self._caught(inp, INPUT_TWINS, INPUT_BAD)

    def test_unguarded_connect_is_caught(self):
        for mod, argv in ((glue, ["--npc", NPC, "--info", INFO]), (inp, [])):
            with self.subTest(script=mod.__name__):
                with mock.patch.object(proof_args, "open_bridge", lambda connect, instance: connect(instance)):
                    self.assertIsNotNone(unreachable_defect(mod, argv + common("unguarded.json")))


if __name__ == "__main__":
    unittest.main()
