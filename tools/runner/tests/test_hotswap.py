"""Game-free tests of tools/runner/hotswap.py (run.py swap): fake running copies with a real core file, a real cache copy and the
loader's bridge methods (dev.status, dev.reload, reload.state, coop.status).

Every behaviour has a check_* function. The real code must pass it. Each twin runs the same check against a broken copy of the code
(a patched function in hotswap) or a broken fake loader (a mutant name), and the check must then fail with an AssertionError.
A twin whose check still passes is a test failure: it means the check cannot see that behaviour go away.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import hotswap  # noqa: E402
import run  # noqa: E402
from nvbridge import ConnectionLost, GameCrashed, RpcError  # noqa: E402


def core_bytes(tag: str, export: bool = True) -> bytes:
    return b"MZ" + b"\x00" * 600 + (b"NvCore_Init\x00" if export else b"Other_Init\x00") + tag.encode() + b"\x00" * 600


class Clock:
    """Fake time: sleep() moves it, so waits cost nothing."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeCopy:
    """One running game copy with the split loader. connect() hands out Bridge objects that talk to it."""

    def __init__(self, root: Path, i: int, mut: Optional[str] = None, coop: bool = True):
        self.i, self.mut = i, mut
        self.dir = root / str(i)
        self.plugins = self.dir / "Data" / "NVSE" / "Plugins"
        (self.plugins / "nvcoop_cache").mkdir(parents=True)
        self.core = self.plugins / "nvcoop_mod.dll"
        self.core.write_bytes(core_bytes("old"))
        self.gen = 1
        self.cache = self._new_cache()
        self.build_id = hotswap.build_id_of(self.cache)
        self.coop = coop
        self.coop_running = coop
        self.coop_polls_left = 0
        self.reload_calls: List[Dict[str, Any]] = []
        self.last = {"ok": True, "rolledBack": False, "coreDead": False, "error": ""}
        self.connect_failures = 0
        self.loaded = True

    def _new_cache(self) -> Path:
        self.seq = getattr(self, "seq", 0) + 1
        n = self.seq
        p = self.plugins / "nvcoop_cache" / f"nvcoop_mod.{self.i}.{n}.dll"
        p.write_bytes(self.core.read_bytes())
        return p

    def status(self) -> Dict[str, Any]:
        st = {"loaded": self.loaded, "generation": self.gen, "reloadCount": self.gen - 1, "corePath": str(self.core),
              "cachePath": str(self.cache), "configDir": str(self.plugins) + "\\", "last": dict(self.last)}
        if self.mut != "old_loader":
            st["buildId"] = self.build_id
            st["hotReload"] = True
        if self.mut == "foreign_core":
            other = self.dir.parent / "elsewhere"
            other.mkdir(exist_ok=True)
            (other / "nvcoop_mod.dll").write_bytes(core_bytes("foreign"))
            st["corePath"] = str(other / "nvcoop_mod.dll")
        if self.mut == "no_core_path":
            del st["corePath"]
        return st

    def reload(self, params):
        self.reload_calls.append(dict(params))
        m = self.mut
        if m == "veto":
            raise RpcError(-32000, "core vetoed the unload (see nvcoop.log); old core stays loaded", "dev.reload")
        if m == "lease":
            raise RpcError(-32011, "instance is leased by another client", "dev.reload")
        if m == "noop_reload":
            return {"generation": self.gen}
        if m == "rollback_quiet":   # answers like a success while the old core runs again
            self.last = {"ok": False, "rolledBack": True, "coreDead": False, "error": "core init() returned failure"}
            return {"generation": self.gen + 1}
        if m == "rollback":
            self.last = {"ok": False, "rolledBack": True, "coreDead": False, "error": "core init() returned failure"}
            raise RpcError(-32000, "core init() returned failure (rolled back to the previous core)", "dev.reload")
        if m == "dead":
            self.last = {"ok": False, "rolledBack": False, "coreDead": True, "error": "rollback also failed"}
            self.loaded = False
            raise RpcError(-32000, "rollback also failed", "dev.reload")
        if m == "crash":
            raise GameCrashed(4242, "pipe closed")
        # a good reload: map the file that is on disk now
        old = self.cache
        self.cache = self._new_cache()
        old.unlink()
        self.gen += 2 if m == "watcher_too" else (0 if m == "gen_stuck" else 1)
        if m != "stale_id":
            self.build_id = hotswap.build_id_of(self.cache)
        if m == "session_lost":
            self.coop_running = False
        if m == "coop_slow":
            self.coop_running, self.coop_polls_left = False, 3
        if m == "drop":
            self.connect_failures = 2
            raise ConnectionLost("no reply within 90s")
        return {"generation": self.gen, "buildId": self.build_id, "msUnload": 4.0, "msLoad": 9.0}

    def call(self, method, params=None, timeout_s=0):
        if self.mut == "monolith" and method.startswith("dev."):
            raise RpcError(METHOD_NOT_FOUND, f"unknown method: {method}", method)
        if method == "dev.status":
            return self.status()
        if method == "dev.reload":
            return self.reload(params or {})
        if method == "reload.state":
            return {"participants": [], "failures": [], "leaks": ["thread 5"] if self.mut == "leaks" else [], "chainedHooks": 0}
        if method == "coop.status":
            if not self.coop:
                raise RpcError(METHOD_NOT_FOUND, "unknown method: coop.status", method)
            if self.coop_polls_left:
                self.coop_polls_left -= 1
                if not self.coop_polls_left:
                    self.coop_running = True
            return {"running": self.coop_running, "role": "host"}
        raise AssertionError(f"unexpected method {method}")


