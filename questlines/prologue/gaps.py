"""Capture/apply gap analysis for the prologue questline -> gaps.json + gaps.md (built by build.py)."""
import collections
import re

import funcmap
import model

# player-bound reads (script conditions / expressions that look at "the player"): name -> party reduction proposal
# (reductions use the vocabulary of docs/quest-policies/rules.json PartyAwareCond)
READS = {
    'getdistance': 'min-over-party', 'getincell': 'entry:all-in,exit:any-in', 'getinsamecell': 'any',
    'isactionref': 'initiator', 'getactionref': 'initiator', 'iskiller': 'any-party-member',
    'getinworldspace': 'any', 'getav': 'initiator', 'getactorvalue': 'initiator', 'getequipped': 'initiator',
    'gethasnote': 'any-party-member', 'getitemcount': 'initiator-or-party-sum', 'getdetected': 'any-party-member',
    'isplayerinregion': 'any', 'getplayerteammate': 'any-party-member', 'getisreference': 'initiator',
    'getdisposition': 'initiator', 'getpcmiscstat': 'initiator', 'getisplayer': 'initiator',
    'getlineofsight': 'any', 'getshouldattack': 'any-party-member', 'getbuttonpressed': 'initiator',
}
READ_RE = re.compile(r'\b(' + '|'.join(sorted(READS, key=len, reverse=True)) + r')\b', re.I)
PLAYER_ONLY_READS = {'gethasnote', 'isplayerinregion', 'isactionref', 'getactionref', 'getplayerteammate', 'getbuttonpressed'}

# BRIEF.md hazard checklist: (tag, description, regex over the script source). Zero hits are reported on purpose.
CHECKLIST = [
    ('player-targeted-reward', 'player-targeted effects: rewards, perks, karma/rep/fame',
     r'\b(rewardxp|rewardkarma|addreputation|addperk|removeperk|addcaps|addnote|setpcfame|setpcinfamy)\b'),
    ('spawn', 'spawns (PlaceAtMe)', r'\bplaceatme(object)?\b'),
    ('companion', 'companions / followers', r'\b(setplayerteammate|openteammatecontainer|addtofaction\s+playerfaction|beaglefollow|vnpcfollowers)\b'),
    ('essential', 'essential flags', r'\bsetessential\b'),
    ('move', 'MoveTo / teleports', r'\b(moveto|movetomarker|movetofade|setpos|centeroncell|fasttravel)\b'),
    ('control', 'cinematics / player controls', r'\b(disableplayercontrols|enableplayercontrols|playbink|startcinematic|setcameraon|forceplayercamera|fadetoblack)\b'),
    ('menu', 'menus (messages, barter, caravan, slots, map)', r'\b(showmessage|showbartermenu|showmap|showcaravanmenu|showslotsmenu|showrepairmenu|showracemenu|getbuttonpressed)\b'),
    ('menumode-block', 'script blocks that only run while a menu is open on ONE machine (Begin MenuMode)', r'^$'),
    ('timer', 'timers (GameDaysPassed waits)', r'\b(gamedayspassed|getdayspassed|getsecondspassed|getcurrenttime|gethourspassed|setquestdelay)\b'),
    ('radio', 'radio', r'\b(setnoradio|radio\w*|playsound3d|playmusic)\b'),
    ('ending', 'endings / slideshows', r'\b(endgame|swaptexture|slideshow\w*)\b'),
    ('kill-all', '"kill everyone" choices',
     r'\b(kill|killactor|startcombat|setenemy|setpcenemyoffaction|sendassaultalarm)\b'),
    ('fast-travel', 'fast travel locks', r'\b(enablefasttravel|disablefasttravel|setplayercantravel)\b'),
    ('save', 'save suppression', r'\b(save|quicksave|autosave|playersavegame)\b'),
]


