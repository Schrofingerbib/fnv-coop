"""Test gate for the NCR questline lane.   python -I questlines/ncr/run_tests.py   -> prints ALL OK and exits 0.

Checks (against out/questdata, never against the game):
  inventory   quests/stages/objectives/targets resolve and equal quests.json; roles; counts
  sweep       every stage of every swept quest is in sweep.json or explicitly excluded with a reason; every console command is well formed
              and every hex FormID / stage / objective / quest variable in it resolves
  gaps        arithmetic, line numbers and texts point at real script lines, function table is consistent with the L5 CODE
              (capture claims are checked against plugin/hooks/engine_hooks.cpp + capture_hub.cpp, apply claims against apply_remote.cpp)
  patch       rules.patch.json keys exist in rules.json, kinds are the 16 existing ones, every EditorID in params resolves, evidence points at gaps
  dialogue    every INFO / topic / NPC ref / quest / variable in dialogue_specs.json resolves and matches the data
  docs        policy_review.md / dialogue.md / internal questline notes mention what they must; generated blocks are up to date
  fresh       regenerating every artifact gives byte-identical content (nothing is stale, nothing hand-edited)
  scanner     unit tests for the script scanner and the setup-atom extractor
Every check family has a mutation twin: a deliberately broken artifact that the same check must reject.
"""
import copy
import json
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
import dialogue  # noqa: E402
import fntable  # noqa: E402
import gaps as gaps_mod  # noqa: E402
import inventory as inv_mod  # noqa: E402
import loadorder  # noqa: E402
import sweep as sweep_mod  # noqa: E402

REPO = common.REPO
KINDS = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner', 'SaveSuppress',
         'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner', 'ShareKarmaRep', 'InitiatorDialogue',
         'ExcludeFromGate']
HAZARDS = {'player-targeted', 'spawn', 'companion', 'essential', 'move', 'cinematic', 'menu', 'timer', 'radio', 'ending', 'kill-everyone', 'reward',
           'container', 'world', 'persistent-state', 'quest-state', 'topic-list', 'npc-ai', 'npc-script-var', 'combat', 'faction-relations',
           'faction-membership', 'control-lock', 'fast-travel', 'party-warp', 'dialogue', 'slideshow', 'save', 'crime', 'cosmetic',
           'player-ref-cond', 'ambient', 'kill'}
SEVERITIES = {'blocker', 'high', 'medium', 'low', 'info'}
STATUSES = {'supported', 'partial', 'missing', 'n/a'}
S, M = 'supported', 'missing'
CMD_RE = [
    ('setstage', re.compile(r'^setstage ([0-9A-F]{6,8}) (\d+)$')),
    ('setobj', re.compile(r'^setobjective(displayed|completed) ([0-9A-F]{6,8}) (\d+) ([01])$')),
    ('quest', re.compile(r'^(startquest|completequest|stopquest) ([0-9A-F]{6,8})$')),
    ('setvar', re.compile(r'^set ([0-9A-F]{6,8})\.(\w+) to (-?\d+(?:\.\d+)?)$')),
    ('additem', re.compile(r'^player\.additem ([0-9A-F]{6,8}) (\d+)$')),
    ('forceav', re.compile(r'^player\.forceav (\w+) (\d+)$')),
    ('setglobal', re.compile(r'^set ([0-9A-F]{6,8}) to (-?\d+(?:\.\d+)?)$')),
]


def hex_ref(h):
    plugins = loadorder.load()
    v = int(h, 16)
    idx, local = v >> 24, v & 0xFFFFFF
    if idx >= len(plugins):
        return None
    return f'{plugins[idx]}:{local:06X}'


class Ctx:
    def __init__(self):
        self.D = common.Data()
        self.qvars = {}
        for q in self.D.quests:
            if q.get('edid') and not q.get('override') and q.get('script_data'):
                self.qvars[q['ref']] = {v['name'].lower() for v in q['script_data'].get('vars', [])}

    def check_cmd(self, cmd, errs, where):
        D = self.D
        for kind, rx in CMD_RE:
            m = rx.match(cmd)
            if not m:
                continue
            g = m.groups()
            if kind == 'forceav':
                return
            if kind == 'additem':
                ref = hex_ref(g[0])
                if not ref or not D.ref_exists(ref):
                    errs.append(f'{where}: item {g[0]} does not resolve ({cmd})')
                return
            if kind == 'setglobal':
                ref = hex_ref(g[0])
                if not ref or not any(x.get('ref') == ref for x in D.d.get('globals', [])):
                    errs.append(f'{where}: global {g[0]} does not resolve ({cmd})')
                return
            qh = g[0] if kind in ('setstage', 'setvar') else g[1]
            ref = hex_ref(qh)
            q = D.by_ref.get(ref) if ref else None
            if q is None:
                errs.append(f'{where}: quest {qh} does not resolve ({cmd})')
                return
            if kind == 'setstage' and int(g[1]) not in {s['index'] for s in q['stages']}:
                errs.append(f'{where}: {q["edid"]} has no stage {g[1]} ({cmd})')
            if kind == 'setobj' and int(g[2]) not in {o['index'] for o in q['objectives']}:
                errs.append(f'{where}: {q["edid"]} has no objective {g[2]} ({cmd})')
            if kind == 'setvar' and g[1].lower() not in self.qvars.get(ref, set()):
                errs.append(f'{where}: {q["edid"]} script has no variable {g[1]} ({cmd})')
            return
        errs.append(f'{where}: command not in the allowed grammar: {cmd!r}')


