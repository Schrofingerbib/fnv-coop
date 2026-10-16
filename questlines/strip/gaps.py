"""gaps.json / gaps.md: every script function in the questline's quest, stage, topic and dependency scripts, mapped to a
synccore Effect + L5 capture/apply status, with hazard tags and exact locations (script EDID + line)."""
import collections
import re

import cmdtable as ct
import scope
import scriptscan

SEVS = ['blocker', 'high', 'medium', 'low', 'info']
TIMER_RE = re.compile(r'\b(gamedayspassed|gamehour)\b', re.I)
ENDING_RE = re.compile(r'(slideshow|endgame|ending\b|vending|endstate)', re.I)
PLAYER_READS = {'isactionref', 'getactionref', 'getplayerteammate', 'getbuttonpressed', 'getcasinowinningslevel',
                'getreputation', 'getreputationthreshold', 'iskiller'}


def collect_scripts(data):
    """label -> rec (+ quests set). One entry per distinct script; quests = member quests that own/depend on/are driven by it."""
    scripts = {}

    def add(rec, quest):
        r = scripts.setdefault(rec['label'], dict(rec, quests=set()))
        if quest:
            r['quests'].add(quest)
    for edid, group, _ in data.members():
        for rec in data.scripts_of_quest(edid):
            add(rec, edid)
        for rec in data.dep_scripts_of_quest(edid):
            add(rec, edid)
        if group in ('core', 'extended'):
            for i, why in data.related_infos(edid):
                for k in ('begin', 'end'):
                    sc = i.get(k)
                    if sc and sc.get('source'):
                        add(dict(kind='info', via='container', label=data.info_label(i, k), edid=f'info:{scope.ref_hex(i["ref"])}',
                                 source=sc['source'], refs=sc.get('refs', []), quest=data.by_ref[i['quest']]['edid']
                                 if i.get('quest') in data.by_ref else None, info=i['ref'], block=k), edid)
    for rec in data.casino_obj_scripts():
        add(rec, None)
    return scripts


def read_row(stmt, data):
    fn = stmt['fn']
    subj = (stmt.get('subject') or '').lower()
    argl = [a.lower() for a in stmt.get('args', [])]
    player = subj in ct.SUBJECT_PLAYER or fn in PLAYER_READS or any(a in ct.SUBJECT_PLAYER for a in argl)
    if fn in ct.SHARED_STATE_READS:
        return dict(status=ct.SUP, sev='info', reduction='shared', tags=[], player=False, effect=None,
                    note='reads shared quest/world state')
    if fn in ct.READS:
        red, tag = ct.READS[fn]
        if red in ('host-clock', 'host-roll'):
            return dict(status=ct.SUP, sev='info', reduction=red, tags=[tag] if tag else [], player=False, effect=None,
                        note='evaluated on the host only (quest scripts run on the host)')
        if fn == 'getbuttonpressed':
            return dict(status=ct.MIS, sev='blocker', reduction=red, tags=[ct.H_MENU], player=True, effect=None,
                        note='result of a host-local message box; a guest initiator cannot answer it')
        if fn == 'getcasinowinningslevel':
            return dict(status=ct.MIS, sev='high', reduction=red, tags=[ct.H_CASINO], player=True, effect=None,
                        note='engine-held per-casino winnings of the LOCAL player; ban/comp quest scripts only see the host player')
        if not player:
            return dict(status=ct.SUP, sev='info', reduction='world', tags=[], player=False, effect=None,
                        note='non-player subject: host world state')
        return dict(status=ct.PAR, sev='medium', reduction=red, tags=[ct.H_READ], player=True, effect=None,
                    note='player-bound read: rules.json PartyAwareCond reduction exists, but no engine hook evaluates it '
                         '(Condition::Evaluate / Cmd_* getters located, not hooked)')
    return None