METHOD_NOT_FOUND = -32601


class Bridge:
    def __init__(self, game: FakeCopy):
        self.game, self.closed = game, False

    def call(self, method, params=None, timeout_s=0):
        return self.game.call(method, params, timeout_s)

    def close(self):
        self.closed = True


class World:
    def __init__(self, tmp: Path, muts: Optional[Dict[int, str]] = None, coop: bool = True, n: int = 2):
        muts = muts or {}
        self.root = tmp / "inst"
        self.games = {i: FakeCopy(self.root, i, muts.get(i), coop) for i in range(n)}
        self.dll = tmp / "out" / "nvcoop_mod.dll"
        self.dll.parent.mkdir(parents=True)
        self.dll.write_bytes(core_bytes("new"))
        self.new_id = hotswap.build_id_of(self.dll)
        self.connects: List[int] = []
        self.clock = Clock()
        self.logs: List[str] = []

    def connect(self, i, wait_s):
        self.connects.append(i)
        g = self.games.get(i)
        if g is None:
            raise ConnectionLost(f"cannot open pipe {i}")
        if g.connect_failures:
            g.connect_failures -= 1
            raise ConnectionLost("pipe busy")
        return Bridge(g)

    def swap(self, ids=(0, 1), **kw):
        kw.setdefault("sleep", self.clock.sleep)
        return hotswap.run_swap(list(ids), kw.pop("dll", self.dll), connect=self.connect, inst_dir_of=lambda i: self.root / str(i),
                                now=self.clock, log=self.logs.append, **kw)


@contextlib.contextmanager
def world(muts=None, **kw):
    with tempfile.TemporaryDirectory() as d:
        yield World(Path(d), muts, **kw)


def txt(p: Path) -> Optional[str]:
    return p.read_text() if p.is_file() else None


def leftovers(w: World) -> List[str]:
    return [p.name for g in w.games.values() for p in g.plugins.glob("*" + hotswap.TMP_SUFFIX)]


