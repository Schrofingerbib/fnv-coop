"""Pure analysis functions for the towns questline: inventory, sweep config, gaps, dialogue extraction.

Everything here is derived from out/questdata; nothing is invented.  build.py writes the files, run_tests.py re-derives
and cross-checks them.  Run with `python -I`.
"""
import re
from collections import Counter, defaultdict

import fnmap
import lib

PLAYER_REF = 'FalloutNV.esm:000014'
TERMINAL_FLAGS = {'complete_quest', 'fail_quest'}

# Where the quest really happens, from objective/stage text and dialogue speakers (checked by reading the data).
# The questgraph/policy line assignment ("Side: Novac" ...) follows EDID adjacency, not geography.
GEO = {
    'VMS08': 'Camp McCarran / Las Vegas airport (Hsu, Curtis); policy file puts it under Primm',
    'nVPrimmDeputyConv': 'Primm (sheriff / Beagle / Bison Steve / NCRCF Meyers)',
    'nVPrimmVanceGun': 'Primm (Vikki and Vance casino); objective-only, 1 empty objective',
    'VES04FactReactPrimmArea01PowderGangers': 'Primm area (Powder Ganger faction reaction)',
    'VNipton': 'Nipton (Vulpes Inculta / captives)',
    'vNiptonVulpes': 'Nipton (Vulpes Inculta)',
    'VMS19': 'Freeside (the King, Major Kieran, Pacer) - NOT Novac',
    'VMS15': 'Nellis AFB (Boomers) - NOT Novac',
    'VMS11': 'Westside (Anderson, Romanowski, Sharecropper farms) - NOT Novac',
    'VMS10': 'Westside (support quest for VMS11) - NOT Novac',
    'VMS23': 'Nellis AFB / Hoover Dam embassy (Ambassador Crocker, Boomers) - NOT Novac',
    'VES01aVictorInNovac': 'Novac (Victor the Securitron appearance, 2 stages)',
    'VMS21': 'Gomorrah (Omerta racket, Cachino) - NOT Boulder City',
    'VMS21a': 'Gomorrah / Freeside (Joana, Carlitos) - NOT Boulder City',
    'VMS20': 'Boulder City (Great Khans vs NCR)',
    'VES01VictorGSMyBodyguard': 'Boulder City / Goodsprings (Victor as bodyguard)',
    'VES01bVictorInBoulder': 'Boulder City (Victor appearance)',
}


def hex_id(d, ref):
    plugin, local = ref.split(':')
    idx = d.loadorder.index(plugin)
    return ('%06X' % (int(local, 16) & 0xFFFFFF)) if idx == 0 else ('%02X%06X' % (idx, int(local, 16) & 0xFFFFFF))


def edid_of(d, ref):
    if ref == PLAYER_REF:
        return 'Player'
    q = d.by_ref.get(ref)
    if q:
        return q['edid']
    f = d.forms.get(ref)
    return f.get('edid') if f else None


def policy_sections():
    """EDID -> (policy file, heading) parsed from docs/quest-policies/side-*.md"""
    out = {}
    base = lib.os.path.join(lib.REPO, 'docs', 'quest-policies')
    for town, fn in (('Primm', 'side-primm.md'), ('Nipton', 'side-nipton.md'), ('Novac', 'side-novac.md'),
                     ('Boulder City', 'side-boulder-city.md')):
        with open(lib.os.path.join(base, fn), encoding='utf-8') as f:
            for ln in f:
                m = re.match(r'^## (\S+) - (.*?)\s+\[(T\d)[^\]]*\]', ln)
                if m:
                    out[m.group(1)] = (fn, ln.strip())
    return out


# ------------------------------------------------------------------------------------------------ inventory
def _cond(d, c):
    o = {'func': c.get('func'), 'op': c.get('op'), 'value': c.get('value')}
    for k in ('p1', 'p2'):
        v = c.get(k)
        if isinstance(v, str) and ':' in v:
            o[k] = v
            o[k + '_edid'] = edid_of(d, v)
        elif v not in (None, 0):
            o[k] = v
    if c.get('or'):
        o['or'] = True
    return o


def quest_prereqs(d, edid):
    q = d.by_edid[edid]
    inbound, outbound = defaultdict(list), defaultdict(list)
    for e in d.graph['edges']:
        if e['dst'] == edid and e['src'] != edid:
            inbound[e['kind']].append(e)
        if e['src'] == edid and e['dst'] != edid:
            outbound[e['kind']].append(e)

    def row(e, other):
        ev = e['evidence'][0] if e['evidence'] else {}
        return {'quest': e[other], 'strength': e['strength'], 'count': e['count'], 'where': ev.get('where'),
                'text': (ev.get('text') or '')[:140]}
    return {
        'quest_conditions': [_cond(d, c) for c in q['conditions']],
        'gated_by': [row(e, 'src') for e in inbound['gate']],
        'triggered_by_stage': [row(e, 'src') for e in inbound['stage'] + inbound['start'] + inbound['complete']],
        'written_by_var': [row(e, 'src') for e in inbound['var']],
        'triggers_stage': [row(e, 'dst') for e in outbound['stage'] + outbound['start'] + outbound['complete']],
        'gates_other': [row(e, 'dst') for e in outbound['gate']],
        'writes_var_of': [row(e, 'dst') for e in outbound['var']],
    }


