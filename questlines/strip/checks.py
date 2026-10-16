"""Validators for every generated artifact. Each returns a list of problems (empty = valid). run_tests.py runs them on the real
artifacts (must be empty) and on deliberately broken copies (the mutation twin: must be non-empty)."""
import collections
import json
import os
import re

import cmdtable as ct
import dialogue
import gaps as gaps_mod
import policy
import scope
import scriptscan

REPO = scope.REPO
M1_KEYS = {'milestone', 'core', 'system_quests', 'touched_quests', 'summary', 'quests'}
M1_QUEST_KEYS = {'edid', 'ref', 'name', 'role', 'tier', 'tier_direct', 'cats', 'priority', 'flags', 'script', 'stages', 'objectives',
                 'globals', 'items', 'enable_disable', 'script_subjects', 'setstage_other_quests', 'npcs', 'dialogue'}
M1_STAGE_KEYS = {'index', 'log', 'flags', 'has_script', 'findings', 'objective_effects'}
SETUP_CMDS = {'startquest', 'setstage', 'completequest', 'setobjectivedisplayed', 'setobjectivecompleted'}
HEX = re.compile(r'^[0-9A-F]{6}$|^[0-9A-F]{8}$')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------------------------------------------------
def validate_scope(data):
    bad = list(data.resolve_scope())
    core_rules = {e for e, v in data.rules['quests'].items() if v['questline'] == policy.QUESTLINE}
    if set(scope.CORE) != core_rules:
        bad.append(f'CORE differs from rules.json questline: {sorted(set(scope.CORE) ^ core_rules)}')
    names = [e for e, _, _ in data.members()]
    dup = [k for k, v in collections.Counter(names).items() if v > 1]
    if dup:
        bad.append(f'duplicate members {dup}')
    if set(scope.CROSS_LANE) & set(names):
        bad.append('cross-lane quest listed as member')
    return bad


def validate_inventory(inv, data):
    bad = []
    names = [e for e, _, _ in data.members()]
    got = [q['edid'] for q in inv['quests']]
    if got != names:
        bad.append(f'inventory quest list differs from scope: {sorted(set(got) ^ set(names))}')
    plugins = scope.loadorder.load()
    for q in inv['quests']:
        raw = data.by_edid.get(q['edid'])
        if raw is None:
            bad.append(f'{q["edid"]}: not in quests.json')
            continue
        if q['ref'] != raw['ref']:
            bad.append(f'{q["edid"]}: ref {q["ref"]} != {raw["ref"]}')
        if q['formid'] != scope.ref_hex(raw['ref']) or q['console_id'] != scope.loadorder.console_id(raw['ref'], plugins):
            bad.append(f'{q["edid"]}: formid/console_id mismatch')
        if not HEX.match(q['console_id']):
            bad.append(f'{q["edid"]}: console_id {q["console_id"]!r} is not hex')
        if q['runtime_formid'] != '%08X' % scope.loadorder.runtime_formid(raw['ref'], plugins):
            bad.append(f'{q["edid"]}: runtime_formid mismatch')
        stages = sorted(s['index'] for s in raw['stages'])
        if [s['index'] for s in q['stages']] != stages:
            bad.append(f'{q["edid"]}: stage list differs from quests.json')
        if [o['index'] for o in q['objectives']] != [o['index'] for o in raw['objectives']]:
            bad.append(f'{q["edid"]}: objective list differs from quests.json')
        rk = data.risk[raw['ref']]
        if q['tier'] != rk['tier'] or q['tier_direct'] != rk['tier_direct']:
            bad.append(f'{q["edid"]}: tier differs from risk.json')
        if q['dialogue']['own_topics'] != len(raw.get('topics', [])) or q['dialogue']['own_infos'] != len(data.infos_by_quest.get(raw['ref'], [])):
            bad.append(f'{q["edid"]}: dialogue counts differ from the data')
        for key in ('gated_by', 'driven_by', 'drives'):
            for e in q['prerequisites'][key]:
                if e['quest'] not in data.by_edid:
                    bad.append(f'{q["edid"]}: prerequisite quest {e["quest"]} unknown')
        for e in q['prerequisites']['explicit_conditions']:
            if e['quest'] not in data.by_edid:
                bad.append(f'{q["edid"]}: condition quest {e["quest"]} unknown')
    c = inv['counts']
    if c['quests'] != len(inv['quests']) or c['stages'] != sum(q['stage_count'] for q in inv['quests']):
        bad.append('inventory counts inconsistent')
    return bad


