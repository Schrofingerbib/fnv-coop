"""Generate every deliverable of the `systems` questline.   python -I questlines/systems/build.py
All builders are pure functions returning data (so run_tests.py can validate them and run mutation twins);
main() writes the files into questlines/systems/ and internal questline notes (status is written by write_status.py).
"""
import collections
import importlib.util
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import census  # noqa: E402
import qlib  # noqa: E402
import scope  # noqa: E402

OUT = qlib.HERE
LOADORDER = os.path.join(qlib.ROOT, 'tools', 'questdata', 'loadorder.json')

_cov = {}


def covrules():
    if 'm' not in _cov:
        spec = importlib.util.spec_from_file_location('covrules', os.path.join(qlib.ROOT, 'tools', 'coverage', 'rules.py'))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _cov['m'] = m
    return _cov['m']


def plugin_index():
    return {p: i for i, p in enumerate(json.load(open(LOADORDER, encoding='utf-8'))['plugins'])}


def hexid(ref):
    """'Plugin.esm:17BA15' -> runtime FormID '0017BA15' (load-order prefix from tools/questdata/loadorder.json)."""
    plugin, local = ref.rsplit(':', 1)
    return '%02X%06X' % (plugin_index()[plugin], int(local, 16) & 0xFFFFFF)


def resolver():
    """lowercase EditorID -> FormRef string, over quests.json + forms.json.gz + refnames.json.gz."""
    d = {}
    for q in qlib.quests():
        d[q['edid'].lower()] = q['ref']
    for src in (qlib.forms(), qlib.refnames()):
        for ref, v in src.items():
            if v.get('edid'):
                d.setdefault(v['edid'].lower(), ref)
    return d


# ---------------------------------------------------------------- inventory
def stage_logs(q):
    out = {}
    for st in q['stages']:
        t = [e.get('text') for e in st['entries'] if e.get('text')]
        out[st['index']] = t[0] if t else None
    return out


def build_inventory(recs=None):
    recs = recs or scope.build_scope()
    Q = qlib.quest_by_edid()
    R = qlib.risk()
    edges = qlib.questgraph()['edges']
    dep_on, dep_by = collections.defaultdict(set), collections.defaultdict(set)
    for e in edges:
        if e.get('strength') == 'hard' and e['src'] != e['dst']:
            dep_on[e['src']].add(e['dst'])
            dep_by[e['dst']].add(e['src'])
    quests = []
    for r in recs:
        q = Q[r['edid']]
        logs = stage_logs(q)
        scripted = {st['index']: any(e.get('script') for e in st['entries']) for st in q['stages']}
        conds = collections.Counter(c['func'] for c in q['conditions'])
        scond = sorted({c['func'] for st in q['stages'] for e in st['entries'] for c in e['conditions']})
        quests.append({
            'edid': r['edid'], 'formid': hexid(q['ref']), 'ref': q['ref'], 'name': q.get('name'), 'plugin': q['plugin'],
            'graph_line': r['graph_line'], 'own_line': r['own_line'], 'overlap_with_other_line': r['overlap'],
            'tier': r['tier'], 'risk_score': r['score'], 'type': r['type'], 'policy': r['policy'],
            'policy_reason': r['policy_reason'], 'companion': r['companion'],
            'start_enabled': r['start_enabled'], 'flags': q['flags'], 'quest_script': q.get('script'),
            'stages': [{'index': i, 'log': logs[i], 'has_script': scripted[i]} for i in r['stages']],
            'objectives': [{'index': o['index'], 'text': o.get('text')} for o in q['objectives']],
            'prerequisites': {
                'start': 'start_enabled' if r['start_enabled'] else 'startquest <formid> (manual / started by another quest script)',
                'quest_condition_funcs': dict(conds), 'stage_condition_funcs': scond,
                'hard_depends_on': sorted(dep_on.get(r['edid'], ())), 'hard_depended_by': sorted(dep_by.get(r['edid'], ())),
            },
            'dialogue': {'topics': r['topics'], 'infos': r['infos'], 'infos_with_result_scripts': r['result_scripts']},
            'risk_tier': r['tier'], 'in_rules_json': r['in_rules_json'],
        })
    n_st = sum(len(x['stages']) for x in quests)
    return {'schema': 1, 'slug': 'systems',
            'scope': {'own_graph_lines': list(scope.MY_LINES),
                      'companion_overlap': 'companion quests on other graph lines are included with overlap_with_other_line=true',
                      'quests': len(quests), 'stages': n_st,
                      'overlap_quests': sorted(x['edid'] for x in quests if x['overlap_with_other_line'])},
            'quests': quests}