def inventory(d):
    pol = policy_sections()
    line = {n['id']: n['line'] for n in d.graph['nodes']}
    out = []
    for town, edid, role in lib.all_quests():
        q = d.by_edid[edid]
        rk = d.risk[q['ref']]
        ins = d.infos.get(q['ref'], [])
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            ents = s['entries']
            stages.append({
                'index': s['index'],
                'text': next((e['text'] for e in ents if e.get('text')), None),
                'flags': sorted({f for e in ents for f in e['flags']}),
                'entries': len(ents),
                'has_script': any(e.get('script') and e['script']['source'].strip() for e in ents),
                'entry_conditions': sum(len(e['conditions']) for e in ents),
            })
        out.append({
            'edid': edid, 'ref': q['ref'], 'formid': q['formid'], 'runtime_formid': hex_id(d, q['ref']),
            'plugin': q['plugin'], 'name': q.get('name'), 'town_assignment': town, 'role': role,
            'in_policy_file': edid in lib.IN_POLICY_FILE,
            'policy_section': pol.get(edid, (None, None))[1],
            'questgraph_line': line.get(edid),
            'geography_note': GEO.get(edid, 'unknown'),
            'tier': rk['tier'], 'tier_direct': rk['tier_direct'], 'risk_score': rk['score'], 'risk_cats': rk['cats'],
            'flags': q['flags'], 'start_enabled': 'start_enabled' in q['flags'],
            'priority': q.get('priority'), 'script': q.get('script'),
            'stages': stages,
            'objectives': [{'index': o['index'], 'text': o.get('text'), 'targets': [t['ref'] for t in o['targets'] if t['ref']]}
                           for o in sorted(q['objectives'], key=lambda o: o['index'])],
            'prerequisites': quest_prereqs(d, edid),
            'dialogue': {'topics': len(q.get('topics', [])), 'infos': len(ins),
                         'scripted_infos': sum(1 for i in ins if i.get('begin') or i.get('end'))},
        })
    return out


# ------------------------------------------------------------------------------------------------ sweep
UNUSED_RE = re.compile(r'no longer does anything|not currently used', re.I)


def _setup_for_quest(d, q, inv):
    cmds = []
    if not inv['start_enabled']:
        cmds.append({'cmd': 'startquest %s' % inv['runtime_formid'], 'why': 'quest is not start_enabled'})
    seen = set()
    for c in q['conditions']:
        p1 = c.get('p1')
        if not (isinstance(p1, str) and p1 in d.by_ref) or p1 == q['ref']:
            continue
        pe = d.by_ref[p1]
        if c['func'] == 'GetStageDone' and c.get('value') == 1.0 and c.get('p2'):
            k = ('setstage', p1, c['p2'])
            if k not in seen:
                seen.add(k)
                cmds.append({'cmd': 'setstage %s %d' % (hex_id(d, p1), c['p2']), 'why': 'quest condition GetStageDone %s %d' % (pe['edid'], c['p2'])})
        elif c['func'] == 'GetQuestRunning' and c.get('value') == 1.0:
            k = ('startquest', p1)
            if k not in seen:
                seen.add(k)
                cmds.append({'cmd': 'startquest %s' % hex_id(d, p1), 'why': 'quest condition GetQuestRunning %s' % pe['edid']})
    return cmds


def stage_variants(d, q, inv, index):
    """Stage entries gated by this quest's own variables (e.g. VMS20 stage 100 has 5 outcomes keyed on bBoulderCityOutcome).
    Returns [{entry, label, setup:[cmd...]}] - empty when the stage has a single unconditional entry."""
    st = [s for s in q['stages'] if s['index'] == index]
    if not st or len(st[0]['entries']) < 2:
        return []
    names = {v['idx']: v['name'] for v in (q.get('script_data') or {}).get('vars', [])}
    out = []
    for n, e in enumerate(st[0]['entries']):
        setup, unresolved = [], []
        for c in e['conditions']:
            if c['func'] == 'GetQuestVariable' and c.get('p1') == q['ref']:
                nm = names.get(c['p2'])
                if nm and c['op'] == '==':
                    setup.append({'cmd': 'set %s.%s to %s' % (inv['runtime_formid'], nm, int(c['value']) if c['value'] == int(c['value']) else c['value']),
                                  'edid_cmd': 'set %s.%s to %s' % (q['edid'], nm, int(c['value']) if c['value'] == int(c['value']) else c['value']),
                                  'verify': 'quest-variable set syntax with a hex FormID prefix is unverified (L7 to confirm; fallback: bridge SetScriptVar)',
                                  'var': nm, 'var_index': c['p2']})
                else:
                    unresolved.append({'func': c['func'], 'op': c['op'], 'value': c['value'], 'p1': c.get('p1'), 'p2': c.get('p2')})
            else:
                unresolved.append({'func': c['func'], 'op': c['op'], 'value': c['value'], 'p1': c.get('p1'), 'p2': c.get('p2')})
        out.append({'entry': n, 'label': e.get('text') or '', 'flags': e['flags'], 'setup': setup, 'unresolved_conditions': unresolved})
    return out if any(v['setup'] or v['unresolved_conditions'] for v in out) else []


