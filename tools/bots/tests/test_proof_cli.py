"""Command lines and run guards of the in-game proof scripts (proof_glue.py, proof_input.py).

A bad command line must make main() return 2 ("could not run") with a message that names the argument, BEFORE the bridge
is opened, and main() must never raise SystemExit. An empty form value is a bad command line. A missing game, and a game
lost during the run (crash or pipe loss), must also give 2, never a traceback.
proof_glue --other-info must be parsed and used by the dialog check, and --info / --other-info are compared as
(plugin, local id) numbers: every spelling of a form gives the same answer, and FalloutNV.esm:1800 is not 800.
Mutation twins: in-process copies of the scripts with a check removed or with the old code put back must be caught by
the same oracles.

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
from bots import primitives as P  # noqa: E402
from bots import proof_args  # noqa: E402
from bots.bot import ConnectionLost  # noqa: E402
from nvbridge import GameCrashed  # noqa: E402  (bots.bot put tools/runner on sys.path)

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


def call_main(mod, argv, mutant=None, connect_error=None, setup=None):
    """One in-process main() call over a fake connector -> (exit code, stdout, stderr, instances it opened).

    `setup(nv)` changes the fake bridge before the run. SystemExit is NOT caught here: main() must return its code."""
    opened = []

    def connect(instance):
        opened.append(instance)
        if connect_error is not None:
            raise connect_error
        nv = mod.fake_bridge(mutant)
        if setup is not None:
            setup(nv)
        return contextlib.nullcontext(nv)

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main(argv, connect=connect)
    return code, out.getvalue(), err.getvalue(), opened


def _main_or_defect(mod, argv, **kw):
    """(call_main result, None), or (None, a sentence) when main() raised instead of returning an exit code."""
    try:
        return call_main(mod, argv, **kw), None
    except SystemExit as e:
        return None, f"raised SystemExit({e.code}) instead of returning an exit code"
    except Exception as e:  # noqa: BLE001  a crash is a defect, not a test error
        return None, f"raised {type(e).__name__} out of main(): {e}"


def refusal_defect(mod, argv, flags):
    """None when main() refuses `argv` up front: returns 2, every name in `flags` on stderr, no bridge opened.
    Else a sentence that says what went wrong (the oracle the twins must trip)."""
    res, defect = _main_or_defect(mod, argv)
    if defect:
        return defect
    code, _, err, opened = res
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
    res, defect = _main_or_defect(mod, argv, connect_error=ConnectionLost(r"cannot open \\.\pipe\nvcoop-97"))
    if defect:
        return defect
    code, _, err, opened = res
    if opened != [int(NO_GAME)]:
        return f"opened {opened}, wanted [{NO_GAME}]"
    if code != 2:
        return f"exit code {code}, wanted 2"
    if "could not run" not in err or NO_GAME not in err:
        return f"the message does not say why: {err!r}"
    return None


def lose_on(method, exc):
    """A setup for call_main: the fake loses the game (raises `exc`) when the proof calls `method` ('exec': any console line)."""
    def setup(nv):
        call, exe = nv.call, nv.exec

        def lost_call(m, params=None, timeout_s=35.0):
            if m == method:
                raise exc
            return call(m, params, timeout_s)

        def lost_exec(cmd, timeout_ms=None):
            if method == "exec":
                raise exc
            return exe(cmd, timeout_ms)
        nv.call, nv.exec = lost_call, lost_exec
    return setup


def lose_on_exec(text, exc):
    """A setup for call_main: the fake loses the game (raises `exc`) on the first console line that contains `text`."""
    def setup(nv):
        exe = nv.exec

        def lost_exec(cmd, timeout_ms=None):
            if text in cmd:
                raise exc
            return exe(cmd, timeout_ms)
        nv.exec = lost_exec
    return setup


class _NeverRaised(Exception):
    """Stands in for ConnectionLost in a twin, so an `except ConnectionLost` clause catches nothing."""


def lost_defect(mod, argv, setup):
    """None when a game lost in the middle of the run gives exit 2 and a 'could not run' message, with no traceback
    and no proof verdict. Else the defect."""
    res, defect = _main_or_defect(mod, argv, setup=setup)
    if defect:
        return defect
    code, out, err, _ = res
    if code != 2:
        return f"exit code {code}, wanted 2"
    if "could not run" not in err or "lost the game" not in err:
        return f"the message does not say why: {err!r}"
    if "PROOF" in out:
        return f"printed a proof verdict for a run that did not finish: {out[-200:]!r}"
    if "PASS" not in out:
        return "the game was lost before the first check, so this case does not test a loss in the middle of the run"
    return None


def cannot_run_defect(argv, flags, setup=None):
    """None when proof_glue opens the bridge, then returns 2 with a 'could not run' message that names `flags`, before
    any check runs (nothing is driven). Else the defect."""
    res, defect = _main_or_defect(glue, argv, setup=setup)
    if defect:
        return defect
    code, out, err, opened = res
    if code != 2:
        return f"exit code {code}, wanted 2"
    if opened != [int(NO_GAME)]:
        return f"opened {opened}, wanted [{NO_GAME}]"
    missing = [f for f in flags if f not in err]
    if "could not run" not in err or missing:
        return f"the message does not say why (missing {missing}): {err!r}"
    if "PASS" in out or "FAIL" in out:
        return f"a check ran before the refusal: {out[-200:]!r}"
    return None


def common(name):
    return ["--instance", NO_GAME, "--out", str(OUT / name)]


def rows(name):
    return {r["check"]: r for r in json.loads((OUT / name).read_text(encoding="utf-8"))}


# case -> (argv, the option names the message must contain)
GLUE_BAD = {
    "missing_npc": (["--info", INFO], ["--npc"]),
    "missing_info": (["--npc", NPC], ["--info"]),
    "missing_both": ([], ["--npc", "--info"]),
    "empty_npc": (["--npc", "", "--info", INFO], ["--npc"]),
    "bad_info": (["--npc", NPC, "--info", "GREETING"], ["--info"]),
    "bad_base": (["--npc", NPC, "--info", INFO, "--base", "SunnySmilesREF"], ["--base"]),
    "bad_other_info": (["--npc", NPC, "--info", INFO, "--other-info", "Sunny line 2"], ["--other-info"]),
    "empty_other_info": (["--npc", NPC, "--info", INFO, "--other-info", ""], ["--other-info"]),
    "blank_other_info": (["--npc", NPC, "--info", INFO, "--other-info", "  "], ["--other-info"]),
    "empty_item": (["--npc", NPC, "--info", INFO, "--item", ""], ["--item"]),
    "same_other_info": (["--npc", NPC, "--info", INFO, "--other-info", "FalloutNV.esm:0800"], ["--other-info", "--info"]),
    "same_other_info_0x": (["--npc", NPC, "--info", INFO, "--other-info", "falloutnv.esm:0x00000800"], ["--other-info", "--info"]),
    "same_other_info_bare": (["--npc", NPC, "--info", "800", "--other-info", "0X0800"], ["--other-info", "--info"]),
    "unknown_option": (["--npc", NPC, "--info", INFO, "--bogus"], ["--bogus"]),
    "bad_instance": (["--npc", NPC, "--info", INFO, "--instance", "x"], ["--instance"]),
}
INPUT_BAD = {
    "bad_target": (["--target", "SunnySmilesREF"], ["--target"]),
    "bad_door": (["--door", "door 1"], ["--door"]),
    "empty_target": (["--target", ""], ["--target"]),
    "empty_door": (["--door", ""], ["--door"]),
    "unknown_option": (["--bogus"], ["--bogus"]),
    "bad_instance": (["--instance", "x"], ["--instance"]),
}


def _only(keep):
    return {k: v for k, v in glue.REQUIRED.items() if k in keep}


def _problems_skipping_empty(a, required, forms):
    """Twin: the old check (`if v and ...`) let an empty form value through, which turned its check off."""
    out = []
    for dest in required:
        if not getattr(a, dest, None):
            out.append(f"argument {proof_args.flag(dest)} is missing")
    for dest in forms:
        v = getattr(a, dest, None)
        if v and not proof_args.FORM_RE.fullmatch(v):
            out.append(f"argument {proof_args.flag(dest)}: {v!r} is not a form id")
    return out


def _check_args_by_number(a):
    """Twin: the old same-INFO refusal compared only the numbers, so other plugins' INFOs with that number were refused."""
    bad = proof_args.problems(a, glue.REQUIRED, glue.FORMS)
    if not bad and a.other_info and int(a.other_info.rsplit(":", 1)[-1], 16) == int(a.info.rsplit(":", 1)[-1], 16):
        bad.append("argument --other-info names the same INFO number as --info")
    return bad


