"""Game-free tests of the provisioning command line: `instances.py provision` and `run.py provision`
(--ini SECTION.KEY=VALUE, --plugin-dll, --core-dll).

Each check_* method takes a mutant name. With None it runs the real code and must pass. With a mutant name it
runs an in-process broken copy (the old behaviour, or the bug that the check exists to find). Each twin test
requires that the check then fails with an AssertionError. A twin that passes, or that crashes with another
exception, is a failed test.
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
from typing import List, Optional, Tuple
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import instances  # noqa: E402
import run  # noqa: E402

REAL_PLAN = instances.plugin_plan
PLUGINS = Path("Data") / "NVSE" / "Plugins"


# ------------------------------------------------------------------ in-process broken copies (mutants)
def _merge_case_sensitive(cfg, extra):
    """The old write_plugin_ini merge: 'Role' and 'role' become two keys."""
    for s, kv in (extra or {}).items():
        cfg.setdefault(s, {}).update(kv)
    return cfg


def _plan_skip_missing(plugin_dll=None, core_dll=None, plugin_files=None):
    """The old CLI: a dll path that does not exist installs nothing and gives no error."""
    files = [Path(f) for f in [plugin_dll, core_dll, *(plugin_files or [])] if f and Path(f).is_file()]
    return files, ({"loader": {"core": Path(core_dll).name}} if core_dll and Path(core_dll).is_file() else {})


def _plan_no_name_check(plugin_dll=None, core_dll=None, plugin_files=None):
    """Missing files are errors, but two files with the same name are not: the second overwrites the first."""
    files = [Path(f) for f in [plugin_dll, core_dll, *(plugin_files or [])] if f]
    for f in files:
        if not f.is_file():
            raise FileNotFoundError(f"not found: {f}")
    return files, ({"loader": {"core": Path(core_dll).name}} if core_dll else {})


def _plan_core_ignored(plugin_dll=None, core_dll=None, plugin_files=None):
    """The old provision: no core dll support."""
    return REAL_PLAN(plugin_dll, None, plugin_files)


def _plan_core_without_loader_key(plugin_dll=None, core_dll=None, plugin_files=None):
    """The core is copied, but [loader] core= is not written, so the loader looks for nvcoop_mod.dll."""
    return REAL_PLAN(plugin_dll, core_dll, plugin_files)[0], {}


def _run_argv_old(a):
    """The old run.py: provision forwards only --n, --plugin-dll, --nvse-dir and --autoload."""
    return (["provision", "--n", str(a.n)] + (["--plugin-dll", a.plugin_dll] if a.plugin_dll else [])
            + (["--nvse-dir", a.nvse_dir] if a.nvse_dir else []) + (["--autoload", a.autoload] if a.autoload else []))


MUTANTS = {
    "ini_ignored": [(instances, "ini_overrides", lambda specs: {})],
    "merge_case_sensitive": [(instances, "merge_ini", _merge_case_sensitive)],
    "no_ini_check": [(instances, "check_ini_entry", lambda *a, **k: None)],
    "missing_dll_skipped": [(instances, "plugin_plan", _plan_skip_missing)],
    "no_name_check": [(instances, "plugin_plan", _plan_no_name_check)],
    "core_ignored": [(instances, "plugin_plan", _plan_core_ignored)],
    "core_without_loader_key": [(instances, "plugin_plan", _plan_core_without_loader_key)],
    "run_drops_options": [(run, "provision_argv", _run_argv_old)],
}


@contextlib.contextmanager
def mutant(name: Optional[str]):
    with contextlib.ExitStack() as st:
        for obj, attr, new in MUTANTS[name] if name else []:
            st.enter_context(mock.patch.object(obj, attr, new))
        yield


# ------------------------------------------------------------------ helpers
def read_ini(path: Path) -> List[Tuple[str, str, str]]:
    """(section, key, value) in file order, duplicates kept."""
    out, sec = [], ""
    for ln in Path(path).read_text().splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("["):
            sec = s[1:s.index("]")]
            continue
        k, v = s.split("=", 1)
        out.append((sec, k, v))
    return out


def ini_values(entries, section: str, key: str) -> List[str]:
    return [v for s, k, v in entries if s.lower() == section.lower() and k.lower() == key.lower()]


def make_fake_game(root: Path) -> Path:
    g = root / "game"
    (g / "Data").mkdir(parents=True)
    (g / "FalloutNV.exe").write_bytes(b"MZ" + b"exe" * 100)
    (g / "Fallout_default.ini").write_text("[General]\nSLocalSavePath=Saves\\\n")
    (g / "Data" / "FalloutNV.esm").write_bytes(b"TES4" + b"x" * 100)
    return g


class ProvisionCli(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.game = make_fake_game(self.root)
        self.inst = self.root / "inst"
        env = mock.patch.dict(os.environ, {"FNV_GAME_DIR": str(self.game), "FNV_INSTANCES_ROOT": str(self.inst)})
        env.start()
        self.addCleanup(env.stop)
        # The default build output must not leak in from the checkout, and free space on the host must not matter.
        for obj, attr, new in ((instances, "DEFAULT_PLUGIN_DLL", self.root / "no-build" / "nvcoop.dll"),
                               (run, "DEFAULT_DLL", self.root / "no-build" / "nvcoop.dll"),
                               (instances.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=100 * 2**30))):
            pt = mock.patch.object(obj, attr, new)
            pt.start()
            self.addCleanup(pt.stop)
        self.dll = self.file("build/nvcoop.dll", b"loader-or-monolith")
        self.core = self.file("split/nvcoop_mod.dll", b"core-v1")

    def file(self, rel: str, data: bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def cli(self, main, argv) -> Tuple[int, str, str]:
        """Run a CLI main in-process. Returns (exit code, stdout, stderr); SystemExit counts as an exit code."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def nvcoop_ini(self, i: int = 0):
        return read_ini(instances.instance_dir(i) / PLUGINS / "nvcoop.ini")

    # ---------------------------------------------------------------- 1. --ini SECTION.KEY=VALUE
    def check_ini_entries_written(self, mut):
        with mutant(mut):
            code, _, err = self.cli(instances.main, [
                "provision", "--n", "2", "--plugin-dll", str(self.dll), "--ini", "coop.enable=1",
                "--ini", "dev.bots=1", "--ini", "dev.animprobe=1", "--ini", "bridge.autoload=save7"])
        self.assertEqual(code, 0, err)
        for i in (0, 1):
            ini = self.nvcoop_ini(i)
            self.assertEqual(ini_values(ini, "coop", "enable"), ["1"])
            self.assertEqual(ini_values(ini, "dev", "bots"), ["1"])
            self.assertEqual(ini_values(ini, "dev", "animprobe"), ["1"])
            self.assertEqual(ini_values(ini, "bridge", "autoload"), ["save7"], "an --ini entry replaces a default")
            self.assertEqual(ini_values(ini, "bridge", "instance"), [str(i)], "the provisioned keys stay")
            self.assertEqual(ini_values(ini, "coop", "role"), ["host" if i == 0 else "client"])
            self.assertEqual(instances.load_meta(i)["ini"]["dev"]["bots"], "1")

    def test_ini_entries_written(self):
        self.check_ini_entries_written(None)

    def test_twin_ini_ignored(self):
        with self.assertRaises(AssertionError):
            self.check_ini_entries_written("ini_ignored")

    def check_ini_override_is_case_insensitive(self, mut):
        """GetPrivateProfileString reads the first duplicate and the coop parser reads the last one. So an
        override must replace the key, whatever its case, and never add a second one."""
        with mutant(mut):
            code, _, err = self.cli(instances.main, [
                "provision", "--n", "1", "--ini", "Coop.Role=client", "--ini", "BRIDGE.Minimized=0"])
        self.assertEqual(code, 0, err)
        ini = self.nvcoop_ini()
        text = (instances.instance_dir(0) / PLUGINS / "nvcoop.ini").read_text().lower()
        self.assertEqual(text.count("[coop]"), 1, text)
        self.assertEqual(text.count("[bridge]"), 1, text)
        self.assertEqual(ini_values(ini, "coop", "role"), ["client"])
        self.assertEqual(ini_values(ini, "bridge", "minimized"), ["0"])

    def test_ini_override_is_case_insensitive(self):
        self.check_ini_override_is_case_insensitive(None)

    def test_twin_ini_merge_case_sensitive(self):
        with self.assertRaises(AssertionError):
            self.check_ini_override_is_case_insensitive("merge_case_sensitive")

    BAD_INI = [
        ("coop.enable", "'=' is missing"),
        ("enable=1", "'.' between section and key is missing"),
        (".enable=1", "bad section name"),
        ("co]op.enable=1", "bad section name"),
        ("coop.=1", "bad key"),
        ("coop.;enable=1", "bad key"),
        ("coop.enable=1\n[dev]", "line break"),
        ("coop.enable=1\rrole=client", "line break"),
    ]

    def check_malformed_ini_rejected(self, mut):
        with mutant(mut):
            for spec, reason in self.BAD_INI:
                for main in (instances.main, run.main):
                    code, _, err = self.cli(main, ["provision", "--n", "1", "--ini", spec])
                    self.assertEqual(code, 2, f"{main.__module__} accepted --ini {spec!r}")
                    self.assertIn("--ini", err)
                    self.assertIn(reason, err)
                    self.assertFalse(self.inst.exists(), "nothing may be written before the input is checked")
            with self.assertRaises(ValueError):  # the Python API checks extra_ini too
                instances.provision(0, extra_ini={"coop": {"enable": "1\n[dev]\nbots=1"}})
            self.assertFalse(instances.instance_dir(0).exists())
        # empty values and values with '=' are valid
        self.assertEqual(instances.parse_ini_override("bridge.autoload="), ("bridge", "autoload", ""))
        self.assertEqual(instances.parse_ini_override(" coop . rules = a=b "), ("coop", "rules", "a=b"))

    def test_malformed_ini_rejected(self):
        self.check_malformed_ini_rejected(None)

    def test_twin_ini_not_checked(self):
        with self.assertRaises(AssertionError):
            self.check_malformed_ini_rejected("no_ini_check")

    # ---------------------------------------------------------------- 2. a missing --plugin-dll / --core-dll
    def check_missing_dll_is_an_error(self, mut):
        missing = self.root / "build" / "nvcoop_typo.dll"
        with mutant(mut):
            for main in (instances.main, run.main):
                for flag, argv in (("--plugin-dll", ["--plugin-dll", str(missing)]),
                                   ("--core-dll", ["--plugin-dll", str(self.dll), "--core-dll", str(missing)])):
                    code, _, err = self.cli(main, ["provision", "--n", "1", *argv])
                    self.assertEqual(code, 2, f"{main.__module__} {flag} {missing}: exit {code}")
                    self.assertIn("not found", err)
                    self.assertIn(flag, err)
                    self.assertIn(str(missing), err)
                    self.assertFalse(self.inst.exists(), "nothing may be written before the input is checked")
            with self.assertRaises(FileNotFoundError):
                instances.provision(0, plugin_dll=missing)
            self.assertFalse(instances.instance_dir(0).exists())
            # Not given and not built (a fresh clone): provision without a plugin, as before.
            code, _, err = self.cli(instances.main, ["provision", "--n", "1"])
        self.assertEqual(code, 0, err)
        self.assertFalse((instances.instance_dir(0) / PLUGINS / "nvcoop.dll").exists())

    def test_missing_dll_is_an_error(self):
        self.check_missing_dll_is_an_error(None)

    def test_twin_missing_dll_skipped(self):
        with self.assertRaises(AssertionError):
            self.check_missing_dll_is_an_error("missing_dll_skipped")

    def test_default_plugin_dll_still_installed_when_built(self):
        built = self.file("out/L1/nvcoop.dll", b"default-build")
        with mock.patch.object(instances, "DEFAULT_PLUGIN_DLL", built):
            code, _, err = self.cli(instances.main, ["provision", "--n", "1"])
        self.assertEqual(code, 0, err)
        self.assertEqual((instances.instance_dir(0) / PLUGINS / "nvcoop.dll").read_bytes(), b"default-build")

    # ---------------------------------------------------------------- 3. --core-dll (split build)
    def check_core_dll_installed_where_the_loader_looks(self, mut):
        """The loader loads <plugins dir>/<[loader] core, default nvcoop_mod.dll> (plugin/main.cpp). A core
        with another file name proves that provision writes the key."""
        core = self.file("split/core_build_7.dll", b"core-v7")
        with mutant(mut):
            code, _, err = self.cli(instances.main, ["provision", "--n", "2", "--plugin-dll", str(self.dll),
                                                     "--core-dll", str(core)])
        self.assertEqual(code, 0, err)
        for i in (0, 1):
            plugins = instances.instance_dir(i) / PLUGINS
            names = ini_values(self.nvcoop_ini(i), "loader", "core")
            self.assertLessEqual(len(names), 1)
            loaded = plugins / (names[0] if names else "nvcoop_mod.dll")
            self.assertTrue(loaded.is_file(), f"instance {i}: the loader would not find {loaded.name}")
            self.assertEqual(loaded.read_bytes(), b"core-v7")
            self.assertEqual((plugins / "nvcoop.dll").read_bytes(), b"loader-or-monolith")
            self.assertIn("Data/NVSE/Plugins/core_build_7.dll", instances.load_meta(i)["installed"])

    def test_core_dll_installed_where_the_loader_looks(self):
        self.check_core_dll_installed_where_the_loader_looks(None)

    def test_twin_core_ignored(self):
        with self.assertRaises(AssertionError):
            self.check_core_dll_installed_where_the_loader_looks("core_ignored")

    def test_twin_core_without_loader_key(self):
        with self.assertRaises(AssertionError):
            self.check_core_dll_installed_where_the_loader_looks("core_without_loader_key")

    def test_explicit_loader_core_entry_wins(self):
        code, _, err = self.cli(instances.main, ["provision", "--n", "1", "--core-dll", str(self.core),
                                                 "--ini", "loader.core=other.dll"])
        self.assertEqual(code, 0, err)
        self.assertEqual(ini_values(self.nvcoop_ini(), "loader", "core"), ["other.dll"])

    def check_same_file_name_is_an_error(self, mut):
        """A monolith nvcoop.dll passed as --core-dll would overwrite the loader nvcoop.dll."""
        twin = self.file("other/NVCOOP.dll", b"monolith")
        with mutant(mut):
            code, _, err = self.cli(instances.main, ["provision", "--n", "1", "--plugin-dll", str(self.dll),
                                                     "--core-dll", str(twin)])
        self.assertEqual(code, 2, err)
        self.assertIn("same file name", err)
        self.assertFalse(self.inst.exists())

    def test_same_file_name_is_an_error(self):
        self.check_same_file_name_is_an_error(None)

    def test_twin_no_name_check(self):
        with self.assertRaises(AssertionError):
            self.check_same_file_name_is_an_error("no_name_check")

    # ---------------------------------------------------------------- run.py provision passes everything on
    def check_run_py_passes_options(self, mut):
        with mutant(mut):
            code, _, err = self.cli(run.main, [
                "provision", "--n", "1", "--plugin-dll", str(self.dll), "--core-dll", str(self.core),
                "--ini", "coop.enable=1", "--ini", "dev.bots=1", "--ini", "dev.animprobe=1"])
        self.assertEqual(code, 0, err)
        ini = self.nvcoop_ini()
        self.assertEqual(ini_values(ini, "coop", "enable"), ["1"])
        self.assertEqual(ini_values(ini, "dev", "bots"), ["1"])
        self.assertEqual(ini_values(ini, "dev", "animprobe"), ["1"])
        self.assertEqual(ini_values(ini, "loader", "core"), ["nvcoop_mod.dll"])
        self.assertEqual((instances.instance_dir(0) / PLUGINS / "nvcoop_mod.dll").read_bytes(), b"core-v1")

    def test_run_py_passes_options(self):
        self.check_run_py_passes_options(None)

    def test_twin_run_py_drops_options(self):
        with self.assertRaises(AssertionError):
            self.check_run_py_passes_options("run_drops_options")

    def test_run_py_forwards_values_that_start_with_a_dash(self):
        a = types.SimpleNamespace(n=1, plugin_dll="-x.dll", nvse_dir=None, autoload="", core_dll="-c.dll",
                                  ini=["-s.k=v"])
        self.assertEqual(run.provision_argv(a), ["provision", "--n", "1", "--plugin-dll=-x.dll",
                                                 "--core-dll=-c.dll", "--ini=-s.k=v"])


if __name__ == "__main__":
    unittest.main()
