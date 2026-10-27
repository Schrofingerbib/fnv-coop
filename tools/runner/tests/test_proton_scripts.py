#!/usr/bin/env python3
"""Tests for tools/runner/proton/launch_copy.sh and in_game.sh (docs/tools/proton-deck.md), with mutation twins.

Game-free: a recorder script stands in for Steam's command and for steam-runtime-launch-client, and writes down the arguments,
the working directory and the environment it was started with. Each check returns its problems; every twin (a copy of a script
with one line broken) must give at least one.

    python -I tools/runner/tests/test_proton_scripts.py      (Linux / SteamOS; skips on Windows, where the scripts are not used)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "tools" / "runner" / "proton"
BUS = "--bus-name=com.steampowered.App22380"

RECORDER = r"""#!/bin/bash
python3 -I -c '
import json, os, sys
keys = ("STEAM_COMPAT_LAUNCHER_SERVICE", "NVCOOP_TEST_HELPERS")
rec = {"args": sys.argv[1:], "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in keys}}
with open(os.environ["RECORD"], "w") as f:
    json.dump(rec, f)
' "$@"
"""

# one broken line per twin: (script, old text, new text)
TWINS = {
    "no_exe_swap": ("launch_copy.sh", 'args+=("$copy/nvse_loader.exe")', 'args+=("$a")'),
    "no_launcher_service": ("launch_copy.sh", "export STEAM_COMPAT_LAUNCHER_SERVICE=proton", ":"),
    "no_cd_into_copy": ("launch_copy.sh", 'cd "$copy" || exit 1', ":"),
    "no_test_helpers": ("launch_copy.sh", "export NVCOOP_TEST_HELPERS=1", ":"),
    "py_wrong_directory": ("in_game.sh", '--directory="$repo" -- wine "$py"', '--directory="$here" -- wine "$py"'),
    "py_wrong_bus": ("in_game.sh", 'bus="com.steampowered.App22380"', 'bus="com.steampowered.PressureVessel.LaunchAlongsideSteam"'),
    "start_no_pass_env": ("in_game.sh", " --pass-env=NVCOOP_TEST_HELPERS", ""),
}


@unittest.skipIf(sys.platform == "win32" or not shutil.which("bash"), "the Proton scripts run on Linux / SteamOS only")
class ProtonScripts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="proton-scripts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "checkout"
        self.root = self.tmp / "fnv-instances"            # next to the checkout: the default of instances.py and the scripts
        for n in ("0", "1"):
            (self.root / n).mkdir(parents=True)
            (self.root / n / "nvse_loader.exe").write_bytes(b"")
        self.recorder = self.tmp / "recorder.sh"
        self.recorder.write_text(RECORDER)
        self.recorder.chmod(0o755)
        self.record = self.tmp / "record.json"
        self.install()

    def install(self, twin: str = ""):
        dst = self.repo / "tools" / "runner" / "proton"
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("launch_copy.sh", "in_game.sh"):
            text = (SCRIPTS / name).read_text()
            if twin and TWINS[twin][0] == name:
                old, new = TWINS[twin][1:]
                self.assertIn(old, text, f"twin {twin}: the line it breaks is gone from {name}")
                text = text.replace(old, new)
            (dst / name).write_text(text)
            (dst / name).chmod(0o755)

    def run_script(self, name, args, extra_env=None, wait_record=False):
        if self.record.exists():
            self.record.unlink()
        env = {k: v for k, v in os.environ.items() if k not in ("FNV_INSTANCES_ROOT", "STEAM_COMPAT_LAUNCHER_SERVICE", "NVCOOP_TEST_HELPERS")}
        env.update(RECORD=str(self.record), STEAM_RUNTIME_LAUNCH_CLIENT=str(self.recorder), **(extra_env or {}))
        p = subprocess.run(["bash", str(self.repo / "tools" / "runner" / "proton" / name), *args], env=env, capture_output=True,
                           text=True, cwd=str(self.tmp), timeout=30)
        if wait_record:      # `start` leaves the client running in the background
            t_end = time.time() + 10
            while not self.record.exists() and time.time() < t_end:
                time.sleep(0.05)
        rec = json.loads(self.record.read_text()) if self.record.exists() else None
        return p, rec

    # ---- checks: each returns its problems ----
    def check_launch(self, exe_name="FalloutNVLauncher.exe"):
        steam_exe = f"/steam/steamapps/common/Fallout New Vegas/{exe_name}"
        p, rec = self.run_script("launch_copy.sh", ["0", str(self.recorder), "waitforexitandrun", steam_exe])
        if rec is None:
            return [f"the game command never ran (exit {p.returncode}: {p.stderr.strip()})"]
        problems = []
        want = str(self.root / "0" / "nvse_loader.exe")
        if rec["args"] != ["waitforexitandrun", want]:
            problems.append(f"args {rec['args']}, wanted the Steam exe swapped for {want}")
        if Path(rec["cwd"]) != self.root / "0":
            problems.append(f"started in {rec['cwd']}, not in the copy")
        if rec["env"]["STEAM_COMPAT_LAUNCHER_SERVICE"] != "proton":
            problems.append("STEAM_COMPAT_LAUNCHER_SERVICE is not proton: in_game.sh would find no launch service")
        if rec["env"]["NVCOOP_TEST_HELPERS"] != "1":
            problems.append("NVCOOP_TEST_HELPERS is not 1: the playbooks' scenario reset is refused")
        if not (self.root / "logs" / "steam-launch-0.log").exists():
            problems.append("no steam-launch-0.log")
        return problems

    def check_py(self):
        p, rec = self.run_script("in_game.sh", ["py", "tools/runner/nvbridge.py", "--instance", "0", "ping"])
        if rec is None:
            return [f"the launch client never ran (exit {p.returncode}: {p.stderr.strip()})"]
        want = [BUS, f"--directory={self.repo}", "--", "wine", "C:\\Python311\\python.exe", "-I", "tools/runner/nvbridge.py",
                "--instance", "0", "ping"]
        return [] if rec["args"] == want else [f"args {rec['args']}, wanted {want}"]

    def check_start(self):
        p, rec = self.run_script("in_game.sh", ["start", "1"], wait_record=True)
        if rec is None:
            return [f"the launch client never ran (exit {p.returncode}: {p.stderr.strip()})"]
        problems = []
        copy = self.root / "1"
        want = [BUS, "--pass-env=NVCOOP_TEST_HELPERS", f"--directory={copy}", "--", "wine", str(copy / "nvse_loader.exe")]
        if rec["args"] != want:
            problems.append(f"args {rec['args']}, wanted {want}")
        if rec["env"]["NVCOOP_TEST_HELPERS"] != "1":
            problems.append("NVCOOP_TEST_HELPERS is not 1 for the client")
        return problems

    # ---- the scripts as shipped ----
    def test_launch_swaps_the_steam_exe_for_the_copys_loader(self):
        for exe in ("FalloutNVLauncher.exe", "FalloutNV.exe"):
            with self.subTest(exe):
                self.assertEqual(self.check_launch(exe), [])

    def test_launch_honours_fnv_instances_root(self):
        other = self.tmp / "elsewhere"
        shutil.copytree(self.root, other)
        p, rec = self.run_script("launch_copy.sh", ["0", str(self.recorder), "/x/FalloutNV.exe"], {"FNV_INSTANCES_ROOT": str(other)})
        self.assertEqual(rec["args"], [str(other / "0" / "nvse_loader.exe")], p.stderr)

    def test_launch_refuses_a_copy_that_is_not_there(self):
        p, rec = self.run_script("launch_copy.sh", ["7", str(self.recorder), "/x/FalloutNV.exe"])
        self.assertIsNone(rec, "the game was started without the copy")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("provision", p.stderr)

    def test_py_runs_windows_python_in_the_games_session_from_the_checkout(self):
        self.assertEqual(self.check_py(), [])

    def test_start_runs_the_copys_loader_in_the_games_session(self):
        self.assertEqual(self.check_start(), [])
        self.assertTrue((self.root / "logs" / "start-1.log").exists())

    def test_start_refuses_a_copy_that_is_not_there_and_usage_is_an_error(self):
        p, rec = self.run_script("in_game.sh", ["start", "7"])
        self.assertIsNone(rec)
        self.assertEqual(p.returncode, 2)
        self.assertIn("provision", p.stderr)
        p, _ = self.run_script("in_game.sh", ["frobnicate"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage", p.stderr)

    # ---- twins: one broken line each, the matching check must see it ----
    def test_every_twin_is_caught(self):
        for twin, (script, _old, _new) in TWINS.items():
            check = self.check_launch if script == "launch_copy.sh" else self.check_start if twin.startswith("start") else self.check_py
            with self.subTest(twin):
                self.install(twin)
                try:
                    self.assertTrue(check(), f"twin {twin} was not caught")
                finally:
                    self.install()


if __name__ == "__main__":
    unittest.main(verbosity=2)
