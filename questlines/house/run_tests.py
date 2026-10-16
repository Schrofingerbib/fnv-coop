"""One command for the House questline lane:   python -I questlines/house/run_tests.py     (prints ALL OK, exit 0)

Checks, each with a mutation twin (a deliberately broken variant the validator must reject):
  scanner      qlib.scan_source on synthetic scripts                      twins: comment stripping off, vocabulary missing `moveto`
  inventory    stages/objectives/ids/tiers/stage setters vs quests.json    twins: drop stage, bad FormID, shifted setter line, ...
  sweep        every stage swept or excluded with a reason, hex console    twins: wrong hex, EditorID in a command, dropped stage, ...
               grammar, ids resolve, prerequisite order, gating globals
  gaps         completeness vs a naive token count, locations point at     twins: dropped function, dropped location, shifted line, ...
               the function, effect vocabulary == effect.h
  rules patch  applies to docs/quest-policies/rules.json, kinds/markers    twins: unknown quest/kind/marker, duplicate kind, no `why`
               resolve, new kinds/params declared
  docs         every `Script:line~needle` citation and FormRef in the      twins: wrong line, wrong needle, unknown FormRef
               hand-written markdown resolves; status doc counts match
  fresh        regenerated files are byte-identical to the ones on disk
Never touches the game, the game install, the INIs, plugins.txt or saves; no network; no commits.
"""
import copy
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnmap  # noqa: E402
import gaps as gaps_mod  # noqa: E402
import gen  # noqa: E402
import inventory as inventory_mod  # noqa: E402
import l5facts  # noqa: E402
import qlib  # noqa: E402
import rules_patch  # noqa: E402
import sweep as sweep_mod  # noqa: E402
import universe  # noqa: E402
import validators as V  # noqa: E402

ROOT = qlib.ROOT
STATUS_DOC = os.path.join(ROOT, 'docs', 'questlines', 'house.md')
FAILS = []
T0 = time.time()


def check(name, errors):
    if errors:
        FAILS.append(name)
        print(f'FAIL {name}')
        for e in errors[:8]:
            print(f'     {e}')
        if len(errors) > 8:
            print(f'     ... {len(errors) - 8} more')
    else:
        print(f'PASS {name}')


def twin(name, errors):
    """A mutation twin passes when the validator REJECTS the broken variant."""
    if errors:
        print(f'PASS {name} (rejected: {errors[0][:90]})')
    else:
        FAILS.append(name)
        print(f'FAIL {name}: the broken variant was accepted')


# ------------------------------------------------------------------------------------------------ scanner
def scanner_errors(mod):
    err = []
    src = ('scn T\n'
           'begin GameMode\n'
           '  ; player.moveto Nowhere\n'
           '  player.MoveTo VHouseEndGameMarker\n'
           '  if GetStage Q == 1\n'
           '    SetStage Q 10\n'
           '  endif\n'
           '  Set GlobalOne to 2\n'
           '  Set QuestOne.iVar to 3\n'
           '  if GameDaysPassed > 1\n'
           '  endif\n'
           'end\n')
    calls = mod.scan_source(src, known_quests=frozenset({'QuestOne'}), known_globals=frozenset({'GlobalOne'}))
    by = {(c['fn'], c['line']): c for c in calls}
    if ('moveto', 4) not in by or not by[('moveto', 4)]['player']:
        err.append('player.MoveTo on line 4 not found as a player-subject moveto')
    if any(c['line'] == 3 for c in calls):
        err.append('a commented-out line was scanned')
    if ('setstage', 6) not in by or not by[('setstage', 6)]['cond']:
        err.append('SetStage inside `if` not flagged conditional')
    if ('set global', 8) not in by or ('set questvar', 9) not in by:
        err.append('set global / set questvar missing')
    if ('read clock', 10) not in by:
        err.append('GameDaysPassed read not found')
    return err


def run_scanner():
    check('scanner: synthetic script', scanner_errors(qlib))

    class NoComment:
        def __getattr__(self, k):
            return getattr(qlib, k)

    saved = qlib.strip_comment
    qlib.strip_comment = lambda line: line
    try:
        twin('scanner twin: comments not stripped', scanner_errors(qlib))
    finally:
        qlib.strip_comment = saved
    saved_v = qlib.VOCAB
    qlib.VOCAB = saved_v - {'moveto'}
    try:
        twin('scanner twin: vocabulary without moveto', scanner_errors(qlib))
    finally:
        qlib.VOCAB = saved_v