def analyse(data):
    scripts = collect_scripts(data)
    known = set(ct.READS) | ct.SHARED_STATE_READS
    quests = {e for e in data.by_edid}
    gl = data.globals_by_edid
    rows = {}            # fn -> aggregate
    unmapped = collections.Counter()
    unmapped_loc = collections.defaultdict(list)
    hazards = collections.defaultdict(lambda: dict(count=0, by_fn=collections.Counter(), locations=[]))
    by_quest = collections.defaultdict(lambda: collections.Counter())
    stmt_total = 0
    for label, sc in sorted(scripts.items()):
        stmts = scriptscan.scan(sc['source'], known)
        player_kills = collections.defaultdict(list)
        for st in stmts:
            kind, fn = st['kind'], st['fn']
            if kind == 'cmd':
                row = ct.lookup(st)
                key = fn + ('@' + row['variant'] if row and row.get('variant') else '')
                if row is None:
                    unmapped[fn] += 1
                    unmapped_loc[fn].append(dict(script=sc['edid'], label=label, line=st['line'], text=st['text']))
                    continue
            elif kind == 'setq':
                key = 'set-questvar' if (st['subject'] or '').lower() in {q.lower() for q in quests} else 'set-refvar'
                row = dict(ct.CMD[key])
                row['tags'] = list(row['tags'])
                row['status'] = ct.combine(row['capture'], row['apply'])
                row['player'] = False
            elif kind == 'setl_or_g':
                if fn not in gl:
                    continue
                key = 'set-global'
                row = dict(ct.CMD[key])
                row['tags'] = list(row['tags'])
                row['status'] = ct.SUP
                row['player'] = False
            elif kind == 'read':
                r = read_row(st, data)
                if r is None:
                    unmapped['read:' + fn] += 1
                    unmapped_loc['read:' + fn].append(dict(script=sc['edid'], label=label, line=st['line'], text=st['text']))
                    continue
                key = 'read:' + fn
                row = dict(effect=None, capture=r['status'], apply=r['status'], tags=r['tags'], sev=r['sev'], note=r['note'],
                           rule='PartyAwareCond', status=r['status'], player=r['player'], reduction=r['reduction'])
            else:
                continue
            stmt_total += 1
            loc = dict(script=sc['edid'], label=label, line=st['line'], kind=sc['kind'], quests=sorted(sc['quests']),
                       conditional=st['conditional'], player=bool(row.get('player')), text=st['text'])
            agg = rows.get(key)
            if agg is None:
                agg = rows[key] = dict(fn=key, kind=kind if kind != 'setl_or_g' else 'setg', effect=row['effect'],
                                       capture=row['capture'], apply=row['apply'], status=row['status'], sev=row['sev'],
                                       tags=row['tags'], note=row['note'], rule=row.get('rule'),
                                       reduction=row.get('reduction'), count=0, conditional=0, player_targeted=0,
                                       scripts=set(), locations=[], variants={})
            agg['count'] += 1
            agg['conditional'] += 1 if st['conditional'] else 0
            agg['player_targeted'] += 1 if row.get('player') else 0
            agg['scripts'].add(label)
            loc['status'] = row['status']
            loc['sev'] = row['sev']
            agg['locations'].append(loc)
            for q in sc['quests']:
                by_quest[q][row['status'] + '/' + row['sev']] += 1
            for tag in row['tags']:
                h = hazards[tag]
                h['count'] += 1
                h['by_fn'][key] += 1
                h['locations'].append(dict(fn=key, **{k: loc[k] for k in ('script', 'label', 'line', 'quests', 'text', 'status', 'sev')}))
            if key in ('kill', 'killactor', 'resurrect'):
                pass
            if kind == 'cmd' and fn in ('kill', 'killactor'):
                player_kills[sc['edid']].append(st['line'])
            if TIMER_RE.search(st['text']):
                m = TIMER_RE.search(st['text']).group(1).lower()
                h = hazards[ct.H_TIMER]
                h['count'] += 1
                h['by_fn'][m] += 1
                h['locations'].append(dict(fn=m, script=sc['edid'], label=label, line=st['line'], quests=sorted(sc['quests']),
                                           text=st['text'], status='supported', sev='info'))
            if ENDING_RE.search(st['text']) and kind in ('cmd', 'read'):
                h = hazards[ct.H_ENDING]
                h['count'] += 1
                h['by_fn'][key] += 1
                h['locations'].append(dict(fn=key, script=sc['edid'], label=label, line=st['line'], quests=sorted(sc['quests']),
                                           text=st['text'], status='partial', sev='medium'))
        stmt_total += 0
    for fn, agg in rows.items():
        agg['scripts'] = len(agg['scripts'])
        agg.pop('variants', None)
    return dict(scripts=scripts, rows=rows, unmapped=unmapped, unmapped_loc=unmapped_loc, hazards=hazards,
                by_quest=by_quest, statements=stmt_total)


