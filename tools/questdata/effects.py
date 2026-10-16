"""Static expected-effects oracle.

For every quest-stage result script and every INFO begin/end script, derive the effects the script will cause in
the synccore Effect vocabulary (synccore/include/fnvcoop/effect.h), with FormRefs resolved through the script's own
SCRO table. Outputs: effects.json.gz (per-script rich format with line/conditional/dynamic bookkeeping),
effects_native.json.gz (L7 native oracle format, see to_native) and vocab_gaps.md.

Per effect: {type, ...fields, line, conditional, dynamic}
  conditional  inside if/elseif/else/while, or after a `return` nested in an if
  dynamic      some argument is not a literal / not resolvable (value unknown statically)
Oracle contract for runtime capture: captured effects ⊇ {e : not e.conditional and not e.dynamic}.
`unmodeled` lists statement-level calls outside the vocabulary (fn, line, conditional).

Usage: python effects.py [--in DIR] [--out DIR] [--data GAME_DATA_DIR]
"""
import argparse
import collections
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify  # noqa: E402
import extract  # noqa: E402
import forms as forms_mod  # noqa: E402
from lexicon import L  # noqa: E402

SCHEMA = 1
PLAYER_REF = 'FalloutNV.esm:000014'
VOCAB = ['SetStage', 'SetObjective', 'SetGlobal', 'EnableRef', 'DisableRef', 'AddItem', 'RemoveItem', 'ModActorValue',
         'ModReputation', 'ModKarma', 'StartQuest', 'StopQuest', 'MoveRef', 'KillActor', 'ResurrectActor']
ORACLE_ONLY = ['CompleteQuest', 'FailQuest', 'ClearObjective', 'SetFactionRank', 'AddPerk']  # not in effect.h; L7 observes them
# mirrors plugin/snapshot/tools/effect_oracle.AV_CODES (lowercase keys); a test checks the two stay in sync
AV_CODES = {'strength': 5, 'perception': 6, 'endurance': 7, 'charisma': 8, 'intelligence': 9, 'agility': 10,
            'luck': 11, 'xp': 24, 'barter': 32, 'bigguns': 33, 'energyweapons': 34, 'explosives': 35,
            'lockpick': 36, 'medicine': 37, 'meleeweapons': 38, 'repair': 39, 'science': 40, 'guns': 41,
            'sneak': 42, 'speech': 43, 'survival': 44, 'unarmed': 45}
SKIP_WORDS = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'return', 'set', 'let', 'short', 'int', 'long',
              'float', 'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname', 'begin', 'end', 'foreach',
              'loop', 'continue', 'break', 'label', 'goto'}
NUM = re.compile(r'^[+-]?\d+(\.\d+)?$')
ARG = re.compile(r'"[^"]*"|\S+')
TARGETING = {'moveto', 'movetomarker', 'addscriptpackage', 'removescriptpackage', 'startcombat', 'setenemy', 'setally',
             'follow', 'activate', 'sayto', 'startconversation', 'setowner', 'setownership', 'placeatme'}
AV_MODE = {'modav': 'mod', 'modactorvalue': 'mod', 'forceav': 'force', 'forceactorvalue': 'force', 'setav': 'set',
           'setactorvalue': 'set', 'damageav': 'damage', 'damageactorvalue': 'damage', 'restoreav': 'restore',
           'restoreactorvalue': 'restore'}


def num(tok):
    if tok is not None and NUM.match(tok):
        v = float(tok)
        return (int(v) if v == int(v) else v), False
    return None, True


class Ctx:
    def __init__(self, refs_by_name, globals_by_name, local_vars=None):
        self.local_vars = local_vars or set()  # quest-script variables (writes are shared quest-var writes)
        self.names = refs_by_name          # lower edid -> ref
        self.globals = globals_by_name     # lower edid -> ref
        self.touched = set()

    def ref(self, tok):
        """-> (ref or None, dynamic)"""
        if tok is None:
            return None, True
        t = tok.strip('"()').lower()
        if t in ('player', 'playerref'):
            return PLAYER_REF, False
        r = self.names.get(t)
        if r:
            self.touched.add(r)
            return r, False
        return None, True


