"""Game-free tests of tools/runner/reload_proof.py: a fake game that behaves like a correct hot reload must pass, and every
mutation twin (a fake game that breaks one reload rule) must be caught by the matching check. Prints ALL OK via unittest
summary lines picked up by tools/test_all.py ("Ran N tests")."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reload_proof as rp  # noqa: E402


class FakeProbe:
    def __init__(self, mut):
        self.mut, self.n = mut, 0

    def threads(self):
        return 40 + (self.n if self.mut == "process_threads" else 0)

    def handles(self):
        return 500 + (self.n * 5 if self.mut == "handles" else 0)

    def working_set_mb(self):
        return 1000.0 + (self.n * 40 if self.mut == "memory" else 0)


class FakeGame:
    """Just enough of the bridge: hooks.status, reload.state, dev.status, coop.status, dev.reload, capture.*, exec."""

    def __init__(self, mut=None, probe=None):
        self.mut, self.gen, self.probe = mut, 1, probe
        self.live_hooks, self.threads, self.token = 7, 2, "1234"
        self.restored = 4
        self.coop_running = True
        self.captured = False

    def exec(self, cmd):
        self.captured = self.mut != "capture_dead"
        return {"ok": True}

    def call(self, method, params=None, timeout_s=0):
        if method == "dev.reload":
            if self.mut == "reload_error":
                raise RuntimeError("core vetoed the unload (see nvcoop.log); old core stays loaded")
            if self.probe:
                self.probe.n += 1
            if self.mut != "generation_stuck":
                self.gen += 1
            if self.mut == "leak_hook":
                self.live_hooks += 1
            if self.mut == "lose_hook":
                self.live_hooks -= 1
            if self.mut == "thread_leak":
                self.threads += 1
            if self.mut == "session_lost":
                self.coop_running = False
            if self.mut == "token_changes":
                self.token = str(int(self.token) + 1)
            if self.mut == "no_restore":
                self.restored = 0
            return {"generation": self.gen, "msUnload": 5.0, "msLoad": 9.0}
        if method == "hooks.status":
            failed = 1 if self.mut == "hook_failed" else 0
            return {"installed": 6, "failed": failed, "criticalOk": self.mut != "not_critical",
                    "hooks": [{"name": "SetStage", "status": "Ok"}]}
        if method == "reload.state":
            parts = [{"name": "l5_hooks", "version": 2 if (self.mut == "version_changes" and self.gen > 1) else 1},
                     {"name": "l10_coop", "version": 1}]
            return {"participants": parts, "liveHooks": self.live_hooks, "liveThreads": self.threads, "chainedHooks": 0,
                    "restored": self.restored, "fresh": 0, "failures": ["restore l10_coop: rejected"] if self.mut == "failure_reported" else [],
                    "leaks": []}
        if method == "dev.status":
            return {"loaded": True, "generation": self.gen, "last": {"ok": True, "rolledBack": self.mut == "rolled_back", "coreDead": False}}
        if method == "coop.status":
            return {"running": self.coop_running, "role": "host", "token": self.token}
        if method == "capture.begin":
            self.captured = False
            return {"capturing": True}
        if method == "capture.end":
            if not self.captured:
                return {"txns": []}
            return {"txns": [{"effects": [{"type": "SetStage", "quest": "falloutnv.esm:10A214", "stage": 10}]}]}
        raise AssertionError(f"unexpected method {method}")


def run(mut=None, cycles=3, capture=True):
    probe = FakeProbe(mut)
    game = FakeGame(mut, probe)
    return rp.run_proof(game, cycles, probe, capture, 150, log=lambda s: None, sleep=lambda s: None)


def failed(rep):
    return {r["check"] for r in rep["results"] if not r["ok"]}


class ReloadProofTests(unittest.TestCase):
    def test_correct_reload_passes_every_check(self):
        rep = run()
        self.assertTrue(rep["ok"], rep["results"])
        self.assertEqual(rep["cycles_run"], 3)
        names = {r["check"] for r in rep["results"]}
        self.assertTrue({"reload", "hooks", "threads", "participants", "session", "capture", "handles", "memory"} <= names, names)

    def test_missing_reload_registry_is_a_setup_error_not_a_pass(self):
        class Bare(FakeGame):
            def call(self, method, params=None, timeout_s=0):
                return {} if method == "reload.state" else super().call(method, params, timeout_s)
        with self.assertRaises(RuntimeError):
            rp.run_proof(Bare(), 1, None, False, 150, log=lambda s: None, sleep=lambda s: None)

    # ---- mutation twins: each broken reload must be caught by the right check ----
    def test_twin_reload_refused(self):
        self.assertIn("reload", failed(run("reload_error")))

    def test_twin_generation_does_not_advance(self):
        self.assertIn("reload", failed(run("generation_stuck")))

    def test_twin_rolled_back_is_not_success(self):
        self.assertIn("reload", failed(run("rolled_back")))

    def test_twin_leaked_hook(self):
        self.assertIn("hooks", failed(run("leak_hook")))

    def test_twin_lost_hook(self):
        self.assertIn("hooks", failed(run("lose_hook")))

    def test_twin_hook_install_failed(self):
        self.assertIn("hooks", failed(run("hook_failed")))

    def test_twin_critical_hook_missing(self):
        self.assertIn("hooks", failed(run("not_critical")))

    def test_twin_leaked_thread(self):
        self.assertIn("threads", failed(run("thread_leak")))

    def test_twin_process_thread_growth(self):
        self.assertIn("threads", failed(run("process_threads")))

    def test_twin_session_lost(self):
        self.assertIn("session", failed(run("session_lost")))

    def test_twin_client_token_changes(self):
        self.assertIn("session", failed(run("token_changes")))

    def test_twin_state_not_restored(self):
        self.assertIn("participants", failed(run("no_restore")))

    def test_twin_participant_version_changes(self):
        self.assertIn("participants", failed(run("version_changes")))

    def test_twin_failure_reported_by_core(self):
        self.assertIn("participants", failed(run("failure_reported")))

    def test_twin_hooks_installed_but_dead(self):
        self.assertIn("capture", failed(run("capture_dead")))

    def test_twin_handle_creep(self):
        self.assertIn("handles", failed(run("handles", cycles=5)))

    def test_twin_memory_growth(self):
        self.assertIn("memory", failed(run("memory", cycles=5)))


if __name__ == "__main__":
    unittest.main()
