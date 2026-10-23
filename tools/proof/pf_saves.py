"""Saves for every copy a test uses (pure file work, no game).

A scenario starts by loading its start save (usually "autosave") on every copy. The first copies (instances 0 and 1) were set up by hand with that save
and its prepared twin (`nvcoop_<save>_ready.*` plus `nvcoop_prepared.json`, tools/scenarios/prepare_save.py); a copy that `run.py provision` made
later (instance 2 and 3, the second pair) has an empty Saves folder, so the jump died with "loadSave: timed out waiting for save to load".

seed_saves() copies, into a copy's Saves folder, every file it lacks from a donor copy: a donor of the same role first (host copies are even
instances, client copies odd ones, the way pairs are laid out), then any other. It never overwrites a file, never touches a donor, skips a file the
game wrote in the last few seconds, and copies through a temp name so a crash leaves no half file. If no donor has the plain start save, the last
resort is the owner's own save (read only), the way tools/runner/multismoke.py seeds its copies.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

# test-only: one deliberate bug (tools/proof/tests/test_proof.py, PROOF_MUTANT)
MUTANT = os.environ.get("PROOF_MUTANT", "")

RECENT_S = 5.0                                  # a file the game touched this recently may be half written
START_SAVE = "autosave.fos"                     # what every scenario in scenarios/ starts from


def donor_order(i: int, k: int, candidates: Sequence[int]) -> List[int]:
    """Donors for instance i, which is copy number k (0 = host) of its test: same role first, nearest instance first."""
    want_even = (k == 0)                        # host copies are even instances, clients odd
    cand = [j for j in candidates if j != i]
    same = [j for j in cand if (j % 2 == 0) == want_even]
    other = [j for j in cand if (j % 2 == 0) != want_even]
    key = lambda j: (abs(j - i), j)             # noqa: E731
    return sorted(same, key=key) + sorted(other, key=key)


def seed_saves(instances: Sequence[int], saves_dir_of: Callable[[int], Path], all_instances: Sequence[int] = tuple(range(4)),
               owner_saves: Optional[Callable[[], Optional[Path]]] = None, now: Callable[[], float] = time.time) -> List[str]:
    """Fill the Saves folders of `instances` from donors; returns what was copied ("<instance>: <file> <- <donor|owner>")."""
    done: List[str] = []
    for k, i in enumerate(instances):
        dst = Path(saves_dir_of(i))
        dst.mkdir(parents=True, exist_ok=True)
        order = [] if MUTANT == "saves_not_seeded" else donor_order(i, k, all_instances)
        same_n = len([j for j in order if (j % 2 == 0) == (k == 0)])
        for n, j in enumerate(order):
            if n >= same_n and (dst / START_SAVE).exists():
                break                           # a donor of the other role only fills a copy that still has no start save at all
            src = Path(saves_dir_of(j))
            if not src.is_dir():
                continue
            for f in sorted(src.iterdir()):
                if not f.is_file() or f.name.endswith(".tmp") or (dst / f.name).exists():
                    continue
                if now() - f.stat().st_mtime < RECENT_S:
                    continue
                _copy(f, dst / f.name)
                done.append(f"{i}: {f.name} <- {j}")
        if owner_saves is not None and not (dst / START_SAVE).exists():
            own = owner_saves()
            if own is not None and (Path(own) / START_SAVE).is_file():
                _copy(Path(own) / START_SAVE, dst / START_SAVE)
                done.append(f"{i}: {START_SAVE} <- owner")
    return done


def _copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)                      # copy2 keeps the mtime: prepare_save.py judges a prepared save by the source save's size and time
    os.replace(tmp, dst)


def missing_start_save(instances: Sequence[int], saves_dir_of: Callable[[int], Path]) -> List[int]:
    """The instances whose Saves folder still lacks the start save (nothing to load: the scenario would time out)."""
    return [i for i in instances if not (Path(saves_dir_of(i)) / START_SAVE).is_file()]
