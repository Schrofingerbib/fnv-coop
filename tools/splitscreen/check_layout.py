#!/usr/bin/env python3
"""HUD and menu layout check for split-screen sizes (half and quarter of a screen). Reports problems; changes nothing.

    python -I tools/splitscreen/check_layout.py --screen 1920x1080 --players 2 --split lr
    python -I tools/splitscreen/check_layout.py --screen 2560x1440 --players 4 [--fit 16:9]
    python -I tools/splitscreen/check_layout.py --players 2 --instances-root ..\\fnv-instances-split   # also reads each copy's INI files
    python -I tools/splitscreen/check_layout.py --players 2 --live                                     # also asks each running copy (split.status)
    python -I tools/splitscreen/check_layout.py --selftest

What it checks (docs/tools/splitscreen.md "HUD and menus at small sizes"). The game cannot be asked whether a menu fits, so the size rules are
HEURISTICS with named thresholds below; the INI and live checks compare what the copy was told with what the layout needs.
  size     a cell below MIN (640x360): ERROR; below COMFORT (960x540): WARN (small text); UI height factor against 720p
  aspect   outside 1.2 .. 2.4: WARN (the HUD is designed for 4:3 .. 16:9; a tall or very wide cell clips or stretches it) -> try --fit 16:9
  layout   cells overlapping each other or leaving the monitor: ERROR
  ini      per copy: bFull Screen must be 0, iSize W/H must equal the cell, iLocation must be the cell origin, vsync with several copies,
           and the UI scale settings (the safe zone margins eat a larger share of a small window)
  live     per running copy: the real client size equals the cell, the window is borderless and not minimized, the plugin is active
Exit code 1 when there is an ERROR (and with --strict also on a WARN). Prints ALL OK when nothing is reported.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for sub in (HERE, REPO / "tools" / "runner"):
    sys.path.insert(0, str(sub))

import plan as planmod  # noqa: E402
import split_mut  # noqa: E402
from layout import MIN_H, MIN_W, Rect  # noqa: E402

COMFORT_W, COMFORT_H = 960, 540        # below this the text of dialogue and the Pip-Boy is small
ASPECT_LO, ASPECT_HI = 1.2, 2.4        # the HUD and menus are designed for 4:3 .. 16:9 (1.33 .. 1.78)
REF_H = 720                            # UI height factor is reported against a 720p window
FACTOR_WARN = 0.6                      # a window shorter than 60% of 720p (432 px) renders text at under 60% of the design size
SAFEZONE_WARN_PCT = 5.0                # the combined margin on one axis above this share of the window is wasted at small sizes

# UI scale related INI keys. The game has no HUD scale setting; these are the ones that change how much of a small window the HUD can use.
# status: believed = expected in stock New Vegas INIs (not verified in game), see tools/pool/lowcost.py for the same wording.
UI_KEYS = [
    ("Interface", "fSafeZoneX", "believed", "horizontal margin (percent of the width) the HUD keeps clear on each side"),
    ("Interface", "fSafeZoneY", "believed", "vertical margin (percent of the height) the HUD keeps clear on each side"),
    ("Display", "fDefaultFOV", "believed", "field of view; a tall or wide cell changes how it looks"),
]


@dataclass
class Finding:
    level: str      # ERROR | WARN | INFO
    code: str
    msg: str

    def __str__(self) -> str:
        return f"{self.level:5} {self.code}: {self.msg}"


def _ini_get(text: str, section: str, key: str) -> Optional[str]:
    import instances
    return instances.ini_get(text, section, key)


def ui_factor(h: int) -> float:
    return h / REF_H


def check_cells(seats: List[planmod.Seat], area: Rect) -> List[Finding]:
    out: List[Finding] = []
    n = len(seats)
    for s in seats:
        c = s.cell
        who = f"player {s.index + 1} ({c.w}x{c.h})"
        if (c.w < MIN_W or c.h < MIN_H) and not split_mut.on("no_size_check"):
            out.append(Finding("ERROR", "size", f"{who} is below {MIN_W}x{MIN_H}: menus and dialogue do not fit"))
        elif c.w < COMFORT_W or c.h < COMFORT_H:
            out.append(Finding("WARN", "size", f"{who} is below {COMFORT_W}x{COMFORT_H}: text is small"))
        f = ui_factor(c.h)
        if f < FACTOR_WARN:
            out.append(Finding("WARN", "ui-scale", f"{who}: the UI is drawn at {f:.2f} of its 720p size (under {FACTOR_WARN})"))
        ratio = c.w / c.h
        if (ratio < ASPECT_LO or ratio > ASPECT_HI) and not split_mut.on("no_aspect_check"):
            out.append(Finding("WARN", "aspect", f"{who} has aspect {ratio:.2f}:1, outside {ASPECT_LO}..{ASPECT_HI}: the HUD is clipped or stretched. "
                                                 f"Try --fit 16:9 (a 16:9 window inside the player's part of the screen)"))
        if not c.inside(area) and not split_mut.on("no_bounds_check"):
            out.append(Finding("ERROR", "bounds", f"{who} at {c.x},{c.y} leaves the monitor area"))
    for i in range(n):
        for j in range(i + 1, n):
            if seats[i].cell.overlaps(seats[j].cell) and not split_mut.on("no_overlap_check"):
                out.append(Finding("ERROR", "overlap", f"player {i + 1} and player {j + 1} overlap"))
    return out


def check_ini(seat: planmod.Seat, prefs: str, fallout: str, n_copies: int) -> List[Finding]:
    out: List[Finding] = []
    who = f"player {seat.index + 1}"
    c = seat.cell

    def num(v: Optional[str]) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except ValueError:
            return None

    if (_ini_get(prefs, "Display", "bFull Screen") or "").strip() != "0":
        out.append(Finding("ERROR", "ini-fullscreen", f"{who}: bFull Screen is not 0 in FalloutPrefs.ini (a full-screen copy covers the others)"))
    for key, want in (("iSize W", c.w), ("iSize H", c.h), ("iLocation X", c.x), ("iLocation Y", c.y)):
        got = num(_ini_get(prefs, "Display", key))
        if got is None:
            out.append(Finding("WARN" if key.startswith("iLocation") else "ERROR", "ini-size", f"{who}: {key} is missing in FalloutPrefs.ini (want {want})"))
        elif int(got) != want:
            out.append(Finding("ERROR" if key.startswith("iSize") else "WARN", "ini-size", f"{who}: {key}={int(got)} but the cell needs {want}"))
    if n_copies > 1 and (_ini_get(prefs, "Display", "iPresentInterval") or "").strip() == "1":
        out.append(Finding("WARN", "ini-vsync", f"{who}: iPresentInterval=1 (vsync) with {n_copies} copies: each window waits for the screen, frame rates drop"))
    if (_ini_get(fallout, "General", "bAlwaysActive") or "").strip() != "1":
        out.append(Finding("ERROR", "ini-active", f"{who}: bAlwaysActive is not 1 in Fallout.ini: the copy stops when it loses focus"))
    for sec, key, status, why in UI_KEYS:
        v = num(_ini_get(fallout, sec, key)) if sec != "Display" else num(_ini_get(prefs, sec, key) or _ini_get(fallout, sec, key))
        if v is None:
            continue       # not set: the engine default applies, nothing to report
        if key == "fSafeZoneX" and 2 * v * 1.0 > SAFEZONE_WARN_PCT * 2 and c.w < 1280:
            out.append(Finding("WARN", "ui-safezone", f"{who}: {key}={v:g} keeps {2 * v:g}% of a {c.w}-wide window clear ({why})"))
        if key == "fSafeZoneY" and 2 * v > SAFEZONE_WARN_PCT * 2 and c.h < 720:
            out.append(Finding("WARN", "ui-safezone", f"{who}: {key}={v:g} keeps {2 * v:g}% of a {c.h}-high window clear ({why})"))
    return out


def check_live(seat: planmod.Seat, st: Optional[dict]) -> List[Finding]:
    who = f"player {seat.index + 1}"
    if not st:
        return [Finding("ERROR", "live", f"{who}: no split.status answer (copy not running, or built without plugin/splitscreen)")]
    out: List[Finding] = []
    if not st.get("enabled"):
        out.append(Finding("ERROR", "live-enabled", f"{who}: [splitscreen] is off in the copy ({st.get('configError') or 'enable=0'})"))
    w = st.get("window") or {}
    c = seat.cell
    if not w.get("found"):
        out.append(Finding("ERROR", "live-window", f"{who}: the game window was not found"))
        return out
    if (w.get("clientW"), w.get("clientH")) != (c.w, c.h):
        out.append(Finding("ERROR", "live-client", f"{who}: the client area is {w.get('clientW')}x{w.get('clientH')}, the cell is {c.w}x{c.h} "
                                                   "(DPI scaling, or the game re-sized itself)"))
    if (w.get("clientX"), w.get("clientY")) != (c.x, c.y):
        out.append(Finding("WARN", "live-position", f"{who}: the client area starts at {w.get('clientX')},{w.get('clientY')}, the cell at {c.x},{c.y}"))
    if not w.get("borderless"):
        out.append(Finding("WARN", "live-borderless", f"{who}: the window still has a frame"))
    if w.get("minimized"):
        out.append(Finding("ERROR", "live-minimized", f"{who}: the window is minimized"))
    return out


def analyze(plan: planmod.Plan, ini: Optional[Dict[int, Dict[str, str]]] = None, live: Optional[Dict[int, Optional[dict]]] = None) -> List[Finding]:
    out = check_cells(plan.seats, plan.area)
    for s in plan.seats:
        if ini and s.index in ini:
            out += check_ini(s, ini[s.index].get("FalloutPrefs.ini", ""), ini[s.index].get("Fallout.ini", ""), len(plan.seats))
        if live is not None:
            out += check_live(s, live.get(s.index))
    return out


def read_instance_inis(root: Path, plan: planmod.Plan) -> Dict[int, Dict[str, str]]:
    out: Dict[int, Dict[str, str]] = {}
    for s in plan.seats:
        d = Path(root) / str(s.instance)
        row = {}
        for name in ("FalloutPrefs.ini", "Fallout.ini"):
            f = d / name
            if f.exists():
                row[name] = f.read_text(encoding="latin-1")
        if row:
            out[s.index] = row
    return out


def read_live(plan: planmod.Plan) -> Dict[int, Optional[dict]]:
    import nvbridge
    out: Dict[int, Optional[dict]] = {}
    for s in plan.seats:
        try:
            with nvbridge.NvBridge.connect(s.instance, wait_s=2.0) as nv:
                out[s.index] = nv.call("split.status", timeout_s=10)
        except Exception:
            out[s.index] = None
    return out


# ------------------------------------------------------------------------------------------------------ selftest
def selftest() -> int:
    fails = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            fails.append(name)

    def make(players, split, w, h, fit="fill", pads=None):
        pads = pads or [None] + list(range(players - 1))
        return planmod.build_plan(players, split, Rect(0, 0, w, h), 1, pads, force_small=True, fit_ratio=fit)

    def codes(fs, level=None):
        return {f.code for f in fs if level is None or f.level == level}

    p = make(4, "lr", 1920, 1080)
    check("quad 1080p is clean", not [f for f in analyze(p) if f.level == "ERROR"] and "aspect" not in codes(analyze(p)))
    p = make(2, "lr", 1920, 1080)
    check("2 players left/right on 16:9 is tall and narrow", "aspect" in codes(analyze(p)))
    p = make(2, "tb", 1920, 1080)
    check("2 players top/bottom on 16:9 is very wide", "aspect" in codes(analyze(p)))
    p = make(2, "lr", 1920, 1080, fit="16:9")
    check("--fit 16:9 removes the aspect warning", "aspect" not in codes(analyze(p)))
    p = make(4, "lr", 1280, 720)
    check("quad on 720p: 640x360 is allowed but small", "size" in codes(analyze(p), "WARN") and "ui-scale" in codes(analyze(p)))
    p = make(4, "lr", 1024, 576)
    check("quad on a tiny screen is an error", "size" in codes(analyze(p), "ERROR"))
    # layout damage
    p = make(2, "lr", 1920, 1080)
    p.seats[1].cell = Rect(900, 0, 960, 1080)
    check("overlap is an error", "overlap" in codes(analyze(p), "ERROR"))
    p.seats[1].cell = Rect(1500, 0, 960, 1080)
    check("leaving the monitor is an error", "bounds" in codes(analyze(p), "ERROR"))
    # ini
    p = make(2, "lr", 1920, 1080)
    good_prefs = "[Display]\r\nbFull Screen=0\r\niSize W=960\r\niSize H=1080\r\niLocation X=0\r\niLocation Y=0\r\niPresentInterval=0\r\n"
    good_fo = "[General]\r\nbAlwaysActive=1\r\n"
    good = {0: {"FalloutPrefs.ini": good_prefs, "Fallout.ini": good_fo}}
    check("a matching ini is clean", not [f for f in analyze(p, good) if f.level == "ERROR"])
    bad = {0: {"FalloutPrefs.ini": good_prefs.replace("bFull Screen=0", "bFull Screen=1"), "Fallout.ini": good_fo}}
    check("full screen is an error", "ini-fullscreen" in codes(analyze(p, bad), "ERROR"))
    bad = {0: {"FalloutPrefs.ini": good_prefs.replace("iSize W=960", "iSize W=640"), "Fallout.ini": good_fo}}
    check("a wrong iSize is an error", "ini-size" in codes(analyze(p, bad), "ERROR"))
    bad = {0: {"FalloutPrefs.ini": good_prefs.replace("iPresentInterval=0", "iPresentInterval=1"), "Fallout.ini": good_fo}}
    check("vsync warns with several copies", "ini-vsync" in codes(analyze(p, bad), "WARN"))
    bad = {0: {"FalloutPrefs.ini": good_prefs, "Fallout.ini": "[General]\r\nbAlwaysActive=0\r\n"}}
    check("bAlwaysActive=0 is an error", "ini-active" in codes(analyze(p, bad), "ERROR"))
    bad = {0: {"FalloutPrefs.ini": good_prefs, "Fallout.ini": good_fo + "[Interface]\r\nfSafeZoneX=12\r\n"}}
    check("a big safe zone warns on a narrow window", "ui-safezone" in codes(analyze(p, bad), "WARN"))
    # live
    live_ok = {"enabled": True, "window": {"found": True, "clientW": 960, "clientH": 1080, "clientX": 0, "clientY": 0, "borderless": True, "minimized": False}}
    check("a matching live window is clean", not [f for f in analyze(p, live={0: live_ok, 1: None}) if f.code.startswith("live") and f.level == "ERROR" and "player 1" in f.msg])
    check("a missing answer is an error", "live" in codes(analyze(p, live={0: live_ok, 1: None}), "ERROR"))
    wrong = dict(live_ok, window=dict(live_ok["window"], clientW=1440))
    check("a wrong client size is an error", "live-client" in codes(analyze(p, live={0: wrong, 1: live_ok}), "ERROR"))
    if fails:
        print("check_layout selftest FAILED: " + ", ".join(fails))
        return 1
    print("check_layout selftest: ALL OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--screen", default="1920x1080", help="monitor size WxH")
    ap.add_argument("--players", type=int, default=2, choices=(2, 3, 4))
    ap.add_argument("--split", default="lr", choices=("lr", "tb"))
    ap.add_argument("--fit", default="fill")
    ap.add_argument("--gap", type=int, default=0)
    ap.add_argument("--instances-root", default="", help="read FalloutPrefs.ini and Fallout.ini of the copies in this folder")
    ap.add_argument("--base-id", type=int, default=planmod.DEFAULT_BASE_ID)
    ap.add_argument("--live", action="store_true", help="ask the running copies (split.status over the bridge)")
    ap.add_argument("--strict", action="store_true", help="a WARN also fails")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    try:
        w, h = (int(x) for x in a.screen.lower().split("x"))
        pads = [None] + list(range(a.players - 1))
        plan = planmod.build_plan(a.players, a.split, Rect(0, 0, w, h), 1, pads, base_id=a.base_id, gap=a.gap, force_small=True, fit_ratio=a.fit)
    except (ValueError, planmod.PlanError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ini = read_instance_inis(Path(a.instances_root), plan) if a.instances_root else None
    live = read_live(plan) if a.live else None
    found = analyze(plan, ini, live)
    if a.json:
        print(json.dumps([f.__dict__ for f in found], indent=1))
    else:
        for s in plan.seats:
            print(f"player {s.index + 1}: {s.cell.w}x{s.cell.h} at {s.cell.x},{s.cell.y} (UI height factor {ui_factor(s.cell.h):.2f})")
        for f in found:
            print(f)
        if not found:
            print("ALL OK")
    bad = [f for f in found if f.level == "ERROR" or (a.strict and f.level == "WARN")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