def analyse(src, ctx):
    """Return (effects, unmodeled, touched_refs) for one script source."""
    effects, unm = [], []
    depth = 0
    after_cond_return = False
    for n, raw in enumerate(src.splitlines(), 1):
        line = classify.strip_comment(raw).strip()
        if not line:
            continue
        toks = ARG.findall(line)
        t0 = toks[0].lower()
        if t0 in ('if', 'while', 'foreach'):
            depth += 1
            continue
        if t0 in ('elseif', 'else'):
            continue
        if t0 in ('endif', 'endwhile', 'loop'):
            depth = max(0, depth - 1)
            continue
        if t0 == 'return':
            if depth == 0:
                break
            after_cond_return = True
            continue
        if t0 in ('begin', 'end', 'scn', 'scriptname', 'short', 'int', 'long', 'float', 'ref', 'reference',
                  'string_var', 'array_var', 'label', 'goto', 'continue', 'break'):
            continue
        cond = depth > 0 or after_cond_return
        if t0 in ('set', 'let'):
            _handle_set(toks, n, line, cond, ctx, effects, unm)
            continue
        subj, cmd = None, toks[0]
        if '.' in toks[0] and not NUM.match(toks[0]):
            subj, cmd = toks[0].split('.', 1)
        lc = cmd.lower()
        args = [a for a in toks[1:]]
        # an object counts as "touched" when a statement acts on it (subject) or targets it with an actor/object command
        before = set(ctx.touched)
        e = _model(lc, subj, args, ctx)
        ctx.touched = before
        for tok in ([subj] if subj else []) + (args[:1] if lc in TARGETING else []):
            r = ctx.names.get(tok.strip('"()').lower())
            if r:
                ctx.touched.add(r)
        if e is None:
            unm.append({'fn': cmd, 'line': n, 'conditional': cond, 'text': line[:120]})
        else:
            for x in (e if isinstance(e, list) else [e]):
                x['line'], x['conditional'] = n, cond
                effects.append(x)
    return effects, unm


def _handle_set(toks, n, line, cond, ctx, effects, unm):
    if len(toks) < 4:
        return
    lhs = toks[1]
    if toks[2].lower() != 'to':
        return
    expr = toks[3:]
    if '.' in lhs and not lhs.startswith('.'):
        unm.append({'fn': 'SetQuestVar', 'line': n, 'conditional': cond, 'text': line[:120]})
        return
    if lhs.lower() in ctx.local_vars:
        unm.append({'fn': 'SetQuestVar', 'line': n, 'conditional': cond, 'text': line[:120], 'local': True})
        return
    g = ctx.globals.get(lhs.lower())
    if g:
        v, dyn = num(expr[0]) if len(expr) == 1 else (None, True)
        effects.append({'type': 'SetGlobal', 'global': g, 'value': v, 'dynamic': dyn, 'line': n, 'conditional': cond})
        ctx.touched.add(g)