# How each script BLOCK type reaches the shared state, judged from internal lane notes rounds 2-8 (all of it unproven in game).
EVENT_PATHS = {
    'gamemode': ('partial', 'quest scripts: host only (guest quest-script gate, L5 round 4). Object scripts: run on both machines, guest mutators '
                            'dropped; the host only simulates loaded cells, so an NPC/trigger script far from the host never runs'),
    'ontriggerenter': ('unverified', 'NV:MP puts each guest ghost in the host world, so host trigger volumes should see guests and the host '
                                     'script runs natively (L5 round 4 design note). `IsActionRef player` then refers to the HOST player, not '
                                     'the guest: needs the PartyAwareCond initiator binding'),
    'ondeath': ('partial', 'NV:MP ghost kill fires OnDeath; shared effects are applied from the host run. Correct only for host-owned actors'),
    'onactivate': ('missing', 'a guest activation (hostage release, Victor, door) reaches no host script: the Activate observer was designed but '
                              'NOT implemented (L5 round 4, section "guest-originated triggers")'),
    'onadd': ('missing', '`OnAdd player` fires for the LOCAL player inventory on the machine that does the pickup; the host script never sees a '
                         'guest pickup and the hook set has no container-transfer observer (L5 limits)'),
    'menumode': ('missing', 'runs only on the machine that has the menu open (message-box buttons); on a guest the mutators are dropped by the '
                            'gate, nothing forwards the choice to the host'),
    'onload': ('partial', 'runs when the ref loads on each machine; guest mutators dropped, host effects captured only if a txn scope is open'),
    'onhitwith': ('unverified', 'combat events: host-run for host-owned actors, nothing story-critical captured'),
    'onhit': ('unverified', 'combat events: host-run for host-owned actors'),
    'onstartcombat': ('unverified', 'combat events: host-run for host-owned actors; StartCombat itself is host-local AI'),
}


def event_paths(scripts):
    block_re = re.compile(r'(?im)^\s*begin\s+(\w+)')
    agg = collections.OrderedDict()
    for s in scripts:
        for m in block_re.finditer(s['source']):
            b = m.group(1).lower()
            e = agg.setdefault(b, dict(block=m.group(1), scripts=set()))
            e['scripts'].add(script_label(s))
    rows = []
    for b, e in sorted(agg.items(), key=lambda kv: -len(kv[1]['scripts'])):
        st, note = EVENT_PATHS.get(b, ('unknown', 'block type not in the event-path table'))
        rows.append(dict(block=e['block'], status=st, note=note, scripts=len(e['scripts']), script_list=sorted(e['scripts'])[:40]))
    return rows


def script_label(s):
    if s['kind'] == 'quest_script':
        return s.get('script_edid') or ('%s quest_script' % s['quest'])
    if s['kind'] == 'object':
        return s['script_edid']
    if s['kind'] == 'stage':
        return '%s stage %d' % (s['quest'], s['stage'])
    return '%s %s' % (s['quest'], s['label'])


