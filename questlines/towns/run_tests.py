"""One-command test gate for the towns questline:  python -I questlines/towns/run_tests.py   ->  prints ALL OK, exit 0.

1. unit tests of the helpers (hex ids, effect-name parse, status table, severity rule)
2. every committed output validates against out/questdata (checks.validate)
3. the outputs are byte-identical to a fresh regeneration (no hand edits, no stale files)
4. mutation twin: each deliberately broken copy of an output MUST be rejected by the validator
"""
import copy
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analysis  # noqa: E402
import build  # noqa: E402
import checks  # noqa: E402
import fnmap  # noqa: E402
import lib  # noqa: E402
import policy  # noqa: E402

FILES = {'inventory': 'inventory.json', 'sweep': 'sweep.json', 'gaps': 'gaps.json', 'patch': 'rules.patch.json',
         'specs': 'dialogue_specs.json', 'dialogue': 'dialogue_data.json'}
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print('FAIL', msg)


def load_bundle(folder):
    return {k: json.load(open(os.path.join(folder, f), encoding='utf-8')) for k, f in FILES.items()}


def unit_tests(d):
    check(analysis.hex_id(d, 'FalloutNV.esm:10E908') == '10E908', 'hex_id base game must be 6 digits')
    check(analysis.hex_id(d, 'DeadMoney.esm:001234') == '01001234', 'hex_id DLC prefix 01')
    check(analysis.hex_id(d, 'LonesomeRoad.esm:000042') == '04000042', 'hex_id LonesomeRoad prefix 04')
    eff = checks.effect_names()
    check(len(eff) >= 33 and 'SetStage' in eff and 'FailQuest' in eff, 'effect.h variant parse found %d names' % len(eff))
    check(set(fnmap.EFFECT_STATUS) <= eff, 'EFFECT_STATUS names not all synccore effects: %s' % (set(fnmap.EFFECT_STATUS) - eff))
    for fn, (e, cls, haz, note) in fnmap.F.items():
        check(e is None or e in eff, 'fnmap %s -> %s is not a synccore effect' % (fn, e))
    check(analysis._severity('missing', ['spawn'], 'state', False) == 'high', 'severity: missing spawn is high')
    check(analysis._severity('missing', [], 'state', False) == 'medium', 'severity: missing plain is medium')
    check(analysis._severity('partial', [], 'state', False) == 'low', 'severity: partial plain is low')
    check(analysis._severity('supported', ['spawn'], 'state', False) == 'ok', 'severity: supported is ok')
    check(len(policy.KINDS) == 16, 'rule kinds must be the 16 existing ones')
    check(analysis.KILL_ALL_RE.search('Help the NCR kill all the Great Khans') is not None, 'kill-all regex')
    check(analysis.UNUSED_RE.search('This Stage No Longer Does Anything.') is not None, 'unused-stage regex')


