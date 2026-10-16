"""inventory.json builder: every quest of the prologue questline with stages, objectives, prerequisites and dialogue size."""
import collections
import re

import model
import sweep as sweep_mod

SETSTAGE_RE = re.compile(r'(?im)\bsetstage\s+(\w+)\s*,?\s*(\d+)')


def _target(t):
    return dict(ref=t['ref'], edid=model.edid_of(t['ref']), name=model.name_of(t['ref']), conditions=len(t.get('conditions', [])))


def stage_writers():
    """{(quest EDID, stage): [{script, line, text}]} for every `SetStage <in-scope quest> N` anywhere in the game's scripts."""
    pat = re.compile(r'(?i)\bsetstage\s+(%s)\s*,?\s*(\d+)' % '|'.join(model.SCOPE))
    canon = {e.lower(): e for e in model.SCOPE}
    out = collections.defaultdict(list)
    for label, src in sorted(model.all_script_sources().items()):
        for n, raw in enumerate(src.splitlines(), 1):
            line = model.classify.strip_comment(raw)
            m = pat.search(line)
            if m:
                out[(canon[m.group(1).lower()], int(m.group(2)))].append(dict(script=label, line=n, text=line.strip()[:100]))
    return out


def _stage_rows(q, plan_q, writers):
    swept = {s['index'] for s in plan_q['stages']}
    excl = {s['index']: s['reason'] for s in plan_q['excluded_stages']}
    D = model.data()
    nat = D['native']['quests'].get(q['edid'], {}).get('stages', {})
    rows = []
    byidx = collections.OrderedDict()
    for s in q['stages']:
        byidx.setdefault(s['index'], []).append(s)
    for idx, ss in sorted(byidx.items()):
        entries = [e for s in ss for e in s['entries']]
        flags = sorted({f for e in entries for f in e.get('flags', [])})
        text = next((e['text'] for e in entries if e.get('text')), '')
        nq = next((e.get('next_quest') for e in entries if e.get('next_quest')), None)
        o = nat.get(str(idx), {})
        rows.append(dict(
            index=idx, text=text, flags=flags, entries=len(entries), has_script=any(e.get('script') for e in entries),
            conditions=[model.cond_text(c) for e in entries for c in e.get('conditions', [])],
            next_quest=model.edid_of(nq) if nq else None,
            oracle=dict(unconditional=[e['type'] for e in o.get('unconditional', [])],
                        conditional=[e['type'] for e in o.get('conditional', [])]),
            set_by=writers.get((q['edid'], idx), [])[:12], set_by_total=len(writers.get((q['edid'], idx), [])),
            sweep='swept' if idx in swept else 'excluded', excluded_reason=excl.get(idx)))
    return rows


def _graph(edid):
    D = model.data()
    g = D['graph']
    ins, outs = collections.defaultdict(list), collections.defaultdict(list)
    for e in g['edges']:
        ev = [x['text'][:100] for x in e['evidence'][:2]]
        row = dict(kind=e['kind'], strength=e['strength'], count=e['count'], evidence=ev)
        if e['dst'] == edid:
            ins[e['src']].append(row)
        if e['src'] == edid:
            outs[e['dst']].append(row)
    def pack(d):
        return [dict(quest=k, links=v) for k, v in sorted(d.items())]
    drivers = [x for x in pack(ins) if any(l['kind'] in ('stage', 'start', 'var', 'complete') for l in x['links'])]
    reads = [x for x in pack(ins) if all(l['kind'] == 'gate' for l in x['links'])]
    return dict(driven_by=drivers, reads_state_of=reads, drives_or_gates=pack(outs))


def _dialogue(q):
    D = model.data()
    infos = model.in_scope_infos(q['edid'])
    scripted = [i for i in infos if i.get('begin') or i.get('end')]
    return dict(topics_total=len(q['topics']), infos_total=len(D['infos'].get(q['ref'], [])), infos_in_scope=len(infos),
                scripted_infos_in_scope=len(scripted),
                skill_gated_infos=sum(1 for i in infos if any(c['func'] == 'GetActorValue' and c.get('p1') in model.SKILL_AVS
                                                              for c in i['conditions'])),
                say_once_infos=sum(1 for i in infos if 'say_once' in i['flags']))


