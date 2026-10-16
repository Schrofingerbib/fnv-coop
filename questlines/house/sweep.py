"""sweep.json for the Mr. House questline: out/questdata/m1_goodsprings.json format + per-quest/per-stage console `setup`.

L7's sweep driver (plugin/snapshot/tools/goodsprings_sweep.py load_plan) reads quests[].{edid, ref, stages[].index, tier_direct}
and, per stage, reloads the autosave, then `setstage <hex> <n>`. Everything it needs is kept in that shape; the additions are
  quests[].setup            commands to run once after the load and BEFORE the S0 snapshot (clean-state prerequisites)
  quests[].stages[].setup   extra commands for that stage (earlier stages of the same quest, gating globals)
  quests[].stages[].step    the scenario step (host/wait/expect) from out/questdata/scenarios/<EDID>.json
  excluded[]                stages deliberately not swept here, each with a reason
  probes[]                  one-off questions the sweep should answer in game (unknowns found while reading the scripts)
Commands carry the refs they were built from so the tests can re-derive every hex FormID from quests.json / forms.json.gz.
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qlib  # noqa: E402
import loadorder  # noqa: E402

ITEM_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(additem|removeitem|additemns|equipitem)\s+(\w+)(?:\s+(-?\d+))?')
TOGGLE_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(enable|disable)\b(?!\w)')
SETSTAGE_RE = re.compile(r'(?im)\bsetstage\s+(\w+)\s+(\d+)')
SET_RE = re.compile(r'(?im)^\s*set\s+(\w+)\s+to\b')
SUBJ_RE = re.compile(r'(?m)\b([A-Za-z_]\w*)\.(?=[A-Za-z])')
OBJ_RE = re.compile(r'(?im)\bsetobjective(displayed|completed)\s+(\w+)\s+(\d+)\s+([01])')


# ------------------------------------------------------------------------------------------------ command rendering
def c_startquest(d, q, why):
    ref = d.q_by_edid[q]['ref']
    return dict(cmd=f'startquest {d.hexid(ref)}', kind='quest', why=why, refs=[ref], edids=[q])


def c_setstage(d, q, n, why):
    ref = d.q_by_edid[q]['ref']
    return dict(cmd=f'setstage {d.hexid(ref)} {n}', kind='quest', why=why, refs=[ref], edids=[q])


def c_global(d, g, v, why):
    ref = d.edid_to_ref(g)
    h = d.hexid(ref)
    return dict(cmd=f'set {h} to {v}', kind='global', why=why, refs=[ref], edids=[g], verified=False,
                fallback=f'set {g} to {v}',
                note='the brief says the console needs hex FormIDs; whether `set <hex> to` parses for a GLOB is not verified in game. '
                     'Fallback is the EditorID form, which the console accepts for globals in vanilla (`set timescale to 30`).')


def c_obj(d, q, obj, why):
    ref = d.q_by_edid[q]['ref']
    return dict(cmd=f'setobjectivedisplayed {d.hexid(ref)} {obj} 1', kind='objective', why=why, refs=[ref], edids=[q])


def c_probe_running(d, q):
    ref = d.q_by_edid[q]['ref']
    return dict(cmd=f'getquestrunning {d.hexid(ref)}', kind='query', why='read back', refs=[ref], edids=[q])


# Curated, per quest. `pre`: commands after the load (before S0) for every stage of the quest. `stage_pre[n]`: additional
# commands for stage n; `prior_stages[n]`: earlier stages of the same quest to set first (in order); `needs_global[n]`:
# {global edid: value} the stage-entry condition demands. `natural`: how the stage is reached in play (from the setters).
def spec_for(d):
    chain = lambda q, lst: lst  # noqa: E731
    S = {}
    S['VMQHouse'] = dict(pre=[('startquest', 'VMQHouse')], note='legacy parent quest: log text only, no scripts, no setter anywhere in the data')
    S['VMQHouse1'] = dict(pre=[], prior={25: [10, 20], 100: [10, 20, 25]}, note='start_enabled; stages 5/7/10/20 never set by a script')
    S['VMQHouse2'] = dict(pre=[('startquest', 'VMQHouse2')], prior={100: [30], 110: [30]}, needs_global={100: {'VStoryEventSecuritronsFort': 1}},
                          note='stage 100 entry (complete + RewardXP 500 + setstage VDialogueLucky38Entrance 10) only runs when VStoryEventSecuritronsFort == 1')
    S['VMQHouse3'] = dict(pre=[('startquest', 'VMQHouse3')], prior={100: [40], 110: [40], 200: [40]})
    S['VMQHouse4'] = dict(pre=[('startquest', 'VMQHouse4')], prior={100: [50], 105: [50], 110: [50]}, needs_global={100: {'VStoryEventRacket': 1}},
                          note='stage 100 (foiled RACKET) is conditional on VStoryEventRacket == 1; 105 is the unconditional variant')
    S['VMQHouse5'] = dict(pre=[('startquest', 'VMQHouse5')], prior={100: [60], 110: [60], 200: [60]},
                          note='stages 100/110 raise VStoryState to 40 and VStoryHouseState to 2 (globals, start at 0)')
    S['VMQHouse6'] = dict(pre=[('startquest', 'VMQHouse6')], prior={100: [70], 110: [70], 120: [70], 130: [70], 200: [70]})
    S['VMQHouse7'] = dict(pre=[('startquest', 'VMQHouse7')], note='stage 100 raises VStoryState to 50 and VStoryHouseState to 3')
    S['VMQHouse8'] = dict(pre=[('startquest', 'VMQHouse8')], note='stage 80 completes the quest and calls AddAchievement 34')
    S['VMQHouseLockdown'] = dict(pre=[('startquest', 'VMQHouseLockdown')], prior={20: [10], 100: [10, 20], 110: [10]},
                                 note='quest script saves/restores VDialogueLucky38Entrance (SetStage 15) and times 24h with GameDaysPassed; stage 100 itself is not timer-gated')
    S['VMQHouseFail'] = dict(pre=[('startquest', 'VMQHouse1'), ('startquest', 'VMQHouse2'), ('startquest', 'VMQHouse3'), ('startquest', 'VMQHouse4'),
                                  ('startquest', 'VMQHouse5'), ('startquest', 'VMQHouse6'), ('startquest', 'VMQHouse7'), ('startquest', 'VMQHouse8'),
                                  ('startquest', 'VMQHouseFail')],
                             prior={5: [1]}, destructive=[1, 5],
                             note='stage 1 fans out to every running VMQHouse1..8 (GetQuestRunning guards), kills House (Lucky38MrHouseREF.SetDestroyed 1), SetAlly Lucky38Faction playerFaction; starting all eight first makes every branch fire')
    S['VHDHouseBattle'] = dict(pre=[('startquest', 'VHDHouseBattle')], prior={20: [10], 30: [10, 20], 40: [10, 20, 30], 50: [10, 20, 30, 40],
                                                                        90: [10, 20, 30, 40, 50], 100: [10, 20, 30, 40, 50, 90]},
                               destructive=[10, 20, 30, 40, 50, 90, 100],
                               note='stage 10 starts vHDBattleController, disables fast travel and moves Arcade; stage 100 starts VEnding (slideshow). '
                                    'Every stage changes the world persistently: reload before each, never chain in one session')
    S['VDialogueMrHouse'] = dict(pre=[], note='start_enabled dialogue container; stages 0/10/20 are state flags for the topics (20 = House dead, set by NVCRMrHouseSCRIPT:134)')
    return S


def render_pre(d, pre):
    out = []
    for item in pre:
        if item[0] == 'startquest':
            out.append(c_startquest(d, item[1], f'quest must be running: {item[1]}'))
        elif item[0] == 'setstage':
            out.append(c_setstage(d, item[1], item[2], 'prerequisite stage'))
        else:
            raise ValueError(item)
    return out


# ------------------------------------------------------------------------------------------------ m1-style per-quest facts
def scripts_of(q, infos):
    return [(s['where'], s) for s in qlib.quest_scripts(q, infos)]


def build(d=None, mutate=None):
    d = d or qlib.data()
    spec = spec_for(d)
    members = qlib.CORE + list(qlib.SYSTEM)
    scen_dir = os.path.join(qlib.QD, 'scenarios')
    glob_names = {g['edid'].lower(): g['edid'] for g in d.quests_doc['globals'] if g.get('edid')}
    globs = {g['ref']: g for g in d.quests_doc['globals']}
    qout, excluded = [], []
    agg = dict(globals=set(), items={}, toggled={}, cross=set())
    touched = {}
    for k in members:
        q = d.q_by_edid[k]
        infos = d.infos_by_quest.get(q['ref'], [])
        for _, sc in scripts_of(q, infos):
            for r in sc['refs']:
                if r in d.q_by_ref and r != q['ref']:
                    touched.setdefault(d.q_by_ref[r]['edid'], set()).add(f'{k}: script ref')
            for qn, _ in SETSTAGE_RE.findall(sc['source']):
                if qn in d.q_by_edid and qn != k:
                    touched.setdefault(qn, set()).add(f'{k}: setstage')
    for k in list(touched):
        if k in members:
            del touched[k]

    order = ['VMQHouse', 'VMQHouse1', 'VMQHouse2', 'VMQHouse3', 'VMQHouse4', 'VMQHouse5', 'VMQHouse6', 'VMQHouse7', 'VMQHouse8',
             'VMQHouseLockdown', 'VDialogueMrHouse', 'VMQHouseFail', 'VHDHouseBattle']
    assert sorted(order) == sorted(members), 'sweep order must cover exactly the owned quests'
    for k in order:
        q = d.q_by_edid[k]
        infos = d.infos_by_quest.get(q['ref'], [])
        rq = d.risk[q['ref']]
        sp = spec[k]
        scr = scripts_of(q, infos)
        gl, items, toggles, subj, setstages, objs = set(), {}, {}, set(), set(), []
        for where, sc in scr:
            src = sc['source']
            for g in SET_RE.findall(src):
                if g.lower() in glob_names:
                    gl.add(glob_names[g.lower()])
            for r in sc['refs']:
                if r in globs and globs[r].get('edid'):
                    gl.add(globs[r]['edid'])
            for who, fn, item, n in ITEM_RE.findall(src):
                key = (item, fn.lower(), (who or 'self').lower())
                items[key] = items.get(key, 0) + 1
            for who, fn in TOGGLE_RE.findall(src):
                toggles.setdefault((who or 'self'), set()).add(fn.lower())
            subj.update(x for x in SUBJ_RE.findall(src) if x.lower() not in ('player', 'playerref'))
            for qn, idx in SETSTAGE_RE.findall(src):
                if qn != k:
                    setstages.add((qn, int(idx)))
            if where.startswith('stage'):
                for kind, qn, n, v in OBJ_RE.findall(src):
                    objs.append(dict(stage=where, kind=kind.lower(), quest=qn, objective=int(n), value=int(v)))
        agg['globals'] |= gl
        for (item, fn, who), n in items.items():
            agg['items'].setdefault(item, {}).setdefault(f'{who}.{fn}', 0)
            agg['items'][item][f'{who}.{fn}'] += n
        for who, fns in toggles.items():
            agg['toggled'].setdefault(who, set()).update(fns)
        agg['cross'] |= {f'{a} {b}' for a, b in setstages}
        scen = json.load(open(os.path.join(scen_dir, f'{k}.json'), encoding='utf-8'))
        step_by = {s['id']: s for s in scen['steps']}
        pre = render_pre(d, sp['pre'])
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            n = s['index']
            sp_pre = []
            for prior in sp.get('prior', {}).get(n, []):
                sp_pre.append(c_setstage(d, k, prior, f'prerequisite stage {prior} of {k}'))
            for g, v in sp.get('needs_global', {}).get(n, {}).items():
                sp_pre.append(c_global(d, g, v, f'stage {n} entry condition: GetGlobalValue {g} == {v}'))
            step = step_by.get(f'stage_{n}')
            stages.append({
                'index': n, 'log': next((e.get('text') for e in s['entries'] if e.get('text')), None),
                'flags': sorted({f for e in s['entries'] for f in e.get('flags', [])}),
                'has_script': any(e.get('script') for e in s['entries']),
                'findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text']) for f in rq['stage_findings'] if f['where'] == f'stage {n}'],
                'objective_effects': [o for o in objs if o['stage'] == f'stage {n}'],
                'setup': sp_pre,
                'cmd': c_setstage(d, k, n, 'the swept stage')['cmd'],
                'destructive': n in sp.get('destructive', []),
                'step': step})
        qout.append({
            'edid': k, 'ref': q['ref'], 'name': q.get('name'), 'role': qlib.role_of(k), 'tier': rq['tier'], 'tier_direct': rq['tier_direct'],
            'cats': rq['cats'], 'priority': q.get('priority'), 'flags': q.get('flags'), 'script': q.get('script'),
            'start_enabled': 'start_enabled' in (q.get('flags') or []), 'formid': d.hexid(q['ref']),
            'setup': pre, 'note': sp.get('note'),
            'start_step': next((s for s in scen['steps'] if s['id'] == 'start'), None),
            'stages': stages,
            'objectives': [{'index': o['index'], 'text': o.get('text'),
                            'targets': [{'ref': t['ref'], 'edid': d.ref_edid(t['ref'])} for t in o['targets']]} for o in q['objectives']],
            'globals': sorted(gl), 'items': [{'item': i, 'fn': f, 'subject': w, 'occurrences': n} for (i, f, w), n in sorted(items.items())],
            'enable_disable': {w: sorted(f) for w, f in sorted(toggles.items())}, 'script_subjects': sorted(subj),
            'setstage_other_quests': sorted(f'{a} {b}' for a, b in setstages),
            'dialogue': {'infos': len(infos), 'scripted_infos': sum(1 for i in infos if i.get('begin') or i.get('end')),
                         'topics': len(q.get('topics', []))},
            'swept': True})
    # stages of shared quests the House path runs through: not swept here, with the owner and the reason
    owners = {'vHDBattleController': 'Q-ncr', 'VEnding': 'Q-ncr', 'VEndingBinks': 'Q-systems', 'VMQEndStates': 'Q-ncr', 'VMQEndStatesTheStrip': 'Q-strip',
              'VMQEndStatesFreeside': 'Q-strip', 'VFreeformHooverDam': 'Q-ncr', 'vDialogueHooverDam': 'Q-ncr', 'VHDKimballSpeech': 'Q-ncr',
              'VHDAssassinationDialog': 'Q-ncr', 'VDialogueLucky38Penthouse': 'Q-strip', 'VDialogueLucky38Entrance': 'Q-act2',
              'VLegateShowdown': 'Q-ncr/Q-legion'}
    for k, why in qlib.SHARED.items():
        q = d.q_by_edid[k]
        owner = owners[k]
        if q['stages']:
            for s in sorted(q['stages'], key=lambda s: s['index']):
                excluded.append(dict(quest=k, stage=s['index'], owner=owner, reason=f'shared infrastructure swept by {owner} (its sweep.json lists this stage); the House path only drives it'))
        else:
            excluded.append(dict(quest=k, stage=None, owner=owner, reason=f'no stages (script/dialogue container) owned by {owner}; exercised through the House stages that start or feed it'))
    for k in qlib.PREREQ:
        q = d.q_by_edid[k]
        for s in sorted(q['stages'], key=lambda s: s['index']):
            excluded.append(dict(quest=k, stage=s['index'], owner='Q-act2/Q-prologue', reason='upstream main-story prerequisite: swept by its owning lane, only needed here to document the natural path'))
    probes = [
        dict(id='P1-implicit-start', question='Does setobjectivedisplayed on a NOT-running quest start it? VMQHouse2..8 are not start_enabled and no script anywhere does StartQuest/SetStage on them, yet VDialogueMrHouse INFOs display their objectives.',
             steps=[c_probe_running(d, 'VMQHouse2'),
                    dict(cmd=f'setobjectivedisplayed {d.hexid("VMQHouse2")} 30 1', kind='objective', why='probe', refs=[d.q_by_edid['VMQHouse2']['ref']], edids=['VMQHouse2']),
                    c_probe_running(d, 'VMQHouse2')],
             expect='unknown: record both getquestrunning results; if 1 -> objective display starts the quest and our SetObjective apply on a client must also start it'),
        dict(id='P2-global-hex-set', question='Does `set <hex> to <v>` parse for a GLOB in the console (brief says hex FormIDs only), or only `set <EditorID> to <v>`?',
             steps=[c_global(d, 'VStoryEventRacket', 1, 'probe')], expect='unknown: record console output for both forms (cmd and fallback)'),
    ]
    out = dict(
        milestone='House', slug=qlib.SLUG, format='m1_goodsprings.json + setup',
        core={k: d.q_by_edid[k]['name'] for k in qlib.CORE}, system_quests=list(qlib.SYSTEM),
        shared_quests=list(qlib.SHARED), prereq_quests=list(qlib.PREREQ),
        touched_quests=[{'edid': e, 'name': d.q_by_edid[e].get('name'), 'stages': len(d.q_by_edid[e]['stages']), 'why': sorted(w)} for e, w in sorted(touched.items())],
        order=order,
        summary=dict(quests=len(qout), stages=sum(len(x['stages']) for x in qout), objectives=sum(len(x['objectives']) for x in qout),
                     infos=sum(x['dialogue']['infos'] for x in qout), globals=sorted(agg['globals']),
                     items={k: v for k, v in sorted(agg['items'].items())},
                     enable_disable_subjects={k: sorted(v) for k, v in sorted(agg['toggled'].items())},
                     cross_quest_setstage=sorted(agg['cross']), excluded=len(excluded),
                     stages_swept=sum(len(x['stages']) for x in qout if x['swept'])),
        preamble=dict(
            note='Per stage: loadSave(autosave) -> run quests[].setup then stages[].setup -> S0 -> S1 -> capture.begin -> stages[].cmd -> capture.end -> S2. '
                 'Setup commands run BEFORE S0 so they are not part of the captured txn.',
            all_stages_reload=True),
        quests=qout, excluded=excluded, probes=probes)
    if mutate:
        _mutate(out, mutate)
    return out


def _mutate(doc, how):
    """Broken variants the validator must catch."""
    qs = {q['edid']: q for q in doc['quests']}
    if how == 'bad_hex':
        qs['VMQHouse2']['stages'][1]['setup'][0]['cmd'] = 'setstage 147887 30'    # a real but WRONG quest (VMQHouse3)
    elif how == 'edid_in_cmd':
        qs['VMQHouse3']['setup'][0]['cmd'] = 'startquest VMQHouse3'
    elif how == 'drop_stage':
        qs['VHDHouseBattle']['stages'].pop()
    elif how == 'unresolved_ref':
        qs['VMQHouse4']['stages'][1]['setup'][0]['refs'] = ['FalloutNV.esm:FFFFFF']
    elif how == 'bad_command':
        qs['VMQHouse5']['setup'][0]['cmd'] = 'frobnicate 147889'
    elif how == 'wrong_stage_cmd':
        qs['VMQHouse8']['stages'][0]['cmd'] = qs['VMQHouse8']['stages'][0]['cmd'].replace(' 80', ' 81')
    else:
        raise ValueError(how)
