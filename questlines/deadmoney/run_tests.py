"""Tests for the Dead Money questline artefacts.  One command:

    python -I questlines/deadmoney/run_tests.py          -> prints ALL OK, exits 0

Regenerates every artefact into a temp dir, checks the committed files are identical (no stale output), then validates all
deliverables against the quest data (IDs resolve, every stage is swept or excluded with a reason, gap locations point at the real
script text, L5 capture/apply claims match the hook sources, patch keys exist, dialogue INFOs exist) and finally runs the MUTATION
TWIN: each check is run against a deliberately broken copy and must fail.
"""
import copy
import json
import shutil
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build  # noqa: E402
import dialogue_build  # noqa: E402
import dmlib  # noqa: E402
import fnmap  # noqa: E402
import hazards  # noqa: E402
import prereq  # noqa: E402

REPO = dmlib.REPO
ARTIFACTS = ['inventory.json', 'sweep.json', 'gaps.json', 'gaps.md', 'dialogue.md', 'dialogue.specs.json']
EXISTING_KINDS = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner',
                  'SaveSuppress', 'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner',
                  'ShareKarmaRep', 'InitiatorDialogue', 'ExcludeFromGate']
KNOWN_FUNCS = {'GetDistance', 'GetInCell', 'GetInSameCell', 'IsActionRef', 'IsKiller', 'GetAV', 'GetEquipped', 'GetInWorldspace'}
# effect types the L3 static oracle emits that effect.h does not (yet) have: tools/questdata/effects.py ORACLE_ONLY
ORACLE_ONLY = {'ClearObjective', 'AddPerk'}
STATUS = {'supported', 'partial', 'missing', 'n/a'}
SEVS = {'blocker', 'high', 'medium', 'low', 'info'}


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def pin():
    """Pinned L4/L5 facts (refresh_l5.py). Tests read ONLY this, never the live sources another lane may be rewriting (CONTRACTS)."""
    return json.load(open(os.path.join(HERE, 'l5_snapshot.json'), encoding='utf-8'))


def effect_names():
    return set(pin()['effect_names'])


def hook_sets():
    p = pin()
    return set(p['captured_hooks']), set(p['block_only_hooks'])


def applied_effects():
    return set(pin()['applied_effects'])


def drift_note():
    """Non-failing: names the live L4/L5 sources that moved since the pin."""
    import refresh_l5
    try:
        live = refresh_l5.digests()
    except OSError:
        return []
    old = pin()['sources']
    return sorted(k for k in live if live[k] != old.get(k))


