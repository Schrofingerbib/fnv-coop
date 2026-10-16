"""inventory.json: every quest of the questline with stages, objectives, prerequisites, dialogue counts and risk tier."""
import collections

import lib

START_KINDS = ('start', 'stage', 'var')


def cond_str(D, c):
    p1 = c.get('p1')
    if isinstance(p1, str):
        p1 = D.edid_of(p1) or p1
    s = '%s(%s%s) %s %s' % (c['func'], p1, '' if not c.get('p2') else ',%s' % c['p2'], c['op'], c['value'])
    if c.get('run_on') and c['run_on'] != 'subject':
        s += ' [run_on %s]' % c['run_on']
    return s


def target_entry(D, ref):
    if not ref:
        return None
    e = {'ref': ref, 'resolved': D.resolves(ref), 'edid': D.edid_of(ref), 'sig': D.sig_of(ref)}
    if e['resolved']:
        e['formid'] = D.hexid(ref)
    return e


def edges_for(D, edid):
    drivers = collections.OrderedDict()
    reads = collections.OrderedDict()
    dependents = collections.OrderedDict()
    for e in D.graph['edges']:
        if e['dst'] == edid:
            if e['kind'] in START_KINDS:
                d = drivers.setdefault(e['src'], {'quest': e['src'], 'kinds': collections.Counter(), 'strength': set(), 'count': 0})
            elif e['kind'] == 'gate':
                d = reads.setdefault(e['src'], {'quest': e['src'], 'kinds': collections.Counter(), 'strength': set(), 'count': 0})
            else:
                continue
            d['kinds'][e['kind']] += e['count']
            d['strength'].add(e['strength'])
            d['count'] += e['count']
            d.setdefault('evidence', e['evidence'][0] if e['evidence'] else None)
        if e['src'] == edid:
            d = dependents.setdefault(e['dst'], {'quest': e['dst'], 'kinds': collections.Counter(), 'strength': set(), 'count': 0})
            d['kinds'][e['kind']] += e['count']
            d['strength'].add(e['strength'])
            d['count'] += e['count']

    def fin(od):
        out = []
        for d in od.values():
            d['kinds'] = dict(sorted(d['kinds'].items()))
            d['strength'] = sorted(d['strength'])
            d['questline'] = (D.rules['quests'].get(d['quest']) or {}).get('questline')
            out.append(d)
        return sorted(out, key=lambda d: (-d['count'], d['quest']))
    return fin(drivers), fin(reads), fin(dependents)