def mutants(bundle):
    """name -> mutated bundle (each must produce >= 1 validation error)"""
    out = {}

    def mut(name, fn):
        b = copy.deepcopy(bundle)
        fn(b)
        out[name] = b
    mut('inventory: wrong FormID', lambda b: b['inventory']['quests'][0].__setitem__('formid', '00000001'))
    mut('inventory: drop a quest', lambda b: b['inventory']['quests'].pop(3))
    mut('inventory: drop a stage', lambda b: b['inventory']['quests'][1]['stages'].pop())
    mut('inventory: bogus prerequisite quest', lambda b: b['inventory']['quests'][12]['prerequisites']['gated_by'].append(
        {'quest': 'NoSuchQuest', 'strength': 'hard', 'count': 1, 'where': 'x', 'text': 'x'}))
    mut('sweep: stage dropped without reason', lambda b: next(q for q in b['sweep']['quests'] if q['edid'] == 'VMS20')['stages'].pop(0))
    mut('sweep: exclusion without reason', lambda b: next(q for q in b['sweep']['quests'] if q['edid'] == 'VMS15')['excluded_stages'][0].__setitem__('reason', ''))
    mut('sweep: unknown FormID in setup', lambda b: next(q for q in b['sweep']['quests'] if q['edid'] == 'VMS19')['setup'].append({'cmd': 'startquest 7F0000', 'why': 'x'}))
    mut('sweep: console command that does not exist', lambda b: next(q for q in b['sweep']['quests'] if q['edid'] == 'VMS19')['setup'].append({'cmd': 'player.additem 0000000F 100', 'why': 'x'}))
    mut('sweep: stage that is not in the quest', lambda b: next(q for q in b['sweep']['quests'] if q['edid'] == 'VMS19')['stages'][0].__setitem__('set_cmd', 'setstage 10E196 999'))
    mut('sweep: variant with unknown script variable', lambda b: next(s for q in b['sweep']['quests'] if q['edid'] == 'VMS20' for s in q['stages'] if s['variants'])['variants'][0]['setup'][0].__setitem__('cmd', 'set 10E908.bNoSuchVar to 1'))
    mut('sweep: summary lies', lambda b: b['sweep']['summary'].__setitem__('stages_swept', 1))
    mut('gaps: effect that is not in synccore', lambda b: next(i for i in b['gaps']['items'] if i['effect'] == 'SetStage').__setitem__('effect', 'TeleportParty'))
    mut('gaps: capture/apply status altered', lambda b: next(i for i in b['gaps']['items'] if i['effect'] == 'SetFactionRank').__setitem__('capture', 'supported'))
    mut('gaps: line beyond the script', lambda b: next(i for i in b['gaps']['items'] if i['source'] == 'quest-script').__setitem__('line', 99999))
    mut('gaps: statement dropped (completeness)', lambda b: b['gaps']['items'].pop(5))
    mut('gaps: supported item flagged high', lambda b: next(i for i in b['gaps']['items'] if i['status'] == 'supported').__setitem__('severity', 'high'))
    mut('gaps: unknown quest', lambda b: b['gaps']['items'][0].__setitem__('quest', 'NoSuchQuest'))
    mut('patch: kind outside the 16', lambda b: next(iter(b['patch']['quests'].values()))['add'].append({'kind': 'TeleportParty', 'params': {}, 'origin': 'curated', 'reason': 'x'}))
    mut('patch: duplicate of an existing kind', lambda b: b['patch']['quests']['VMS19'].__setitem__('add', b['patch']['quests']['VMS19']['add'] + [
        {'kind': 'PartyAwareCond', 'params': {'funcs': {}}, 'origin': 'curated', 'reason': 'x'}]))
    mut('patch: quest that does not exist', lambda b: b['patch']['quests'].__setitem__('NoSuchQuest', {'add': []}))
    mut('patch: set_params on a kind the quest lacks', lambda b: b['patch']['quests']['VMS20'].__setitem__('set_params', {'FastTravelLock': {'scope': 'party'}}))
    mut('patch: unresolvable cell', lambda b: b['patch']['quests']['VMS21a'].__setitem__('add', b['patch']['quests']['VMS21a']['add'] + [
        {'kind': 'PartyTether', 'params': {'cells': ['NoSuchCell'], 'gather': 'auto-warp'}, 'origin': 'curated', 'reason': 'x'}]))
    mut('patch: reduction outside the L4 set', lambda b: b['patch']['quests']['VNipton']['set_params']['PartyAwareCond']['funcs'].__setitem__('GetPlayerTeammate', 'majority'))
    mut('patch: rule without a reason', lambda b: b['patch']['quests']['VMS19']['add'][0].__setitem__('reason', ''))
    mut('dialogue: INFO that does not exist', lambda b: b['specs']['specs'][0]['steps'][2].__setitem__('info', 'FalloutNV.esm:FFFFFF'))
    mut('dialogue: NPC ref that does not exist', lambda b: b['specs']['specs'][2]['steps'][1].__setitem__('npc_ref', 'FalloutNV.esm:FFFFFE'))
    mut('dialogue: row with wrong topic', lambda b: b['dialogue']['rows'][0].__setitem__('topic', 'FalloutNV.esm:000001'))
    return out


def main():
    d = lib.Data()
    ctx = checks.Ctx(d)
    unit_tests(d)

    bundle = load_bundle(HERE)
    errs = checks.validate(bundle, d, ctx)
    for e in errs[:40]:
        print('FAIL', e)
    check(not errs, '%d validation errors in the committed outputs' % len(errs))

    # byte-identical regeneration
    with tempfile.TemporaryDirectory() as tmp:
        sys.argv = ['build.py', '--out', tmp]
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            build.main(['--out', tmp])
        for f in list(FILES.values()) + ['gaps.md', 'dialogue.md', 'policy_review.md']:
            a = open(os.path.join(HERE, f), 'rb').read()
            b = open(os.path.join(tmp, f), 'rb').read()
            check(a == b, 'stale output: %s differs from a fresh build (run build.py)' % f)

    # deliverables exist
    for f in ('inventory.json', 'sweep.json', 'gaps.json', 'gaps.md', 'policy_review.md', 'rules.patch.json', 'dialogue.md'):
        check(os.path.exists(os.path.join(HERE, f)), 'missing deliverable %s' % f)
    check(os.path.exists(os.path.join(lib.REPO, 'docs', 'questlines', 'towns.md')), 'missing internal questline notes')
    md = open(os.path.join(HERE, 'gaps.md'), encoding='utf-8').read()
    for sec in ('## Unsupported / partial functions', '## Exact locations', '## Co-op hazards by category'):
        check(sec in md, 'gaps.md lacks section %s' % sec)

    # mutation twin
    caught = 0
    for name, mb in mutants(bundle).items():
        merrs = checks.validate(mb, d, ctx)
        if merrs:
            caught += 1
            print('MUTANT caught: %-52s (%d errors, e.g. %s)' % (name, len(merrs), merrs[0][:70]))
        else:
            check(False, 'MUTANT SURVIVED: %s' % name)
    print('mutants caught: %d' % caught)

    if failures:
        print('%d FAILURES' % len(failures))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