# ------------------------------------------------------------------------------------------------------------------ inventory
def check_inventory(C, inv):
    D, errs = C.D, []
    roles = {}
    for e in common.CORE:
        roles[e] = 'core'
    for e in common.SUPPORT:
        roles[e] = 'support'
    for e in common.SYSTEM:
        roles[e] = 'system'
    seen = {}
    for q in inv['quests']:
        e = q['edid']
        if e in seen:
            errs.append(f'inventory: duplicate quest {e}')
        seen[e] = q
        src = D.by_edid.get(e)
        if src is None:
            errs.append(f'inventory: {e} is not in quests.json')
            continue
        if q['ref'] != src['ref'] or q['formid'] != src['formid']:
            errs.append(f'inventory: {e} ref/formid differ from quests.json')
        if q['console_id'] != common.hexid(src['ref']):
            errs.append(f'inventory: {e} console_id {q["console_id"]} != {common.hexid(src["ref"])}')
        if [s['index'] for s in q['stages']] != sorted(s['index'] for s in src['stages']):
            errs.append(f'inventory: {e} stage list differs from quests.json')
        if sorted(o['index'] for o in q['objectives']) != sorted(o['index'] for o in src['objectives']):
            errs.append(f'inventory: {e} objective list differs from quests.json')
        for o in q['objectives']:
            for t in o['targets']:
                if not D.ref_exists(t['ref']):
                    errs.append(f'inventory: {e} objective {o["index"]} target {t["ref"]} unresolved')
        if q['dialogue']['infos'] != len(D.infos.get(src['ref'], [])) or q['dialogue']['topics'] != len(src.get('topics', [])):
            errs.append(f'inventory: {e} dialogue counts differ')
        if q['tier'] != D.risk[src['ref']]['tier']:
            errs.append(f'inventory: {e} tier differs from risk.json')
        if e in roles and q['role'] != roles[e]:
            errs.append(f'inventory: {e} role {q["role"]} != {roles[e]}')
        if q['role'] in ('core', 'support') and not q['stages'] and e not in ('x',):
            pass
    for e in roles:
        if e not in seen:
            errs.append(f'inventory: scope quest {e} missing')
    for e in inv['critical_path_ncr']:
        if e not in D.by_edid:
            errs.append(f'inventory: critical path quest {e} unknown')
    s = inv['summary']
    if s['stages'] != sum(len(q['stages']) for q in inv['quests'] if q['role'] in ('core', 'support')):
        errs.append('inventory: summary.stages inconsistent')
    hc = inv.get('hostowned_coverage', {})
    if hc.get('objective_target_actors_missing') or hc.get('script_subject_actor_lines_missing'):
        errs.append(f'inventory: actors missing from hostowned.json: {hc}')
    return errs


