"""Game-free check that plugin/avatar/avatar_groups.h agrees with the generated xNVSE group table
(tools/animcheck/animgroups_table.py): every id in kGroupTable carries the same name, and every `kGrpName = id` enum
constant matches the table entry of that name. Exit 0 and print 'avatar groups: OK' when everything agrees.

    python -I tools/avatar/check_groups.py            # check the real header
    python -I tools/avatar/check_groups.py --mutant   # check a deliberately wrong copy: MUST report failures (exit 1)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from animcheck.animgroups_table import NAMES  # noqa: E402

HEADER = ROOT / "plugin" / "avatar" / "avatar_groups.h"


def check_text(text: str) -> list[str]:
    errors: list[str] = []
    table = {int(i): n for i, n in re.findall(r'^\s*\{(\d+),\s*"([A-Za-z0-9_]+)"\},', text, re.M)}
    if not table:
        return ["kGroupTable not found in the header"]
    for gid, name in sorted(table.items()):
        if gid >= len(NAMES):
            errors.append(f"id {gid} ({name}) is outside the xNVSE table (size {len(NAMES)})")
        elif NAMES[gid] != name:
            errors.append(f"id {gid}: header says {name!r}, xNVSE table says {NAMES[gid]!r}")
    enum = re.findall(r"\bkGrp([A-Za-z0-9_]+)\s*=\s*(\d+)", text)
    if not enum:
        errors.append("enum Group constants not found")
    for name, gid in enum:
        gid = int(gid)
        if table.get(gid) != name:
            errors.append(f"enum kGrp{name} = {gid} but the table has {table.get(gid)!r} at that id")
    ids = list(table)
    if ids != sorted(ids):
        errors.append("kGroupTable is not sorted by id")
    used = {n for n, _ in enum}
    for gid, name in table.items():
        if name not in used:
            errors.append(f"table entry {gid} {name} has no kGrp{name} constant")
    return errors


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = HEADER.read_text(encoding="utf-8")
    if "--mutant" in argv:
        # swap two ids: the checker must notice
        text = text.replace('{3, "Forward"}', '{4, "Forward"}', 1)
        errs = check_text(text)
        print(f"avatar groups (mutant): {len(errs)} problems found")
        for e in errs[:5]:
            print("  ", e)
        return 1 if errs else 0   # a mutant that is NOT caught returns 0 = the twin survived
    errs = check_text(text)
    if errs:
        for e in errs:
            print("FAIL", e)
        return 1
    n = len(re.findall(r'^\s*\{\d+,', text, re.M))
    print(f"avatar groups: OK ({n} groups match the xNVSE table)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
