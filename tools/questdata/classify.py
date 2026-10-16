"""Co-op risk classifier: script tokenizer + per-quest roll-up -> risk.json / risk_summary.md.

Usage: python classify.py [--in DIR] [--out DIR]   (reads quests.json + infos.jsonl.gz from extract.py)
"""
import argparse
import collections
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon import L, PLAYER_NAMES, PLAYER_IMPLICIT, DIALOG_LOCK  # noqa: E402

TOKEN = re.compile(r'"[^"]*"|0[xX][0-9A-Fa-f]+|[A-Za-z_][A-Za-z0-9_]*|\.|\S')
DECL = {'short', 'long', 'int', 'float', 'ref', 'string_var', 'array_var', 'scn', 'scriptname'}
PLAYER_REFS = {'FalloutNV.esm:000014', 'FalloutNV.esm:000007'}
QUEST_DEP_FUNCS = {'GetStage', 'GetStageDone', 'GetQuestVariable', 'GetQuestRunning', 'GetQuestCompleted',
                   'GetObjectiveCompleted', 'GetObjectiveDisplayed'}
# condition functions that evaluate against the player when run on the player / inherently player-bound
PLAYER_COND = re.compile(r'^(GetPC|GetPlayer|GetDisposition|GetReputation|GetPCFame|GetPCInfamy|GetIsPlayerBirthday|'
                         r'GetIsSex$|GetIsRace$|GetActorValue$|GetBaseActorValue$|GetItemCount$|GetEquipped$)')


def strip_comment(line):
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
        if ch == ';' and not q:
            break
        out.append(ch)
    return ''.join(out)


def classify_source(src, known_quests=frozenset(), known_globals=frozenset(), ctx=None):
    """Return list of findings {line,text,cat,level,fn,player,block} for one script source."""
    findings = []
    aliases = set(PLAYER_NAMES)
    block = None
    for n, raw in enumerate(src.splitlines(), 1):
        line = strip_comment(raw).strip()
        if not line:
            continue
        toks = TOKEN.findall(line)
        low = [t.lower() for t in toks]
        if low[0] == 'begin' and len(low) > 1:
            block = toks[1]
            continue
        if low[0] == 'end' and len(low) == 1:
            block = None
            continue
        if low[0] in DECL:
            continue
        # ref aliasing: set rX to player
        if low[0] == 'set' and len(low) >= 4 and low[2] == 'to' and low[3] in aliases | {'0x14'}:
            aliases.add(low[1])
        is_player = lambda t: t in aliases or t == '0x14'  # noqa: E731
        # quest var / global assignment
        if low[0] == 'set' and len(low) >= 4:
            if low[2] == '.' and low[1] in known_quests:
                findings.append(_f(n, line, 'Q-STATE', 1, 'set questvar', False, block))
            elif low[2] == 'to' and low[1] in known_globals:
                findings.append(_f(n, line, 'Q-STATE', 1, 'set global', False, block))
        seen_fn = False
        for i, t in enumerate(low):
            ent = L.get(t)
            if ent is None:
                continue
            if i >= 2 and low[i - 1] == '.' and low[i - 2] in known_quests and t not in ('setstage',):
                pass
            subj = low[i - 2] if i >= 2 and low[i - 1] == '.' else None
            subj_player = subj is not None and is_player(subj)
            arg_player = any(is_player(a) for a in low[i + 1:])
            player = subj_player or arg_player
            cat, base, lvl_p = ent
            inherent = t.startswith(('getpc', 'getplayer'))
            if cat == 'P-REF':
                if not (player or inherent):
                    continue
                level = base
            elif cat in ('P-INV', 'P-STAT', 'REWARD', 'S-STAT'):
                if subj_player or t in PLAYER_IMPLICIT or (cat in ('P-STAT', 'S-STAT') and arg_player) or inherent:
                    level = lvl_p
                else:
                    cat, level = 'ACTOR', 1
            elif cat == 'CTRL' and t in DIALOG_LOCK and ctx == 'dialogue':
                cat, level = 'CTRL-DIALOG', 2
            elif cat == 'MOVE':
                level = lvl_p if subj_player else (2 if arg_player else base)
            else:
                level = lvl_p if player else base
            findings.append(_f(n, line, cat, level, t, player, block))
            seen_fn = True
        if not seen_fn and any(is_player(t) for t in low) and low[0] not in ('set',):
            findings.append(_f(n, line, 'P-REF', 3, 'player-ref', True, block))
        elif not seen_fn and low[0] == 'set' and any(is_player(t) for t in low[3:]) and not is_player(low[3] if len(low) > 3 else ''):
            findings.append(_f(n, line, 'P-REF', 3, 'player-ref', True, block))
    return findings


