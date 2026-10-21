"""The split-screen plan: who sits where, which pad each seat answers to, and the files that make it so. Pure data and text, no processes.

A seat is one game copy. Seat 0 is the host and player one. Input rule (docs/tools/splitscreen.md "Input facts"): the game reads the keyboard
and mouse for gameplay only while no XInput pad answers, and reads only the pad while one does. So a seat is either
  * keyboard and mouse: pad "none", ghostPad off (XInput reports "not connected"), or
  * a pad: pad N, and a neutral ghost pad when it is unplugged, so the copy never falls back to the keyboard.
By default seat 0 uses keyboard and mouse and the other seats take the connected controllers in order.
"""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_mut  # noqa: E402
from layout import Rect, cells, fit, mode_for, too_small  # noqa: E402

DEFAULT_BASE_ID = 40        # instances 40..43: own folders and pipes (nvcoop-40 ...), so they never meet the dev lanes' 0, 1, 10, 11
DEFAULT_PORT = 27981
RAM_PER_COPY_GB = 2.5       # free memory a further copy needs (docs/tools/splitscreen.md "Minimum specs")


class PlanError(ValueError):
    pass


@dataclass
class Seat:
    index: int
    instance: int
    role: str
    cell: Rect               # the window (and render) rectangle
    slot: Rect               # the part of the screen this seat owns (the cell, or more when the window is a fitted 16:9 inside it)
    pad: Optional[int]       # physical XInput pad 0..3, None = no pad
    kbm: bool
    ghost: bool
    audio: str               # "play" | "mute" | "off" (off = bEnableAudio=0 in the copy's Fallout.ini)

    def as_dict(self) -> dict:
        return {"seat": self.index + 1, "instance": self.instance, "role": self.role, "cell": self.cell.as_dict(), "slot": self.slot.as_dict(),
                "pad": self.pad, "kbm": self.kbm, "ghostPad": self.ghost, "audio": self.audio}


@dataclass
class Plan:
    mode: str
    monitor: int
    area: Rect
    seats: List[Seat]
    port: int = DEFAULT_PORT
    autoload: str = ""
    fps_cap: int = 60
    vsync: bool = False
    dev_inject: bool = False
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"mode": self.mode, "monitor": self.monitor, "area": self.area.as_dict(), "port": self.port, "autoload": self.autoload,
                "fpsCap": self.fps_cap, "vsync": self.vsync, "seats": [s.as_dict() for s in self.seats], "warnings": self.warnings}


# ------------------------------------------------------------------------------------------------------ pads
def connected_pads() -> List[int]:
    """Physical XInput pads (0..3) that answer right now."""
    if os.name != "nt":
        return []
    for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            lib = getattr(ctypes.windll, dll)
            break
        except (OSError, AttributeError):
            continue
    else:
        return []
    out = []
    state = (ctypes.c_byte * 16)()
    for i in range(4):
        if lib.XInputGetState(i, ctypes.byref(state)) == 0:
            out.append(i)
    return out


def parse_pad_spec(spec: str, players: int) -> List[Optional[int]]:
    """"none,0,1" -> [None, 0, 1]. One entry per seat; entries are none or 0..3 and each pad may be used once."""
    parts = [p.strip().lower() for p in spec.split(",")]
    if len(parts) != players:
        raise PlanError(f"--pads needs {players} entries (one per player, 'none' for keyboard and mouse), got {len(parts)}")
    out: List[Optional[int]] = []
    for p in parts:
        if p in ("none", "-", "kbm", ""):
            out.append(None)
        elif p in ("0", "1", "2", "3"):
            out.append(int(p))
        else:
            raise PlanError(f"--pads entry {p!r}: use none or 0..3")
    used = [p for p in out if p is not None]
    if len(used) != len(set(used)) and not split_mut.on("allow_shared_pad"):
        raise PlanError("--pads: two players cannot share one controller")
    return out


def assign_pads(players: int, available: List[int], p1_pad: bool = False, allow_missing: bool = False) -> List[Optional[int]]:
    """Seat 0 keeps keyboard and mouse (unless p1_pad); the other seats take the controllers in order."""
    pool = list(available)
    out: List[Optional[int]] = []
    for seat in range(players):
        if seat == 0 and not p1_pad:
            out.append(None)
        elif pool:
            out.append(pool.pop(0))
        elif allow_missing:
            out.append(None)
        else:
            need = players - (0 if p1_pad else 1)
            raise PlanError(f"{players} players need {need} controller(s) ({'player one on a controller too' if p1_pad else 'player one keeps keyboard and mouse'}), "
                            f"found {len(available)}. Plug in more controllers, or use --allow-missing-pads for idle seats")
    return out


def seat_flags(index: int, pad: Optional[int], audio: str) -> Dict[str, object]:
    kbm = pad is None and index == 0
    ghost = not kbm
    if split_mut.on("no_ghost"):
        ghost = False
    if audio == "auto":
        audio = "play" if index == 0 else "mute"
    return {"kbm": kbm, "ghost": ghost, "audio": audio}


