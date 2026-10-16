"""One command: python -I questlines/strip/run_tests.py   (prints ALL OK and exits 0 when everything holds)

1. REAL checks: every generated artifact (inventory.json, sweep.json, gaps.json, rules.patch.json, dialogue*.json) validates against
   the quest data, the synccore headers and the L5 hook tables; the files on disk equal a fresh in-memory build.
2. MUTATION TWIN: every validator is also run on a deliberately broken copy; the validator MUST report a problem
   (otherwise the check is vacuous and the run fails).
"""
import copy
import json
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build as build_mod  # noqa: E402
import checks  # noqa: E402
import cmdtable as ct  # noqa: E402
import dialogue  # noqa: E402
import scope  # noqa: E402

FAILS = []
RESULTS = []


def check(name, problems):
    ok = not problems
    RESULTS.append((name, ok))
    print(('PASS  ' if ok else 'FAIL  ') + name + ('' if ok else f'  ({len(problems)} problems)'))
    for p in problems[:8]:
        print('        ' + str(p))
    if not ok:
        FAILS.append(name)


def kill(name, problems):
    """A mutant is killed when its validator reports at least one problem."""
    ok = bool(problems)
    RESULTS.append(('mutant ' + name, ok))
    print(('KILLED  ' if ok else 'SURVIVED  ') + name)
    if not ok:
        FAILS.append('mutant ' + name)


