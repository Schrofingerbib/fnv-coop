"""inventory.json for the Mr. House questline: every quest with stages, objectives, prerequisites (questgraph + conditions +
who sets each stage), dialogue topic counts and risk tier. All facts come from out/questdata; nothing is hand-entered except the
scope sets in qlib (CORE / SYSTEM / SHARED / PREREQ)."""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qlib  # noqa: E402
import loadorder  # noqa: E402

STAGE_CMD = re.compile(r'(?i)\b(setstage|startquest|stopquest|completequest|failquest|resetquest)\s+(\w+)\s*(-?\w+)?')
GRAPH = os.path.join(qlib.ROOT, 'out', 'coverage', 'questgraph.json')


def _fmt_cond(d, c):
    p1 = c.get('p1')
    if isinstance(p1, str) and ':' in p1:
        p1 = d.ref_edid(p1) or p1
    p2 = c.get('p2')
    if isinstance(p2, str) and ':' in p2:
        p2 = d.ref_edid(p2) or p2
    arg = ', '.join(str(x) for x in (p1, p2) if x not in (None, 0, ''))
    return f'{c["func"]}({arg}) {c["op"]} {c["value"]}' + (' [or]' if c.get('or') else '')


def collect_setters(d, targets):
    """target quest edid -> [setter record]. Scans every script that can hold a stage command: standalone SCPT records, quest and
    stage scripts of all quests, and INFO begin/end result scripts."""
    tl = {t.lower(): t for t in targets}
    out = collections.defaultdict(list)
    qname = {q['ref']: q['edid'] for q in d.quests if q.get('edid')}

    def scan(src, rec):
        low = src.lower()
        if 'stage' not in low and 'quest' not in low:
            return
        for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
            line = qlib.strip_comment(raw)
            m = STAGE_CMD.search(line)
            if not m:
                continue
            tgt = tl.get(m.group(2).lower())
            if not tgt:
                continue
            arg = m.group(3)
            r = dict(rec)
            r.update(op=m.group(1).lower(), line=n, text=line.strip()[:120],
                     stage=int(arg) if arg and arg.lstrip('-').isdigit() else None, dynamic=not (arg and arg.lstrip('-').isdigit()))
            out[tgt].append(r)

    for s in d.scripts:
        sc = s.get('script') or {}
        if sc.get('source'):
            scan(sc['source'], dict(by='script', owner=s['edid'], where='script'))
    for q in d.quests:
        sd = q.get('script_data')
        # quest scripts are standalone SCPT records too (scanned above); stage scripts are not
        for st in q['stages']:
            for e in st['entries']:
                sc = e.get('script')
                if sc and sc.get('source'):
                    scan(sc['source'], dict(by='stage', owner=q['edid'], where=f'stage {st["index"]}'))
    for qref, infos in d.infos_by_quest.items():
        qe = qname.get(qref)
        for i in infos:
            for k in ('begin', 'end'):
                sc = i.get(k)
                if sc and sc.get('source'):
                    t = d.d_by_ref.get(i.get('topic')) or {}
                    scan(sc['source'], dict(by='info', owner=qe, where=f'info {i["ref"].split(":")[1]} {k}', topic=t.get('edid'),
                                            prompt=(i.get('prompt') or '')[:60]))
    return out


def graph_edges(d, quests):
    g = json.load(open(GRAPH, encoding='utf-8'))
    qs = set(quests)
    ins, outs = collections.defaultdict(list), collections.defaultdict(list)
    for e in g['edges']:
        if e['dst'] in qs:
            ins[e['dst']].append(dict(quest=e['src'], kind=e['kind'], strength=e['strength'], count=e['count'],
                                      evidence=[x['text'][:100] for x in e.get('evidence', [])[:2]]))
        if e['src'] in qs:
            outs[e['src']].append(dict(quest=e['dst'], kind=e['kind'], strength=e['strength'], count=e['count'],
                                       evidence=[x['text'][:100] for x in e.get('evidence', [])[:2]]))
    return ins, outs


