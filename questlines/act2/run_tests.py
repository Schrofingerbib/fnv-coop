"""act2 questline tests.   python -I questlines/act2/run_tests.py     -> prints ALL OK and exits 0

What is checked
  * the data products are rebuilt from a PINNED COPY of the inputs (no dependence on files another lane rewrites mid-run),
    are deterministic, and equal the committed files when those were built from the same inputs (else: STALE warning);
  * inventory/sweep/gaps/rules.patch/dialogue outputs validate against the quest data (IDs resolve, every stage is swept or
    excluded with a reason, every gap location is a real script line containing the function, patch keys exist, spec INFOs match);
  * status claims are cross-checked against the L4/L5 source (Effect types in effect.h, hooks in engine_addrs.h, appliers in
    apply_remote.cpp) so "supported" cannot be claimed for something that has no hook/applier;
  * MUTATION TWINS: every validator is run against deliberately broken copies of the outputs and must report errors.
"""
import copy
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import dialogue as dlg_mod  # noqa: E402
import fnmap  # noqa: E402
import gaps as gaps_mod  # noqa: E402
import lib  # noqa: E402
import policy as policy_mod  # noqa: E402
import sweep as sweep_mod  # noqa: E402

REPO = lib.REPO
OUTPUT_FILES = ['inventory.json', 'sweep.json', 'gaps.json', 'gaps.md', 'rules.patch.json', 'policy_review.md', 'dialogue.json', 'dialogue.md']
SPEC_KEYS = {'schema', 'kind', 'id', 'name', 'plugin', 'summary', 'tier', 'players', 'quests', 'save', 'policy_checks', 'setup', 'steps',
             'invariants', 'oracle'}
SPEC_ACTIONS = {'setup', 'talk', 'parallel', 'pick', 'pick_info', 'pick_nowait', 'leave', 'event', 'assert'}
CMD_RX = [
    re.compile(r'^startquest ([0-9A-F]+)$'),
    re.compile(r'^setstage ([0-9A-F]+) (\d+)$'),
    re.compile(r'^setobjective(?:displayed|completed) ([0-9A-F]+) (\d+) [01]$'),
    re.compile(r'^set ([0-9A-F]+)\.(\w+) to (-?\d+)$'),
    re.compile(r'^set ([0-9A-F]+) to (-?\d+(?:\.\d+)?)$'),
    re.compile(r'^player\.additem ([0-9A-F]+) (\d+)$'),
]

# capture/apply claims -> the source that must back them
HOOK_FOR = {
    'setstage': 'TESQuest::SetStage', 'setobjectivedisplayed': 'Cmd_SetObjectiveDisplayed', 'setobjectivecompleted': 'Cmd_SetObjectiveCompleted',
    'startquest': 'Cmd_StartQuest', 'stopquest': 'Cmd_StopQuest', 'completequest': 'Cmd_CompleteQuest', 'failquest': 'TESQuest::SetFailed',
    'enable': 'Cmd_Enable', 'disable': 'Cmd_Disable', 'kill': 'Cmd_KillActor', 'killactor': 'Cmd_KillActor', 'resurrect': 'Cmd_ResurrectActor',
    'rewardxp': 'Cmd_RewardXP', 'rewardkarma': 'Cmd_RewardKarma', 'addreputation': 'Cmd_AddReputation', 'setreputation': 'TESReputation::Set',
    'addreputationexact': 'TESReputation::AddExact', 'removereputation': 'TESReputation::Remove', 'moveto': 'Cmd_MoveToMarker',
    'movetomarker': 'Cmd_MoveToMarker', 'movetomarkerwithfade': 'Cmd_MoveToMarkerWithFade', 'setpos': 'Cmd_SetPos',
    'additem': 'Cmd_AddItem', 'removeitem': 'Cmd_RemoveItem', 'resurrectactor': 'Cmd_ResurrectActor',
    'modav': 'Cmd_ModActorValue', 'modactorvalue': 'Cmd_ModActorValue', 'forceav': 'Cmd_ForceActorValue', 'forceactorvalue': 'Cmd_ForceActorValue',
    'setav': 'Cmd_SetActorValue', 'setactorvalue': 'Cmd_SetActorValue', 'damageav': 'Cmd_DamageActorValue',
    'damageactorvalue': 'Cmd_DamageActorValue',
    'set': None, 'setquestvar': None, 'setglobalvalue': None,      # captured by the global / quest-var diff, no command hook
}
APPLY_FOR = {
    'setstage': 'SetStage', 'setobjectivedisplayed': 'SetObjective', 'setobjectivecompleted': 'SetObjective', 'startquest': 'StartQuest',
    'stopquest': 'StopQuest', 'completequest': 'CompleteQuest', 'failquest': 'FailQuest', 'enable': 'EnableRef', 'disable': 'DisableRef',
    'kill': 'KillActor', 'killactor': 'KillActor', 'resurrect': 'ResurrectActor', 'rewardxp': 'ModActorValue', 'rewardkarma': 'ModKarma',
    'addreputation': 'ModReputation', 'setreputation': 'ModReputation', 'addreputationexact': 'ModReputation', 'moveto': 'MoveRef',
    'additem': 'AddItem', 'removeitem': 'RemoveItem', 'modav': 'ModActorValue', 'forceav': 'ModActorValue', 'setav': 'ModActorValue',
    'damageav': 'ModActorValue', 'set': 'SetGlobal', 'setquestvar': 'SetScriptVar', 'modactorvalue': 'ModActorValue',
    'forceactorvalue': 'ModActorValue', 'setactorvalue': 'ModActorValue', 'damageactorvalue': 'ModActorValue', 'resurrectactor': 'ResurrectActor',
    'removereputation': 'ModReputation', 'setglobalvalue': 'SetGlobal', 'movetomarker': 'MoveRef', 'movetomarkerwithfade': 'MoveRef',
    'setpos': 'MoveRef',
}
EFFECT_EXTRA = {'PartyAwareCond', 'ShowMessageChoice', 'XP', 'AddItem', 'RemoveItem'}   # names used in descriptors that are not structs