# ---------------------------------------------------------------- types
def build_types(recs):
    by = collections.defaultdict(list)
    for r in recs:
        by[r['type']].append(r)
    table = []
    for t in sorted(by, key=lambda t: -len(by[t])):
        pol = collections.Counter(x['policy'] for x in by[t])
        table.append({'type': t, 'quests': len(by[t]), 'policies': dict(pol),
                      'examples': [x['edid'] for x in by[t][:6]]})
    return {'schema': 1, 'policy_definitions': POLICY_DEFS, 'types': table,
            'quests': [{k: r[k] for k in ('edid', 'name', 'type', 'policy', 'policy_reason', 'tier', 'graph_line',
                                          'overlap')} for r in recs]}


POLICY_DEFS = {
    'host-only': 'The quest script and its INFO/stage results run only on the host (guest gate: quest-owned scripts never run on a guest). '
                 'Its variables are not journal-visible; consequences replicate only as the EffectTxns it causes.',
    'shared': 'Party-visible story state: stages, objectives, quest vars and dialogue results are one canonical copy in the story log '
              '(stage = max, flags = OR, vars host-wins) and every player sees journal/HUD.',
    'per-player': 'Each player machine keeps its own copy of the state (and may need to RUN the script locally); never merged into the '
                  'story log. Results that mutate the shared world still go through the host as txns.',
    'exclude': 'Not loaded or synced (owner decision: debug quests are excluded).',
}


# ---------------------------------------------------------------- companions
RECRUIT_RE = re.compile(r'SetPlayerTeammate\s+1', re.I)
DISMISS_RE = re.compile(r'SetPlayerTeammate\s+0|Hired\s+to\s+0', re.I)


def comp_token_re(name):
    c = scope.COMPANIONS[name]
    toks = list(c['refs']) + [c['flags']]
    if c.get('npc'):
        toks.append(c['npc'])
    return re.compile('|'.join(re.escape(t) for t in toks), re.I)