def validate_sweep(sw, inv, data):
    bad = []
    if set(sw) < M1_KEYS:
        bad.append(f'sweep top-level keys missing: {sorted(M1_KEYS - set(sw))}')
    excl = {(e['quest'], e['stage']): e['reason'] for e in sw.get('exclusions', [])}
    by_hex = {}
    for q in data.quests:
        if q.get('edid'):
            by_hex[scope.console_hex(q['ref'])] = q
    for q in sw['quests']:
        if not M1_QUEST_KEYS <= set(q):
            bad.append(f'{q["edid"]}: missing m1 keys {sorted(M1_QUEST_KEYS - set(q))}')
        raw = data.by_edid.get(q['edid'])
        if raw is None or q['ref'] != raw['ref']:
            bad.append(f'{q["edid"]}: ref does not resolve')
            continue
        real = sorted(s['index'] for s in raw['stages'])
        swept = sorted(s['index'] for s in q['stages'] if s['sweep'])
        skipped = sorted(s['index'] for s in q['stages'] if not s['sweep'])
        for s in q['stages']:
            if not M1_STAGE_KEYS <= set(s):
                bad.append(f'{q["edid"]}:{s["index"]}: missing m1 stage keys')
            if not s['sweep'] and not (s.get('exclude_reason') or excl.get((q['edid'], s['index']))):
                bad.append(f'{q["edid"]}:{s["index"]}: excluded without a reason')
            if s['command'] != f'setstage {scope.console_hex(raw["ref"])} {s["index"]}':
                bad.append(f'{q["edid"]}:{s["index"]}: command {s["command"]!r} is not the console setstage line')
        if sorted(swept + skipped) != real:
            bad.append(f'{q["edid"]}: stages not covered: {sorted(set(real) ^ set(swept + skipped))}')
        if not real and (q['edid'], None) not in excl:
            bad.append(f'{q["edid"]}: no stages and no exclusion entry')
        lines = list(q['setup']) + [ln for s in q['stages'] for ln in s['setup']]
        for ln in lines:
            bad += validate_setup_line(ln, by_hex, q['edid'])
        for s in q['stages']:
            if len(set(s['setup'])) != len(s['setup']):
                bad.append(f'{q["edid"]}:{s["index"]}: duplicate setup lines')
            if len(s['setup']) != len(s['setup_meta']):
                bad.append(f'{q["edid"]}:{s["index"]}: setup/setup_meta length differ')
    plan = [dict(edid=q['edid'], ref=q['ref'], stages=[s['index'] for s in q['stages']], tier=q.get('tier_direct') or q.get('tier'))
            for q in sw['quests']]       # same fields plugin/snapshot/tools/goodsprings_sweep.load_plan reads
    if len(plan) != len(inv['quests']):
        bad.append('sweep quest count differs from inventory')
    return bad


def validate_setup_line(ln, by_hex, ctx):
    bad = []
    parts = ln.split()
    if not parts or parts[0] not in SETUP_CMDS:
        return [f'{ctx}: setup command {ln!r} is not one of {sorted(SETUP_CMDS)} (lower-case FNV console names)']
    if len(parts) < 2 or not HEX.match(parts[1]):
        return [f'{ctx}: setup {ln!r}: FormID is not hex']
    q = by_hex.get(parts[1])
    if q is None:
        return [f'{ctx}: setup {ln!r}: FormID resolves to no quest']
    try:
        if parts[0] == 'setstage':
            if int(parts[2]) not in {s['index'] for s in q['stages']}:
                bad.append(f'{ctx}: setup {ln!r}: stage {parts[2]} does not exist in {q["edid"]}')
        elif parts[0].startswith('setobjective'):
            if int(parts[2]) not in {o['index'] for o in q['objectives']} or parts[3] not in ('0', '1'):
                bad.append(f'{ctx}: setup {ln!r}: objective does not exist in {q["edid"]}')
        elif len(parts) != 2:
            bad.append(f'{ctx}: setup {ln!r}: unexpected arguments')
    except (IndexError, ValueError):
        bad.append(f'{ctx}: setup {ln!r}: malformed')
    return bad