# ------------------------------------------------------------------------------------------------------------------ sweep
def check_sweep(C, sw, inv):
    D, errs = C.D, []
    sq = {q['edid']: q for q in sw['quests']}
    for iq in inv['quests']:
        e = iq['edid']
        if iq['role'] in ('core', 'support'):
            q = sq.get(e)
            if q is None:
                errs.append(f'sweep: {e} missing from sweep.json')
                continue
            if sorted(s['index'] for s in q['stages']) != [s['index'] for s in iq['stages']]:
                errs.append(f'sweep: {e} stages differ from inventory (not every stage swept)')
        elif iq['stages']:
            ex = [x for x in sw['excluded'] if x['quest'] == e]
            if not ex:
                errs.append(f'sweep: {e} has {len(iq["stages"])} stages but is neither swept nor excluded')
                continue
            if not set(s['index'] for s in iq['stages']) <= set(ex[0]['stages']):
                errs.append(f'sweep: {e} excluded stages incomplete')
            if not ex[0].get('reason'):
                errs.append(f'sweep: {e} exclusion has no reason')
    for x in sw['excluded']:
        if x['quest'] in sq:
            errs.append(f'sweep: {x["quest"]} is both swept and excluded')
    for q in sw['quests']:
        e = q['edid']
        src = D.by_edid.get(e)
        if src is None:
            errs.append(f'sweep: {e} unknown')
            continue
        for k in ('edid', 'ref', 'stages', 'tier_direct', 'tier'):
            if k not in q:
                errs.append(f'sweep: {e} lacks m1-compatible key {k}')
        if q['console_id'] not in q['reset']:
            errs.append(f'sweep: {e} reset list lacks the quest itself')
        for h in q['reset']:
            ref = hex_ref(h)
            if not ref or ref not in D.by_ref:
                errs.append(f'sweep: {e} reset id {h} is not a quest')
        valid = {s['index'] for s in q['stages']}
        for k in list(q['stage_setup']) + list(q['risky']):
            if int(k) not in valid:
                errs.append(f'sweep: {e} stage_setup/risky key {k} is not a stage')
        for item in q['setup'] + q['chain_setup']:
            C.check_cmd(item['cmd'], errs, f'sweep: {e} setup')
            if item.get('required') not in (True, False):
                errs.append(f'sweep: {e} setup entry lacks required flag')
        for b in q['quest_script_branches']:
            for c in b['cmds']:
                C.check_cmd(c, errs, f'sweep: {e} quest-script branch line {b["line"]}')
        for k, v in q['stage_setup'].items():
            for b in v['branches']:
                for c in b['cmds']:
                    C.check_cmd(c, errs, f'sweep: {e} stage {k} branch line {b["line"]}')
        if 'start_enabled' not in (src.get('flags') or []) and not any(i['cmd'] == f'startquest {q["console_id"]}' for i in q['setup']):
            errs.append(f'sweep: {e} is not start-enabled but setup has no startquest')
    return errs


# ------------------------------------------------------------------------------------------------------------------ function table vs L5 code
def check_fntable(table=None, split=None):
    table = fntable.FN if table is None else table
    split = fntable.PLAYER_SPLIT if split is None else split
    names, recorded, applied = fntable.NAMES, fntable.RECORDED, fntable.APPLIED
    errs = []
    entries = [(fn, e) for fn, e in table.items()] + [(fn + ' (player)', e) for fn, e in split.items()]
    for fn, e in entries:
        if e['capture'] not in STATUSES or e['apply'] not in STATUSES:
            errs.append(f'fntable: {fn} has an invalid status')
        if e['severity'] not in SEVERITIES:
            errs.append(f'fntable: {fn} has an invalid severity')
        for h in e['hazards']:
            if h not in HAZARDS:
                errs.append(f'fntable: {fn} hazard {h!r} not in the taxonomy')
        tk = fntable.tokens(e['effect'], names)
        if e['effect'] and not tk:
            errs.append(f'fntable: {fn} effect {e["effect"]!r} names no synccore Effect type')
        auto = e.get('auto', {})
        if auto.get('capture') and e['capture'] != (S if tk and tk <= recorded else M):
            errs.append(f'fntable: {fn} capture={e["capture"]} disagrees with plugin/hooks (records {sorted(tk & recorded)} of {sorted(tk)})')
        if auto.get('apply') and e['apply'] != (S if tk and tk <= applied else M):
            errs.append(f'fntable: {fn} apply={e["apply"]} disagrees with RemoteApplier (handles {sorted(tk & applied)} of {sorted(tk)})')
        if e['capture'] == 'supported' and not tk <= recorded:
            errs.append(f'fntable: {fn} claims capture=supported but L5 never records {sorted(tk - recorded)}')
        if e['apply'] == 'supported' and not tk <= applied:
            errs.append(f'fntable: {fn} claims apply=supported but RemoteApplier has no branch for {sorted(tk - applied)}')
        if e['capture'] == 'missing' and tk and tk <= recorded:
            errs.append(f'fntable: {fn} claims capture=missing but L5 records {sorted(tk)} (update the table)')
        if e['apply'] == 'missing' and tk and tk <= applied:
            errs.append(f'fntable: {fn} claims apply=missing but RemoteApplier handles {sorted(tk)} (update the table)')
    return errs


# ------------------------------------------------------------------------------------------------------------------ gaps
def own_script_lines(C):
    out = {}
    for e in common.CORE + common.SUPPORT + common.SYSTEM:
        q = C.D.by_edid[e]
        for where, sc, _ in C.D.quest_scripts(q):
            out[(e, where)] = sc['source'].replace('\r', '').split('\n')
    return out