# fn (lower) -> how the L5 sources must classify it: 'captured' | 'block-only' | 'none'
CAPTURE_EXPECT = {
    'setstage': ('captured', 'SetStage'), 'setobjectivedisplayed': ('captured', 'ObjectiveSetState'),
    'setobjectivecompleted': ('captured', 'ObjectiveSetState'), 'startquest': ('captured', 'QuestSetRunning'),
    'stopquest': ('captured', 'QuestSetRunning'), 'enable': ('captured', 'Cmd_Enable'), 'disable': ('captured', 'Cmd_Disable'),
    'additem': ('captured', 'Cmd_AddItem'), 'removeitem': ('captured', 'Cmd_RemoveItem'), 'rewardxp': ('captured', 'Cmd_RewardXP'),
    'rewardkarma': ('captured', 'PlayerRewardKarma'), 'killactor': ('captured', 'Cmd_KillActor'), 'kill': ('captured', 'Cmd_KillActor'),
    'resurrectactor': ('captured', 'Cmd_ResurrectActor'), 'movetomarker': ('captured', 'Cmd_MoveToMarker'),
    'movetomarkerwithfade': ('captured', 'Cmd_MoveToMarkerWithFade'), 'setpos': ('captured', 'Cmd_SetPos'),
    'modactorvalue': ('captured', 'Cmd_ModActorValue'), 'setactorvalue': ('captured', 'Cmd_SetActorValue'),
    'forceactorvalue': ('captured', 'Cmd_ForceActorValue'), 'damageactorvalue': ('captured', 'Cmd_DamageActorValue'),
    'addperk': ('captured', 'Cmd_AddPerk'), 'removeperk': ('captured', 'Cmd_RemovePerk'),
    'addtofaction': ('captured', 'Cmd_AddToFaction'), 'removefromfaction': ('captured', 'Cmd_RemoveFromFaction'),
    'setfactionrank': ('captured', 'Cmd_SetFactionRank'), 'lock': ('captured', 'Cmd_Lock'), 'unlock': ('captured', 'Cmd_UnLock'),
    'setopenstate': ('captured', 'Cmd_SetOpenState'), 'setdestroyed': ('captured', 'Cmd_SetDestroyed'),
    'addnote': ('captured', 'Cmd_AddNote'), 'removenote': ('captured', 'Cmd_RemoveNote'), 'setweather': ('captured', 'Cmd_SetWeather'),
    'autosave': ('captured', 'Cmd_Autosave'), 'removeallitems': ('captured', 'Cmd_RemoveAllItems'),
    'placeatme': ('captured', 'Cmd_PlaceAtMe'), 'disableplayercontrols': ('captured', 'PlayerSetControlsDisabled'),
    'enableplayercontrols': ('captured', 'PlayerSetControlsDisabled'), 'completeallobjectives': ('captured', 'QuestCompleteAllObjectives'),
    # hooked but only to DROP the call on a guest: nothing is recorded
    'activate': ('block-only', 'Cmd_Activate'), 'addscriptpackage': ('block-only', 'Cmd_AddScriptPackage'),
    'removescriptpackage': ('block-only', 'Cmd_RemoveScriptPackage'), 'startcombat': ('block-only', 'Cmd_StartCombat'),
    'stopcombat': ('block-only', 'Cmd_StopCombat'), 'modfactionrank': ('block-only', 'Cmd_ModFactionRank'),
    # no hook at all (the HookSpec name must be absent from the HK table)
    'setplayerteammate': ('none', 'Cmd_SetPlayerTeammate'), 'equipitem': ('none', 'Cmd_EquipItem'),
    'removealltypeditems': ('none', 'Cmd_RemoveAllTypedItems'), 'restoreactorvalue': ('none', 'Cmd_RestoreActorValue'),
    'resethealth': ('none', 'Cmd_ResetHealth'), 'showmessage': ('none', 'Cmd_ShowMessage'), 'sayto': ('none', 'Cmd_SayTo'),
    'say': ('none', 'Cmd_Say'), 'forceactivequest': ('none', 'Cmd_ForceActiveQuest'), 'showmap': ('none', 'Cmd_ShowMap'),
    'swaptexture': ('none', 'Cmd_SwapTexture'), 'setinchargen': ('none', 'Cmd_SetInChargen'),
}


class Art:
    """All artefacts + the quest data, loaded once; checks take an Art so mutants can swap pieces."""

    def __init__(self, dirpath):
        j = lambda n: json.load(open(os.path.join(dirpath, n), encoding='utf-8'))  # noqa: E731
        self.dir = dirpath
        self.inv = j('inventory.json')
        self.sweep = j('sweep.json')
        self.gaps = j('gaps.json')
        self.dlg = j('dialogue.specs.json')
        self.patch = json.load(open(os.path.join(HERE, 'rules.patch.json'), encoding='utf-8'))
        self.gaps_md = read(os.path.join(dirpath, 'gaps.md'))
        self.dlg_md = read(os.path.join(dirpath, 'dialogue.md'))
        self.policy_md = read(os.path.join(HERE, 'policy_review.md'))
        self.hazards = hazards.H


_DATA = {}


def data():
    if 'd' not in _DATA:
        d = dmlib.Data()
        _DATA['d'] = d
        _DATA['scripts'] = {s.label: s for s in dmlib.universe(d)}
        _DATA['rules'] = json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))['quests']
    return _DATA['d']


def scripts():
    data()
    return _DATA['scripts']


def resolves(name):
    d = data()
    return name.lower() in d.edid_index()