# ------------------------------------------------------------------------------------------------ L5 facts + verdicts
ENGINE_SNIPPET = """
// g_hub->record(SwapTexture{r, t});      <- commented out: NOT a producer
const Entry kEntries[] = {
    HK(SetStage, h_SetStage, o_SetStage, true),
    HK(Cmd_SetDestroyed, h_SetDestroyed, o_SetDestroyed, true),
};
BLOCK_CMD(StartCombat)
void f() { g_hub->record(SetStage{qr, stage}); ObjectState os; os.ref = rr; g_hub->record(os); g_hub->recordScriptVar({qr, id, v}); }
"""
APPLY_SNIPPET = "if (auto* x = std::get_if<SetStage>(&e)) {} if (auto* x = std::get_if<ObjectState>(&e)) {}"
HUB_SNIPPET = "#else\nconstexpr bool kMutEmitPerScope = false;\nconstexpr bool kMutRecordAmbient = false;\n#endif"
ETYPES = ['SetStage', 'ObjectState', 'SwapTexture', 'SetScriptVar']


def l5_errors():
    err = []
    f = l5facts.parse(ENGINE_SNIPPET, APPLY_SNIPPET, HUB_SNIPPET, '', ETYPES, '')
    if f['hooks'] != {'SetStage', 'Cmd_SetDestroyed'}:
        err.append(f'hooks {sorted(f["hooks"])}')
    if f['producers'] != {'SetStage', 'ObjectState', 'SetScriptVar'}:
        err.append(f'producers {sorted(f["producers"])} (commented-out SwapTexture must not count)')
    if f['block_only'] != {'StartCombat'}:
        err.append(f'block_only {sorted(f["block_only"])}')
    if f['apply_engine'] != {'SetStage', 'ObjectState'}:
        err.append(f'apply_engine {sorted(f["apply_engine"])}')
    if f['ambient_recorded'] is not False:
        err.append('ambient policy not parsed as False')
    return err


def verdict_errors():
    """fnmap.resolve against synthetic facts: the verdict must follow the facts, including the ambient-scope rule."""
    err = []
    full = dict(hooks={'Cmd_SetDestroyed'}, block_only=set(), producers={'ObjectState'}, apply_engine={'ObjectState'}, apply_coop=set(),
                ambient_recorded=False, rules_producers=set(), effect_types=[])
    v = fnmap.resolve('setdestroyed', False, 'stage', full)
    if v['status'] != 'supported':
        err.append(f'setdestroyed in a stage script should be supported, got {v["status"]}')
    v = fnmap.resolve('setdestroyed', False, 'object', full)
    if v['status'] != 'missing' or 'AMBIENT' not in v['tags']:
        err.append(f'setdestroyed in an object script must be dropped as ambient, got {v["status"]} {v["tags"]}')
    amb = dict(full, ambient_recorded=True)
    if fnmap.resolve('setdestroyed', False, 'object', amb)['status'] != 'supported':
        err.append('with ambient recording on, setdestroyed in an object script should be supported')
    noprod = dict(full, producers=set())
    if fnmap.resolve('setdestroyed', False, 'stage', noprod)['status'] not in ('partial', 'missing'):
        err.append('without a producer the verdict cannot be supported')
    if fnmap.resolve('setdestroyed', False, 'stage', dict(noprod, apply_engine=set()))['status'] != 'missing':
        err.append('no producer and no applier must be missing')
    if fnmap.resolve('getdistance', True, 'object', full)['status'] != 'n/a':
        err.append('queries are n/a')
    return err


def run_l5():
    check('l5facts: parses hooks/producers/appliers from synthetic C++', l5_errors())
    saved = l5facts.strip_cpp
    l5facts.strip_cpp = lambda t: t
    try:
        twin('l5facts twin: comments not stripped', l5_errors())
    finally:
        l5facts.strip_cpp = saved
    check('fnmap: verdict follows the facts (hook, producer, applier, ambient scope)', verdict_errors())
    saved_k = fnmap.AMBIENT_KINDS
    fnmap.AMBIENT_KINDS = ()
    try:
        twin('fnmap twin: ambient rule disabled', verdict_errors())
    finally:
        fnmap.AMBIENT_KINDS = saved_k
    saved_s = fnmap.SPEC['setdestroyed']['hook']
    fnmap.SPEC['setdestroyed']['hook'] = 'Cmd_NoSuchHook'
    try:
        twin('fnmap twin: hook name wrong', verdict_errors())
    finally:
        fnmap.SPEC['setdestroyed']['hook'] = saved_s