# ------------------------------------------------------------------------------ the checks (each one is also run against twins)
def check_swaps_every_copy(t: unittest.TestCase, w_muts=None):
    with world(w_muts) as w:
        code, rep = w.swap()
        t.assertEqual(code, 0, rep)
        t.assertTrue(rep["ok"])
        t.assertEqual(rep["buildId"], w.new_id)
        for i, g in w.games.items():
            t.assertEqual(g.core.read_bytes(), w.dll.read_bytes(), f"copy {i} core file holds the new build")
            t.assertEqual(g.build_id, w.new_id, f"copy {i} runs the new build")
            t.assertEqual(g.gen, 2)
            t.assertEqual(len(g.reload_calls), 1)
            t.assertEqual(g.reload_calls[0]["reason"], "hotswap")
            t.assertGreaterEqual(g.reload_calls[0]["timeoutMs"], 60000, "the bridge's own 30 s wait is lifted")
        rows = {r["instance"]: r for r in rep["instances"]}
        t.assertEqual({r["status"] for r in rows.values()}, {"swapped"})
        t.assertNotEqual(rows[0]["buildBefore"], rows[0]["buildAfter"])
        t.assertEqual(rows[0]["buildAfter"], w.new_id)
        t.assertEqual(rows[0]["verifiedBy"], "loader buildId")
        t.assertEqual(leftovers(w), [], "no temporary file is left beside the core")
        t.assertTrue(all(g.coop_running for g in w.games.values()))


def check_unchanged_build_is_not_reloaded(t):
    with world() as w:
        for g in w.games.values():
            g.core.write_bytes(w.dll.read_bytes())
            g.cache = g._new_cache()
            g.build_id = hotswap.build_id_of(g.cache)
        code, rep = w.swap()
        t.assertEqual(code, 0, rep)
        t.assertEqual([r["status"] for r in rep["instances"]], ["unchanged", "unchanged"])
        t.assertEqual([len(g.reload_calls) for g in w.games.values()], [0, 0])
        code, rep = w.swap(force=True)
        t.assertEqual(code, 0, rep)
        t.assertEqual([len(g.reload_calls) for g in w.games.values()], [1, 1], "--force reloads anyway")
        t.assertEqual([g.gen for g in w.games.values()], [2, 2])


def check_data_files_follow_the_core(t):
    with world() as w:
        out = w.dll.parent
        (out / "nvcoop_rules.json").write_text('{"v":1}')
        (out / "nvcoop_volatile_keys.txt").write_text("keys")
        (out / "nvcoop.ini").write_text("[x]")
        (out / "notes.txt").write_text("not ours")
        w.games[1].plugins.joinpath("nvcoop_rules.json").write_text('{"v":0}')
        w.games[1].plugins.joinpath("nvcoop_rules.json").chmod(0o444)   # read-only, like a hand-protected ini
        code, rep = w.swap()
        t.assertEqual(code, 0, rep)
        for g in w.games.values():
            t.assertEqual(txt(g.plugins / "nvcoop_rules.json"), '{"v":1}')
            t.assertEqual(txt(g.plugins / "nvcoop_volatile_keys.txt"), "keys")
            t.assertFalse((g.plugins / "nvcoop.ini").exists(), "the per-instance ini is never copied")
            t.assertFalse((g.plugins / "notes.txt").exists())
        t.assertEqual(sorted(rep["instances"][0]["dataFiles"]), ["nvcoop_rules.json", "nvcoop_volatile_keys.txt"])
        # same DLL, a data file edited: the copy still reloads so that the loader mirrors the file into its cache
        (out / "nvcoop_rules.json").write_text('{"v":2}')
        code, rep = w.swap()
        t.assertEqual(code, 0, rep)
        t.assertEqual([r["status"] for r in rep["instances"]], ["swapped", "swapped"])
        t.assertEqual([len(g.reload_calls) for g in w.games.values()], [2, 2])
        t.assertEqual(txt(w.games[0].plugins / "nvcoop_rules.json"), '{"v":2}')
        # nothing differs now: nothing to do; and --no-data ignores a difference
        code, rep = w.swap()
        t.assertEqual([r["status"] for r in rep["instances"]], ["unchanged", "unchanged"])
        (out / "nvcoop_rules.json").write_text('{"v":3}')
        code, rep = w.swap(sync_data=False)
        t.assertEqual([r["status"] for r in rep["instances"]], ["unchanged", "unchanged"])
        t.assertEqual(txt(w.games[0].plugins / "nvcoop_rules.json"), '{"v":2}')


