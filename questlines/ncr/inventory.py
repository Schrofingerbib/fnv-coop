"""inventory.json builder (no game needed).

python -I questlines/ncr/inventory.py  -> questlines/ncr/inventory.json
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ENTRY_RE = re.compile(r'(?i)\b(setstage|startquest)\s+(\w+)(?:\s+(\d+))?')
COND_Q = re.compile(r'(?i)\b(GetStageDone|GetStage|GetObjectiveDisplayed|GetObjectiveCompleted|GetQuestCompleted|GetQuestRunning|GetQR|GetQC)\s+(\w+)(?:\s+(\d+))?')


def all_script_sources(D):
    """Yield (owner_label, where, source) for every quest/stage/info script and object script in the data set."""
    for q in D.quests:
        if q.get('override'):
            continue
        for where, sc, _ in D.quest_scripts(q):
            yield q.get('edid') or q['ref'], where, sc['source']
    for s in D.scpt_list:
        sc = s.get('script') or {}
        if sc.get('source') and sc.get('type') != 'quest':   # quest scripts are already yielded via the quest itself
            yield s.get('edid') or s['ref'], 'script', sc['source']
    # INFOs of quests flagged override are already counted through D.infos (keyed by quest ref)


def entry_index(D):
    """quest edid(lower) -> [{owner, where, line, cmd, stage, text}] for every SetStage / StartQuest on it."""
    idx = collections.defaultdict(list)
    for owner, where, src in all_script_sources(D):
        for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
            line = common.strip(raw)
            if not line:
                continue
            for m in ENTRY_RE.finditer(line):
                cmd, q, st = m.group(1).lower(), m.group(2).lower(), m.group(3)
                idx[q].append({'owner': owner, 'where': where, 'line': n, 'cmd': cmd,
                               'stage': int(st) if st else None, 'text': line[:140]})
    return idx


def neighbours(D, names):
    """Graph neighbours of `names`: {edid: [{dir, kind, strength, count, evidence}]}."""
    out = collections.defaultdict(list)
    for e in D.graph['edges']:
        if e['src'] in names and e['dst'] not in names:
            out[e['dst']].append({'dir': 'out', 'kind': e['kind'], 'strength': e['strength'], 'count': e['count'], 'peer': e['src'],
                                  'evidence': [x['text'][:120] for x in e['evidence'][:2]]})
        elif e['dst'] in names and e['src'] not in names:
            out[e['src']].append({'dir': 'in', 'kind': e['kind'], 'strength': e['strength'], 'count': e['count'], 'peer': e['dst'],
                                  'evidence': [x['text'][:120] for x in e['evidence'][:2]]})
    return out


def _nm(D, p):
    if isinstance(p, str):
        return D.ref_info(p).get('edid') or p
    return p


def quest_conditions_summary(q, D):
    """Quest-level and stage-level conditions that reference other quests."""
    out = []
    for c in q.get('conditions', []):
        out.append({'where': 'quest', 'func': c['func'], 'p1': _nm(D, c.get('p1')),
                    'p2': c.get('p2'), 'op': c.get('op'), 'value': c.get('value')})
    for s in q['stages']:
        for e in s['entries']:
            for c in e['conditions']:
                out.append({'where': f'stage {s["index"]}', 'func': c['func'],
                            'p1': _nm(D, c.get('p1')),
                            'p2': c.get('p2'), 'op': c.get('op'), 'value': c.get('value')})
    return out


SUBJ_RE = re.compile(r'(?im)^\s*(\w+)\.(enable|disable|kill|killactor|resurrect|moveto|movetomarker|movetofade|addscriptpackage|removescriptpackage|startcombat|stopcombat|setessential|setghost|sifh|setav|forceav|evp|resetai|sendassaultalarm)\b')


def hostowned_coverage(D, quests):
    """Are the NPCs the line's quests target or mutate in out/coverage/hostowned.json (NV:MP host-owned actor list)?"""
    owned = {a['ref'] for a in D.hostowned['actors']}
    targets, missing_t, lines, missing_l = set(), [], 0, collections.Counter()
    for iq in quests:
        if iq['role'] not in ('core', 'support'):
            continue
        for o in iq['objectives']:
            for t in o['targets']:
                if t.get('sig') in ('ACHR', 'ACRE') and t['ref'] not in targets:
                    targets.add(t['ref'])
                    if t['ref'] not in owned:
                        missing_t.append(t['ref'])
        q = D.by_edid[iq['edid']]
        for where, sc, _ in D.quest_scripts(q):
            if where.startswith('info '):
                continue
            for m in SUBJ_RE.finditer(sc['source']):
                ref = D.resolve(m.group(1))
                if ref and D.ref_info(ref).get('sig') in ('ACHR', 'ACRE'):
                    lines += 1
                    if ref not in owned:
                        missing_l[(iq['edid'], m.group(1))] += 1
    return {'objective_target_actors': len(targets), 'objective_target_actors_missing': sorted(missing_t),
            'script_subject_actor_lines': lines, 'script_subject_actor_lines_missing': [f'{q}:{a} x{n}' for (q, a), n in sorted(missing_l.items())],
            'source': 'out/coverage/hostowned.json'}