def build(D, edids, drivers_map):
    quests = []
    neighbours = collections.OrderedDict()
    mine = set(edids)
    for e in edids:
        q = D.by_edid[e]
        r = D.risk[q['ref']]
        drv, reads, deps = edges_for(D, e)
        starters = [x for x in drv if 'start' in x['kinds']]
        for x in drv + reads + deps:
            if x['quest'] not in mine:
                n = neighbours.setdefault(x['quest'], {'quest': x['quest'], 'questline': x['questline'], 'edges': 0,
                                                      'name': (D.by_edid.get(x['quest']) or {}).get('name')})
                n['edges'] += x['count']
        dinfos = [i for i, hit in drivers_map.items() if e in hit]
        dq = collections.Counter(D.by_ref.get(D.info_by_ref[i].get('quest'), {}).get('edid') or '?' for i in dinfos)
        infos = D.infos_by_quest.get(q['ref'], [])
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            ents = []
            for en in s['entries']:
                ents.append({'log': en.get('text'), 'flags': en['flags'], 'conditions': [cond_str(D, c) for c in en['conditions']],
                             'has_script': bool(en.get('script') and en['script']['source'].strip())})
            stages.append({'index': s['index'], 'entries': ents, 'has_script': any(x['has_script'] for x in ents),
                           'flags': sorted({f for x in ents for f in x['flags']})})
        objs = []
        for o in sorted(q['objectives'], key=lambda o: o['index']):
            objs.append({'index': o['index'], 'text': o.get('text'),
                         'targets': [t for t in (dict(target_entry(D, t['ref']), conditions=[cond_str(D, c) for c in t['conditions']])
                                                 for t in o['targets'] if t['ref']) if t]})
        rules = (D.rules['quests'].get(e) or {})
        quests.append({
            'edid': e, 'formid': D.hexid(q['ref']), 'ref': q['ref'], 'name': q.get('name'), 'plugin': q['plugin'],
            'kind': r['kind'], 'tier': r['tier'], 'tier_direct': r['tier_direct'], 'risk_score': r['score'],
            'risk_categories': dict(sorted(r['cats'].items())), 'priority': q.get('priority'), 'flags': q.get('flags'),
            'start_enabled': 'start_enabled' in (q.get('flags') or []),
            'quest_script': (q.get('script_data') or {}).get('edid'),
            'quest_vars': [{'idx': v['idx'], 'name': v['name'], 'type': v['type']} for v in (q.get('script_data') or {}).get('vars', [])],
            'stages': stages, 'objectives': objs,
            'prerequisites': {
                'quest_conditions': [cond_str(D, c) for c in q['conditions']],
                'started_by': [{'quest': x['quest'], 'questline': x['questline'], 'evidence': x.get('evidence')} for x in starters],
                'driven_by': drv, 'reads_state_of': reads,
                'note': ('edges from out/coverage/questgraph.json: driven_by = other quests whose scripts start/stage/var-write this one; '
                         'reads_state_of = other quests whose state this quest\'s scripts or dialogue read'),
            },
            'dependents': deps,
            'dialogue': {
                'own_topics': len(q.get('topics', [])), 'own_infos': len(infos),
                'own_scripted_infos': sum(1 for i in infos if (i.get('begin') and i['begin']['source'].strip())
                                          or (i.get('end') and i['end']['source'].strip())),
                'speakers': sorted({D.edid_of(i['speaker']) or i['speaker'] for i in infos if i.get('speaker')}),
                'driver_infos': len(dinfos), 'driver_infos_by_quest': dict(sorted(dq.items())),
                'related_topics': len({D.info_by_ref[i]['topic'] for i in dinfos}) + len(q.get('topics', [])),
            },
            'rules_kinds': [x['kind'] for x in rules.get('rules', [])], 'rules_curated': bool(rules.get('curated')),
            'dep_scripts_touched': r['dep_scripts'], 'dep_scripts_untouched': r['dep_scripts_untouched'],
        })
    # quests next to the questline that no questline owns (policy gap) or that are timers/containers for it
    nb = sorted(neighbours.values(), key=lambda n: (-n['edges'], n['quest']))
    unowned = [n['quest'] for n in nb if not n['questline']]
    owned = set(D.rules['quests'])
    low_tier = [{'edid': q['edid'], 'name': q.get('name'), 'stages': len(q['stages']), 'objectives': len(q['objectives']),
                 'tier': D.risk[q['ref']]['tier']}
                for q in D.quests if (q.get('edid') or '').startswith('VMQ') and q['edid'] not in owned and (q['stages'] or q['objectives'])]
    return {
        'schema': 1, 'questline': lib.QUESTLINE, 'quests': quests,
        'neighbours': nb[:60], 'neighbours_total': len(nb),
        'unowned_neighbours': unowned[:60],
        'main_quests_below_policy_tier': low_tier,
        'totals': {
            'quests': len(quests), 'stages': sum(len(q['stages']) for q in quests),
            'stage_entries': sum(len(s['entries']) for q in quests for s in q['stages']),
            'objectives': sum(len(q['objectives']) for q in quests),
            'own_infos': sum(q['dialogue']['own_infos'] for q in quests),
            'driver_infos': len(drivers_map),
            'tiers': dict(collections.Counter(q['tier'] for q in quests)),
        },
    }
