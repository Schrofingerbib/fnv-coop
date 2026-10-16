"""gaps.json / gaps.md generator: every script function in the questline's stage, topic, quest and dependent-object scripts,
mapped to a synccore Effect type plus its L5 capture/apply status (fnmap.py)."""
import collections
import re

import lib  # noqa: F401  (sets sys.path for tools/questdata)
import classify  # noqa: E402
import fnmap  # noqa: E402

# commands/words that are not script commands (parse artefacts of multi-line conditions, variable names)
NOISE_RX = re.compile(r'^(if\(|elseif\(|\(|b[a-z]|n[a-z]|spoketo|\w+\)$)', re.I)
ALIAS_OK = {'evp', 'moveto', 'cios', 'scaonactor', 'sifh', 'set', 'setquestvar'}

# themes group hazards into blockers the owner can act on (ranked by severity, then reach)
THEMES = [
    ('TELEPORT', 'Player teleports (MoveTo/MoveToMarker on the player) are not party-wide', 'PartyWarp / MoveRef'),
    ('CONTROLS', 'Cutscene control locks (Disable/EnablePlayerControls) are not captured from scripts', 'SetPlayerControls'),
    ('GUEST_EVENT', 'Player-triggered event blocks (OnAdd/OnDrop player, OnTriggerEnter, OnActivate, OnHit/OnDeath by player) never fire on the host for a guest', 'guest-originated trigger intents (L5 design, not built)'),
    ('GUEST_ACTIVATION', 'Guest-originated Activate / IsActionRef triggers never reach the host scripts', 'Activate intent (L5 design, not built)'),
    ('PLAYER_REF_COND', 'Player-reference reads (GetDistance/GetInCell/IsKiller/GetAV ...) see only the host player', 'PartyAwareCond engine hook'),
    ('FORCED_DIALOGUE', 'Forced dialogue (SayTo player / StartConversation) has no capture or spectator playback', 'ForcedDialogue'),
    ('FACTION', 'Player faction membership / rank changes are shared by decision but not recorded or applied', 'SetFactionRank'),
    ('FACTION_RELATION', 'Faction hostility toward the player (SetEnemy PlayerFaction, SetPCEnemyOfFaction) has no effect type', 'new Effect needed'),
    ('COMPANION', 'Companion recruit/dismiss (SetPlayerTeammate) not captured', 'CompanionOwner'),
    ('PERK', 'AddPerk/RemovePerk are suppressed on guests and never recorded', 'SetPerk'),
    ('FAST_TRAVEL', 'EnableFastTravel locks are not captured or applied', 'SetFastTravelLock'),
    ('SPAWN', 'PlaceAtMe spawns have no Effect type', 'new Effect needed'),
    ('DOOR_STATE', 'Lock/Unlock/SetOpenState door state is suppressed on guests and never recorded', 'SetLock'),
    ('MESSAGE_CHOICE', 'Modal ShowMessage/GetButtonPressed choices (elevator floors, endgame prompts) can only be answered on the host', 'ShowMessageChoice routed to the initiator (new Effect)'),
    ('POINT_OF_NO_RETURN', 'Endgame warning prompts (VEndGameWarningMsg*) start the final battle with no party agreement', 'party vote or Barrier + initiator'),
    ('MENU', 'ShowMessage / barter / map UI shown only where the script ran', 'dialogue layer (L8) / new Effect'),
    ('PLAYER_REWARD', 'Pip-Boy notes, achievements and spells are per-player with no effect', 'new Effect needed'),
    ('PLAYER_INVENTORY', 'Bulk player inventory removal (RemoveAllTypedItems) has no hook', 'new Effect needed'),
    ('SAVE', 'Script saves (SystemSave/AutoSave) are not hooked', 'SaveSuppress'),
    ('DELETE', 'MarkForDelete removals are not replicated', 'new Effect needed'),
    ('TIME_SKIP', 'Scripted time skips (set GameHour ...) must move every player clock', 'clock authority'),
    ('TIMER', 'Game-clock reads in quest/object scripts', 'clock authority'),
    ('ESSENTIAL', 'Essential/ghost flags set on host actors only', 'NV:MP actor sync'),
]
THEME_NAMES = [t[0] for t in THEMES]

