"""Statement-level script scanner for the Yes Man questline (game-free, reads out/questdata only).

Yields one record per statement-level command / assignment and per condition-level function so gaps.json can map every
function to a synccore Effect and an L5 capture/apply status.  Reuses tools/questdata (classify.strip_comment, ctda table).
"""
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'questdata'))
import classify  # noqa: E402
from ctda_funcs import FUNCS  # noqa: E402

ARG = re.compile(r'"[^"]*"|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\S')
NUMTOK = re.compile(r'^[+-]?\d+(\.\d+)?$')
CONTROL = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'foreach', 'loop', 'return', 'begin', 'end', 'scn',
           'scriptname', 'short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var', 'label', 'goto',
           'continue', 'break'}
COND_FUNCS = {f[0].lower(): f[0] for f in FUNCS.values()}
# script-only getters that appear in conditions but are not in the CTDA table
COND_FUNCS.update({'getqr': 'GetQR', 'getqc': 'GetQC', 'getdisabled': 'GetDisabled', 'getdestroyed': 'GetDestroyed',
                   'isactionref': 'IsActionRef', 'getactionref': 'GetActionRef', 'iskiller': 'IsKiller',
                   'getplayerteammate': 'GetPlayerTeammate', 'getsecondspassed': 'GetSecondsPassed',
                   'isimagespaceactive': 'IsImagespaceActive', 'getreputationthreshold': 'GetReputationThreshold',
                   'gettalkedtopc': 'GetTalkedToPC', 'isweaponout': 'IsWeaponOut', 'getweaponanimtype': 'GetWeaponAnimType',
                   'isspelltarget': 'IsSpellTarget', 'getincell': 'GetInCell', 'getdead': 'GetDead',
                   'isinterior': 'IsInInterior', 'isininterior': 'IsInInterior', 'getgamehour': 'GetGameHour'})
PLAYER_NAMES = {'player', 'playerref', '0x14'}


def script_name(src):
    for ln in src.splitlines():
        t = classify.strip_comment(ln).strip().split()
        if len(t) >= 2 and t[0].lower() in ('scn', 'scriptname'):
            return t[1]
    return None


def scan(src):
    """-> list of dict(line, kind, fn, subj, args, text, cond, block)
    kind: cmd (statement-level command), set (assignment), query (function used inside a condition)."""
    out = []
    depth = 0
    block = None
    for n, raw in enumerate(src.splitlines(), 1):
        line = classify.strip_comment(raw).strip()
        if not line:
            continue
        toks = ARG.findall(line)
        t0 = toks[0].lower()
        if t0 == 'begin' and len(toks) > 1:
            block = ' '.join(toks[1:])
            continue
        if t0 == 'end' and len(toks) == 1:
            block = None
            continue
        if t0 in ('if', 'elseif', 'while'):
            if t0 in ('if', 'while'):
                depth += 1
            for i, t in enumerate(toks):
                lc = t.lower()
                if lc in COND_FUNCS and (i == 0 or toks[i - 1] != 'set'):
                    subj = toks[i - 2] if i >= 2 and toks[i - 1] == '.' else None
                    out.append(dict(line=n, kind='query', fn=COND_FUNCS[lc], subj=subj, args=[], text=line[:160],
                                    cond=True, block=block))
            continue
        if t0 == 'else':
            continue
        if t0 in ('endif', 'endwhile', 'loop'):
            depth = max(0, depth - 1)
            continue
        if t0 in CONTROL:
            continue
        cond = depth > 0
        if t0 in ('set', 'let'):
            lhs = toks[1] if len(toks) > 1 else ''
            if len(toks) > 3 and toks[2] == '.':   # set Quest.var to X
                lhs = toks[1] + '.' + toks[3]
            out.append(dict(line=n, kind='set', fn='set', subj=None, args=[lhs], text=line[:160], cond=cond, block=block))
            for i, t in enumerate(toks):
                lc = t.lower()
                if lc in COND_FUNCS and i > 3:
                    subj = toks[i - 2] if i >= 2 and toks[i - 1] == '.' else None
                    out.append(dict(line=n, kind='query', fn=COND_FUNCS[lc], subj=subj, args=[], text=line[:160],
                                    cond=cond, block=block))
            continue
        # statement: [subj.]cmd args  (the ARG tokenizer splits on '.')
        subj, cmd, rest = None, toks[0], toks[1:]
        if len(toks) >= 3 and toks[1] == '.':
            subj, cmd, rest = toks[0], toks[2], toks[3:]
        out.append(dict(line=n, kind='cmd', fn=cmd, subj=subj, args=rest[:6], text=line[:160], cond=cond, block=block))
    return out


def is_player(name):
    return bool(name) and name.lower() in PLAYER_NAMES