def build(data):
    a = analyse(data)
    funcs = sorted(a['rows'].values(), key=lambda r: (SEVS.index(r['sev']), -r['count'], r['fn']))
    for r in funcs:
        r['gap'] = r['status'] not in (ct.SUP, ct.NA)
        if not r['gap']:
            r['locations'] = [dict(script=l['script'], line=l['line']) for l in r['locations'][:3]] + (
                [] if len(r['locations']) <= 3 else [{'more': len(r['locations']) - 3}])
    sev_totals = collections.Counter()
    status_totals = collections.Counter()
    for r in funcs:
        status_totals[r['status']] += r['count']
        if r['gap']:
            sev_totals[r['sev']] += r['count']
    by_effect = {}
    for r in funcs:
        e = r['effect'] or '(no Effect in vocabulary)'
        b = by_effect.setdefault(e, dict(effect=r['effect'], functions=[], occurrences=0, supported=0, partial=0, missing=0, na=0))
        b['functions'].append(r['fn'])
        b['occurrences'] += r['count']
        b[{ct.SUP: 'supported', ct.PAR: 'partial', ct.MIS: 'missing', ct.NA: 'na'}[r['status']]] += r['count']
    hz = {}
    for tag in ct.ALL_HAZARDS:
        h = a['hazards'].get(tag)
        hz[tag] = dict(count=h['count'] if h else 0, by_fn=dict(h['by_fn'].most_common()) if h else {},
                       locations=(h['locations'] if h else []))
    quests = {}
    for q, c in sorted(a['by_quest'].items()):
        quests[q] = dict(c)
    unsupported = [r for r in funcs if r['gap']]
    return dict(
        schema=1, slug=scope.SLUG,
        scope=dict(scripts=len(a['scripts']), statements=a['statements'],
                   by_kind=dict(collections.Counter(s['kind'] for s in a['scripts'].values())),
                   quests=len(data.members())),
        status_occurrences=dict(status_totals), unsupported_by_severity=dict(sev_totals),
        unsupported_functions=len(unsupported), unsupported_occurrences=sum(r['count'] for r in unsupported),
        unmapped={k: dict(count=v, locations=a['unmapped_loc'][k][:20]) for k, v in a['unmapped'].items()},
        functions=funcs, by_effect=by_effect, hazards=hz, by_quest=quests)