def build_gaps():
    D = model.data()
    qnames = {e.lower() for e in D['by_edid']}
    gnames = {e.lower() for e in D['globals']}
    scripts = model.collect_scripts() + model.driver_scripts()
    occ, reads, statements = [], [], 0
    check = {tag: [] for tag, _, _ in CHECKLIST}
    check_re = {tag: re.compile(rx, re.I) for tag, _, rx in CHECKLIST}
    for s in scripts:
        label = script_label(s)
        for r in model.scan_source(s['source'], qnames, gnames):
            statements += 1
            loc = dict(quest=s['quest'] or 'driver', role=s['role'], script=label, kind=s['kind'], info=s.get('info'),
                       stage=s.get('stage'), script_edid=s.get('script_edid'), line=r['line'], block=r.get('block'),
                       text=r['text'])
            for tag, cre in check_re.items():
                if cre.search(r['text']):
                    check[tag].append(dict(loc))
            for m in READ_RE.finditer(r['text']):
                fn = m.group(1).lower()
                if fn in PLAYER_ONLY_READS or re.search(r'\bplayer(ref)?\b', r['text'], re.I):
                    reads.append(dict(loc, fn=fn, reduction=READS[fn]))
            c = funcmap.classify_statement(r, qnames)
            if c is None:
                continue
            occ.append(dict(loc, fn=c['fn_key'], fn_orig=r.get('fn_orig') or r['fn'], conditional=r['conditional'],
                            subject=r.get('subject'), effect=c['effect'], capture=c['capture'], apply=c['apply'],
                            severity=c['severity'], hazards=c['hazards'], note=c['note']))
    mm = re.compile(r'(?im)^\s*begin\s+menumode')
    for s in scripts:
        for m in mm.finditer(s['source']):
            ln = s['source'][:m.start()].count(chr(10)) + 1
            check['menumode-block'].append(dict(quest=s['quest'] or 'driver', role=s['role'], script=script_label(s), kind=s['kind'],
                                                info=s.get('info'), stage=s.get('stage'), script_edid=s.get('script_edid'), line=ln,
                                                block='MenuMode', text=m.group(0).strip()))
    # group by function
    by_fn = collections.OrderedDict()
    for o in occ:
        key = (o['fn'], o['effect'], o['capture'], o['apply'], o['severity'])
        g = by_fn.setdefault(key, dict(fn=o['fn'], effect=o['effect'], capture=o['capture'], apply=o['apply'],
                                       severity=o['severity'], hazards=set(), note=o['note'], count=0, scripts=set(),
                                       locations=[]))
        g['count'] += 1
        g['hazards'].update(o['hazards'])
        g['scripts'].add(o['script'])
        g['locations'].append(dict(quest=o['quest'], script=o['script'], line=o['line'], text=o['text'],
                                   conditional=o['conditional'], subject=o['subject']))
    groups = []
    for g in by_fn.values():
        g['hazards'] = sorted(g['hazards'])
        g['scripts'] = sorted(g['scripts'])
        g['n_scripts'] = len(g['scripts'])
        groups.append(g)
    sev_rank = {k: i for i, k in enumerate(funcmap.SEVERITY_ORDER + ['unknown'])}
    groups.sort(key=lambda g: (sev_rank[g['severity']], -g['count'], g['fn']))
    unsupported = [g for g in groups if g['capture'] not in ('supported', 'n/a') or g['apply'] not in ('supported', 'n/a')]
    supported = [g for g in groups if g not in unsupported]
    by_sev = collections.OrderedDict((k, 0) for k in funcmap.SEVERITY_ORDER + ['unknown'])
    occ_by_sev = collections.OrderedDict((k, 0) for k in funcmap.SEVERITY_ORDER + ['unknown'])
    for g in unsupported:
        by_sev[g['severity']] += 1
        occ_by_sev[g['severity']] += g['count']
    # reads grouped
    rd = collections.OrderedDict()
    for r in reads:
        g = rd.setdefault(r['fn'], dict(fn=r['fn'], reduction=r['reduction'], count=0, locations=[]))
        g['count'] += 1
        g['locations'].append(dict(quest=r['quest'], script=r['script'], line=r['line'], text=r['text']))
    # dependency-object hazards (risk.json, level >= 2) for the in-scope quests
    dep = []
    for edid in model.SCOPE:
        rk = {v['edid']: v for v in D['risk'].values()}[edid]
        for f in rk['findings'] + rk.get('stage_findings', []):
            if f['where'].startswith('dep obj') and f['level'] >= 2:
                if model.SCOPE[edid] == 'boundary':
                    continue    # the boundary quest's dependency objects belong to the Ring-a-Ding-Ding line
                dep.append(dict(quest=edid, script=f['where'], line=f['line'], cat=f['cat'], level=f['level'], fn=f['fn'],
                                text=f['text'][:140]))
    seen, dep_u = set(), []
    for d in dep:       # the same object script is a dependency of several quests: keep one row per (script, line)
        k = (d['script'], d['line'], d['text'])
        if k in seen:
            continue
        seen.add(k)
        d['quests'] = sorted({x['quest'] for x in dep if (x['script'], x['line'], x['text']) == k})
        d.pop('quest')
        dep_u.append(d)
    return dict(
        schema=1, questline='prologue', scripts_scanned=len(scripts), statements_scanned=statements,
        effects_found=len(occ), status_legend='supported|partial|missing|n/a (see questlines/prologue/funcmap.py header)',
        by_function=groups, unsupported=unsupported,
        summary=dict(functions=len(groups), unsupported_functions=len(unsupported), supported_functions=len(supported),
                     unsupported_by_severity=dict(by_sev), unsupported_occurrences_by_severity=dict(occ_by_sev),
                     unsupported_occurrences=sum(g['count'] for g in unsupported), occurrences=len(occ)),
        player_reads=list(rd.values()), event_paths=event_paths(scripts),
        hazard_checklist=[dict(tag=t, what=w, count=len(check[t]), locations=check[t]) for t, w, _ in CHECKLIST],
        dep_object_hazards=sorted(dep_u, key=lambda d: (-d['level'], d['script'], d['line'])),
    )