def _model(lc, subj, args, ctx):
    a = args + [None] * 4
    if lc == 'setstage':
        q, d1 = ctx.ref(a[0])
        s, d2 = num(a[1])
        return {'type': 'SetStage', 'quest': q, 'stage': s, 'dynamic': d1 or d2}
    if lc in ('setobjectivedisplayed', 'setobjectivecompleted'):
        q, d1 = ctx.ref(a[0])
        o, d2 = num(a[1])
        v, d3 = num(a[2]) if a[2] is not None else (1, False)
        kind = 'displayed' if lc.endswith('displayed') else 'completed'
        return {'type': 'SetObjective', 'quest': q, 'objective': o, 'kind': kind, 'value': v,
                'flags': (1 if kind == 'displayed' else 2) if v else 0, 'dynamic': d1 or d2 or d3}
    if lc == 'setglobalvalue':
        g, d1 = ctx.ref(a[0])
        v, d2 = num(a[1])
        return {'type': 'SetGlobal', 'global': g, 'value': v, 'dynamic': d1 or d2}
    if lc in ('enable', 'disable'):
        r, d = ctx.ref(subj) if subj else (None, False)
        return {'type': 'EnableRef' if lc == 'enable' else 'DisableRef', 'ref': r if subj else 'self', 'dynamic': d}
    if lc in ('additem', 'additemns', 'additemhealthpercent', 'removeitem'):
        t, d0 = ctx.ref(subj) if subj else (None, False)
        it, d1 = ctx.ref(a[0])
        c, d2 = num(a[1]) if a[1] is not None else (1, False)
        return {'type': 'RemoveItem' if lc == 'removeitem' else 'AddItem', 'item': it, 'count': c,
                'to': t if subj else 'self', 'dynamic': d0 or d1 or d2}
    if lc in AV_MODE:
        t, d0 = ctx.ref(subj) if subj else (None, False)
        v, d2 = num(a[1])
        return {'type': 'ModActorValue', 'av_name': a[0], 'mode': AV_MODE[lc], 'delta': v, 'to': t if subj else 'self',
                'dynamic': d0 or d2 or a[0] is None}
    if lc in ('modreputation', 'addreputation', 'setreputation'):
        r, d1 = ctx.ref(a[0])
        rest = [x for x in args[1:]]
        return {'type': 'ModReputation', 'faction': r, 'mode': 'set' if lc == 'setreputation' else 'mod',
                'args': rest, 'dynamic': True if d1 or any(num(x)[1] for x in rest) else False}
    if lc in ('rewardkarma', 'playerrewardkarma'):
        v, d = num(a[0])
        return {'type': 'ModKarma', 'delta': v, 'dynamic': d}
    if lc in ('startquest', 'stopquest'):
        q, d = ctx.ref(a[0])
        return {'type': 'StartQuest' if lc == 'startquest' else 'StopQuest', 'quest': q, 'dynamic': d}
    if lc in ('moveto', 'movetomarker'):
        r, d0 = ctx.ref(subj) if subj else (None, False)
        dest, d1 = ctx.ref(a[0])
        return {'type': 'MoveRef', 'ref': r if subj else 'self', 'dest': dest, 'offset': [x for x in args[1:4] if x],
                'dynamic': d0 or d1}
    if lc == 'rewardxp':
        v, d = num(a[0])
        return {'type': 'ModActorValue', 'av_name': 'XP', 'mode': 'mod', 'delta': v, 'to': PLAYER_REF, 'dynamic': d}
    if lc in ('completequest', 'failquest'):
        q, d = ctx.ref(a[0])
        return {'type': 'CompleteQuest' if lc == 'completequest' else 'FailQuest', 'quest': q, 'dynamic': d}
    if lc in ('addtofaction', 'setfactionrank') and subj and ctx.ref(subj)[0] == PLAYER_REF:
        f, d1 = ctx.ref(a[0])
        r, d2 = num(a[1]) if a[1] is not None else (0, False)
        return {'type': 'SetFactionRank', 'faction': f, 'rank': r, 'dynamic': d1 or d2}
    if lc == 'removefromfaction' and subj and ctx.ref(subj)[0] == PLAYER_REF:
        f, d1 = ctx.ref(a[0])
        return {'type': 'RemoveFromFaction', 'faction': f, 'dynamic': d1}
    if lc == 'addperk' and subj and ctx.ref(subj)[0] == PLAYER_REF:
        p_, d1 = ctx.ref(a[0])
        r, d2 = num(a[1]) if a[1] is not None else (1, False)
        return {'type': 'AddPerk', 'perk': p_, 'rank': r, 'dynamic': d1 or d2}
    if lc in ('kill', 'killactor'):
        r, d0 = ctx.ref(subj) if subj else (None, False)
        return {'type': 'KillActor', 'actor': r if subj else 'self', 'dynamic': d0}
    if lc == 'resurrect':
        r, d0 = ctx.ref(subj) if subj else (None, False)
        return {'type': 'ResurrectActor', 'actor': r if subj else 'self', 'dynamic': d0}
    return None