# ---------------------------------------------------------------------------------------------------------------------
def effect_names():
    src = read(os.path.join(REPO, 'synccore', 'include', 'fnvcoop', 'effect.h'))
    m = re.search(r'using Effect = std::variant<(.*?)>;', src, re.S)
    return {x.strip() for x in m.group(1).split(',')}


def hook_names():
    src = read(os.path.join(REPO, 'plugin', 'hooks', 'engine_addrs.h'))
    return set(re.findall(r'\{"([A-Za-z_:0-9]+)"', src))


def applier_effects():
    src = read(os.path.join(REPO, 'plugin', 'hooks', 'apply_remote.cpp'))
    return set(re.findall(r'std::get_if<(\w+)>', src))


# command -> hooks that must exist for capture == supported
HOOK_FOR = {
    'setstage': ['TESQuest::SetStage'], 'setobjectivedisplayed': ['BGSQuestObjective::SetState'],
    'setobjectivecompleted': ['BGSQuestObjective::SetState'], 'startquest': ['TESQuest::SetRunning'],
    'stopquest': ['TESQuest::SetRunning'], 'completequest': ['TESQuest::SetCompleted'], 'failquest': ['TESQuest::SetFailed'],
    'enable': ['TESObjectREFR::SetDisabled'], 'disable': ['TESObjectREFR::SetDisabled'], 'additem': ['Cmd_AddItem'],
    'removeitem': ['Cmd_RemoveItem'], 'rewardxp': ['Cmd_RewardXP'], 'setav': ['Cmd_SetActorValue'],
    'setactorvalue': ['Cmd_SetActorValue'], 'modav': ['Cmd_ModActorValue'], 'modactorvalue': ['Cmd_ModActorValue'],
    'damageactorvalue': ['Cmd_DamageActorValue'], 'rewardkarma': ['Player::RewardKarma'], 'addreputation': ['TESReputation::Add'],
    'removereputation': ['TESReputation::Remove'], 'setreputation': ['TESReputation::Set'],
    'addreputationexact': ['TESReputation::AddExact'], 'kill': ['Cmd_KillActor'], 'killactor': ['Cmd_KillActor'],
    'resurrect': ['Cmd_ResurrectActor'], 'moveto': ['Cmd_MoveToMarker'], 'movetomarker': ['Cmd_MoveToMarker'],
    'movetomarkerwithfade': ['Cmd_MoveToMarkerWithFade'], 'setpos': ['Cmd_SetPos'],
    'completeallobjectives': ['TESQuest::CompleteAllObjectives'], 'addtofaction': ['Cmd_AddToFaction'],
    'removefromfaction': ['Cmd_RemoveFromFaction'], 'setfactionrank': ['Cmd_SetFactionRank'], 'addperk': ['Cmd_AddPerk'],
    'removeperk': ['Cmd_RemovePerk'], 'lock': ['Cmd_Lock'], 'unlock': ['Cmd_UnLock'], 'setopenstate': ['Cmd_SetOpenState'],
    'setdestroyed': ['Cmd_SetDestroyed'], 'addnote': ['Cmd_AddNote'], 'removenote': ['Cmd_RemoveNote'], 'addtopic': ['Cmd_AddTopic'],
    'autosave': ['Cmd_Autosave'], 'systemsave': ['Cmd_SystemSave'], 'placeatme': ['Cmd_PlaceAtMe'],
    'disableplayercontrols': ['Player::SetControlsDisabled'], 'enableplayercontrols': ['Player::SetControlsDisabled'],
}
# commands the table calls `missing` because no hook exists: if L5 ever installs one, this test fails and the row must be revisited
NOT_HOOKED = {
    'enablefasttravel': 'Cmd_EnableFastTravel', 'setplayerteammate': 'Cmd_SetPlayerTeammate', 'say': 'Cmd_Say', 'sayto': 'Cmd_SayTo',
    'startconversation': 'Cmd_StartConversation', 'restoreav': 'Cmd_RestoreActorValue', 'restoreactorvalue': 'Cmd_RestoreActorValue',
    'resethealth': 'Cmd_ResetHealth', 'showmessage': 'Cmd_ShowMessage', 'showbartermenu': 'Cmd_ShowBarterMenu',
    'removealltypeditems': 'Cmd_RemoveAllTypedItems', 'sendassaultalarm': 'Cmd_SendAssaultAlarm', 'swaptexture': 'Cmd_SwapTexture',
    'markfordelete': 'Cmd_MarkForDelete', 'showmap': 'Cmd_ShowMap', 'removefromallfactions': 'Cmd_RemoveFromAllFactions',
    'additemns': 'Cmd_AddItemNS', 'forceactivequest': 'Cmd_ForceActiveQuest',
}


