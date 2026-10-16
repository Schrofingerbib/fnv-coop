"""inventory.json: every quest of the Old World Blues DLC with stages, objectives, prerequisites, dialogue counts, tier."""
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan  # noqa: E402
from common import PLUGIN  # noqa: E402

SETSTAGE = re.compile(r'(?i)\bsetstage\s+(\w+)\s+(\d+)')
STARTQ = re.compile(r'(?i)\bstartquest\s+(\w+)')
STOPQ = re.compile(r'(?i)\bstopquest\s+(\w+)')

# questline roles, hand-assigned from the quest names/scripts (checked by run_tests: every OWB quest is assigned)
CORE = ['NVDLC03MQ00', 'NVDLC03MQ01', 'NVDLC03MQ02', 'NVDLC03MQ03', 'NVDLC03MQ04', 'NVDLC03MQ05']
SIDE = ['NVDLC03HQBuddy', 'NVDLC03HQBuddyUpgrade', 'NVDLC03HQBuddySamples', 'NVDLC03ThinkTankQuest',
        'NVDLC03ThinkTankDoctor0a', 'NVDLC03ThinkTankDoctor0b', 'NVDLC03ThinkTankDoctor8',
        'NVDLC03ThinkTankDoctorBorous', 'NVDLC03ThinkTankDoctorDala', 'NVDLC03ThinkTankDoctorKlein',
        'NVDLC03X8Setup', 'NVDLC03X8Replay', 'NVDLC03X13Setup', 'NVDLC03X13Replay', 'NVDLC03SonicEmitterQuest']
LINE_OF = {}
for _q in CORE:
    LINE_OF[_q] = 'main'
for _q in SIDE:
    LINE_OF[_q] = 'side'


def role_of(q):
    if q['edid'] in LINE_OF:
        return LINE_OF[q['edid']]
    if q['infos'] or q['topics']:
        return 'dialogue'
    return 'system'


def _setters(d, units):
    """(target edid lower, stage) -> [{by, line}] ; quest edid lower -> start/stop sources"""
    st = collections.defaultdict(list)
    start = collections.defaultdict(list)
    stop = collections.defaultdict(list)
    for u in units:
        for ln, raw in enumerate(u['source'].replace('\r', '').split('\n'), 1):
            s = raw.split(';')[0]
            for m in SETSTAGE.finditer(s):
                st[(m.group(1).lower(), int(m.group(2)))].append({'by': u['id'], 'line': ln})
            for m in STARTQ.finditer(s):
                start[m.group(1).lower()].append({'by': u['id'], 'line': ln})
            for m in STOPQ.finditer(s):
                stop[m.group(1).lower()].append({'by': u['id'], 'line': ln})
    return st, start, stop


def _related_dialogue(d):
    """quest edid -> {'infos': set(info ref), 'topics': set(topic ref)} for OWB INFOs that mention the quest."""
    names = {q['edid'].lower(): q['edid'] for q in d.owb_quests()}
    ref_to_edid = {q['ref']: q['edid'] for q in d.owb_quests()}
    out = collections.defaultdict(lambda: {'infos': set(), 'topics': set()})
    word = re.compile(r'\b(NVDLC03\w+)\b', re.I)
    for i in d.infos:
        hit = set()
        for c in i.get('conditions', []):
            for k in ('p1', 'p2'):
                v = c.get(k)
                if isinstance(v, str) and v in ref_to_edid:
                    hit.add(ref_to_edid[v])
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc.get('source'):
                for w in word.findall(sc['source']):
                    if w.lower() in names:
                        hit.add(names[w.lower()])
                for r in sc.get('refs', []):
                    if r in ref_to_edid:
                        hit.add(ref_to_edid[r])
        if i.get('quest') in ref_to_edid:
            hit.add(ref_to_edid[i['quest']])
        for h in hit:
            out[h]['infos'].add(i['ref'])
            out[h]['topics'].add(i['topic'])
    return out