def _string_key(form, plugins):
    """Twin: forms compared as spellings, not numbers ('0x801' is not '801'), as the old dialog check did."""
    if isinstance(form, int):
        return None, f"{form:x}"
    plugin, _, hexpart = form.rpartition(":")
    return plugin.lower() or None, hexpart.lower()


def _low24_key(form, plugins):
    """Twin: the low 24 bits compared even when the plugin is not known to match (the load index is ignored)."""
    plugin, num = proof_args.parse_form(form) if isinstance(form, str) else (None, form)
    return plugin or plugins[0].lower(), num & proof_args.LOCAL_MASK


def _suffix_offers(choices, want, plugins):
    """Twin: the old dialog check matched the end of the text, so FalloutNV.esm:1800 also matched 800."""
    return any(c["info"].lower().endswith(f"{want[1]:x}") for c in choices)


def _dialog_keys_without_same_check(a, plugins):
    """Twin: --info and --other-info resolved, but a bare id and a Plugin.esm:HEX id of the same INFO are let through."""
    return (proof_args.form_key(a.info, plugins),
            proof_args.form_key(a.other_info, plugins) if a.other_info is not None else None)


# twin -> (patcher that makes the in-process broken copy, the case that must catch it)
GLUE_TWINS = {
    "no_validation": (lambda: mock.patch.object(glue, "check_args", lambda a: []), "missing_npc"),
    "npc_not_required": (lambda: mock.patch.object(glue, "REQUIRED", _only({"info"})), "missing_npc"),
    "info_not_required": (lambda: mock.patch.object(glue, "REQUIRED", _only({"npc"})), "missing_info"),
    "forms_unchecked": (lambda: mock.patch.object(glue, "FORMS", ()), "bad_base"),
    "same_info_allowed": (lambda: mock.patch.object(
        glue, "check_args", lambda a: proof_args.problems(a, glue.REQUIRED, glue.FORMS)), "same_other_info"),
    "empty_values_allowed": (lambda: mock.patch.object(proof_args, "problems", _problems_skipping_empty), "empty_item"),
    "usage_exits": (lambda: mock.patch.object(proof_args, "refuse", lambda ap, bad: ap.error("; ".join(bad))), "missing_npc"),
    "parse_exits": (lambda: mock.patch.object(proof_args, "parse", lambda ap, argv: (ap.parse_args(argv), None)), "unknown_option"),
}
INPUT_TWINS = {
    "no_validation": (lambda: mock.patch.object(inp, "check_args", lambda a: []), "bad_target"),
    "forms_unchecked": (lambda: mock.patch.object(inp, "FORMS", ()), "bad_target"),
    "empty_values_allowed": (lambda: mock.patch.object(proof_args, "problems", _problems_skipping_empty), "empty_target"),
    "usage_exits": (lambda: mock.patch.object(proof_args, "refuse", lambda ap, bad: ap.error("; ".join(bad))), "bad_target"),
    "parse_exits": (lambda: mock.patch.object(proof_args, "parse", lambda ap, argv: (ap.parse_args(argv), None)), "unknown_option"),
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

    def test_every_form_argument_refuses_an_empty_value(self):
        """An empty value would turn the check of that option off without a word (`if a.x:` is false for "")."""
        base = {"npc": NPC, "info": INFO}
        for mod, forms in ((glue, glue.FORMS), (inp, inp.FORMS)):
            for dest in forms:
                with self.subTest(script=mod.__name__, arg=dest):
                    given = dict(base if mod is glue else {}, **{dest: ""})
                    argv = [x for k, v in given.items() for x in (proof_args.flag(k), v)]
                    self.assertIsNone(refusal_defect(mod, argv + common("empty.json"), [proof_args.flag(dest)]))

    def test_help_returns_0_without_system_exit(self):
        for mod in (glue, inp):
            with self.subTest(script=mod.__name__):
                res, defect = _main_or_defect(mod, ["--help"])
                self.assertIsNone(defect)
                self.assertEqual(res[0], 0)
                self.assertIn("usage:", res[1])
                with mock.patch.object(proof_args, "parse", lambda ap, argv: (ap.parse_args(argv), None)):
                    self.assertIsNotNone(_main_or_defect(mod, ["--help"])[1], "twin parse_exits slipped through --help")

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


# A game lost in the middle of the run: script -> (argv, setup). proof_glue loses it when it asks for the dialogue
# choices (after the talk), proof_input on its first console line (after the hook check), or on its first sneak read
# (after the jump check: the sneak check turns a failed read into a FAIL, but a lost game must still give 2).
LOST = {
    "glue_connection_lost": (glue, ["--npc", NPC, "--info", INFO],
                             lose_on(P.M_BOT_DIALOG, ConnectionLost("fake: pipe closed"))),
    "glue_game_crashed": (glue, ["--npc", NPC, "--info", INFO],
                          lose_on(P.M_BOT_DIALOG, GameCrashed(4242, "fake: pipe closed"))),
    "input_connection_lost": (inp, [], lose_on("exec", ConnectionLost("fake: pipe closed"))),
    "input_game_crashed": (inp, [], lose_on("exec", GameCrashed(4242, "fake: pipe closed"))),
    "input_lost_in_sneak": (inp, [], lose_on_exec(inp.SNEAK_EXPR, ConnectionLost("fake: pipe closed"))),
    "input_crashed_in_sneak": (inp, [], lose_on_exec(inp.SNEAK_EXPR, GameCrashed(4242, "fake: pipe closed"))),
}


class LostGame(unittest.TestCase):
    """A game crash or a pipe loss during the run is 'could not run' (2), not a traceback with exit 1."""

    def test_a_lost_game_is_exit_2(self):
        for case, (mod, argv, setup) in LOST.items():
            with self.subTest(case=case):
                self.assertIsNone(lost_defect(mod, argv + common(case + ".json"), setup))

    def test_unguarded_run_is_caught(self):
        for case, (mod, argv, setup) in LOST.items():
            with self.subTest(case=case):
                with mock.patch.object(proof_args, "guarded", lambda where, body: body()):
                    self.assertIsNotNone(lost_defect(mod, argv + common("unguarded_run.json"), setup))

    def test_the_sneak_check_does_not_hide_a_lost_game(self):
        for case in ("input_lost_in_sneak", "input_crashed_in_sneak"):
            mod, argv, setup = LOST[case]
            with self.subTest(case=case):
                self.assertIsNone(lost_defect(mod, argv + common(case + ".json"), setup))
                # twin: the sneak block catches the lost game as well (a sneak FAIL row and exit 1 instead of exit 2)
                with mock.patch.object(inp, "ConnectionLost", _NeverRaised):
                    defect = lost_defect(mod, argv + common("sneak_swallows_loss.json"), setup)
                self.assertIsNotNone(defect, "twin sneak_swallows_loss slipped through " + case)


class OtherInfo(unittest.TestCase):
    def test_parsed(self):
        self.assertEqual(glue.parser().parse_args(["--other-info", OTHER]).other_info, OTHER)
        self.assertIn("--other-info", glue.parser().format_help())

    def test_used_by_the_dialog_check(self):
        code, out, err, _ = call_main(glue, ["--npc", NPC, "--info", INFO, "--other-info", OTHER] + common("other_ok.json"))
        self.assertEqual(code, 0, out + err)
        d = rows("other_ok.json")["dialog"]
        self.assertEqual((d["result"], d["other_info"], d["other_offered"]), ("PASS", OTHER, False))

    def test_the_fake_self_test_uses_it(self):
        code, out, err, _ = call_main(glue, ["--fake", "--out", str(OUT / "other_fake.json")])
        self.assertEqual(code, 0, out + err)
        d = rows("other_fake.json")["dialog"]
        self.assertEqual((d["result"], d["other_info"], d["other_offered"]), ("PASS", OTHER, False))

    def test_a_dialog_list_that_offers_everything_fails_only_with_it(self):
        """Twin: the fake mutant dialog_lists_all. Without --other-info it slips through, so the option is what catches it."""
        code, out, err, _ = call_main(glue, ["--npc", NPC, "--info", INFO, "--other-info", OTHER] + common("other_mut.json"),
                                      mutant="dialog_lists_all")
        self.assertEqual(code, 1, out + err)
        d = rows("other_mut.json")["dialog"]
        self.assertEqual((d["result"], d["other_offered"]), ("FAIL", True))
        code, out, err, _ = call_main(glue, ["--npc", NPC, "--info", INFO] + common("other_mut_off.json"), mutant="dialog_lists_all")
        self.assertEqual(rows("other_mut_off.json")["dialog"]["result"], "PASS", out + err)


# Every spelling that FORM_RE accepts for the INFO 0x801 (another actor's line) and for the NPC's INFO 0x800.
OTHER_SPELLINGS = ("FalloutNV.esm:801", "falloutnv.esm:801", "FalloutNV.esm:0x801", "FalloutNV.esm:0801",
                   "FalloutNV.esm:00000801", "801", "0x801", "0X801", "0801", "00000801")
INFO_SPELLINGS = ("FalloutNV.esm:800", "FalloutNV.esm:0x0800", "800", "0x800", "00000800")


def spelling_defect(info, other):
    """None when --info `info` and --other-info `other` keep the negative control: the healthy fake passes the dialog
    check, and the mutant dialog_lists_all fails it with other_offered=True. Else the defect."""
    argv = ["--npc", NPC, "--info", info, "--other-info", other] + common("spelling.json")
    res, defect = _main_or_defect(glue, argv)
    if defect:
        return defect
    if res[0] != 0:
        return f"healthy fake: exit {res[0]}, wanted 0: {res[2].strip()[-200:]}"
    res, defect = _main_or_defect(glue, argv, mutant="dialog_lists_all")
    if defect:
        return defect
    d = rows("spelling.json")["dialog"]
    if res[0] != 1 or (d["result"], d.get("other_offered")) != ("FAIL", True):
        return f"mutant dialog_lists_all: exit {res[0]}, dialog {d['result']}, other_offered {d.get('other_offered')}: the negative control is off"
    return None


def _second_plugin(nv):
    """A setup for call_main: the fake game loads FalloutNV.esm (index 00) and DeadMoney.esm (index 01)."""
    nv.plugins = lambda: ["FalloutNV.esm", "DeadMoney.esm"]


def _suffix_world(nv):
    """A setup for call_main: the NPC 0x602 offers 0x1800, another actor offers 0x800 (whose hex is a suffix of 1800)."""
    nv.world.things[0x602].topics = [0x1800]
    nv.world.things[0x603].topics = [0x800]


def collision_defect():
    """None when --info FalloutNV.esm:1800 --other-info FalloutNV.esm:800 passes on a healthy fake whose NPC offers 1800
    and fails on the mutant dialog_lists_all. Else the defect (a text match says 800 is offered because 1800 ends in 800)."""
    argv = ["--npc", NPC, "--info", "FalloutNV.esm:1800", "--other-info", "FalloutNV.esm:800"] + common("collision.json")
    res, defect = _main_or_defect(glue, argv, setup=_suffix_world)
    if defect:
        return defect
    d = rows("collision.json")["dialog"]
    if res[0] != 0 or (d["result"], d.get("other_offered")) != ("PASS", False):
        return f"healthy fake: exit {res[0]}, dialog {d['result']}, other_offered {d.get('other_offered')}: 800 matched 1800"
    res, defect = _main_or_defect(glue, argv, mutant="dialog_lists_all", setup=_suffix_world)
    if defect:
        return defect
    if res[0] != 1:
        return f"mutant dialog_lists_all: exit {res[0]}, wanted 1"
    return None


def other_plugin_defect():
    """None when --other-info DeadMoney.esm:800 (the same number as --info, another plugin) is not refused up front
    and passes on a healthy fake that loads both plugins. Else the defect."""
    argv = ["--npc", NPC, "--info", INFO, "--other-info", "DeadMoney.esm:800"] + common("other_plugin.json")
    res, defect = _main_or_defect(glue, argv, setup=_second_plugin)
    if defect:
        return defect
    code, out, err, opened = res
    if (code, opened) != (0, [int(NO_GAME)]):
        return f"exit {code}, opened {opened}, wanted 0 and [{NO_GAME}]: {err.strip()[-200:]}"
    return None


# case -> (argv, the names the 'could not run' message must contain, setup). Each is refused after the bridge opens
# (the load order is needed) and before any check runs.
GLUE_CANNOT_RUN = {
    "index_past_load_order": (["--other-info", "01000801"], ["--other-info", "01000801"], None),
    "index_not_its_plugin": (["--other-info", "FalloutNV.esm:01000801"], ["--other-info", "load index"], None),
    "other_plugin_not_loaded": (["--other-info", "DeadMoney.esm:801"], ["--other-info", "not loaded"], None),
    "info_plugin_not_loaded": (["--info", "DeadMoney.esm:800"], ["--info", "not loaded"], None),
    "same_info_bare_and_plugin": (["--other-info", "00000800"], ["--other-info", "same INFO"], None),
    "same_info_runtime_id": (["--info", "DeadMoney.esm:800", "--other-info", "0x01000800"], ["--other-info", "same INFO"],
                             _second_plugin),
}


def _glue_argv(extra):
    """--npc NPC --info INFO, with the options in `extra` given or replaced."""
    given = {"--npc": NPC, "--info": INFO}
    given.update(dict(zip(extra[::2], extra[1::2])))
    return [x for kv in given.items() for x in kv]


# twin -> (patcher, oracle call that must return a defect under the twin)
FORM_TWINS = {
    "string_forms": (lambda: mock.patch.object(proof_args, "form_key", _string_key),
                     lambda: spelling_defect(INFO, "0x801")),
    "suffix_match": (lambda: mock.patch.object(glue, "offers", _suffix_offers), collision_defect),
    "same_info_by_number": (lambda: mock.patch.object(glue, "check_args", _check_args_by_number), other_plugin_defect),
    "low24_always": (lambda: mock.patch.object(proof_args, "form_key", _low24_key),
                     lambda: cannot_run_defect(_glue_argv(["--other-info", "01000801"]) + common("low24.json"),
                                               ["--other-info"])),
    "runtime_same_unchecked": (lambda: mock.patch.object(glue, "dialog_keys", _dialog_keys_without_same_check),
                               lambda: cannot_run_defect(_glue_argv(["--other-info", "00000800"]) + common("same_rt.json"),
                                                         ["--other-info", "same INFO"])),
}

P2 = ["FalloutNV.esm", "DeadMoney.esm"]
# form -> the key form_key gives with the load order P2, or ValueError
KEYS = {
    "FalloutNV.esm:801": ("falloutnv.esm", 0x801),
    "falloutnv.esm:0x0801": ("falloutnv.esm", 0x801),
    "FalloutNV.esm:00000801": ("falloutnv.esm", 0x801),
    "00000801": ("falloutnv.esm", 0x801),
    "0x801": ("falloutnv.esm", 0x801),
    "01000801": ("deadmoney.esm", 0x801),
    "DeadMoney.esm:01000801": ("deadmoney.esm", 0x801),
    0x01000801: ("deadmoney.esm", 0x801),
    "FalloutNV.esm:01000801": ValueError,
    "02000801": ValueError,
    "Missing.esp:801": ValueError,
    "GREETING": ValueError,
    "": ValueError,
}


def key_table_defect():
    """None when proof_args.form_key gives every entry of KEYS. Else the first difference."""
    for form, want in KEYS.items():
        try:
            got = proof_args.form_key(form, P2)
        except ValueError:
            got = ValueError
        if got != want:
            return f"form_key({form!r}) = {got}, wanted {want}"
    return None


class FormMatching(unittest.TestCase):
    """--info and --other-info are compared as (plugin, local id) numbers, never as text."""

    def test_every_spelling_keeps_the_negative_control(self):
        for other in OTHER_SPELLINGS:
            with self.subTest(other_info=other):
                self.assertIsNone(spelling_defect(INFO, other))
        for info in INFO_SPELLINGS:
            with self.subTest(info=info):
                self.assertIsNone(spelling_defect(info, OTHER))

    def test_1800_is_not_800(self):
        self.assertIsNone(collision_defect())

    def test_the_same_number_in_another_plugin_is_not_the_same_info(self):
        self.assertIsNone(other_plugin_defect())

    def test_forms_the_load_order_cannot_resolve_are_refused_before_any_check(self):
        for case, (extra, flags, setup) in GLUE_CANNOT_RUN.items():
            with self.subTest(case=case):
                self.assertIsNone(cannot_run_defect(_glue_argv(extra) + common(case + ".json"), flags, setup))

    def test_form_key_rule(self):
        """A runtime id compares by its low 24 bits only when its plugin is known to match."""
        self.assertIsNone(key_table_defect())
        for twin in (_string_key, _low24_key):
            with self.subTest(twin=twin.__name__):
                with mock.patch.object(proof_args, "form_key", twin):
                    self.assertIsNotNone(key_table_defect())

    def test_form_twins_are_caught(self):
        for twin, (patcher, oracle) in FORM_TWINS.items():
            with self.subTest(twin=twin):
                self.assertIsNone(oracle(), "the healthy script must pass the oracle of " + twin)
                with patcher():
                    defect = oracle()
                self.assertIsNotNone(defect, f"twin {twin} slipped through")


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