def build_companions():
    Q = qlib.quest_by_edid()
    qref = {q['ref']: q['edid'] for q in qlib.quests()}
    import gzip
    with gzip.open(os.path.join(qlib.QD, 'dials.json.gz'), 'rt', encoding='utf-8') as f:
        dials = {x['ref']: x for x in json.load(f)}
    res = resolver()
    out = {'schema': 1, 'follower_limit': {}, 'companions': {}, 'system_quests': {}, 'hazards': []}
    infos = qlib.infos()
    for name, c in scope.COMPANIONS.items():
        rx = comp_token_re(name)
        ev = {'recruit': [], 'dismiss': [], 'perk_add': [], 'perk_remove': []}
        for i in infos:
            for k in ('begin', 'end'):
                s = i.get(k)
                if not s:
                    continue
                src = s['source']
                if not rx.search(src):
                    continue
                d = dials.get(i['topic'], {})
                row = {'info': i['formid'], 'on': k, 'quest': qref.get(i['quest']), 'topic': d.get('edid'),
                       'line': (i['responses'][0]['text'][:70] if i['responses'] else '')}
                if RECRUIT_RE.search(src) or re.search(re.escape(c['flags']) + r'\s+to\s+1', src, re.I):
                    if re.search(re.escape(c['flags']) + r'\s+to\s+1|SetPlayerTeammate\s+1', src, re.I):
                        ev['recruit'].append(row)
                if DISMISS_RE.search(src) and rx.search(src):
                    ev['dismiss'].append(row)
                if re.search(r'player\.addperk\s+%s\b' % c['perk'], src, re.I):
                    ev['perk_add'].append(row)
                if re.search(r'player\.removeperk\s+%s\b' % c['perk'], src, re.I):
                    ev['perk_remove'].append(row)
        out['companions'][name] = {
            'kind': c['kind'], 'refs': {r: res.get(r.lower()) for r in c['refs']},
            'npc_base': ({c['npc']: res.get(c['npc'].lower())} if c.get('npc') else None),
            'recruit_perk_on_player': {c['perk']: res.get(c['perk'].lower())},
            'hired_flag_in_VNPCFollowers': c['flags'], 'home_marker': c['home'],
            'home_marker_ref': res.get(c['home'].lower()) if c['home'] else None,
            'dialogue_quests': c['dialogue'], 'personal_quests': c['personal'], 'timer_quests': c['timers'],
            'personal_quest_note': ('no stage-bearing personal quest found in the data (unknown): all Raul content is dialogue-only in VDialogueRaul'
                                    if not c['personal'] else None),
            'counts': {k: len(v) for k, v in ev.items()},
            'evidence': {k: v[:12] for k, v in ev.items()},
        }
    vn = Q['VNPCFollowers']['script_data']['source']
    out['follower_limit'] = {
        'mechanism': 'quest vars on VNPCFollowers: bHumanoidInParty (max 1 humanoid) + bCritterInParty (max 1 critter; ED-E and Rex) '
                     '=> vanilla cap is 2 companions (1 humanoid + 1 critter); nCurrentFollowers/bPlayerHasFollower track the count',
        'evidence_vars': [v for v in ('bHumanoidInParty', 'bCritterInParty', 'nCurrentFollowers', 'bPlayerHasFollower') if v in vn],
        'coop_hazard': 'the vars are ONE shared counter => the whole party shares 1 humanoid + 1 critter. Per-player caps would need '
                       'per-owner counters; see policy_review.md "Companions".',
    }
    out['system_quests'] = {e: {'ref': Q[e]['ref'], 'name': Q[e].get('name'), 'plugin': Q[e]['plugin'],
                                'infos': len(Q[e]['infos'])} for e in scope.COMPANION_SYSTEM}
    vref = Q['VNPCFollowers']['ref']
    cnt, prompt = collections.Counter(), {}
    for i in infos:
        if i['quest'] == vref:
            d = dials.get(i['topic'], {})
            if (d.get('edid') or '').startswith('Followers'):
                cnt[d['edid']] += 1
                prompt[d['edid']] = d.get('name')
    out['wheel_topics'] = {'quest': 'VNPCFollowers', 'note': 'the companion wheel entries are VNPCFollowers topics (player-choice, speaker = the companion): '
                           'hire / fire / tactics (distance, combat style, weapon) / wait / follow / Lucky 38 suite. Each entry runs its INFO end script on the host.',
                           'topics': [{'topic': k, 'infos': v, 'prompt': prompt[k]} for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])]}
    out['auto_fire'] = {
        'what': 'VNPCFollowers GameMode: a WAITING companion is fired (SetPlayerTeammate 0, MoveTo home, perk removed, faction removed) when '
                'GameDaysPassed >= fFollowerWaitingLeaveDay and <Companion>.GetInSameCell Player == 0',
        'hazard': 'Player = one machine; with N players the check must be "in the same cell as ANY player of the owner" (PartyAwareCond '
                  'getinsamecell=any) or companions waiting near player B are fired because the host player left',
    }
    out['hazards'] = [
        'player.AddPerk/RemovePerk on recruit/dismiss (8 perks) is a per-player effect on a host-run script: needs SetPerk capture with Recipient=owner (L5 BLOCK_CMD only)',
        'Player.AddToFaction/RemoveFromFaction VBooneFaction: faction membership is SHARED (owner decision) => replicate via SetFactionRank (no capture yet)',
        'SetPlayerTeammate is not hooked (no engine address): CompanionOwner never fires; NV:MP ghosts the companion NPC',
        'companion MoveTo <home marker> on dismiss and ED-E/Rex/Lily MoveTo are ordinary MoveRef (partial)',
        'VNPCFollowers GameMode confiscates/restores companion weapons (bWeaponCheck, Fort/Tops/Gomorrah frisk) using Player cell state',
        'VEnding disables all 11 companion refs for the slideshow (Disable captured; fine) then moves the PLAYER (PartyWarp)',
        'NVDLC01NewVegasFollowerFireQuest fires all companions entering Dead Money; P04CompanionFireQuest is the same for the Dismissal Terminal',
    ]
    return out


