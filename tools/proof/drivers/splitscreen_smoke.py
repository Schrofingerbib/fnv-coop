#!/usr/bin/env python3
"""prove.py piece for `splitscreen_smoke`: start the split-screen copies with a synthetic controller, run the smoke playbook, close them.

    python -I tools/proof/drivers/splitscreen_smoke.py [--players 2] [--split lr] [--autoload autosave] [--no-movement]

It is the sequence of docs/tools/splitscreen.md section 2, steps 4 and 6, as one command:
  1. take a game slot (tools/gameslot.py, owner proof-split) so the copies never meet another lane's;
  2. `launch.py up --players N --dev-inject --autoload SAVE --pads none,0,... --no-keep`: provisions N copies in ../fnv-instances-split
     (ids 40..), starts them, places the windows (the launcher itself refuses when any other FalloutNV.exe runs or the RAM is short);
  3. `smoke_playbook.py --players N --pads ... --movement`: windows, hooks, pads, unfocused, movement, with a synthetic pad per copy;
  4. ALWAYS, last: `launch.py down` (closes only the copies the launcher started) and the slot is released.
Exit: the playbook's code (0 ALL OK, 1 a step failed); 2 when the PC is not ready (a game runs, no RAM, slot busy, a copy never came up
on the bridge); 1 when the copies did not boot or the windows did not reach their cells. The tests (no game) are in tools/proof/drivers/tests.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
import drv_common as D  # noqa: E402

PY = [sys.executable, "-I", "-X", "utf8"]
SLOT_OWNER = "proof-split"
LAUNCH = "tools/splitscreen/launch.py"
SMOKE = "tools/splitscreen/smoke_playbook.py"
GAMESLOT = "tools/gameslot.py"
Call = Callable[[List[str], float], Tuple[int, str]]


def real_call(argv: List[str], timeout: float) -> Tuple[int, str]:
    full = PY + [str(REPO / argv[0])] + argv[1:]
    try:
        cp = subprocess.run(full, cwd=REPO, capture_output=True, text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        return -1, f"timed out after {timeout:.0f} s\n{(e.stdout or b'')[-400:] if isinstance(e.stdout, str) else ''}"
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def pads_for(players: int) -> str:
    """Player one keyboard and mouse, the others synthetic physical pads 0, 1, 2 (no controller needs to be plugged in)."""
    return ",".join(["none"] + [str(i) for i in range(players - 1)])


def up_argv(players: int, split: str, autoload: str) -> List[str]:
    a = [LAUNCH, "up", "--players", str(players), "--split", split, "--pads", pads_for(players), "--no-keep"]
    if D.MUTANT != "no_dev_inject":
        a.append("--dev-inject")
    if autoload:
        a += ["--autoload", autoload]
    return a


def smoke_argv(players: int, split: str, movement: bool) -> List[str]:
    return [SMOKE, "--players", str(players), "--split", split, "--pads", pads_for(players)] + (["--movement"] if movement else [])


def last_line(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[-1][:200] if lines else "no output"


def run(a, call: Call = real_call, out: Callable[[str], None] = lambda s: print(s, flush=True)) -> int:
    code, text = call([GAMESLOT, "acquire", "--owner", SLOT_OWNER, "--ttl", "1800", "--slots", "1", "--wait", "120"], 200)
    if code != 0:
        return _env(out, f"game slot busy: {last_line(text)}")
    try:
        code, text = call(up_argv(a.players, a.split, a.autoload), 900)
        out(text.rstrip())
        if code == 2:      # the launcher refused before it started anything: a game runs, or too little RAM
            return _env(out, "split-screen launcher refused: " + last_line(text))
        if code != 0 and D.MUTANT != "ignore_up_failure":
            out(f"FAIL up: the split-screen copies did not come up with their windows placed (launch.py exit {code}): {last_line(text)}")
            return 1
        code, text = call(smoke_argv(a.players, a.split, a.movement), 600)
        out(text.rstrip())
        if code == 3:
            return _env(out, "smoke playbook: " + last_line(text))
        if code == 0:
            out("PASS splitscreen smoke: windows, hooks, pads, unfocused" + (", movement" if a.movement else ""))
            return 0
        if not any(l.startswith("FAIL") for l in text.splitlines()):
            out(f"FAIL smoke playbook exit {code}: {last_line(text)}")
        return 1
    finally:
        if D.MUTANT != "no_down":
            _code, text = call([LAUNCH, "down"], 120)
            out("down: " + last_line(text))
        call([GAMESLOT, "release", "--owner", SLOT_OWNER], 60)


def _env(out: Callable[[str], None], why: str) -> int:
    return D.env_exit(why, out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--players", type=int, default=2, choices=(2, 3, 4))
    ap.add_argument("--split", default="lr", choices=("lr", "tb"))
    ap.add_argument("--autoload", default="autosave", help="save every copy loads by itself (movement needs a loaded world)")
    ap.add_argument("--no-movement", dest="movement", action="store_false")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
