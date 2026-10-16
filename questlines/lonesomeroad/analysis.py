"""Static analysis of every Lonesome Road script: function uses -> gap table.

collect_scripts(data) -> list of script items (quest / stage / info / object / terminal / effect scripts, deduped)
extract_uses(item, data)  -> list of function-use dicts with exact (script, line) locations
build_gaps(data)          -> the gaps.json payload

No game is needed. Pure function of out/questdata/*.
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
import funcmap  # noqa: E402

DECL_RE = re.compile(r'(?i)^(short|long|int|float|ref|reference)\s+(\w+)')
SET_RE = re.compile(r'(?i)^set\s+([A-Za-z_]\w*)(?:\s*\.\s*([A-Za-z_]\w*))?\s+to\b')
BEGIN_RE = re.compile(r'(?i)^begin\s+(\w+)')
SUBJ_RE = re.compile(r'([A-Za-z_]\w*)\s*\.\s*$')

# severity / hazard overrides by (function, script label regex)
OVERRIDES = [
    ('showmessage', r'TempleLaunchTerminal', 'critical', ['killall', 'world-change'], 'nuke-target choice menu (4 branches)'),
    ('getbuttonpressed', r'TempleLaunchTerminal|NVDLC04MQ03b|NVDLC04MQ04', 'critical', ['killall'], 'answer read on the host machine only'),
    ('moveto', r'NukedLegionBoat', 'high', [], 'boat travel to the nuked Legion camp'),
    ('moveto', r'NVDLC04Ending', 'critical', ['ending', 'cinematic'], 'ending teleport to the slideshow "theatre"'),
    ('setdestroyed', r'TempleLaunch|EDEFinale|NVDLC04EDE', 'high', ['killall'], 'launch console / ED-E finale'),
    ('placeatme', r'MissileLaunch|MissileExplosion|EDEFinale|Warhead', 'high', ['cinematic'], 'story-beat explosion'),
    ('addperk', r'NVDLC04MQ03b|NVDLC04MQ04|GenericPerk', 'high', ['ending'], 'endgame perk award'),
    ('setessential', r'EDEFinale|UlyssesTempleExitDoor|UlyssesBattleCodebox', 'high', ['essential'], 'Ulysses / ED-E essential flag'),
    ('startconversation', r'Ulysses', 'critical', ['menu'], 'forced greeting that starts the Ulysses scene'),
    ('say', r'EDEFinale', 'high', ['cinematic'], 'ED-E last words'),
    ('sayto', r'Narrator|SlideShow|NVDLC04Ending', 'high', ['ending'], 'ending slides narration'),
    ('swaptexture', r'.', 'high', ['ending'], 'slideshow screen'),
]

CMDS = None


def commands():
    """Name vocabulary used to recognise function tokens (lowercase): engine command table + condition functions +
    the L3 lexicon + the functions this analysis maps."""
    global CMDS
    if CMDS is None:
        ct = json.load(open(os.path.join(common.REPO, 'plugin', 'hooks', 'tools', 're', 'command_table.json'), encoding='utf-8'))
        s = {k.lower() for k in ct}
        s |= {v[0].lower() for v in common.ctda_funcs.FUNCS.values()}
        s |= set(common.classify.L)
        s |= set(funcmap.F)
        s |= {'sayto', 'moveto', 'pcb', 'entertrigger', 'swaptexture', 'getbuttonpressed', 'showrecipemenu', 'addformtoformlist',
              'additemtoleveledlist', 'playsound3d', 'openteammatecontainer', 'incrementscriptedchallenge', 'addspecialpoints',
              'addtagskills', 'setinchargen', 'startconversation', 'markfordelete', 'modweaponhealthperc'}
        s -= funcmap.IGNORE
        CMDS = s
    return CMDS


def collect_scripts(data):
    """Every script in scope, deduped. kind: quest | stage | info | object | effect | terminal."""
    out = []
    seen_ref = set()
    qscript_owner = {q['script']: q['edid'] for q in data.quests if q.get('script')}
    scpt_by_obj = {id(r['script']): r for r in data.extra['scpt'].values()}
    # reachability: object scripts reached from a quest's dep_refs
    reach = collections.defaultdict(set)
    term_items = []
    for q in data.quests:
        for dr in q.get('dep_refs', ()):
            for label, sc in common.classify.dep_scripts(dr, data.extra):
                rec = scpt_by_obj.get(id(sc))
                if rec is not None:
                    reach[rec['ref']].add(q['edid'])
                else:
                    term_items.append((q['edid'], label, sc))
    for rec in data.lr_scripts:
        t = rec['script']['type']
        kind = {'quest': 'quest', 'effect': 'effect'}.get(t, 'object')
        owner = qscript_owner.get(rec['ref'])
        quests = sorted(reach.get(rec['ref'], set()) | ({owner} if owner else set()))
        out.append({'id': rec['edid'], 'label': rec['edid'], 'kind': kind, 'ref': rec['ref'], 'owner': owner, 'quests': quests,
                    'source': rec['script']['source'], 'reached': bool(quests)})
        seen_ref.add(rec['ref'])
    for q in data.quests:
        for s in q['stages']:
            for e in s['entries']:
                sc = e.get('script')
                if sc and sc.get('source'):
                    lab = '%s stage %d' % (q['edid'], s['index'])
                    out.append({'id': lab, 'label': lab, 'kind': 'stage', 'ref': None, 'owner': q['edid'], 'quests': [q['edid']],
                                'source': sc['source'], 'reached': True, 'stage': s['index']})
        for i in data.infos_by_quest.get(q['ref'], []):
            for k in ('begin', 'end'):
                sc = i.get(k)
                if sc and sc.get('source'):
                    lab = 'info %s %s' % (i['ref'], k)
                    out.append({'id': lab, 'label': lab, 'kind': 'info', 'ref': i['ref'], 'owner': q['edid'], 'quests': [q['edid']],
                                'source': sc['source'], 'reached': True, 'topic': i.get('topic')})
    done = set()
    for qe, label, sc in term_items:
        key = (label, sc.get('source'))
        if key in done or not sc.get('source'):
            continue
        done.add(key)
        out.append({'id': label, 'label': label, 'kind': 'terminal', 'ref': None, 'owner': None, 'quests': [qe],
                    'source': sc['source'], 'reached': True})
    return out


def _override(fn, label):
    for f, rx, sev, hz, note in OVERRIDES:
        if f == fn and re.search(rx, label):
            return sev, hz, note
    return None


def extract_uses(item, data):
    """List of {fn, line, text, block, subject, player} for one script item."""
    cmds = commands()
    qnames = {k.lower() for k in data.by_edid}
    gnames = {g['edid'].lower() for g in data.d['globals'] if g.get('edid')}
    src = item['source']
    decl = set()
    for n, line in common.script_lines(src):
        m = DECL_RE.match(line)
        if m:
            decl.add(m.group(2).lower())
    uses = []
    block = None
    for n, line in common.script_lines(src):
        mb = BEGIN_RE.match(line)
        if mb:
            block = mb.group(1)
            continue
        if re.match(r'(?i)^end\s*$', line):
            block = None
            continue
        if DECL_RE.match(line) or re.match(r'(?i)^(scn|scriptname)\b', line):
            continue
        ms = SET_RE.match(line)
        if ms:
            a, b = ms.group(1), ms.group(2)
            if b:
                if a.lower() in qnames:
                    fn = 'set questvar'
                elif a.lower() in gnames:
                    fn = 'set global'
                else:
                    fn = 'set objvar'
            elif a.lower() in gnames:
                fn = 'set global'
            elif a.lower() in decl:
                fn = 'set questvar' if item['kind'] in ('quest', 'stage') else 'set localvar'
            else:
                fn = 'set global' if a.lower() in gnames else 'set localvar'
            uses.append({'fn': fn, 'line': n, 'text': common.clean(data, line, 140), 'block': block, 'subject': common.mask(data, a) if b else None, 'player': False})
        for m in common.TOK.finditer(line):
            t = m.group(0)
            if t.startswith('"'):
                continue
            tl = t.lower()
            if tl in decl or tl not in cmds:
                continue
            if tl in ('set',):
                continue
            ms2 = SUBJ_RE.search(line[:m.start()])
            subj = ms2.group(1) if ms2 else None
            if ms and m.start() < line.lower().find(' to') and tl not in funcmap.F:
                continue
            uses.append({'fn': tl, 'line': n, 'text': common.clean(data, line, 140), 'block': block, 'subject': common.mask(data, subj),
                         'player': bool(subj and subj.lower() in common.classify.PLAYER_NAMES)})
    return uses


def entry_for(use, item):
    fn = use['fn']
    ent = funcmap.F.get(fn)
    if ent is None:
        return None
    if 'status' not in ent:
        ent = ent['player'] if use['player'] else ent['other']
    ent = dict(ent)
    ov = _override(fn, item['label'])
    if ov:
        sev, hz, note = ov
        ent['sev'] = sev
        ent['hazards'] = sorted(set(ent['hazards']) | set(hz))
        ent['override'] = note
    # Player-subject use of an implicit-player command
    if fn in ('additem', 'removeitem', 'additemns') and not use['player']:
        pass
    return ent


# block types that are entry points driven by a player action; a guest cannot cause them on the host
GUEST_ENTRY = {'onactivate': 'guest-activation', 'ontriggerenter': 'trigger', 'menumode': 'ui-local', 'sayto': 'sayto',
               'saytodone': 'sayto'}


def block_inventory(items):
    inv = collections.defaultdict(lambda: collections.Counter())
    act = []
    for it in items:
        if it['kind'] not in ('object', 'terminal', 'effect', 'quest'):
            continue
        cur = None
        buf = []
        for n, line in common.script_lines(it['source']):
            mb = BEGIN_RE.match(line)
            if mb:
                cur = mb.group(1)
                buf = []
                inv[it['kind']][cur.lower()] += 1
                continue
            if re.match(r'(?i)^end\s*$', line) and cur:
                if cur.lower() == 'onactivate':
                    body = '\n'.join(buf)
                    act.append({'script': it['label'], 'quests': it['quests'], 'player_gate': bool(re.search(r'(?i)isactionref\s*\(?\s*player', body)),
                                'mutates': bool(re.search(r'(?i)\b(setstage|set\s+\w+(\.\w+)?\s+to|enable|disable|moveto|startquest|additem|addperk|unlock|lock|showmessage|setdestroyed|playgroup)\b', body))})
                cur = None
                continue
            if cur:
                buf.append(line)
    return {k: dict(v) for k, v in inv.items()}, act


COND_CLASS = {
    'getisid': 'npc', 'getisvoicetype': 'npc', 'getdead': 'npc', 'getinfaction': 'npc', 'getrelationshiprank': 'npc',
    'getisrace': 'player-private', 'getissex': 'player-private', 'getisclass': 'npc',
    'getquestvariable': 'shared', 'getstage': 'shared', 'getstagedone': 'shared', 'getglobalvalue': 'shared',
    'getobjectivecompleted': 'shared', 'getobjectivedisplayed': 'shared', 'getquestcompleted': 'shared', 'getquestrunning': 'shared',
    'getreputationthreshold': 'shared', 'getscriptvariable': 'script-var', 'getrandompercent': 'random',
    'getactorvalue': 'player-private', 'getitemcount': 'player-private', 'hasperk': 'player-private', 'getequipped': 'player-private',
    'gethasnote': 'player-private', 'getlevel': 'player-private', 'getinsamecell': 'p-ref', 'getdistance': 'p-ref',
    'getincell': 'p-ref', 'getinworldspace': 'p-ref', 'getdisposition': 'player-private', 'gettalkedtopc': 'npc',
}


def condition_summary(data):
    cnt = collections.Counter()
    runon = collections.defaultdict(collections.Counter)
    for i in data.lr_infos:
        for c in i['conditions']:
            cnt[c['func']] += 1
            runon[c['func']][c.get('run_on', 'subject')] += 1
    out = []
    for fn, n in cnt.most_common():
        cls = COND_CLASS.get(fn.lower(), 'unclassified')
        if runon[fn].get('target') and cls in ('npc', 'unclassified'):
            cls = 'player-private'
        rule = {'npc': 'evaluated by the initiator on the NPC puppet (class 1)', 'shared': 'identical on every machine (class 2)',
                'player-private': 'evaluated against the initiator on the initiator\'s machine (class 3)',
                'script-var': 'needs SetScriptVar replication of NPC script vars (class 4)', 'p-ref': 'PartyAwareCond reduction',
                'random': 'resolved once by the initiator', 'unclassified': 'unknown: not in the analysis table'}[cls]
        out.append({'func': fn, 'count': n, 'class': cls, 'run_on': dict(runon[fn]), 'rule': rule})
    return out


ORACLE_STATUS = {'SetStage': 'supported', 'SetObjective': 'supported', 'ClearObjective': 'supported', 'StartQuest': 'supported',
                 'StopQuest': 'supported', 'CompleteQuest': 'supported', 'FailQuest': 'supported', 'EnableRef': 'supported',
                 'DisableRef': 'supported', 'AddItem': 'supported', 'RemoveItem': 'supported', 'ModActorValue': 'supported',
                 'ModReputation': 'supported', 'ModKarma': 'supported', 'SetGlobal': 'supported', 'SetScriptVar': 'supported',
                 'KillActor': 'supported', 'ResurrectActor': 'supported', 'MoveRef': 'partial'}


def oracle_effect_summary(data):
    cnt = collections.Counter()
    for q in data.quests:
        eq = data.effects_native['quests'].get(q['edid'])
        if not eq:
            continue
        for st, v in eq['stages'].items():
            for cond in ('unconditional', 'conditional'):
                for e in v.get(cond, []):
                    cnt[e['type']] += 1
    return [{'type': t, 'count': n, 'status': ORACLE_STATUS.get(t, 'unknown')} for t, n in cnt.most_common()]


GAP = ('partial', 'missing')
SEV_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}


def build_gaps(data):
    items = collect_scripts(data)
    funcs = {}
    unmapped = collections.defaultdict(list)
    by_quest = collections.defaultdict(lambda: {'uses': 0, 'gaps': collections.Counter(), 'functions': collections.Counter()})
    total_uses = 0
    for it in items:
        for u in extract_uses(it, data):
            ent = entry_for(u, it)
            loc = {'script': it['label'], 'kind': it['kind'], 'line': u['line'], 'block': u['block'], 'subject': u['subject'],
                   'text': u['text'], 'sev': None}
            if ent is None:
                unmapped[u['fn']].append(loc)
                continue
            total_uses += 1
            loc['sev'] = ent['sev']
            key = u['fn']
            f = funcs.setdefault(key, {'fn': key, 'entries': collections.Counter(), 'locations': [], 'quests': set()})
            f['entries'][json.dumps({k: ent[k] for k in ('effect', 'capture', 'apply', 'status', 'hazards', 'note')}, sort_keys=True)] += 1
            loc['status'] = ent['status']
            loc['reached'] = it['reached']
            loc['hazards'] = ent['hazards']
            f['locations'].append(loc)
            f['quests'].update(it['quests'])
            for qn in it['quests'] or ['(unreached)']:
                b = by_quest[qn]
                b['uses'] += 1
                b['functions'][key] += 1
                if ent['status'] in GAP:
                    b['gaps'][ent['sev']] += 1
    fl = []
    for key, f in funcs.items():
        statuses = collections.Counter(l['status'] for l in f['locations'])
        sevs = collections.Counter(l['sev'] for l in f['locations'])
        # representative entry: the most frequent
        rep = json.loads(f['entries'].most_common(1)[0][0])
        notes = []
        for k_, _n in f['entries'].most_common():
            nt = json.loads(k_)['note']
            if nt and nt not in notes:
                notes.append(nt)
        rep['note'] = ' // '.join(notes[:2])
        rep_status = rep['status']
        worst_sev = min(sevs, key=lambda s: SEV_ORDER[s])
        fl.append({'fn': key, 'effect': rep['effect'], 'capture': rep['capture'], 'apply': rep['apply'], 'status': rep_status,
                   'statuses': dict(statuses), 'sev': worst_sev, 'sev_counts': dict(sevs), 'hazards': sorted({h for l in f['locations'] for h in l['hazards']}),
                   'note': rep['note'], 'count': len(f['locations']), 'scripts': len({l['script'] for l in f['locations']}),
                   'quests': sorted(f['quests']), 'locations': f['locations']})
    fl.sort(key=lambda x: (SEV_ORDER[x['sev']], -x['count'], x['fn']))
    status_counts = collections.Counter()
    sev_gap = collections.Counter()
    for x in fl:
        for st, n in x['statuses'].items():
            status_counts[st] += n
    for x in fl:
        for l in x['locations']:
            if l['status'] in GAP:
                sev_gap[l['sev']] += 1
    blocks, activates = block_inventory(items)
    hazards = collections.defaultdict(lambda: {'count': 0, 'functions': collections.Counter(), 'locations': []})
    for x in fl:
        for l in x['locations']:
            for h in l['hazards']:
                hz = hazards[h]
                hz['count'] += 1
                hz['functions'][x['fn']] += 1
                if len(hz['locations']) < 12 and SEV_ORDER[l['sev']] <= 2:
                    hz['locations'].append({'script': l['script'], 'line': l['line'], 'fn': x['fn'], 'sev': l['sev']})
    # guest-originated activation pseudo-gap
    act_gate = [a for a in activates if a['player_gate']]
    return {
        'schema': 1,
        'plugin': common.PLUGIN,
        'doc': 'Lonesome Road function/effect gap map. status: supported | partial | missing | host-only | read | presentation (see funcmap.py).',
        'summary': {
            'scripts': len(items), 'scripts_by_kind': dict(collections.Counter(i['kind'] for i in items)),
            'unreached_object_scripts': sum(1 for i in items if not i['reached']),
            'function_uses_mapped': total_uses, 'distinct_functions': len(fl),
            'uses_by_status': dict(status_counts), 'gap_uses_by_severity': {k: sev_gap.get(k, 0) for k in SEV_ORDER},
            'unmapped_functions': {k: len(v) for k, v in unmapped.items()},
            'onactivate_blocks': len(activates), 'onactivate_player_gated': len(act_gate),
            'onactivate_player_gated_mutating': sum(1 for a in act_gate if a['mutates']),
        },
        'functions': fl,
        'unmapped': [{'fn': k, 'count': len(v), 'locations': v[:20]} for k, v in sorted(unmapped.items())],
        'blocks': blocks,
        'onactivate_player_gated': act_gate,
        'hazards': {k: {'count': v['count'], 'functions': dict(v['functions'].most_common()), 'locations': v['locations']}
                    for k, v in sorted(hazards.items())},
        'conditions': condition_summary(data),
        'oracle_effect_types': oracle_effect_summary(data),
        'by_quest': {k: {'uses': v['uses'], 'gaps_by_severity': dict(v['gaps']), 'top_functions': dict(v['functions'].most_common(12))}
                     for k, v in sorted(by_quest.items())},
    }


if __name__ == '__main__':
    d = common.Data()
    g = build_gaps(d)
    print(json.dumps(g['summary'], indent=1))
    print([x['fn'] for x in g['functions'][:30]])
    print(g['unmapped'])
