"""Game-free tests of two-copy pairs: pairs.py (layout, ini checks, RAM guard) and `provision --pairs` / `up --pairs`.

Each check_* method takes a mutant name. With None it runs the real code and must pass. With a mutant name it runs a
broken copy (the defect the check exists to find). Each twin test requires that the check then fails with an
AssertionError. A twin that passes, or that crashes with another exception, is a failed test.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Callable, Dict, Optional
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import instances  # noqa: E402
import pairs  # noqa: E402
import run  # noqa: E402
from test_provision_cli import PLUGINS, ini_values, make_fake_game, read_ini  # noqa: E402

GIB = 2 ** 30
REAL_VALIDATE = pairs.validate_pairs


# ------------------------------------------------------------------ broken copies (mutants)
def _validate_without(*words: str) -> Callable:
    """validate_pairs that forgets one kind of check: the problems that mention any of the words are dropped."""
    def v(pair_list, cfgs):
        return [p for p in REAL_VALIDATE(pair_list, cfgs) if not any(w in p for w in words)]
    return v


def _max_pairs_per_pair(free_bytes, up_copies=0, copy_gb=pairs.COPY_GB, reserve_gb=pairs.RESERVE_GB):
    """Counts the memory of one copy per pair, not per copy: twice as many pairs fit."""
    spare = free_bytes / GIB - reserve_gb
    return int(spare // copy_gb) if spare > 0 else 0


def _max_pairs_ignores_running(free_bytes, up_copies=0, copy_gb=pairs.COPY_GB, reserve_gb=pairs.RESERVE_GB):
    """Treats the copies that already run as if they still had to be started."""
    spare = free_bytes / GIB - reserve_gb
    return (int(spare // copy_gb) if spare > 0 else 0) // 2


def _run_argv_no_pairs(a):
    """The old run.py: provision forwards --n only, so --pairs 3 would provision 2 instances."""
    return ["provision", "--n", str(a.n)]


MUTANTS = {
    "port_constant": [(pairs, "port_of", lambda k, base=pairs.PORT_BASE: int(base))],
    "pair_of_shifted": [(pairs, "pair_of", lambda i: (int(i) + 1) // 2)],
    "roles_all_client": [(pairs, "role_of", lambda i: "client")],
    "pair_ini_missing": [(pairs, "pair_ini", lambda i, base=pairs.PORT_BASE: {})],
    "pair_ini_not_checked": [(pairs, "check_pair_ini", lambda extra: None)],
    "n_not_checked": [(pairs, "resolve_n", lambda n, p: 2 * p if p else (2 if n is None else n))],
    "port_range_unchecked": [(pairs, "check_port_base", lambda base, n: None)],
    "validate_no_dup_port": [(pairs, "validate_pairs", _validate_without("share co-op port"))],
    "validate_no_port_match": [(pairs, "validate_pairs", _validate_without("joins port"))],
    "validate_no_role": [(pairs, "validate_pairs", _validate_without("must be the"))],
    "validate_no_remote": [(pairs, "validate_pairs", _validate_without("not this PC"))],
    "validate_no_enable": [(pairs, "validate_pairs", _validate_without("co-op is off"))],
    "validate_no_missing": [(pairs, "validate_pairs", _validate_without("no nvcoop.ini"))],
    "validate_no_id": [(pairs, "validate_pairs", _validate_without("expected"))],
    "validate_no_pipe": [(pairs, "validate_pairs", _validate_without("pipe"))],
    "ram_per_pair": [(pairs, "max_pairs_by_ram", _max_pairs_per_pair)],
    "ram_ignores_running": [(pairs, "max_pairs_by_ram", _max_pairs_ignores_running)],
    "ram_never_refuses": [(pairs, "ram_problem", lambda *a, **k: None)],
    "run_drops_pairs": [(run, "provision_argv", _run_argv_no_pairs)],
}


@contextlib.contextmanager
def mutant(name: Optional[str]):
    with contextlib.ExitStack() as st:
        for obj, attr, new in MUTANTS[name] if name else []:
            st.enter_context(mock.patch.object(obj, attr, new))
        yield


def good_cfg(i: int, base: int = pairs.PORT_BASE, **over) -> Dict[str, Optional[str]]:
    """What provision --pairs --ini coop.enable=1 writes for instance i, as pairs.read_instance_config returns it."""
    port = str(pairs.port_of(pairs.pair_of(i), base))
    cfg = {"present": "1", "enable": "1", "role": "host" if i % 2 == 0 else "client", "port": port, "host": "127.0.0.1",
           "listen": "127.0.0.1", "instanceid": str(i), "bridge": str(i), "pipe": rf"\\.\pipe\nvcoop-{i}"}
    cfg.update(over)
    return cfg


def good_cfgs(n_pairs: int) -> Dict[int, Dict[str, Optional[str]]]:
    return {i: good_cfg(i) for i in range(2 * n_pairs)}


class Pairs(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.game = make_fake_game(self.root)
        self.inst = self.root / "inst"
        env = mock.patch.dict(os.environ, {"FNV_GAME_DIR": str(self.game), "FNV_INSTANCES_ROOT": str(self.inst)})
        env.start()
        self.addCleanup(env.stop)
        for obj, attr, new in ((instances, "DEFAULT_PLUGIN_DLL", self.root / "no-build" / "nvcoop.dll"),
                               (run, "DEFAULT_DLL", self.root / "no-build" / "nvcoop.dll"),
                               (instances.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=100 * 2**30))):
            pt = mock.patch.object(obj, attr, new)
            pt.start()
            self.addCleanup(pt.stop)

    def cli(self, main, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def ini(self, i: int):
        return read_ini(instances.instance_dir(i) / PLUGINS / "nvcoop.ini")

    # ---------------------------------------------------------------- layout
    def check_layout(self, mut):
        with mutant(mut):
            ports = []
            for k in range(5):
                h, c = pairs.pair_ids(k)
                self.assertEqual((h, c), (2 * k, 2 * k + 1))
                self.assertEqual((pairs.pair_of(h), pairs.pair_of(c)), (k, k), "both copies of a pair share the pair number")
                self.assertEqual((pairs.role_of(h), pairs.role_of(c)), ("host", "client"))
                ports.append(pairs.port_of(k))
            self.assertEqual(ports, [27960 + k for k in range(5)], "every pair has its own port, counting up from the base")
            self.assertEqual(pairs.port_of(2, 31000), 31002)

    def test_layout(self):
        self.check_layout(None)

    def test_twin_port_constant(self):
        with self.assertRaises(AssertionError):
            self.check_layout("port_constant")

    def test_twin_pair_of_shifted(self):
        with self.assertRaises(AssertionError):
            self.check_layout("pair_of_shifted")

    def test_twin_roles_all_client(self):
        with self.assertRaises(AssertionError):
            self.check_layout("roles_all_client")

    # ---------------------------------------------------------------- provision --pairs
    def check_provision_pairs(self, mut):
        """provision --pairs 3 writes, for each of 6 instances: its role, its pair's port, loopback, its own ids. The files
        pass validate_pairs (the same check run_parallel makes) and no two pairs share a port."""
        with mutant(mut):
            code, out, err = self.cli(instances.main, ["provision", "--pairs", "3", "--plugin-dll", "", "--ini", "coop.enable=1"])
            self.assertEqual(code, 0, err)
            ports = {}
            for i in range(6):
                ini, k = self.ini(i), i // 2
                self.assertEqual(ini_values(ini, "coop", "role"), ["host" if i % 2 == 0 else "client"], f"instance {i}")
                self.assertEqual(ini_values(ini, "coop", "port"), [str(27960 + k)], f"instance {i}")
                self.assertEqual(ini_values(ini, "coop", "host"), ["127.0.0.1"])
                self.assertEqual(ini_values(ini, "coop", "listen"), ["127.0.0.1"])
                self.assertEqual(ini_values(ini, "coop", "enable"), ["1"], "the user's --ini still applies")
                self.assertEqual(ini_values(ini, "coop", "instanceId"), [str(i)])
                self.assertEqual(ini_values(ini, "bridge", "instance"), [str(i)])
                meta = instances.load_meta(i)
                self.assertEqual((meta["pair"], meta["port"]), (k, 27960 + k))
                ports.setdefault(k, set()).add(ini_values(ini, "coop", "port")[0])
            self.assertEqual(sorted(ports), [0, 1, 2], "6 instances = 3 pairs")
            self.assertTrue(all(len(v) == 1 for v in ports.values()), "host and client of a pair use one port")
            self.assertEqual(len({next(iter(v)) for v in ports.values()}), 3, "the pairs use three different ports")
            cfgs = {i: pairs.read_instance_config(i) for i in range(6)}
            self.assertEqual(REAL_VALIDATE([0, 1, 2], cfgs), [])
            self.assertIn("pair=2 port=27962", out)

    def test_provision_pairs(self):
        self.check_provision_pairs(None)

    def test_twin_provision_port_constant(self):
        with self.assertRaises(AssertionError):
            self.check_provision_pairs("port_constant")

    def test_twin_provision_pair_of_shifted(self):
        with self.assertRaises(AssertionError):
            self.check_provision_pairs("pair_of_shifted")

    def test_twin_provision_no_pair_ini(self):
        with self.assertRaises(AssertionError):
            self.check_provision_pairs("pair_ini_missing")

    def check_pairs_options_rejected(self, mut):
        with mutant(mut):
            cases = [
                (["--pairs", "2", "--n", "3"], "does not match"),
                (["--pairs", "2", "--ini", "coop.port=30000"], "per-pair key"),
                (["--pairs", "2", "--ini", "Coop.Host=10.0.0.5"], "per-pair key"),
                (["--pairs", "2", "--port-base", "65535"], "1024..65535"),
                (["--pairs", "2", "--port-base", "80"], "1024..65535"),
                (["--pairs", "-1"], "must be 1 or more"),
            ]
            for argv, reason in cases:
                code, _, err = self.cli(instances.main, ["provision", "--plugin-dll", "", *argv])
                self.assertEqual(code, 2, f"{argv}: exit {code}")
                self.assertIn(reason, err, argv)
                self.assertFalse(self.inst.exists() and any(self.inst.glob("*/instance.json")), "nothing is written first")
            # --pairs 2 with the matching --n 4 is fine
            code, _, err = self.cli(instances.main, ["provision", "--plugin-dll", "", "--pairs", "2", "--n", "4"])
            self.assertEqual(code, 0, err)

    def test_pairs_options_rejected(self):
        self.check_pairs_options_rejected(None)

    def test_twin_pair_ini_not_checked(self):
        with self.assertRaises(AssertionError):
            self.check_pairs_options_rejected("pair_ini_not_checked")

    def test_twin_n_not_checked(self):
        with self.assertRaises(AssertionError):
            self.check_pairs_options_rejected("n_not_checked")

    def test_twin_port_range_unchecked(self):
        with self.assertRaises(AssertionError):
            self.check_pairs_options_rejected("port_range_unchecked")

    def test_without_pairs_nothing_changes(self):
        """No --pairs: no port, host or listen key is written, so every client joins the plugin's default (as before)."""
        code, _, err = self.cli(instances.main, ["provision", "--n", "3", "--plugin-dll", ""])
        self.assertEqual(code, 0, err)
        for i in range(3):
            ini = self.ini(i)
            self.assertEqual(ini_values(ini, "coop", "port"), [])
            self.assertEqual(ini_values(ini, "coop", "host"), [])
            self.assertEqual(ini_values(ini, "coop", "role"), ["host" if i == 0 else "client"])
            self.assertNotIn("pair", instances.load_meta(i))
        code, _, _ = self.cli(instances.main, ["provision", "--plugin-dll", ""])
        self.assertEqual(code, 0)
        self.assertEqual(len(list(self.inst.glob("[0-9]*/instance.json"))), 3, "default --n is 2 and instance 2 stays from before")

    def test_first_pair_leaves_earlier_pairs_alone(self):
        code, _, err = self.cli(instances.main, ["provision", "--pairs", "3", "--first-pair", "1", "--plugin-dll", ""])
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(p.parent.name for p in self.inst.glob("[0-9]*/instance.json")), ["2", "3", "4", "5"])
        self.assertEqual(ini_values(self.ini(2), "coop", "port"), ["27961"], "ports do not depend on --first-pair")
        for argv in (["--pairs", "3", "--first-pair", "3"], ["--first-pair", "1"]):
            code, _, err = self.cli(instances.main, ["provision", "--plugin-dll", "", *argv])
            self.assertEqual(code, 2, argv)
            self.assertIn("--first-pair", err)
        code, _, err = self.cli(run.main, ["provision", "--pairs", "3", "--first-pair", "2", "--plugin-dll", ""])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.inst / "5" / "instance.json").exists())

    def test_show_and_destroy_take_pairs(self):
        self.cli(instances.main, ["provision", "--pairs", "2", "--plugin-dll", ""])
        code, out, _ = self.cli(instances.main, ["show", "--pairs", "2"])
        self.assertEqual((code, out.count('"pair":')), (0, 4))
        code, out, _ = self.cli(instances.main, ["destroy", "--pairs", "2"])
        self.assertEqual((code, out.count("destroyed")), (0, 4))
        self.assertFalse(list(self.inst.glob("[0-9]*/instance.json")))

    def check_run_py_forwards_pairs(self, mut):
        with mutant(mut):
            code, _, err = self.cli(run.main, ["provision", "--pairs", "2", "--port-base", "31000", "--plugin-dll", ""])
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(p.parent.name for p in self.inst.glob("[0-9]*/instance.json")), ["0", "1", "2", "3"])
            self.assertEqual(ini_values(self.ini(3), "coop", "port"), ["31001"])
            code, _, err = self.cli(run.main, ["provision", "--pairs", "2", "--n", "5", "--plugin-dll", ""])
            self.assertEqual(code, 2, "run.py provision --pairs 2 --n 5 is an error (argparse exits 2)")

    def test_run_py_forwards_pairs(self):
        self.check_run_py_forwards_pairs(None)

    def test_twin_run_drops_pairs(self):
        with self.assertRaises(AssertionError):
            self.check_run_py_forwards_pairs("run_drops_pairs")

    # ---------------------------------------------------------------- validate_pairs
    CASES = [
        # (name, cfgs edit, expected text, mutant that drops that check)
        ("same_port", lambda c: c[2].update(port="27960") or c[3].update(port="27960"), "share co-op port", "validate_no_dup_port"),
        ("default_port_everywhere", lambda c: [c[i].update(port=None) for i in range(6)], "share co-op port", "validate_no_dup_port"),
        ("client_other_port", lambda c: c[3].update(port="27999"), "joins port 27999", "validate_no_port_match"),
        ("host_is_client", lambda c: c[2].update(role="client"), "must be the host", "validate_no_role"),
        ("client_is_host", lambda c: c[5].update(role="host"), "must be the client", "validate_no_role"),
        ("client_remote", lambda c: c[1].update(host="203.0.113.5"), "not this PC", "validate_no_remote"),
        ("coop_off", lambda c: c[4].update(enable=None), "co-op is off", "validate_no_enable"),
        ("not_provisioned", lambda c: c.update({5: {"present": None}}), "no nvcoop.ini", "validate_no_missing"),
        ("wrong_instance_id", lambda c: c[3].update(instanceid="1"), "expected 3", "validate_no_id"),
        ("same_pipe", lambda c: c[3].update(pipe=c[1]["pipe"]), "pipe", "validate_no_pipe"),
    ]

    def check_validate(self, mut):
        with mutant(mut):
            self.assertEqual(pairs.validate_pairs([0, 1, 2], good_cfgs(3)), [], "a clean setup has no problems")
            self.assertEqual(pairs.validate_pairs([1], good_cfgs(3)), [], "a subset of the pairs is checked on its own")
            for name, edit, text, _ in self.CASES:
                cfgs = good_cfgs(3)
                edit(cfgs)
                got = pairs.validate_pairs([0, 1, 2], cfgs)
                self.assertTrue(any(text in g for g in got), f"{name}: expected a problem with {text!r}, got {got}")

    def test_validate(self):
        self.check_validate(None)

    def test_twins_validate(self):
        for name, _, text, mut in self.CASES:
            with self.subTest(mut):
                with self.assertRaises(AssertionError):
                    self.check_validate(mut)

    def test_validate_ignores_pairs_not_asked_for(self):
        cfgs = good_cfgs(3)
        cfgs[2].update(port="27960")   # pair 1 is broken, but only pair 0 and 2 are run
        self.assertEqual(pairs.validate_pairs([0, 2], cfgs), [])

    def test_guest_counts_as_client(self):
        cfgs = good_cfgs(1)
        cfgs[1]["role"] = "guest"
        self.assertEqual(pairs.validate_pairs([0], cfgs), [])

    # ---------------------------------------------------------------- RAM guard
    def check_ram(self, mut):
        with mutant(mut):
            # 10 GiB free, 2 GiB reserve, 0.7 GB a copy: 11 copies = 5 pairs. Copies that run count on top: (2 + 11) // 2 = 6.
            self.assertEqual(pairs.max_pairs_by_ram(10 * GIB), 5)
            self.assertEqual(pairs.max_pairs_by_ram(10 * GIB, up_copies=2), 6)
            self.assertEqual(pairs.max_pairs_by_ram(1 * GIB), 0, "below the reserve nothing fits")
            self.assertIsNone(pairs.ram_problem(5, 0, 10 * GIB))
            why = pairs.ram_problem(6, 0, 10 * GIB)
            self.assertIsNotNone(why, "6 pairs need 12 copies and only 11 fit")
            self.assertIn("at most 5", why)
            self.assertIn("8.4 GB", why)
            self.assertIsNotNone(pairs.ram_problem(3, 0, int(5.5 * GIB)), "6 copies need 4.2 GB beyond the 2 GB reserve")
            # the lead's pair is already running: its memory is taken out of the free RAM, so it needs no more
            self.assertIsNone(pairs.ram_problem(1, 2, int(0.5 * GIB)), "both copies of the pair already run")
            self.assertIsNone(pairs.ram_problem(2, 2, int(4.6 * GIB)), "two more copies need 1.4 GB beyond the reserve")
            self.assertIsNotNone(pairs.ram_problem(3, 2, int(4.6 * GIB)), "four more copies need 2.8 GB")

    def test_ram(self):
        self.check_ram(None)

    def test_twin_ram_per_pair(self):
        with self.assertRaises(AssertionError):
            self.check_ram("ram_per_pair")

    def test_twin_ram_ignores_running(self):
        with self.assertRaises(AssertionError):
            self.check_ram("ram_ignores_running")

    def test_twin_ram_never_refuses(self):
        with self.assertRaises(AssertionError):
            self.check_ram("ram_never_refuses")

    @unittest.skipUnless(os.name == "nt", "Windows memory API")
    def test_real_ram_and_pipe_probe(self):
        self.assertGreater(pairs.free_ram_bytes(), 64 * 2**20)
        self.assertFalse(pairs.pipe_up(987654), "no copy has that instance number")

    def test_ram_unreadable_refuses(self):
        self.assertIsNotNone(pairs.ram_problem(1, 0, 0), "free_ram_bytes() == 0 (unreadable) must refuse, not allow")

    # ---------------------------------------------------------------- run.py up --pairs
    def check_up_pairs_guard(self, mut):
        started = []
        with mutant(mut), mock.patch.object(run, "cmd_up", lambda a: started.append(a.n) or 0), \
                mock.patch.object(pairs, "free_ram_bytes", lambda: 3 * GIB):
            code, _, err = self.cli(run.main, ["up", "--pairs", "3"])
            self.assertEqual(code, 2, err)
            self.assertIn("refusing 3 pair(s)", err)
            self.assertEqual(started, [], "no copy may be launched when the RAM guard refuses")
            code, _, err = self.cli(run.main, ["up", "--pairs", "3", "--no-ram-check"])
            self.assertEqual((code, started), (0, [6]), "--no-ram-check overrides; --pairs 3 is --n 6")
        started.clear()
        with mock.patch.object(run, "cmd_up", lambda a: started.append(a.n) or 0), \
                mock.patch.object(pairs, "free_ram_bytes", lambda: 64 * GIB):
            self.assertEqual(self.cli(run.main, ["up", "--pairs", "2"])[0], 0)
            self.assertEqual(started, [4])
            started.clear()
            self.assertEqual(self.cli(run.main, ["up", "--n", "2"])[0], 0)   # without --pairs nothing is checked
            self.assertEqual(started, [2])

    def test_up_pairs_guard(self):
        self.check_up_pairs_guard(None)

    def test_twin_up_never_refuses(self):
        with self.assertRaises(AssertionError):
            self.check_up_pairs_guard("ram_never_refuses")


if __name__ == "__main__":
    unittest.main()