def render_md(g):
    L = ['# Prologue: capture/apply gaps', '',
         'Generated by `questlines/prologue/build.py` from the stage, topic (INFO begin/end) and quest scripts of the questline',
         '(`questlines/prologue/model.py` SCOPE). Status vocabulary: `supported` / `partial` / `missing` / `n/a`, judged against',
         '`internal lane notes` (rounds 1-8), `plugin/hooks/{engine_hooks,apply_remote}.cpp` and `synccore/include/fnvcoop/effect.h`.',
         'Nothing here is proven in the two-instance game yet; where L5 says "unverified" the row says so.', '']
    s = g['summary']
    L += ['## Summary', '',
          '%d scripts, %d statements, %d effect-bearing statements in %d distinct functions; %d functions (%d occurrences) are not '
          'fully supported.' % (g['scripts_scanned'], g['statements_scanned'], s['occurrences'], s['functions'],
                                 s['unsupported_functions'], s['unsupported_occurrences']), '',
          '| severity | functions | occurrences |', '|---|---:|---:|']
    for k in funcmap.SEVERITY_ORDER + ['unknown']:
        if s['unsupported_by_severity'].get(k):
            L.append('| %s | %d | %d |' % (k, s['unsupported_by_severity'][k], s['unsupported_occurrences_by_severity'][k]))
    L += ['', '## Unsupported or partial (capture and/or apply)', '',
          '| fn | effect type | capture | apply | severity | hazards | count | scripts |', '|---|---|---|---|---|---|---:|---:|']
    for x in g['unsupported']:
        L.append('| %s | %s | %s | %s | %s | %s | %d | %d |' % (x['fn'], x['effect'] or '-', x['capture'], x['apply'], x['severity'],
                                                              ', '.join(x['hazards']) or '-', x['count'], x['n_scripts']))
    L += ['', '### Exact locations', '',
          '`script` is `<quest> stage N`, `<quest> info <INFO local id> begin|end`, or the quest script EDID. `(c)` = inside an '
          'if/while (conditional). Subject is the object the command runs on.', '']
    for x in g['unsupported']:
        L += ['#### `%s` -> %s | capture %s, apply %s | %s' % (x['fn'], x['effect'] or 'no Effect type', x['capture'], x['apply'],
                                                            x['severity']), '', x['note'], '']
        for loc in x['locations']:
            L.append('- `%s:%d`%s %s' % (loc['script'], loc['line'], ' (c)' if loc['conditional'] else '',
                                          loc['text'].replace('|', '\\|')))
        L.append('')
    L += ['## Supported (for the record)', '', '| fn | effect type | capture | apply | count | note |', '|---|---|---|---|---:|---|']
    for x in g['by_function']:
        if x in g['unsupported']:
            continue
        L.append('| %s | %s | %s | %s | %d | %s |' % (x['fn'], x['effect'] or '-', x['capture'], x['apply'], x['count'],
                                                     x['note'].replace('|', '/')[:150]))
    L += ['', '## Player-bound reads (host must evaluate over the party)', '',
          'Reads of "the player" in conditions and expressions. The quest/stage/topic scripts run on the host only, so each of these '
          'is evaluated against the host character unless the party-aware evaluator (rule `PartyAwareCond`) reduces it.', '',
          '| function | proposed reduction | count |', '|---|---|---:|']
    for r in g['player_reads']:
        L.append('| %s | %s | %d |' % (r['fn'], r['reduction'], r['count']))
    L.append('')
    for r in g['player_reads']:
        L.append('- **%s**: ' % r['fn'] + '; '.join('`%s:%d`' % (x['script'], x['line']) for x in r['locations']))
    L += ['', '## Event paths: how each script block type reaches the shared state', '',
          'Beyond the commands above, WHERE a script runs decides whether its effects are ever captured. Judged from L5 rounds 2-8.', '',
          '| block | scripts in the questline | status | note |', '|---|---:|---|---|']
    for e in g['event_paths']:
        L.append('| %s | %d | %s | %s |' % (e['block'], e['scripts'], e['status'], e['note'].replace('|', '/')))
    L += ['', '## BRIEF hazard checklist (zero rows are findings too)', '', '| hazard | hits | where |', '|---|---:|---|']
    for c in g['hazard_checklist']:
        w = ', '.join(sorted({'%s:%d' % (x['script'], x['line']) for x in c['locations']}))
        L.append('| %s | %d | %s |' % (c['what'], c['count'], w[:600] if w else 'none in the questline scripts'))
    L += ['', '## Dependency-object scripts (NPC/trigger scripts the quests depend on; risk.json level >= 2)', '',
          'Not the quest, stage or topic scripts, so not part of the counts above; listed because they run the same story logic.', '',
          '| level | cat | script:line | fn | text | quests |', '|---:|---|---|---|---|---|']
    for d in g['dep_object_hazards']:
        L.append('| %d | %s | %s:%d | %s | `%s` | %s |' % (d['level'], d['cat'], d['script'], d['line'], d['fn'],
                                                         d['text'].replace('|', '\\|')[:90], ', '.join(d['quests'])))
    return '\n'.join(L) + '\n'