def sweep(d, inv):
    quests = []
    stage_total = stage_swept = 0
    for iv in inv:
        q = d.by_edid[iv['edid']]
        rq = d.risk[q['ref']]
        base_setup = _setup_for_quest(d, q, iv)
        for r in iv['prerequisites']['gated_by']:
            m = re.search(r'GetStageDone\s+(\w+)\s+(\d+)\s*==\s*1', r['text'] or '')
            if r['strength'] == 'hard' and m and m.group(1) in d.by_edid and m.group(1) != iv['edid']:
                c = 'setstage %s %s' % (hex_id(d, d.by_edid[m.group(1)]['ref']), m.group(2))
                if not any(x['cmd'] == c for x in base_setup):
                    base_setup.append({'cmd': c, 'why': 'script gate (%s): %s' % (r['where'], (r['text'] or '')[:90])})
        swept, excluded = [], []
        ordered = iv['stages']
        for s in ordered:
            stage_total += 1
            reason = None
            if UNUSED_RE.search(s['text'] or ''):
                reason = 'log text says the stage is unused: "%s"' % s['text'][:80]
            if reason:
                excluded.append({'index': s['index'], 'reason': reason})
                continue
            stage_swept += 1
            chain = [{'cmd': 'setstage %s %d' % (iv['runtime_formid'], p['index']), 'why': 'optional predecessor (non-terminal stage < %d)' % s['index']}
                     for p in ordered if p['index'] < s['index'] and not (set(p['flags']) & TERMINAL_FLAGS)
                     and not UNUSED_RE.search(p['text'] or '')]
            swept.append({
                'index': s['index'], 'log': s['text'], 'flags': s['flags'], 'has_script': s['has_script'],
                'findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text'])
                             for f in rq['stage_findings'] if f['where'] == 'stage %d' % s['index']],
                'setup': base_setup, 'chain': chain, 'variants': stage_variants(d, q, iv, s['index']),
                'set_cmd': 'setstage %s %d' % (iv['runtime_formid'], s['index']),
                'wait_cmd': 'GetStageDone %s %d' % (iv['runtime_formid'], s['index']),
            })
        obj_steps = []
        if not iv['stages']:
            # objective-only / container quests: drive the objective state instead of a stage
            for o in iv['objectives']:
                obj_steps.append({'objective': o['index'], 'text': o['text'],
                                  'set_cmd': 'SetObjectiveDisplayed %s %d 1' % (iv['runtime_formid'], o['index']),
                                  'setup': base_setup})
        quests.append({
            'edid': iv['edid'], 'ref': iv['ref'], 'runtime_formid': iv['runtime_formid'], 'name': iv['name'],
            'role': 'core' if iv['role'] == 'core' else 'system', 'town': iv['town_assignment'],
            'tier': iv['tier'], 'tier_direct': iv['tier_direct'], 'cats': iv['risk_cats'],
            'priority': iv['priority'], 'flags': iv['flags'], 'script': iv['script'],
            'setup': base_setup,
            'advisory_prereqs': [{'quest': r['quest'], 'formid': hex_id(d, d.by_edid[r['quest']]['ref']) if r['quest'] in d.by_edid else None,
                                  'strength': r['strength'], 'evidence': r['text'], 'where': r['where']}
                                 for r in iv['prerequisites']['gated_by'] if r['strength'] == 'hard'],
            'stages': swept, 'excluded_stages': excluded, 'objective_steps': obj_steps,
            'stageless_reason': None if iv['stages'] else (
                'no stages: dialogue/system container%s' % ('; objectives swept via objective_steps' if obj_steps else '; nothing to sweep')),
            'objectives': iv['objectives'],
        })
    return {
        'milestone': 'Questline towns (Primm, Nipton, Novac, Boulder City)',
        'format': 'm1_goodsprings.json compatible: quests[].{edid,ref,stages[].index,tier}; extra keys setup/chain/objective_steps/excluded_stages',
        'setup_doc': 'setup = console commands (hex FormIDs, ordered) to run once per stage after loading the clean save; chain = OPTIONAL '
                     'predecessor stages (non-terminal, lower index) for stages whose result scripts branch on quest vars set earlier. '
                     'Only startquest/setstage/SetObjectiveDisplayed are emitted - all exist in FNV and need no xNVSE.',
        'core': {q['edid']: q['name'] for q in quests if q['role'] == 'core'},
        'system_quests': [q['edid'] for q in quests if q['role'] != 'core'],
        'summary': {'quests': len(quests), 'stages_total': stage_total, 'stages_swept': stage_swept,
                    'stages_excluded': stage_total - stage_swept,
                    'objective_only_quests': sum(1 for q in quests if q['objective_steps']),
                    'stageless_quests': sum(1 for q in quests if not q['stages'] and not q['excluded_stages'])},
        'quests': quests,
    }