# ---------------------------------------------------------------------------------------------------------------------------
# checks: each returns a list of error strings (empty = pass)
# ---------------------------------------------------------------------------------------------------------------------------
def check_inventory(a):
    d = data()
    e = []
    inv = a.inv
    edids = {q['edid'] for q in inv['quests']}
    if edids != {q['edid'] for q in d.quests}:
        e.append('inventory quest set differs from DeadMoney.esm QUST set: %s' % sorted(edids ^ {q['edid'] for q in d.quests}))
    for q in inv['quests']:
        raw = d.by_edid.get(q['edid'])
        if raw is None:
            continue
        if q['formid'] != dmlib.runtime_id(raw['ref']) or not q['formid'].startswith('01') or len(q['formid']) != 8:
            e.append('%s: bad formid %s' % (q['edid'], q['formid']))
        if [s['index'] for s in q['stages']] != sorted(s['index'] for s in raw['stages']):
            e.append('%s: stage list differs from the ESM' % q['edid'])
        if sorted(o['index'] for o in q['objectives']) != sorted(o['index'] for o in raw['objectives']):
            e.append('%s: objective list differs from the ESM' % q['edid'])
        for o in q['objectives']:
            for t in o['targets']:
                if t['ref'] and not d.known_ref(t['ref']):
                    e.append('%s obj %d: target %s unresolved' % (q['edid'], o['index'], t['ref']))
        for k in ('tier', 'tier_direct', 'risk_score'):
            if q.get(k) is None:
                e.append('%s: missing %s' % (q['edid'], k))
        if q['tier'] != d.risk[raw['ref']]['tier']:
            e.append('%s: tier %s != risk.json' % (q['edid'], q['tier']))
        for k in ('own_infos', 'own_topics', 'related_infos_by_condition', 'related_infos_by_script'):
            if k not in q['dialogue']:
                e.append('%s: dialogue.%s missing' % (q['edid'], k))
        for p in q['prerequisites']['quests']:
            if p not in edids:
                e.append('%s: prerequisite quest %s unknown' % (q['edid'], p))
        for s in q['stages']:
            for t in s['triggered_by']:
                if t['by'] not in scripts():
                    e.append('%s stage %d: trigger %s is not a Dead Money script' % (q['edid'], s['index'], t['by']))
                else:
                    src = scripts()[t['by']].source.splitlines()
                    if t['line'] > len(src) or 'setstage' not in src[t['line'] - 1].lower():
                        e.append('%s stage %d: trigger %s:%d is not a SetStage line' % (q['edid'], s['index'], t['by'], t['line']))
    if inv['summary']['stages'] != sum(len(q['stages']) for q in d.quests):
        e.append('stage total wrong')
    if inv['summary']['quests'] != len(d.quests):
        e.append('quest total wrong')
    return e


def script_vars(edid):
    d = data()
    q = d.by_edid.get(edid)
    if not q or not q.get('script_data'):
        return set()
    return {v['name'].lower() for v in q['script_data'].get('vars', [])}


def check_sweep(a):
    d = data()
    e = []
    sw = a.sweep
    qids = {q['edid']: dmlib.runtime_id(q['ref']) for q in d.quests}
    swept = {}
    for q in sw['quests']:
        raw = d.by_edid.get(q['edid'])
        if raw is None:
            e.append('sweep quest %s not in DeadMoney.esm' % q['edid'])
            continue
        if q['formid'] != qids[q['edid']] or q['ref'] != raw['ref']:
            e.append('%s: formid/ref mismatch' % q['edid'])
        for s in q['stages']:
            key = (q['edid'], s['index'])
            if key in swept:
                e.append('duplicate stage %s' % (key,))
            swept[key] = s
            if s['sweep'] == 'exclude' and len(s.get('exclude_reason', '')) < 20:
                e.append('%s stage %d excluded without a real reason' % key)
            if s['sweep'] not in ('run', 'exclude'):
                e.append('%s stage %d: bad sweep value' % key)
            for c in s['setup'] + (q['setup'] if s['sweep'] == 'run' else []):
                e += check_cmd(a, c, qids, d, q['edid'], s['index'])
        for c in q['setup']:
            e += check_cmd(a, c, qids, d, q['edid'], None)
    expected = {(q['edid'], s['index']) for q in d.quests for s in q['stages']}
    if set(swept) != expected:
        e.append('stages not covered by the sweep (neither run nor excluded): %s; unknown: %s'
                 % (sorted(expected - set(swept))[:5], sorted(set(swept) - expected)[:5]))
    if sw['summary']['stages_run'] + sw['summary']['stages_excluded'] != len(expected):
        e.append('summary counts do not add up')
    if sw['summary']['stages_excluded'] != len(sw['excluded']):
        e.append('excluded list length differs from summary')
    for x in sw['reset_ids']:
        if x not in qids.values():
            e.append('reset id %s is not a Dead Money quest' % x)
    if set(sw['order']) - set(qids):
        e.append('order lists unknown quests')
    ef = effect_names()
    for p in sw['event_probes']:
        if p.get('call_ref') and not resolves(p['call_ref']):
            e.append('probe %s: call_ref %s unresolved' % (p['id'], p['call_ref']))
        if p.get('script') and p['script'] not in {s.label for s in scripts().values()}:
            e.append('probe %s: script %s unknown' % (p['id'], p['script']))
        for fx in p['expect_effects']:
            if fx not in ef:
                e.append('probe %s: %s is not a synccore effect' % (p['id'], fx))
        for v in p.get('vars', []):
            if v['var'].lower() not in script_vars(v['quest']):
                e.append('probe %s: variable %s.%s is not declared' % (p['id'], v['quest'], v['var']))
        if p.get('cmd_hex') and re.search(r'[A-Za-z_]{4,}\b', p['cmd_hex'].split(' ', 1)[0]) is None and False:
            pass
    return e