def effect_key(e):
    """Identity of an effect for oracle comparison (drops bookkeeping fields)."""
    return json.dumps({k: v for k, v in e.items() if k not in ('line', 'conditional', 'dynamic')}, sort_keys=True)


def oracle_missing(static_effects, captured_effects):
    """Unconditional, static effects the oracle expects that the runtime capture did not report
    (captured contains every unconditional static effect). Returns the missing ones (empty list = pass)."""
    have = {effect_key(c) for c in captured_effects}
    return [e for e in static_effects if not e['conditional'] and not e['dynamic'] and effect_key(e) not in have]


def _conv(e):
    """Rich effect -> (native effect dict | None, bucket) with bucket in ok / unobservable / unresolved."""
    t = e['type']
    if e['dynamic']:
        return _conv_dynamic(e)
    P = PLAYER_REF
    if t == 'SetStage':
        return {'type': t, 'quest': e['quest'], 'stage': e['stage']}, 'ok'
    if t == 'SetObjective':
        if e['value']:
            return {'type': t, 'quest': e['quest'], 'objective': e['objective'], 'flags': e['flags']}, 'ok'
        return {'type': 'ClearObjective', 'quest': e['quest'], 'objective': e['objective'],
                'flags': 1 if e['kind'] == 'displayed' else 2}, 'ok'
    if t == 'SetGlobal':
        return {'type': t, 'global': e['global'], 'value': e['value']}, 'ok'
    if t in ('EnableRef', 'DisableRef'):
        return ({'type': t, 'ref': e['ref']}, 'ok') if e['ref'] not in ('self', None) else (None, 'unobservable')
    if t in ('AddItem', 'RemoveItem'):
        if e['to'] == P and e['count'] and e['count'] > 0:
            return {'type': t, 'item': e['item'], 'count': e['count']}, 'ok'  # `to` omitted: wildcard (recipient policy)
        return None, 'unobservable'
    if t == 'ModActorValue':
        code = AV_CODES.get((e['av_name'] or '').lower())
        if e['to'] == P and e['mode'] == 'mod' and code and e['delta']:
            return {'type': t, 'av': code, 'delta': e['delta']}, 'ok'
        return None, 'unobservable'
    if t == 'ModReputation':
        # vanilla: GetReputation Rep 1 = fame (positive), 0 = infamy (negative); types 2/3 (e.g. total/other) have no
        # snapshot field -> unobservable. Always conditional until L5 verifies the in-game delta equals the argument.
        a = e['args']
        if e['mode'] == 'mod' and len(a) >= 2 and not (num(a[0])[1] or num(a[1])[1]) and int(num(a[0])[0]) in (0, 1):
            kind, amt = int(num(a[0])[0]), int(num(a[1])[0])
            return {'type': t, 'faction': e['faction'], 'fame': amt if kind == 1 else 0,
                    'infamy': amt if kind == 0 else 0, 'to': 'shared'}, 'dynamic'
        return None, 'unobservable'
    if t == 'ModKarma':
        return {'type': t, 'delta': e['delta'], 'to': 'shared'}, 'ok'
    if t in ('StartQuest', 'StopQuest', 'CompleteQuest', 'FailQuest'):
        return {'type': t, 'quest': e['quest']}, 'ok'
    if t == 'MoveRef':
        return ({'type': t, 'ref': e['ref']}, 'ok') if e['ref'] not in ('self', None) else (None, 'unobservable')
    if t in ('KillActor', 'ResurrectActor'):
        return ({'type': t, 'actor': e['actor']}, 'ok') if e['actor'] not in ('self', None) else (None, 'unobservable')
    if t == 'SetFactionRank':
        return {'type': t, 'faction': e['faction'], 'rank': e['rank']}, 'ok'
    if t == 'AddPerk':
        return {'type': t, 'perk': e['perk'], 'rank': e['rank']}, 'ok'
    return None, 'unobservable'  # RemoveFromFaction etc.: no snapshot field


