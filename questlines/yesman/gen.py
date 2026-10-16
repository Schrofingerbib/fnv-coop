"""Generator for questlines/yesman/: inventory.json, sweep.json, gaps.json, gaps.md, dialogue_specs.json.

    python -I questlines/yesman/gen.py            # (re)write the generated files
    python -I questlines/yesman/gen.py --check    # exit 1 if a file on disk differs from a fresh build
    python -I questlines/yesman/gen.py --refresh-fixtures   # re-pin rules.json / questgraph.json entries into fixtures/

Everything is derived from out/questdata (quests.json, infos.jsonl.gz, dials.json.gz, forms.json.gz, refnames.json.gz,
risk.json), out/coverage/questgraph.json and docs/quest-policies/rules.json.  No FormID is invented: every ref/edid that is
emitted comes from those files (run_tests.py re-checks that).
"""
import collections
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import scan  # noqa: E402
import funcmap  # noqa: E402
import classify  # noqa: E402  (via scan's sys.path insert of tools/questdata)

IN = os.path.join(REPO, 'out', 'questdata')
SLUG = 'yesman'
QUESTLINE = 'Main: Yes Man (Independent Vegas)'
CORE = ['VMQYesMan01a', 'VMQYesMan01', 'VMQYesMan02', 'VMQYesManFailsafe', 'VMQYesMan03', 'VHDIndependentBattle']
SUPPORT = ['VMQYesManSupport', 'VHDOliverDeathTimer']
MEMBERS = CORE + SUPPORT
# quests that drive our objectives/stages but belong to another lane; listed as touched/shared, never swept here
SHARED_HINT = {
    'VMQTops': 'Main: Shared (Ring-a-Ding-Ding and Act 2); hosts the Yes Man dialogue topics',
    'vHDBattleController': 'Hoover Dam battle controller shared by all four endings',
    'VHDKimballSpeech': "President Kimball's speech, shared with House/NCR assassination branch",
    'VHDOliverRetreatTimer': 'Legion-battle twin of VHDOliverDeathTimer',
    'VEnding': 'ending slideshow quest started by the battle (stage 100)',
    'SecuritronVaultExplodeQuest': 'House/Fort path: destroys the Securitron army (Yes Man 01a obj 48)',
}
SEEN_FILES = ['inventory.json', 'sweep.json', 'gaps.json', 'gaps.md', 'dialogue_specs.json', 'policy_table.md']


def jdump(o):
    return json.dumps(o, indent=1, ensure_ascii=False) + '\n'


