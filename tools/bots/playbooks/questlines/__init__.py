"""Questline playthrough playbooks: one two-copy playbook per questline (plans: questlines/<line>/playthrough.json).

    from playbooks import questlines
    questlines.names()            -> ['ql_goodsprings', 'ql_prologue', ...]
    pb = questlines.make('ql_ncr')
    world, refs = questlines.fake('ql_ncr', n_players=2, bugs=())

Run them with tools/questsweep/run_questlines.py (fake or in a game). They are NOT in playbooks/registry.py on purpose: the T6b suite loops
over that registry with scenario-based fake worlds, and 17 plan-driven playbooks would slow it down for nothing.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from .companions import CompanionsPlaybook
from .driver import Driver, QuestlinePlaybook
from .fake import ALL_BUGS, QL_BUGS, QuestlineFake, build_fake
from .model import Model, Result
from .plan import Plan, available_lines, check_plan, key


def names(repo: Optional[str] = None) -> List[str]:
    return ["ql_" + l for l in available_lines(repo) if True] if repo else ["ql_" + l for l in available_lines()]


def line_of(name: str) -> str:
    if not name.startswith("ql_"):
        raise KeyError(name)
    return name[3:]


def make(name: str, plan: Optional[Plan] = None, repo: Optional[str] = None) -> QuestlinePlaybook:
    line = line_of(name)
    cls = CompanionsPlaybook if line == "companions" else QuestlinePlaybook
    return cls(line, plan, repo)


def fake(name: str, n_players: int = 2, bugs: Iterable[str] = (), plan: Optional[Plan] = None, **kw) -> Tuple[QuestlineFake, dict]:
    from .plan import REPO
    p = plan if plan is not None else Plan.load(line_of(name), REPO)
    return build_fake(p, n_players, set(bugs), **kw)
