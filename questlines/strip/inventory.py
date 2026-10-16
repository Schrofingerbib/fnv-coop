"""inventory.json: every quest of the Freeside / Strip questline with stages, objectives, prerequisites, dialogue counts, tier."""
import collections

import classify
import scope

GATE_KINDS = ('gate',)
DRIVE_KINDS = ('start', 'stage', 'var', 'complete', 'fail')


def ids(data, ref):
    return dict(ref=ref, formid=scope.ref_hex(ref), console_id=scope.console_hex(ref))


def graph_view(data, edid, members):
    """Gate edges into the quest (its prerequisites), edges driving it and edges it drives."""
    gates, driven_by, drives = [], [], []
    for e in data.graph['edges']:
        if e['dst'] == edid and e['kind'] in GATE_KINDS:
            gates.append(e)
        elif e['dst'] == edid and e['kind'] in DRIVE_KINDS:
            driven_by.append(e)
        elif e['src'] == edid and e['kind'] in DRIVE_KINDS:
            drives.append(e)

    def fmt(e, other):
        return dict(quest=e[other], kind=e['kind'], strength=e['strength'], count=e['count'],
                    evidence=[x['text'] for x in e['evidence'][:2]], in_scope=e[other] in members)
    order = lambda r: (r['strength'] != 'hard', -r['count'])  # noqa: E731
    return (sorted((fmt(e, 'src') for e in gates), key=order), sorted((fmt(e, 'src') for e in driven_by), key=order),
            sorted((fmt(e, 'dst') for e in drives), key=order))


def explicit_prereqs(data, q):
    """Cross-quest gating read by the quest's own conditions / stage-entry conditions (hard prerequisites)."""
    out = []
    conds = [('quest', c) for c in q['conditions']]
    for s in q['stages']:
        for e in s['entries']:
            conds += [(f'stage {s["index"]}', c) for c in e['conditions']]
    for where, c in conds:
        if c['func'] in classify.QUEST_DEP_FUNCS and c.get('p1') in data.by_ref and c['p1'] != q['ref']:
            out.append(dict(where=where, func=c['func'], quest=data.by_ref[c['p1']]['edid'], arg=c.get('p2'),
                            op=c['op'], value=c['value']))
    return out


def build(data):
    members = {e for e, _, _ in data.members()}
    quests = []
    for edid, group, detail in data.members():
        q = data.by_edid[edid]
        rk = data.risk.get(q['ref'], {})
        pol = data.policies.get(edid, {})
        rules_entry = data.rules['quests'].get(edid)
        infos = data.infos_by_quest.get(q['ref'], [])
        related = data.related_infos(edid) if group in ('core', 'extended') else []
        rel_topics = {i['topic'] for i, _ in related if i.get('topic')}
        containers = collections.Counter(data.by_ref[i['quest']]['edid'] for i, _ in related if i.get('quest') in data.by_ref)
        gates, driven_by, drives = graph_view(data, edid, members)
        sc = data.scenario(edid)
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            ents = s['entries']
            stages.append(dict(
                index=s['index'], log=next((e.get('text') for e in ents if e.get('text')), None),
                flags=sorted({f for e in ents for f in e['flags']}), has_script=any(e.get('script') for e in ents),
                conditions=sum(len(e['conditions']) for e in ents)))
        objectives = [dict(index=o['index'], text=o.get('text'),
                           targets=[dict(ref=t['ref'], edid=(data.forms.get(t['ref']) or {}).get('edid'))
                                    for t in o['targets']]) for o in q['objectives']]
        quests.append(dict(
            edid=edid, group=group, group_detail=detail if isinstance(detail, str) else None, name=q.get('name'),
            plugin=q['plugin'], **ids(data, q['ref']), runtime_formid='%08X' % scope.loadorder.runtime_formid(q['ref']),
            kind=rk.get('kind'), flags=q.get('flags'), priority=q.get('priority'),
            tier=rk.get('tier'), tier_direct=rk.get('tier_direct'), risk_score=rk.get('score'),
            risk_categories=rk.get('cats'),
            policy=dict(questline=pol.get('questline'), curated=bool(rules_entry and rules_entry.get('curated')),
                        rules=[r['kind'] for r in (rules_entry or {}).get('rules', [])]),
            stage_count=len(stages), stages=stages, objective_count=len(objectives), objectives=objectives,
            prerequisites=dict(
                explicit_conditions=explicit_prereqs(data, q),
                scenario_preconditions=(sc or {}).get('preconditions'),
                gated_by=gates, driven_by=driven_by, drives=drives),
            dialogue=dict(own_topics=len(q.get('topics', [])), own_infos=len(infos),
                          own_scripted_infos=sum(1 for i in infos if i.get('begin') or i.get('end')),
                          related_infos=len(related), related_topics=len(rel_topics),
                          related_containers=dict(containers.most_common(8)),
                          topics_total=len(q.get('topics', [])) + len(rel_topics)),
            scenario=dict(exists=bool(sc), steps=len((sc or {}).get('steps', [])), start_enabled=(sc or {}).get('start_enabled')),
        ))
    return dict(schema=1, slug=scope.SLUG, questline='Side: Freeside / Strip',
                policy_file='docs/quest-policies/side-freeside-strip.md',
                counts=dict(quests=len(quests), core=sum(1 for q in quests if q['group'] == 'core'),
                            extended=sum(1 for q in quests if q['group'] == 'extended'),
                            system=sum(1 for q in quests if q['group'] == 'system'),
                            stages=sum(q['stage_count'] for q in quests),
                            objectives=sum(q['objective_count'] for q in quests),
                            own_infos=sum(q['dialogue']['own_infos'] for q in quests)),
                cross_lane=[dict(edid=e, **ids(data, data.by_edid[e]['ref']), note=n) for e, n in scope.CROSS_LANE.items()],
                quests=quests)