KILL_ALL_MIN = 3
BEGIN_RX = re.compile(r'^\s*begin\s+(\w+)\s*(\w*)', re.I)


def _unit_key(u):
    if u['scope'] == 'dep_obj':
        return ('dep', u['script_ref'])
    if u['scope'] == 'stage':
        return ('stage', u['quest'], u['stage'], u['entry'])
    if u['scope'] == 'quest_script':
        return ('qs', u['quest'])
    return ('info', u['info'], u['scope'])


def collect_units(D, edids):
    """All script units of the questline, own + driver INFOs. Each unit gains quest (owner) and 'attrib' (quests it serves)."""
    units = {}
    for e in edids:
        q = D.by_edid[e]
        for u in lib.units_for_quest(D, q):
            u['quest'] = e
            u['quest_ref'] = q['ref']
            k = _unit_key(u)
            if k in units:
                units[k]['attrib'].add(e)
                continue
            u['attrib'] = {e}
            units[k] = u
    mention = re.compile(r'\b(%s)\b' % '|'.join(re.escape(e) for e in edids), re.I)
    for u in units.values():
        if u['scope'] == 'dep_obj' and not u['touched'] and mention.search(u['sc']['source']):
            u['touched'] = True
            u['promoted'] = True      # untouched by the oracle's reachability filter, but its script names a questline quest
    drv = lib.driver_infos(D, edids)
    for iref, hit in sorted(drv.items()):
        i = D.info_by_ref[iref]
        cq = D.by_ref.get(i.get('quest'), {}).get('edid') or i.get('quest')
        for k in ('begin', 'end'):
            sc = i.get(k)
            if not sc or not sc['source'].strip():
                continue
            u = {'scope': 'driver_info_' + k, 'loc': 'info %s %s' % (lib.info_id(i), k), 'info': iref, 'sc': sc,
                 'script_edid': 'INFO %s %s (%s)' % (lib.info_id(i), k, cq), 'touched': True, 'quest': cq, 'quest_ref': i.get('quest'),
                 'attrib': set(hit), 'driver_of': hit}
            units[('info', iref, u['scope'])] = u
    return units


def _expr_records(D, u):
    """Expression/condition-level player-reference reads in a unit -> [(line, fn, text)]."""
    out = []
    seen = set()
    ctx = 'dialogue' if u['scope'].startswith(('info', 'driver')) else None
    for f in classify.classify_source(u['sc']['source'], D.kq, D.kg, ctx):
        if not (f['cat'] == 'P-REF' or (f['cat'] == 'P-INV' and f['fn'] == 'getitemcount' and f['player'])):
            continue
        line = f['text']
        low = line.lower()
        fns = [fn for fn in fnmap.PREF_EXPR if fn != 'player-ref' and re.search(r'(?<![a-z_])' + fn + r'(?![a-z_])', low)]
        if not fns:
            fns = [f['fn'] if f['fn'] in fnmap.PREF_EXPR else 'player-ref']
        for fn in fns:
            key = (f['line'], fn)
            if key not in seen:
                seen.add(key)
                out.append((f['line'], fn, line))
    return out


def _stmt_ok(s):
    fn = s['fn']
    if fn in fnmap.TABLE or fn in ALIAS_OK:
        return True
    return False


