"""DXVK for a test copy. Under NVSE on an AMD RX 6600 the game's own D3D9 path ran exteriors at about 6 fps (main thread in the driver
creating and destroying GPU allocations); with DXVK the same copy runs at 60+ fps, and the game's speed follows its frame rate, so
provision also writes a 60 fps cap. Twin: without the cap file, or with NVCOOP_NO_DXVK=1, the check fails.
Run: python -I -m unittest discover -s tools/runner/tests -p test_dxvk.py
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


class InstallDxvk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "deps" / "dxvk").mkdir(parents=True)
        (self.root / "deps" / "dxvk" / "d3d9.dll").write_bytes(b"MZ-fake-dxvk")
        self.inst = self.root / "inst"
        self.inst.mkdir()
        self.env = mock.patch.dict(os.environ, {"NVCOOP_DEPS_DIR": str(self.root / "deps")}, clear=False)
        self.env.start()
        os.environ.pop("NVCOOP_NO_DXVK", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_installs_dll_and_cap(self):
        self.assertTrue(instances.install_dxvk(self.inst))
        self.assertEqual((self.inst / "d3d9.dll").read_bytes(), b"MZ-fake-dxvk")
        self.assertIn("d3d9.maxFrameRate = 60", (self.inst / "dxvk.conf").read_text())

    def test_replaces_a_linked_file_without_writing_through(self):
        game = self.root / "game_d3d9.dll"
        game.write_bytes(b"original-game-dll")
        os.link(game, self.inst / "d3d9.dll")
        self.assertTrue(instances.install_dxvk(self.inst))
        self.assertEqual(game.read_bytes(), b"original-game-dll", "the game's own file must never be written through a hardlink")
        self.assertEqual((self.inst / "d3d9.dll").read_bytes(), b"MZ-fake-dxvk")

    def test_missing_cache_is_a_no_op(self):
        (self.root / "deps" / "dxvk" / "d3d9.dll").unlink()
        self.assertFalse(instances.install_dxvk(self.inst))
        self.assertFalse((self.inst / "d3d9.dll").exists())

    def test_opt_out(self):
        with mock.patch.dict(os.environ, {"NVCOOP_NO_DXVK": "1"}):
            self.assertFalse(instances.install_dxvk(self.inst))
        self.assertFalse((self.inst / "d3d9.dll").exists())

    def test_cap_is_sixty(self):
        self.assertEqual(instances.DXVK_CONF.strip(), "d3d9.maxFrameRate = 60")


if __name__ == "__main__":
    unittest.main()