def _f(n, line, cat, level, fn, player, block):
    return {'line': n, 'text': line[:160], 'cat': cat, 'level': level, 'fn': fn, 'player': player, 'block': block}


def classify_conditions(conds, where):
    out = []
    for c in conds:
        fn = c['func']
        if fn in QUEST_DEP_FUNCS:
            continue
        if PLAYER_COND.match(fn) or c.get('run_on') == 'reference' and c.get('ref') in PLAYER_REFS:
            out.append({'line': 0, 'text': f'cond {fn}', 'cat': 'P-COND', 'level': 2, 'fn': fn, 'player': True,
                        'block': where})
    return out


def script_node(sc, where, kq, kg, ctx=None):
    fs = classify_source(sc['source'], kq, kg, ctx) if sc and sc['source'] else []
    for f in fs:
        f['where'] = where
    pr = sc and any(r in PLAYER_REFS for r in sc.get('refs', []))
    return fs, bool(pr)


def dep_scripts(ref, extra):
    """Yield (label, script) for the objects a quest depends on: terminal item scripts, package scripts,
    and the object scripts (SCRI -> SCPT) of target/referenced NPCs and objects (via placed-ref base)."""
    forms, scpt = extra.get('forms', {}), extra.get('scpt', {})
    terms, packs = extra.get('terms', {}), extra.get('packs', {})
    cands = [ref]
    f = forms.get(ref)
    if f and f.get('base'):
        cands.append(f['base'])
    for c in cands:
        fc = forms.get(c) or {}
        nm = fc.get('edid') or c
        if c in terms:
            t = terms[c]
            for it in t['items']:
                if it.get('script'):
                    yield f'term {nm}', it['script']
        if c in packs:
            for k, sc in packs[c]['scripts'].items():
                yield f'pack {nm} {k}', sc
        sref = fc.get('script')
        if sref and sref in scpt and 'script' in scpt[sref]:
            yield f'obj {nm}', scpt[sref]['script']
        if c in terms and terms[c].get('script_ref') in scpt:
            yield f'term {nm} object', scpt[terms[c]['script_ref']]['script']