# ------------------------------------------------------------------------------------------------ main
def main():
    d = qlib.data()
    print(f'data loaded in {time.time() - T0:.1f}s: {len(d.quests)} quests, {len(d.scripts)} standalone scripts')
    run_scanner()
    run_l5()

    # ---- build every artefact once
    uni = universe.build(d)
    rows = gaps_mod.classify_calls(d, uni)
    inv = inventory_mod.build(d)
    sw = sweep_mod.build(d)
    gp = gaps_mod.build(d, uni, rows)
    patch = rules_patch.build(d)
    built = {'inventory.json': gen.dumps(inv), 'sweep.json': gen.dumps(sw), 'gaps.json': gen.dumps(gp, compact=True),
             'gaps.md': gaps_mod.to_md(gp), 'rules.patch.json': gen.dumps(patch)}
    print(f'artefacts built at {time.time() - T0:.1f}s')

    # ---- fresh
    def fresh_errors(name, text, cur):
        return [] if cur == text else [f'{name} on disk differs from the generator output (run: python -I questlines/house/gen.py)']

    stale = []
    for name, text in built.items():
        p = os.path.join(HERE, name)
        stale += fresh_errors(name, text, open(p, encoding='utf-8').read() if os.path.exists(p) else None)
    check('fresh: files on disk == generator output', stale)
    twin('fresh twin: one changed byte is detected', fresh_errors('sweep.json', built['sweep.json'], built['sweep.json'] + ' '))

    # ---- inventory
    check('inventory: valid', V.validate_inventory(inv, d))
    for how in ('drop_stage', 'bad_formid', 'bad_setter_line', 'drop_quest', 'bad_objective_target', 'wrong_stage_flag'):
        m = copy.deepcopy(inv)
        inventory_mod._mutate(m, how)
        twin(f'inventory twin: {how}', V.validate_inventory(m, d))

    # ---- sweep
    check('sweep: valid', V.validate_sweep(sw, d))
    for how in ('bad_hex', 'edid_in_cmd', 'drop_stage', 'unresolved_ref', 'bad_command', 'wrong_stage_cmd'):
        m = copy.deepcopy(sw)
        sweep_mod._mutate(m, how)
        twin(f'sweep twin: {how}', V.validate_sweep(m, d))
    # the L7 driver only needs these keys (plugin/snapshot/tools/goodsprings_sweep.py load_plan)
    miss = [q['edid'] for q in sw['quests'] if not all(k in q for k in ('edid', 'ref', 'stages', 'tier_direct'))
            or not all('index' in s for s in q['stages'])]
    check('sweep: readable by the L7 load_plan()', [f'{m}: missing key' for m in miss])
    # a gating global that is not set by any setup command must be caught
    m = copy.deepcopy(sw)
    q = next(x for x in m['quests'] if x['edid'] == 'VMQHouse2')
    s = next(x for x in q['stages'] if x['index'] == 100)
    s['setup'] = [c for c in s['setup'] if c['kind'] != 'global']
    twin('sweep twin: stage-entry condition not satisfied', V.validate_sweep(m, d))

    # ---- gaps
    check('gaps: valid', V.validate_gaps(gp, d, uni))
    for how in ('drop_function', 'drop_location', 'bad_line', 'status_flip', 'count_off'):
        m = copy.deepcopy(gp)
        gaps_mod._mutate(m, how)
        twin(f'gaps twin: {how}', V.validate_gaps(m, d, uni))
    s = gp['summary']
    check('gaps: summary arithmetic', [] if sum(s['calls_by_status'].values()) == s['calls'] and s['calls'] == len(rows)
          else [f'calls_by_status {s["calls_by_status"]} vs {len(rows)} rows'])

    # ---- rules patch
    check('rules.patch: valid', V.validate_patch(patch, d))
    for how in ('unknown_quest', 'unknown_kind', 'bad_marker', 'remove_absent_rule', 'duplicate_kind', 'no_why'):
        m = copy.deepcopy(patch)
        rules_patch._mutate(m, how)
        twin(f'rules.patch twin: {how}', V.validate_patch(m, d))
    m = copy.deepcopy(patch)
    m['base_sha256'] = '0' * 64
    twin('rules.patch twin: stale base hash', V.validate_patch(m, d))

    # ---- hand-written docs
    docs = {}
    errs = []
    total_cites = 0
    for name in ('policy_review.md', 'dialogue.md'):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            errs.append(f'{name} is missing')
            continue
        text = open(p, encoding='utf-8').read()
        docs[name] = text
        e, n = V.validate_doc(text, d, name)
        errs += e
        total_cites += n
        if n < 20:
            errs.append(f'{name}: only {n} verified citations (expected >= 20)')
    check('docs: policy_review.md + dialogue.md citations and FormRefs resolve', errs)
    if docs:
        for how, fn in (('wrong line', lambda t: t.replace('`MrHouseSCRIPT:78~moveto`', '`MrHouseSCRIPT:79~moveto`', 1)),
                        ('wrong needle', lambda t: t.replace('`MrHouseSCRIPT:78~moveto`', '`MrHouseSCRIPT:78~autosave`', 1)),
                        ('unknown FormRef', lambda t: t + ' FalloutNV.esm:FFFFF0 '),
                        ('unknown script', lambda t: t + ' `NoSuchScriptEver:3` ')):
            t = fn(docs['policy_review.md'])
            if t == docs['policy_review.md']:
                FAILS.append(f'docs twin: {how}')
                print(f'FAIL docs twin: {how}: anchor citation `MrHouseSCRIPT:78~moveto` not present in policy_review.md')
                continue
            e, _ = V.validate_doc(t, d, 'policy_review.md')
            twin(f'docs twin: {how}', e)
    check('docs: required sections present', [f'{n}: missing `{h}`' for n, hs in (('policy_review.md', ('## 3. Findings', 'F4.', 'F5.')),
                                                                              ('dialogue.md', ('## 3. Proposed specs', '## 4. Where the initiator-picks model breaks')))
                                              for h in hs if n in docs and h not in docs[n]])

    # ---- status doc: counts must match the data
    errs = []
    if not os.path.exists(STATUS_DOC):
        errs.append('internal questline notes is missing')
    else:
        t = open(STATUS_DOC, encoding='utf-8').read()
        m = re.search(r'<!-- house-counts (\{.*?\}) -->', t)
        if not m:
            errs.append('status doc has no <!-- house-counts {...} --> block')
        else:
            c = json.loads(m.group(1))
            owned = [q for q in inv['quests'] if q['role'] in ('core', 'system')]
            want = dict(quests=len(owned), stages=sum(len(q['stages']) for q in owned),
                        swept=sw['summary']['stages_swept'], excluded=sw['summary']['excluded'],
                        patch_ops=len(patch['ops']), stages_without_setter=inv['summary']['stages_without_setter'],
                        functions_missing=gp['summary']['functions_missing'], functions_partial=gp['summary']['functions_partial'],
                        gap_calls=gp['summary']['gap_calls_by_severity'], gap_calls_house=gp['summary']['gap_calls_by_lane_severity'].get('house', {}),
                        scripts=gp['summary']['scripts'], calls=gp['summary']['calls'], citations=total_cites)
            for k, v in want.items():
                if c.get(k) != v:
                    errs.append(f'status doc count {k}={c.get(k)!r} but the data says {v!r}')
            e2, _ = V.validate_doc(t.split('## Test output')[0], d, 'internal questline notes')   # the pasted output quotes the twins' deliberately bad ids
            errs += e2
            if 'ALL OK' not in t:
                errs.append('status doc does not contain the pasted test output (ALL OK)')
            sec = [h for h in ('Top 10 blockers', 'Counts', 'Test output') if h not in t]
            errs += [f'status doc lacks the `{h}` section' for h in sec]
    check('status doc: counts match the data, sections present', errs)

    # ---- hygiene: the lane never launches the game
    bad = []
    for fn in os.listdir(HERE):
        if fn.endswith('.py') and fn != 'run_tests.py':
            src = open(os.path.join(HERE, fn), encoding='utf-8').read()
            for pat in (r'subprocess', r'os\.system', r'FalloutNV\.exe', r'plugins\.txt', r'urllib', r'requests'):
                if re.search(pat, src):
                    bad.append(f'{fn}: references {pat}')
    check('hygiene: no game launch / network / plugins.txt in the lane code', bad)

    dt = time.time() - T0
    print(f'{dt:.0f}s')
    if FAILS:
        print(f'FAILED {len(FAILS)}: ' + ', '.join(FAILS))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