# ---------------------------------------------------------------- census + gaps
HAZARD_FN = {
    'player-effect': 'addperk removeperk addtofaction removefromfaction setfactionrank addreputation setreputation rewardkarma rewardxp '
                     'setactorvalue modactorvalue forceactorvalue damageactorvalue restoreactorvalue addnote removenote '
                     'addachievement incrementscriptedchallenge setplayertagskill addspecialpoints setpcenemyoffaction'.split(),
    'reward': 'rewardxp rewardkarma addreputation additem addnote addperk'.split(),
    'spawn': ['placeatme', 'placeleveledactoratme'],
    'companion': ['setplayerteammate', 'openteammatecontainer'],
    'essential': ['setessential', 'setactorrefessential'],
    'teleport': ['movetomarker', 'movetomarkerwithfade', 'setpos', 'setangle'],
    'cinematic': ['disableplayercontrols', 'enableplayercontrols', 'enablecontrol', 'disablecontrol', 'applyimagespacemodifier',
                  'removeimagespacemodifier', 'playbink', 'setinchargen', 'forceweather', 'setweather', 'setglobaltimemultiplier'],
    'menu': 'showbartermenu showcaravanmenu showrepairmenu showrecipemenu showtraitmenu showbarbermenu showplasticsurgeonmenu '
            'showroulettemenuparams showblackjackmenuparams showslotmachinemenuparams showlovetestermenuparams showsleepwaitmenu '
            'showtutorialmenu showracemenu showmessage showmap'.split(),
    'timer': ['setpcsleephours', 'wakeuppc', 'forcesave'],
    'radio': ['pipboyradiooff', 'pipboyradio', 'setnpcradio', 'forceradiostationupdate', 'startradioconversation'],
    'ending': ['setinchargen', 'exitgame', 'playbink'],
    'save': ['autosave', 'systemsave', 'forcesave'],
    'travel': ['enablefasttravel'],
    'kill': ['killactor', 'resurrectactor'],
}
FN_HAZARD = collections.defaultdict(list)
for _h, _fns in HAZARD_FN.items():
    for _f in _fns:
        FN_HAZARD[_f].append(_h)


def lex_level(canon, is_player):
    L = census.lexicon_cat(canon)
    if not L:
        return None, None
    return L[0], (L[2] if is_player else L[1])


def severity(status, level, hazards):
    if status == census.SUP:
        return 'none'
    if status == census.LOC:
        return 'info'
    hz = set(hazards)
    if hz & {'companion', 'spawn', 'cinematic', 'save', 'ending', 'essential'} or (level or 0) >= 4:
        return 'blocker'
    if (level or 0) >= 3 or hz & {'player-effect', 'reward', 'teleport', 'menu', 'travel', 'kill', 'timer', 'radio'}:
        return 'major'
    if (level or 0) >= 2:
        return 'minor'
    return 'low'


SUBJECT_SENSITIVE = set('additem removeitem setactorvalue modactorvalue forceactorvalue damageactorvalue restoreactorvalue addperk removeperk '
                        'addtofaction removefromfaction setfactionrank movetomarker movetomarkerwithfade setpos equipitem'.split())