def check_gaps(C, gp, lines=None):
    errs = []
    lines = lines or own_script_lines(C)
    s = gp['summary']
    if s['gap_occurrences'] != sum(g['count'] for g in gp['gaps']):
        errs.append('gaps: gap_occurrences != sum of counts')
    if sum(v['occurrences'] for v in s['by_severity'].values()) != s['gap_occurrences']:
        errs.append('gaps: by_severity does not add up')
    for g in gp['gaps']:
        if g['count'] != len(g['locations']) or g['count'] != sum(g['by_quest'].values()):
            errs.append(f'gaps: {g["fn"]} count/locations/by_quest disagree')
        if g['status'] == 'supported':
            errs.append(f'gaps: {g["fn"]} is listed as a gap but is supported')
        if g['severity'] not in SEVERITIES or any(h not in HAZARDS for h in g['hazards']):
            errs.append(f'gaps: {g["fn"]} bad severity/hazard')
        for l in g['locations']:
            if l['where'].startswith('dep '):
                if g['fn'] not in ('set refvar', 'set questvar', 'set global') and not g['fn'].split('(')[0].replace(' ', '') in l['text'].lower().replace(' ', ''):
                    errs.append(f'gaps: {g["fn"]} dep location text lacks the function: {l}')
                continue
            src = lines.get((l['quest'], l['where']))
            if src is None:
                errs.append(f'gaps: {g["fn"]} location {l["quest"]}/{l["where"]} is not a script of the quest')
                continue
            if not 1 <= l['line'] <= len(src):
                errs.append(f'gaps: {g["fn"]} {l["quest"]}/{l["where"]} line {l["line"]} out of range')
                continue
            if common.strip(src[l['line'] - 1])[:160] != l['text']:
                errs.append(f'gaps: {g["fn"]} {l["quest"]}/{l["where"]} line {l["line"]} text differs from the source')
    am = gp['ambient']
    scope = set(common.CORE + common.SUPPORT + common.SYSTEM)
    if sum(sum(a['dropped'].values()) for a in am['sources']) != am['dropped_events']:
        errs.append('gaps: ambient.sources do not add up to ambient.dropped_events')
    for a in am['sources']:
        if not a['owners'] or not set(a['owners']) <= scope:
            errs.append(f'gaps: ambient source {a["script"]} has no valid owner quest {a["owners"]}')
    if gp['unclassified']:
        errs.append(f'gaps: {len(gp["unclassified"])} unclassified commands remain (extend fntable.py): {[u["fn"] for u in gp["unclassified"]][:5]}')
    return errs


# ------------------------------------------------------------------------------------------------------------------ rules patch
def check_patch(C, patch, locs, rules=None):
    D, errs = C.D, []
    rules = rules or D.rules['quests']
    if patch.get('new_kinds'):
        errs.append('patch: new kinds proposed without the justification this lane promises (none expected)')
    if len(set(KINDS)) != 16:
        errs.append('test: KINDS must have 16 entries')
    sig_ok = {'cells': {'CELL', 'WRLD'}, 'markers': {'REFR', 'ACHR', 'ACRE'}}
    fact_ok = {'FACT'}
    for i, op in enumerate(patch['ops']):
        tag = f'patch op {i} ({op.get("op")} {op.get("quest")})'
        kind = op.get('op')
        q = op.get('quest')
        if kind not in ('add_rule', 'remove_rule', 'update_params', 'add_quest'):
            errs.append(f'{tag}: unknown op')
            continue
        if q not in D.by_edid:
            errs.append(f'{tag}: quest not in quests.json')
            continue
        have = {r['kind']: r for r in rules.get(q, {}).get('rules', [])}
        if kind == 'add_quest':
            if q in rules:
                errs.append(f'{tag}: quest already has an entry in rules.json')
            ent = op['entry']
            rl = ent['rules']
        elif kind == 'add_rule':
            if q not in rules:
                errs.append(f'{tag}: quest has no rules.json entry (use add_quest)')
            rl = [op['rule']]
            if op['rule']['kind'] in have:
                errs.append(f'{tag}: rule kind {op["rule"]["kind"]} already present (use update_params)')
        else:
            if op['kind'] not in have:
                errs.append(f'{tag}: rules.json has no {op["kind"]} rule on {q}')
            rl = []
            if op['kind'] not in KINDS:
                errs.append(f'{tag}: kind {op["kind"]} is not one of the 16')
            pm = op.get('params_merge', {})
            for key in ('cells', 'markers'):
                rl.append({'kind': op['kind'], 'params': {key: pm[key]}}) if key in pm else None
            if 'factions' in pm:
                rl.append({'kind': op['kind'], 'params': {'factions': pm['factions']}})
        if not op.get('why'):
            errs.append(f'{tag}: no justification')
        for r in rl:
            if r['kind'] not in KINDS:
                errs.append(f'{tag}: kind {r["kind"]} is not one of the 16 existing kinds')
            p = r.get('params', {})
            for key, sigs in sig_ok.items():
                for name in p.get(key, []):
                    ref = D.resolve(name)
                    if not ref or D.ref_info(ref).get('sig') not in sigs:
                        errs.append(f'{tag}: {key[:-1]} {name!r} does not resolve to a {sorted(sigs)}')
            for name in p.get('factions', []):
                ref = D.resolve(name)
                if not ref or D.ref_info(ref).get('sig') not in fact_ok:
                    errs.append(f'{tag}: faction {name!r} does not resolve to a FACT')
        for ev in op.get('evidence', []):
            if (ev['script'], ev['line'], ev['fn']) not in locs:
                errs.append(f'{tag}: evidence {ev} is not a scanned script location')
    for qq in patch.get('param_extensions', []):
        if qq['kind'] not in KINDS:
            errs.append('patch: param extension on unknown kind')
    if not patch.get('owner_questions'):
        errs.append('patch: owner_questions missing')
    return errs


