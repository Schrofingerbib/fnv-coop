#!/usr/bin/env python3
"""Collect evidence after a game crash: logs + Windows crash record, into a run directory.

    from crashlog import collect_crash_report
    collect_crash_report(r"runs\2026-10-16_x\crash")          # default game dir
CLI: python crashlog.py <dest_dir> [--game-dir DIR]
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_GAME_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas")
LOGS = ["nvse.log", "nvse_loader.log", "nvse_steam_loader.log", "falloutnv_error.log", "falloutnv_havok.log",
        r"Data\NVSE\Plugins\nvcoop.log"]


def _wer_events(minutes):
    ps = ("Get-WinEvent -FilterHashtable @{LogName='Application';Id=1000;StartTime=(Get-Date).AddMinutes(-@MIN@)} "
          "-ErrorAction SilentlyContinue | ? { $_.Message -match 'Fallout' } | select -First 3 | "
          "% { $_.TimeCreated.ToString('s') + ' ' + ($_.Message -replace \"`r?`n\", ' | ') }").replace("@MIN@", str(int(minutes)))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception as e:  # noqa: BLE001
        return [f"(could not query event log: {e})"]


def collect_crash_report(dest, game_dir=None, event_minutes=30, event_query=None):
    """Copy the game's logs into `dest`, add a summary.json (last log lines, Windows crash records,
    any `CRASH code=...` line written by nvcoop.dll). `event_query(minutes)->list[str]` can be injected (tests do) instead of querying the real event log.
    Returns the summary dict."""
    game = Path(game_dir) if game_dir else DEFAULT_GAME_DIR
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied, tails = [], {}
    for rel in LOGS:
        src = game / rel
        if src.exists():
            shutil.copy2(src, dest / src.name)
            copied.append(src.name)
            lines = src.read_text(errors="replace").splitlines()
            tails[src.name] = lines[-8:]
    crash_lines = [l for l in tails.get("nvcoop.log", []) if "CRASH" in l]
    summary = {"collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "game_dir": str(game), "files": copied,
               "nvcoop_crash_lines": crash_lines, "windows_crash_records": (event_query or _wer_events)(event_minutes), "log_tails": tails}
    (dest / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    gd = sys.argv[sys.argv.index("--game-dir") + 1] if "--game-dir" in sys.argv else None
    print(json.dumps(collect_crash_report(sys.argv[1], gd), indent=2)[:4000])