# ------------------------------------------------------------------------------------------------ gaps
HIGH_HAZ = {'spawn', 'companion', 'essential', 'teleport', 'cinematic', 'control-lock', 'ending', 'kill-all'}
# state reads: evaluated against already-synced quest state, so nothing to capture
STATE_READS = {'getobjectivecompleted', 'getobjectivedisplayed', 'getstagedone', 'getstage', 'getquestcompleted',
               'getquestrunning', 'getglobalvalue'}


def _severity(status, hazards, cls, player_targeted):
    """high   = missing support for something that moves/creates/locks players or actors (HIGH_HAZ) in a story path
       medium = missing support elsewhere, or partial support with a HIGH_HAZ hazard, or a player-ref condition
       low    = partial support without a hazard, host-only AI, cosmetics, parse noise"""
    if status == 'supported':
        return 'ok'
    if cls in ('cosmetic', 'noise', 'host-only'):
        return 'low'
    haz = set(hazards)
    if status == 'missing':
        return 'high' if haz & HIGH_HAZ else 'medium'
    return 'medium' if haz & HIGH_HAZ else 'low'


def _mk(quest, town, source, script, where, line, fn, effect, cls, hazards, note, player, cond, dyn=False, text=None):
    if effect:
        cap, app, enote = fnmap.status_of(effect)
        status = fnmap.worst(cap, app)
    else:
        cap = app = status = 'missing'
        enote = note or 'no synccore Effect type'
    haz = list(hazards)
    if player and cls == 'player' and 'player-effect' not in haz and 'reward' not in haz and 'karma-rep' not in haz:
        haz.append('player-effect')
    sev = _severity(status, haz, cls, player)
    return {'quest': quest, 'town': town, 'source': source, 'script': script, 'where': where, 'line': line, 'fn': fn,
            'effect': effect, 'class': cls, 'hazards': haz, 'player_targeted': bool(player), 'conditional': bool(cond),
            'dynamic': bool(dyn), 'capture': cap, 'apply': app, 'status': status, 'severity': sev,
            'note': enote if effect else note, 'text': text}


def _from_oracle_effect(e, quest, town, source, script, where):
    t = e['type']
    eff = {'AddPerk': 'SetPerk'}.get(t, t)
    fn = {'ModActorValue': 'ModActorValue(%s)' % e.get('av_name', '?'), 'SetPerk': 'AddPerk', 'AddPerk': 'AddPerk'}.get(t, t)
    cls, haz = 'state', []
    player = False
    if t in ('ModActorValue', 'AddItem', 'RemoveItem') and e.get('to') == PLAYER_REF:
        cls, player = 'player', True
        haz = ['reward'] if t in ('ModActorValue', 'AddItem') and (e.get('delta', 1) or 1) > 0 else ['player-effect']
    if t in ('ModReputation',):
        cls, haz = 'player', ['karma-rep', 'faction']
    if t == 'ModKarma':
        cls, haz = 'player', ['karma-rep']
    if t == 'AddPerk':
        cls, player, haz = 'player', True, ['player-effect']
    if t == 'SetFactionRank':
        haz = ['faction']
    if t == 'MoveRef':
        haz = ['teleport']
        if e.get('ref') == PLAYER_REF:
            player = True
            cls = 'player'
    if t == 'SetObjective':
        pass
    return _mk(quest, town, source, script, where, e.get('line'), fn, eff, cls, haz, '', player, e.get('conditional'),
               e.get('dynamic'))


def _from_unmodeled(u, quest, town, source, script, where):
    fn = u['fn']
    info = fnmap.lookup(fn)
    text = u.get('text')
    low = (text or '').lower()
    player = bool(re.match(r'^\s*(player|playerref)\.', low))
    if info is None:
        return _mk(quest, town, source, script, where, u.get('line'), fn, None, 'unmapped', [], 'function not in fnmap.F: classify by hand',
                   player, u.get('conditional'), text=text)
    eff, cls, haz, note = info
    if eff == 'SetPlayerControls':
        player = True
    if fn.lower() in ('addnote', 'rewardxp', 'additem'):
        player = True
    return _mk(quest, town, source, script, where, u.get('line'), fn, eff, cls, haz, note, player, u.get('conditional'), text=text)


COND_CATS = {'P-REF', 'P-COND'}
COND_FNS = {'getitemcount'}