_DYN_FIELDS = {  # rich key -> native key, per type (unknown/None values are dropped = wildcard in L7's matcher)
    'SetStage': {'quest': 'quest', 'stage': 'stage'}, 'SetGlobal': {'global': 'global', 'value': 'value'},
    'StartQuest': {'quest': 'quest'}, 'StopQuest': {'quest': 'quest'}, 'CompleteQuest': {'quest': 'quest'},
    'FailQuest': {'quest': 'quest'}, 'EnableRef': {'ref': 'ref'}, 'DisableRef': {'ref': 'ref'},
    'KillActor': {'actor': 'actor'}, 'ResurrectActor': {'actor': 'actor'}, 'MoveRef': {'ref': 'ref'},
    'SetFactionRank': {'faction': 'faction', 'rank': 'rank'}, 'AddPerk': {'perk': 'perk', 'rank': 'rank'},
    'AddItem': {'item': 'item', 'count': 'count'}, 'RemoveItem': {'item': 'item', 'count': 'count'},
    'ModActorValue': {'delta': 'delta'}, 'ModKarma': {'delta': 'delta'},
    'ModReputation': {'faction': 'faction'}}


def _conv_dynamic(e):
    """Effect with an unknown argument -> native effect with only the known fields (goes to `conditional`)."""
    t = e['type']
    if t == 'SetObjective':
        flags = e.get('flags')
        n = {'type': 'SetObjective' if e.get('value') != 0 else 'ClearObjective', 'quest': e['quest'],
             'objective': e['objective'], 'flags': flags}
    elif t in _DYN_FIELDS:
        n = {'type': t, **{nk: e[rk] for rk, nk in _DYN_FIELDS[t].items()}}
        if t in ('AddItem', 'RemoveItem') and e['to'] != PLAYER_REF:
            return None, 'unobservable'
        if t == 'ModActorValue':
            code = AV_CODES.get((e.get('av_name') or '').lower())
            if e['to'] != PLAYER_REF or not code:
                return None, 'unobservable'
            n['av'] = code
        if t in ('ModKarma', 'ModReputation'):
            n['to'] = 'shared'
    else:
        return None, 'unobservable'
    ident = [k for k in n if k != 'type']
    n = {k: v for k, v in n.items() if v is not None and v != 'self'}
    if t in ('EnableRef', 'DisableRef', 'MoveRef', 'KillActor', 'ResurrectActor') and len(n) == 1:
        return None, 'unresolved'  # no subject at all: would match any ref
    if not ident:
        return None, 'unresolved'
    return n, 'dynamic'


def _bucket(effs, force_conditional=False):
    out = {'unconditional': [], 'conditional': []}
    extra = {'unobservable': [], 'unresolved': []}
    for e in effs:
        n, b = _conv(e)
        if b == 'dynamic':
            if n not in out['conditional']:
                out['conditional'].append(n)
            continue
        if b != 'ok':
            extra[b].append({k: v for k, v in e.items() if k != 'line'})
            continue
        tgt = out['conditional' if (e['conditional'] or force_conditional) else 'unconditional']
        if n not in tgt:
            tgt.append(n)
    out.update({k: v for k, v in extra.items() if v})
    return out