# ---------------------------------------------------------------------------------------------------------------------
def pin_inputs(tmp):
    for rel in lib.INPUTS:
        dst = Path(tmp) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)


def read_json(p):
    return json.load(open(p, encoding='utf-8'))


def effect_names():
    txt = (REPO / 'synccore' / 'include' / 'fnvcoop' / 'effect.h').read_text(encoding='utf-8')
    return set(re.findall(r'^struct (\w+)', txt, re.M))


def hook_names():
    txt = (REPO / 'plugin' / 'hooks' / 'engine_addrs.h').read_text(encoding='utf-8')
    return set(re.findall(r'HookSpec \w+ = \{"([^"]+)"', txt))


def applier_types():
    txt = (REPO / 'plugin' / 'hooks' / 'apply_remote.cpp').read_text(encoding='utf-8')
    return set(re.findall(r'get_if<(\w+)>', txt))


def line_of(D, edid_map, script, line):
    """Text of a 1-based line of a script identified the way gaps.json names it, or None."""
    sc = edid_map.get(script)
    if sc is None:
        return None
    lines = sc.splitlines()
    return lines[line - 1] if 1 <= line <= len(lines) else None


def script_index(D, edids):
    """script label (as used in gaps.json locations) -> source text, for every unit of the questline."""
    units = gaps_mod.collect_units(D, edids)
    out = {}
    for u in units.values():
        out.setdefault(u['script_edid'], u['sc']['source'])
    return out


# ---- validators: each takes the parsed outputs and returns a list of error strings ------------------------------------
def v_inventory(D, inv, edids):
    err = []
    names = [q['edid'] for q in inv['quests']]
    if sorted(names) != sorted(edids):
        err.append('inventory quests %s != questline %s' % (sorted(names), sorted(edids)))
    for q in inv['quests']:
        src = D.by_edid.get(q['edid'])
        if not src:
            err.append('%s not in quests.json' % q['edid'])
            continue
        if q['ref'] != src['ref'] or q['formid'] != D.hexid(src['ref']):
            err.append('%s ref/formid mismatch %s %s' % (q['edid'], q['ref'], q['formid']))
        if sorted(s['index'] for s in q['stages']) != sorted(s['index'] for s in src['stages']):
            err.append('%s stage list differs from quests.json' % q['edid'])
        if sorted(o['index'] for o in q['objectives']) != sorted(o['index'] for o in src['objectives']):
            err.append('%s objective list differs from quests.json' % q['edid'])
        if q['tier'] != D.risk[src['ref']]['tier']:
            err.append('%s tier %s != risk.json %s' % (q['edid'], q['tier'], D.risk[src['ref']]['tier']))
        for o in q['objectives']:
            for t in o['targets']:
                if not D.resolves(t['ref']):
                    err.append('%s objective %s target %s does not resolve' % (q['edid'], o['index'], t['ref']))
        if q['dialogue']['own_infos'] != len(D.infos_by_quest.get(src['ref'], [])):
            err.append('%s own_infos mismatch' % q['edid'])
        if len(q['stages']) == 0 and src['stages']:
            err.append('%s lost its stages' % q['edid'])
        for d in q['prerequisites']['driven_by'] + q['prerequisites']['reads_state_of']:
            if d['quest'] not in D.by_edid:
                err.append('%s prerequisite quest %s unknown' % (q['edid'], d['quest']))
    t = inv['totals']
    if t['stages'] != sum(len(q['stages']) for q in inv['quests']):
        err.append('totals.stages inconsistent')
    return err


