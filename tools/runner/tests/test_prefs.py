"""FalloutPrefs.ini for a test copy when the owner has none. FalloutNV.exe starts its launcher (a window on the owner's
desktop) instead of the game when the prefs have no sD3DDevice; checked in game on 2026-10-21.
Twin: the old base (an empty prefs file plus the display edits) must fail the check.
Run: python -I -m unittest discover -s tools/runner/tests -p test_prefs.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import instances  # noqa: E402


def prefs_errors(rendered: str) -> list:
    errs = []
    low = rendered.lower()
    if 'sd3ddevice="fake gpu 9000"' not in low.replace(" =", "=").replace("= ", "="):
        errs.append("no sD3DDevice: the game would open its launcher instead of starting")
    if "ishadowmapresolution=256" not in low:
        errs.append("the game's low.ini preset was not applied")
    if "fdefaultfov=75" not in low:
        errs.append("Fallout_default.ini was not the base")
    if "isize w=640" not in low:
        errs.append("the instance display edits were not applied")
    return errs


class DefaultPrefs(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        self.game = root / "game"
        self.game.mkdir()
        (self.game / "FalloutNV.exe").write_bytes(b"MZ")
        (self.game / "Fallout_default.ini").write_text("[Display]\r\nfDefaultFOV=75\r\niSize W=1024\r\n", encoding="latin-1")
        (self.game / "low.ini").write_text("[Display]\r\niShadowMapResolution=256\r\n", encoding="latin-1")
        self.docs = root / "docs"
        self.docs.mkdir()
        env = mock.patch.dict(os.environ, {"FNV_GAME_DIR": str(self.game), "FNV_DOCUMENTS_DIR": str(self.docs)})
        env.start()
        self.addCleanup(env.stop)

    def render(self):
        with mock.patch.object(instances, "display_adapter_name", lambda: "Fake GPU 9000"):
            return instances.render_inis()["FalloutPrefs.ini"]

    def test_no_owner_prefs_gives_a_startable_file(self):
        self.assertEqual(prefs_errors(self.render()), [])

    def test_twin_old_empty_base_is_caught(self):
        with mock.patch.object(instances, "default_prefs", lambda adapter=None: ""):
            self.assertNotEqual(prefs_errors(self.render()), [])

    def test_unknown_adapter_is_refused(self):
        with mock.patch.object(instances, "display_adapter_name", lambda: None):
            with self.assertRaises(instances.PrefsUnavailable):
                instances.render_inis()

    def test_owner_prefs_still_win(self):
        mg = self.docs / "My Games" / "FalloutNV"
        mg.mkdir(parents=True)
        (mg / "FalloutPrefs.ini").write_text('[Display]\r\nsD3DDevice="Owner GPU"\r\n', encoding="latin-1")
        out = self.render()
        self.assertIn('sD3DDevice="Owner GPU"', out)
        self.assertNotIn("Fake GPU 9000", out)

    def check_verify_report_without_owner_prefs(self, owner_base=None):
        """lowcost.verify() is a read-only report: no owner prefs and an unknown adapter must not make it raise."""
        sys.path.insert(0, str(HERE.parents[1] / "pool"))
        import lowcost
        patches = [mock.patch.object(instances, "display_adapter_name", lambda: None)]
        if owner_base:
            patches.append(mock.patch.object(lowcost, "_owner_base", owner_base))
        with patches[0], (patches[1] if len(patches) > 1 else mock.patch.dict(os.environ, {})):
            try:
                found = lowcost.verify()
            except instances.PrefsUnavailable as e:
                raise AssertionError(f"verify() raised for a read-only report: {e}")
        self.assertTrue(found["absent"], found)  # the prefs keys are reported as absent instead

    def test_verify_report_without_owner_prefs(self):
        self.check_verify_report_without_owner_prefs()

    def test_twin_verify_raises_without_owner_prefs(self):
        with self.assertRaises(AssertionError):
            self.check_verify_report_without_owner_prefs(owner_base=lambda name: instances._base_ini(name))

    @unittest.skipUnless(os.name == "nt", "Windows display API")
    def test_live_adapter_name(self):
        name = instances.display_adapter_name()
        self.assertTrue(name and name.strip(), name)


if __name__ == "__main__":
    unittest.main()