def rollup(quests, infos_by_quest, known_quests, known_globals, extra=None, overrides=None):
    extra = {k: v for k, v in (extra or {}).items() if not (k == 'touched' and v is None)}
    overrides = overrides or {}
    qrefs = {q['ref']: q for q in quests}
    fan_in = collections.defaultdict(set)
    out = {}
    # quest -> quest dependencies via conditions and script refs
    def cond_deps(owner, conds):
        for c in conds:
            if c['func'] in QUEST_DEP_FUNCS and isinstance(c.get('p1'), str) and c['p1'] != owner and c['p1'] in qrefs:
                fan_in[c['p1']].add(owner)
    deps_out = collections.defaultdict(set)
    for q in quests:
        r = q['ref']
        cond_deps(r, q['conditions'])
        for s in q['stages']:
            for e in s['entries']:
                cond_deps(r, e['conditions'])
        for o in q['objectives']:
            for t in o['targets']:
                cond_deps(r, t['conditions'])
        for i in infos_by_quest.get(r, ()):
            cond_deps(r, i['conditions'])
    for q in quests:
        r = q['ref']
        fs = []
        player_ref_scro = False
        sd = q.get('script_data')
        if sd:
            f, pr = script_node(sd, 'quest_script', known_quests, known_globals)
            fs += f
            player_ref_scro |= pr
            deps_out[r].update(x for x in sd['refs'] if x in qrefs and x != r)
        for s in q['stages']:
            for e in s['entries']:
                sc = e.get('script')
                if sc:
                    f, pr = script_node(sc, f'stage {s["index"]}', known_quests, known_globals)
                    fs += f
                    player_ref_scro |= pr
                    deps_out[r].update(x for x in sc['refs'] if x in qrefs and x != r)
                if e.get('next_quest'):
                    fs.append(_f(0, 'next_quest', 'Q-STATE', 1, 'next_quest', False, None) | {'where': f'stage {s["index"]}'})
        n_info_scripts = 0
        for i in infos_by_quest.get(r, ()):
            for k in ('begin', 'end'):
                sc = i.get(k)
                if sc:
                    n_info_scripts += 1
                    f, pr = script_node(sc, f'info {i["ref"].split(":")[1]} {k}', known_quests, known_globals, 'dialogue')
                    fs += f
                    player_ref_scro |= pr
                    deps_out[r].update(x for x in sc['refs'] if x in qrefs and x != r)
            fs += [dict(x, where=f'info {i["ref"].split(":")[1]} cond') for x in classify_conditions(i['conditions'], 'info')]
            if 'speech_challenge' in i.get('flags', []) or i.get('speech_challenge') not in (None, 'none'):
                fs.append(_f(0, 'speech challenge', 'CONV', 3, 'speech_challenge', True, None) |
                          {'where': f'info {i["ref"].split(":")[1]}'})
            if i.get('add_topics'):
                fs.append(_f(0, 'add topics', 'Q-STATE', 1, 'addtopic', False, None) | {'where': f'info {i["ref"].split(":")[1]}'})
        fs += [dict(x, where='quest cond') for x in classify_conditions(q['conditions'], 'quest')]
        direct_lvl = max((f['level'] for f in fs), default=0)
        n_dep_scripts = 0
        seen_scripts = set()
        touched = extra.get('touched', {}).get(r) if 'touched' in extra else None
        touched = None if touched is None else set(touched) | {t['ref'] for o in q['objectives'] for t in o['targets']}
        untouched_lvl = 0
        n_untouched = 0
        for dr in q.get('dep_refs', ()):
            is_touched = touched is None or dr in touched
            for label, sc in dep_scripts(dr, extra):
                if id(sc) in seen_scripts:
                    continue
                seen_scripts.add(id(sc))
                f, _ = script_node(sc, f'dep {label}', known_quests, known_globals,
                                   'terminal' if label.startswith('term') else None)
                for x in f:
                    x['dep'] = True
                if is_touched:
                    n_dep_scripts += 1
                    fs += f
                else:  # object never touched by this quest's statements: audit-only
                    n_untouched += 1
                    untouched_lvl = max([untouched_lvl] + [x['level'] for x in f])
        lvl = max((f['level'] for f in fs), default=0)
        cats = collections.Counter(f['cat'] for f in fs)
        catmax = {}
        for f in fs:
            catmax[f['cat']] = max(catmax.get(f['cat'], 0), f['level'])
        n_infos = len(infos_by_quest.get(r, ()))
        real = bool(q['stages'] or q['objectives'])
        tier_auto = f'T{lvl}'
        lvl_auto = lvl
        ov = overrides.get(q.get('edid') or '', {})
        if ov.get('tier'):
            lvl = int(ov['tier'][1])
        out[r] = {
            'edid': q.get('edid'), 'name': q.get('name'), 'plugin': q['plugin'], 'tier': f'T{lvl}', 'max_level': lvl,
            'tier_auto': tier_auto, 'tier_direct': f'T{direct_lvl}', 'tier_all_deps': f'T{max(lvl_auto, untouched_lvl)}',
            'dep_scripts_untouched': n_untouched, 'override_notes': ov.get('notes'),
            'dep_scripts': n_dep_scripts,
            'score': sum(f['level'] for f in fs), 'kind': 'story' if real else 'container',
            'cats': dict(cats), 'cat_max_level': catmax,
            'has_cutscene': catmax.get('CTRL', 0) >= 4, 'has_move': 'MOVE' in cats,
            'mutates_player': bool(cats.get('P-INV') or cats.get('P-STAT')),
            'mutates_world': bool(cats.get('WORLD') or cats.get('ACTOR')), 'mutates_quest': bool(cats.get('Q-STATE')),
            'player_scro_ref': player_ref_scro, 'stages': len(q['stages']), 'objectives': len(q['objectives']),
            'infos': n_infos, 'info_scripts': n_info_scripts, 'findings_total': len(fs),
            'fan_in': len(fan_in.get(r, ())), 'fan_out': len(deps_out.get(r, ())),
            'findings': sorted((f for f in fs if f['level'] >= 1), key=lambda f: -f['level'])[:40],
            'stage_findings': [f for f in fs if f['level'] >= 1 and f['where'].startswith('stage')],
        }
    return out


def load(indir):
    d = json.load(open(os.path.join(indir, 'quests.json'), encoding='utf-8'))
    infos = collections.defaultdict(list)
    with gzip.open(os.path.join(indir, 'infos.jsonl.gz'), 'rb') as f:
        for ln in f:
            i = json.loads(ln)
            if i.get('quest'):
                infos[i['quest']].append(i)
    return d, infos