def to_native(scripts, d):
    """Rich per-script effects -> L7 native oracle (plugin/snapshot/tools/check_stage.load_native).
    A stage with several log entries, or an entry with conditions, makes its effects conditional."""
    by_id = {s['id']: s for s in scripts}
    quests = {}
    for q in d['quests']:
        if not q['stages']:
            continue
        name = q.get('edid') or f'Q{q["formid"]}'
        stages = {}
        for st in q['stages']:
            multi = len(st['entries']) > 1
            acc = {'unconditional': [], 'conditional': []}
            extra = {'unobservable': [], 'unresolved': []}
            for ei, en in enumerate(st['entries']):
                forced = multi or bool(en['conditions'])
                effs = list(by_id.get(f'stage:{q["ref"]}:{st["index"]}:{ei}', {}).get('effects', []))
                for fl, typ in (('complete_quest', 'CompleteQuest'), ('fail_quest', 'FailQuest')):
                    if fl in en['flags']:
                        effs.append({'type': typ, 'quest': q['ref'], 'conditional': False, 'dynamic': False})
                b = _bucket(effs, forced)
                for k in ('unconditional', 'conditional'):
                    acc[k] += [x for x in b[k] if x not in acc[k]]
                for k in extra:
                    extra[k] += b.get(k, [])
            acc['conditional'] = [x for x in acc['conditional'] if x not in acc['unconditional']]
            acc.update({k: v for k, v in extra.items() if v})
            stages[str(st['index'])] = acc
        quests[name] = {'ref': q['ref'], 'stages': stages}
    infos = {}
    for s in scripts:
        if s['kind'] not in ('info_begin', 'info_end'):
            continue
        e = infos.setdefault(s['info'], {'quest': s.get('quest'), 'topic': s.get('topic')})
        e['begin' if s['kind'] == 'info_begin' else 'end'] = _bucket(s['effects'])
    return {'schema': SCHEMA, 'quests': quests, 'infos': infos}


def name_map(sc, refinfo):
    m = {}
    for r in sc.get('refs', ()):
        e = refinfo.get(r)
        if e and e.get('edid'):
            m[e['edid'].lower()] = r
    return m


def run(d, infos_by_quest, infos_all, refinfo):
    globals_by_name = {g['edid'].lower(): g['ref'] for g in d['globals'] if g.get('edid')}
    scripts, quest_touched = [], {}
    gap = collections.defaultdict(lambda: {'scripts': set(), 'occurrences': 0, 'conditional': 0})

    def do(kind, sid, sc, meta, emit=True):
        ctx = Ctx(name_map(sc, refinfo), globals_by_name)
        eff, unm = analyse(sc['source'], ctx)
        if emit:
            scripts.append({'id': sid, 'kind': kind, **meta, 'effects': eff, 'unmodeled': unm})
            for u in unm:
                g = gap[u['fn'].lower() if u['fn'] != 'SetQuestVar' else 'SetQuestVar']
                g['scripts'].add(sid)
                g['occurrences'] += 1
                g['conditional'] += u['conditional']
                g.setdefault('display', u['fn'])
        return ctx.touched

    for q in d['quests']:
        t = set()
        sd = q.get('script_data')
        if sd and sd.get('source'):
            t |= do('quest_script', None, sd, {}, emit=False)
        for s in q['stages']:
            for ei, e in enumerate(s['entries']):
                sc = e.get('script')
                if sc and sc['source'].strip():
                    t |= do('stage', f'stage:{q["ref"]}:{s["index"]}:{ei}', sc,
                            {'quest': q['ref'], 'quest_edid': q.get('edid'), 'stage': s['index'], 'entry': ei})
        quest_touched[q['ref']] = t
    for i in infos_all:
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc['source'].strip():
                t = do(f'info_{k}', f'info:{i["ref"]}:{k}', sc,
                       {'info': i['ref'], 'quest': i.get('quest'), 'topic': i.get('topic')})
                if i.get('quest') in quest_touched:
                    quest_touched[i['quest']] |= t
    return scripts, quest_touched, gap


def collect_refs(d, infos_all):
    refs = set()
    for q in d['quests']:
        if q.get('script_data'):
            refs.update(q['script_data']['refs'])
        for s in q['stages']:
            for e in s['entries']:
                if e.get('script'):
                    refs.update(e['script']['refs'])
    for i in infos_all:
        for k in ('begin', 'end'):
            if i.get(k):
                refs.update(i[k]['refs'])
    return {r for r in refs if r}