def resolve_hex(D, hx):
    v = int(hx, 16)
    plugin = D.loadorder[v >> 24]
    return '%s:%06X' % (plugin, v & 0xFFFFFF)


def v_sweep(D, sw, inv, edids):
    err = []
    seen = {}
    swq = {q['edid']: q for q in sw['quests']}
    for e in edids:
        if e not in swq:
            err.append('quest %s missing from sweep' % e)
            continue
    excl = {(x['quest'], x['stage']): x for x in sw.get('excluded', [])}
    for x in sw.get('excluded', []):
        if not str(x.get('reason', '')).strip():
            err.append('excluded stage %s %s has no reason' % (x['quest'], x['stage']))
    for iq in inv['quests']:
        sq = swq.get(iq['edid'])
        if not sq:
            continue
        idx = [s['index'] for s in sq['stages']]
        if len(idx) != len(set(idx)):
            err.append('%s duplicate stages in sweep' % iq['edid'])
        for s in iq['stages']:
            if s['index'] not in idx:
                if (iq['edid'], s['index']) not in excl:
                    err.append('%s stage %d neither swept nor excluded' % (iq['edid'], s['index']))
        for s in sq['stages']:
            if s['index'] not in [x['index'] for x in iq['stages']]:
                err.append('%s sweep stage %d not in quest' % (iq['edid'], s['index']))
    reset = set(sw['reset_quests'])
    for e in edids:
        if '%X' % int(D.hexid(D.by_edid[e]['ref']), 16) not in reset:
            err.append('quest %s not in reset_quests' % e)
    # plan loader compatibility (plugin/snapshot/tools/goodsprings_sweep.load_plan)
    for q in sw['quests']:
        for k in ('edid', 'ref', 'stages', 'tier'):
            if k not in q:
                err.append('%s lacks plan key %s' % (q.get('edid'), k))
        for s in q['stages']:
            if 'index' not in s:
                err.append('%s stage without index' % q['edid'])
    # commands
    for q in sw['quests']:
        qsrc = D.by_edid.get(q['edid'])
        cmds = [(None, c) for c in q['setup']] + [(s['index'], c) for s in q['stages'] for c in s['setup']] + \
               [(s['index'], c) for s in q['stages'] for v in s['variants'] for c in v['setup']]
        for stage, c in cmds:
            text = c['cmd']
            m = next((rx.match(text) for rx in CMD_RX if rx.match(text)), None)
            if not m:
                err.append('%s: command not in the allowed grammar: %r' % (q['edid'], text))
                continue
            try:
                ref = resolve_hex(D, m.group(1))
            except Exception:
                err.append('%s: bad FormID in %r' % (q['edid'], text))
                continue
            if not D.resolves(ref):
                err.append('%s: FormID of %r does not resolve (%s)' % (q['edid'], text, ref))
                continue
            if text.startswith(('startquest', 'setstage', 'setobjective')) and ref not in D.by_ref:
                err.append('%s: %r targets a non-quest' % (q['edid'], text))
            if text.startswith('setstage') and ref in D.by_ref:
                n = int(m.group(2))
                if n not in [s['index'] for s in D.by_ref[ref]['stages']]:
                    err.append('%s: %r stage does not exist' % (q['edid'], text))
                if stage is not None and ref == qsrc['ref'] and n >= stage:
                    err.append('%s: prerequisite %r is not before stage %d' % (q['edid'], text, stage))
            if text.startswith('set ') and '.' in text.split(' ')[1]:
                names = {v['name'] for v in ((D.by_ref.get(ref) or {}).get('script_data') or {}).get('vars', [])}
                if m.group(2) not in names:
                    err.append('%s: quest var %r does not exist' % (q['edid'], text))
    return err


