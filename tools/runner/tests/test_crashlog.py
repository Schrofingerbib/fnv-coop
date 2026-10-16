import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crashlog  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