def build(D, edids, command_table):
    units = collect_units(D, edids)
    cmd_names = {k.lower() for k in command_table}
    rows = []            # one row per (unit, line, fn)
    noise = collections.Counter()
    noise_loc = collections.defaultdict(list)
    unmapped = collections.Counter()
    unmapped_loc = collections.defaultdict(list)
    cond_counts = collections.Counter()
    quest_var_names = {}
    for u in units.values():
        sc = u['sc']
        for s in lib.statements(D, u):
            fn = s['fn']
            if fn.startswith(('if(', 'elseif(')) or fn in ('if', 'elseif') or not re.match(r'^[a-z_][a-z0-9_]*$', fn):
                noise[fn] += 1
                noise_loc[fn].append((u['script_edid'], s['line']))
                continue
            d = fnmap.describe_stmt(s, D.kq, D.kq)
            if d is None:
                if fn not in cmd_names and fn not in fnmap.TABLE:
                    # not a vanilla command and not in our table: a variable/parse artefact of a multi-line condition
                    noise[fn] += 1
                    noise_loc[fn].append((u['script_edid'], s['line']))
                else:
                    unmapped[fn] += 1
                    unmapped_loc[fn].append((u['script_edid'], s['line']))
                continue
            rows.append(dict(unit=u, line=s['line'], fn=fn, text=s['text'], conditional=s['conditional'], d=d, kind='stmt'))
        for line, fn, text in _expr_records(D, u):
            d = fnmap.describe_expr(fn, text)
            rows.append(dict(unit=u, line=line, fn=fn, text=text, conditional=True, d=d, kind='expr'))
        # INFO condition-level player reads (CTDA): evaluated by the initiator's engine
        if u['scope'].endswith(('begin',)) and u['scope'].startswith(('info', 'driver')):
            i = D.info_by_ref[u['info']]
            for f in classify.classify_conditions(i['conditions'], 'info'):
                cond_counts[f['fn']] += 1

    # player-triggered event blocks of object/quest scripts
    for u in units.values():
        if u['scope'] not in ('dep_obj', 'quest_script'):
            continue
        for n, raw in enumerate(u['sc']['source'].splitlines(), 1):
            m = BEGIN_RX.match(raw)
            if not m:
                continue
            d = fnmap.describe_block(m.group(1), m.group(2))
            if d:
                rows.append(dict(unit=u, line=n, fn='begin ' + m.group(1).lower() + (' ' + m.group(2).lower() if m.group(2) else ''),
                                 text=raw.strip()[:100], conditional=False, d=d, kind='block'))

    # modal message choices: GetButtonPressed waits for a button only the host can press
    for u in units.values():
        for n, raw in enumerate(u['sc']['source'].splitlines(), 1):
            if re.search(r'\bGetButtonPressed\b', classify.strip_comment(raw), re.I):
                rows.append(dict(unit=u, line=n, fn='getbuttonpressed', text=raw.strip()[:100], conditional=False,
                                 d=fnmap.SCAN_BUTTON, kind='scan'))

    for u in units.values():
        for n, raw in enumerate(u['sc']['source'].splitlines(), 1):
            if re.match(r'\s*set\s+GameHour\s+to\b', classify.strip_comment(raw), re.I):
                rows.append(dict(unit=u, line=n, fn='set gamehour', text=raw.strip()[:100], conditional=False,
                                 d=fnmap.SCAN_TIMESKIP, kind='scan'))

    # timers / ending words found by text scan of the whole unit
    extra_hz = collections.defaultdict(list)
    for u in units.values():
        src = u['sc']['source']
        for n, raw in enumerate(src.splitlines(), 1):
            line = classify.strip_comment(raw)
            if re.search(r'\b(GameDaysPassed|GameHour|GetCurrentTime|GetDaysPassed|GetDaysSince)\b', line, re.I):
                extra_hz['TIMER'].append((u, n, line.strip()[:120]))
            if re.search(r'\b(EndGame|OnScreenSlide|StartCinematic|PlayBink|StartMovie)\b', line, re.I):
                extra_hz['ENDING'].append((u, n, line.strip()[:120]))
            if re.search(r'kill\w*all|killthem', line, re.I):
                extra_hz['KILL_ALL'].append((u, n, line.strip()[:120]))
            if re.search(r'radio', line, re.I):
                extra_hz['RADIO'].append((u, n, line.strip()[:120]))
    # kill-everyone: a unit with several KillActor/Kill statements
    killers = collections.defaultdict(list)
    for r in rows:
        if r['fn'] in ('kill', 'killactor'):
            killers[_unit_key(r['unit'])].append(r)
    for k, rs in killers.items():
        if len(rs) >= KILL_ALL_MIN:
            for r in rs:
                extra_hz['KILL_ALL'].append((r['unit'], r['line'], r['text']))

    # ---- aggregate per function --------------------------------------------------------------------------------------
    by_fn = collections.OrderedDict()
    seen_loc = set()
    audit_only = collections.Counter()
    for r in sorted(rows, key=lambda r: (r['fn'], r['unit']['script_edid'], r['line'])):
        u, d = r['unit'], r['d']
        if not u['touched']:
            audit_only[r['fn']] += 1
            continue
        key = (r['fn'], u['script_edid'], r['line'], r['kind'])
        loc = {'script': u['script_edid'], 'scope': u['scope'], 'loc': u['loc'], 'line': r['line'], 'text': r['text'],
               'conditional': r['conditional'], 'quests': sorted(u['attrib'])}
        e = by_fn.setdefault((r['fn'], r['kind']), {'fn': r['fn'], 'kind': r['kind'], 'descriptors': {}, 'locations': [],
                                                   'quests': collections.Counter()})
        dk = (d['effect'], d['capture'], d['apply'], d['severity'], tuple(d['hazards']), d['note'])
        e['descriptors'].setdefault(dk, d)
        if key in seen_loc:
            continue
        seen_loc.add(key)
        loc['status'] = fnmap.overall(d['capture'], d['apply'])
        loc['severity'] = d['severity']
        loc['hazards'] = d['hazards']
        loc['effect'] = d['effect']
        e['locations'].append(loc)
        for q in u['attrib']:
            e['quests'][q] += 1

    functions = []
    for (fn, kind), e in by_fn.items():
        ds = list(e['descriptors'].values())
        worst_d = max(ds, key=lambda d: (fnmap.STATUS_ORDER[fnmap.overall(d['capture'], d['apply'])], fnmap.SEV_ORDER[d['severity']]))
        status = fnmap.overall(worst_d['capture'], worst_d['apply'])
        sev = max((d['severity'] for d in ds), key=lambda s: fnmap.SEV_ORDER[s])
        hazards = sorted({h for d in ds for h in d['hazards']})
        locs = e['locations']
        n_unsupported = sum(1 for l in locs if l['status'] in ('missing', 'partial'))
        functions.append({
            'fn': fn, 'kind': kind, 'effect': worst_d['effect'], 'capture': worst_d['capture'], 'apply': worst_d['apply'],
            'status': status, 'severity': sev, 'hazards': hazards, 'note': worst_d['note'],
            'variants': [{'effect': d['effect'], 'capture': d['capture'], 'apply': d['apply'], 'severity': d['severity'],
                          'note': d['note']} for d in sorted(ds, key=lambda d: (d['capture'], d['apply'], d['note']))]
            if len(ds) > 1 else [],
            'occurrences': len(locs), 'unsupported_occurrences': n_unsupported,
            'conditional_occurrences': sum(1 for l in locs if l['conditional']),
            'quests': dict(sorted(e['quests'].items())),
            'locations': locs if status in ('missing', 'partial') else
            [l for l in locs if l['status'] in ('missing', 'partial')] or [],
            'locations_total': len(locs),
        })
    functions.sort(key=lambda f: (-fnmap.SEV_ORDER[f['severity']], -fnmap.STATUS_ORDER[f['status']], -f['unsupported_occurrences'], f['fn']))

    # ---- hazards ---------------------------------------------------------------------------------------------------------
    hazards = collections.OrderedDict()
    for f in functions:
        for l in f['locations']:
            for h in l['hazards']:
                hazards.setdefault(h, []).append({'fn': f['fn'], 'script': l['script'], 'loc': l['loc'], 'line': l['line'],
                                                  'status': l['status'], 'severity': l['severity'], 'text': l['text'][:120],
                                                  'quests': l['quests']})
    for h, lst in extra_hz.items():
        for u, n, text in lst:
            hazards.setdefault(h, []).append({'fn': '(text scan)', 'script': u['script_edid'], 'loc': u['loc'], 'line': n,
                                              'status': 'info', 'severity': 'info', 'text': text, 'quests': sorted(u['attrib'])})
    hazards = collections.OrderedDict(sorted(hazards.items()))

    # ---- themes / blockers -------------------------------------------------------------------------------------------------
    blockers = []
    for theme, title, needs in THEMES:
        locs = [dict(h, hazard=theme) for h in hazards.get(theme, ())
                if h['status'] in ('missing', 'partial', 'info') or theme in ('TIMER',)]
        gap_locs = [l for l in locs if l['status'] in ('missing', 'partial')]
        if not gap_locs:
            continue
        sev = max((l['severity'] for l in gap_locs), key=lambda s: fnmap.SEV_ORDER[s], default='info')
        quests = collections.Counter(q for l in gap_locs for q in l['quests'])
        uniq = {(l['script'], l['line']) for l in gap_locs}
        blockers.append({'theme': theme, 'title': title, 'needs': needs, 'severity': sev, 'occurrences': len(uniq),
                         'quests': dict(sorted(quests.items())), 'sample_locations': sorted(
                             ({'script': l['script'], 'line': l['line'], 'fn': l['fn'], 'text': l['text']} for l in gap_locs),
                             key=lambda x: (x['script'], x['line']))[:12]})
    blockers.sort(key=lambda b: (-fnmap.SEV_ORDER[b['severity']], -len(b['quests']), -b['occurrences'], b['theme']))
    for n, b in enumerate(blockers, 1):
        b['rank'] = n

    # ---- summary ------------------------------------------------------------------------------------------------------------
    st = collections.Counter()
    sv = collections.Counter()
    unique_gap = set()
    for f in functions:
        for l in f['locations'] if f['status'] in ('missing', 'partial') else []:
            pass
    total_occ = collections.Counter()
    for (fn, kind), e in by_fn.items():
        for l in e['locations']:
            total_occ[l['status']] += 1
            if l['status'] in ('missing', 'partial'):
                sv[l['severity']] += 1
    per_quest = collections.defaultdict(lambda: collections.Counter())
    for (fn, kind), e in by_fn.items():
        for l in e['locations']:
            for q in l['quests']:
                per_quest[q][l['status']] += 1
    summary = {
        'units': len(units), 'units_by_scope': dict(collections.Counter(u['scope'] for u in units.values())),
        'distinct_functions': len(functions), 'occurrences_by_status': dict(total_occ),
        'unsupported_occurrences_by_severity': {k: sv[k] for k in ('critical', 'high', 'medium', 'low', 'info') if sv[k]},
        'functions_by_status': dict(collections.Counter(f['status'] for f in functions)),
        'audit_only_occurrences': dict(audit_only),
        'noise_tokens': dict(sorted(noise.items())),
        'per_quest': {q: dict(c) for q, c in sorted(per_quest.items())},
        'info_condition_reads': dict(cond_counts),
    }
    return {
        'rows': rows, 'units': units, 'functions': functions, 'hazards': hazards, 'blockers': blockers, 'summary': summary,
        'noise': {k: noise_loc[k] for k in sorted(noise_loc)}, 'unmapped': {k: unmapped_loc[k] for k in sorted(unmapped_loc)},
    }