def check_cmd(a, c, qids, d, where_q, where_s):
    e = []
    if c['verb'] not in prereq.ALLOWED_VERBS:
        e.append('%s: verb %s not an FNV/xNVSE console command we allow' % (where_q, c['verb']))
    if c['quest'] not in qids:
        return e + ['%s: setup quest %s unknown' % (where_q, c['quest'])]
    hexid = qids[c['quest']]
    if hexid not in c.get('cmd', ''):
        e.append('%s: command %r does not use the hex FormID %s' % (where_q, c.get('cmd'), hexid))
    if c['verb'] == 'setstage':
        if c['stage'] not in {s['index'] for s in d.by_edid[c['quest']]['stages']}:
            e.append('%s: setstage %s %s: no such stage' % (where_q, c['quest'], c['stage']))
        if where_s is not None and c['quest'] == where_q and c['stage'] == where_s:
            e.append('%s stage %s: setup sets the stage under test' % (where_q, where_s))
    if c['verb'] == 'set':
        if c['var'].lower() not in script_vars(c['quest']):
            e.append('%s: set %s.%s: variable not declared in the quest script' % (where_q, c['quest'], c['var']))
        if c.get('verified') is not False:
            e.append('%s: quest-variable set must carry verified=false (console syntax unverified)' % where_q)
    return e


def check_gaps(a):
    d = data()
    e = []
    g = a.gaps
    if g['summary']['unclassified_functions']:
        e.append('unclassified functions: %s' % g['summary']['unclassified_functions'])
    if g['summary']['unknown_calls']:
        e.append('unknown statement-leading calls: %d' % g['summary']['unknown_calls'])
    ef = effect_names()
    sc = scripts()
    for r in g['functions']:
        if r['status'] not in STATUS or r['severity'] not in SEVS or r['capture'] not in STATUS or r['apply'] not in STATUS:
            e.append('%s: bad status/severity vocabulary' % r['fn'])
        for part in r['effect'].split('|'):
            part = part.strip()
            if part != '-' and part not in ef and part not in ('SetGlobal', 'SetScriptVar'):
                e.append('%s: effect %s is not in effect.h' % (r['fn'], part))
        if r['status'] in ('partial', 'missing') and r['kind'] != 'condition':
            if not r['locations_complete'] or len(r['locations']) != r['count']:
                e.append('%s: %d unsupported sites but %d locations' % (r['fn'], r['count'], len(r['locations'])))
        for l in r['locations']:
            s = sc.get(l['script'])
            if s is None:
                e.append('%s: location script %s unknown' % (r['fn'], l['script']))
                continue
            lines = s.source.splitlines()
            if not (1 <= l['line'] <= len(lines)):
                e.append('%s: %s:%d out of range' % (r['fn'], l['script'], l['line']))
                continue
            real = dmlib.classify.strip_comment(lines[l['line'] - 1]).strip()[:160]
            if real != l['text']:
                e.append('%s: %s:%d text does not match the script' % (r['fn'], l['script'], l['line']))
            full = dmlib.classify.strip_comment(lines[l['line'] - 1]).lower()
            aliases = {alias for alias, canon in dmlib.ALIASES.items() if canon.lower() == r['fn'].lower()}
            if r['fn'] not in ('OnPlayerEvent', 'SetQuestVar', 'SetRefVar', 'SetLocalVar', 'SetGlobal') and                     r['fn'].lower() not in full and not any(al in full for al in aliases):
                e.append('%s: line %s:%d does not contain the function' % (r['fn'], l['script'], l['line']))
    tot = sum(g['summary']['by_status'].values())
    if tot != g['summary']['statements']:
        e.append('by_status does not add up to statements')
    if sum(r['count'] for r in g['functions']) != g['summary']['statements']:
        e.append('per-function counts do not add up to statements')
    # L5 claims versus the PINNED hook / applier facts (l5_snapshot.json)
    captured, blockonly = hook_sets()
    applied = applied_effects()
    by_fn = {r['fn'].lower(): r for r in g['functions']}
    for fn, (cls, spec) in CAPTURE_EXPECT.items():
        r = by_fn.get(fn)
        actual = 'captured' if spec in captured else 'block-only' if spec in blockonly else 'none'
        if actual != cls:
            e.append('pinned L5 facts disagree with the expectation table: %s is %s (table says %s)' % (spec, actual, cls))
        if r is None:
            continue
        if cls == 'captured' and r['capture'] == 'missing':
            e.append('%s: hooked in L5 but row says capture missing' % r['fn'])
        if cls in ('block-only', 'none') and r['capture'] == 'supported':
            e.append('%s: not captured by L5 but row says capture supported' % r['fn'])
    for r in g['functions']:
        for part in (x.strip() for x in r['effect'].split('|')):
            if part in ('-', ''):
                continue
            if r['apply'] == 'supported' and part not in applied:
                e.append('%s: apply claimed supported but RemoteApplier has no branch for %s' % (r['fn'], part))
            if r['apply'] == 'missing' and part in applied and r['fn'] not in ('RestoreActorValue', 'AddItemHealthPercent'):
                e.append('%s: apply claimed missing but RemoteApplier now handles %s' % (r['fn'], part))
    return e