def check_dry_run_changes_nothing(t):
    with world() as w:
        before = {i: g.core.read_bytes() for i, g in w.games.items()}
        code, rep = w.swap(dry_run=True)
        t.assertEqual(code, 0, rep)
        t.assertEqual([r["status"] for r in rep["instances"]], ["would swap", "would swap"])
        t.assertEqual({i: g.core.read_bytes() for i, g in w.games.items()}, before)
        t.assertEqual([len(g.reload_calls) for g in w.games.values()], [0, 0])


def check_monolith_is_refused_before_anything_changes(t):
    with world({1: "monolith"}) as w:
        before = {i: g.core.read_bytes() for i, g in w.games.items()}
        code, rep = w.swap()
        t.assertEqual(code, 2, rep)
        t.assertIn("instance 1", rep["refused"])
        t.assertIn("monolithic", rep["refused"])
        t.assertEqual({i: g.core.read_bytes() for i, g in w.games.items()}, before, "copy 0 was not touched either")
        t.assertEqual([len(g.reload_calls) for g in w.games.values()], [0, 0])
        t.assertEqual(leftovers(w), [])


def check_no_pipe_is_refused(t):
    with world() as w:
        code, rep = w.swap(ids=(0, 5))
        t.assertEqual(code, 2, rep)
        t.assertIn("instance 5", rep["refused"])
        t.assertEqual(w.games[0].core.read_bytes(), core_bytes("old"))


def check_non_core_dll_is_refused(t):
    with world() as w:
        for blob, why in ((core_bytes("loader", export=False), "NvCore_Init"), (b"MZ" + b"\x00" * 10, "not a DLL"), (b"text file" * 200, "not a DLL")):
            w.dll.write_bytes(blob)
            code, rep = w.swap()
            t.assertEqual(code, 2, rep)
            t.assertIn(why, rep["refused"])
        t.assertEqual(w.connects, [], "refused before any pipe was opened")
        code, rep = w.swap(dll=w.dll.with_name("missing.dll"))
        t.assertEqual(code, 2, rep)