def gaps_md(scripts, gap, n_total):
    md = ['# Effect-vocabulary coverage', '',
          f'{n_total} stage/INFO result scripts analysed. Vocabulary: {", ".join(VOCAB)}.', '',
          '## Modeled effects found', '', '| Effect | total | unconditional & static |', '|---|---|---|']
    cnt = collections.Counter()
    cert = collections.Counter()
    for s in scripts:
        for e in s['effects']:
            cnt[e['type']] += 1
            if not e['conditional'] and not e['dynamic']:
                cert[e['type']] += 1
    for t in VOCAB + ORACLE_ONLY + ['RemoveFromFaction']:
        md.append(f'| {t} | {cnt[t]} | {cert[t]} |')
    md += ['', '## Calls NOT in the Effect vocabulary, ranked by scripts using them', '',
           '| # | Function | scripts | occurrences | conditional occ. | lexicon category |', '|---|---|---|---|---|---|']
    rows = sorted(gap.items(), key=lambda kv: (-len(kv[1]['scripts']), -kv[1]['occurrences']))
    for i, (k, g) in enumerate(rows[:60], 1):
        cat = L.get(k, ('-',))[0]
        md.append(f'| {i} | {g["display"]} | {len(g["scripts"])} | {g["occurrences"]} | {g["conditional"]} | {cat} |')
    return '\n'.join(md) + '\n'


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, '..', '..', 'out', 'questdata')
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='indir', default=default)
    ap.add_argument('--out', default=default)
    ap.add_argument('--data', default=extract.DEFAULT_DATA)
    a = ap.parse_args(argv)
    d, infos_by_quest = classify.load(a.indir)
    infos_all = []
    with gzip.open(os.path.join(a.indir, 'infos.jsonl.gz'), 'rb') as f:
        for ln in f:
            infos_all.append(json.loads(ln))
    import blocks as blocks_mod
    scpts = json.loads(gzip.open(os.path.join(a.indir, 'scripts.json.gz')).read().decode('utf-8'))
    refs = collect_refs(d, infos_all) | blocks_mod.script_refs(scpts)
    paths = [os.path.join(a.data, n) for n in extract.PLUGINS if os.path.exists(os.path.join(a.data, n))]
    cache = os.path.join(a.indir, 'refnames.json.gz')
    refinfo = None
    if os.path.exists(cache):
        with gzip.open(cache, 'rb') as f:
            refinfo = json.loads(f.read().decode('utf-8'))
        if not refs <= set(refinfo):
            refinfo = None
    if refinfo is None:
        refinfo = {r: {k: v for k, v in o.items() if k in ('sig', 'edid')} for r, o in forms_mod.lookup(paths, refs).items()}
        with gzip.open(cache, 'wb') as f:
            f.write(json.dumps(refinfo, separators=(',', ':')).encode('utf-8'))
    scripts, touched, gap = run(d, infos_by_quest, infos_all, refinfo)
    qrefs = {q['script'] for q in d['quests'] if q.get('script')} | {q['script'] for q in d['quest_overrides'] if q.get('script')}
    blocks = blocks_mod.analyse_scripts(scpts, qrefs, refinfo, {g['edid'].lower(): g['ref'] for g in d['globals'] if g.get('edid')})
    meta = {'schema': SCHEMA, 'vocab': VOCAB, 'scripts': len(scripts),
            'contract': 'captured effects must include every effect with conditional=false and dynamic=false',
            'unmodeled_ranking': [{'fn': g['display'], 'scripts': len(g['scripts']), 'occurrences': g['occurrences']}
                                  for _, g in sorted(gap.items(), key=lambda kv: -len(kv[1]['scripts']))[:80]]}
    out = {'meta': meta, 'scripts': scripts, 'blocks': blocks, 'quest_touched': {k: sorted(v) for k, v in touched.items()}}
    with gzip.open(os.path.join(a.out, 'effects.json.gz'), 'wb', compresslevel=6) as f:
        f.write(json.dumps(out, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
    native = to_native(scripts, d)
    with gzip.open(os.path.join(a.out, 'effects_native.json.gz'), 'wb', compresslevel=6) as f:
        f.write(json.dumps(native, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
    with open(os.path.join(a.out, 'guest_suppression_report.md'), 'w', encoding='utf-8') as f:
        f.write(blocks_mod.report_md(blocks, len(scpts)))
    md = gaps_md(scripts, gap, len(scripts))
    with open(os.path.join(a.out, 'vocab_gaps.md'), 'w', encoding='utf-8') as f:
        f.write(md)
    print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
