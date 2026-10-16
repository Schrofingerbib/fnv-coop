"""Script scanner for the Honest Hearts questline: finds every engine command / condition function call.

A "unit" is one script body that runs in the game:
  stage  : a quest stage result script (quests.json stages[].entries[].script)
  info   : a topic-info result script (begin/end) of a quest in this plugin
  quest  : a quest script (scpt type quest)
  object : an object / NPC / trigger / activator script from HonestHearts.esm (scpt type object)
  effect : magic-effect scripts from HonestHearts.esm (scpt type effect)
Location of a call = (unit label, line number inside that script, block, text). Line numbers are 1-based inside the
script source, exactly like the risk.json findings.
"""
import json
import os
import re

import qlib

# short aliases (command table only lists the long names)
ALIASES = {
    'evp': 'EvaluatePackage', 'sifh': 'SetIgnoreFriendlyHits', 'pcb': 'PurgeCellBuffers', 'kill': 'KillActor',
    'resurrect': 'ResurrectActor', 'en': 'Enable', 'dis': 'Disable', 'getqr': 'GetQuestRunning',
    'sv': 'ShowVars', 'ms': 'MoveToMarkerWithFade', 'cios': 'CastImmediateOnSelf', 'gdc': 'GetDeadCount',
}
KEYWORDS = {'if', 'elseif', 'else', 'endif', 'begin', 'end', 'set', 'to', 'return', 'while', 'endwhile', 'let',
            'scn', 'scriptname', 'short', 'long', 'int', 'float', 'ref', 'reference', 'player', 'playerref', 'and', 'or',
            'not', 'gamemode', 'menumode', 'onactivate', 'ontriggerenter', 'ontriggerleave', 'ondeath', 'onhit',
            'onload', 'onadd', 'onequip', 'onunequip', 'onstartcombat', 'oncombatend', 'saytodone', 'onactivate'}
# names that are a function in the engine table but a plain variable / enum word in these scripts
NOT_A_CALL = {'ref', 'to', 'x', 'y', 'z', 'a', 'b'}


def load_names():
    ct = qlib.jload(os.path.join(qlib.REPO, 'plugin', 'hooks', 'tools', 're', 'command_table.json'))
    import ctda_funcs
    names = {}
    for k in ct:
        k = k.strip()
        if k and k[0].isalpha() and ' ' not in k:
            names[k.lower()] = k
    for _i, (n, _a, _b) in ctda_funcs.FUNCS.items():
        names.setdefault(n.lower(), n)
    from lexicon import L
    for k in L:
        names.setdefault(k.lower(), k)
    for a, full in ALIASES.items():
        names[a] = full
    names['moveto'] = 'MoveTo'
    names['setav'] = 'SetAV'
    names['damageav'] = 'DamageAV'
    names['forceav'] = 'ForceAV'
    names['modav'] = 'ModAV'
    names['restoreav'] = 'RestoreAV'
    names['setinchargen'] = 'SetInCharGen'
    return names


NAMES = load_names()
_GLOBALS = None


def globals_set():
    global _GLOBALS
    if _GLOBALS is None:
        _GLOBALS = {g['edid'].lower() for g in qlib.jload(os.path.join(qlib.QD, 'quests.json'))['globals']}
    return _GLOBALS
BLOCK_RE = re.compile(r'(?i)^\s*begin\s+(\w+)')
DECL_RE = re.compile(r'(?i)^\s*(short|long|int|float|ref|reference)\s+(\w+)')
SET_RE = re.compile(r'(?i)^\s*set\s+([\w.]+)\s+to\b')


def scan_source(src):
    """Yield dicts {line, block, fn, text, conditional, subject} for every recognised call in `src`."""
    block = None
    decls = set()
    depth = 0
    out = []
    for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
        line = qlib.strip_comment(raw).strip()
        if not line:
            continue
        m = BLOCK_RE.match(line)
        if m:
            block = m.group(1)
            depth = 0
            continue
        if re.match(r'(?i)^end\s*$', line):
            block = None
            depth = 0
            continue
        m = DECL_RE.match(line)
        if m:
            decls.add(m.group(2).lower())
            continue
        low = line.lower()
        ms = SET_RE.match(line)
        if ms:
            tgt = ms.group(1)
            if '.' in tgt:
                out.append({'line': n, 'block': block, 'fn': 'SetScriptVar', 'text': qlib.clip(line, 160), 'conditional': depth > 0,
                            'subject': tgt.split('.')[0]})
            elif tgt.lower() in globals_set():
                out.append({'line': n, 'block': block, 'fn': 'SetGlobal', 'text': qlib.clip(line, 160), 'conditional': depth > 0,
                            'subject': tgt})
        cond_line = False
        if re.match(r'(?i)^(if|elseif)\b', line):
            cond_line = True
        in_cond = depth > 0 or cond_line
        toks = qlib.TOKEN.findall(line)
        prev = ''
        skip_next = 0
        for ti, t in enumerate(toks):
            tl = t.lower()
            if t.startswith('"'):
                prev = t
                continue
            if tl in KEYWORDS or tl in NOT_A_CALL or tl in decls:
                prev = t
                continue
            # "Set Quest.var to" : the var name is not a call
            if tl in NAMES and not (prev == '.' and ti >= 2 and False):
                # a token directly after a '.' is a member call (subject.Fn); a token before '.' is a subject
                nxt = toks[ti + 1] if ti + 1 < len(toks) else ''
                if nxt == '.':
                    prev = t
                    continue   # subject name that happens to equal a command name
                subject = toks[ti - 2] if prev == '.' and ti >= 2 else None
                out.append({'line': n, 'block': block, 'fn': NAMES[tl], 'text': qlib.clip(line, 160), 'conditional': in_cond,
                            'subject': subject})
            prev = t
        if re.match(r'(?i)^if\b', line):
            depth += 1
        elif re.match(r'(?i)^endif\b', line):
            depth = max(0, depth - 1)
    return out


def units(d):
    """All script units of the questline: list of {label, kind, quest, source}."""
    res = []
    for q in d.quests:
        for st, ei, src in qlib.stage_scripts(q):
            res.append({'label': 'stage %s:%d.%d' % (q['edid'], st, ei), 'kind': 'stage', 'quest': q['edid'],
                        'source': src, 'stage': st})
    for qref, lst in d.infos.items():
        qe = d.edid_of(qref)
        for i in lst:
            for k in ('begin', 'end'):
                sc = i.get(k)
                if sc and sc.get('source'):
                    res.append({'label': 'info %s %s' % (i['ref'], k), 'kind': 'info', 'quest': qe, 'source': sc['source'],
                                'info': i['ref']})
    for ref, s in d.extra['scpt'].items():
        if not ref.startswith(qlib.PLUGIN + ':'):
            continue
        sc = s.get('script') or {}
        if not sc.get('source'):
            continue
        res.append({'label': '%s %s' % (sc.get('type', 'object'), s.get('edid')), 'kind': sc.get('type', 'object'),
                    'quest': None, 'source': sc['source'], 'script': s.get('edid'), 'script_ref': ref})
    return res


def scan_all(d):
    """Returns (calls, unit_count). calls: list of call dicts with unit info merged in."""
    calls = []
    us = units(d)
    for u in us:
        for c in scan_source(u['source']):
            c = dict(c)
            c['unit'] = u['label']
            c['unit_kind'] = u['kind']
            c['quest'] = u.get('quest')
            c['stage'] = u.get('stage')
            c['info'] = u.get('info')
            c['script'] = u.get('script')
            calls.append(c)
    return calls, len(us)