def build_census(recs):
    rows, scope_locs, meta = census.run({r['edid']: r['ref'] for r in recs})
    out_rows = []
    for canon, v in rows.items():
        eff, cap, app, st, note = census.status_of(canon)
        cat, lvl = lex_level(canon, False)
        _, lvlp = lex_level(canon, True)
        hz = FN_HAZARD.get(canon, [])
        out_rows.append({
            'fn': canon, 'count': v['count'], 'scripts': len(v['scripts']), 'by_script_kind': v['by_kind'],
            'effect': eff, 'l5_capture': cap, 'l5_apply': app, 'l5_status': st, 'category': cat, 'level': lvl,
            'level_if_player': lvlp, 'hazards': hz, 'severity': severity(st, max(lvl or 0, lvlp or 0), hz),
            'note': note, 'samples': v['samples'],
        })
    out_rows.sort(key=lambda r: -r['count'])
    by_status = collections.Counter()
    for r in out_rows:
        by_status[r['l5_status']] += r['count']
    backlog = [r for r in out_rows if r['l5_status'] in (census.MIS, census.PAR)]
    prio = {'blocker': 3, 'major': 2, 'minor': 1, 'low': 0, 'info': 0, 'none': 0}
    backlog.sort(key=lambda r: (-prio[r['severity']], -r['count']))
    return {
        'schema': 1, 'meta': {k: v for k, v in meta.items() if k != 'garbage_tokens'},
        'garbage_tokens_folded': sum(meta['garbage_tokens'].values()),
        'distinct_functions': len(out_rows), 'calls_by_status': dict(by_status),
        'rows': out_rows,
        'hook_backlog': [{'rank': i + 1, 'fn': r['fn'], 'count': r['count'], 'effect': r['effect'], 'status': r['l5_status'],
                          'severity': r['severity'], 'hazards': r['hazards'], 'what': r['l5_capture'],
                          'note': r['note']} for i, r in enumerate(backlog)],
    }, scope_locs


def build_gaps(recs, scope_locs, cen):
    status = {r['fn']: r for r in cen['rows']}
    Qr = {r['edid']: r for r in recs}
    items = []
    per_quest = collections.defaultdict(lambda: collections.Counter())
    for canon, locs in scope_locs.items():
        s = status[canon]
        if s['l5_status'] == census.SUP:
            continue
        for lc in locs:
            is_player = (lc.get('subj') or '').lower() in ('player', 'playerref')
            hz = list(FN_HAZARD.get(canon, []))
            if canon in SUBJECT_SENSITIVE and not is_player:
                hz = [h for h in hz if h not in ('teleport', 'player-effect', 'reward')]
            elif canon in SUBJECT_SENSITIVE and is_player and 'player-effect' not in hz and canon not in ('movetomarker', 'movetomarkerwithfade', 'setpos'):
                hz.append('player-effect')
            _, lvl = lex_level(canon, is_player)
            items.append({'quest': lc['quest'], 'policy': Qr[lc['quest']]['policy'], 'fn': canon, 'raw': lc['fn'],
                          'subject': lc.get('subj'), 'where': lc['where'], 'script': lc['owner'], 'line': lc['line'],
                          'text': lc['text'], 'effect': s['effect'], 'l5_status': s['l5_status'], 'hazards': hz,
                          'severity': severity(s['l5_status'], lvl or 0, hz)})
            per_quest[lc['quest']][s['l5_status']] += 1
    fn_totals = collections.defaultdict(lambda: {'count': 0, 'quests': set()})
    for it in items:
        fn_totals[it['fn']]['count'] += 1
        fn_totals[it['fn']]['quests'].add(it['quest'])
    sevrank = {'blocker': 3, 'major': 2, 'minor': 1, 'low': 0, 'info': 0, 'none': 0}
    worst = {}
    for it in items:
        if it['fn'] not in worst or sevrank[it['severity']] > sevrank[worst[it['fn']]]:
            worst[it['fn']] = it['severity']
    summary = [{'fn': k, 'count': v['count'], 'quests': len(v['quests']), 'effect': status[k]['effect'],
                'l5_status': status[k]['l5_status'], 'severity': worst[k], 'hazards': status[k]['hazards']}
               for k, v in fn_totals.items()]
    summary.sort(key=lambda r: (-{'blocker': 3, 'major': 2, 'minor': 1, 'low': 0, 'info': 0}[r['severity']], -r['count']))
    sev = collections.Counter(i['severity'] for i in items)
    return {'schema': 1, 'scope_scripts': 'quest script + stage result scripts + INFO begin/end of the in-scope quests',
            'counts': {'unsupported_calls': len(items), 'by_severity': dict(sev),
                       'by_status': dict(collections.Counter(i['l5_status'] for i in items))},
            'by_function': summary, 'items': items,
            'per_quest': {k: dict(v) for k, v in per_quest.items()}}