def gaps(d):
    refs = {}
    for town, edid, role in lib.all_quests():
        refs[d.by_edid[edid]['ref']] = (edid, town)
    qscripts = {d.by_edid[e]['script']: (e, t) for t, e, r in lib.all_quests() if d.by_edid[e].get('script')}
    items = []
    infos_by_ref = {i['ref']: i for ii in d.infos.values() for i in ii}

    def line_of(src, n):
        if not src or not n:
            return None
        ls = src.replace(chr(13), '').split(chr(10))
        return ls[n - 1].strip()[:140] if 0 < n <= len(ls) else None

    # 1. stage + info (begin/end) result scripts from the effects oracle
    for s in d.effects['scripts']:
        if s['quest'] not in refs:
            continue
        edid, town = refs[s['quest']]
        if s['kind'] == 'stage':
            source, script, where = 'stage', '%s stage %d' % (edid, s['stage']), 'stage %d' % s['stage']
            st = [x for x in d.by_edid[edid]['stages'] if x['index'] == s['stage']]
            ents = st[0]['entries'] if st else []
            es = ents[s['entry']] if s.get('entry', 0) < len(ents) else None
            src = (es.get('script') or {}).get('source') if es else None
        else:
            local = s['info'].split(':')[1]
            blk = 'begin' if s['kind'] == 'info_begin' else 'end'
            source, script, where = 'info-' + blk, '%s INFO %s %s' % (edid, local, blk), 'info %s %s' % (local, blk)
            inf = infos_by_ref.get(s['info'])
            src = ((inf or {}).get(blk) or {}).get('source')
        new_items = [_from_oracle_effect(e, edid, town, source, script, where) for e in s['effects']]
        new_items += [_from_unmodeled(u, edid, town, source, script, where) for u in s['unmodeled']]
        for it in new_items:
            if not it['text']:
                it['text'] = line_of(src, it['line'])
        items.extend(new_items)
    # 2. quest script blocks
    for b in d.effects['blocks']:
        if b['script'] in qscripts:
            edid, town = qscripts[b['script']]
            script, where = b['edid'], 'quest script %s' % b['block']
            src = (d.by_edid[edid].get('script_data') or {}).get('source')
            new_items = [_from_oracle_effect(e, edid, town, 'quest-script', script, where) for e in b['effects']]
            new_items += [_from_unmodeled(u, edid, town, 'quest-script', script, where) for u in b['unmodeled']]
            for it in new_items:
                if not it['text']:
                    it['text'] = line_of(src, it['line'])
            items.extend(new_items)
    # 3. risk findings: conditions everywhere + dependent object scripts (NPC / trigger scripts the quest relies on)
    for ref, (edid, town) in refs.items():
        rk = d.risk[ref]
        for f in rk['findings'] + rk['stage_findings']:
            w = f['where']
            is_dep = w.startswith('dep obj')
            is_cond = f['cat'] in COND_CATS or f['fn'].lower() in COND_FNS
            if not (is_dep or is_cond):
                continue
            if is_dep:
                script, where, source = w.split(' ', 2)[2], w, 'dep-object'
            else:
                script, where, source = '%s %s' % (edid, w), w, 'condition'
            if is_cond:
                fn = f['fn']
                it = _mk(edid, town, source, script, where, f['line'], fn, None, 'condition', ['player-effect'] if f['cat'] == 'P-REF' else [],
                         'party-aware condition: synccore RulesEngine::reduceCondition exists (L4) but no engine hook evaluates player-ref '
                         'conditions over the party yet', f['cat'] == 'P-REF', False, text=f['text'])
                it['capture'] = it['apply'] = 'partial'
                it['status'] = 'partial'
                it['severity'] = 'medium' if f['cat'] == 'P-REF' else 'low'
                items.append(it)
            elif f['fn'].lower() in STATE_READS:
                it = _mk(edid, town, source, script, where, f['line'], f['fn'].lower(), None, 'read', [], 'quest-state read, evaluated on already-synced state',
                         False, False, text=f['text'])
                it['capture'] = it['apply'] = it['status'] = 'supported'
                it['severity'] = 'ok'
                items.append(it)
            else:
                u = {'fn': f['fn'], 'line': f['line'], 'conditional': False, 'text': f['text']}
                items.append(_from_unmodeled(u, edid, town, source, script, where))
    for it in items:
        _post(it)
    return items


def _post(it):
    """Refine with the source text: who is the subject (player vs NPC) decides teleport / faction / control hazards."""
    if it['class'] in ('condition', 'read'):
        return
    low = (it['text'] or '').lower()
    if re.match(r'^\s*(player|playerref)\.', low):
        it['player_targeted'] = True
    haz = [h for h in it['hazards']]
    if 'teleport' in haz and not it['player_targeted']:
        haz = ['npc-move' if h == 'teleport' else h for h in haz]
    if it['effect'] == 'SetFactionRank' and it['player_targeted'] and 'player-faction' not in haz:
        haz.append('player-faction')
    it['hazards'] = haz
    it['severity'] = _severity(it['status'], haz, it['class'], it['player_targeted'])