def v_gaps(D, g, edids, src_index, eff_names, hooks, appliers):
    err = []
    seen_status = {'supported', 'partial', 'missing', 'host-local', 'n/a'}
    for f in g['functions']:
        if f['status'] not in ('supported', 'partial', 'missing', 'host-local'):
            err.append('function %s bad status %s' % (f['fn'], f['status']))
        if f['capture'] not in seen_status or f['apply'] not in seen_status:
            err.append('function %s bad capture/apply' % f['fn'])
        if f['severity'] not in ('none', 'info', 'low', 'medium', 'high', 'critical'):
            err.append('function %s bad severity' % f['fn'])
        for tok in re.findall(r'[A-Z][A-Za-z]+', f['effect'] or ''):
            if tok not in eff_names and tok not in EFFECT_EXTRA:
                err.append('function %s names effect %s which is not in effect.h' % (f['fn'], tok))
        if f['kind'] == 'stmt' and f['capture'] == 'supported' and f['fn'] in HOOK_FOR:
            hk = HOOK_FOR[f['fn']]
            if hk is not None and hk not in hooks:
                err.append('function %s claims capture supported but hook %s is not in engine_addrs.h' % (f['fn'], hk))
        if f['kind'] == 'stmt' and f['apply'] == 'supported' and f['fn'] in APPLY_FOR:
            if APPLY_FOR[f['fn']] not in appliers:
                err.append('function %s claims apply supported but apply_remote.cpp has no get_if<%s>' % (f['fn'], APPLY_FOR[f['fn']]))
        if f['kind'] == 'stmt' and f['capture'] == 'supported' and f['fn'] not in HOOK_FOR:
            err.append('function %s claims capture supported with no verifiable hook mapping' % f['fn'])
        if f['status'] in ('missing', 'partial') and f['locations_total'] and not f['locations']:
            err.append('unsupported function %s lists no locations' % f['fn'])
        for loc in f['locations']:
            text = line_of(D, src_index, loc['script'], loc['line'])
            if text is None:
                err.append('%s: location %s:%s does not exist' % (f['fn'], loc['script'], loc['line']))
                continue
            low = text.lower()
            probe = {'setquestvar': 'set ', 'set': 'set'}.get(f['fn'], f['fn'].split(' ')[-1])
            if f['kind'] == 'block':
                if 'begin' not in low:
                    err.append('%s: %s:%s is not a begin line' % (f['fn'], loc['script'], loc['line']))
            elif probe not in low and not (f['kind'] == 'expr' and f['fn'] == 'player-ref' and 'player' in low):
                err.append('%s: line %s:%s does not contain the function: %r' % (f['fn'], loc['script'], loc['line'], text.strip()[:60]))
    if g['unmapped_functions']:
        err.append('unmapped functions: %s' % sorted(g['unmapped_functions']))
    for tok in g['parse_noise']:
        if tok in fnmap.TABLE:
            err.append('parse-noise token %s is a mapped function' % tok)
    ranks = [b['rank'] for b in g['blockers']]
    if ranks != list(range(1, len(ranks) + 1)):
        err.append('blocker ranks not sequential')
    order = [(fnmap.SEV_ORDER[b['severity']], len(b['quests']), b['occurrences']) for b in g['blockers']]
    if order != sorted(order, reverse=True):
        err.append('blockers not sorted by severity/reach/occurrences')
    for b in g['blockers']:
        for s in b['sample_locations']:
            if line_of(D, src_index, s['script'], s['line']) is None:
                err.append('blocker %s sample %s:%s does not exist' % (b['theme'], s['script'], s['line']))
        for q in b['quests']:
            if q not in D.by_edid:
                err.append('blocker %s names unknown quest %s' % (b['theme'], q))
    # accounting: per-status occurrences must add up to the per-function totals
    tot = sum(g['summary']['occurrences_by_status'].values())
    if tot != sum(f['locations_total'] for f in g['functions']):
        err.append('summary occurrences %d != sum of function occurrences' % tot)
    return err


def v_recount(D, g, units):
    """Independent recount: statement counts of a few functions straight from the sources vs gaps.json."""
    err = []
    by_fn = {f['fn']: f for f in g['functions'] if f['kind'] == 'stmt'}
    for fn in ('setstage', 'rewardxp', 'startquest', 'stopquest', 'completequest', 'rewardkarma'):
        rx = re.compile(r'^\s*(?:\w+\.)?%s\b' % fn, re.I)
        seen = set()
        for u in units.values():
            if not u['touched']:
                continue
            for n, raw in enumerate(u['sc']['source'].splitlines(), 1):
                code = raw.split(';')[0]
                if rx.match(code):
                    seen.add((u['script_edid'], n))
        got = by_fn[fn]['locations_total'] if fn in by_fn else 0
        if got != len(seen):
            err.append('recount %s: gaps.json has %d, independent scan finds %d' % (fn, got, len(seen)))
    return err