def check_hazards(a):
    e = []
    seen = set()
    sc = scripts()
    for h in a.gaps['hazards']:
        if h['id'] in seen:
            e.append('duplicate hazard id %s' % h['id'])
        seen.add(h['id'])
        if h['sev'] not in ('blocker', 'high', 'medium', 'low'):
            e.append('%s: bad severity' % h['id'])
        if not h['script_found'] or not h['resolved']:
            e.append('%s: pattern %r does not match script %s' % (h['id'], h['rx'], h['script']))
            continue
        s = sc.get(h['script'])
        if s is None:
            # case-insensitive label match as in build.py
            s = next((v for k, v in sc.items() if k.lower() == h['script'].lower()), None)
        if s is None:
            e.append('%s: script %s unknown' % (h['id'], h['script']))
            continue
        lines = s.source.splitlines()
        for r in h['resolved']:
            real = dmlib.classify.strip_comment(lines[r['line'] - 1]).strip()[:160]
            if real != r['text'] or not re.search(h['rx'], real, re.I):
                e.append('%s: resolved line %d does not match' % (h['id'], r['line']))
        if not h['why'] or not h['handling']:
            e.append('%s: missing why/handling' % h['id'])
    # every blocker is discussed in the policy review
    for h in a.gaps['hazards']:
        if h['sev'] == 'blocker' and h['id'] not in a.policy_md:
            e.append('blocker hazard %s is not discussed in policy_review.md' % h['id'])
    return e


def check_patch(a):
    d = data()
    rules = _DATA['rules']
    e = []
    p = a.patch
    dm = {q['edid']: q for q in d.quests}
    ext = {k for k in p.get('funcs_vocabulary_extension', {}) if k != 'doc'}
    prop = {x['kind'] for x in p.get('proposed_kinds', [])}
    if prop & set(EXISTING_KINDS):
        e.append('proposed kind duplicates an existing kind')
    for x in p.get('proposed_kinds', []):
        if len(x.get('justification', '')) < 200:
            e.append('proposed kind %s has no real justification' % x['kind'])
        for script, vs in x.get('shard_vars', {}).items():
            s = next((v for v in scripts().values() if v.label == script), None)
            if s is None:
                e.append('shard_vars: script %s unknown' % script)
                continue
            declared = dmlib.local_vars(s.source)
            for v in vs:
                if v.lower() not in declared:
                    e.append('shard_vars: %s.%s is not declared' % (script, v))

    def chk_rule(owner, r):
        out = []
        k = r['kind']
        if k not in EXISTING_KINDS:
            return ['%s: kind %s is not one of the 16 existing kinds' % (owner, k)]
        pr = r['params']
        if k == 'PartyWarp':
            for m in pr.get('markers', []):
                if not resolves(m):
                    out.append('%s: PartyWarp marker %s does not resolve' % (owner, m))
            if pr.get('mode') != 'slot-offset' or pr.get('absent') not in ('hold', 'warp'):
                out.append('%s: bad PartyWarp params' % owner)
        if k == 'PartyTether':
            for c in pr.get('cells', []):
                ref = d.edid_index().get(c.lower())
                sig = (d.forms.get(ref) or d.refnames.get(ref) or {}).get('sig') if ref else None
                if sig not in ('CELL', 'WRLD'):
                    out.append('%s: PartyTether cell %s is not a CELL/WRLD (%s)' % (owner, c, sig))
            if pr.get('gather') not in ('auto-warp', 'wait'):
                out.append('%s: bad gather' % owner)
        if k == 'SetPlayerControls' and (pr.get('scope') != 'all' or pr.get('release') != 'host-effect' or not isinstance(pr.get('timeout_s'), int)):
            out.append('%s: bad SetPlayerControls params' % owner)
        if k == 'Barrier' and pr.get('step') != 'all-players-pass':
            out.append('%s: bad Barrier' % owner)
        if k == 'CompanionOwner' and pr.get('owner') != 'recruiter':
            out.append('%s: CompanionOwner must be recruiter (owner decision)' % owner)
        if k == 'QuestItemDup' and pr.get('recipient') != 'all':
            out.append('%s: QuestItemDup must be recipient=all (owner decision)' % owner)
        if k == 'SlideshowEnd' and pr.get('run') != 'host-once':
            out.append('%s: bad SlideshowEnd' % owner)
        if k == 'PartyAwareCond':
            for fn, red in pr.get('funcs', {}).items():
                if fn not in KNOWN_FUNCS and fn not in ext:
                    out.append('%s: PartyAwareCond function %s neither known nor in funcs_vocabulary_extension' % (owner, fn))
                if not red:
                    out.append('%s: empty reduction for %s' % (owner, fn))
        return out

    for qn, patch in p['quests'].items():
        if qn not in dm:
            e.append('patch quest %s not in DeadMoney.esm' % qn)
            continue
        base = rules.get(qn)
        if base is None:
            e.append('patch quest %s has no entry in rules.json (use new_quests)' % qn)
            continue
        have = {r['kind'] for r in base['rules']}
        for r in patch.get('replace', []):
            if r['kind'] not in have:
                e.append('%s: replace of %s but the quest has no such rule' % (qn, r['kind']))
            e += chk_rule(qn, r)
        for r in patch.get('add', []):
            if r['kind'] in have:
                e.append('%s: add of %s but the quest already has it (use replace)' % (qn, r['kind']))
            e += chk_rule(qn, r)
        for k in patch.get('remove', []):
            if k not in have:
                e.append('%s: remove of absent kind %s' % (qn, k))
        if not patch.get('why'):
            e.append('%s: no why' % qn)
    for qn, nq in p['new_quests'].items():
        if qn not in dm:
            e.append('new quest %s not in DeadMoney.esm' % qn)
            continue
        if qn in rules:
            e.append('new quest %s already in rules.json' % qn)
        if nq['tier'] != d.risk[dm[qn]['ref']]['tier']:
            e.append('new quest %s: tier %s != risk.json %s' % (qn, nq['tier'], d.risk[dm[qn]['ref']]['tier']))
        if nq['questline'] != 'DLC: Dead Money':
            e.append('%s: questline' % qn)
        kinds = [r['kind'] for r in nq['rules']]
        if len(kinds) != len(set(kinds)):
            e.append('%s: duplicate kinds' % qn)
        for r in nq['rules']:
            e += chk_rule(qn, r)
    # every T3/T4 Dead Money story quest has some rule (existing or patched)
    covered = set(rules) | set(p['new_quests'])
    for q in d.quests:
        if d.risk[q['ref']]['tier'] in ('T3', 'T4') and q['edid'] not in covered:
            e.append('T3/T4 quest %s has no rule in rules.json or the patch' % q['edid'])
    return e