def gz(name):
    with gzip.open(os.path.join(IN, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


def hexid(ref):
    """'FalloutNV.esm:157321' -> '157321' (console id; all members are FalloutNV.esm = load index 00)."""
    return ref.split(':')[1].lstrip('0').upper() or '0'


class Ctx:
    pass


# Inputs that other lanes rewrite while we work (L9 owns rules.json and questgraph.json) are pinned under fixtures/ so the
# tests never depend on a file that is being regenerated; `gen.py --refresh-fixtures` re-pins them from the live files.
FIX = os.path.join(HERE, 'fixtures')
LIVE_RULES = os.path.join(REPO, 'docs', 'quest-policies', 'rules.json')
LIVE_GRAPH = os.path.join(REPO, 'out', 'coverage', 'questgraph.json')
EXTRA_RULE_QUESTS = ['VMQTops', 'VMS45', 'VEnding', 'VFreeformTheFort', 'VMQ03a', 'vHDBattleController']


def pinned_inputs():
    with open(os.path.join(FIX, 'questgraph_snapshot.json'), encoding='utf-8') as f:
        graph = json.load(f)
    with open(os.path.join(FIX, 'rules_snapshot.json'), encoding='utf-8') as f:
        rules = json.load(f)
    return graph, rules


def refresh_fixtures():
    """Re-pin the rules/questgraph entries this questline needs (members, hosting quests, patch targets, shared neighbours)."""
    os.makedirs(FIX, exist_ok=True)
    live_rules = json.load(open(LIVE_RULES, encoding='utf-8'))
    live_graph = json.load(open(LIVE_GRAPH, encoding='utf-8'))
    d, infos = classify.load(IN)
    byref = {q['ref']: q for q in d['quests']}
    rx = re.compile(r'\b(' + '|'.join(MEMBERS) + r')\b', re.I)
    want = set(MEMBERS) | set(EXTRA_RULE_QUESTS)
    for v in infos.values():
        for i in v:
            if any(i.get(k) and rx.search(i[k]['source']) for k in ('begin', 'end')):
                want.add(byref.get(i['quest'], {}).get('edid'))
    patch_path = os.path.join(HERE, 'rules.patch.json')
    if os.path.exists(patch_path):
        pj = json.load(open(patch_path, encoding='utf-8'))
        want |= {p['quest'] for p in pj.get('patch', [])} | {x['quest'] for x in pj.get('cross_lane', [])}
    want.discard(None)
    import hashlib
    raw = open(LIVE_RULES, 'rb').read()
    snap = {'schema': live_rules.get('schema'), 'pinned_from': 'docs/quest-policies/rules.json',
            'live_sha256_when_pinned': hashlib.sha256(raw).hexdigest(),
            'quests': {k: live_rules['quests'][k] for k in sorted(want) if k in live_rules['quests']}}
    edges = [e for e in live_graph['edges'] if e['src'] in MEMBERS or e['dst'] in MEMBERS]
    ids = {e['src'] for e in edges} | {e['dst'] for e in edges}
    gsnap = {'schema': live_graph.get('schema'), 'pinned_from': 'out/coverage/questgraph.json',
             'nodes': [n for n in live_graph['nodes'] if n['id'] in ids], 'edges': edges}
    with open(os.path.join(FIX, 'rules_snapshot.json'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(jdump(snap))
    with open(os.path.join(FIX, 'questgraph_snapshot.json'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(jdump(gsnap))
    print('pinned %d rule entries, %d graph edges' % (len(snap['quests']), len(edges)))


def live_drift():
    """-> list of quests whose live rules.json entry differs from the pinned one (informational, never a failure)."""
    snap = json.load(open(os.path.join(FIX, 'rules_snapshot.json'), encoding='utf-8'))
    try:
        live = json.load(open(LIVE_RULES, encoding='utf-8'))['quests']
    except (OSError, ValueError):
        return ['<live rules.json unreadable>']
    return [k for k, v in snap['quests'].items() if live.get(k) != v] + [k for k in snap['quests'] if k not in live]


def load():
    c = Ctx()
    c.d, c.infos = classify.load(IN)
    c.extra = classify.load_extra(IN)
    c.forms = gz('forms.json.gz')
    c.refnames = gz('refnames.json.gz')
    c.dials = {x['ref']: x for x in gz('dials.json.gz')}
    c.risk = json.load(open(os.path.join(IN, 'risk.json'), encoding='utf-8'))['quests']
    c.graph, c.rules = pinned_inputs()
    c.by = {q['edid']: q for q in c.d['quests'] if q.get('edid')}
    c.byref = {q['ref']: q for q in c.d['quests']}
    c.globals_lc = {g['edid'].lower() for g in c.d['globals'] if g.get('edid')}
    c.allinfos = [i for v in c.infos.values() for i in v]
    rx = re.compile(r'\b(' + '|'.join(MEMBERS) + r')\b', re.I)
    c.rx = rx
    rel = []
    for i in c.allinfos:
        for k in ('begin', 'end'):
            s = i.get(k)
            if s and rx.search(s['source']):
                rel.append(i)
                break
    c.related = sorted(rel, key=lambda i: i['ref'])
    return c


def name_of(c, ref):
    f = c.forms.get(ref) or c.refnames.get(ref) or {}
    return f.get('edid') or f.get('name')


# ----------------------------------------------------------------------------------------------- scope scripts
def script_items(c):
    """-> list of dict(scope, id, quest, source, ...).  scope in quest_script | stage | info | dep."""
    out = []
    for e in MEMBERS:
        q = c.by[e]
        sd = q.get('script_data')
        if sd and sd.get('source'):
            out.append(dict(scope='quest_script', id=sd.get('edid') or (e + ' quest script'), quest=e, source=sd['source'],
                            label=e + ' quest script'))
        for s in sorted(q['stages'], key=lambda s: s['index']):
            for en in s['entries']:
                sc = en.get('script')
                if sc and sc['source'].strip():
                    out.append(dict(scope='stage', id='%s stage %d' % (e, s['index']), quest=e, source=sc['source'],
                                    label='%s stage %d' % (e, s['index']), stage=s['index']))
    for i in c.related:
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and c.rx.search(sc['source']):
                cont = c.byref.get(i['quest'], {}).get('edid')
                topic = c.dials.get(i['topic'], {}).get('edid')
                out.append(dict(scope='info', id='info %s %s' % (i['ref'].split(':')[1], k), quest=cont, source=sc['source'],
                                label='info %s (%s) %s' % (i['ref'].split(':')[1], topic, k), info=i['ref'], topic=topic))
    seen = set()
    for e in MEMBERS:
        for dr in c.by[e].get('dep_refs') or []:
            for label, sc in classify.dep_scripts(dr, c.extra):
                if id(sc) in seen:
                    continue
                seen.add(id(sc))
                nm = scan.script_name(sc['source']) or label
                out.append(dict(scope='dep', id=nm, quest=e, source=sc['source'], label=label))
    return out


PLAYER_RE = re.compile(r'\b(player|playerref)\b', re.I)


def records(c):
    """Flatten every scanned statement with its classification (cached on the context)."""
    if getattr(c, '_recs', None) is not None:
        return c._recs
    recs = []
    kills = collections.Counter()
    items = script_items(c)
    for it in items:
        sc = scan.scan(it['source'])
        for r in sc:
            if r['kind'] == 'cmd' and r['fn'].lower() in ('kill', 'killactor'):
                kills[(it['scope'], it['id'])] += 1
        for r in sc:
            subj_player = scan.is_player(r['subj']) or (r['subj'] is None and r['fn'].lower() in
                                                          ('rewardxp', 'rewardkarma', 'addperk', 'removeperk', 'addreputation',
                                                           'removereputation', 'setreputation', 'addnote', 'removenote',
                                                           'enableplayercontrols', 'disableplayercontrols'))
            if r['kind'] == 'query':
                lc = r['fn'].lower()
                playerish = scan.is_player(r['subj']) or bool(PLAYER_RE.search(r['text'])) or lc in (
                    'isactionref', 'getactionref', 'iskiller', 'getplayerteammate', 'getreputationthreshold', 'getpcenemyofaction')
                if lc == 'getsecondspassed' and it['scope'] in ('quest_script', 'stage', 'info'):
                    recs.append(dict(r, scope=it['scope'], script=it['id'], label=it['label'], quest=it['quest'],
                                     cls={'effect': None, 'capture': 'n/a', 'apply': 'n/a', 'severity': 'medium',
                                          'tags': ['timer'], 'note': 'host-only timer; effects it triggers run in a quest-script GameMode block (ambient)',
                                          'ambient': False}))
                    continue
                if lc in funcmap.QUERY_PLAYER and playerish:
                    recs.append(dict(r, scope=it['scope'], script=it['id'], label=it['label'], quest=it['quest'],
                                     cls={'effect': None, 'capture': 'n/a', 'apply': 'n/a', 'severity': 'medium',
                                          'tags': ['player-query'], 'note': 'reduce with: ' + funcmap.QUERY_PLAYER[lc],
                                          'ambient': False, 'reduction': funcmap.QUERY_PLAYER[lc]}))
                continue
            cls = funcmap.classify_occurrence(r, it['scope'], c.globals_lc, subj_player)
            if r['kind'] == 'cmd' and r['fn'].lower() == 'startquest' and r['args'] and r['args'][0].lower() == 'vending':
                cls['tags'] = sorted(set(cls['tags']) | {'ending'})
                cls['severity'] = 'high'
                cls['note'] = (cls['note'] + '; ' if cls['note'] else '') + 'StartQuest VEnding = ending slideshow'
            recs.append(dict(r, scope=it['scope'], script=it['id'], label=it['label'], quest=it['quest'], cls=cls))
    for rr in recs:
        if rr['kind'] == 'cmd' and rr['fn'].lower() in ('kill', 'killactor') and kills[(rr['scope'], rr['script'])] >= funcmap.KILL_BATCH:
            rr['cls']['tags'] = sorted(set(rr['cls']['tags']) | {'kill-batch'})
            rr['cls']['severity'] = 'medium'
    c._recs = (recs, items)
    return c._recs


# ----------------------------------------------------------------------------------------------- inventory
def graph_edges(c):
    inb = collections.defaultdict(list)
    outb = collections.defaultdict(list)
    for e in c.graph['edges']:
        if e['dst'] in MEMBERS and e['src'] != e['dst']:
            inb[e['dst']].append(e)
        if e['src'] in MEMBERS and e['src'] != e['dst']:
            outb[e['src']].append(e)
    return inb, outb


def edge_row(e, other):
    return {'quest': other, 'kind': e['kind'], 'strength': e['strength'], 'count': e['count'],
            'evidence': [x['text'][:110] for x in e['evidence'][:2]]}


def related_stats(c):
    """per member quest: infos whose result scripts touch it / infos whose conditions gate on it."""
    res = {e: {'result_infos': 0, 'topics': set(), 'gating_infos': 0, 'gating_topics': set(), 'host_quests': collections.Counter()}
           for e in MEMBERS}
    for i in c.allinfos:
        cs = i['conditions']
        for e in MEMBERS:
            ref = c.by[e]['ref']
            if any(x.get('p1') == ref for x in cs):
                res[e]['gating_infos'] += 1
                res[e]['gating_topics'].add(i['topic'])
    for i in c.related:
        for e in MEMBERS:
            for k in ('begin', 'end'):
                s = i.get(k)
                if s and re.search(r'\b' + e + r'\b', s['source'], re.I):
                    res[e]['result_infos'] += 1
                    res[e]['topics'].add(i['topic'])
                    res[e]['host_quests'][c.byref.get(i['quest'], {}).get('edid')] += 1
                    break
    return res


def build_inventory(c):
    inb, outb = graph_edges(c)
    rs = related_stats(c)
    quests = []
    for e in MEMBERS:
        q = c.by[e]
        rk = c.risk[q['ref']]
        qrules = (c.rules['quests'].get(e) or {}).get('rules', [])
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            ent = s['entries']
            stages.append({'index': s['index'], 'log': next((x.get('text') for x in ent if x.get('text')), None),
                           'flags': sorted({f for x in ent for f in x['flags']}),
                           'has_script': any(x.get('script') and x['script']['source'].strip() for x in ent),
                           'conditions': [{'func': cc['func'], 'op': cc['op'], 'value': cc['value'],
                                           'p1': name_of(c, cc['p1']) if isinstance(cc.get('p1'), str) else cc.get('p1')}
                                          for x in ent for cc in x['conditions']]})
        objs = []
        for o in q['objectives']:
            objs.append({'index': o['index'], 'text': o.get('text'),
                         'targets': [{'ref': t['ref'], 'edid': name_of(c, t['ref'])} for t in o['targets'] if t['ref']]})
        sd = q.get('script_data') or {}
        quests.append({
            'edid': e, 'ref': q['ref'], 'console_id': hexid(q['ref']), 'name': q.get('name'),
            'role': 'core' if e in CORE else 'support', 'tier': rk['tier'], 'tier_direct': rk['tier_direct'],
            'risk_score': rk['score'], 'risk_cats': rk['cats'], 'flags': q.get('flags'), 'priority': q.get('priority'),
            'stages': stages, 'objectives': objs,
            'quest_script': {'edid': sd.get('edid'), 'vars': [{'idx': v['idx'], 'name': v['name'], 'type': v['type']}
                                                              for v in sd.get('vars', [])]} if sd else None,
            'prerequisites': [edge_row(x, x['src']) for x in sorted(inb[e], key=lambda x: (x['kind'], x['src']))],
            'unlocks': [edge_row(x, x['dst']) for x in sorted(outb[e], key=lambda x: (x['kind'], x['dst']))],
            'dialogue': {'own_infos': len(c.infos.get(q['ref'], [])), 'own_topics': len(q.get('topics') or []),
                         'related_result_infos': rs[e]['result_infos'], 'related_topics': len(rs[e]['topics']),
                         'gating_infos': rs[e]['gating_infos'], 'gating_topics': len(rs[e]['gating_topics']),
                         'hosted_in_quests': dict(sorted(rs[e]['host_quests'].items(), key=lambda kv: -kv[1]))},
            'rules_current': [r['kind'] for r in qrules],
        })
    members = set(MEMBERS)
    touched = {}
    for e in c.graph['edges']:
        for a, b in ((e['src'], e['dst']), (e['dst'], e['src'])):
            if b in members and a not in members and a in c.by:
                t = touched.setdefault(a, {'edid': a, 'ref': c.by[a]['ref'], 'name': c.by[a].get('name'),
                                           'stages': len(c.by[a]['stages']), 'why': set(), 'owner_hint': SHARED_HINT.get(a)})
                t['why'].add('%s %s %s %s' % (e['src'], '->', e['dst'], e['kind']))
    node_line = {n['id']: n.get('line') for n in c.graph['nodes']}
    for t in touched.values():
        t['why'] = sorted(t['why'])[:6]
        t['graph_line'] = node_line.get(t['edid'])
    tl = sorted(touched.values(), key=lambda t: t['edid'])
    return {
        'slug': SLUG, 'questline': QUESTLINE, 'policy_file': 'docs/quest-policies/main-yes-man.md',
        'source': 'out/questdata (quests.json, infos.jsonl.gz, dials.json.gz, risk.json), out/coverage/questgraph.json',
        'summary': {'quests': len(quests), 'core': len(CORE), 'support': len(SUPPORT),
                    'stages': sum(len(q['stages']) for q in quests), 'objectives': sum(len(q['objectives']) for q in quests),
                    'related_result_infos': len(c.related), 'related_topics': len({i['topic'] for i in c.related}),
                    'touched_quests': len(tl)},
        'critical_path': ['VCG00', 'VCG01', 'VCG02', 'VCG03', 'VMQ01', 'VMQTops', 'VMQYesMan01', 'VMQYesMan02', 'VMQYesMan01a',
                          'VMQYesMan03', 'vHDBattleController', 'VHDIndependentBattle'],
        'critical_path_source': 'out/coverage/summary.md (Yes Man chain)',
        'quests': quests, 'touched_quests': tl,
    }


# ----------------------------------------------------------------------------------------------- sweep
def con(cmd, why):
    return {'op': 'console', 'cmd': cmd, 'why': why}


def sweep_setup(c):
    """Per (quest, stage) ordered console prerequisites from a clean state (after test.quest.reset).
    Natural order of the questline is used; predecessors are the stages the game itself sets before this one."""
    ids = {e: hexid(c.by[e]['ref']) for e in c.by if e in MEMBERS or e in ('VMQ03a', 'VMQTops', 'VHDKimballSpeech')}
    st = lambda q, n: con('setstage %s %d' % (ids[q], n), 'predecessor stage %s %d' % (q, n))  # noqa: E731
    start = lambda q: con('startquest %s' % ids[q], 'start %s' % q)  # noqa: E731
    disp = lambda q, o, v=1: con('setobjectivedisplayed %s %d %d' % (ids[q], o, v), 'objective %s %d displayed' % (q, o))  # noqa: E731
    done = lambda q, o: con('setobjectivecompleted %s %d 1' % (ids[q], o), 'objective %s %d completed' % (q, o))  # noqa: E731
    H = 'VHDIndependentBattle'
    spec = {
        ('VMQYesMan01', 0): dict(pre=[start('VMQYesMan01')], note='stage 0 has no result script (quest entry); proves startquest/stage 0 capture only'),
        ('VMQYesMan01', 70): dict(pre=[start('VMQYesMan01')], note='Yes Man mainframe upload (VGenericTimer / Tops topic 1588DA set it)'),
        ('VMQYesMan01', 75): dict(pre=[start('VMQYesMan01'), st('VMQYesMan01', 70)]),
        ('VMQYesMan01', 80): dict(pre=[start('VMQYesMan01'), st('VMQYesMan01', 70), st('VMQYesMan01', 75)]),
        ('VMQYesMan01', 100): dict(pre=[start('VMQYesMan01'), st('VMQYesMan01', 70), st('VMQYesMan01', 75), st('VMQYesMan01', 80)],
                                   note='complete_quest; RewardXP 500 + VStoryState/VStoryIndependentState globals'),
        ('VMQYesMan01a', 100): dict(pre=[start('VMQYesMan01a'), disp('VMQYesMan01a', 45)],
                                    note='upgrade branch; VMQYesMan02 left not running so the conditional Failsafe objective branch fires'),
        ('VMQYesMan01a', 110): dict(pre=[start('VMQYesMan01a'), disp('VMQYesMan01a', 48)],
                                    note='destroy branch; objective 48 displayed so the "hide 48" branch fires'),
        ('VMQYesMan02', 100): dict(pre=[start('VMQYesMan02'), st('VMQYesMan01', 100)],
                                   note='VMQYesMan01 100 done first so the VStoryState 40 / VStoryIndependentState 2 branch fires'),
        ('VMQYesManFailsafe', 1): dict(pre=[start('VMQYesManFailsafe')],
                                       note='VMQTops.MetYesMan is 0 on a clean state -> objective 1 branch; BennySuiteDoorREF.Unlock'),
        ('VMQYesManFailsafe', 100): dict(pre=[start('VMQYesManFailsafe'), st('VMQYesManFailsafe', 1)], note='complete_quest, empty result script'),
        ('VMQYesMan03', 100): dict(pre=[start('VMQYesMan03'), start('VMQ03a')],
                                   note='VMQ03a started so the body of `if GetQR VMQ03a` (Kimball assassination teardown, StopQuest x2) runs'),
        (H, 10): dict(pre=[start(H)], phase='last', note='battle start: StartQuest vHDBattleController, SetEnemy PlayerFaction Legion, EnableFastTravel 0'),
        (H, 20): dict(pre=[start(H), st(H, 10)], phase='last', note='alternative to 25 (Send power to the Fort)'),
        (H, 25): dict(pre=[start(H), st(H, 10)], phase='last', note='alternative to 20 (Destroy the dam generators)'),
        (H, 30): dict(pre=[start(H), st(H, 10), st(H, 20)], phase='last'),
        (H, 40): dict(pre=[start(H), st(H, 10), st(H, 20), st(H, 30)], phase='last'),
        (H, 50): dict(pre=[start(H), st(H, 10), st(H, 20), st(H, 30), st(H, 40)], phase='last',
                      note='VLegateShowdown vars are 0 on a clean state so the 25-NPC Kill batch is skipped (conditional)'),
        (H, 90): dict(pre=[start(H), st(H, 10), st(H, 20), st(H, 30), st(H, 40), st(H, 50)], phase='last'),
        (H, 100): dict(pre=[start(H), st(H, 10), st(H, 20), st(H, 30), st(H, 40), st(H, 50), st(H, 90)], phase='last',
                       disposable_save=True,
                       note='DisablePlayerControls + StartQuest VEnding (ending slideshow). Run LAST on a disposable save; '
                            'variant bSecuritronVaultActive>=1 changes VStoryState 64 -> 65',
                       variants=[{'name': 'vault_active', 'pre': [{'op': 'apply_txn', 'why': 'bSecuritronVaultActive = 1 (VStoryState 65 branch)',
                                                                  'effects': [{'type': 'SetScriptVar', 'owner': c.by[H]['ref'].lower(),
                                                                               'var': 3, 'value': 1}]}]}]),
    }
    return spec, ids


def objective_steps(c, ids):
    steps = {}
    for e in CORE:
        q = c.by[e]
        steps[e] = [{'objective': o['index'],
                     'displayed': 'setobjectivedisplayed %s %d 1' % (ids[e], o['index']),
                     'completed': 'setobjectivecompleted %s %d 1' % (ids[e], o['index'])}
                    for o in sorted(q['objectives'], key=lambda o: o['index'])]
    return steps


def quest_script_runs(c, e, ids):
    """Runs for script-only quests (no stages): drive the quest-script GameMode block and observe it for `wait_s` seconds."""
    q = c.by[e]
    vars_ = {v['name']: v['idx'] for v in (q.get('script_data') or {}).get('vars', [])}
    if e == 'VHDOliverDeathTimer':
        return [{'name': 'oliver_death_cutscene', 'wait_s': 20,
                 'setup': [{'op': 'reset', 'quests': [ids[e]]}, con('startquest %s' % ids[e], 'start the timer quest'),
                           {'op': 'apply_txn', 'why': 'bRunTimer = 1 starts the 7-step cutscene state machine',
                            'effects': [{'type': 'SetScriptVar', 'owner': q['ref'].lower(), 'var': vars_['bRunTimer'], 'value': 1}]}],
                 'expect': 'Player.moveto x2, Kill, DisablePlayerControls, fades: all ambient today (droppedNoScope); a green capture needs C2',
                 'note': 'also needs the Hoover Dam refs enabled (stage 10 of VHDIndependentBattle) for a faithful run'}]
    if e == 'VMQYesManSupport':
        return [{'name': 'yes_man_repositioning', 'wait_s': 30,
                 'setup': [{'op': 'reset', 'quests': [ids[e]]}, con('startquest %s' % ids[e], 'quest is start_enabled; (re)start it')],
                 'expect': 'MoveTo/Resurrect of VMQTopsYesManREF driven by GetInSameCell/GetDistance against the player (needs C2, C5)',
                 'note': 'no stage can be set; observe Yes Man position over time'}]
    return []


def build_sweep(c):
    spec, ids = sweep_setup(c)
    inv_ids = set()
    quests = []
    excluded = []
    for e in MEMBERS:
        q = c.by[e]
        rk = c.risk[q['ref']]
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            key = (e, s['index'])
            sp = spec.get(key)
            if sp is None:
                excluded.append({'quest': e, 'stage': s['index'], 'reason': 'no sweep spec (should not happen)'})
                continue
            stages.append({'index': s['index'],
                           'log': next((x.get('text') for x in s['entries'] if x.get('text')), None),
                           'flags': sorted({f for x in s['entries'] for f in x['flags']}),
                           'has_script': any(x.get('script') and x['script']['source'].strip() for x in s['entries']),
                           'findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text'])
                                        for f in rk['stage_findings'] if f['where'] == 'stage %d' % s['index']],
                           'setup': sp['pre'], 'phase': sp.get('phase', 'normal'), 'note': sp.get('note'),
                           'disposable_save': bool(sp.get('disposable_save')), 'variants': sp.get('variants', []),
                           'action': con('setstage %s %d' % (ids[e], s['index']), 'stage under test')})
        quests.append({'edid': e, 'ref': q['ref'], 'console_id': ids[e], 'name': q.get('name'),
                       'role': 'core' if e in CORE else 'system', 'tier': rk['tier'], 'tier_direct': rk['tier_direct'],
                       'cats': rk['cats'], 'priority': q.get('priority'), 'flags': q.get('flags'),
                       'script': (q.get('script_data') or {}).get('edid'),
                       'reset': [{'op': 'reset', 'quests': sorted({ids[e]} | {m.group(1) for sp_ in spec.items() if sp_[0][0] == e
                                                                              for op in sp_[1]['pre'] if op.get('cmd')
                                                                              for m in [re.match(r'\w+ ([0-9A-F]+)', op['cmd'])] if m})}],
                       'stages': stages,
                       'objectives': [{'index': o['index'], 'text': o.get('text'),
                                       'targets': [{'ref': t['ref'], 'edid': name_of(c, t['ref'])} for t in o['targets'] if t['ref']]}
                                      for o in q['objectives']],
                       'objective_steps': objective_steps(c, ids).get(e, []),
                       'quest_script_runs': quest_script_runs(c, e, ids),
                       'no_stages_reason': ('quest has no stages: progress is driven by its quest-script GameMode block, '
                                            'which the stage sweep cannot trigger; covered by the ambient-capture gap list'
                                            if not q['stages'] else None)})
    inv = build_inventory(c)
    return {
        'milestone': 'Yes Man / Independent Vegas', 'format': 'm1_goodsprings.json + setup',
        'core': {e: c.by[e].get('name') for e in CORE},
        'system_quests': SUPPORT,
        'setup_ops': {
            'reset': 'bridge test.quest.reset {"quests": [hex ids]} (needs NVCOOP_TEST_HELPERS=1) - pristine quests before every stage',
            'console': 'nv.exec(cmd); only startquest/setstage/setobjectivedisplayed/setobjectivecompleted with HEX form ids '
                       '(editor ids do not resolve in the console, internal lane notes)',
            'apply_txn': 'bridge apply.txn {"txn": {"cause": {"kind": 3, "source": "", "aux": 0}, "origin": 0, "effects": [...]}} '
                         '- used only for quest script variables, which have no console setter',
        },
        'order': 'quests in listed order, stages ascending; every stage with phase "last" runs after all "normal" ones; '
                 'disposable_save stages run last of all',
        'touched_quests': inv['touched_quests'],
        'summary': {'quests': len(quests), 'stages': sum(len(q['stages']) for q in quests),
                    'objectives': sum(len(q['objectives']) for q in quests),
                    'stages_swept': sum(len(q['stages']) for q in quests), 'stages_excluded': len(excluded),
                    'infos': len(c.related),
                    'globals': sorted({'VStoryState', 'VStoryIndependentState', 'VStoryEventKimballAssassination',
                                       'VStoryEventHooverDamBattleStarted'})},
        'excluded': excluded,
        'quests': quests,
    }


# ----------------------------------------------------------------------------------------------- gaps
def loc(r):
    return {'scope': r['scope'], 'script': r['script'], 'label': r['label'], 'line': r['line'], 'text': r['text'], 'quest': r['quest'],
            'block': r['block'], 'conditional': r['cond'], 'capture': r['cls']['capture'], 'apply': r['cls']['apply'],
            'ambient': r['cls']['ambient']}


def overall(cls):
    return funcmap.worst(cls['capture'] if cls['capture'] in funcmap.ORDER else 'supported',
                         cls['apply'] if cls['apply'] in funcmap.ORDER else 'supported')


DEP_CAP = 25
HAZARDS = [
    ('H01', 'Player teleports (party warp)', 'player-teleport', 'high'),
    ('H02', 'Cutscene control locks / fades', 'cinematic', 'high'),
    ('H03', 'Fast-travel locks', 'fast-travel', 'high'),
    ('H04', 'Ending sequence', 'ending', 'high'),
    ('H05', 'Companions (SetPlayerTeammate, companion containers)', 'companion', 'high'),
    ('H06', '"Kill everyone" batches', 'kill-batch', 'medium'),
    ('H07', 'Spawns (PlaceAtMe)', 'spawn', 'medium'),
    ('H08', 'Essential flags', 'essential', 'medium'),
    ('H09', 'Menus / message boxes', 'menu', 'medium'),
    ('H10', 'Autosave / save', 'save', 'medium'),
    ('H11', 'Faction membership / relations', 'faction', 'medium'),
    ('H12', 'Perks / player AV / reward routing', 'player-effect', 'low'),
    ('H13', 'Achievements', 'achievement', 'low'),
    ('H14', 'Radio', 'radio', 'low'),
    ('H15', 'Player-bound conditions (need a party reduction)', 'player-query', 'medium'),
    ('H16', 'Quest-script timers (GetSecondsPassed)', 'timer', 'medium'),
]


def build_gaps(c):
    recs, items = records(c)
    byfn = collections.OrderedDict()
    for r in recs:
        if r['kind'] == 'query':
            key = 'cond:' + r['fn'].lower()
        elif r['kind'] == 'set':
            key = 'set'
        else:
            key = r['fn'].lower()
        byfn.setdefault(key, []).append(r)
    funcs = []
    tot = collections.Counter()
    by_scope = collections.Counter()
    by_sev = collections.Counter()
    by_overall = collections.Counter()
    for key, rs in byfn.items():
        cl0 = rs[0]['cls']
        # per-function status = worst occurrence (non-player item targets etc. differ per occurrence)
        counts = collections.Counter(r['scope'] for r in rs)
        statuses = collections.Counter(overall(r['cls']) if r['kind'] != 'query' else 'n/a' for r in rs)
        direct = [r for r in rs if r['scope'] != 'dep' and (r['kind'] == 'query' or overall(r['cls']) != 'supported')]
        dep = [r for r in rs if r['scope'] == 'dep' and (r['kind'] == 'query' or overall(r['cls']) != 'supported')]
        dep_by_script = collections.Counter(r['script'] for r in dep)
        worst_o = 'n/a' if rs[0]['kind'] == 'query' else max(statuses, key=lambda s: funcmap.ORDER[s])
        sev = max((r['cls']['severity'] for r in rs), key=lambda s: funcmap.SEV_ORDER[s])
        tags = sorted({t for r in rs for t in r['cls']['tags']})
        entry = {
            'fn': key, 'kind': rs[0]['kind'], 'effect': cl0['effect'],
            'capture': (max((r['cls']['capture'] for r in rs), key=lambda s: funcmap.ORDER.get(s, -1))),
            'apply': (max((r['cls']['apply'] for r in rs), key=lambda s: funcmap.ORDER.get(s, -1))),
            'overall': worst_o, 'severity': sev, 'tags': tags, 'note': cl0['note'],
            'counts': dict(sorted(counts.items())), 'total': len(rs), 'status_counts': dict(sorted(statuses.items())),
            'ambient_occurrences': sum(1 for r in rs if r['cls']['ambient']),
            'locations': [loc(r) for r in sorted(direct, key=lambda r: (r['scope'], r['script'], r['line']))],
            'dep_locations': [loc(r) for r in sorted(dep, key=lambda r: (r['script'], r['line']))[:DEP_CAP]],
            'dep_locations_total': len(dep), 'dep_scripts': dict(collections.Counter(r['script'] for r in dep).most_common(15)),
        }
        funcs.append(entry)
        if rs[0]['kind'] != 'query':
            for r in rs:
                tot['occurrences'] += 1
                by_scope[r['scope']] += 1
                by_overall[overall(r['cls'])] += 1
                by_sev[(overall(r['cls']), r['cls']['severity'])] += 1
    funcs.sort(key=lambda f: (-funcmap.SEV_ORDER[f['severity']], f['overall'] == 'supported', -f['total'], f['fn']))
    hazards = []
    for hid, title, tag, sev in HAZARDS:
        hits = [r for r in recs if tag in r['cls']['tags']]
        direct = [r for r in hits if r['scope'] != 'dep']
        dep = [r for r in hits if r['scope'] == 'dep']
        hazards.append({'id': hid, 'title': title, 'tag': tag, 'severity': sev, 'count': len(hits),
                        'direct': len(direct), 'dep': len(dep),
                        'locations': [loc(r) for r in sorted(direct, key=lambda r: (r['script'], r['line']))[:40]],
                        'dep_locations': [loc(r) for r in sorted(dep, key=lambda r: (r['script'], r['line']))[:40]]})
    sc_counts = collections.Counter(i['scope'] for i in items)
    # ambient mutations inside the member quests' own quest scripts: the sharpest capture gap
    amb = collections.Counter()
    for r in recs:
        if r['scope'] == 'quest_script' and r['cls']['ambient'] and r['kind'] == 'cmd':
            amb[(r['quest'], r['fn'].lower())] += 1
    summary = {
        'scripts_scanned': dict(sc_counts), 'functions': len([f for f in funcs if f['kind'] != 'query']),
        'condition_functions': len([f for f in funcs if f['kind'] == 'query']),
        'occurrences': tot['occurrences'], 'by_scope': dict(by_scope), 'by_overall_status': dict(by_overall),
        'by_status_severity': {'%s/%s' % k: v for k, v in sorted(by_sev.items())},
        'unclassified': sorted({f['fn'] for f in funcs if 'unclassified' in f['tags']}),
        'gaps_functions': len([f for f in funcs if f['kind'] != 'query' and f['overall'] != 'supported']),
        'direct_gap_occurrences': sum(1 for r in recs if r['kind'] != 'query' and r['scope'] != 'dep' and overall(r['cls']) != 'supported'),
        'ambient_quest_script_mutations': {'%s:%s' % k: v for k, v in sorted(amb.items())},
        'related_infos': len(c.related),
        'unsupported_by_severity': {
            side: dict(sorted(collections.Counter(
                r['cls']['severity'] for r in recs if r['kind'] != 'query' and overall(r['cls']) != 'supported'
                and ((r['scope'] == 'dep') == (side == 'dependent'))).items()))
            for side in ('own', 'dependent')},
        'unsupported_by_status': {
            side: dict(sorted(collections.Counter(
                overall(r['cls']) for r in recs if r['kind'] != 'query' and overall(r['cls']) != 'supported'
                and ((r['scope'] == 'dep') == (side == 'dependent'))).items()))
            for side in ('own', 'dependent')},
    }
    return {'slug': SLUG, 'method': 'questlines/yesman/scan.py statement scanner + funcmap.py (L5 hooks read 2026-10-16)',
            'status_vocab': ['supported', 'partial', 'missing'], 'summary': summary, 'hazards': hazards, 'functions': funcs}


def gaps_md(g):
    L = ['# Yes Man questline: capture/apply gaps', '',
         'Generated by `questlines/yesman/gen.py` (do not edit). Statuses: **supported** = L5 records it in a capture scope and '
         'applies it remotely; **partial** = recorded only inside a scope (`ambient` = quest-script GameMode / object-script '
         'statement, no scope open, dropped as `droppedNoScope`) or applied only through a console line; **missing** = no hook / no '
         'Effect / no apply.', '']
    s = g['summary']
    L += ['## Summary', '',
          '- scripts scanned: ' + ', '.join('%s %d' % kv for kv in sorted(s['scripts_scanned'].items())),
          '- statement-level functions: %d (%d not fully supported); condition functions with player/party meaning: %d'
          % (s['functions'], s['gaps_functions'], s['condition_functions']),
          '- occurrences: %d (by scope %s)' % (s['occurrences'], s['by_scope']),
          '- overall status: %s' % s['by_overall_status'],
          '- not-supported occurrences in the questline\'s own scripts (quest/stage/topic): %d' % s['direct_gap_occurrences'],
          '- unclassified functions: %s' % (s['unclassified'] or 'none'), '']
    L += ['## Ambient (no capture scope) mutations in the quests\' own quest scripts', '']
    for k, v in s['ambient_quest_script_mutations'].items():
        L.append('- `%s` x%d' % (k, v))
    L += ['', '## Hazards', '', '| id | hazard | severity | own scripts | dependent scripts |', '|---|---|---|---:|---:|']
    for h in g['hazards']:
        L.append('| %s | %s | %s | %d | %d |' % (h['id'], h['title'], h['severity'], h['direct'], h['dep']))
    L.append('')
    for h in g['hazards']:
        if not h['direct'] and not h['dep']:
            continue
        L += ['### %s %s (%s)' % (h['id'], h['title'], h['severity']), '']
        for x in h['locations'][:12]:
            L.append('- `%s:%d` [%s/%s%s] `%s`' % (x['script'], x['line'], x['capture'], x['apply'], ' ambient' if x['ambient'] else '', x['text'][:90]))
        if h['dep_locations']:
            L.append('- dependent scripts (first %d of %d):' % (min(12, h['dep']), h['dep']))
            for x in h['dep_locations'][:12]:
                L.append('  - `%s:%d` [%s/%s] `%s`' % (x['script'], x['line'], x['capture'], x['apply'], x['text'][:80]))
        L.append('')
    L += ['## Function table', '', '| function | Effect | capture | apply | sev | own | dep | ambient | tags |', '|---|---|---|---|---|---:|---:|---:|---|']
    for f in g['functions']:
        if f['kind'] == 'query':
            continue
        own = sum(v for k, v in f['counts'].items() if k != 'dep')
        L.append('| %s | %s | %s | %s | %s | %d | %d | %d | %s |' % (f['fn'], f['effect'] or '-', f['capture'], f['apply'], f['severity'],
                                                                  own, f['counts'].get('dep', 0), f['ambient_occurrences'], ' '.join(f['tags'])))
    L += ['', '## Exact locations of unsupported functions in the questline\'s own scripts', '']
    for f in g['functions']:
        if f['kind'] == 'query' or f['overall'] == 'supported' or not f['locations']:
            continue
        bad = [x for x in f['locations'] if x['capture'] != 'supported' or x['apply'] != 'supported']
        if not bad:
            continue
        L.append('### %s -> %s (%s)' % (f['fn'], f['effect'] or 'no Effect', f['severity']))
        for x in bad[:30]:
            L.append('- `%s:%d` [%s/%s%s] `%s`' % (x['script'], x['line'], x['capture'], x['apply'], ' ambient' if x['ambient'] else '', x['text'][:90]))
        if len(bad) > 30:
            L.append('- ... %d more in gaps.json' % (len(bad) - 30))
        L.append('')
    L += ['## Player-bound conditions (PartyAwareCond candidates)', '', '| condition | occurrences | reduction | own | dep |', '|---|---:|---|---:|---:|']
    for f in g['functions']:
        if f['kind'] != 'query':
            continue
        L.append('| %s | %d | %s | %d | %d |' % (f['fn'][5:], f['total'], f['note'].replace('reduce with: ', ''),
                                                  sum(v for k, v in f['counts'].items() if k != 'dep'), f['counts'].get('dep', 0)))
    return '\n'.join(L) + '\n'


# ----------------------------------------------------------------------------------------------- dialogue specs
AVN = {5: 'Strength', 6: 'Perception', 7: 'Endurance', 8: 'Charisma', 9: 'Intelligence', 10: 'Agility', 11: 'Luck', 32: 'Barter',
       33: 'BigGuns', 34: 'EnergyWeapons', 35: 'Explosives', 36: 'Lockpick', 37: 'Medicine', 38: 'MeleeWeapons', 39: 'Repair',
       40: 'Science', 41: 'Guns', 42: 'Sneak', 43: 'Speech', 44: 'Survival', 45: 'Unarmed'}
# (spec id, topic edid, policy note, policy checks)
KEY = [
    ('dlg_yesman_ready_for_lucky38', 'VMQTopsVMQTopsYesManTopic049', 'tether: party must be at the Lucky 38 before objective 60 starts the relocation', ['party-tether']),
    ('dlg_yesman_boomers_killed', 'VMQTopsVMQTopsYesManTopic022', 'faction hand-in: Boomers leaders killed (Side Bets)', ['initiator-choice', 'reward-all']),
    ('dlg_yesman_khans_wiped_out', 'VMQTopsVMQTopsYesManTopic030', 'faction hand-in: Khans wiped out', ['initiator-choice']),
    ('dlg_yesman_khans_support_ncr', 'VMQTopsVMQTopsYesManTopic031', 'faction hand-in: Khans support NCR', ['initiator-choice']),
    ('dlg_yesman_wgs_support_legion', 'VMQTopsVMQTopsYesManTopic043', 'faction hand-in: White Glove Society to Legion', ['initiator-choice']),
    ('dlg_yesman_omertas_slaughter', 'VMQTopsVMQTopsYesManTopic039', 'faction hand-in: help the Omertas attack the Strip', ['initiator-choice']),
    ('dlg_yesman_bos_destroyed', 'VMQTopsVMQTopsYesManTopic045', 'faction hand-in: Brotherhood bunker destroyed', ['initiator-choice']),
    ('dlg_yesman_tribes_done', 'VMQTopsVMQTopsYesManTopic019', 'last hand-in completes Side Bets (SetStage VMQYesMan02 100)', ['initiator-choice', 'barrier-candidate']),
    ('dlg_yesman_power_to_fort', 'VMQTopsVMQTopsYesManTopic004', 'choice: send dam power to the Fort (VHDIndependentBattle 20)', ['initiator-choice', 'branch']),
    ('dlg_yesman_dam_out_of_operation', 'VMQTopsVMQTopsYesManTopic007', 'choice: destroy the dam generators (VHDIndependentBattle 25)', ['initiator-choice', 'branch']),
    ('dlg_yesman_followers_support', 'VMQTopsVMQTopsYesManTopic063', 'go talk to the Followers (VMQYesMan03 86/90)', ['initiator-choice']),
    ('dlg_yesman_protect_kimball_good_rep', 'VMQTopsVMQTopsYesManTopic074', 'NCR reputation gate to protect Kimball (VMQYesMan03 75)', ['shared-reputation']),
    ('dlg_yesman_boomers_air_support', 'VFreeformNellisNellisPearlTopic016', 'Nellis: Boomers promise air support (VMQYesMan02 20/22)', ['initiator-choice']),
    ('dlg_yesman_papa_khan_speech', 'VMS45VRRCPapaKhanTopic009', 'Speech 75 check on Papa Khan (VMQYesMan02 42/50)', ['speech-check-initiator']),
    ('dlg_yesman_control_room_speech', 'VFreeformHooverDamVHDNCRControlRoomGuardTopic000', 'Speech 75 lie to the control-room guards', ['speech-check-initiator']),
    ('dlg_yesman_control_room_armor', 'VFreeformHooverDamVHDNCRControlRoomGuardTopic001', 'NCR-armor lie to the control-room guards', ['inventory-check-initiator']),
    ('dlg_yesman_oliver_retreat', 'VFreeformHooverDamVHDGeneralOliverTopic057', 'General Oliver retreats (VHDIndependentBattle 90)', ['initiator-choice', 'branch']),
    ('dlg_yesman_lanius_parting', 'VFreeformHooverDamVHDLegionLegateLaniusTopic046', 'Legate Lanius yields (faction removals)', ['initiator-choice', 'faction']),
    ('dlg_yesman_securitron_upgrade', 'vDialogueYesManSecuritronUpgrade', 'Securitron upgrade presentation (VMQYesMan01 34/100), forced conversation', ['forced-dialogue', 'cutscene']),
    ('dlg_yesman_house_dead_ack', 'VMQTopsTopic004', 'acknowledge House is dead: VMQYesMan01 objective 50 displayed and completed', ['initiator-choice']),
    ('dlg_yesman_failsafe_invited', 'VMQTopsTopic001', 'Failsafe path: "invited to visit the Lucky 38" completes Failsafe objective 5 and stage 100 (conditional)', ['initiator-choice', 'failsafe']),
]


def expect_from(src):
    """Unconditional quest consequences of a result script (what the pick step asserts)."""
    stage, odisp, ocomp, var = {}, {}, {}, {}
    for r in scan.scan(src):
        if r['cond'] or r['kind'] not in ('cmd', 'set'):
            continue
        fn = r['fn'].lower()
        a = r['args']
        if r['kind'] == 'cmd' and fn == 'setstage' and len(a) >= 2 and a[1].isdigit():
            stage[a[0]] = int(a[1])
        elif r['kind'] == 'cmd' and fn in ('setobjectivedisplayed', 'setobjectivecompleted') and len(a) >= 3 and a[1].isdigit():
            (odisp if fn.endswith('displayed') else ocomp).setdefault(a[0], []).append(int(a[1]))
        elif r['kind'] == 'set' and a and '.' in a[0]:
            m = re.search(r'\bto\s+(-?\d+)', r['text'], re.I)
            if m:
                var[a[0]] = int(m.group(1))
    out = {}
    if stage:
        out['stage'] = stage
    if odisp:
        out['objective_displayed'] = {k: sorted(set(v)) for k, v in odisp.items()}
    if ocomp:
        out['objective_completed'] = {k: sorted(set(v)) for k, v in ocomp.items()}
    if var:
        out['script_var'] = var
    return out


def build_dialogue(c):
    by_topic_edid = collections.defaultdict(list)
    for r, d_ in c.dials.items():
        if d_.get('edid'):
            by_topic_edid[d_['edid']].append(r)
    specs = []
    for sid, tedid, note, checks in KEY:
        trefs = by_topic_edid.get(tedid, [])
        infos = [i for i in c.allinfos if i['topic'] in trefs]
        infos.sort(key=lambda i: i['ref'])
        if not infos:
            continue
        # prefer an info whose result script touches the questline
        rel_ = [i for i in infos if any(i.get(k) and c.rx.search(i[k]['source']) for k in ('begin', 'end'))] or infos
        size = lambda i: sum(len(v) for v in expect_from('\n'.join((i.get(k) or {}).get('source', '') for k in ('begin', 'end'))).values())  # noqa: E731
        pick = max(rel_, key=lambda i: (size(i), i['ref']))
        res_src = '\n'.join((pick.get(k) or {}).get('source', '') for k in ('begin', 'end'))
        dl = c.dials.get(pick['topic'], {})
        checks_ = [{'skill': AVN.get(x['p1'], x['p1']), 'op': x['op'], 'value': x['value'], 'run_on': x.get('run_on')}
                   for x in pick['conditions'] if x['func'] == 'GetActorValue' and x['p1'] in AVN]
        spk = pick.get('speaker')
        host_q = c.byref.get(pick['quest'], {}).get('edid')
        step_pick = {'id': 'pick_p1_' + pick['ref'].split(':')[1].lower(), 'action': 'pick', 'actor': 'p1',
                     'topic': pick['topic'], 'topic_edid': tedid, 'prompt': pick.get('prompt') or dl.get('name'),
                     'info': pick['ref'], 'line': (pick['responses'][0]['text'] if pick['responses'] else '')[:40],
                     'expect': dict(expect_from(res_src), converged=True)}
        if checks_:
            step_pick['expect']['check_evaluated_on'] = 'p1'
        steps = [{'id': 'prep', 'action': 'setup', 'players': {'host': [], 'p1': [], 'p2': []}}]
        if spk:
            talk = {'id': 'talk_p1', 'action': 'talk', 'actor': 'p1', 'npc': name_of(c, spk),
                    'expect': {'opened': True, 'spectators': ['host', 'p2']}}
            if name_of(c, spk):
                talk['npc_ref'] = spk
            else:
                talk['npc_ref'] = None
                talk['npc_note'] = 'speaker form is not in the extracted name tables (forms/refnames)'
            steps.append(talk)
        steps.append(step_pick)
        specs.append({
            'schema': 1, 'kind': 'dialogue', 'id': sid, 'name': '%s (%s)' % (dl.get('name') or tedid, tedid), 'plugin': 'FalloutNV.esm',
            'summary': note, 'players': {'host': 0, 'p1': 1, 'p2': 2},
            'quests': sorted({host_q} | {q for q in MEMBERS if re.search(r'\b' + q + r'\b', res_src, re.I)} - {None}),
            'container_quest': host_q, 'speaker_check': checks_, 'flags': pick.get('flags', []),
            'info_count_in_topic': len(infos), 'policy_checks': checks, 'steps': steps,
            'invariants': ['no_duplicate_reward', 'one_conversation_per_npc', 'result_script_ran_once_on_host', 'host.shared == client.shared'],
            'oracle': 'docs/dialogue-design.md'})
    return {'slug': SLUG, 'note': 'L8 dialogue-scenario shaped specs for the key Yes Man conversations; info/topic refs are real',
            'specs': specs}


# ----------------------------------------------------------------------------------------------- policy table
def policy_table(c, gaps):
    sys.path.insert(0, os.path.join(REPO, 'tools', 'coverage'))
    import rules as covrules  # noqa: E402  (CAT_KINDS: the category -> rule-kind derivation used by L9)
    L = ['# Yes Man: current rules vs what the scripts imply', '',
         'Generated by `questlines/yesman/gen.py`. "implied" = rule kinds `tools/coverage/rules.py` derives from the risk categories '
         '(CAT_KINDS); "present" = kinds in docs/quest-policies/rules.json today. Hosting quests are where the Yes Man dialogue '
         'results run: L10 rewrites the cause.source of a DialogueResult txn from the INFO to its hosting quest (nvcoop_infoquest.json), '
         'so the HOSTING quest rules are the ones that filter those txns.', '',
         '| quest | role | tier | present | implied by categories | implied but absent |', '|---|---|---|---|---|---|']
    hosts = collections.Counter()
    for i in c.related:
        hosts[c.byref.get(i['quest'], {}).get('edid')] += 1
    rows = [(e, 'member') for e in MEMBERS] + [(h, 'hosts %d result infos' % n) for h, n in sorted(hosts.items(), key=lambda kv: -kv[1]) if h and h not in MEMBERS]
    for e, role in rows:
        q = c.by[e]
        rk = c.risk[q['ref']]
        present = [r['kind'] for r in (c.rules['quests'].get(e) or {}).get('rules', [])]
        implied = sorted({k for cat, lvl, k in covrules.CAT_KINDS if rk['cat_max_level'].get(cat, -1) >= lvl})
        L.append('| %s | %s | %s | %s | %s | %s |' % (e, role, rk['tier'], ', '.join(present) or '-', ', '.join(implied) or '-',
                                                   ', '.join(k for k in implied if k not in present) or '-'))
    L += ['', '## Gap counts per member quest (own scripts: quest script + stages + result infos naming the quest; ambient = quest-script/object statements)', '',
          '| quest | scope | statements | supported | partial | missing | high-severity gaps |', '|---|---|---:|---:|---:|---:|---:|']
    recs, _ = records(c)
    for e in MEMBERS:
        for scope_name, pred in (('quest script + stages', lambda r: r['scope'] in ('quest_script', 'stage')),
                                 ('dependent scripts', lambda r: r['scope'] == 'dep')):
            rs = [r for r in recs if r['quest'] == e and r['kind'] != 'query' and pred(r)]
            st = collections.Counter(overall(r['cls']) for r in rs)
            hi = sum(1 for r in rs if r['cls']['severity'] == 'high' and overall(r['cls']) != 'supported')
            L.append('| %s | %s | %d | %d | %d | %d | %d |' % (e, scope_name, len(rs), st['supported'], st['partial'], st['missing'], hi))
    for h in sorted({r['quest'] for r in recs if r['scope'] == 'info'}, key=lambda x: x or ''):
        rs = [r for r in recs if r['quest'] == h and r['scope'] == 'info' and r['kind'] != 'query']
        st = collections.Counter(overall(r['cls']) for r in rs)
        hi = sum(1 for r in rs if r['cls']['severity'] == 'high' and overall(r['cls']) != 'supported')
        L.append('| %s | result infos naming a Yes Man quest | %d | %d | %d | %d | %d |' % (h, len(rs), st['supported'], st['partial'], st['missing'], hi))
    return '\n'.join(L) + '\n'


# ----------------------------------------------------------------------------------------------- driver
def build_all():
    c = load()
    gaps = build_gaps(c)
    return {'inventory.json': jdump(build_inventory(c)), 'sweep.json': jdump(build_sweep(c)), 'gaps.json': jdump(gaps),
            'gaps.md': gaps_md(gaps), 'dialogue_specs.json': jdump(build_dialogue(c)), 'policy_table.md': policy_table(c, gaps)}


def main(argv):
    if '--refresh-fixtures' in argv:
        refresh_fixtures()
        return 0
    out = build_all()
    if '--check' in argv:
        bad = []
        for n, txt in out.items():
            p = os.path.join(HERE, n)
            old = open(p, encoding='utf-8', newline='').read() if os.path.exists(p) else None
            if old != txt:
                bad.append(n)
        print('STALE: ' + ', '.join(bad) if bad else 'generated files up to date')
        return 1 if bad else 0
    for n, txt in out.items():
        with open(os.path.join(HERE, n), 'w', encoding='utf-8', newline='\n') as f:
            f.write(txt)
        print('wrote %s (%d bytes)' % (n, len(txt.encode('utf-8'))))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