def load_extra(indir):
    def gz(n):
        p = os.path.join(indir, n)
        if not os.path.exists(p):
            return None
        with gzip.open(p, 'rb') as f:
            return json.loads(f.read().decode('utf-8'))
    scpt = gz('scripts.json.gz') or []
    terms = gz('terms.json.gz') or []
    packs = gz('packs.json.gz') or []
    eff = gz('effects.json.gz')
    return {'touched': eff['quest_touched'] if eff else None, 'forms': gz('forms.json.gz') or {}, 'scpt': {x['ref']: x for x in scpt},
            'terms': {x['ref']: x for x in terms}, 'packs': {x['ref']: x for x in packs}}


def load_overrides(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'overrides.json')
    if not os.path.exists(path):
        return {}
    return json.load(open(path, encoding='utf-8')).get('quests', {})


def report_md(risk, meta):
    qs = list(risk.values())
    story = [q for q in qs if q['kind'] == 'story']
    md = ['# Quest co-op risk summary', '', f'{len(qs)} quests ({len(story)} with stages/objectives, '
          f'{len(qs) - len(story)} dialogue/system containers). Tier = max level reached by any reachable script '
          '(quest script, stage scripts, linked INFO scripts/conditions).', '', '## Tier distribution', '',
          '| Tier | all quests | story quests | story, direct scripts only | story, all dep scripts (no reachability filter) | story, tier_auto (no overrides) |',
          '|---|---|---|---|---|---|']
    for t in range(5):
        md.append(f'| T{t} | {sum(1 for q in qs if q["max_level"] == t)} | {sum(1 for q in story if q["max_level"] == t)} | '
                  f'{sum(1 for q in story if q["tier_direct"] == f"T{t}")} | {sum(1 for q in story if q["tier_all_deps"] == f"T{t}")} | '
                  f'{sum(1 for q in story if q["tier_auto"] == f"T{t}")} |')
    md += ['', '## By plugin (story quests)', '', '| Plugin | quests | T0 | T1 | T2 | T3 | T4 |', '|---|---|---|---|---|---|---|']
    for p in sorted({q['plugin'] for q in story}):
        s = [q for q in story if q['plugin'] == p]
        md.append(f'| {p} | {len(s)} | ' + ' | '.join(str(sum(1 for q in s if q['max_level'] == t)) for t in range(5)) + ' |')
    md += ['', '## Category presence (story quests with at least one finding in the category)', '']
    cc = collections.Counter(c for q in story for c in q['cats'])
    md.append(', '.join(f'{c}: {n}' for c, n in cc.most_common()))
    md += ['', '## Top 30 riskiest story quests (by tier, then score)', '',
           '| # | Quest | EDID | Plugin | Tier | Score | Stages | INFOs | Categories | Cutscene | Move | Fan-in |',
           '|---|---|---|---|---|---|---|---|---|---|---|---|']
    top = sorted(story, key=lambda q: (-q['max_level'], -q['score']))[:30]
    for i, q in enumerate(top, 1):
        cats = ','.join(f'{c}' for c, _ in sorted(q['cats'].items(), key=lambda kv: -q['cat_max_level'][kv[0]])[:5])
        md.append(f'| {i} | {q["name"] or ""} | {q["edid"]} | {q["plugin"]} | {q["tier"]} | {q["score"]} | {q["stages"]} | '
                  f'{q["infos"]} | {cats} | {"Y" if q["has_cutscene"] else ""} | {"Y" if q["has_move"] else ""} | {q["fan_in"]} |')
    md += ['', '## Highest fan-in (quests whose state other quests read)', '', '| Quest | EDID | fan-in |', '|---|---|---|']
    for q in sorted(story, key=lambda q: -q['fan_in'])[:10]:
        md.append(f'| {q["name"] or ""} | {q["edid"]} | {q["fan_in"]} |')
    return '\n'.join(md) + '\n'


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, '..', '..', 'out', 'questdata')
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='indir', default=default)
    ap.add_argument('--out', default=default)
    a = ap.parse_args(argv)
    d, infos = load(a.indir)
    kq = {q['edid'].lower() for q in d['quests'] if q.get('edid')}
    kg = {g['edid'].lower() for g in d['globals'] if g.get('edid')}
    risk = rollup(d['quests'], infos, kq, kg, load_extra(a.indir), load_overrides())
    meta = {'lexicon_functions': len(L), 'levels': '0 safe,1 sync-state,2 per-player replicate,3 needs design,4 hard'}
    with open(os.path.join(a.out, 'risk.json'), 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'quests': risk}, f, separators=(',', ':'), ensure_ascii=False)
    md = report_md(risk, meta)
    with open(os.path.join(a.out, 'risk_summary.md'), 'w', encoding='utf-8') as f:
        f.write(md)
    print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