def build_inventory(d):
    units = list(scan.units(d))
    st, start, stop = _setters(d, units)
    rel = _related_dialogue(d)
    oq = d.effects_native['quests']
    edge_in = collections.defaultdict(list)
    for e in d.graph['edges']:
        edge_in[e['dst']].append(e)
    quests = []
    for q in d.owb_quests():
        edid = q['edid']
        risk = d.risk[q['ref']]
        o = oq.get(edid, {}).get('stages', {})
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            ent = o.get(str(s['index']), {'unconditional': [], 'conditional': []})
            stages.append({
                'index': s['index'],
                'log': next((e.get('text') for e in s['entries'] if e.get('text')), None),
                'flags': sorted({f for e in s['entries'] for f in e['flags']}),
                'has_script': any(e.get('script') and e['script'].get('source') for e in s['entries']),
                'condition_count': sum(len(e['conditions']) for e in s['entries']),
                'oracle_unconditional': [x['type'] for x in ent['unconditional']],
                'oracle_conditional': [x['type'] for x in ent['conditional']],
                'set_by': st.get((edid.lower(), s['index']), []),
                'findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], line=f['line'], text=f['text'])
                             for f in risk['stage_findings'] if f['where'] == 'stage %d' % s['index']],
            })
        objectives = []
        for ob in sorted(q['objectives'], key=lambda x: x['index']):
            tg = []
            for t in ob['targets']:
                if not t.get('ref'):
                    continue
                src, rec = d.resolve(t['ref'])
                tg.append({'ref': t['ref'], 'edid': (rec or {}).get('edid'), 'source': src})
            objectives.append({'index': ob['index'], 'text': ob.get('text'), 'targets': tg})
        prereq = []
        for e in edge_in.get(edid, []):
            if e['kind'] in ('stage', 'start', 'gate') and e['strength'] == 'hard' and e['src'] != edid:
                prereq.append({'quest': e['src'], 'kind': e['kind'], 'count': e['count'],
                               'evidence': [x['text'][:100] for x in e['evidence'][:2]]})
        cond_prereq = sorted({c['p1'] for c in q['conditions'] if isinstance(c.get('p1'), str) and ':' in c['p1']})
        r = rel.get(edid, {'infos': set(), 'topics': set()})
        quests.append({
            'edid': edid, 'ref': q['ref'], 'runtime_formid': d.runtime_id(q['ref']), 'name': q.get('name'),
            'role': role_of(q), 'tier': risk['tier'], 'tier_direct': risk['tier_direct'],
            'tier_all_deps': risk.get('tier_all_deps'), 'risk_score': risk['score'], 'categories': risk['cats'],
            'priority': q.get('priority'), 'flags': q.get('flags'), 'script': q.get('script'),
            'stages': stages, 'objectives': objectives,
            'prerequisites': {'quests': prereq, 'quest_conditions': cond_prereq,
                              'started_by': start.get(edid.lower(), []), 'stopped_by': stop.get(edid.lower(), [])},
            'dialogue': {'own_infos': len(q['infos']), 'own_topics': len(q['topics']),
                         'related_infos': len(r['infos']), 'related_topics': len(r['topics'])},
            'policy_rules': [x['kind'] for x in d.rules.get(edid, {}).get('rules', [])],
            'policy_curated': d.rules.get(edid, {}).get('curated'),
        })
    return {
        'schema': 1, 'questline': 'Old World Blues (OldWorldBlues.esm, load-order prefix 03)',
        'plugin': PLUGIN,
        'summary': {
            'quests': len(quests),
            'stages': sum(len(x['stages']) for x in quests),
            'objectives': sum(len(x['objectives']) for x in quests),
            'by_role': dict(collections.Counter(x['role'] for x in quests)),
            'by_tier': dict(sorted(collections.Counter(x['tier'] for x in quests).items())),
            'quests_with_stages': sum(1 for x in quests if x['stages']),
        },
        'quests': quests,
    }