def quest_entry(d, edid, setters, gin, gout):
    q = d.q_by_edid[edid]
    risk = d.risk[q['ref']]
    infos = d.infos_by_quest.get(q['ref'], [])
    nat = d.native['quests'].get(edid, {}).get('stages', {})
    stages = []
    sset = collections.defaultdict(list)
    for r in setters.get(edid, []):
        if r['op'] == 'setstage':
            sset[r['stage']].append(r)
    for st in sorted(q['stages'], key=lambda s: s['index']):
        ents = st['entries']
        flags = sorted({f for e in ents for f in e.get('flags', [])})
        o = nat.get(str(st['index']), {})
        stages.append(dict(
            index=st['index'], flags=flags, log=next((e.get('text') for e in ents if e.get('text')), None),
            has_script=any(e.get('script') for e in ents),
            entry_conditions=[_fmt_cond(d, c) for e in ents for c in e.get('conditions', [])],
            oracle=dict(unconditional=sorted({x['type'] for x in o.get('unconditional', [])}),
                        conditional=sorted({x['type'] for x in o.get('conditional', [])})),
            set_by=[dict(by=r['by'], owner=r['owner'], where=r['where'], line=r['line'], text=r['text'],
                         **({'topic': r['topic'], 'prompt': r['prompt']} if r['by'] == 'info' else {})) for r in sset.get(st['index'], [])],
        ))
    other_ops = [dict(op=r['op'], stage=r['stage'], by=r['by'], owner=r['owner'], where=r['where'], line=r['line'], text=r['text'])
                 for r in setters.get(edid, []) if r['op'] != 'setstage']
    dyn = [dict(op=r['op'], by=r['by'], owner=r['owner'], where=r['where'], line=r['line'], text=r['text'])
           for r in setters.get(edid, []) if r['op'] == 'setstage' and r['dynamic']]
    return dict(
        edid=edid, ref=q['ref'], formid=loadorder.console_id(q['ref'], d.plugins), name=q.get('name'), plugin=q['plugin'],
        role=qlib.role_of(edid), flags=q.get('flags'), start_enabled='start_enabled' in (q.get('flags') or []), priority=q.get('priority'),
        tier=risk['tier'], tier_direct=risk['tier_direct'], risk_score=risk['score'], risk_cats=risk['cats'],
        quest_script=dict(ref=q.get('script'), edid=(q.get('script_data') or {}).get('edid'),
                          blocks=sorted({m.group(1) for m in re.finditer(r'(?im)^\s*begin\s+(\w+)', (q.get('script_data') or {}).get('source', ''))})),
        stages=stages,
        objectives=[dict(index=o['index'], text=o.get('text'),
                         targets=[dict(ref=t['ref'], edid=d.ref_edid(t['ref']), conditions=[_fmt_cond(d, c) for c in t.get('conditions', [])])
                                  for t in o['targets']]) for o in sorted(q['objectives'], key=lambda o: o['index'])],
        prerequisites=dict(
            quest_conditions=[_fmt_cond(d, c) for c in q['conditions']],
            graph_in=sorted(gin.get(edid, []), key=lambda e: (e['strength'] != 'hard', e['quest'])),
            graph_out=sorted(gout.get(edid, []), key=lambda e: (e['strength'] != 'hard', e['quest'])),
            non_setstage_commands=other_ops, dynamic_setstage=dyn),
        dialogue=dict(topics=len(q.get('topics', [])), infos=len(infos), scripted_infos=sum(1 for i in infos if i.get('begin') or i.get('end'))),
    )


def build(d=None, mutate=None):
    d = d or qlib.data()
    owned = qlib.CORE + list(qlib.SYSTEM)
    shared = list(qlib.SHARED)
    prereq = list(qlib.PREREQ)
    everything = owned + shared + prereq
    setters = collect_setters(d, everything)
    gin, gout = graph_edges(d, everything)
    quests = [quest_entry(d, e, setters, gin, gout) for e in everything]
    for q in quests:
        e = q['edid']
        if e in qlib.SHARED:
            q['note'] = qlib.SHARED[e]
    tot_stages = sum(len(q['stages']) for q in quests if q['role'] in ('core', 'system'))
    doc = dict(
        schema=1, slug=qlib.SLUG, load_order=d.plugins, generated_from='out/questdata/quests.json, effects_native.json.gz, risk.json, out/coverage/questgraph.json, scripts/infos',
        summary=dict(
            quests_owned=len(owned), quests_shared=len(shared), quests_prereq=len(prereq), stages_owned=tot_stages,
            objectives_owned=sum(len(q['objectives']) for q in quests if q['role'] in ('core', 'system')),
            infos_owned=sum(q['dialogue']['infos'] for q in quests if q['role'] in ('core', 'system')),
            topics_owned=sum(q['dialogue']['topics'] for q in quests if q['role'] in ('core', 'system')),
            stages_without_setter=sum(1 for q in quests if q['role'] in ('core', 'system') for s in q['stages'] if not s['set_by']),
            tiers=dict(collections.Counter(q['tier'] for q in quests if q['role'] in ('core', 'system')))),
        quests=quests)
    if mutate:
        _mutate(doc, mutate)
    return doc


def _mutate(doc, how):
    """Broken variants the validator must catch."""
    qs = {q['edid']: q for q in doc['quests']}
    if how == 'drop_stage':
        qs['VMQHouse2']['stages'].pop()
    elif how == 'bad_formid':
        qs['VMQHouse3']['formid'] = '147888'
    elif how == 'bad_setter_line':
        s = next(s for s in qs['VHDHouseBattle']['stages'] if s['set_by'])
        s['set_by'][0]['line'] += 5
    elif how == 'drop_quest':
        doc['quests'].remove(qs['VHDHouseBattle'])
    elif how == 'bad_objective_target':
        qs['VMQHouse1']['objectives'][0]['targets'][0]['edid'] = 'NoSuchActorRef'
    elif how == 'wrong_stage_flag':
        qs['VMQHouse1']['stages'][-1]['flags'] = ['complete_quest']
    else:
        raise ValueError(how)