def to_json(res, header):
    return {
        'schema': 1, 'questline': lib.QUESTLINE, **header,
        'status_legend': {
            'supported': 'hooked/applied and unit-tested (live-proven rounds noted in the note)',
            'partial': 'works for some subjects/arguments only, or only one of capture/apply exists',
            'missing': 'no hook / no engine apply / no Effect type',
            'host-local': 'deliberately not replicated (NPC AI, audio, camera); relies on NV:MP actor sync',
            'n/a': 'read-only expression; replicated state is read instead'},
        'severity_legend': {
            'high': 'a guest visibly desyncs or soft-locks during a main-quest moment',
            'medium': 'world/quest-state divergence a player can notice; recoverable by baseline resync',
            'low': 'cosmetic or per-player bookkeeping', 'info': 'host-local by design'},
        'summary': res['summary'], 'blockers': res['blockers'], 'hazards': res['hazards'],
        'functions': [{k: v for k, v in f.items()} for f in res['functions']],
        'unmapped_functions': {k: [{'script': s, 'line': l} for s, l in v] for k, v in res['unmapped'].items()},
        'parse_noise': {k: [{'script': s, 'line': l} for s, l in v[:6]] for k, v in res['noise'].items()},
    }


def to_md(res, header):
    s = res['summary']
    md = ['# act2 gaps: script functions vs synccore Effect types and L5 capture/apply', '',
          'Generated by `questlines/act2/build.py` (do not edit by hand). Questline: %s.' % lib.QUESTLINE, '',
          'Scope: %d script units (quest scripts, stage scripts, INFO begin/end scripts of the questline AND of the %d INFOs in other '
          'quests that drive its quests, and the object scripts the quests depend on).' % (
              s['units'], len({u['info'] for u in res['units'].values() if u['scope'].startswith('driver_info_')})),
          'Units by scope: ' + ', '.join('%s %d' % (k, v) for k, v in sorted(s['units_by_scope'].items())) + '.', '',
          '## Status legend', '',
          '| status | meaning |', '|---|---|',
          '| supported | hooked/applied and unit-tested |', '| partial | some subjects/arguments only, or half of capture/apply |',
          '| missing | no hook / no engine apply / no Effect type |',
          '| host-local | not replicated by design (NPC AI, audio); relies on NV:MP actor sync |',
          '| n/a | read-only expression |', '',
          '## Summary', '',
          'Distinct functions: %d. Occurrences by status: %s.' % (s['distinct_functions'], ', '.join(
              '%s %d' % (k, v) for k, v in sorted(s['occurrences_by_status'].items()))),
          'Unsupported (missing+partial) occurrences by severity: %s.' % ', '.join(
              '%s %d' % (k, v) for k, v in s['unsupported_occurrences_by_severity'].items()), '',
          '## Blockers by theme (ranked: severity, quests reached, occurrences)', '',
          '| # | severity | theme | needs | occurrences | quests |', '|---|---|---|---|---|---|']
    for b in res['blockers']:
        md.append('| %d | %s | %s | %s | %d | %s |' % (b['rank'], b['severity'], b['title'], b['needs'], b['occurrences'],
                                                        ', '.join('%s (%d)' % kv for kv in b['quests'].items())))
    md += ['', '## Unsupported functions (missing or partial) with exact locations', '',
           'Location = script EDID (stage and INFO scripts are named `QUEST stage N` / `INFO <id> begin|end`, driver INFOs add `(container quest)`) and 1-based line.', '']
    for f in res['functions']:
        if f['status'] not in ('missing', 'partial'):
            continue
        md.append('### `%s`  (%s, %s)' % (f['fn'], f['status'], f['severity']))
        md.append('')
        md.append('- Effect: %s; capture **%s**, apply **%s**. %s' % (f['effect'] or 'none', f['capture'], f['apply'], f['note']))
        if f['hazards']:
            md.append('- Hazards: %s' % ', '.join(f['hazards']))
        md.append('- %d occurrence(s) in %d unsupported location(s); quests: %s' % (
            f['locations_total'], f['unsupported_occurrences'], ', '.join('%s %d' % kv for kv in f['quests'].items())))
        by_script = collections.OrderedDict()
        for l in f['locations']:
            by_script.setdefault(l['script'], []).append(l['line'])
        shown = list(by_script.items())
        md.append('- Locations: ' + '; '.join('%s: %s' % (sc, ','.join(str(x) for x in sorted(set(ls)))) for sc, ls in shown[:40])
                  + (' ... (+%d more scripts, full list in gaps.json)' % (len(shown) - 40) if len(shown) > 40 else ''))
        md.append('')
    md += ['## Supported / host-local functions (counts only)', '', '| fn | status | effect | occurrences |', '|---|---|---|---|']
    for f in res['functions']:
        if f['status'] in ('missing', 'partial'):
            continue
        md.append('| %s | %s | %s | %d |' % (f['fn'], f['status'], f['effect'] or '-', f['locations_total']))
    md += ['', '## Co-op hazard index', '']
    for h, lst in res['hazards'].items():
        scripts = sorted({x['script'] for x in lst})
        md.append('- **%s**: %d location(s) in %d script(s), e.g. %s' % (
            h, len(lst), len(scripts), '; '.join('%s:%d' % (x['script'], x['line']) for x in lst[:4])))
    if res['unmapped']:
        md += ['', '## Unmapped functions (need a fnmap entry)', ''] + ['- %s' % k for k in res['unmapped']]
    return '\n'.join(md) + '\n'