# ---------------------------------------------------------------- sweep
def build_sweep(recs):
    Q = qlib.quest_by_edid()
    R = qlib.risk()
    quests, exclusions, stageless = [], [], []
    for r in recs:
        q = Q[r['edid']]
        rk = R[q['ref']]
        if r['policy'] == 'exclude':
            exclusions.append({'edid': r['edid'], 'reason': r['policy_reason'], 'stages': r['stages']})
            continue
        if not r['stages']:
            stageless.append({'edid': r['edid'], 'type': r['type'], 'policy': r['policy'], 'infos': r['infos'],
                              'reason': 'no stages: nothing for setstage to drive (quest-script/INFO behaviour is covered by the dialogue scenarios and the L5 suppression report, not the stage sweep)'})
            continue
        sf = collections.defaultdict(list)
        for f in rk['stage_findings']:
            m = re.match(r'stage (\d+)', f.get('where') or '')
            if m:
                sf[int(m.group(1))].append({'cat': f['cat'], 'fn': f['fn'], 'level': f['level'], 'text': f['text']})
        logs = stage_logs(q)
        scripted = {st['index']: any(e.get('script') for e in st['entries']) for st in q['stages']}
        h = hexid(q['ref'])
        setup = []
        if 'start_enabled' not in q['flags']:
            setup.append('startquest %s' % h)
        for who in ([r['companion']['who']] if r['companion'] and r['companion']['who'] in scope.COMPANIONS else []):
            fl = scope.COMPANIONS[who]['flags']
            vn = hexid(Q['VNPCFollowers']['ref'])
            setup += ['set %s.%s to 1' % (vn, fl), 'set %s.bPlayerHasFollower to 1' % vn]
        stages = []
        for st in q['stages']:
            i = st['index']
            stages.append({'index': i, 'log': logs[i], 'flags': [], 'has_script': scripted[i],
                           'cmds': ['setstage %s %d' % (h, i)], 'findings': sf.get(i, []), 'objective_effects': []})
        quests.append({'edid': r['edid'], 'ref': q['ref'], 'formid': h, 'name': q.get('name'), 'role': r['type'],
                       'tier': rk['tier'], 'tier_direct': rk['tier_direct'], 'cats': rk['cats'], 'priority': q['priority'],
                       'flags': q['flags'], 'script': q.get('script'), 'policy': r['policy'], 'setup': setup,
                       'setup_note': 'clean-state prerequisites; stage jumps need no earlier stages (console setstage). Companion setup sets the VNPCFollowers hired flag only - '
                                     'the ref-level recruit (SetPlayerTeammate) cannot be driven from the console without prid, so L7 must treat companion stages as best-effort',
                       'stages': stages})
    probes = build_probes()
    n_st = sum(len(x['stages']) for x in quests)
    return {'milestone': 'Systems (unassigned buckets + companions)', 'core': {x['edid']: x['name'] for x in quests if x['role'] == 'companion_personal'},
            'system_quests': [x['edid'] for x in quests if x['role'] != 'companion_personal'],
            'touched_quests': [], 'format': 'm1_goodsprings.json compatible (quests[].stages[].index) + setup/cmds/exclusions/stageless/probes',
            'summary': {'quests_with_stages': len(quests), 'stages': n_st, 'excluded_quests': len(exclusions),
                        'stageless_quests': len(stageless)},
            'quests': quests, 'exclusions': exclusions, 'stageless': stageless, 'probes': probes}


def build_probes():
    Q = qlib.quest_by_edid()
    vn = hexid(Q['VNPCFollowers']['ref'])
    pr = []
    for name, c in scope.COMPANIONS.items():
        pr.append({'id': 'recruit_%s' % name.lower().replace('-', ''), 'goal': 'quest-var level recruit state for %s' % name,
                   'cmds': ['set %s.%s to 1' % (vn, c['flags']), 'set %s.bPlayerHasFollower to 1' % vn],
                   'observe': 'snapshot VNPCFollowers vars; expect host == client after replication. UNVERIFIED console syntax (<hexid>.var)'})
    pr.append({'id': 'dismiss_all', 'goal': 'P04CompanionFireQuest fire-all path', 'cmds': ['startquest %s' % hexid(Q['P04CompanionFireQuest']['ref']),
                                                                                              'set %s.bFireCompanions to 1' % hexid(Q['P04CompanionFireQuest']['ref'])],
               'observe': 'hired flags back to 0; MoveRef to home markers; perks removed (NOT captured today: SetPerk gap)'})
    return pr