# ------------------------------------------------------------------------------------------------------ plan
def build_plan(players: int, orientation: str, area: Rect, monitor: int, pads: List[Optional[int]], *, base_id: int = DEFAULT_BASE_ID,
               port: int = DEFAULT_PORT, autoload: str = "", audio: str = "auto", fps_cap: int = 60, vsync: bool = False,
               dev_inject: bool = False, gap: int = 0, force_small: bool = False, fit_ratio: str = "fill") -> Plan:
    if not 2 <= players <= 4:
        raise PlanError("players must be 2, 3 or 4")
    if len(pads) != players:
        raise PlanError("one pad entry per player")
    if audio not in ("auto", "play", "mute", "off"):
        raise PlanError("audio must be auto, play, mute or off")
    mode = mode_for(players, orientation)
    slots = cells(mode, area, gap)
    rects = [fit(r, fit_ratio) for r in slots]
    warnings: List[str] = []
    for i, r in enumerate(rects):
        why = too_small(r)
        if why:
            msg = f"player {i + 1}: {r.w}x{r.h} is too small ({'; '.join(why)})"
            if not force_small:
                raise PlanError(msg + ". Use a larger monitor or fewer players (--force-small to try anyway)")
            warnings.append(msg)
    seats = []
    for i in range(players):
        f = seat_flags(i, pads[i], audio)
        seats.append(Seat(index=i, instance=base_id + i, role="host" if i == 0 else "client", cell=rects[i], slot=slots[i], pad=pads[i],
                          kbm=bool(f["kbm"]), ghost=bool(f["ghost"]), audio=str(f["audio"])))
    if players >= 3 and not warnings:
        warnings.append(f"{players} copies of the game share this PC: expect about {players * RAM_PER_COPY_GB:.0f} GB of free memory to be used")
    return Plan(mode=mode, monitor=monitor, area=area, seats=seats, port=port, autoload=autoload, fps_cap=fps_cap, vsync=vsync,
                dev_inject=dev_inject, warnings=warnings)


def ram_verdict(available_gb: float, players: int) -> Optional[str]:
    """None when there is enough free memory for the copies still to start, else the reason."""
    need = RAM_PER_COPY_GB * players
    if available_gb >= need:
        return None
    return f"{available_gb:.1f} GB free, about {need:.1f} GB needed for {players} copies"


# ------------------------------------------------------------------------------------------------------ files
def nvcoop_ini_extra(plan: Plan, seat: Seat) -> Dict[str, Dict[str, str]]:
    """Extra nvcoop.ini entries for one seat (instances.provision(extra_ini=...)). provision itself sets bridge.instance, coop.instanceId,
    coop.pipe and coop.role."""
    sp = {
        "enable": "1", "player": str(seat.index), "players": str(len(plan.seats)),
        "pad": "none" if seat.pad is None else str(seat.pad),
        "kbm": "1" if seat.kbm else "0", "ghostPad": "1" if seat.ghost else "0",
        "audio": "mute" if seat.audio == "mute" else "play", "focusFake": "1",
    }
    if plan.dev_inject:
        sp["devInject"] = "1"
    coop = {"enable": "1", "port": str(plan.port), "host": "127.0.0.1", "token": str(1000 + seat.instance),
            "dialogue": "1", "handshake": "1"}
    if seat.role == "host":
        coop["listen"] = "127.0.0.1"
    return {
        # visible windows: do not minimize, and keep the focus guard on (an unfocused copy must not open the pause menu)
        "bridge": {"minimized": "0", "focusGuard": "1", "multiInstance": "1"},
        "coop": coop,
        "splitscreen": sp,
    }


def ini_edits(plan: Plan, seat: Seat) -> Dict[str, List[tuple]]:
    """Edits for the copy's Fallout.ini / FalloutPrefs.ini: (section, key, value). Applied after provision (which rewrites both files)."""
    c = seat.cell
    prefs = [
        ("Display", "bFull Screen", "0"),
        ("Display", "iSize W", str(c.w)),
        ("Display", "iSize H", str(c.h)),
        ("Display", "iLocation X", str(c.x)),
        ("Display", "iLocation Y", str(c.y)),
        ("Display", "iPresentInterval", "1" if plan.vsync else "0"),
    ]
    fallout = [("General", "bAlwaysActive", "1")]
    if plan.fps_cap > 0:
        fallout.append(("General", "iFPSClamp", str(plan.fps_cap)))
    if seat.audio == "off":
        fallout.append(("Audio", "bEnableAudio", "0"))
    return {"FalloutPrefs.ini": prefs, "Fallout.ini": fallout}


def apply_ini_edits(inst_dir: Path, edits: Dict[str, List[tuple]]) -> None:
    """Write the edits into the copy's own INIs. Refuses anything that is not a private single-link file inside the instance."""
    import instances  # noqa: WPS433  (tools/runner)
    import lowcost    # noqa: WPS433  (tools/pool): its guard refuses My Games, the game dir, hardlinked files and links
    for name, rows in edits.items():
        f = Path(inst_dir) / name
        lowcost.guard(f)
        text = f.read_bytes().decode("latin-1") if f.exists() else ""   # bytes: keep the CRLF line ends
        for sec, key, val in rows:
            text = instances.ini_set(text, sec, key, val)
        f.write_bytes(text.encode("latin-1"))