def validate_cmdtable():
    bad = []
    effects, hooks, applied = effect_names(), hook_names(), applier_effects()
    for fn, row in ct.CMD.items():
        for key in ('capture', 'apply'):
            if row[key] not in (ct.SUP, ct.PAR, ct.MIS, ct.NA):
                bad.append(f'{fn}: bad {key} status {row[key]!r}')
        if row['effect'] and row['effect'] not in effects:
            bad.append(f'{fn}: Effect {row["effect"]} is not in synccore effect.h')
        if row['sev'] not in gaps_mod.SEVS:
            bad.append(f'{fn}: bad severity')
        for t in row['tags']:
            if t not in ct.ALL_HAZARDS:
                bad.append(f'{fn}: unknown hazard tag {t}')
        if row['capture'] == ct.SUP and not fn.startswith('set-'):
            need = HOOK_FOR.get(fn)
            if not need:
                bad.append(f'{fn}: capture=supported but no hook mapping in HOOK_FOR')
            else:
                for h in need:
                    if h not in hooks:
                        bad.append(f'{fn}: capture=supported but hook {h} is not in engine_addrs.h')
        if row['apply'] == ct.SUP and row['effect'] and row['effect'] not in applied and row['effect'] not in ('SetGlobal',):
            bad.append(f'{fn}: apply=supported but apply_remote.cpp has no get_if<{row["effect"]}>')
        if row['effect'] and row['effect'] not in applied and row['effect'] not in ('RefScriptVar', 'SpawnRef', 'RequestSave') \
                and row['apply'] in (ct.SUP, ct.PAR) and row['effect'] != 'MoveRef':
            bad.append(f'{fn}: applier has no get_if<{row["effect"]}> but apply={row["apply"]}')
    for fn, hook in NOT_HOOKED.items():
        if hook in hooks:
            bad.append(f'{fn}: table says no capture hook but engine_addrs.h now has {hook}: revisit cmdtable.py')
        if fn in ct.CMD and ct.CMD[fn]['capture'] == ct.SUP:
            bad.append(f'{fn}: listed NOT_HOOKED but marked supported')
    return bad