def md(g):
    L = ['# Capture / apply gaps: Freeside / Strip questline', '',
         '_Generated by `questlines/strip/build.py` from the quest data (do not edit by hand). Full location lists: `gaps.json`._', '',
         f'Scope: {g["scope"]["scripts"]} distinct scripts ({", ".join(f"{k} {v}" for k, v in sorted(g["scope"]["by_kind"].items()))}), '
         f'{g["scope"]["statements"]} classified statements (commands, condition reads, variable writes).', '',
         'Status is `capture` (host emits an Effect) / `apply` (client applies without running the script) read from `plugin/hooks/engine_hooks.cpp`, '
         '`apply_remote.cpp` and `engine_addrs.h` at build time (`run_tests.py` fails if they drift from `cmdtable.py`). `n/a` = not shared state. '
         '`partial` also means "delegated to NV:MP actor sync, unproven" or "session-level event, not RemoteApplier". Nothing here was exercised in game.', '',
         '## Totals', '',
         '| status | occurrences |', '|---|---|']
    for k in (ct.SUP, ct.PAR, ct.MIS, ct.NA):
        L.append(f'| {k} | {g["status_occurrences"].get(k, 0)} |')
    L += ['', f'Unsupported (partial or missing): **{g["unsupported_functions"]} functions, {g["unsupported_occurrences"]} occurrences**.', '',
          '| severity | unsupported occurrences |', '|---|---|']
    for s in SEVS:
        L.append(f'| {s} | {g["unsupported_by_severity"].get(s, 0)} |')
    L += ['', '## Unmapped statements', '']
    if g['unmapped']:
        for k, v in sorted(g['unmapped'].items()):
            L.append(f'- `{k}` x{v["count"]}, e.g. {v["locations"][0]["script"]}:{v["locations"][0]["line"]}')
    else:
        L.append('None: every command, read and variable write in scope is classified in `cmdtable.py`.')
    L += ['', '## By synccore Effect', '', '| Effect | functions | supported | partial | missing | n/a |', '|---|---|---|---|---|---|']
    for e, b in sorted(g['by_effect'].items(), key=lambda kv: (-(kv[1]['partial'] + kv[1]['missing']), kv[0])):
        L.append(f'| {e} | {", ".join(sorted(b["functions"])[:6])}{"..." if len(b["functions"]) > 6 else ""} | '
                 f'{b["supported"]} | {b["partial"]} | {b["missing"]} | {b["na"]} |')
    L += ['', '## Unsupported functions (ranked by severity, then count)', '',
          '| fn | Effect | capture | apply | sev | n | player | cond | scripts | hazards | first locations (script:line) |',
          '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in g['functions']:
        if not r['gap']:
            continue
        locs = ', '.join(f'{l["script"]}:{l["line"]}' for l in r['locations'][:3])
        L.append(f'| `{r["fn"]}` | {r["effect"] or "-"} | {r["capture"]} | {r["apply"]} | {r["sev"]} | {r["count"]} | '
                 f'{r["player_targeted"]} | {r["conditional"]} | {r["scripts"]} | {", ".join(r["tags"]) or "-"} | {locs} |')
    L += ['', '## Notes per unsupported function', '']
    for r in g['functions']:
        if r['gap'] and r['note']:
            L.append(f'- `{r["fn"]}` ({r["sev"]}): {r["note"]}')
    L += ['', '## Supported functions (counts only)', '', '| fn | Effect | n |', '|---|---|---|']
    for r in sorted((r for r in g['functions'] if not r['gap'] and r['status'] == ct.SUP), key=lambda r: -r['count']):
        L.append(f'| `{r["fn"]}` | {r["effect"] or "-"} | {r["count"]} |')
    L += ['', '## Co-op hazard checklist (BRIEF list)', '', '| hazard | occurrences | top functions |', '|---|---|---|']
    for tag, h in g['hazards'].items():
        top = ', '.join(f'{k} {v}' for k, v in list(h['by_fn'].items())[:5]) or 'none found in scope'
        L.append(f'| {tag} | {h["count"]} | {top} |')
    L += ['', '## Per-quest unsupported load (status/severity: count)', '', '| quest | blockers+high | medium | low | supported |', '|---|---|---|---|---|']
    for q, c in sorted(g['by_quest'].items(), key=lambda kv: -sum(v for k, v in kv[1].items() if k.split('/')[1] in ('blocker', 'high') and not k.startswith('supported') and not k.startswith('n/a'))):
        def n(sevs):
            return sum(v for k, v in c.items() if k.split('/')[1] in sevs and k.split('/')[0] in (ct.PAR, ct.MIS))
        L.append(f'| {q} | {n(("blocker", "high"))} | {n(("medium",))} | {n(("low",))} | '
                 f'{sum(v for k, v in c.items() if k.startswith("supported"))} |')
    return '\n'.join(L) + '\n'