def v_patch(D, patch, edids):
    err = []
    base = D.rules['quests']
    kinds = set(policy_mod.KINDS)
    if patch['kinds_allowed'] != policy_mod.KINDS:
        err.append('kinds_allowed changed')
    for q, p in patch['quests'].items():
        if q not in base:
            err.append('patch quest %s not in rules.json' % q)
            continue
        if q not in edids:
            err.append('patch quest %s is outside the questline' % q)
        have = {r['kind'] for r in base[q]['rules']}
        for a in p['add']:
            if a['kind'] not in kinds:
                err.append('%s: add of unknown kind %s' % (q, a['kind']))
            if a['kind'] in have:
                err.append('%s: add of kind %s that already exists' % (q, a['kind']))
            if not a['evidence']:
                err.append('%s: add %s has no evidence' % (q, a['kind']))
            for ident in a['params'].get('markers', []) + a['params'].get('cells', []):
                if not D.ref_of_edid(ident):
                    err.append('%s: identifier %s does not resolve' % (q, ident))
        for m in p['modify']:
            if m['kind'] not in have:
                err.append('%s: modify of kind %s that does not exist in rules.json' % (q, m['kind']))
            if m['param'] in ('markers', 'cells'):
                for ident in m['add']:
                    if not D.ref_of_edid(ident):
                        err.append('%s: modify identifier %s does not resolve' % (q, ident))
    for nk in patch['new_kinds']:
        if nk['kind'] in kinds:
            err.append('new kind %s is not new' % nk['kind'])
        if not nk.get('why_not_existing') or not nk.get('evidence'):
            err.append('new kind %s lacks justification' % nk['kind'])
    return err


def v_dialogue(D, specs, dj, edids, dials):
    err = []
    for sid, sp in specs.items():
        if SPEC_KEYS - set(sp):
            err.append('%s lacks spec keys %s' % (sid, sorted(SPEC_KEYS - set(sp))))
        for q in sp['quests']:
            if q not in D.by_edid:
                err.append('%s: unknown quest %s' % (sid, q))
        for st in sp['steps']:
            if st['action'] not in SPEC_ACTIONS:
                err.append('%s: bad action %s' % (sid, st['action']))
            if st['action'] == 'pick':
                info = D.info_by_ref.get(st['info'])
                if info is None:
                    err.append('%s: INFO %s does not exist' % (sid, st['info']))
                    continue
                if info['topic'] != st['topic']:
                    err.append('%s: INFO %s is not under topic %s' % (sid, st['info'], st['topic']))
                d = dials.get(st['topic'])
                if not d or d['name'] != st['prompt'] or d['edid'] != st['topic_edid']:
                    err.append('%s: topic %s prompt/edid mismatch' % (sid, st['topic']))
                txt = next((r['text'] for r in info['responses'] if r.get('text')), '')
                if not txt.startswith(st['line']):
                    err.append('%s: line %r is not the INFO text' % (sid, st['line']))
            if st['action'] == 'talk':
                info = D.info_by_ref.get(st['greeting_info'])
                if info is None:
                    err.append('%s: greeting INFO %s does not exist' % (sid, st['greeting_info']))
                if not D.resolves(st['npc_ref']):
                    err.append('%s: npc_ref %s does not resolve' % (sid, st['npc_ref']))
    for c in dj['key_infos']:
        if c['ref'] not in D.info_by_ref:
            err.append('dialogue.json INFO %s does not exist' % c['ref'])
    if dj['summary']['key_infos'] != len(dj['key_infos']):
        err.append('dialogue.json key_infos count mismatch')
    return err


def v_docs(out):
    err = []
    for name in OUTPUT_FILES:
        if not out.get(name):
            err.append('%s empty' % name)
    pr = out.get('policy_review.md', '')
    for sect in ('## 2. Owner decisions', '## 4. Barriers and tethers', '## 5. Who controls cinematics', '## 6. Reward routing'):
        if sect not in pr:
            err.append('policy_review.md lacks section %s' % sect)
    dm = out.get('dialogue.md', '')
    if '## 2. Default model and where it breaks' not in dm or '## 4. Scenario specs' not in dm:
        err.append('dialogue.md lacks sections')
    gm = out.get('gaps.md', '')
    if '## Unsupported functions' not in gm:
        err.append('gaps.md lacks the unsupported-function section')
    return err