def summarize_gaps(items):
    by_sev = Counter(i['severity'] for i in items)
    by_status = Counter(i['status'] for i in items)
    unsupported = defaultdict(lambda: {'count': 0, 'locations': [], 'quests': set(), 'hazards': set(), 'status': None,
                                       'severity': 'low', 'effect': None, 'class': None, 'note': None})
    order = {'ok': 0, 'low': 1, 'medium': 2, 'high': 3}
    for i in items:
        if i['status'] == 'supported':
            continue
        fnl = i['fn'].lower() if not i['fn'].startswith('ModActorValue(') else i['fn']
        key = fnl if not i['effect'] or i['effect'].lower() == fnl else '%s -> %s' % (fnl, i['effect'])
        g = unsupported[key]
        g['count'] += 1
        g['locations'].append({'quest': i['quest'], 'script': i['script'], 'line': i['line']})
        g['quests'].add(i['quest'])
        g['hazards'].update(i['hazards'])
        g['status'] = i['status'] if g['status'] in (None, i['status']) else 'mixed'
        g['effect'], g['class'], g['note'] = i['effect'], i['class'], i['note']
        if order[i['severity']] > order[g['severity']]:
            g['severity'] = i['severity']
    out = {}
    for k, g in unsupported.items():
        g = dict(g)
        g['quests'] = sorted(g['quests'])
        g['hazards'] = sorted(g['hazards'])
        out[k] = g
    return {'by_severity': dict(by_sev), 'by_status': dict(by_status), 'unsupported': out}


# ------------------------------------------------------------------------------------------------ dialogue
AV_NAMES = {32: 'Barter', 33: 'BigGuns', 34: 'EnergyWeapons', 35: 'Explosives', 36: 'Lockpick', 37: 'Medicine', 38: 'MeleeWeapons',
            39: 'Repair', 40: 'Science', 41: 'Guns', 42: 'Sneak', 43: 'Speech', 44: 'Survival', 45: 'Unarmed',
            5: 'Strength', 6: 'Perception', 7: 'Endurance', 8: 'Charisma', 9: 'Intelligence', 10: 'Agility', 11: 'Luck'}
BRANCH_RE = re.compile(r'(?im)\b(setstage|startquest|stopquest|completequest|failquest|setobjectivecompleted|setobjectivedisplayed)\b')
RECRUIT_RE = re.compile(r'(?i)setplayerteammate|addtofaction|setfactionrank|removefromfaction|addreputation|setreputation|rewardkarma|setenemy|setally|setpcfactionenemy')
CHAR_RE = re.compile(r'(?i)setplayerteammate')


def dialogue(d):
    dials = {x['ref']: x for x in lib.gload('dials.json.gz')}
    rows = []
    for town, edid, role in lib.all_quests():
        q = d.by_edid[edid]
        for i in d.infos.get(q['ref'], []):
            conds = i['conditions']
            speaker = None
            for c in conds:
                if c['func'] == 'GetIsID':
                    speaker = edid_of(d, c['p1']) or c['p1']
            skill = [(AV_NAMES.get(c['p1'], str(c['p1'])), c['op'], c['value'], c.get('run_on')) for c in conds if c['func'] == 'GetActorValue' and isinstance(c['p1'], int)]
            rep = [c for c in conds if c['func'] in ('GetReputationThreshold', 'GetInFaction')]
            inv = [c for c in conds if c['func'] in ('GetItemCount', 'GetEquipped', 'HasPerk', 'GetIsSex', 'GetPCIsSex', 'GetIsClass')]
            scripts = []
            for k in ('begin', 'end'):
                s = i.get(k)
                if s and s.get('source', '').strip():
                    scripts.append((k, s['source']))
            src = '\n'.join(s for _, s in scripts)
            branch = bool(BRANCH_RE.search(src))
            recruit = bool(CHAR_RE.search(src))
            factionish = bool(RECRUIT_RE.search(src))
            if not (skill or branch or recruit or factionish or rep or inv):
                continue
            dial = dials.get(i['topic'])
            rows.append({
                'quest': edid, 'town': town, 'info': i['ref'], 'topic': i['topic'], 'topic_edid': dial.get('edid') if dial else None,
                'prompt': dial.get('name') if dial else None, 'speaker': speaker,
                'flags': i['flags'], 'line': (i['responses'][0]['text'] if i['responses'] else '')[:110],
                'skill_checks': skill, 'rep_faction_conds': [(c['func'], edid_of(d, c['p1']) if isinstance(c['p1'], str) else c['p1'], c['op'], c['value']) for c in rep],
                'player_conds': [(c['func'], edid_of(d, c['p1']) if isinstance(c['p1'], str) else c['p1'], c['op'], c['value']) for c in inv],
                'branches_quest': branch, 'recruit': recruit, 'faction_or_stat': factionish,
                'script_fns': sorted({m.lower() for m in re.findall(r'(?i)\b(setstage|startquest|stopquest|completequest|failquest|setobjectivecompleted|setobjectivedisplayed|setplayerteammate|addtofaction|setfactionrank|removefromfaction|addreputation|setreputation|rewardkarma|rewardxp|additem|removeitem|setenemy|setally|moveto|showbartermenu|startcombat)\b', src)}),
                'result_excerpt': ' ; '.join(l.strip() for l in src.replace('\r', '').split('\n') if l.strip() and not l.strip().startswith(';'))[:260],
            })
    return rows