def load(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return json.load(f)


def main():
    t0 = time.time()
    data = scope.Data()
    out = build_mod.build_all(data)
    inv, sw, gp, patch, dlg, specs = out['inventory'], out['sweep'], out['gaps'], out['patch'], out['dialogue'], out['specs']

    print('== real checks ==')
    check('scope resolves and matches rules.json questline', checks.validate_scope(data))
    check('inventory.json (on disk) validates against quests.json/risk.json', checks.validate_inventory(load('inventory.json'), data))
    check('sweep.json (on disk): m1 format, every stage swept or excluded, setup lines resolve', checks.validate_sweep(load('sweep.json'), load('inventory.json'), data))
    check('command table: Effects exist in effect.h, hooks/applier match engine_addrs.h + apply_remote.cpp', checks.validate_cmdtable())
    check('gaps.json (on disk): nothing unmapped, every location (script EDID + line) re-verified', checks.validate_gaps(load('gaps.json'), data))
    check('rules.patch.json (on disk): applies, 16 kinds, ids resolve, citations verified', checks.validate_patch(load('rules.patch.json'), data))
    check('dialogue_specs.json (on disk): L8 schema, every INFO/topic/speaker/effect resolves', checks.validate_dialogue(load('dialogue_specs.json')['specs'], load('dialogue.json'), data))

    fresh = []
    for fname, obj in (('inventory.json', inv), ('sweep.json', sw), ('gaps.json', gp), ('rules.patch.json', patch), ('dialogue.json', dlg),
                       ('dialogue_specs.json', dict(schema=1, kind='dialogue', specs=specs))):
        if json.loads(json.dumps(obj)) != load(fname):
            fresh.append(f'{fname} on disk differs from a fresh build (run: python -I questlines/strip/build.py)')
    for fname, text in (('policy_review.md', out['review_md']), ('dialogue.md', out['dialogue_md'])):
        with open(os.path.join(HERE, fname), encoding='utf-8', newline='') as f:
            if f.read() != text:
                fresh.append(f'{fname} on disk differs from a fresh build')
    check('generated files are current (deterministic rebuild equals disk)', fresh)

    doc_path = os.path.join(scope.REPO, 'docs', 'questlines', 'strip.md')
    doc = None
    if not os.environ.get('QSTRIP_SKIP_STATUS'):
        if os.path.exists(doc_path):
            with open(doc_path, encoding='utf-8') as f:
                doc = f.read()
            check('internal questline notes quotes the same counts and 10 blockers', checks.validate_status(doc, out))
        else:
            check('internal questline notes exists', ['missing: run python -I questlines/strip/status.py'])

    print('\n== mutation twin ==')
    # --- inventory
    m = copy.deepcopy(inv)
    m['quests'][0]['stages'].pop()
    kill('inventory: stage dropped', checks.validate_inventory(m, data))
    m = copy.deepcopy(inv)
    m['quests'][1]['console_id'] = 'ZZZZZZ'
    kill('inventory: non-hex console id', checks.validate_inventory(m, data))
    m = copy.deepcopy(inv)
    m['quests'][2]['tier'] = 'T0'
    kill('inventory: wrong tier', checks.validate_inventory(m, data))
    m = copy.deepcopy(inv)
    m['quests'][3]['prerequisites']['gated_by'].append(dict(quest='NoSuchQuest', kind='gate', strength='hard', count=1, evidence=[], in_scope=False))
    kill('inventory: prerequisite quest that does not exist', checks.validate_inventory(m, data))
    # --- sweep
    q_with_stages = next(q for q in sw['quests'] if q['stages'])
    m = copy.deepcopy(sw)
    mq = next(q for q in m['quests'] if q['edid'] == q_with_stages['edid'])
    mq['stages'].pop()
    kill('sweep: stage missing from the sweep', checks.validate_sweep(m, inv, data))
    m = copy.deepcopy(sw)
    mq = next(q for q in m['quests'] if q['edid'] == q_with_stages['edid'])
    mq['stages'][0]['sweep'] = False
    kill('sweep: stage excluded without a reason', checks.validate_sweep(m, inv, data))
    m = copy.deepcopy(sw)
    mq = next(q for q in m['quests'] if q['edid'] == q_with_stages['edid'])
    mq['setup'].append('setstage 00ZZ99 10')
    kill('sweep: setup with a non-hex FormID', checks.validate_sweep(m, inv, data))
    m = copy.deepcopy(sw)
    mq = next(q for q in m['quests'] if q['edid'] == q_with_stages['edid'])
    mq['setup'].append(f'SetStage {mq["console_id"]} {mq["stages"][0]["index"]}')
    kill('sweep: setup command not an FNV console command', checks.validate_sweep(m, inv, data))
    m = copy.deepcopy(sw)
    mq = next(q for q in m['quests'] if q['edid'] == q_with_stages['edid'])
    mq['setup'].append(f'setstage {mq["console_id"]} 987654')
    kill('sweep: setup stage that does not exist', checks.validate_sweep(m, inv, data))
    m = copy.deepcopy(sw)
    mq = next(q for q in m['quests'] if q['edid'] == q_with_stages['edid'])
    mq['stages'][0]['command'] = 'setstage ' + q_with_stages['edid'] + ' 10'
    kill('sweep: EditorID instead of hex FormID in the stage command', checks.validate_sweep(m, inv, data))
    # --- gaps
    m = copy.deepcopy(gp)
    row = next(r for r in m['functions'] if r['gap'] and r['locations'])
    row['locations'][0]['line'] += 1
    kill('gaps: location line shifted', checks.validate_gaps(m, data))
    m = copy.deepcopy(gp)
    next(r for r in m['functions'] if r['effect'])['effect'] = 'NotAnEffect'
    kill('gaps: Effect that does not exist', checks.validate_gaps(m, data, deep=False))
    m = copy.deepcopy(gp)
    next(r for r in m['functions'] if r['gap'])['status'] = 'supported'
    kill('gaps: unsupported function relabelled supported', checks.validate_gaps(m, data, deep=False))
    m = copy.deepcopy(gp)
    m['unmapped'] = {'frobnicate': dict(count=1, locations=[])}
    kill('gaps: unmapped command', checks.validate_gaps(m, data, deep=False))
    saved = copy.deepcopy(ct.CMD)
    try:
        ct.CMD['sayto']['capture'] = ct.SUP
        kill('cmdtable: SayTo claimed captured (no hook)', checks.validate_cmdtable())
        ct.CMD.clear()
        ct.CMD.update(copy.deepcopy(saved))
        ct.CMD['setstage']['effect'] = 'Teleport'
        kill('cmdtable: invented Effect type', checks.validate_cmdtable())
        ct.CMD.clear()
        ct.CMD.update(copy.deepcopy(saved))
        ct.CMD['setplayerteammate']['apply'] = ct.SUP
        kill('cmdtable: CompanionOwner claimed appliable', checks.validate_cmdtable())
        ct.CMD.clear()
        ct.CMD.update(copy.deepcopy(saved))
        ct.CMD['addtofaction']['capture'] = ct.MIS
        ct.CMD['restoreav']['capture'] = ct.SUP
        kill('cmdtable: RestoreAV claimed captured (hook list unchanged)', checks.validate_cmdtable())
    finally:
        ct.CMD.clear()
        ct.CMD.update(saved)
    # --- patch
    m = copy.deepcopy(patch)
    m['amend']['NoSuchQuestEdid'] = dict(remove=[], add=[])
    kill('patch: key that is not a quest', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['amend']['VMS24']['add'].append(dict(kind='TeleportEveryone', params={}, origin='curated'))
    kill('patch: invented rule kind', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['amend']['VMS24']['remove'] = ['Barrier']
    kill('patch: removes a kind the quest does not have', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['amend']['VMS35']['add'].append(dict(kind='PartyTether', params=dict(cells=['ImaginaryCellNobodyShipped'], gather='auto-warp'), origin='curated'))
    kill('patch: tether cell that does not resolve', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['volatile_keys']['vDialogueCasinoCashier'].append('NoSuchVariable')
    kill('patch: volatile variable that is not declared', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['proposals']['kind_uses']['VDialogueTops'].append('NotAVar')
    kill('patch: per-player variable that is not declared', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['amend']['VMS24']['evidence'] = []
    kill('patch: removal without evidence', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['amend']['VMS24']['add'].append(dict(kind='SetPlayerControls', params={}, origin='curated'))
    m['amend']['VMS24']['remove'] = []
    kill('patch: SetPlayerControls and PerPlayerScene together', checks.validate_patch(m, data))
    m = copy.deepcopy(patch)
    m['blocked_params']['markers'].append('PresidentialSuiteXMarker')
    kill('patch: blocked list contains an id that resolves', checks.validate_patch(m, data))
    # --- dialogue
    m = copy.deepcopy(specs)
    m[0]['steps'][-1]['info'] = 'FalloutNV.esm:FFFFFF'
    kill('dialogue: INFO that does not exist', checks.validate_dialogue(m, dlg, data))
    m = copy.deepcopy(specs)
    m[1]['steps'][-1]['prompt'] = 'Something else entirely'
    kill('dialogue: prompt text altered', checks.validate_dialogue(m, dlg, data))
    m = copy.deepcopy(specs)
    m[0]['steps'][-1]['expect']['stage'] = {'VMS29': 999}
    kill('dialogue: expected stage not set by the result script', checks.validate_dialogue(m, dlg, data))
    m = copy.deepcopy(specs)
    m[2]['steps'][-1]['topic'] = m[3]['steps'][-1]['topic']
    kill('dialogue: INFO under the wrong topic', checks.validate_dialogue(m, dlg, data))
    # --- status doc twin
    base_doc = doc if doc is not None else '- stages: **%d**' % inv['counts']['stages']
    kill('status doc: wrong stage count', checks.validate_status(base_doc.replace('- stages: **%d**' % inv['counts']['stages'], '- stages: **1**'), out))
    # --- freshness twin
    stale = load('inventory.json')
    stale['counts']['stages'] += 1
    kill('freshness: stale inventory differs from rebuild', ['differs'] if json.loads(json.dumps(inv)) != stale else [])

    print()
    n_ok = sum(1 for _, ok in RESULTS if ok)
    print(f'{n_ok}/{len(RESULTS)} checks passed in {time.time() - t0:.1f}s')
    if FAILS:
        print('FAILED: ' + '; '.join(FAILS))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