def validate_gaps(g, data, deep=True):
    bad = []
    if g['unmapped']:
        bad.append(f'unmapped statements: {sorted(g["unmapped"])}')
    effects = effect_names()
    scripts = gaps_mod.collect_scripts(data) if deep else {}
    total = collections.Counter()
    unsupported = 0
    for r in g['functions']:
        total[r['status']] += r['count']
        if r['effect'] and r['effect'] not in effects:
            bad.append(f'{r["fn"]}: Effect {r["effect"]} not in effect.h')
        if r['status'] not in (ct.SUP, ct.PAR, ct.MIS, ct.NA):
            bad.append(f'{r["fn"]}: bad status')
        if r['gap'] != (r['status'] in (ct.PAR, ct.MIS)):
            bad.append(f'{r["fn"]}: gap flag inconsistent with status')
        if not r['gap']:
            continue
        unsupported += r['count']
        if len(r['locations']) != r['count']:
            bad.append(f'{r["fn"]}: {len(r["locations"])} locations for count {r["count"]}')
        if not deep:
            continue
        for l in r['locations']:
            sc = scripts.get(l['label'])
            if sc is None:
                bad.append(f'{r["fn"]}: script {l["label"]} not in scope')
                continue
            lines = sc['source'].replace('\r', '').split('\n')
            if not (0 < l['line'] <= len(lines)):
                bad.append(f'{r["fn"]}: {l["script"]}:{l["line"]} beyond end of script')
                continue
            want = scriptscan.strip_comment(lines[l['line'] - 1]).strip()[:160]
            if want != l['text']:
                bad.append(f'{r["fn"]}: {l["script"]}:{l["line"]} text differs: {want!r} vs {l["text"]!r}')
    if dict(total) != {k: v for k, v in g['status_occurrences'].items()}:
        bad.append('status_occurrences do not add up')
    if unsupported != g['unsupported_occurrences']:
        bad.append('unsupported_occurrences do not add up')
    return bad


# ---------------------------------------------------------------------------------------------------------------------
def validate_patch(patch, data):
    bad = []
    base = data.rules
    forms_edids = {v['edid'].lower() for v in data.forms.values() if v.get('edid')} | {e.lower() for e in data.by_edid}
    try:
        after = policy.apply_patch(base, patch)
    except ValueError as e:
        return [f'patch does not apply: {e}']
    allowed = set(patch['kinds_allowed'])
    if allowed != set(policy.KINDS):
        bad.append('kinds_allowed differs from the 16 kinds of tools/coverage/rules.py')
    rules_py = read(os.path.join(REPO, 'tools', 'coverage', 'rules.py'))
    for k in policy.KINDS:
        if not re.search(r'^\s{2}' + k + r'\s', rules_py, re.M):
            bad.append(f'kind {k} is not documented in tools/coverage/rules.py')
    for edid in list(patch['amend']) + list(patch['new_quests']):
        if edid not in data.by_edid:
            bad.append(f'patch key {edid} is not a quest in quests.json')
    for edid in patch['amend']:
        if edid not in base['quests']:
            bad.append(f'amend key {edid} is not in rules.json')
    for edid, n in patch['new_quests'].items():
        if edid in base['quests']:
            bad.append(f'new_quests key {edid} already in rules.json')
        rk = data.risk[data.by_edid[edid]['ref']] if edid in data.by_edid else None
        if rk and n['tier'] != rk['tier']:
            bad.append(f'{edid}: tier {n["tier"]} != risk.json {rk["tier"]}')
    touched = list(patch['amend']) + list(patch['new_quests'])
    emitted = []        # (quest, rule) pairs this patch introduces: only these must resolve (rules.json's own unresolved ids are reported in facts F9)
    for edid, a in patch['amend'].items():
        emitted += [(edid, r) for r in a.get('add', [])]
        emitted += [(edid, dict(kind=k, params=p)) for k, p in a.get('set_params', {}).items()]
    for edid, n in patch['new_quests'].items():
        emitted += [(edid, r) for r in n['rules']]
    blocked = set(patch['blocked_params']['markers']) | set(patch['blocked_params']['cells'])
    for edid in touched:
        kinds = [r['kind'] for r in after['quests'][edid]['rules']]
        if len(kinds) != len(set(kinds)):
            bad.append(f'{edid}: duplicate kinds after patch {kinds}')
        for r in after['quests'][edid]['rules']:
            if r['kind'] not in allowed:
                bad.append(f'{edid}: kind {r["kind"]} not allowed')
        if 'SetPlayerControls' in kinds and 'PerPlayerScene' in kinds:
            bad.append(f'{edid}: SetPlayerControls (scope all) and PerPlayerScene contradict each other')
    for edid, r in emitted:
        for key in ('cells', 'markers'):
            for name in r.get('params', {}).get(key, []) or []:
                if name.lower() not in forms_edids:
                    bad.append(f'{edid}: {r["kind"]}.{key} names {name!r} which does not resolve in forms.json.gz / quests.json')
                if name in blocked:
                    bad.append(f'{edid}: blocked (unresolved) id {name} used as a rule param')
    for name in blocked:
        if name.lower() in forms_edids:
            bad.append(f'blocked_params lists {name} but it resolves now: move it into a rule')
    # removals must be justified by evidence that exists
    cites, cbad = policy.verify_citations(data)
    bad += cbad
    for edid, a in patch['amend'].items():
        for k in a.get('remove', []):
            if k not in {r['kind'] for r in base['quests'][edid]['rules']}:
                bad.append(f'{edid}: removes {k} which the quest does not have')
        if not a.get('evidence') and a.get('remove'):
            bad.append(f'{edid}: removal without evidence')
    # proposals: per-player vars and volatile keys must be real quest-script variables
    for table in (patch['proposals']['kind_uses'], patch['volatile_keys']):
        for q, vars_ in table.items():
            raw = data.by_edid.get(q)
            have = {v['name'].lower() for v in ((raw or {}).get('script_data') or {}).get('vars', [])}
            for v in vars_:
                if v.lower() not in have:
                    bad.append(f'{q}: variable {v} is not declared in its quest script')
    if {k['kind'] for k in patch['proposals']['kinds']} & set(policy.KINDS):
        bad.append('a proposed kind collides with an existing kind')
    used = {r['kind'] for edid in touched for r in after['quests'][edid]['rules']}
    if used - allowed:
        bad.append(f'patched quests use kinds outside the allowed set: {used - allowed}')
    return bad