# ------------------------------------------------------------------------------------------------ text-scan hazards
TEXT_HAZARDS = [
    ('timer', re.compile(r'(?i)\b(gamedayspassed|gamehour|getcurrenttime|getsecondspassed|gamedaypassed)\b')),
    ('essential', re.compile(r'(?i)\bset(essential|noessential|protected)\b')),
    ('radio', re.compile(r'(?i)\b(setradio|enableradio|radio\w*)\b')),
    ('ending', re.compile(r'(?i)\b(slideshow|endgame|setending)\b')),
    ('save', re.compile(r'(?i)\b(autosave|savegame|disablesave|enablesave)\b')),
]
KILL_ALL_RE = re.compile(r'(?i)\bkill (all|every|everyone)\b|wiped out')


def _scripts_of(q, infos):
    sd = q.get('script_data')
    if sd and sd.get('source', '').strip():
        yield 'quest script', sd['source'], q['edid']
    for s in q['stages']:
        for e in s['entries']:
            if e.get('script') and e['script'].get('source', '').strip():
                yield 'stage %d' % s['index'], e['script']['source'], '%s stage %d' % (q['edid'], s['index'])
    for i in infos:
        for k in ('begin', 'end'):
            if i.get(k) and i[k].get('source', '').strip():
                yield 'info %s %s' % (i['ref'].split(':')[1], k), i[k]['source'], '%s INFO %s %s' % (q['edid'], i['ref'].split(':')[1], k)


def text_hazards(d):
    """Hazards that are not function calls the oracle models: timers, essential flags, radio, endings, save control, kill-all objectives."""
    out = []
    for town, edid, role in lib.all_quests():
        q = d.by_edid[edid]
        for where, src, script in _scripts_of(q, d.infos.get(q['ref'], [])):
            for n, raw in enumerate(src.replace(chr(13), '').split(chr(10)), 1):
                line = raw.split(';')[0].strip()
                if not line:
                    continue
                for h, rx in TEXT_HAZARDS:
                    m = rx.search(line)
                    if m and not (h == 'timer' and m.group(1).lower() == 'timer' and 'ftimer' in line.lower()):
                        out.append({'quest': edid, 'town': town, 'hazard': h, 'script': script, 'where': where, 'line': n, 'text': line[:140]})
        for o in q['objectives']:
            if KILL_ALL_RE.search(o.get('text') or ''):
                out.append({'quest': edid, 'town': town, 'hazard': 'kill-all', 'script': '%s objective %d' % (edid, o['index']), 'where': 'objective',
                            'line': None, 'text': o['text'][:140]})
    return out


# ------------------------------------------------------------------------------------------------ dialogue specs (L8 style)
SPEC_PAIRS = [
    # id, pass INFO, fail INFO, short name
    ('towns_vms08_hsu_speech', '0EF962', '0EF963', 'Colonel Hsu "<Speech 60>" (I Put a Spell on You)'),
    ('towns_primm_beagle_speech', '0E501C', '0E501B', 'Deputy Beagle hostage speech (My Kind Of Town)'),
    ('towns_boulder_jessup_speech', '153C2B', '153C2C', 'Jessup "<Speech - 45>" free the hostages (Boulder City Showdown)'),
    ('towns_boulder_monroe_barter', '153C25', '153C26', 'Lt. Monroe "<Barter 45>" cheaper bribe (Boulder City Showdown)'),
    ('towns_primm_vancegun_science', '0E287F', '12060E', 'Primm Slim "What happened to Vance\'s Gun?" Science 50'),
]
SPEC_PRE = {   # minimal host console setup per spec (hex FormIDs); INFO quest-variable conditions beyond this are NOT solved here
    'towns_vms08_hsu_speech': [],
    'towns_primm_beagle_speech': ['setstage 0DA115 20'],
    'towns_boulder_jessup_speech': ['startquest 10E908', 'setstage 10E908 10'],
    'towns_boulder_monroe_barter': ['startquest 10E908', 'setstage 10E908 10'],
    'towns_primm_vancegun_science': [],
}
XP_RE = re.compile(r'(?i)\brewardxp\s+(\d+)')
STAGE_RE = re.compile(r'(?i)\bsetstage\s+(\w+)\s+(\d+)')
OBJC_RE = re.compile(r'(?i)\bsetobjectivecompleted\s+(\w+)\s+(\d+)\s+1')
VAR_RE = re.compile(r'(?im)^\s*set\s+(\w+\.\w+)\s+to\s+(-?\d+)')
CAPS_RE = re.compile(r'(?i)player\.removeitem\s+caps001\s+(\d+)')