def _cross_writes(edid):
    """Stage writes that this quest's in-scope scripts make into OTHER quests (cross-line coupling)."""
    D = model.data()
    canon = {e.lower(): e for e in D['by_edid']}
    out = collections.Counter()
    for s in model.collect_scripts():
        if s['quest'] != edid:
            continue
        for m in SETSTAGE_RE.finditer(s['source']):
            tgt = canon.get(m.group(1).lower())
            if tgt and tgt != edid:
                out['%s %s' % (tgt, m.group(2))] += 1
    return dict(out)


def build_inventory():
    D = model.data()
    plan = {q['edid']: q for q in sweep_mod.build_plan()['quests']}
    writers = stage_writers()
    quests = []
    for edid, role in model.SCOPE.items():
        q = D['by_edid'][edid]
        rk = D['risk'][q['ref']]
        pol = D['policies'].get(edid)
        rules = D['rules']['quests'].get(edid)
        sd = q.get('script_data') or {}
        quests.append(dict(
            edid=edid, formid=model.hexid(q['ref']), ref=q['ref'], name=q.get('name'), plugin=q['plugin'], role=role,
            flags=q['flags'], priority=q.get('priority'),
            quest_script=dict(edid=sd.get('edid'), ref=q.get('script')) if sd else None,
            risk=dict(tier=rk['tier'], tier_direct=rk['tier_direct'], score=rk['score'], kind=rk['kind'], cats=rk['cats'],
                      has_cutscene=rk['has_cutscene'], has_move=rk['has_move'], mutates_player=rk['mutates_player']),
            policy=dict(questline=(pol or {}).get('questline'), curated=(rules or {}).get('curated'),
                        rule_kinds=[r['kind'] for r in (rules or {}).get('rules', [])], in_rules_json=rules is not None),
            stages=_stage_rows(q, plan[edid], writers),
            objectives=[dict(index=o['index'], text=o['text'], targets=[_target(t) for t in o['targets'] if t.get('ref')],
                             target_conditions=[model.cond_text(c) for t in o['targets'] for c in t.get('conditions', [])])
                        for o in q['objectives']],
            prerequisites=_graph(edid),
            quest_conditions=[model.cond_text(c) for c in q['conditions']],
            dialogue=_dialogue(q),
            cross_quest_stage_writes=_cross_writes(edid),
            scope_note=('stages 5 and 10 only (Benny located)' if role == 'boundary' else None)))
    refs = []
    for e in model.REFERENCE:
        q = D['by_edid'].get(e)
        if q:
            refs.append(dict(edid=e, formid=model.hexid(q['ref']), name=q.get('name'), reason='M1 Goodsprings, done (out/questdata/m1_goodsprings.json)'))
    gates = []
    for e in model.GATE_ONLY:
        q = D['by_edid'].get(e)
        if q:
            gates.append(dict(edid=e, formid=model.hexid(q['ref']), name=q.get('name'), topics=len(q['topics']),
                              reason='reads VMQ01 state in INFO conditions only; companion dialogue owned by the companion/Act lines'))
    drivers = []
    for s in model.driver_scripts():
        drivers.append(dict(script=s['script_edid'], ref=s['ref'], lines=len(s['source'].splitlines()),
                            blocks=sorted(set(re.findall(r'(?im)^\s*begin\s+(\w+)', s['source'])))))
    n_st = sum(len(q['stages']) for q in quests)
    n_sw = sum(1 for q in quests for s in q['stages'] if s['sweep'] == 'swept')
    return dict(
        schema=1, questline='prologue',
        scope=('They Went That-a-Way (VMQ01) and the dialogue/support quests that drive it: Primm (Nash, Deputy Beagle, Primm Slim), Nipton '
               '(Booted), Novac (Manny Vargas, Victor in Novac), Boulder City (Jessup), up to reaching Benny (VMQTops stages 5/10). '
               'Goodsprings (VCG00-03, VMS16) is the M1 questline and only referenced.'),
        quests=quests, reference_done=refs, gate_only=gates, driver_object_scripts=drivers,
        driver_exclusions=model.DRIVER_EXCLUDE,
        summary=dict(quests=len(quests), stages=n_st, stages_swept=n_sw, stages_excluded=n_st - n_sw,
                     objectives=sum(len(q['objectives']) for q in quests),
                     topics=sum(q['dialogue']['topics_total'] for q in quests),
                     scripted_infos_in_scope=sum(q['dialogue']['scripted_infos_in_scope'] for q in quests),
                     driver_object_scripts=len(drivers)))