def check_dialogue(a):
    d = data()
    e = []
    idx = {i['ref']: i for i in d.infos}
    ef = effect_names() | ORACLE_ONLY
    ids = set()
    for s in a.dlg['scenarios']:
        if s['id'] in ids:
            e.append('duplicate scenario %s' % s['id'])
        ids.add(s['id'])
        if s['id'] not in a.dlg_md:
            e.append('%s missing from dialogue.md' % s['id'])
        if not d.edid_index().get(s['steps'][0]['npc'].lower()):
            e.append('%s: speaker %s unresolved' % (s['id'], s['steps'][0]['npc']))
        seen = set()
        for st in s['steps'][1:]:
            i = idx.get(st['info'])
            if i is None:
                e.append('%s: INFO %s does not exist' % (s['id'], st['info']))
                continue
            if st['info'] in seen:
                e.append('%s: INFO %s listed twice' % (s['id'], st['info']))
            seen.add(st['info'])
            if i['topic'] != st['topic'] or i['topic'] not in d.dials and not i['topic'].startswith('FalloutNV'):
                e.append('%s: topic mismatch for %s' % (s['id'], st['info']))
            if st['info_formid'] != dmlib.runtime_id(st['info']):
                e.append('%s: bad formid for %s' % (s['id'], st['info']))
            for f in st['effects_static']:
                if f['type'] not in ef:
                    e.append('%s: effect %s not in effect.h' % (s['id'], f['type']))
            if not (i['responses'] and st['line'] and i['responses'][0]['text'].startswith(st['line'][:30])):
                e.append('%s: line text of %s differs from the ESM' % (s['id'], st['info']))
        for qn in s['quests']:
            if not resolves(qn):
                e.append('%s: quest %s unresolved' % (s['id'], qn))
        if s['initiator_model'] not in ('ok', 'ok-with-gate', 'ok-with-service-session', 'breaks'):
            e.append('%s: bad initiator_model' % s['id'])
    if sum(1 for s in a.dlg['scenarios'] if s['initiator_model'] == 'breaks') < 3:
        e.append('the review must flag the conversations the initiator-picks model breaks')
    return e


