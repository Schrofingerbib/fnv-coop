"""Per-quest stage-walk test specs (JSON) for the nvbridge runner.

Usage: python scenarios.py [--in DIR] [--out DIR]
Reads quests.json, infos.jsonl.gz, risk.json; writes <out>/scenarios/<EDID>.json + _index.json.

Spec format (schema 1), per docs/04-quest-data.md section 10:
  quest (EDID, readability only), ref, formid (console hex FormID via loadorder.json), plugin, tier, manual, start_enabled, preconditions{stages_done,globals,quest_conditions},
  steps[]: {id, host: <console cmd run on host via nvbridge exec>, wait: <console expr>,
            expect: {both: {stage, objectives{displayed,completed}, quest_completed, quest_failed},
                     side_effects: [{cat, fn, text, check, ...}]}}
  policy_checks[], dialogue{total, scripted[]}
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import load  # noqa: E402
import loadorder  # noqa: E402

SCHEMA = 2
OBJ_RE = re.compile(r'(?im)^\s*setobjective(displayed|completed)\s+(\w+)\s+(\d+)\s+([01])')
ITEM_RE = re.compile(r'(?im)^\s*(\w+\.)?(additem|removeitem)\s+(\w+)\s+(-?\d+)')
CHECK = {'Q-STATE': 'quest_state_equal', 'P-INV': 'inventory_diff', 'P-STAT': 'actor_value_or_rep_diff',
         'WORLD': 'world_state_equal', 'ACTOR': 'actor_state_equal', 'MOVE': 'position_policy',
         'CTRL': 'manual_cutscene', 'CONV': 'ui_local_only', 'SAVE': 'manual', 'P-REF': 'condition_mapping',
         'P-COND': 'condition_mapping', 'TIME': 'time_equal'}
POLICY = {'P-INV': 'reward-both-vs-owner', 'P-STAT': 'karma-rep-shared', 'CTRL': 'cutscene-lock-policy',
          'MOVE': 'teleport-scope', 'CONV': 'dialogue-ownership', 'P-REF': 'player-reference-mapping',
          'P-COND': 'player-reference-mapping', 'SAVE': 'engine-global'}


def safe(s):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', s)


def stage_steps(q, rq, edid, static=None):
    fid = loadorder.console_id(q['ref'])
    by_stage = {}
    for f in rq['stage_findings']:
        by_stage.setdefault(f['where'], []).append(f)
    steps = []
    for st in sorted(q['stages'], key=lambda s: s['index']):
        idx = st['index']
        disp, comp, fx = [], [], []
        flags = set()
        scripts = [e['script']['source'] for e in st['entries'] if e.get('script')]
        for e in st['entries']:
            flags.update(e['flags'])
        for src in scripts:
            for kind, qn, n, v in OBJ_RE.findall(src):
                if qn.lower() == edid.lower() and v == '1':
                    (disp if kind.lower() == 'displayed' else comp).append(int(n))
        for f in by_stage.get(f'stage {idx}', []):
            d = {'cat': f['cat'], 'fn': f['fn'], 'level': f['level'], 'text': f['text'],
                 'check': CHECK.get(f['cat'], 'manual')}
            m = ITEM_RE.match(f['text']) if f['cat'] == 'P-INV' else None
            if m:
                d.update(item=m.group(3), count=int(m.group(4)), target=(m.group(1) or 'self').rstrip('.').lower())
            fx.append(d)
        e = {'both': {'stage': {'quest': edid, 'value': idx}}}
        stat = (static or {}).get((q['ref'], idx))
        if stat:
            e['effects_static'] = [x for x in stat if not x['conditional'] and not x['dynamic']]
            e['effects_maybe'] = sum(1 for x in stat if x['conditional'] or x['dynamic'])
        if disp or comp:
            e['both']['objectives'] = {'displayed': sorted(set(disp)), 'completed': sorted(set(comp))}
        if 'complete_quest' in flags:
            e['both']['quest_completed'] = True
        if 'fail_quest' in flags:
            e['both']['quest_failed'] = True
        if fx:
            e['side_effects'] = fx
        steps.append({'id': f'stage_{idx}', 'host': f'setstage {fid} {idx}', 'wait': f'GetStageDone {fid} {idx}',
                      'host_edid': f'setstage {edid} {idx}',
                      'expect': e, 'log': (st['entries'][0].get('text') if st['entries'] else None)})
    return steps


def preconditions(q):
    stages_done, globals_, other = [], [], []
    conds = list(q['conditions']) + [c for s in q['stages'] for e in s['entries'] for c in e['conditions']]
    for c in conds:
        if c['func'] == 'GetStageDone' and c['op'] in ('==', '>=') and c['value'] == 1.0:
            stages_done.append({'quest': c['p1'], 'stage': c['p2']})
        elif c['func'] == 'GetGlobalValue':
            globals_.append({'global': c['p1'], 'op': c['op'], 'value': c['value']})
        else:
            other.append({'func': c['func'], 'op': c['op'], 'value': c['value'], 'p1': c['p1'], 'p2': c['p2']})
    return {'stages_done': stages_done, 'globals': globals_, 'quest_conditions': other[:20]}


def dialogue_summary(infos):
    scripted = []
    for i in infos:
        if i.get('begin') or i.get('end'):
            scripted.append({'info': i['ref'], 'topic': i.get('topic'), 'speaker': i.get('speaker'),
                             'flags': i.get('flags', []), 'n_conditions': len(i['conditions']),
                             'begin': bool(i.get('begin')), 'end': bool(i.get('end'))})
    return {'total': len(infos), 'scripted_total': len(scripted), 'scripted': scripted[:100]}


def build(quests, infos, risk, static=None):
    out = []
    for q in quests:
        edid = q.get('edid') or f'Q{q["formid"]}'
        rq = risk[q['ref']]
        steps = stage_steps(q, rq, edid, static)
        flags = q.get('flags', [])
        start = []
        if 'start_enabled' not in flags:
            fid = loadorder.console_id(q['ref'])
            start = [{'id': 'start', 'host': f'startquest {fid}', 'wait': f'GetQuestRunning {fid}', 'host_edid': f'startquest {edid}',
                      'expect': {'both': {'quest_running': edid}}}]
        cats = sorted(rq['cats'])
        spec = {
            'schema': SCHEMA, 'quest': edid, 'ref': q['ref'], 'formid': loadorder.console_id(q['ref']), 'name': q.get('name'), 'plugin': q['plugin'],
            'tier': rq['tier'], 'kind': rq['kind'], 'manual': rq['tier_direct'] == 'T4' or rq['tier'] != rq['tier_auto'] and rq['max_level'] >= 4,
            'start_enabled': 'start_enabled' in flags, 'preconditions': preconditions(q),
            'steps': start + steps,
            'policy_checks': sorted({POLICY[c] for c in cats if c in POLICY and rq['cat_max_level'][c] >= 2}),
            'dialogue': dialogue_summary(infos.get(q['ref'], [])),
        }
        if spec['manual']:
            spec['manual_note'] = ('Tier 4 in direct scripts or manual override (cinematic lock/forced camera/teleport): automated stage walk validates state sync '
                                   'only; scripted presentation needs a hand-designed scenario.')
        out.append((edid, spec))
    return out


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, '..', '..', 'out', 'questdata')
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='indir', default=default)
    ap.add_argument('--out', default=default)
    a = ap.parse_args(argv)
    d, infos = load(a.indir)
    risk = json.load(open(os.path.join(a.indir, 'risk.json'), encoding='utf-8'))['quests']
    static = {}
    ep = os.path.join(a.indir, 'effects.json.gz')
    if os.path.exists(ep):
        import gzip
        for sc in json.loads(gzip.open(ep).read().decode('utf-8'))['scripts']:
            if sc['kind'] == 'stage':
                static.setdefault((sc['quest'], sc['stage']), []).extend(sc['effects'])
    sdir = os.path.join(a.out, 'scenarios')
    os.makedirs(sdir, exist_ok=True)
    for fn in os.listdir(sdir):
        if fn.endswith('.json'):
            os.remove(os.path.join(sdir, fn))
    seen, index = set(), []
    for edid, spec in build(d['quests'], infos, risk, static):
        name = safe(edid)
        if name.lower() in seen:
            name += '_' + spec['ref'].split(':')[1]
        seen.add(name.lower())
        with open(os.path.join(sdir, name + '.json'), 'w', encoding='utf-8') as f:
            json.dump(spec, f, separators=(',', ':'), ensure_ascii=False)
        index.append({'file': name + '.json', 'quest': edid, 'tier': spec['tier'], 'steps': len(spec['steps']),
                      'manual': spec['manual'], 'kind': spec['kind']})
    with open(os.path.join(sdir, '_index.json'), 'w') as f:
        json.dump({'schema': SCHEMA, 'count': len(index), 'scenarios': index}, f, separators=(',', ':'))
    print(f'{len(index)} scenarios, {sum(i["steps"] for i in index)} steps -> {sdir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
