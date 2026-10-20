import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crashlog  # noqa: E402
import instances  # noqa: E402

OLD_STEAM_DEFAULT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas")


class CrashLog(unittest.TestCase):
    def test_collects_logs_and_crash_line(self):
        with tempfile.TemporaryDirectory() as g, tempfile.TemporaryDirectory() as d:
            g = Path(g)
            (g / "Data" / "NVSE" / "Plugins").mkdir(parents=True)
            (g / "nvse.log").write_text("init complete\n")
            (g / "Data" / "NVSE" / "Plugins" / "nvcoop.log").write_text("[nvcoop] x\n[nvcoop] CRASH code=C0000005 addr=0\n")
            s = crashlog.collect_crash_report(Path(d) / "out", g, event_minutes=1, event_query=lambda m: ["stub"])
            self.assertEqual(s["windows_crash_records"], ["stub"])
            self.assertIn("nvse.log", s["files"])
            self.assertIn("nvcoop.log", s["files"])
            self.assertEqual(len(s["nvcoop_crash_lines"]), 1)
            self.assertTrue((Path(d) / "out" / "summary.json").exists())
            self.assertEqual(json.loads((Path(d) / "out" / "summary.json").read_text())["files"], s["files"])

    def test_missing_logs_ok(self):
        with tempfile.TemporaryDirectory() as g, tempfile.TemporaryDirectory() as d:
            s = crashlog.collect_crash_report(d, g, event_minutes=1, event_query=lambda m: ["stub"])
            self.assertEqual(s["files"], [])


class CrashLogGameDir(unittest.TestCase):
    """Without a game_dir argument, crashlog uses the runner's game dir (instances.game_dir: FNV_GAME_DIR, else
    the Steam default). Before, it had its own copy of the Steam path and ignored FNV_GAME_DIR."""

    def check_default_game_dir_is_the_runners(self, mut):
        patch = mock.patch.object(crashlog, "default_game_dir", lambda: OLD_STEAM_DEFAULT) if mut else nullcontext()
        with patch, tempfile.TemporaryDirectory() as g, tempfile.TemporaryDirectory() as d:
            (Path(g) / "nvse.log").write_text("from the FNV_GAME_DIR install\n")
            with mock.patch.dict(os.environ, {"FNV_GAME_DIR": g}):
                s = crashlog.collect_crash_report(Path(d) / "out", event_minutes=1, event_query=lambda m: ["stub"])
            self.assertEqual(Path(s["game_dir"]), Path(g))
            self.assertIn("nvse.log", s["files"])
            with mock.patch.dict(os.environ):
                os.environ.pop("FNV_GAME_DIR", None)
                self.assertEqual(crashlog.default_game_dir(), instances.game_dir())  # one Steam default, not two

    def test_default_game_dir_is_the_runners(self):
        self.check_default_game_dir_is_the_runners(None)

    def test_twin_own_steam_default_ignores_env(self):
        """Twin: the old module-level Steam path ignores FNV_GAME_DIR, so the check must fail."""
        with self.assertRaises(AssertionError):
            self.check_default_game_dir_is_the_runners("own_steam_default")


if __name__ == "__main__":
    unittest.main()