def check_docs(a):
    e = []
    for f in ('policy_review.md', 'gaps.md', 'dialogue.md'):
        if f == 'policy_review.md':
            t = a.policy_md
        elif f == 'gaps.md':
            t = a.gaps_md
        else:
            t = a.dlg_md
        if len(t) < 2000:
            e.append('%s is too short' % f)
    for k in ('barrier', 'tether', 'cinematic', 'reward'):
        if k not in a.policy_md.lower():
            e.append('policy_review.md does not cover %s' % k)
    return e


CHECKS = [('inventory', check_inventory), ('sweep', check_sweep), ('gaps', check_gaps), ('hazards', check_hazards),
          ('patch', check_patch), ('dialogue', check_dialogue), ('docs', check_docs)]


# ---------------------------------------------------------------------------------------------------------------------------
# mutation twin
# ---------------------------------------------------------------------------------------------------------------------------
def mutants(a):
    """(name, check, mutated Art).  Every one must produce at least one error."""
    out = []

    def clone():
        b = copy.copy(a)
        for n in ('inv', 'sweep', 'gaps', 'dlg', 'patch'):
            setattr(b, n, copy.deepcopy(getattr(a, n)))
        return b

    b = clone()
    b.sweep['quests'][0]['stages'].pop()
    out.append(('sweep: a stage is neither run nor excluded', check_sweep, b))
    b = clone()
    q = next(q for q in b.sweep['quests'] if q['edid'] == 'NVDLC01MQ01a')
    q['setup'][0]['cmd'] = 'setstage 01FFFFFF 5'
    out.append(('sweep: setup command with an invented FormID', check_sweep, b))
    b = clone()
    q = next(q for q in b.sweep['quests'] if q['edid'] == 'NVDLC01MQ01a')
    q['setup'][0]['stage'] = 77
    out.append(('sweep: setup stage that does not exist', check_sweep, b))
    b = clone()
    b.sweep['excluded'][0]['reason'] = 'x'
    q = next(q for q in b.sweep['quests'] if q['edid'] == 'NVDLC01EnemyTest')
    q['stages'][0]['exclude_reason'] = 'x'
    out.append(('sweep: exclusion without a reason', check_sweep, b))
    b = clone()
    b.sweep['event_probes'][0]['expect_effects'].append('NotAnEffect')
    out.append(('sweep: probe expects an effect that does not exist', check_sweep, b))
    b = clone()
    b.inv['quests'][3]['formid'] = '00000001'
    out.append(('inventory: wrong FormID', check_inventory, b))
    b = clone()
    next(q for q in b.inv['quests'] if q['stages'])['stages'].pop()
    out.append(('inventory: a stage is missing', check_inventory, b))
    b = clone()
    b.inv['quests'].pop()
    out.append(('inventory: a quest is missing', check_inventory, b))
    b = clone()
    r = next(r for r in b.gaps['functions'] if r['fn'] == 'PlaceAtMe')
    r['locations'].pop()
    out.append(('gaps: an unsupported site has no exact location', check_gaps, b))
    b = clone()
    r = next(r for r in b.gaps['functions'] if r['fn'] == 'PlaceAtMe')
    r['locations'][0]['line'] += 1
    out.append(('gaps: a location points at the wrong line', check_gaps, b))
    b = clone()
    r = next(r for r in b.gaps['functions'] if r['fn'] == 'SetPlayerTeammate')
    r['capture'] = 'supported'
    out.append(('gaps: claims capture of a command L5 does not hook', check_gaps, b))
    b = clone()
    r = next(r for r in b.gaps['functions'] if r['fn'] == 'Activate')
    r['capture'] = 'supported'
    out.append(('gaps: claims capture of a block-only hook', check_gaps, b))
    b = clone()
    next(r for r in b.gaps['functions'] if r['fn'] == 'AddItem')['effect'] = 'GiveItem'
    out.append(('gaps: effect type that does not exist in effect.h', check_gaps, b))
    b = clone()
    b.gaps['summary']['unknown_calls'] = 2
    out.append(('gaps: unknown calls present', check_gaps, b))
    b = clone()
    b.gaps['hazards'][0]['resolved'] = []
    out.append(('hazards: citation no longer resolves', check_hazards, b))
    b = clone()
    b.gaps['hazards'][1]['id'] = b.gaps['hazards'][0]['id']
    out.append(('hazards: duplicate id', check_hazards, b))
    b = clone()
    b.policy_md = b.policy_md.replace('COLLAR-STATE', 'XXXX')
    out.append(('hazards: blocker missing from the policy review', check_hazards, b))
    b = clone()
    b.patch['quests']['NVDLC01MQ99'] = {'add': [], 'why': 'x'}
    out.append(('patch: quest that does not exist', check_patch, b))
    b = clone()
    b.patch['quests']['NVDLC01MQ00']['add'].append({'kind': 'WarpCore', 'params': {}})
    out.append(('patch: a 17th rule kind without a proposal', check_patch, b))
    b = clone()
    b.patch['quests']['NVDLC01MQ00']['replace'][0]['params']['markers'].append('NoSuchMarkerXYZ')
    out.append(('patch: marker that does not resolve', check_patch, b))
    b = clone()
    b.patch['new_quests']['NVDLC01Intro']['tier'] = 'T0'
    out.append(('patch: tier disagrees with risk.json', check_patch, b))
    b = clone()
    b.patch['quests']['NVDLC01MQ02']['add'].append({'kind': 'CompanionOwner', 'params': {'owner': 'host'}})
    out.append(('patch: CompanionOwner contradicts the owner decision', check_patch, b))
    b = clone()
    b.patch['quests']['NVDLC01MQ01']['add'].append({'kind': 'RewardAll', 'params': {}})
    out.append(('patch: add of a kind the quest already has', check_patch, b))
    b = clone()
    b.patch['proposed_kinds'][0]['shard_vars']['NVDLC01BombCollarQuestSCRIPT'].append('iNoSuchVariable')
    out.append(('patch: shard variable that is not declared', check_patch, b))
    b = clone()
    b.dlg['scenarios'][0]['steps'][1]['info'] = 'DeadMoney.esm:0FFFFF'
    out.append(('dialogue: INFO that does not exist', check_dialogue, b))
    b = clone()
    b.dlg['scenarios'][2]['steps'][1]['line'] = 'Completely different text'
    out.append(('dialogue: line text differs from the ESM', check_dialogue, b))
    b = clone()
    for s in b.dlg['scenarios']:
        s['initiator_model'] = 'ok'
    out.append(('dialogue: no conversation flagged as breaking the model', check_dialogue, b))
    b = clone()
    b.policy_md = 'short'
    out.append(('docs: policy review missing', check_docs, b))
    return out