def stage_record(q, s, D):
    entries = s['entries']
    text = next((e.get('text') for e in entries if e.get('text')), None)
    flags = sorted({f for e in entries for f in e['flags']})
    return {'index': s['index'], 'log': text, 'flags': flags, 'has_script': any(e.get('script') for e in entries)}


def build(D=None):
    D = D or common.Data()
    scope = {}
    for e in common.CORE:
        scope[e] = 'core'
    for e in common.SUPPORT:
        scope[e] = 'support'
    for e in common.SYSTEM:
        scope[e] = 'system'
    names = set(scope)
    nb = neighbours(D, {e for e in names if e in D.by_edid})
    entries = entry_index(D)
    lines = {n['id']: n['line'] for n in D.graph['nodes']}
    for e in sorted(nb):
        if e not in scope and e in D.by_edid:
            scope[e] = 'cross_lane' if (e in common.CROSS_LANE or e.startswith(common.CROSS_PREFIX)) else 'touched'
    quests = []
    for edid, role in sorted(scope.items(), key=lambda kv: ({'core': 0, 'support': 1, 'system': 2, 'cross_lane': 3, 'touched': 4}[kv[1]], kv[0])):
        q = D.by_edid.get(edid)
        if q is None:
            continue
        rq = D.risk[q['ref']]
        infos = D.infos.get(q['ref'], [])
        edges = nb.get(edid, [])
        rules = D.rules['quests'].get(edid)
        quests.append({
            'edid': edid, 'ref': q['ref'], 'formid': q['formid'], 'console_id': common.hexid(q['ref']),
            'name': q.get('name'), 'role': role, 'plugin': q['plugin'],
            'tier': rq['tier'], 'tier_direct': rq['tier_direct'], 'risk_score': rq['score'], 'flags': q.get('flags'),
            'priority': q.get('priority'), 'script': q.get('script'), 'graph_line': lines.get(edid),
            'stages': [stage_record(q, s, D) for s in sorted(q['stages'], key=lambda s: s['index'])],
            'objectives': [{'index': o['index'], 'text': o.get('text'),
                            'targets': [D.ref_info(t['ref']) for t in o['targets'] if t.get('ref')]} for o in q['objectives']],
            'prerequisites': {
                'condition_refs': quest_conditions_summary(q, D),
                'graph_edges': edges if role in ('core', 'support') else [],
            },
            'linked_by': ([{'peer': e['peer'], 'dir': e['dir'], 'kind': e['kind'], 'strength': e['strength'], 'count': e['count']} for e in edges]
                          if role not in ('core', 'support') else []),
            'entered_by': [x for x in entries.get(edid.lower(), []) if x['owner'] != edid][:40],
            'dialogue': {'infos': len(infos), 'scripted_infos': sum(1 for i in infos if i.get('begin') or i.get('end')),
                         'topics': len(q.get('topics', [])),
                         'speakers': sorted({D.ref_info(i['speaker']).get('edid') or i['speaker'] for i in infos if i.get('speaker')})[:60]},
            'rules': [r['kind'] for r in rules['rules']] if rules else [],
            'rules_curated': bool(rules and rules.get('curated')),
        })
    inv = {'schema': 1, 'slug': common.SLUG, 'questline': 'Main: NCR (through the Hoover Dam ending)',
           'sources': ['out/questdata/quests.json', 'out/coverage/questgraph.json', 'out/questdata/risk.json', 'docs/quest-policies/rules.json'],
           'critical_path_ncr': ['VCG00', 'VCG01', 'VCG02', 'VCG03', 'VMQ01', 'VMQTops', 'VMQ03a', 'VMQ04', 'VMS45', 'VMQ05', 'VMQNCRFail', 'VMS21', 'vHDBattleController'],
           'critical_path_note': 'from out/coverage/summary.md; VMS45, VMQNCRFail, VMS21 are script-graph-derived (confirm by hand: see policy_review.md)',
           'summary': {
               'quests': len(quests),
               'by_role': dict(collections.Counter(x['role'] for x in quests)),
               'stages': sum(len(x['stages']) for x in quests if x['role'] in ('core', 'support')),
               'objectives': sum(len(x['objectives']) for x in quests if x['role'] in ('core', 'support')),
               'infos': sum(x['dialogue']['infos'] for x in quests if x['role'] in ('core', 'support', 'system')),
               'topics': sum(x['dialogue']['topics'] for x in quests if x['role'] in ('core', 'support', 'system')),
           },
           'hostowned_coverage': hostowned_coverage(D, quests),
           'quests': quests}
    return inv


def main():
    inv = build()
    p = os.path.join(common.HERE, 'inventory.json')
    common.jdump(inv, p)
    s = inv['summary']
    print(f'inventory: {s["quests"]} quests {s["by_role"]}, swept-scope stages {s["stages"]}, objectives {s["objectives"]}, '
          f'infos {s["infos"]}, topics {s["topics"]} -> {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