# ---------------------------------------------------------------------------------------------------------------------
class Runner:
    def __init__(self):
        self.fail = []
        self.n = 0

    def check(self, name, errors):
        self.n += 1
        if errors:
            self.fail.append((name, errors))
            print('FAIL  %s' % name)
            for e in errors[:8]:
                print('        ' + e)
            if len(errors) > 8:
                print('        ... %d more' % (len(errors) - 8))
        else:
            print('ok    %s' % name)

    def mutant(self, name, errors, expect=None):
        """A mutation twin passes when the validator REPORTS errors (optionally containing `expect`)."""
        self.n += 1
        good = bool(errors) and (expect is None or any(expect in e for e in errors))
        if good:
            print('ok    mutant caught: %s (%d errors)' % (name, len(errors)))
        else:
            self.fail.append(('mutant ' + name, ['validator did not catch the mutation' + (' (expected %r)' % expect if expect else '')]))
            print('FAIL  mutant NOT caught: %s' % name)


def main():
    t0 = time.time()
    R = Runner()
    tmp = tempfile.mkdtemp(prefix='act2_pin_')
    try:
        pin_inputs(tmp)
        D = lib.Data(tmp)
        edids = D.questline_edids()
        pol = sorted(p['edid'] for p in D.policies if p.get('questline') == lib.QUESTLINE)
        R.check('questline membership: 14 quests of "%s", rules.json == policies.json' % lib.QUESTLINE,
                [] if (len(edids) == 14 and edids == pol) else ['questline set changed: rules %s policies %s' % (edids, pol)])

        # ---- build twice from the pinned inputs: deterministic ------------------------------------------------------------
        out1, g1 = build.generate(D)
        out2, _ = build.generate(lib.Data(tmp))
        R.check('generators are deterministic (two runs, byte-identical)',
                [k for k in out1 if json.dumps(out1[k], sort_keys=True) != json.dumps(out2[k], sort_keys=True)])

        # ---- compare with the committed files ---------------------------------------------------------------------------
        disk = {}
        missing = []
        for name in list(out1):
            p = HERE / name
            if not p.exists():
                missing.append(name)
                continue
            disk[name] = read_json(p) if name.endswith('.json') else p.read_text(encoding='utf-8')
        R.check('all deliverables exist on disk', missing)
        stale = [k for k in disk if k.endswith('.json') and isinstance(disk[k], dict) and disk[k].get('inputs') and
                 disk[k]['inputs'] != D.input_hashes]
        if stale:
            print('WARN  committed outputs were built from different inputs (%s ...): regenerate with build.py. '
                  'Equality check skipped; validators still run against the committed files.' % ', '.join(stale[:3]))
        else:
            diff = [k for k in out1 if k in disk and (json.dumps(out1[k], sort_keys=True) != json.dumps(disk[k], sort_keys=True)
                                                       if k.endswith('.json') else out1[k] != disk[k])]
            R.check('committed outputs equal a fresh rebuild from the pinned inputs', diff)
        status_doc = REPO / 'docs' / 'questlines' / 'act2.md'
        R.check('status doc internal questline notes exists', [] if status_doc.exists() else ['missing'])

        # ---- validators on committed outputs ------------------------------------------------------------------------------
        eff_names, hooks, appliers = effect_names(), hook_names(), applier_types()
        src_idx = script_index(D, edids)
        units = gaps_mod.collect_units(D, edids)
        dials = dlg_mod.load_dials(Path(tmp))
        specs = {}
        for p in sorted((HERE / 'dialogue_specs').glob('dlg_act2_*.json')):
            specs[p.stem] = read_json(p)
        R.check('dialogue_specs on disk == generated', [] if sorted(specs) == sorted(k.split('/')[1][:-5] for k in out1
                                                                                    if k.startswith('dialogue_specs/dlg_')) else ['spec set differs'])
        inv, sw, g, patch, dj = (disk[k] for k in ('inventory.json', 'sweep.json', 'gaps.json', 'rules.patch.json', 'dialogue.json'))
        R.check('inventory: quests, stages, objectives, tiers, targets resolve', v_inventory(D, inv, edids))
        R.check('sweep: every stage swept or excluded with a reason; setup commands valid', v_sweep(D, sw, inv, edids))
        R.check('gaps: locations real, statuses backed by hooks/appliers/effect.h', v_gaps(D, g, edids, src_idx, eff_names, hooks, appliers))
        R.check('gaps: independent recount of key functions', v_recount(D, g, units))
        R.check('rules.patch: keys exist, kinds valid, identifiers resolve, evidence present', v_patch(D, patch, edids))
        R.check('dialogue: specs and key INFOs match the ESM data', v_dialogue(D, specs, dj, edids, dials))
        docs = dict(disk)
        R.check('docs: sections present', v_docs(docs))
        R.check('gaps: no unmapped function and every touched statement is mapped', [] if not g['unmapped_functions'] else ['unmapped'])
        R.check('inventory: totals as reported', [] if inv['totals']['quests'] == 14 else ['quest count != 14'])

        # ---- mutation twins ------------------------------------------------------------------------------------------------
        m = copy.deepcopy(inv)
        del m['quests'][3]['stages'][0]
        R.mutant('inventory loses a stage', v_inventory(D, m, edids), 'stage list differs')
        m = copy.deepcopy(inv)
        m['quests'][0]['formid'] = '00000000'
        R.mutant('inventory corrupt FormID', v_inventory(D, m, edids), 'formid mismatch')
        m = copy.deepcopy(inv)
        qo = next(q for q in m['quests'] if q['objectives'])
        qo['objectives'][0]['targets'] = [{'ref': 'FalloutNV.esm:FFFFFF', 'resolved': False, 'edid': None, 'sig': None}]
        R.mutant('inventory invented target ref', v_inventory(D, m, edids), 'does not resolve')
        m = copy.deepcopy(inv)
        m['quests'] = m['quests'][:-1]
        R.mutant('inventory drops a quest', v_inventory(D, m, edids), 'inventory quests')

        m = copy.deepcopy(sw)
        del m['quests'][2]['stages'][0]
        R.mutant('sweep drops a stage', v_sweep(D, m, inv, edids), 'neither swept nor excluded')
        m = copy.deepcopy(sw)
        m['excluded'] = [{'quest': m['quests'][0]['edid'], 'stage': m['quests'][0]['stages'][0]['index'], 'reason': ''}]
        R.mutant('sweep exclusion without reason', v_sweep(D, m, inv, edids), 'no reason')
        m = copy.deepcopy(sw)
        m['quests'][0]['setup'].append({'cmd': 'setstage FFFFFF 10', 'op': 'setstage', 'why': 'x', 'verify': False})
        R.mutant('sweep invented FormID', v_sweep(D, m, inv, edids), 'does not resolve')
        m = copy.deepcopy(sw)
        m['quests'][0]['setup'].append({'cmd': 'coc Goodsprings', 'op': 'coc', 'why': 'x', 'verify': False})
        R.mutant('sweep non-whitelisted command', v_sweep(D, m, inv, edids), 'allowed grammar')
        m = copy.deepcopy(sw)
        m['reset_quests'] = m['reset_quests'][1:]
        R.mutant('sweep reset list loses a quest', v_sweep(D, m, inv, edids), 'reset_quests')
        m = copy.deepcopy(sw)
        q = next(x for x in m['quests'] if any(s['setup'] for s in x['stages']))
        st = next(s for s in q['stages'] if s['setup'])
        st['setup'][0]['cmd'] = st['setup'][0]['cmd'].rsplit(' ', 1)[0] + ' %d' % (st['index'] + 1)
        R.mutant('sweep prerequisite stage after the swept stage', v_sweep(D, m, inv, edids))

        m = copy.deepcopy(g)
        loc = next(f for f in m['functions'] if f['locations'])['locations'][0]
        loc['line'] = 99999
        R.mutant('gaps location past end of script', v_gaps(D, m, edids, src_idx, eff_names, hooks, appliers), 'does not exist')
        m = copy.deepcopy(g)
        f = next(f for f in m['functions'] if f['fn'] == 'lock')
        f['capture'], f['apply'], f['status'] = 'supported', 'supported', 'supported'
        R.mutant('gaps claims supported capture for an unhooked function', v_gaps(D, m, edids, src_idx, eff_names, hooks, appliers),
                 'no verifiable hook mapping')
        m = copy.deepcopy(g)
        f = next(f for f in m['functions'] if f['fn'] == 'setstage')
        f['effect'] = 'InventedEffect'
        R.mutant('gaps names an effect that is not in effect.h', v_gaps(D, m, edids, src_idx, eff_names, hooks, appliers), 'effect.h')
        m = copy.deepcopy(g)
        f = next(f for f in m['functions'] if f['fn'] == 'completeallobjectives')
        f['capture'] = f['apply'] = 'supported'
        f['status'] = 'supported'
        # completeallobjectives has no hook mapping -> must be rejected
        R.mutant('gaps upgrades an unsupported effect to supported', v_gaps(D, m, edids, src_idx, eff_names, hooks, appliers),
                 'no verifiable hook mapping')
        m = copy.deepcopy(g)
        m['functions'] = [f for f in m['functions'] if f['fn'] != 'rewardxp']
        R.mutant('gaps drops a function that the scripts use (recount)', v_recount(D, m, units), 'rewardxp')
        m = copy.deepcopy(g)
        m['blockers'][0], m['blockers'][-1] = m['blockers'][-1], m['blockers'][0]
        R.mutant('gaps blockers mis-ranked', v_gaps(D, m, edids, src_idx, eff_names, hooks, appliers), 'rank')
        m = copy.deepcopy(g)
        m['unmapped_functions'] = {'fooBar': [{'script': 'x', 'line': 1}]}
        R.mutant('gaps leaves a function unmapped', v_gaps(D, m, edids, src_idx, eff_names, hooks, appliers), 'unmapped')

        m = copy.deepcopy(patch)
        m['quests']['NotAQuest'] = {'add': [], 'modify': []}
        R.mutant('patch names a quest that is not in rules.json', v_patch(D, m, edids), 'not in rules.json')
        m = copy.deepcopy(patch)
        q0 = next(iter(m['quests']))
        m['quests'][q0]['add'].append({'kind': 'TeleportEverything', 'params': {}, 'evidence': [{'script': 'x', 'line': 1}]})
        R.mutant('patch adds an unknown rule kind', v_patch(D, m, edids), 'unknown kind')
        m = copy.deepcopy(patch)
        m['quests']['VMQ01']['modify'].append({'kind': 'PartyWarp', 'param': 'markers', 'add': ['Lucky38BasementMarker']})
        R.mutant('patch modifies a rule that does not exist', v_patch(D, m, edids), 'does not exist in rules.json')
        m = copy.deepcopy(patch)
        qq = next(q for q, p in m['quests'].items() if p['add'])
        m['quests'][qq]['add'][0]['evidence'] = []
        R.mutant('patch add without evidence', v_patch(D, m, edids), 'no evidence')
        m = copy.deepcopy(patch)
        m['quests']['VDialogueLucky38Entrance']['add'].append({'kind': 'PartyWarp', 'params': {'markers': ['NoSuchMarkerXYZ']},
                                                                'evidence': [{'script': 'x', 'line': 1}]})
        R.mutant('patch invented marker EditorID', v_patch(D, m, edids), 'does not resolve')
        m = copy.deepcopy(patch)
        m['new_kinds'].append({'kind': 'PartyTether', 'why_not_existing': 'x', 'evidence': 'x'})
        R.mutant('patch proposes an existing kind as new', v_patch(D, m, edids), 'is not new')

        m = copy.deepcopy(specs)
        sid = sorted(m)[0]
        pk = next(s for s in m[sid]['steps'] if s['action'] == 'pick')
        pk['info'] = 'FalloutNV.esm:FFFFFF'
        R.mutant('dialogue spec INFO does not exist', v_dialogue(D, m, dj, edids, dials), 'does not exist')
        m = copy.deepcopy(specs)
        pk = next(s for s in m[sid]['steps'] if s['action'] == 'pick')
        pk['topic'] = 'FalloutNV.esm:0000C8'
        R.mutant('dialogue spec topic mismatch', v_dialogue(D, m, dj, edids, dials), 'not under topic')
        m = copy.deepcopy(specs)
        pk = next(s for s in m[sid]['steps'] if s['action'] == 'pick')
        pk['line'] = 'this is not what the character says'
        R.mutant('dialogue spec wrong line', v_dialogue(D, m, dj, edids, dials), 'not the INFO text')

        d2 = dict(disk)
        d2['policy_review.md'] = ''
        R.mutant('docs: empty policy review', v_docs(d2), 'empty')

        # generator mutation: a broken fnmap (everything supported) must make the *validators* fail on regenerated output
        saved = dict(fnmap.TABLE)
        try:
            for k in list(fnmap.TABLE):
                fnmap.TABLE[k] = dict(fnmap.TABLE[k], capture='supported', apply='supported')
            bad_out, bad_g = build.generate(D)
            R.mutant('generator twin: fnmap claims everything supported',
                     v_gaps(D, bad_out['gaps.json'], edids, src_idx, eff_names, hooks, appliers))
        finally:
            fnmap.TABLE.clear()
            fnmap.TABLE.update(saved)

        # generator mutation: sweep drops stage prerequisites
        saved_chain = sweep_mod.objective_chain
        try:
            sweep_mod.objective_chain = lambda q, idx, depth=0, seen=None: [idx]       # bogus: a stage is its own prerequisite
            bad_out, _ = build.generate(D)
            R.mutant('generator twin: bogus objective chain', v_sweep(D, bad_out['sweep.json'], bad_out['inventory.json'], edids),
                     'not before stage')
        finally:
            sweep_mod.objective_chain = saved_chain
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print('%d checks, %d failed, %.1fs' % (R.n, len(R.fail), time.time() - t0))
    if R.fail:
        print('FAILED')
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
