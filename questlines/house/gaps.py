"""Capture/apply gap analysis of the Mr. House questline (gaps.json + gaps.md).

Every statement-level script function (fnmap.F) and every static-oracle effect in the scripts of the questline is mapped to
a synccore Effect type plus its L5 capture/apply status. Unsupported ones are listed with counts and exact locations
(script EDID + line, the same base risk.json / the policy files cite).
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fnmap  # noqa: E402
import qlib  # noqa: E402
import universe  # noqa: E402

SEV_RANK = {'high': 3, 'medium': 2, 'low': 1, 'info': 0}
STATUS_RANK = {'missing': 3, 'partial': 2, 'supported': 1, 'n/a': 0}
COMPANION_REFS = {'craigbooneref', 'roseofsharoncassidyref', 'veronicaref', 'lilyref', 'raulref', 'arcaderef', 'ede1ref', 'ede2ref',
                  'ede3ref', 'rexref', 'vhdsecuritronescort01ref', 'edeaccessref'}
KILL_ALL_MIN = 5

# Notes that cannot be derived from the source (limitations the verdict only hints at)
EFFECT_NOTES = {
    'AddItem': 'player subject only (non-player becomes RefAddItem)', 'RemoveItem': 'player subject only (non-player becomes RefRemoveItem)',
    'ModActorValue': 'player subject only (XP pseudo-AV 0x10000, Set/Force/Mod/Damage AV)',
    'MoveRef': 'MoveRef.partyWarp is never set by the capture; marker destinations only on apply',
    'SetScriptVar': 'quest vars captured by diff; ref owners applied but never captured (RefScriptVar has no producer)',
    'SetPlayerControls': 'coop layer applies coarsely: any disable locks all 7 classes', 'SetGlobal': 'diff + host state poll; clock globals unobservable',
    'ClearObjective': 'oracle name for SetObjective flags 0 (R-NORM)', 'PartyTether': 'rules engine (session level)',
    'BarrierArrive': 'rules engine (session level)', 'BarrierRelease': 'rules engine (session level)',
    'SaveSuppress': 'rules engine emits the state; no engine call blocks a save',
    'SetFastTravelLock': 'rules engine emits it from a FastTravelLock rule; script EnableFastTravel is not hooked',
    'SlideshowEnd': 'only deduplicated by the rules engine; nothing produces it',
}
PARTIAL_BY_DESIGN = ('AddItem', 'RemoveItem', 'ModActorValue', 'MoveRef', 'SetPlayerControls', 'SetScriptVar')


def effect_l5(f, t):
    """(status, capture, apply) of one Effect type, derived from l5facts."""
    if t == 'ClearObjective':
        return effect_l5(f, 'SetObjective')
    prod = t in f['producers']
    rules = t in f['rules_producers']
    app = t in f['apply_engine'] or t in f['apply_coop']
    cap = 'yes (hooks record it)' if prod else ('rules engine (session level)' if rules else 'no producer in plugin/hooks/engine_hooks.cpp')
    ap = 'yes' if t in f['apply_engine'] else ('coop layer (session level)' if t in f['apply_coop'] else 'no apply branch')
    if (prod or rules) and app:
        st = 'partial' if t in PARTIAL_BY_DESIGN else 'supported'
    elif prod or rules or app:
        st = 'partial'
    else:
        st = 'missing'
    if t in ('PartyTether', 'BarrierArrive', 'BarrierRelease') and (rules or app):
        st = 'n/a'
    return st, cap, ap


def classify_calls(d, uni):
    """One row per scanned call: the record + the fnmap verdict."""
    kq, kg = d.known_quests(), d.known_globals()
    rows = []
    for u in uni:
        for c in qlib.scan_source(u['source'], kq, kg):
            if fnmap.lookup(c['fn']) is None:
                raise KeyError(f'function {c["fn"]!r} missing from fnmap ({u["script"]} line {c["line"]}: {c["text"]})')
            v = fnmap.resolve(c['fn'], player=bool(c['player']), kind=u['kind'])
            tags = list(v['tags'])
            subj = (c['subject'] or '').lower()
            if subj in COMPANION_REFS and c['fn'] in ('moveto', 'movetomarker', 'enable', 'disable', 'kill', 'killactor'):
                tags.append('COMPANION')
            if 'radio' in c['text'].lower() and 'RADIO' not in tags:
                tags.append('RADIO')
            rows.append(dict(fn=c['fn'], status=v['status'], sev=v['sev'], tags=sorted(set(tags)), effect=v['effect'], quest=u['quest'],
                             lane=u['lane'], script=u['script'], where=u['where'], kind=u['kind'], line=c['line'], text=c['text'],
                             cond=bool(c['cond']), block=c['block'], subject=c['subject'], player=bool(c['player']),
                             script_id=u['id'], note=v['note'], capture=v['capture'], apply=v['apply']))
    # kill-all choices: many .Kill calls in one script
    per = collections.Counter((r['script_id']) for r in rows if r['fn'] in ('kill', 'killactor'))
    for r in rows:
        if r['fn'] in ('kill', 'killactor') and per[r['script_id']] >= KILL_ALL_MIN and 'KILL-ALL' not in r['tags']:
            r['tags'] = sorted(set(r['tags']) | {'KILL-ALL'})
    return rows


def oracle_effects(d, uni_quests):
    """Static effects (effects_native.json.gz) of the stage + INFO scripts of the given quests, counted by Effect type."""
    native = d.native
    byref = {q['ref']: q['edid'] for q in d.quests if q.get('edid')}
    cnt = collections.defaultdict(lambda: collections.Counter())
    for edid in uni_quests:
        n = native['quests'].get(edid)
        if not n:
            continue
        for st, blk in n['stages'].items():
            for kind in ('unconditional', 'conditional'):
                for e in blk[kind]:
                    cnt[e['type']][(edid, 'stage ' + st, kind)] += 1
    qrefs = {d.q_by_edid[e]['ref']: e for e in uni_quests}
    for ref, inf in native['infos'].items():
        qe = qrefs.get(inf.get('quest'))
        if not qe:
            continue
        for part in ('begin', 'end'):
            blk = inf.get(part)
            if not blk:
                continue
            for kind in ('unconditional', 'conditional'):
                for e in blk[kind]:
                    cnt[e['type']][(qe, f'info {ref.split(":")[1]} {part}', kind)] += 1
    return cnt


def build(d=None, uni=None, rows=None, mutate=None):
    d = d or qlib.data()
    uni = uni if uni is not None else universe.build(d)
    rows = rows if rows is not None else classify_calls(d, uni)
    by_fn = collections.defaultdict(list)
    for r in rows:
        by_fn[r['fn']].append(r)
    functions = []
    for fn, rs in by_fn.items():
        spec = fnmap.lookup(fn)
        statuses = collections.Counter(r['status'] for r in rs)
        join = lambda key: ' | '.join(dict.fromkeys(r[key] for r in rs if r[key]))  # noqa: E731
        sev = max((r['sev'] for r in rs), key=lambda s: SEV_RANK[s])
        status = max(statuses, key=lambda s: STATUS_RANK[s])
        by_lane = collections.Counter(r['lane'] for r in rs)
        keep = status != 'supported' or any(r['tags'] for r in rs)
        locs = [dict(script=r['script'], where=r['where'], line=r['line'], quest=r['quest'], lane=r['lane'], status=r['status'],
                     severity=r['sev'], conditional=r['cond'], subject=r['subject'], text=r['text'][:140])
                for r in rs if r['status'] != 'supported']
        functions.append(dict(fn=fn, effects=spec['effects'], mode=spec['mode'], status=status, status_by_call=dict(statuses),
                              capture=join('capture'), apply=join('apply'), severity=sev, tags=sorted({t for r in rs for t in r['tags']}),
                              count=len(rs), count_by_lane=dict(by_lane), note=join('note'), scripts=len({r['script_id'] for r in rs}),
                              locations=locs))
    functions.sort(key=lambda f: (-STATUS_RANK[f['status']], -SEV_RANK[f['severity']], -f['count'], f['fn']))
    hazards = collections.defaultdict(lambda: dict(count=0, count_by_lane=collections.Counter(), scripts=set(), cand=[]))
    for r in rows:
        for t in r['tags']:
            h = hazards[t]
            h['count'] += 1
            h['count_by_lane'][r['lane']] += 1
            h['scripts'].add(r['script'])
            h['cand'].append((r['lane'] != 'house', -SEV_RANK[r['sev']], r['status'] == 'supported', r['script'], r['line'], r['text'][:90]))
    hz = {}
    for t, h in sorted(hazards.items()):
        ex = sorted(h['cand'])[:12]
        hz[t] = dict(count=h['count'], count_by_lane=dict(h['count_by_lane']), scripts=len(h['scripts']),
                     examples=[f'{c[3]}:{c[4]} {c[5]}' for c in ex])
    by_quest = collections.defaultdict(collections.Counter)
    for r in rows:
        if r['lane'] == 'house':
            by_quest[r['quest']][r['status']] += 1
    by_quest = {q: dict(c) for q, c in sorted(by_quest.items())}
    scripts = []
    per = collections.defaultdict(list)
    for r in rows:
        per[r['script_id']].append(r)
    for u in uni:
        rs = per.get(u['id'], [])
        worst = max((r['status'] for r in rs), key=lambda s: STATUS_RANK[s], default='n/a')
        scripts.append(dict(id=u['id'], script=u['script'], quest=u['quest'], lane=u['lane'], kind=u['kind'], via=u['via'], where=u['where'],
                            calls=len(rs), worst=worst, missing=sum(1 for r in rs if r['status'] == 'missing'),
                            partial=sum(1 for r in rs if r['status'] == 'partial')))
    # oracle (static effect) view for the swept quests
    quests = qlib.CORE + list(qlib.SYSTEM)
    oc = oracle_effects(d, quests)
    effects = []
    f5 = fnmap.facts()
    for et, c in sorted(oc.items()):
        st = effect_l5(f5, et) if (et in f5['effect_types'] or et == 'ClearObjective') else ('unknown', 'unknown', 'unknown')
        effects.append(dict(effect=et, status=st[0], capture=st[1], apply=st[2], note=EFFECT_NOTES.get(et, ''), count=sum(c.values()),
                            unconditional=sum(v for k, v in c.items() if k[2] == 'unconditional'),
                            locations=[f'{k[0]} {k[1]} ({k[2]})' for k in sorted(c)][:40]))
    effect_vocab = [dict(effect=t, status=effect_l5(f5, t)[0], capture=effect_l5(f5, t)[1], apply=effect_l5(f5, t)[2], note=EFFECT_NOTES.get(t, ''))
                    for t in sorted(f5['effect_types'])]
    nrows = len(rows)
    by_status = collections.Counter(r['status'] for r in rows)
    by_sev = collections.Counter(r['sev'] for r in rows if r['status'] in ('missing', 'partial'))
    gaps_by_lane = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        if r['status'] in ('missing', 'partial'):
            gaps_by_lane[r['lane']][r['sev']] += 1
    doc = dict(
        schema=1, slug=qlib.SLUG,
        evidence='derived from the CURRENT source tree by l5facts.py: plugin/hooks (engine_hooks.cpp hooks and record() calls, apply_remote.cpp, capture_hub.cpp ambient policy), '
                 'plugin/coop/coop_module.cpp, synccore/src/rules.cpp, synccore/include/fnvcoop/effect.h; nothing here ran in-game',
        l5_snapshot=dict(hooks_referenced={h: (h in f5['hooks']) for h in sorted({v['hook'] for v in fnmap.SPEC.values() if v['hook']})},
                         producers=sorted(f5['producers']), apply_engine=sorted(f5['apply_engine']),
                         apply_coop=sorted(f5['apply_coop']), rules_producers=sorted(f5['rules_producers']), block_only=sorted(f5['block_only']),
                         ambient_mutations_recorded=f5['ambient_recorded']),
        status_vocabulary=dict(supported='Effect exists, captured on host and applied on client', partial='captured/applied with a stated limitation',
                               missing='no Effect, no capture hook, or capture without apply: will not replicate', **{'n/a': 'read-only query'}),
        summary=dict(scripts=len(uni), scripts_by_lane=dict(collections.Counter(u['lane'] for u in uni)), calls=nrows,
                     calls_by_status=dict(by_status), gap_calls_by_severity=dict(by_sev),
                     gap_calls_by_lane_severity={k: dict(v) for k, v in gaps_by_lane.items()},
                     functions=len(functions), functions_missing=sum(1 for f in functions if f['status'] == 'missing'),
                     functions_partial=sum(1 for f in functions if f['status'] == 'partial')),
        functions=functions, hazards=hz, house_calls_by_quest=by_quest, scripts=scripts, oracle_effects=effects, effect_vocabulary=effect_vocab)
    if mutate:
        _mutate(doc, mutate)
    return doc


def _mutate(doc, how):
    """Broken variants the validator must catch (mutation twin)."""
    fs = doc['functions']
    if how == 'drop_function':
        fs[:] = [f for f in fs if f['fn'] != 'moveto']
    elif how == 'drop_location':
        f = next(f for f in fs if f['fn'] == 'setdestroyed')
        f['locations'].pop()
    elif how == 'bad_line':
        f = next(f for f in fs if f['fn'] == 'swaptexture')
        f['locations'][0]['line'] += 7
    elif how == 'status_flip':
        f = next(f for f in fs if f['fn'] == 'swaptexture')
        f['status'] = 'supported'
    elif how == 'count_off':
        f = next(f for f in fs if f['fn'] == 'showmessage')
        f['count'] += 1
    else:
        raise ValueError(how)


def _short(s, n):
    s = ' '.join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + '~'


def to_md(g):
    s = g['summary']
    L = []
    L.append('# Mr. House questline: capture/apply gaps (generated by gaps.py, do not edit)')
    L.append('')
    L.append('Source of truth: `gaps.json` (all locations). Status vocabulary: supported / partial / missing / n/a (read-only). '
             'Evidence is the L5 source, nothing was run in-game. Lines are 1-based within the script source (the `scn` line is 1), '
             'the same base `risk.json` and `docs/quest-policies/main-house.md` cite.')
    L.append('')
    L.append(f'- Scripts analysed: **{s["scripts"]}** ({s["scripts_by_lane"]}); statement-level calls: **{s["calls"]}**; distinct functions: {s["functions"]}.')
    L.append(f'- Calls by status: {s["calls_by_status"]}.')
    L.append(f'- Gap calls (missing + partial) by severity: {s["gap_calls_by_severity"]}; by lane: {s["gap_calls_by_lane_severity"]}.')
    L.append(f'- Functions: {s["functions_missing"]} missing, {s["functions_partial"]} partial.')
    L.append('- Lane `house` = scripts owned by this questline; lane `shared` = Hoover Dam / ending infrastructure owned by Q-ncr (and Q-strip), analysed here because the House path runs through it.')
    L.append('')
    L.append('## Effect types used by the static oracle in this questline (stage + INFO scripts of the swept quests)')
    L.append('')
    L.append('| Effect | L5 status | capture | apply | oracle entries | unconditional | note |')
    L.append('|---|---|---|---|---|---|---|')
    for e in g['oracle_effects']:
        L.append(f'| {e["effect"]} | {e["status"]} | {_short(e["capture"], 60)} | {_short(e["apply"], 50)} | {e["count"]} | {e["unconditional"]} | {_short(e["note"], 90)} |')
    L.append('')
    L.append('## All Effect types of synccore/include/fnvcoop/effect.h and their L5 status (derived from the source tree)')
    L.append('')
    L.append('| Effect | status | capture | apply | note |')
    L.append('|---|---|---|---|---|')
    for e in g['effect_vocabulary']:
        L.append(f'| {e["effect"]} | {e["status"]} | {_short(e["capture"], 55)} | {_short(e["apply"], 40)} | {_short(e["note"], 80)} |')
    L.append('')
    L.append('Derived facts: ' + json.dumps(g['l5_snapshot']) )
    L.append('')
    L.append('## House-lane calls by owning quest (own scripts + the object/driver scripts attributed to the quest)')
    L.append('')
    L.append('| Quest | supported | partial | missing | n/a |')
    L.append('|---|---|---|---|---|')
    for q, c in g['house_calls_by_quest'].items():
        L.append(f'| `{q}` | {c.get("supported", 0)} | {c.get("partial", 0)} | {c.get("missing", 0)} | {c.get("n/a", 0)} |')
    L.append('')
    L.append('## Co-op hazards (tag counts over all scanned calls; examples: house lane first, highest severity first)')
    L.append('')
    L.append('| Tag | calls | scripts | by lane | examples (script:line) |')
    L.append('|---|---|---|---|---|')
    for t, h in g['hazards'].items():
        L.append(f'| {t} | {h["count"]} | {h["scripts"]} | {h["count_by_lane"]} | {_short("; ".join(h["examples"][:3]), 170)} |')
    L.append('')
    L.append('## Functions, worst status first')
    L.append('')
    L.append('| fn | Effect | status | severity | calls (house/shared) | capture | note |')
    L.append('|---|---|---|---|---|---|---|')
    for f in g['functions']:
        c = f['count_by_lane']
        L.append(f'| `{f["fn"]}` | {", ".join(f["effects"]) or "-"} | {f["status"]} | {f["severity"]} | {f["count"]} ({c.get("house", 0)}/{c.get("shared", 0)}) '
                 f'| {_short(f["capture"], 70)} | {_short(f["note"], 110)} |')
    L.append('')
    L.append('## Unsupported functions: exact locations')
    L.append('')
    L.append('Per function: up to 12 locations, house lane first, conditional=`c`. Full lists in `gaps.json` (`functions[].locations`).')
    L.append('')
    for f in g['functions']:
        if f['status'] in ('supported', 'n/a') and not f['locations']:
            continue
        if f['status'] == 'n/a' and f['severity'] in ('info', 'low'):
            continue
        L.append(f'### `{f["fn"]}`  [{f["status"]}, {f["severity"]}] {f["count"]} calls')
        L.append('')
        L.append(f'Effect: {", ".join(f["effects"]) or "none"}. Capture: {f["capture"]}. Apply: {f["apply"]}.' + (f' {f["note"]}' if f['note'] else ''))
        L.append('')
        locs = sorted(f['locations'], key=lambda x: (x['lane'] != 'house', -SEV_RANK[x['severity']], x['script'], x['line']))
        for l in locs[:12]:
            L.append(f'- `{l["script"]}:{l["line"]}`{" c" if l["conditional"] else ""} ({l["lane"]}, {l["status"]}) `{_short(l["text"], 90)}`')
        if len(locs) > 12:
            L.append(f'- ... {len(locs) - 12} more in gaps.json')
        L.append('')
    return '\n'.join(L) + '\n'
