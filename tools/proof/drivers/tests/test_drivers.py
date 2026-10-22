"""Game-free tests for the scripted pieces in tools/proof/drivers (release .cmd scripts, launcher loopback, public tree build, split-screen smoke).

    python -I tools/proof/drivers/tests/test_drivers.py          (run_tests.py in this folder adds the mutation twins)

Two kinds of twin: `Twin*` tests damage the thing the driver checks (a broken release script, a launcher fault switched on with fnv_mut) and
require the driver to FAIL; the PROOF_DRIVER_MUTANT twins in run_tests.py damage the driver itself and require these tests to fail.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
DRIVERS = HERE.parent
REPO = DRIVERS.parents[2]
sys.path.insert(0, str(DRIVERS))
import drv_common as D  # noqa: E402
import launcher_loopback as LB  # noqa: E402
import load_scenario as LS  # noqa: E402
import public_tree_build as PT  # noqa: E402
import release_cmd_scripts as RC  # noqa: E402
import splitscreen_smoke as SS  # noqa: E402

sys.path.insert(0, str(REPO / "tools" / "installer"))
import fnv_mut  # noqa: E402

# built in pieces: the public export scan looks for this handle and for personal paths, so this file must not hold them as one string
OWNER = "mth" + "wj"


def collect():
    lines = []
    return lines, lines.append


# ------------------------------------------------------------------ split-screen smoke
class FakeCall:
    """Stands in for the subprocess runner of splitscreen_smoke: records argv, answers by script name."""

    def __init__(self, **codes):
        self.calls = []
        self.codes = codes

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        key = argv[0].rsplit("/", 1)[-1] + (":" + argv[1] if argv[0].endswith(("launch.py", "gameslot.py")) else "")
        code, text = self.codes.get(key, (0, "ok"))
        return code, text

    def names(self):
        return [c[0].rsplit("/", 1)[-1] + ":" + c[1] if c[0].endswith(("launch.py", "gameslot.py")) else c[0].rsplit("/", 1)[-1] for c in self.calls]


ARGS = SimpleNamespace(players=2, split="lr", autoload="autosave", movement=True)


class SplitSmoke(unittest.TestCase):
    def go(self, **codes):
        fc = FakeCall(**codes)
        lines, out = collect()
        rc = SS.run(ARGS, call=fc, out=out)
        return rc, fc, "\n".join(lines)

    def test_happy_path_runs_up_smoke_down_in_that_order_and_releases_the_slot(self):
        rc, fc, text = self.go()
        self.assertEqual(rc, 0, text)
        self.assertEqual(fc.names(), ["gameslot.py:acquire", "launch.py:up", "smoke_playbook.py", "launch.py:down", "gameslot.py:release"])
        up = fc.calls[1]
        self.assertIn("--dev-inject", up, "the synthetic controller hook is what makes the smoke playbook need no real pad")
        self.assertEqual(up[up.index("--autoload") + 1], "autosave")
        self.assertEqual(up[up.index("--pads") + 1], "none,0")
        self.assertIn("--no-keep", up)
        self.assertIn("--movement", fc.calls[2])
        self.assertIn("PASS splitscreen smoke", text)

    def test_pads_per_player_count(self):
        self.assertEqual([SS.pads_for(n) for n in (2, 3, 4)], ["none,0", "none,0,1", "none,0,1,2"])

    def test_a_refused_start_is_an_env_problem_and_the_smoke_never_runs(self):
        rc, fc, text = self.go(**{"launch.py:up": (2, "Fallout: New Vegas is already running (pids [1])")})
        self.assertEqual(rc, 2)
        self.assertIn("cannot reach the game instances", text)
        self.assertNotIn("smoke_playbook.py", fc.names())
        self.assertIn("launch.py:down", fc.names())

    def test_copies_that_do_not_come_up_fail_and_are_closed(self):
        rc, fc, text = self.go(**{"launch.py:up": (3, "player 2: the game did not come up within 240 s")})
        self.assertEqual(rc, 1)
        self.assertTrue(any(l.startswith("FAIL up") for l in text.splitlines()), text)
        self.assertNotIn("smoke_playbook.py", fc.names(), "no smoke playbook against copies that never came up")
        self.assertEqual(fc.names()[-2:], ["launch.py:down", "gameslot.py:release"])

    def test_a_failing_step_fails_the_run_and_still_closes_everything(self):
        rc, fc, text = self.go(**{"smoke_playbook.py": (1, "PASS windows\nFAIL pads: copy 1 saw pad 0")})
        self.assertEqual(rc, 1)
        self.assertEqual(fc.names()[-2:], ["launch.py:down", "gameslot.py:release"])

    def test_a_copy_the_playbook_cannot_reach_is_env(self):
        rc, fc, text = self.go(**{"smoke_playbook.py": (3, "cannot reach every copy: pipe nvcoop-41")})
        self.assertEqual(rc, 2)

    def test_busy_slot_starts_nothing(self):
        rc, fc, text = self.go(**{"gameslot.py:acquire": (3, "BUSY held by L5")})
        self.assertEqual(rc, 2)
        self.assertEqual(fc.names(), ["gameslot.py:acquire"])

    def test_an_exception_in_the_playbook_still_closes_everything(self):
        class Boom(FakeCall):
            def __call__(self, argv, timeout):
                if argv[0].endswith("smoke_playbook.py"):
                    self.calls.append(list(argv))
                    raise RuntimeError("boom")
                return super().__call__(argv, timeout)
        fc = Boom()
        with self.assertRaises(RuntimeError):
            SS.run(ARGS, call=fc, out=lambda s: None)
        self.assertEqual(fc.names()[-2:], ["launch.py:down", "gameslot.py:release"])


# ------------------------------------------------------------------ public tree build
class PublicTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / "w"
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **kw):
        d = dict(work=str(self.work), nvse="", skip_build=False, skip_tests=False, keep=True)
        d.update(kw)
        return SimpleNamespace(**d)

    def call(self, export=(0, "kept 5\nSCAN CLEAN (paths)"), tests=(0, "ALL OK"), build=(0, "BUILD-TIME public 1")):
        def f(argv, cwd, timeout):
            joined = " ".join(argv)
            if "export_public" in joined:
                self.log.append("export")
                (self.work / "export").mkdir(parents=True, exist_ok=True)
                return export
            if "test_all" in joined:
                self.log.append("tests")
                return tests
            if "plugin/all" in joined:
                self.log.append("build")
                return build
            return 0, ""
        return f

    def nvse(self):
        d = Path(self.tmp.name) / "nvse"
        (d / "nvse").mkdir(parents=True, exist_ok=True)
        return str(d)

    def stages(self):
        return list(self.log)

    def go(self, call, rescan=lambda p: [], dll=lambda p: [], **kw):
        lines, out = collect()
        a = self.args(**kw)
        # the build stage looks for nvcoop.dll below the export: plant one
        rc = PT.run(a, call=call, out=out, rescan=rescan, dll_problems=dll)
        return rc, "\n".join(lines)

    def test_happy_path_exports_scans_tests_and_builds(self):
        def call(argv, cwd, timeout):
            if "plugin/all" in " ".join(argv):
                (self.work / "export" / "out").mkdir(parents=True, exist_ok=True)
                (self.work / "export" / "out" / "nvcoop.dll").write_bytes(b"MZ")
            return self.call()(argv, cwd, timeout)
        rc, text = self.go(call, nvse=self.nvse())
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.stages(), ["export", "tests", "build"])
        self.assertIn("checks passed", text)

    def test_export_without_scan_clean_stops_everything(self):
        rc, text = self.go(self.call(export=(0, "kept 5")), nvse=self.nvse())
        self.assertEqual(rc, 1)
        self.assertIn("FAIL export: its own scan says SCAN CLEAN", text)
        self.assertEqual(self.stages(), ["export"], "no tests or build on an export that did not scan clean")

    def test_a_failed_export_stops(self):
        rc, text = self.go(self.call(export=(1, "SCAN FAILED")), nvse=self.nvse())
        self.assertEqual((rc, self.stages()), (1, ["export"]))

    def test_rescan_hit_fails_before_tests(self):
        rc, text = self.go(self.call(), rescan=lambda p: ["x.py:1: owner handle: ..."], nvse=self.nvse())
        self.assertEqual(rc, 1)
        self.assertIn("FAIL rescan", text)
        self.assertEqual(self.stages(), ["export"])

    def test_missing_nvse_is_blocked_not_failed(self):
        rc, text = self.go(self.call(), nvse=str(Path(self.tmp.name) / "no-nvse"))
        self.assertEqual(rc, 3)
        self.assertIn("BLOCKED", text)

    def test_failing_tests_or_build_fail(self):
        rc, text = self.go(self.call(tests=(1, "FAIL suite X")), nvse=self.nvse(), skip_build=True)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL tests", text)
        rc, text = self.go(self.call(build=(1, "error C2065")), nvse=self.nvse(), skip_tests=True)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL build", text)

    def test_work_folder_inside_the_checkout_is_refused(self):
        lines, out = collect()
        rc = PT.run(self.args(work=str(REPO / "out" / "x-test-public")), call=self.call(), out=out)
        self.assertEqual(rc, 3)
        self.assertFalse((REPO / "out" / "x-test-public" / "export").exists())

    def test_the_rescan_finds_what_must_never_be_published(self):
        root = Path(self.tmp.name) / "tree"
        (root / "tools").mkdir(parents=True)
        (root / "tools" / "ok.py").write_text("print('fine')\n", encoding="utf-8")
        self.assertEqual(PT.scan_hits(root), [])
        user_path = "C:" + chr(92) + "Us" + "ers" + chr(92) + OWNER
        (root / "tools" / "bad.py").write_text(f"p = r'{user_path}'\n", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (root / "out").mkdir()
        (root / "out" / "log.txt").write_text("x\n", encoding="utf-8")
        (root / "plugin").mkdir()
        (root / "plugin" / "nvcoop.dll").write_bytes(b"MZ")
        hits = "\n".join(PT.scan_hits(root))
        for need in ("bad.py", ".git/", "out/", "nvcoop.dll"):
            self.assertIn(need, hits)


# ------------------------------------------------------------------ launcher loopback
class Loopback(unittest.TestCase):
    def run_driver(self, chaos=""):
        lines, out = collect()
        rc = LB.run(SimpleNamespace(chaos=chaos, seed=7), out=out)
        return rc, "\n".join(lines)

    def test_clean_launcher_passes_every_check(self):
        rc, text = self.run_driver()
        self.assertEqual(rc, 0, text)
        self.assertGreaterEqual(text.count("PASS "), 18)

    def test_clean_launcher_passes_through_the_seeded_relay(self):
        rc, text = self.run_driver("bad_wifi")
        self.assertEqual(rc, 0, text)
        self.assertIn("PASS chaos: the probe really went through the relay", text)
        self.assertIn("PASS chaos: with the way to the host cut", text)

    def test_twin_listener_answers_anything(self):
        with fnv_mut.use("answer_anything"):
            rc, text = self.run_driver()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL listener: it answers nothing but the probe message", text)

    def test_twin_probe_accepts_any_reply(self):
        with fnv_mut.use("accept_any_reply"):
            rc, text = self.run_driver()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL listener: an answer that is not the agreed one does not count", text)

    def test_twin_host_skips_the_port_check(self):
        with fnv_mut.use("skip_port_check"):
            rc, text = self.run_driver()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL port: Host on a busy port refuses", text)

    def test_twin_host_page_says_every_port_is_free(self):
        with fnv_mut.use("port_always_ok"):
            rc, text = self.run_driver()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL port: a port another program holds is reported busy", text)


# ------------------------------------------------------------------ release .cmd scripts
@unittest.skipUnless(os.name == "nt", "the release scripts are cmd files")
class ReleaseCmd(unittest.TestCase):
    def run_driver(self, mutate=None):
        lines, out = collect()
        rc = RC.run(SimpleNamespace(keep=False), out=out, mutate=mutate)
        return rc, "\n".join(lines)

    def test_clean_release_passes_every_scenario(self):
        rc, text = self.run_driver()
        self.assertEqual(rc, 0, text)
        self.assertGreaterEqual(text.count("PASS "), 24)

    def test_the_scripts_run_on_the_synthetic_game_only(self):
        # the isolation shim is what keeps Install.cmd off this PC's real install: without a game given, no game is found
        work = Path(tempfile.mkdtemp(prefix="fnv-cmd-test-"))
        try:
            rel = RC.package(work / "pkg")
            rig = RC.new_rig(work, "solo", rel)
            code, text = rig.run("Check.cmd")
            self.assertIn(str(rig.game), text)
            e = rig.env()
            e.pop("FNV_GAME_DIR")
            rig.env = lambda: e
            code, text = rig.run("Check.cmd")
            self.assertIn("Could not find", text, "with the shim and no FNV_GAME_DIR the scripts must find no game at all")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_twin_install_script_does_nothing(self):
        rc, text = self.run_driver(lambda rel: (rel / "Install.cmd").write_bytes(b"@echo off\r\nexit /b\r\n"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL clean: Install.cmd copied every payload file", text)

    def test_twin_uninstall_script_does_nothing(self):
        rc, text = self.run_driver(lambda rel: (rel / "Uninstall.cmd").write_bytes(b"@echo off\r\nexit /b\r\n"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL clean: Uninstall.cmd removed the FNVCoOp folder", text)

    def test_twin_check_script_installs(self):
        rc, text = self.run_driver(lambda rel: (rel / "Check.cmd").write_bytes((rel / "Install.cmd").read_bytes()))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL clean: Check.cmd changed nothing", text)

    def test_twin_payload_corrupted_after_packaging(self):
        def corrupt(rel):
            p = rel / "payload" / "nvcoop.dll"
            p.write_bytes(p.read_bytes() + b"\0")
        rc, text = self.run_driver(corrupt)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL clean: Install.cmd copied every payload file", text)


# ------------------------------------------------------------------ load_scenario (puts a booted copy next to the NPC)
class FakeNv:
    """A bridge whose game is still booting for the first `boot_pings` pings (menu not ready), then ready."""

    def __init__(self, boot_pings=3, log=None):
        self.n, self.boot, self.log = 0, boot_pings, log if log is not None else []

    def call(self, method, params=None):
        assert method == "ping"
        self.n += 1
        return {"pong": True, "menuReady": self.n > self.boot, "mainLoopAgeMs": 5 if self.n > self.boot else -1}


class LoadScenario(unittest.TestCase):
    def go(self, nv, start=None, wait_s=60.0):
        lines, t = [], [0.0]
        seen = []

        def sleep(s):
            t[0] += s

        def default_start(bridges):
            seen.append(bridges[0].n > bridges[0].boot)      # was the game ready when the scenario jump began?
            return {"scenario": "goodsprings_enemies", "mode": "scenario"}
        rc = LS.run([nv], start or default_start, "enemy_fight", wait_s, out=lines.append, sleep=sleep, now=lambda: t[0])
        return rc, chr(10).join(lines), seen

    def test_the_jump_waits_until_the_game_is_ready(self):
        rc, text, seen = self.go(FakeNv(boot_pings=5))
        self.assertEqual((rc, seen), (0, [True]), text)
        self.assertIn("PASS load scenario", text)

    def test_a_game_that_never_gets_ready_is_an_env_problem_and_nothing_is_loaded(self):
        rc, text, seen = self.go(FakeNv(boot_pings=10 ** 6), wait_s=20.0)
        self.assertEqual((rc, seen), (2, []))
        self.assertIn("cannot reach the game instances", text)

    def test_a_failing_jump_fails(self):
        def boom(bridges):
            raise RuntimeError("scenario goodsprings_enemies setup failed")
        rc, text, _ = self.go(FakeNv(boot_pings=0), start=boom)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL load scenario", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