def dialogue_specs(d):
    allinfo = {i['ref']: i for ii in d.infos.values() for i in ii}
    dials = {x['ref']: x for x in lib.gload('dials.json.gz')}
    quest_of = {}
    for q in d.quests['quests']:
        for i in d.infos.get(q['ref'], []):
            quest_of[i['ref']] = q['edid']
    placed = {}
    for r, f in d.forms.items():
        if f.get('base') and f['sig'] in ('ACHR', 'ACRE'):
            placed.setdefault(f['base'], (r, f.get('edid')))
    out = []
    for sid, ok, bad, title in SPEC_PAIRS:
        for variant, ref in (('pass', ok), ('fail', bad)):
            i = allinfo['FalloutNV.esm:' + ref]
            chk = [c for c in i['conditions'] if c['func'] == 'GetActorValue' and isinstance(c['p1'], int)][0]
            av = AV_NAMES[chk['p1']]
            npc_ref = [c['p1'] for c in i['conditions'] if c['func'] == 'GetIsID'][0]
            placed_ref, placed_edid = placed.get(npc_ref, (None, None))
            src = '\n'.join(((i.get(k) or {}).get('source') or '') for k in ('begin', 'end'))
            exp = {'check_evaluated_on': 'p1', 'converged': True}
            xp = XP_RE.findall(src)
            if xp:
                exp['xp_each_player'] = int(xp[0])
            st = {}
            for qn, n in STAGE_RE.findall(src):
                st[qn] = int(n)
            if st:
                exp['stage'] = st
            oc = {}
            for qn, n in OBJC_RE.findall(src):
                oc[qn] = int(n)
            if oc:
                exp['objective_completed'] = oc
            sv = {k: int(v) for k, v in VAR_RE.findall(src)}
            if sv:
                exp['script_var'] = sv
            caps = CAPS_RE.findall(src)
            if caps:
                exp['caps_removed'] = {'p1': int(caps[0]), 'host': 0, 'p2': 0}
            hi, lo = chk['value'] + 5, max(chk['value'] - 20, 1)
            p1v, hostv = (hi, lo) if variant == 'pass' else (lo, hi)
            if chk['op'] == '<':      # the info is the FAILED branch: invert which side clears the bar
                p1v, hostv = (lo, hi) if variant == 'fail' else (hi, lo)
            out.append({
                'schema': 1, 'kind': 'dialogue', 'id': '%s_%s' % (sid, variant), 'name': '%s (%s)' % (title, variant),
                'plugin': 'FalloutNV.esm',
                'summary': 'The initiator p1 has %s %d, host and p2 have %d: the check runs on p1 (INFO condition run_on=%s) and the %s branch plays; '
                           'quest consequences and XP are shared by every player.' % (av, p1v, hostv, chk['run_on'], variant.upper()),
                'tier': 'T2', 'players': {'host': 0, 'p1': 1, 'p2': 2}, 'quests': [quest_of[i['ref']]],
                'policy_checks': ['dialogue-ownership', 'speech-check-initiator', 'reward-both-vs-owner'],
                'setup': {'host': SPEC_PRE[sid]},
                'setup_note': 'minimal host setup only; the INFO quest-variable conditions (e.g. VMS20 var 35 == 0, VMS08 branch flags) are not solved here - L8 to confirm in game',
                'steps': [
                    {'id': 'prep', 'action': 'setup', 'players': {'p1': ['player.forceav %s %d' % (av.lower(), p1v)],
                                                                'host': ['player.forceav %s %d' % (av.lower(), hostv)],
                                                                'p2': ['player.forceav %s %d' % (av.lower(), hostv)]}},
                    {'id': 'talk_p1', 'action': 'talk', 'actor': 'p1', 'npc': placed_edid, 'npc_ref': placed_ref, 'greeting_info': None,
                     'greeting_info_note': 'unknown: the NPC GREETING INFO that opens the menu was not resolved here (L8 fills it from the dialogue recorder)',
                     'expect': {'opened': True, 'spectators': ['host', 'p2']}},
                    {'id': 'pick_p1', 'action': 'pick', 'actor': 'p1', 'topic': i['topic'],
                     'topic_edid': (dials.get(i['topic']) or {}).get('edid'), 'prompt': (dials.get(i['topic']) or {}).get('name'),
                     'info': i['ref'], 'line': i['responses'][0]['text'][:24],
                     'expect': dict(exp, spectator_lines=[i['responses'][0]['text'][:24]])},
                ],
                'invariants': ['no_duplicate_reward', 'one_conversation_per_npc', 'result_script_ran_once_on_host', 'host.shared == client.shared'],
                'oracle': 'docs/dialogue-design.md',
            })
    return out