# ------------------------------------------------------------------------------------------------------------------ dialogue
def check_dialogue(C, dl):
    D, errs = C.D, []
    dials = {d['ref']: d for d in D.dials}
    infos = {}
    for lst in D.infos.values():
        for i in lst:
            infos[i['ref']] = i
    ids = set()
    for sc in dl['scenarios']:
        if sc['id'] in ids:
            errs.append(f'dialogue: duplicate id {sc["id"]}')
        ids.add(sc['id'])
        for qn in sc['quests']:
            if qn not in D.by_edid:
                errs.append(f'dialogue {sc["id"]}: quest {qn} unknown')
        sids = set()
        for st in sc['steps']:
            tag = f'dialogue {sc["id"]} step {st["id"]}'
            if st['id'] in sids:
                errs.append(f'{tag}: duplicate step id')
            sids.add(st['id'])
            if st['action'] == 'setup':
                for c in st.get('cmds', []):
                    C.check_cmd(c, errs, tag)
                for plist in st.get('players', {}).values():
                    for c in plist:
                        C.check_cmd(c, errs, tag)
            if st['action'] == 'pick':
                i = infos.get(st['info'])
                if i is None:
                    errs.append(f'{tag}: INFO {st["info"]} not in infos.jsonl')
                    continue
                d = dials.get(st['topic'])
                if i['topic'] != st['topic'] or d is None:
                    errs.append(f'{tag}: topic {st["topic"]} is not the INFO topic')
                    continue
                if d.get('edid') != st['topic_edid'] or d.get('name') != st['prompt']:
                    errs.append(f'{tag}: topic edid/prompt differ from dials.json')
                txt = i['responses'][0]['text'] if i['responses'] else ''
                if not txt.startswith(st['line']):
                    errs.append(f'{tag}: line prefix {st["line"]!r} != INFO text {txt[:40]!r}')
            if st['action'] in ('talk', 'event') and st.get('npc_ref'):
                ri = D.ref_info(st['npc_ref'])
                if ri.get('sig') != 'ACHR':
                    errs.append(f'{tag}: npc_ref {st["npc_ref"]} is not an ACHR')
                if st.get('npc') and ri.get('edid') != st['npc'] and not st['npc'].lower() == (ri.get('edid') or '').lower():
                    errs.append(f'{tag}: npc {st["npc"]} != EDID {ri.get("edid")}')
            if st['action'] == 'talk' and st.get('greeting_info') and st['greeting_info'] not in infos:
                errs.append(f'{tag}: greeting INFO unknown')
            ex = st.get('expect', {})
            for key in ('stage', 'stage_not', 'stage_eventually', 'objective_displayed', 'objective_completed'):
                for qn in ex.get(key, {}):
                    if qn not in D.by_edid:
                        errs.append(f'{tag}: expect.{key} quest {qn} unknown')
            for qn in ex.get('quest_running', []) + ex.get('quest_completed', []):
                if qn not in D.by_edid:
                    errs.append(f'{tag}: expect quest {qn} unknown')
            for rn in ex.get('reputation', {}):
                ref = D.resolve(rn)
                if not ref or D.ref_info(ref).get('sig') != 'REPU':
                    errs.append(f'{tag}: reputation {rn} unknown')
            for fnm in ex.get('faction_rank_shared', {}):
                ref = D.resolve(fnm)
                if not ref or D.ref_info(ref).get('sig') != 'FACT':
                    errs.append(f'{tag}: faction {fnm} unknown')
            for var in ex.get('script_var', {}):
                qn, _, vn = var.partition('.')
                q = D.by_edid.get(qn)
                if q is None or vn.lower() not in {v['name'].lower() for v in (q.get('script_data') or {}).get('vars', [])}:
                    errs.append(f'{tag}: script variable {var} unknown')
    return errs