def validate_dialogue(specs_, dlg, data):
    bad = []
    if not specs_:
        bad.append('no dialogue specs')
    ids = [s['id'] for s in specs_]
    if len(ids) != len(set(ids)):
        bad.append('duplicate spec ids')
    for s in specs_:
        bad += dialogue.check_spec(data, s)
        if s.get('schema') != 1 or s.get('kind') != 'dialogue' or not s.get('steps'):
            bad.append(f'{s.get("id")}: not L8 schema 1 kind dialogue')
        for q in s.get('quests', []):
            if q not in data.by_edid:
                bad.append(f'{s["id"]}: quest {q} unknown')
    for r in dlg['infos']:
        i = data.all_infos.get(r['info'])
        if not i or i.get('topic') != r['topic']:
            bad.append(f'{r["info"]}: INFO/topic does not resolve')
            break
    return bad


def validate_status(text, out):
    """internal questline notes must quote the same counts the generated artifacts have."""
    bad = []
    inv, sw, g = out['inventory'], out['sweep'], out['gaps']
    c = inv['counts']
    swept = sum(1 for q in sw['quests'] for s in q['stages'] if s['sweep'])
    want = [f'- quests: **{c["quests"]}**', f'- stages: **{c["stages"]}**', f'- stages swept: **{swept} of {c["stages"]}**',
            f'**{g["unsupported_functions"]} functions, {g["unsupported_occurrences"]} occurrences**']
    for w in want:
        if w not in text:
            bad.append(f'status doc does not contain {w!r}')
    if 'TOP 10 blockers' not in text:
        bad.append('status doc has no TOP 10 blockers section')
    top = [ln for ln in text.splitlines() if re.match(r'^(10|[1-9])\. \*\*', ln)]
    if len(top) != 10:
        bad.append(f'status doc lists {len(top)} blockers, expected 10')
    if 'ALL OK' not in text and 'QSTRIP_SKIP_STATUS' not in os.environ:
        bad.append('status doc has no pasted passing test output')
    return bad
