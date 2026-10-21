"""The questlines that get a stage sweep config and a two-copy playthrough playbook.

One entry per line. `sweep` is the lane's own single-player sweep config (questlines/<slug>/sweep.json, written by the lane in its
own shape); the generator only reads the quest list, the order and the exclusions out of it, and takes every stage, effect and
objective from out/questdata. `goodsprings` has no lane sweep (its reference sweep is out/questdata/m1_goodsprings.json) so its
quest list is fixed here.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional, Tuple


class Line(NamedTuple):
    slug: str                      # directory under questlines/ and the playbook suffix (ql_<slug>)
    title: str
    category: str                  # main | side | companions | dlc
    sweep: Optional[str]           # questlines/<sweep>/sweep.json, or None
    quests: Tuple[str, ...] = ()   # fixed quest list (when `sweep` is None)
    playbook: str = ""             # filled below: ql_<slug>


def _l(slug, title, cat, sweep=None, quests=()):
    return Line(slug, title, cat, sweep, tuple(quests), "ql_" + slug)


LINES: List[Line] = [
    _l("goodsprings", "Goodsprings: chargen, the first day, the ghost town fight", "main", None,
       ("VCG00", "VCG01", "VCG02", "VCG03", "VMS16", "VMS16b")),
    _l("prologue", "Main story: prologue (They Went That-a-Way, Primm, Nipton, Novac, Boulder City, Benny lead-in)", "main", "prologue"),
    _l("act2", "Main story: Ring-a-Ding-Ding and Act 2 (shared by every path)", "main", "act2"),
    _l("house", "Main story: Mr. House path", "main", "house"),
    _l("ncr", "Main story: NCR path", "main", "ncr"),
    _l("legion", "Main story: Caesar's Legion path", "main", "legion"),
    _l("yesman", "Main story: Yes Man / Independent Vegas path", "main", "yesman"),
    _l("towns", "Side quests: Goodsprings, Primm, Nipton, Novac, Boulder City, Nellis, Camp Golf and other towns", "side", "towns"),
    _l("strip", "Side quests: Freeside and the Strip", "side", "strip"),
    _l("factions", "Side quests: faction quests (Brotherhood, Boomers, Khans, Followers, ...)", "side", "factions"),
    _l("leftovers", "Side quests: everything not owned by another line", "side", "leftovers"),
    _l("systems", "Side quests: systems, events, timers, radio, achievements", "side", "systems"),
    _l("companions", "Companions: recruit, follower caps, dismiss, personal quests", "companions", "systems"),
    _l("deadmoney", "DLC: Dead Money", "dlc", "deadmoney"),
    _l("honesthearts", "DLC: Honest Hearts", "dlc", "honesthearts"),
    _l("owb", "DLC: Old World Blues", "dlc", "owb"),
    _l("lonesomeroad", "DLC: Lonesome Road", "dlc", "lonesomeroad"),
]

BY_SLUG = {l.slug: l for l in LINES}
