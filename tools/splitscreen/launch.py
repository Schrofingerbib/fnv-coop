#!/usr/bin/env python3
"""One-click split-screen launcher for Fallout: New Vegas co-op (docs/tools/splitscreen.md).

    python -I tools/splitscreen/launch.py up --players 2 --split lr          # left / right on the primary monitor
    python -I tools/splitscreen/launch.py up --players 2 --split tb          # top / bottom
    python -I tools/splitscreen/launch.py up --players 4                     # quad (3 players use three of the four quarters)
    python -I tools/splitscreen/launch.py plan --players 3                   # print the plan, write nothing, start nothing
    python -I tools/splitscreen/launch.py down                               # close the copies this launcher started
    python -I tools/splitscreen/launch.py monitors | status

`up` does, in order: provision one game copy per player (tools/runner/instances.py: hardlinked, own INIs and saves), set each copy's INI to
its cell size, windowed, vsync off, frame cap, write each copy's nvcoop.ini ([coop] host + clients on 127.0.0.1, [splitscreen] seat, pad, audio),
start the host and wait for its bridge, start the clients one at a time, then make every window borderless and keep it on its cell
(SetWindowPos, never activating a window). It never touches Documents\\My Games and never closes a game it did not start.
Player one keeps the keyboard and mouse; the other players use the connected controllers in order (--pads to choose, --p1-pad to put
player one on a controller too).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for sub in (HERE, REPO / "tools" / "runner", REPO / "tools" / "pool", REPO / "tools"):
    sys.path.insert(0, str(sub))

import monitors as mon  # noqa: E402
import plan as planmod  # noqa: E402
import split_mut  # noqa: E402
import winplace  # noqa: E402
from layout import Rect  # noqa: E402

STATE_NAME = "splitscreen-state.json"


# ------------------------------------------------------------------------------------------------------ backend
class Backend:
    """Everything that touches processes, files of the game copies and the bridge. Tests replace it."""

    def __init__(self, a: argparse.Namespace):
        self.a = a

    def root(self) -> Path:
        import instances
        return instances.instances_root()

    def free_ram_gb(self) -> float:
        import gameslot
        return float(gameslot._avail_gb())

    def foreign_game_pids(self, ours: List[int]) -> List[int]:
        import winproc
        return [p for p in winproc.list_pids("FalloutNV.exe") if p not in ours]

    def provision(self, plan: planmod.Plan, seat: planmod.Seat) -> dict:
        import instances
        dll, core = instances.plugin_args(self.a.plugin_dll, self.a.core_dll)
        extra = instances.ini_overrides(self.a.ini)
        instances.merge_ini(extra, planmod.nvcoop_ini_extra(plan, seat))
        meta = instances.provision(seat.instance, role=seat.role, nvse_dir=Path(self.a.nvse_dir) if self.a.nvse_dir else None,
                                   plugin_dll=dll, core_dll=core, extra_ini=extra, autoload=plan.autoload)
        planmod.apply_ini_edits(Path(meta["dir"]), planmod.ini_edits(plan, seat))
        return meta

    def spawn(self, seat: planmod.Seat) -> None:
        import instances
        import run
        d = instances.instance_dir(seat.instance)
        loader = d / "nvse_loader.exe"
        exe = loader if loader.exists() else d / "FalloutNV.exe"
        env = dict(os.environ)
        env.update(run.STEAM_ENV)
        env["NVCOOP_INSTANCE"] = str(seat.instance)
        import subprocess
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 4          # SW_SHOWNOACTIVATE: the window appears without taking the keyboard focus from the player
        run.spawn_detached([str(exe)], str(d), env, si)

    def bridge_pid(self, seat: planmod.Seat, wait_s: float) -> Optional[dict]:
        """Connect to the copy's bridge and return its ping (it names the game's real pid). None when it did not come up."""
        import nvbridge
        try:
            nv = nvbridge.NvBridge.connect(seat.instance, wait_s=wait_s)
        except nvbridge.BridgeError:
            return None
        try:
            return nv.ping()
        except nvbridge.BridgeError:
            return None
        finally:
            nv.close()

    def pid_alive(self, pid: int) -> bool:
        import winproc
        return winproc.pid_alive(pid)

    def is_game(self, pid: int) -> bool:
        import winproc
        return pid in winproc.list_pids("FalloutNV.exe")

    def kill(self, pid: int) -> None:
        import winproc
        winproc.kill_pid(pid)


# ------------------------------------------------------------------------------------------------------ state
def state_path(root: Path) -> Path:
    return Path(root) / STATE_NAME


def load_state(root: Path) -> dict:
    try:
        return json.loads(state_path(root).read_text())
    except (OSError, ValueError):
        return {"pids": {}}


def save_state(root: Path, st: dict) -> None:
    Path(root).mkdir(parents=True, exist_ok=True)
    tmp = state_path(root).with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    # Windows: a virus scanner or the search indexer can hold the fresh file for a moment (WinError 5 / 32). Retry briefly.
    for attempt in range(20):
        try:
            os.replace(tmp, state_path(root))
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


# ------------------------------------------------------------------------------------------------------ steps
def make_plan(a: argparse.Namespace, monitors: List[mon.Monitor], pads_now: List[int]) -> planmod.Plan:
    m = mon.choose(monitors, a.monitor)
    area = m.work if a.workarea else m.rect
    if a.pads:
        pads = planmod.parse_pad_spec(a.pads, a.players)
    else:
        if getattr(a, "dev_inject", False):
            # --dev-inject: split.inject gives every copy a synthetic pad 0..3, so every seat has a pad without a real controller.
            # In game 2026-10-21 without controllers the seats stayed idle and the smoke playbook's pad step failed (it expects pad 0).
            pads_now = list(range(4))
        pads = planmod.assign_pads(a.players, pads_now, p1_pad=a.p1_pad, allow_missing=a.allow_missing_pads)
    return planmod.build_plan(a.players, a.split, area, m.index, pads, base_id=a.base_id, port=a.port, autoload=a.autoload, audio=a.audio,
                              fps_cap=a.fps_cap, vsync=a.vsync, dev_inject=a.dev_inject, gap=a.gap, force_small=a.force_small, fit_ratio=a.fit)


def print_plan(plan: planmod.Plan, out: Callable[[str], None] = print) -> None:
    out(f"layout {plan.mode} on monitor {plan.monitor} ({plan.area.w}x{plan.area.h}+{plan.area.x}+{plan.area.y})")
    for s in plan.seats:
        who = "keyboard + mouse" if s.kbm else (f"controller {s.pad + 1}" if s.pad is not None else "no controller (idle)")
        out(f"  player {s.index + 1}: {s.cell.w}x{s.cell.h} at {s.cell.x},{s.cell.y}  instance {s.instance}  {s.role}  {who}  sound {s.audio}")
    for w in plan.warnings:
        out(f"  note: {w}")


def up(a: argparse.Namespace, plan: planmod.Plan, be: Backend, api: Optional[winplace.WindowApi] = None,
       log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> int:
    root = be.root()
    st = load_state(root)
    alive = [int(p) for p in st.get("pids", {}).values() if be.pid_alive(int(p)) and be.is_game(int(p))]
    if alive:
        log(f"split-screen is already running (pids {alive}). Close it first: launch.py down")
        return 2
    foreign = be.foreign_game_pids([])
    if foreign and not a.allow_foreign:
        log(f"Fallout: New Vegas is already running (pids {foreign}). Close it first, or pass --allow-foreign "
            "(each extra copy needs its own memory).")
        return 2
    bad = planmod.ram_verdict(be.free_ram_gb(), len(plan.seats))
    if bad and not a.force:
        log(f"not enough free memory: {bad}. Close other programs, use fewer players, or pass --force.")
        return 2

    print_plan(plan, log)
    for s in plan.seats:
        log(f"provisioning player {s.index + 1} (instance {s.instance}) ...")
        be.provision(plan, s)
    if a.no_launch:
        log("provisioned; nothing started (--no-launch).")
        return 0

    pids: Dict[int, int] = {}
    st = {"pids": {}, "plan": plan.as_dict()}
    order = list(reversed(plan.seats)) if split_mut.on("clients_first") else plan.seats      # the host must be up before the clients
    for s in order:
        log(f"starting player {s.index + 1} ({s.role}) ...")
        be.spawn(s)
        ping = be.bridge_pid(s, wait_s=a.boot_timeout)
        if not ping or not int(ping.get("pid", 0) or 0):
            log(f"player {s.index + 1}: the game did not come up within {a.boot_timeout:.0f} s (see Data\\NVSE\\Plugins\\nvcoop.log in the copy). Stopping.")
            _stop(be, st, root, log)
            return 3
        pids[s.index] = int(ping["pid"])
        st["pids"][str(s.instance)] = pids[s.index]
        save_state(root, st)
        sleep(a.stagger)
    log("all copies are up; placing the windows ...")

    keeper = winplace.Keeper(api or winplace.Win32Api(), {pids[s.index]: s.cell for s in plan.seats}, log)
    stop_now = lambda: not any(be.pid_alive(p) for p in pids.values())  # noqa: E731
    if not keeper.run(stop_now, interval=getattr(a, "keep_interval", 0.5), until_all_ok_s=getattr(a, "settle_s", 2.0), timeout_s=a.place_timeout):
        if stop_now():
            log("a game window closed before the windows were placed.")
        else:
            log(f"the windows were not on their cells after {a.place_timeout:.0f} s: {keeper.step()}")
        return 4
    log("READY. Every window is on its cell.")
    for line in tester_steps(plan):
        log(line)
    if a.no_keep:
        return 0
    log("Keeping the windows in place. Press Ctrl+C here (or run launch.py down) when you are done.")
    try:
        keeper.run(stop_now, interval=2 * getattr(a, "keep_interval", 0.5))
    except KeyboardInterrupt:
        log("stopped keeping the windows; the games keep running (launch.py down closes them).")
    return 0


def tester_steps(plan: planmod.Plan) -> List[str]:
    steps = ["Next:",
             "  1. Player one (the host window): load a saved game or start a new one with the keyboard and mouse.",
             "  2. The other windows join the host by themselves once the host world is loaded (127.0.0.1)."
             if plan.autoload == "" else f"  2. All windows load the save '{plan.autoload}' by themselves and join the host."]
    for s in plan.seats[1:]:
        if s.pad is not None:
            steps.append(f"  - Player {s.index + 1}: use controller {s.pad + 1} (only that controller moves this window).")
        else:
            steps.append(f"  - Player {s.index + 1}: no controller assigned; plug one in and restart, or pass --pads.")
    steps.append("  Click a window to give it the keyboard and mouse; the other windows keep running and keep answering their controllers.")
    return steps


def _stop(be: Backend, st: dict, root: Path, log: Callable[[str], None]) -> int:
    n = 0
    for inst, pid in list((st.get("pids") or {}).items()):
        pid = int(pid)
        if be.pid_alive(pid) and (be.is_game(pid) or split_mut.on("kill_any_pid")):      # never a reused pid
            be.kill(pid)
            log(f"closed player copy instance {inst} (pid {pid})")
            n += 1
        st["pids"].pop(inst, None)
    save_state(root, st)
    return n


def down(be: Backend, log: Callable[[str], None] = print) -> int:
    root = be.root()
    st = load_state(root)
    n = _stop(be, st, root, log)
    log(f"closed {n} game copy(ies)." if n else "nothing of ours is running.")
    return 0


# ------------------------------------------------------------------------------------------------------ cli
def default_root() -> str:
    return str(REPO.parent / "fnv-instances-split")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("up", "plan"):
        p = sub.add_parser(name, help="start split-screen" if name == "up" else "print the plan only")
        p.add_argument("--players", type=int, default=2, choices=(2, 3, 4))
        p.add_argument("--split", default="lr", choices=("lr", "tb"), help="2 players only: left/right or top/bottom")
        p.add_argument("--monitor", default="primary", help="primary or a monitor number (see: launch.py monitors)")
        p.add_argument("--workarea", action="store_true", help="avoid the taskbar (default: the whole monitor)")
        p.add_argument("--gap", type=int, default=0, help="pixels between the windows")
        p.add_argument("--fit", default="fill", help="fill (default) or W:H such as 16:9: size each window to that aspect ratio inside its part of the screen (the HUD is designed for 16:9 and 4:3)")
        p.add_argument("--pads", default="", help="one entry per player, none or 0..3, e.g. none,0,1 (default: player one keyboard and mouse, the rest take the connected controllers in order)")
        p.add_argument("--p1-pad", action="store_true", help="player one uses the first controller instead of keyboard and mouse")
        p.add_argument("--allow-missing-pads", action="store_true", help="seats without a controller stay idle instead of failing")
        p.add_argument("--audio", default="auto", choices=("auto", "play", "mute", "off"), help="auto: only player one has sound; off also disables the other copies' audio device in their INI")
        p.add_argument("--autoload", default="", help="save name (without .fos) every copy loads by itself")
        p.add_argument("--port", type=int, default=planmod.DEFAULT_PORT)
        p.add_argument("--base-id", type=int, default=planmod.DEFAULT_BASE_ID, help="first instance id (folder and pipe nvcoop-<id>)")
        p.add_argument("--fps-cap", type=int, default=60, help="frame cap per copy, 0 = engine default")
        p.add_argument("--vsync", action="store_true")
        p.add_argument("--plugin-dll", default=None)
        p.add_argument("--core-dll", default=None)
        p.add_argument("--nvse-dir", default="")
        p.add_argument("--ini", action="append", default=[], help="extra nvcoop.ini entry SECTION.KEY=VALUE for every copy")
        p.add_argument("--dev-inject", action="store_true", help="test only: register split.inject (synthetic controller) for the smoke playbook")
        p.add_argument("--force-small", action="store_true")
        p.add_argument("--force", action="store_true", help="start even when free memory looks too low")
        p.add_argument("--allow-foreign", action="store_true")
        p.add_argument("--no-launch", action="store_true", help="provision only")
        p.add_argument("--no-keep", action="store_true", help="return once the windows are placed")
        p.add_argument("--boot-timeout", type=float, default=240.0)
        p.add_argument("--stagger", type=float, default=3.0)
        p.add_argument("--place-timeout", type=float, default=180.0)
        p.add_argument("--json", action="store_true", help="plan: print JSON")
    sub.add_parser("down")
    sub.add_parser("status")
    sub.add_parser("monitors")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    os.environ.setdefault("FNV_INSTANCES_ROOT", default_root())
    if a.cmd == "monitors":
        for m in mon.list_monitors():
            print(m)
        return 0
    if a.cmd == "down":
        return down(Backend(a))
    if a.cmd == "status":
        be = Backend(a)
        st = load_state(be.root())
        rows = {inst: {"pid": int(pid), "alive": be.pid_alive(int(pid))} for inst, pid in st.get("pids", {}).items()}
        print(json.dumps({"root": str(be.root()), "copies": rows}, indent=1))
        return 0
    try:
        plan = make_plan(a, mon.list_monitors(), planmod.connected_pads())
    except (planmod.PlanError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.cmd == "plan":
        if a.json:
            print(json.dumps(plan.as_dict(), indent=1))
        else:
            print_plan(plan)
        return 0
    return up(a, plan, Backend(a))


if __name__ == "__main__":
    sys.exit(main())
