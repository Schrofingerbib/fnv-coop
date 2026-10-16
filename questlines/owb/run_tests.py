"""One command for the OWB questline:  python -I questlines/owb/run_tests.py   (prints ALL OK, exit 0).

Real checks validate the committed deliverables against out/questdata; every validator also gets a deliberately broken copy
(mutation twin) that it MUST reject.
"""
import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import checks  # noqa: E402
import common  # noqa: E402
import dialogue_specs  # noqa: E402
import fnmap  # noqa: E402
import gaps  # noqa: E402
import gen_all  # noqa: E402
import inventory  # noqa: E402
import rules_patch  # noqa: E402
import sweep  # noqa: E402

RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))


def expect_clean(name, errs):
    check(name, not errs, '; '.join(errs[:3]) + (' (+%d more)' % (len(errs) - 3) if len(errs) > 3 else ''))


def expect_caught(name, errs):
    check('mutation twin: ' + name, bool(errs), 'validator accepted a broken input')


def main():
    d = common.Data()
    expected, (d, inv, sw, gp, specs, ctx) = gen_all.build_all(d)
    patch = gen_all.patch_json(len(gp['quest_scripts_player_bound']))

    # ---- inputs sanity -------------------------------------------------------------------------------------------
    new_qust = json.load(open(os.path.join(common.QD, 'quests.json'), encoding='utf-8'))['meta']['plugins'][common.PLUGIN]['stats']['QUST']['new']
    check('OWB quest count equals the ESM parser count (QUST new)', len(d.owb_quests()) == new_qust == 62, '%d vs %d' % (len(d.owb_quests()), new_qust))
    check('load order puts OldWorldBlues.esm at index 3', d.loadorder.index(common.PLUGIN) == 3)

    # ---- committed files are current -------------------------------------------------------------------------------
    actual = {}
    for p in expected:
        actual[p] = open(p, encoding='utf-8', newline='').read() if os.path.exists(p) else None
    expect_clean('committed deliverables equal regenerated output (%d files)' % len(expected), checks.validate_docs_current(expected, actual))
    stale = dict(actual)
    first = sorted(stale)[0]
    stale[first] = (stale[first] or '') + ' '
    expect_caught('a stale deliverable is detected', checks.validate_docs_current(expected, stale))

    # ---- inventory -------------------------------------------------------------------------------------------------
    expect_clean('inventory covers all %d quests, %d stages, ids/tiers/objective targets resolve' % (inv['summary']['quests'], inv['summary']['stages']),
                 checks.validate_inventory(d, inv))
    expect_clean('role tables name only real quests', checks.validate_roles(inventory, d))
    m = copy.deepcopy(inv)
    next(q for q in m['quests'] if q['stages'])['stages'].pop()
    expect_caught('inventory with a dropped stage', checks.validate_inventory(d, m))
    m = copy.deepcopy(inv)
    m['quests'][0]['runtime_formid'] = '01' + m['quests'][0]['runtime_formid'][2:]
    expect_caught('inventory with a wrong load-order prefix', checks.validate_inventory(d, m))
    m = copy.deepcopy(inv)
    m['quests'] = m['quests'][1:]
    expect_caught('inventory missing a quest', checks.validate_inventory(d, m))

    # ---- sweep -----------------------------------------------------------------------------------------------------
    expect_clean('sweep covers %d/%d stages; every command grammar-checked and every hex FormID resolves' % (
        sw['summary']['stages_swept'], inv['summary']['stages']), checks.validate_sweep(d, inv, sw))
    check('sweep keeps the m1_goodsprings keys L7 reads (quests[].edid/ref/stages[].index/tier)',
          all({'edid', 'ref', 'stages', 'tier'} <= set(q) and all('index' in s for s in q['stages']) for q in sw['quests'])
          and {'milestone', 'core', 'system_quests', 'touched_quests', 'summary', 'quests'} <= set(sw))
    def chain_ok(q):
        for st in q['stages']:
            for c in st['setup']:
                mm = re.match(r'setstage ([0-9A-F]{8}) (\d+)$', c)
                if mm and mm.group(1) == q['runtime_formid'] and int(mm.group(2)) >= st['index']:
                    return False
        return True
    check('stage setup chains only reference EARLIER stages of the same quest (stage N is never set up by N or later)',
          all(chain_ok(q) for q in sw['quests']))
    q = copy.deepcopy(next(q for q in sw['quests'] if q['edid'] == 'NVDLC03MQ05'))
    q['stages'][0]['setup'].append('setstage %s 100' % q['runtime_formid'])
    check('mutation twin: a setup chain that sets a later stage is detected', not chain_ok(q))
    m = copy.deepcopy(sw)
    m['quests'][0]['stages'].pop()
    expect_caught('sweep with a dropped stage', checks.validate_sweep(d, inv, m))
    m = copy.deepcopy(sw)
    q = next(q for q in m['quests'] if q['stages'][-1]['setup'])
    q['stages'][-1]['setup'][0] = 'player.moveto 03FFFFFF'
    expect_caught('sweep with an invented FormID', checks.validate_sweep(d, inv, m))
    m = copy.deepcopy(sw)
    q = next(q for q in m['quests'] if q['edid'] == 'NVDLC03MQ05')
    q['stages'][0]['setup'].append('setstage %s 999' % q['runtime_formid'])
    expect_caught('sweep with an undefined stage number', checks.validate_sweep(d, inv, m))
    m = copy.deepcopy(sw)
    m['quests'][0]['stages'][0]['setup'].append('coc NVDLC03TheSink')
    expect_caught('sweep with a command outside the allowed grammar', checks.validate_sweep(d, inv, m))
    m = copy.deepcopy(sw)
    m['quests'][0]['stages'][0]['setup'].append('player.moveto 02000001')
    expect_caught('sweep with a FormID in the wrong load-order slot', checks.validate_sweep(d, inv, m))

    # ---- gaps ------------------------------------------------------------------------------------------------------
    units = checks._unit_sources(d)
    expect_clean('gaps: %d calls, every unsupported/partial location exists in the script text, counts add up' % gp['calls'],
                 checks.validate_gaps(d, gp, units))
    expect_clean('gaps cover every control-lock and MoveTo finding of the independent risk.json classifier',
                 checks.validate_gaps_vs_risk(d, gp))
    check('no statement-level call is missing from the function table', gp['statement_level_calls_not_in_table'] == {})
    check('all 9 hazard classes asked for in the brief are populated',
          all(gp['hazards'].get(t, {}).get('count', 0) > 0 for t in
              ('player-effect', 'spawn', 'companion', 'move-player', 'cinematic', 'menu', 'timer', 'radio', 'ending', 'kill')))
    m = copy.deepcopy(gp)
    r = next(r for r in m['functions'] if r['fn'] == 'DisablePlayerControls')
    r['locations'][0]['line'] = 1
    expect_caught('gaps with a wrong line number', checks.validate_gaps(d, m, units))
    m = copy.deepcopy(gp)
    m['functions'].pop(0)
    expect_caught('gaps with a dropped function', checks.validate_gaps(d, m, units))
    m = copy.deepcopy(gp)
    m['functions'][0]['locations'][0]['unit'] = 'NoSuchScript'
    expect_caught('gaps with an unknown script', checks.validate_gaps(d, m, units))
    saved = fnmap.FN.pop('setenemy')
    try:
        g2 = gaps.build_gaps(d)
    finally:
        fnmap.FN['setenemy'] = saved
    expect_caught('function table missing SetEnemy (new unmapped statement)', checks.validate_gaps(d, g2, units))

    # ---- rules patch -----------------------------------------------------------------------------------------------
    expect_clean('rules patch: %d quests, only the 16 kinds + %d justified new kind, every EDID param resolves, ops match rules.json' % (
        len(patch['quests']), len(patch['new_kinds'])), checks.validate_patch(d, patch))
    m = copy.deepcopy(patch)
    m['quests']['NVDLC03MQ05']['rules'][0]['kind'] = 'PartyHug'
    expect_caught('patch with an unknown rule kind', checks.validate_patch(d, m))
    m = copy.deepcopy(patch)
    m['quests']['NVDLC03Fake'] = m['quests'].pop('NVDLC03MQ00')
    expect_caught('patch keyed by a non-OWB quest', checks.validate_patch(d, m))
    m = copy.deepcopy(patch)
    m['quests']['NVDLC03MQ05']['rules'][0]['params']['cells'] = ['NVDLC03NoSuchCell']
    expect_caught('patch with an unresolvable cell EDID', checks.validate_patch(d, m))
    m = copy.deepcopy(patch)
    m['quests']['NVDLC03ThinkTankQuest']['op'] = 'replace'
    expect_caught('patch with a wrong op', checks.validate_patch(d, m))
    m = copy.deepcopy(patch)
    for r in m['quests']['NVDLC03MQ05']['rules']:
        if r['kind'] == 'SetPlayerControls':
            r['params']['masks'] = ['1 1 1']
    expect_caught('patch with a malformed control mask', checks.validate_patch(d, m))
    m = copy.deepcopy(patch)
    m['new_kinds'][0]['justification'] = 'because'
    expect_caught('patch with an unjustified new kind', checks.validate_patch(d, m))
    m = copy.deepcopy(patch)
    del m['quests']['NVDLC03MQ01']
    expect_caught('patch that ignores an existing rules.json entry', checks.validate_patch(d, m))
    check('LocalQuestScript justification quotes the computed count of player-bound quest scripts',
          ('%d OWB quest scripts' % len(gp['quest_scripts_player_bound'])) in patch['new_kinds'][0]['justification'],
          'justification says a different number than gaps.json (%d)' % len(gp['quest_scripts_player_bound']))

    # ---- dialogue specs --------------------------------------------------------------------------------------------
    expect_clean('%d dialogue specs: INFO ids, prompts, line prefixes, setup commands all match the game data' % len(specs),
                 checks.validate_specs(d, specs))
    m = copy.deepcopy(specs)
    pick = next(s for s in m['dlg_owb_klein_assign_tasks']['steps'] if s['action'] == 'pick')
    pick['line'] = 'Not what he says'
    expect_caught('spec with the wrong line text', checks.validate_specs(d, m))
    m = copy.deepcopy(specs)
    next(s for s in m['dlg_owb_klein_assign_tasks']['steps'] if s['action'] == 'pick')['info'] = 'OldWorldBlues.esm:FFFFFF'
    expect_caught('spec with an invented INFO id', checks.validate_specs(d, m))
    md = open(os.path.join(HERE, 'dialogue.md'), encoding='utf-8').read()
    blocks = re.findall(r'```json\n(.*?)\n```', md, re.S)
    check('dialogue.md embeds every spec verbatim', [json.loads(b) for b in blocks] == list(specs.values()))
    try:
        dialogue_specs.Gen(d).info('01355E', 'Totally different line')
        caught = False
    except SystemExit:
        caught = True
    check('mutation twin: spec builder refuses a line that is not in the INFO', caught)

    # ---- prose / numbers -------------------------------------------------------------------------------------------
    status = open(gen_all.STATUS_PATH, encoding='utf-8').read()
    check('status doc states the computed counts', ('| quests in the DLC | %d ' % inv['summary']['quests']) in status and
          ('%d of %d' % (sw['summary']['stages_swept'], inv['summary']['stages'])) in status)
    check('status doc lists exactly 10 ranked blockers', len(re.findall(r'^\d+\. \*\*', status, re.M)) == 10)
    pr = open(os.path.join(HERE, 'policy_review.md'), encoding='utf-8').read()
    check('policy review covers every OWB quest that the patch touches',
          all(('**%s**' % e) in pr or ('| %s |' % e.replace('NVDLC03', '')) in pr for e in rules_patch.QUESTS))

    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(('PASS  ' if ok else 'FAIL  ') + name + ('' if ok else '   <- ' + detail))
    print('%d checks, %d failed' % (len(RESULTS), len(bad)))
    if bad:
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