# ------------------------------------------------------------------------------------------------------------------ docs / fresh
def norm(x):
    return json.loads(json.dumps(x))


def load_art():
    def j(n):
        with open(os.path.join(HERE, n), encoding='utf-8') as f:
            return json.load(f)
    return {'inv': j('inventory.json'), 'sw': j('sweep.json'), 'gp': j('gaps.json'), 'dl': j('dialogue_specs.json'), 'patch': j('rules.patch.json')}


def check_docs(C, art):
    errs = []
    pr = open(os.path.join(HERE, 'policy_review.md'), encoding='utf-8').read()
    for e in common.CORE + common.SUPPORT:
        if e not in pr:
            errs.append(f'docs: policy_review.md never mentions {e}')
    for op in art['patch']['ops']:
        if op['quest'] not in pr:
            errs.append(f'docs: policy_review.md does not discuss patched quest {op["quest"]}')
    dm = open(os.path.join(HERE, 'dialogue.md'), encoding='utf-8').read()
    for sc in art['dl']['scenarios']:
        if sc['id'] not in dm:
            errs.append(f'docs: dialogue.md does not list spec {sc["id"]}')
    rows = dialogue.stats(C.D)
    a, b = dm.find(dialogue.BEGIN), dm.find(dialogue.END)
    if a < 0 or b < 0 or dm[a + len(dialogue.BEGIN):b].strip() != dialogue.stats_table(rows).strip():
        errs.append('docs: dialogue.md stats block is stale (run build_all.py)')
    st = os.path.join(REPO, 'docs', 'questlines', 'ncr.md')
    if not os.path.exists(st):
        errs.append('docs: internal questline notes missing')
    else:
        import status
        txt = open(st, encoding='utf-8').read()
        blk = status.counts_block(art['inv'], art['sw'], art['gp'])
        if blk.strip() not in txt:
            errs.append('docs: internal questline notes counts block is stale (run build_all.py --status)')
        if 'ALL OK' not in txt:
            errs.append('docs: internal questline notes lacks the pasted test output')
        if txt.count('| ') < 20 or 'TOP 10' not in txt.upper():
            errs.append('docs: internal questline notes lacks the top-10 blocker list')
    return errs


def check_fresh(C, art):
    errs = []
    inv = inv_mod.build(C.D)
    gp = gaps_mod.build(C.D)
    sw = sweep_mod.build(C.D, inv=art['inv'], gaps=art['gp'])
    dl = dialogue.build()
    for name, new, old in (('inventory.json', inv, art['inv']), ('gaps.json', gp, art['gp']), ('sweep.json', sw, art['sw']), ('dialogue_specs.json', dl, art['dl'])):
        if norm(new) != old:
            errs.append(f'fresh: {name} on disk differs from what the generator produces now (run build_all.py)')
    return errs


# ------------------------------------------------------------------------------------------------------------------ scanner units
def scanner_units(C, scan=None, atoms_fn=None):
    errs = []
    scan = scan or fntable.scan_script
    D = C.D
    evs, unc = scan('Player.MoveTo vHDOliverTeleportMarker\nvHDMikeLawsonRef.MoveTo vHDMikeLawsonDialogueMarker\n', set(), D)
    got = [(e['fn'], e['severity']) for e in evs]
    if got != [('moveto', 'blocker'), ('moveto', 'low')]:
        errs.append(f'scanner: MoveTo subject split wrong: {got}')
    evs, _ = scan('if GetStage VMQ05 > 0\nSetStage VMQ05 200\nendif\nSetStage VMQ05 100\n', set(), D)
    if [e['conditional'] for e in evs] != [True, False]:
        errs.append('scanner: conditional/unconditional nesting wrong')
    evs, _ = scan('set VMS45.NumKhansDead to 3\nset VStoryNCRState to 3\nset bLocal to 1\n', {'blocal'}, D)
    if [e['fn'] for e in evs] != ['set questvar', 'set global']:
        errs.append(f'scanner: assignments classified wrong: {[e["fn"] for e in evs]}')
    evs, _ = scan('SetObjectiveDisplayed VMQ05 25 0\nSetObjectiveDisplayed VMQ05 26 1\n', set(), D)
    if [e['fn'] for e in evs] != ['setobjectivedisplayed(0)', 'setobjectivedisplayed']:
        errs.append('scanner: objective hide not distinguished')
    evs, _ = scan('; Player.MoveTo X\nRewardXP 100\n', set(), D)
    if [e['fn'] for e in evs] != ['rewardxp']:
        errs.append('scanner: comment not ignored')
    sw = sweep_mod.Sweep(D)
    f = atoms_fn or sw.atoms
    cmds, _ = f('if GetStage VMQ05 > 0')
    if cmds != ['setstage 136166 10']:
        errs.append(f'atoms: GetStage > 0 -> {cmds}')
    cmds, _ = f('If GetQC VMS21a != 1 && GetStage VMS21a >= 5')
    if cmds != ['setstage 110A65 5']:
        errs.append(f'atoms: != 1 handling -> {cmds}')
    cmds, man = f('if VHDColonelMooreRef.GetDead == 0')
    if cmds or man:
        errs.append('atoms: default-state GetDead produced output')
    cmds, _ = f('if (VDialogueMrHouse.BoomerQuest == 3 && bHouse3 == 0)')
    if cmds != ['set 116B58.BoomerQuest to 3']:
        errs.append(f'atoms: quest variable -> {cmds}')
    return errs