def stale(tmp):
    errs = []
    for n in ARTIFACTS:
        p = os.path.join(HERE, n)
        if not os.path.exists(p):
            errs.append('%s missing: run build.py and dialogue_build.py' % n)
        elif read(p) != read(os.path.join(tmp, n)):
            errs.append('%s is stale: run `python -I questlines/deadmoney/build.py` and dialogue_build.py' % n)
    return errs


def generator_twin():
    """The generator's own guards: a bogus function, a script with an unknown call, a broken hazard regex."""
    errs = []
    names = dmlib.command_names()
    if not dmlib.unknown_calls('scn X\nBEGIN GameMode\n    player.NoSuchCommandXyz 1\nEND\n', names):
        errs.append('unknown_calls did not flag an unknown command')
    if fnmap.row('NoSuchCommandXyz') is not None:
        errs.append('fnmap invented a row')
    if prereq.stage_setup('NVDLC01MQ01a', 100) != [prereq.S('NVDLC01MQ01a', s, 'earlier stage on the nominal path') for s in (10, 20, 30, 40)]:
        errs.append('stage_setup chain is wrong')
    return errs


def main():
    tmp = tempfile.mkdtemp(prefix='dm_tests_')
    build.main(['--out', tmp])
    dialogue_build.main(['--out', tmp])
    fails = []
    moved = drift_note()
    if moved:
        print('NOTE: live L4/L5 sources changed since l5_snapshot.json: %s (run refresh_l5.py, review fnmap.py; not a failure)' % ', '.join(moved))
    fails += ['STALE: ' + x for x in stale(tmp)]
    a = Art(HERE)           # committed artefacts (already proven identical to the regenerated ones)
    for name, fn in CHECKS:
        errs = fn(a)
        print('%-10s %s' % (name, 'ok' if not errs else '%d error(s)' % len(errs)))
        fails += ['%s: %s' % (name, x) for x in errs[:10]]
    caught = 0
    ms = mutants(a)
    for name, fn, b in ms:
        errs = fn(b)
        if errs:
            caught += 1
        else:
            fails.append('MUTANT SURVIVED: %s' % name)
    print('mutation twin: %d/%d mutants caught' % (caught, len(ms)))
    g = generator_twin()
    print('generator guards: %s' % ('ok' if not g else '%d error(s)' % len(g)))
    fails += ['guard: ' + x for x in g]
    shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print('\n'.join(fails))
        print('FAILED (%d)' % len(fails))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