def check_core_outside_the_instance_is_refused(t):
    with world({0: "foreign_core"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 2, rep)
        t.assertIn("outside its instance folder", rep["refused"])
        t.assertEqual([len(g.reload_calls) for g in w.games.values()], [0, 0])
    with world({0: "no_core_path"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 2, rep)
    with world() as w:
        code, rep = w.swap(dll=w.games[0].core)
        t.assertEqual(code, 2, rep)
        t.assertIn("own core file", rep["refused"])


def check_old_loader_is_verified_by_its_cache_copy(t):
    with world({0: "old_loader", 1: "old_loader"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 0, rep)
        t.assertEqual({r["verifiedBy"] for r in rep["instances"]}, {"cache copy crc"})
        t.assertEqual(rep["instances"][0]["buildAfter"], w.new_id)


def check_pipe_is_awaited_after_a_dropped_call(t):
    with world({0: "drop"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 0, rep)
        t.assertEqual(w.games[0].gen, 2)
        t.assertEqual(w.games[0].build_id, w.new_id)
        t.assertGreaterEqual(w.connects.count(0), 2, "the pipe was opened again")
        t.assertTrue(any("waiting for it" in line for line in w.logs))
    with world({0: "drop"}) as w:
        code, rep = w.swap(wait_s=0.2)   # no time to try again: a pipe that does not come back in time is a failure
        t.assertEqual(code, 1, rep)
        t.assertIn("did not answer again", rep["instances"][0]["detail"])


def check_crash_is_a_failure(t):
    with world({0: "crash"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 1, rep)
        t.assertIn("died", rep["instances"][0]["detail"])


def check_failure_stops_the_swap(t):
    with world({0: "veto"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 1, rep)
        rows = {r["instance"]: r for r in rep["instances"]}
        t.assertEqual(rows[0]["status"], "failed")
        t.assertIn("vetoed", rows[0]["detail"])
        t.assertEqual(rows[1]["status"], "skipped")
        t.assertEqual(len(w.games[1].reload_calls), 0)
        t.assertEqual(w.games[1].core.read_bytes(), core_bytes("old"), "the skipped copy keeps its old core")
        t.assertEqual(w.games[0].gen, 1)


def check_lease_hint(t):
    with world({0: "lease"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 1, rep)
        t.assertIn("lease", rep["instances"][0]["detail"])


def check_reload_that_changed_nothing_fails(t, mut):
    with world({0: mut}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 1, f"{mut} must fail: {rep}")
        t.assertEqual(rep["instances"][0]["status"], "failed")
        t.assertEqual(rep["instances"][1]["status"], "skipped")


def check_leaks_fail(t):
    with world({0: "leaks"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 1, rep)
        t.assertIn("leaks", rep["instances"][0]["detail"])


def check_watcher_double_reload_is_fine(t):
    with world({0: "watcher_too"}) as w:
        code, rep = w.swap()
        t.assertEqual(code, 0, rep)
        t.assertEqual(w.games[0].gen, 3)


def check_lost_session_fails_and_slow_one_waits(t):
    with world({0: "session_lost"}) as w:
        code, rep = w.swap(settle_s=5)
        t.assertEqual(code, 1, rep)
        t.assertIn("co-op session", rep["instances"][0]["detail"])
        t.assertFalse(rep["instances"][0]["coopBack"])
    with world({0: "coop_slow"}) as w:
        code, rep = w.swap(settle_s=20)
        t.assertEqual(code, 0, rep)
        t.assertTrue(rep["instances"][0]["coopBack"])
    with world({0: "session_lost"}, coop=False) as w:
        code, rep = w.swap()
        t.assertEqual(code, 0, "a copy without co-op has no session to wait for")


def check_locked_core_file_is_retried(t):
    with world() as w:
        calls = {"n": 0}
        real = os.replace

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError("sharing violation")
            return real(a, b)
        with mock.patch.object(hotswap.os, "replace", flaky):
            code, rep = w.swap(ids=(0,))
        t.assertEqual(code, 0, rep)
        t.assertEqual(calls["n"], 4)
    with world() as w:
        def locked(a, b):
            raise PermissionError("locked")
        with mock.patch.object(hotswap.os, "replace", locked):
            code, rep = w.swap(ids=(0,))
        t.assertEqual(code, 1, rep)
        t.assertIn("cannot replace", rep["instances"][0]["detail"])
        t.assertEqual(leftovers(w), [], "the temporary file is removed after a failed rename")
        t.assertEqual(w.games[0].core.read_bytes(), core_bytes("old"))
        t.assertEqual(len(w.games[0].reload_calls), 0, "no reload when the file could not be put in place")


# ------------------------------------------------------------------------------ twins: broken tool code
def _always_swap(before_id, expected_id, force):
    return True


def _verify_trusts_the_reply(before, after, expected_id, before_id):
    return True, "reply", "dev.reload answered"


def _install_does_nothing(src, dest, expected_id, sleep=None, replace=None):
    return None


def _reconnect_gives_up(connect, instance, wait_s, sleep, now):
    raise hotswap.SwapFailed("gave up")


def _wait_coop_always_true(nv, settle_s, sleep=None, now=None):
    return True


def _path_unchecked(st, inst_dir):
    return Path(st["corePath"])


def _any_dll_is_fine(path):
    return ""


def _no_leak_check(nv):
    return ""


class HotswapTests(unittest.TestCase):
    def assertCaught(self, check, patches, *args):
        """The check passes on the real code (the tests above) and must raise AssertionError with the broken code."""
        with contextlib.ExitStack() as st:
            for target, repl in patches:
                st.enter_context(mock.patch.object(hotswap, target, repl))
            try:
                check(self, *args)
            except AssertionError:
                return
        self.fail(f"twin {[p[0] for p in patches]} was not caught by {check.__name__}")

    # ---- the real code ----
    def test_build_id_matches_the_loader_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.bin"
            p.write_bytes(b"123456789")
            self.assertEqual(hotswap.build_id_of(p), "cbf43926-9")   # the standard CRC-32 check value, then the size
            p.write_bytes(b"")
            self.assertEqual(hotswap.build_id_of(p), "00000000-0")
            p.write_bytes(os.urandom(3 << 20))   # more than one read chunk
            import zlib
            self.assertEqual(hotswap.build_id_of(p), f"{zlib.crc32(p.read_bytes()) & 0xFFFFFFFF:08x}-{3 << 20}")

    def test_swaps_every_copy(self):
        check_swaps_every_copy(self)

    def test_unchanged_build_is_not_reloaded(self):
        check_unchanged_build_is_not_reloaded(self)

    def test_data_files_follow_the_core(self):
        check_data_files_follow_the_core(self)

    def test_dry_run_changes_nothing(self):
        check_dry_run_changes_nothing(self)

    def test_monolith_is_refused_before_anything_changes(self):
        check_monolith_is_refused_before_anything_changes(self)

    def test_no_pipe_is_refused(self):
        check_no_pipe_is_refused(self)

    def test_non_core_dll_is_refused(self):
        check_non_core_dll_is_refused(self)

    def test_core_outside_the_instance_is_refused(self):
        check_core_outside_the_instance_is_refused(self)

    def test_old_loader_is_verified_by_its_cache_copy(self):
        check_old_loader_is_verified_by_its_cache_copy(self)

    def test_pipe_is_awaited_after_a_dropped_call(self):
        check_pipe_is_awaited_after_a_dropped_call(self)

    def test_crash_is_a_failure(self):
        check_crash_is_a_failure(self)

    def test_failure_stops_the_swap(self):
        check_failure_stops_the_swap(self)

    def test_lease_hint(self):
        check_lease_hint(self)

    def test_broken_reloads_fail(self):
        for mut in ("rollback", "rollback_quiet", "dead", "stale_id", "gen_stuck", "noop_reload"):
            check_reload_that_changed_nothing_fails(self, mut)

    def test_leaks_fail(self):
        check_leaks_fail(self)

    def test_watcher_double_reload_is_fine(self):
        check_watcher_double_reload_is_fine(self)

    def test_lost_session_fails_and_slow_one_waits(self):
        check_lost_session_fails_and_slow_one_waits(self)

    def test_locked_core_file_is_retried(self):
        check_locked_core_file_is_retried(self)

    def test_old_loader_with_a_stale_cache_copy_fails(self):
        with world({0: "old_loader"}) as w:
            # a loader that says "reloaded" but still maps the old bytes: cachePath keeps pointing at a copy of the old build
            real = w.games[0].reload

            def lying(params):
                real(params)
                w.games[0].cache.write_bytes(core_bytes("old"))
                return {"generation": w.games[0].gen}
            w.games[0].reload = lying
            code, rep = w.swap(ids=(0,))
            self.assertEqual(code, 1, rep)
            self.assertIn("not the one that was copied", rep["instances"][0]["detail"])

    def test_command_line(self):
        with world() as w:
            # run.py swap: bad list is an argparse error (exit 2), a missing DLL is a refusal (exit 2) before any pipe is opened
            import io
            sink = io.StringIO()
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(sink):
                run.main(["swap", "--instances", "0,0"])
            self.assertEqual(cm.exception.code, 2)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                self.assertEqual(run.main(["swap", "--instances", "0,1", "--dll", str(w.dll.with_name("nope.dll"))]), 2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(sink):
                code = run.main(["swap", "--dll", str(w.dll.with_name("nope.dll")), "--out", str(w.dll.parent / "r.json")])
            self.assertEqual(code, 2)
            self.assertIn("refused", json.loads(buf.getvalue()))
            self.assertEqual(json.loads((w.dll.parent / "r.json").read_text())["refused"], json.loads(buf.getvalue())["refused"])
            self.assertEqual(hotswap.instance_list("0,1"), [0, 1])

    # ---- mutation twins ----
    def test_twin_unchanged_build_reloaded_anyway(self):
        self.assertCaught(check_unchanged_build_is_not_reloaded, [("needs_swap", _always_swap)])

    def test_twin_reply_trusted_instead_of_build_id(self):
        for mut in ("stale_id", "gen_stuck", "rollback_quiet", "noop_reload"):
            self.assertCaught(check_reload_that_changed_nothing_fails, [("verify_after", _verify_trusts_the_reply)], mut)

    def test_twin_file_never_copied(self):
        self.assertCaught(check_swaps_every_copy, [("install_dll", _install_does_nothing)])

    def test_twin_pipe_not_awaited(self):
        self.assertCaught(check_pipe_is_awaited_after_a_dropped_call, [("reconnect", _reconnect_gives_up)])

    def test_twin_session_not_awaited(self):
        self.assertCaught(check_lost_session_fails_and_slow_one_waits, [("wait_coop", _wait_coop_always_true)])

    def test_twin_core_path_not_checked(self):
        self.assertCaught(check_core_outside_the_instance_is_refused, [("check_core_path", _path_unchecked)])

    def test_twin_any_dll_accepted(self):
        self.assertCaught(check_non_core_dll_is_refused, [("core_dll_problem", _any_dll_is_fine)])

    def test_twin_data_files_not_copied(self):
        self.assertCaught(check_data_files_follow_the_core, [("sync_data_files", lambda files, target_dir: [])])

    def test_twin_data_files_never_stale(self):
        self.assertCaught(check_data_files_follow_the_core, [("stale_data_files", lambda dll, target_dir: [])])

    def test_twin_ini_is_copied_too(self):
        real = hotswap.stale_data_files

        def with_ini(dll, target_dir):
            return real(dll, target_dir) + [f for f in Path(dll).parent.glob("nvcoop.ini")]
        self.assertCaught(check_data_files_follow_the_core, [("stale_data_files", with_ini)])

    def test_twin_leaks_ignored(self):
        self.assertCaught(check_leaks_fail, [("reload_state_problems", _no_leak_check)])

    def test_twin_locked_file_not_retried(self):
        def no_retry_install(src, dest, expected_id, sleep=None, replace=os.replace):
            tmp = dest.with_name(dest.name + hotswap.TMP_SUFFIX)
            tmp.write_bytes(Path(src).read_bytes())
            try:
                os.replace(str(tmp), str(dest))   # one try, and the temporary file is left behind on an error
            except PermissionError as e:
                raise hotswap.SwapFailed(f"cannot replace {dest}: {e}")
        self.assertCaught(check_locked_core_file_is_retried, [("install_dll", no_retry_install)])

    def test_twin_monolith_not_recognised(self):
        real = hotswap.read_status

        def lax(nv):
            try:
                return real(nv)
            except hotswap.Refused:
                return {"generation": 0, "loaded": True, "corePath": "", "last": {}}
        self.assertCaught(check_monolith_is_refused_before_anything_changes, [("read_status", lax)])

    def test_twin_failure_does_not_stop_the_swap(self):
        real = hotswap.swap_one

        def carry_on(plan, *a, **kw):
            try:
                return real(plan, *a, **kw)
            except hotswap.SwapFailed as e:
                return {"instance": plan.instance, "status": "swapped", "detail": str(e)}
        self.assertCaught(check_failure_stops_the_swap, [("swap_one", carry_on)])


if __name__ == "__main__":
    unittest.main()