# ------------------------------------------------------------------------------------------------------------------ runner
def main():
    C = Ctx()
    art = load_art()
    results, failed = [], []

    def run(name, errs):
        results.append((name, errs))
        if errs:
            failed.append(name)

    run('inventory', check_inventory(C, art['inv']))
    run('sweep', check_sweep(C, art['sw'], art['inv']))
    run('fntable-vs-L5-code', check_fntable())
    run('gaps', check_gaps(C, art['gp']))
    all_events, _unc, _n = gaps_mod.collect(C.D)
    locs = {(e['script'], e['line'], e['fn']) for e in all_events}
    run('patch', check_patch(C, art['patch'], locs))
    run('dialogue', check_dialogue(C, art['dl']))
    run('scanner-units', scanner_units(C))
    run('docs', check_docs(C, art))
    run('fresh', check_fresh(C, art))

    # ---------------- mutation twins: each broken artifact MUST be rejected by the same check ----------------
    mutants = []

    def mutant(name, errs):
        mutants.append((name, errs))

    lines = own_script_lines(C)

    m = copy.deepcopy(art['inv'])
    q = next(x for x in m['quests'] if x['edid'] == 'VMQ05')
    q['stages'].pop()
    mutant('inventory: stage dropped', check_inventory(C, m))
    m = copy.deepcopy(art['inv'])
    next(x for x in m['quests'] if x['edid'] == 'VMS45')['console_id'] = 'DEADBE'
    mutant('inventory: wrong console id', check_inventory(C, m))

    m = copy.deepcopy(art['sw'])
    next(x for x in m['quests'] if x['edid'] == 'VMQ05')['stages'].pop(0)
    mutant('sweep: stage not swept', check_sweep(C, m, art['inv']))
    m = copy.deepcopy(art['sw'])
    m['excluded'].pop(0)
    mutant('sweep: exclusion removed', check_sweep(C, m, art['inv']))
    m = copy.deepcopy(art['sw'])
    next(x for x in m['quests'] if x['edid'] == 'VMS21')['setup'][0]['cmd'] = 'startquest 7F0000'
    mutant('sweep: bogus FormID in setup', check_sweep(C, m, art['inv']))
    m = copy.deepcopy(art['sw'])
    qq = next(x for x in m['quests'] if x['edid'] == 'VMQNCRFail')
    qq['quest_script_branches'][0]['cmds'] = ['set 116B58.NoSuchVariable to 3']
    mutant('sweep: unknown quest variable', check_sweep(C, m, art['inv']))
    m = copy.deepcopy(art['sw'])
    next(x for x in m['quests'] if x['edid'] == 'VMQ05')['setup'].append({'cmd': 'player.kill', 'why': 'x', 'required': False})
    mutant('sweep: command outside the grammar', check_sweep(C, m, art['inv']))

    def pick(field, status):
        return next(fn for fn, e in fntable.FN.items() if e['auto'][field] and e[field] == status and fntable.tokens(e['effect']))
    fn_a = pick('capture', 'missing')
    t = copy.deepcopy(fntable.FN)
    t[fn_a] = dict(t[fn_a], capture='supported')
    mutant(f'fntable: {fn_a} claimed captured although L5 has no hook', check_fntable(t))
    fn_b = pick('capture', 'supported')
    t = copy.deepcopy(fntable.FN)
    t[fn_b] = dict(t[fn_b], capture='missing')
    mutant(f'fntable: {fn_b} claimed uncaptured although L5 records it', check_fntable(t))
    fn_c = pick('apply', 'missing')
    t = copy.deepcopy(fntable.FN)
    t[fn_c] = dict(t[fn_c], apply='supported')
    mutant(f'fntable: {fn_c} claimed applied although RemoteApplier has no branch', check_fntable(t))

    m = copy.deepcopy(art['gp'])
    m['gaps'][0]['locations'][0]['line'] += 1
    mutant('gaps: location line shifted', check_gaps(C, m, lines))
    m = copy.deepcopy(art['gp'])
    m['gaps'][0]['count'] += 1
    mutant('gaps: count tampered', check_gaps(C, m, lines))
    m = copy.deepcopy(art['gp'])
    m['ambient']['sources'][0]['owners'] = []
    mutant('gaps: ambient source without an owner quest', check_gaps(C, m, lines))
    m = copy.deepcopy(art['gp'])
    m['gaps'][1]['status'] = 'supported'
    mutant('gaps: supported listed as gap', check_gaps(C, m, lines))

    m = copy.deepcopy(art['patch'])
    m['ops'][0]['rule']['kind'] = 'NotARuleKind'
    mutant('patch: unknown rule kind', check_patch(C, m, locs))
    m = copy.deepcopy(art['patch'])
    m['ops'][0]['quest'] = 'VMQ05Nope'
    mutant('patch: unknown quest', check_patch(C, m, locs))
    m = copy.deepcopy(art['patch'])
    next(o for o in m['ops'] if o['op'] == 'add_rule' and o['rule']['kind'] == 'PartyWarp')['rule']['params']['markers'][0] = 'NoSuchMarkerEdid'
    mutant('patch: marker EditorID unresolved', check_patch(C, m, locs))
    m = copy.deepcopy(art['patch'])
    next(o for o in m['ops'] if o.get('evidence'))['evidence'][0]['line'] = 99999
    mutant('patch: evidence not in gaps', check_patch(C, m, locs))
    m = copy.deepcopy(art['patch'])
    m['new_kinds'] = [{'kind': 'Whatever'}]
    mutant('patch: unjustified new kind', check_patch(C, m, locs))
    m = copy.deepcopy(art['patch'])
    next(o for o in m['ops'] if o['op'] == 'remove_rule')['kind'] = 'Barrier'
    mutant('patch: remove_rule of a rule that does not exist', check_patch(C, m, locs))

    m = copy.deepcopy(art['dl'])
    next(s for s in m['scenarios'][0]['steps'] if s['action'] == 'pick')['info'] = 'FalloutNV.esm:FFFFFF'
    mutant('dialogue: INFO ref bogus', check_dialogue(C, m))
    m = copy.deepcopy(art['dl'])
    next(s for s in m['scenarios'][0]['steps'] if s['action'] == 'pick')['prompt'] = 'Not the prompt'
    mutant('dialogue: prompt changed', check_dialogue(C, m))
    m = copy.deepcopy(art['dl'])
    next(s for s in m['scenarios'][1]['steps'] if s['action'] == 'talk')['npc_ref'] = 'FalloutNV.esm:1206FF'
    mutant('dialogue: NPC ref not an actor', check_dialogue(C, m))

    # scanner / atoms twins: broken implementations must fail the unit tests
    def broken_scan(src, lv, D):
        evs, unc = fntable.scan_script(src, lv, D)
        for e in evs:
            if e['fn'] == 'moveto':
                e['severity'] = 'low'
        return evs, unc
    mutant('scanner: player MoveTo not distinguished', scanner_units(C, scan=broken_scan))
    sw = sweep_mod.Sweep(C.D)
    mutant('atoms: != 1 treated as ==', scanner_units(C, atoms_fn=lambda line: ([c for c in sw.atoms(line)[0]] + ['completequest 110A65'] if 'GetQC' in line else sw.atoms(line)[0], [])))

    uncaught = [n for n, errs in mutants if not errs]

    print('check families:')
    for name, errs in results:
        print(f'  {"PASS" if not errs else "FAIL"}  {name}')
        for e in errs[:12]:
            print('        ' + e)
        if len(errs) > 12:
            print(f'        ... {len(errs) - 12} more')
    print(f'mutation twins: {len(mutants) - len(uncaught)}/{len(mutants)} caught')
    for n, errs in mutants:
        print(f'  {"caught" if errs else "MISSED"}  {n}' + (f'  <- {errs[0][:110]}' if errs else ''))
    if failed or uncaught:
        print('FAILED:', failed + [f'uncaught mutant: {n}' for n in uncaught])
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
