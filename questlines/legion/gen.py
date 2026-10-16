#!/usr/bin/env python
"""Generate the Legion questline deliverables (inventory, sweep, gaps, dialogue data) from out/questdata.

Usage: python -I questlines/legion/gen.py [--out DIR]       (default DIR = questlines/legion)
Writes: inventory.json sweep.json gaps.json gaps.md dialogue_data.json dialogue_specs.json
Nothing here launches the game; everything is derived from the extracted ESM data. Never invents a FormID.
"""
import argparse
import collections
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C  # noqa: E402
import gaps as G  # noqa: E402
import rules_patch as RP  # noqa: E402
import dialogue_md as DM  # noqa: E402

GEN_VERSION = 1
ROLE_OWNER = {'core': 'Q-legion', 'battle': 'Q-legion', 'dialogue': 'Q-legion', 'system': 'Q-legion', 'prereq': C.OWNER_OF_PREREQ}
ORDER_LAST = ['VHDLegionBattle', 'vHDBattleController']   # ending slideshow / control lock: run last in a sweep
# stage chains that the linear heuristic gets wrong, with the evidence read from the stage scripts
CHAIN_OVERRIDE = {
    ('VMS35', 105): ([10], 'stage 105 script tests "GetObjectiveDisplayed VMS35 10 == 1 && GetObjectiveCompleted == 0" (Auto-Doc cure skips the surgery objectives 20/30)'),
    ('VMS35', 130): ([10], 'stage 130 script tests objective 10/11/20/25/35 displayed-but-not-completed (Auto-Doc kill path)'),
    ('VMS35', 101): ([10, 20, 30], 'killed during surgery: after the tools/bag objectives'),
    ('VMS35', 110): ([10, 20, 30], 'blamed for Caesar death during surgery'),
    ('VMS35', 120): ([10, 20, 30], 'Caesar died during surgery, not blamed'),
}
SWEEP_NOTES = [  # (regex on stage script text, note for the sweep runner)
    (r'startconversation', 'opens a dialogue menu on the host: the sweep must close it (bridge dialog close / Esc) or expect a dialog.open event'),
    (r'disableplayercontrols', 'locks player controls: sweep must enableplayercontrols after the stage (and reload the save)'),
    (r'startquest\s+vending|endgame|setinchargen', 'ending slideshow path: run last, expect the game to leave normal play; reload autosave afterwards'),
    (r'\.moveto\s|player\.moveto|movetomarker', 'teleports: run in a throwaway save'),
    (r'enablefasttravel\s+0', 'disables fast travel party-wide'),
    (r'setenemy|setally', 'changes faction hostility (persistent): reload save after the stage'),
    (r'forceactivequest', 'changes the active quest tracker'),
]


# ---------------------------------------------------------------- helpers
def sev_rank(s):
    return G.SEV_ORDER.index(s)


def tier_of(d, q):
    return d.risk[q['ref']]['tier']


def cond_summary(d, conds):
    return [d.cond_text(c) for c in conds]


def triggers_scan(d, targets):
    """Who sets stages / starts / stops the given quests, across the WHOLE game."""
    pat = re.compile(r'(?i)\b(startquest|setstage|forceactivequest|stopquest|completequest|failquest)\s+(%s)\b(?:\s+(\d+))?' %
                     '|'.join(re.escape(t) for t in targets))
    out = collections.defaultdict(list)

    def scan(src, where, owner):
        for ln, l in enumerate(src.replace('\r', '').split('\n'), 1):
            m = pat.search(C.classify.strip_comment(l))
            if m:
                tgt = next(t for t in targets if t.lower() == m.group(2).lower())
                out[tgt].append({'by': owner, 'where': where, 'line': ln, 'fn': m.group(1).lower(),
                                 'stage': int(m.group(3)) if m.group(3) else None, 'text': l.strip()[:110]})
    for q in d.quests:
        e = q.get('edid') or q['ref']
        if q.get('script_data') and q['script_data'].get('source'):
            scan(q['script_data']['source'], 'quest script %s' % q['script_data']['edid'], e)
        for s in q['stages']:
            for en in s['entries']:
                if en.get('script'):
                    scan(en['script']['source'], '%s stage %d' % (e, s['index']), e)
    for refs, i in d.info_by_ref.items():
        qe = d.by_ref.get(i.get('quest'), {}).get('edid')
        for k in ('begin', 'end'):
            if i.get(k):
                scan(i[k]['source'], 'INFO %s %s' % (C.local_hex(i['ref']), k), qe)
    for ent in d.extra['scpt'].values():
        if 'script' in ent:
            scan(ent['script']['source'], 'script %s' % ent['edid'], ent['edid'])
    return out


def gate_summary(d, q):
    """Quests/stages/vars that this quest's INFOs, stage entries and objectives are conditioned on."""
    cnt = collections.Counter()
    conds = list(q['conditions'])
    for s in q['stages']:
        for e in s['entries']:
            conds += e['conditions']
    for o in q['objectives']:
        for t in o['targets']:
            conds += t['conditions']
    for i in d.infos.get(q['ref'], []):
        conds += i['conditions']
    for c in conds:
        if c['func'] in C.classify.QUEST_DEP_FUNCS and isinstance(c.get('p1'), str) and c['p1'] in d.by_ref:
            tq = d.by_ref[c['p1']]['edid']
            if tq != q['edid']:
                cnt[(tq, c['func'], c.get('p2'), c['op'], c['value'])] += 1
    return [{'quest': k[0], 'func': k[1], 'param': k[2], 'op': k[3], 'value': k[4], 'count': n}
            for k, n in sorted(cnt.items(), key=lambda kv: -kv[1])[:40]]


# ---------------------------------------------------------------- inventory
def build_inventory(d):
    targets = [e for e, _ in C.SCOPE]
    trig = triggers_scan(d, targets)
    edges = d.graph['edges']
    qs = []
    for edid, role in C.SCOPE:
        q = d.by_edid[edid]
        rk = d.risk[q['ref']]
        infos = d.infos.get(q['ref'], [])
        hard_in = [{'quest': e['src'], 'kind': e['kind'], 'count': e['count']} for e in edges
                   if e['dst'] == edid and e['strength'] == 'hard']
        hard_out = [{'quest': e['dst'], 'kind': e['kind'], 'count': e['count']} for e in edges
                    if e['src'] == edid and e['strength'] == 'hard']
        soft_in = [{'quest': e['src'], 'kind': e['kind'], 'count': e['count']} for e in edges
                   if e['dst'] == edid and e['strength'] != 'hard']
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            fl = sorted({f for e in s['entries'] for f in e['flags']})
            stages.append({'index': s['index'], 'log': next((e.get('text') for e in s['entries'] if e.get('text')), None),
                           'flags': fl, 'terminal': bool({'complete_quest', 'fail_quest'} & set(fl)),
                           'has_script': any(e.get('script') for e in s['entries']),
                           'conditions': [c for e in s['entries'] for c in cond_summary(d, e['conditions'])]})
        qs.append({
            'edid': edid, 'formid': C.runtime_id(q['ref']), 'ref': q['ref'], 'plugin': q['plugin'], 'name': q.get('name'),
            'role': role, 'owner': ROLE_OWNER[role], 'tier': rk['tier'], 'tier_direct': rk['tier_direct'], 'risk_score': rk['score'],
            'flags': q.get('flags'), 'start_enabled': 'start_enabled' in (q.get('flags') or []),
            'quest_script': (q.get('script_data') or {}).get('edid'),
            'stages': stages,
            'objectives': [{'index': o['index'], 'text': o.get('text'),
                            'targets': [d.nm(t['ref']) for t in o['targets'] if t['ref']]} for o in q['objectives']],
            'prerequisites': {
                'quest_conditions': cond_summary(d, q['conditions']),
                'gated_on': gate_summary(d, q),
                'graph_hard_in': hard_in, 'graph_hard_out': hard_out, 'graph_soft_in': soft_in[:20],
                'triggered_by': trig.get(edid, []),
            },
            'dialogue': {'infos': len(infos), 'topics': len(q.get('topics', [])),
                         'scripted_infos': sum(1 for i in infos if i.get('begin') or i.get('end'))},
            'risk_cats': rk['cats'],
            'rules_json': [x['kind'] for x in d.rules['quests'].get(edid, {}).get('rules', [])] if edid in d.rules['quests'] else None,
            'stages_excluded': ({'reason': 'owned by %s; swept in that lane, listed here only as a prerequisite' % C.OWNER_OF_PREREQ}
                                if role == 'prereq' else None),
        })
    return {'schema': 1, 'slug': C.SLUG, 'generator': GEN_VERSION,
            'counts': {'quests': len(qs), 'stages': sum(len(x['stages']) for x in qs),
                       'stages_owned': sum(len(x['stages']) for x in qs if x['role'] != 'prereq'),
                       'objectives': sum(len(x['objectives']) for x in qs),
                       'infos': sum(x['dialogue']['infos'] for x in qs if x['role'] != 'prereq')},
            'quests': qs}


# ---------------------------------------------------------------- sweep (m1_goodsprings format + setup)
def build_sweep(d, inv):
    import m1
    owned = [x for x in inv['quests'] if x['role'] != 'prereq']
    core_like = [x for x in owned if x['role'] in ('core', 'battle') and 'name' in d.by_edid[x['edid']]]
    m1.CORE = {x['edid']: d.by_edid[x['edid']]['name'] for x in core_like}
    m1.SYSTEM = [x['edid'] for x in owned if x['edid'] not in m1.CORE]
    # the m1 builder resolves names through the ESM; use the already-extracted tables instead (no game files touched)
    def lookup(paths, want):
        out = {}
        for r in want:
            v = d.forms.get(r) or d.refnames.get(r)
            if v:
                out[r] = {'sig': v.get('sig'), 'edid': v.get('edid'), 'name': v.get('name'), 'base': v.get('base')}
        return out
    m1.forms_mod.lookup = lookup
    tmp = tempfile.mkdtemp(prefix='legion_m1_', dir=os.environ.get('TMP') or None)
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        m1.main(['--in', d.qd, '--out', tmp])
    sw = json.load(open(os.path.join(tmp, 'm1_goodsprings.json'), encoding='utf-8'))
    os.remove(os.path.join(tmp, 'm1_goodsprings.json'))
    os.rmdir(tmp)
    sw['milestone'] = 'Caesar\'s Legion main questline (through the ending)'
    sw['slug'] = C.SLUG
    sw['format'] = 'm1_goodsprings.json (plugin/snapshot/tools/goodsprings_sweep.py load_plan reads quests[].edid/ref/stages[].index) + setup/order/notes'
    byinv = {x['edid']: x for x in inv['quests']}
    for qq in sw['quests']:
        x = byinv[qq['edid']]
        qq['role'] = x['role']
        qq['formid'] = x['formid']
        qq['owner'] = x['owner']
        qq['order'] = 'last' if qq['edid'] in ORDER_LAST else 'normal'
        qq['setup'] = build_setup(d, x)
        # sweep notes from stage script text
        q = d.by_edid[qq['edid']]
        for st in qq['stages']:
            txt = ' '.join(e['script']['source'] for s in q['stages'] if s['index'] == st['index']
                           for e in s['entries'] if e.get('script') and e['script'].get('source'))
            st['sweep_notes'] = [n for rx, n in SWEEP_NOTES if re.search(rx, txt, re.I)]
    # natural story path, for the two-game end-to-end test (owned by other lanes where marked)
    sw['natural_path'] = natural_path(d)
    sw['excluded'] = []
    for x in inv['quests']:
        if x['role'] == 'prereq':
            for s in x['stages']:
                sw['excluded'].append({'quest': x['edid'], 'stage': s['index'], 'formid': x['formid'],
                                       'reason': x['stages_excluded']['reason']})
        else:
            for s in x['stages']:
                if (x['edid'], s['index']) in C.SWEEP_EXCLUDE:
                    sw['excluded'].append({'quest': x['edid'], 'stage': s['index'], 'formid': x['formid'],
                                           'reason': C.SWEEP_EXCLUDE[(x['edid'], s['index'])]})
    sw['sweep_summary'] = {'quests': len(sw['quests']), 'stages_swept': sum(len(q['stages']) for q in sw['quests']) - sum(
        1 for e in sw['excluded'] if e['quest'] in {q['edid'] for q in sw['quests']}),
        'stages_excluded': len(sw['excluded'])}
    return sw


def cmd_setstage(x, stage, why, conf='heuristic'):
    return {'cmd': 'setstage %s %d' % (x['formid'], stage), 'quest': x['edid'], 'stage': stage, 'kind': 'setstage',
            'why': why, 'confidence': conf}


def build_setup(d, x):
    """Console prerequisites to reach each stage of quest x from a clean (new-game / M1 save) state."""
    setup = {'quest': [], 'stages': {}}
    if not x['start_enabled'] and x['stages']:
        setup['quest'].append({'cmd': 'startquest %s' % x['formid'], 'quest': x['edid'], 'kind': 'startquest',
                               'why': 'quest is not start_enabled; setstage on a stopped quest still runs the stage but startquest makes the state explicit',
                               'confidence': 'certain'})
    lin = [s['index'] for s in x['stages'] if not s['terminal']]
    for s in x['stages']:
        ov = CHAIN_OVERRIDE.get((x['edid'], s['index']))
        if ov:
            chain, why = ov
        else:
            chain = [i for i in lin if i < s['index']]
            why = 'linear chain heuristic: earlier non-terminal stages in ascending order'
        steps = [cmd_setstage(x, i, why, 'evidence' if ov else 'heuristic') for i in chain]
        setup['stages'][str(s['index'])] = steps
    return setup


def natural_path(d):
    """Ordered natural play through the Legion route. Stages of quests not owned here are listed but not verified here."""
    def st(q, s, owner, why):
        return {'quest': q, 'formid': C.runtime_id(d.by_edid[q]['ref']), 'stage': s, 'owner': owner, 'why': why}
    path = [
        st('VMQ02', 100, C.OWNER_OF_PREREQ, 'Render Unto Caesar done (Fort); VMS35 stage 100/101/105 do ForceActiveQuest VMQ02'),
        st('VMS35', 10, 'Q-legion', 'Caesar dialogue (VDialogueCaesar INFOs 16457E/16457F/164590/164585) StartQuest VMS35 + SetStage 10'),
        st('VMS35', 20, 'Q-legion', 'surgical tools picked up (VMS35SurgicalToolsScript SetStage VMS35 20)'),
        st('VMS35', 100, 'Q-legion', 'FortCaesarScript L97/L172: cured by surgery'),
        st('VMQ03a', 100, C.OWNER_OF_PREREQ, 'You will know it when it happens (Kimball assassination setup)'),
        st('VMQ03b', 100, C.OWNER_OF_PREREQ, 'Arizona Killer'),
        st('VHDLegionBattle', 10, 'Q-legion', 'VHDLegionEnterLegatesTentScript OnTrigger (host player only): SetStage VHDLegionBattle 10'),
        st('VHDLegionBattle', 20, 'Q-legion', 'player enters the power plant'),
        st('VHDLegionBattle', 30, 'Q-legion', 'player finds General Oliver'),
        st('VHDLegionBattle', 100, 'Q-legion', 'INFO 14C60F end (Legate congratulates): ending slideshow'),
    ]
    for p in path:
        assert any(s['index'] == p['stage'] for s in d.by_edid[p['quest']]['stages']), p
    return path


# ---------------------------------------------------------------- gaps
def build_gaps(d):
    scripts, sites, census = G.analyse(d)
    haz = G.hazards(d, scripts, sites)
    unsupported = [s for s in sites if s['status'] in ('missing', 'partial', 'unclassified')]
    by_fn = collections.OrderedDict()
    for s in sorted(sites, key=lambda s: (s['cmd'], s['where'], s['line'])):
        k = (s['cmd'], s['effect'], s['capture'], s['apply'], s['tag'], s['sev'], s['status'])
        by_fn.setdefault(k, []).append(s)
    functions = []
    for (cmd, effect, cap, app, tag, sev, status), ss in by_fn.items():
        f = {'function': cmd, 'effect': effect, 'capture': cap, 'apply': app, 'status': status, 'severity': sev, 'tag': tag,
             'count': len(ss), 'scripts': len({(x['where'], x['script']) for x in ss}),
             'note': next((fnmapnote(cmd, x) for x in ss[:1]), '')}
        if status in ('missing', 'partial', 'unclassified'):
            f['locations'] = [{'script': x['script'], 'where': x['where'], 'line': x['line'], 'quest': x['quest'],
                               'block': x['block'], 'text': x['text']} for x in ss]
        else:
            f['sample_locations'] = ['%s:%d' % (x['where'], x['line']) for x in ss[:5]]
        functions.append(f)
    sev_counts = collections.Counter(s['sev'] for s in unsupported)
    status_counts = collections.Counter(s['status'] for s in sites)
    quest_counts = collections.defaultdict(collections.Counter)
    for s in unsupported:
        quest_counts[s['quest']][s['sev']] += 1
    out = {
        'schema': 1, 'slug': C.SLUG, 'generator': GEN_VERSION,
        'method': 'statement-level tokenizer over quest scripts, stage scripts, INFO begin/end scripts and the object/NPC/terminal/package '
                  'scripts of every in-scope quest dep_ref (classify.dep_scripts); player-reads are flagged only when they name the player. '
                  'Capture/apply status comes from fnmap.py (cites plugin/hooks code).',
        'scope': [{'quest': e, 'role': r} for e, r in C.SCOPE if r != 'prereq'],
        'totals': {'scripts': len(scripts), 'script_uses_analysed': sum(census.values()), 'gap_sites': len(sites),
                   'by_status': dict(status_counts), 'unsupported_by_severity': {k: sev_counts.get(k, 0) for k in G.SEV_ORDER}},
        'hazards': haz,
        'by_quest': {k: {s: v.get(s, 0) for s in G.SEV_ORDER} for k, v in sorted(quest_counts.items())},
        'functions': functions,
    }
    return out, sites, scripts


def fnmapnote(cmd, site):
    import fnmap
    if site['tag'] == 'condition-read':
        return 'player-ref read; PartyAwareCond reduction needed (no engine hook feeds the rules engine)'
    e = fnmap.FN.get(cmd)
    return e['note'] if e else ''


def gaps_md(g, sites):
    L = ['# Legion questline: capture/apply gaps (generated by gen.py; do not hand-edit)', '',
         'Scope: %d quests (%s).' % (len(g['scope']), ', '.join(x['quest'] for x in g['scope'])), '',
         g['method'], '',
         'Attribution caveat: object/NPC scripts reached through `dep_refs` are listed once, under the first in-scope quest that reaches them. '
         'Dialogue and system containers (`FortEquipmentConfiscationQuest`, `VMQCaesarFail`, `VFreeform*`) depend on shared companion scripts '
         '(Boone, Lily, Raul, Arcade, Veronica, ED-E, `VNPCFollowers`), so their per-quest counts include companion-system sites that are not '
         'Legion-specific. Quest, stage and INFO scripts are always attributed to their own quest.', '',
         '## Totals', '',
         '| metric | value |', '|---|---|',
         '| scripts analysed (deduplicated) | %d |' % g['totals']['scripts'],
         '| command/function uses tokenized | %d |' % g['totals']['script_uses_analysed'],
         '| gap-analysis sites (non-trivial uses) | %d |' % g['totals']['gap_sites']]
    for k, v in sorted(g['totals']['by_status'].items()):
        L.append('| status %s | %d |' % (k, v))
    L += ['', '### Unsupported sites by severity', '', '| severity | sites |', '|---|---|']
    for s in G.SEV_ORDER:
        L.append('| %s | %d |' % (s, g['totals']['unsupported_by_severity'][s]))
    L += ['', '## Co-op hazard checklist (brief)', '', '| hazard | sites | sample locations (script:line) |', '|---|---|---|']
    for k, v in g['hazards'].items():
        L.append('| %s | %d | %s |' % (k, v['count'], '; '.join(x.replace('|', '/') for x in v['sites'][:3])))
    L += ['', '## Unsupported functions (capture or apply not `supported`)', '',
          'Status: capture = host records it into an EffectTxn; apply = replica realises it. `missing` = no hook / no applier branch. '
          'Exact locations are listed for blocker and high severity (and in gaps.json for everything).', '',
          '| function | Effect type | capture | apply | sev | tag | uses | scripts |', '|---|---|---|---|---|---|---|---|']
    uns = [f for f in g['functions'] if f['status'] in ('missing', 'partial', 'unclassified')]
    uns.sort(key=lambda f: (G.SEV_ORDER.index(f['severity']), -f['count']))
    for f in uns:
        L.append('| `%s` | %s | %s | %s | %s | %s | %d | %d |' % (f['function'], f['effect'] or '-', f['capture'], f['apply'],
                                                                  f['severity'], f['tag'], f['count'], f['scripts']))
    for sev in ('blocker', 'high'):
        L += ['', '## %s: exact locations' % sev.capitalize(), '']
        for f in [f for f in uns if f['severity'] == sev]:
            L.append('### `%s` (%s) - %d uses, %d scripts' % (f['function'], f['effect'] or 'no Effect type', f['count'], f['scripts']))
            L.append('')
            if f['note']:
                L.append('_%s_' % f['note'])
                L.append('')
            by = collections.OrderedDict()
            for loc in f['locations']:
                by.setdefault(loc['where'], []).append(loc['line'])
            L.append('; '.join('`%s`:%s' % (w, ','.join(str(x) for x in sorted(set(ls)))) for w, ls in by.items()))
            L.append('')
    L += ['', '## Medium and low severity (counts; exact lines in gaps.json)', '',
          '| function | sev | uses | top scripts |', '|---|---|---|---|']
    for f in [f for f in uns if f['severity'] in ('medium', 'low')]:
        top = collections.Counter(x['where'] for x in f['locations']).most_common(3)
        L.append('| `%s` | %s | %d | %s |' % (f['function'], f['severity'], f['count'], ', '.join('%s (%d)' % t for t in top)))
    L += ['', '## Supported or n/a (for completeness)', '', '| function | Effect type | status | uses |', '|---|---|---|---|']
    for f in [f for f in g['functions'] if f['status'] in ('supported', 'n/a')]:
        L.append('| `%s` | %s | %s | %d |' % (f['function'], f['effect'] or '-', f['status'], f['count']))
    L += ['', '## Per-quest unsupported site counts', '', '| quest | blocker | high | medium | low |', '|---|---|---|---|---|']
    for q, v in g['by_quest'].items():
        L.append('| %s | %d | %d | %d | %d |' % (q, v['blocker'], v['high'], v['medium'], v['low']))
    return '\n'.join(L) + '\n'


# ---------------------------------------------------------------- dialogue
AV_NAMES = {5: 'Strength', 6: 'Perception', 7: 'Endurance', 8: 'Charisma', 9: 'Intelligence', 10: 'Agility', 11: 'Luck',
            32: 'Barter', 33: 'Big Guns', 34: 'Energy Weapons', 35: 'Explosives', 36: 'Lockpick', 37: 'Medicine',
            38: 'Melee Weapons', 39: 'Repair', 40: 'Science', 41: 'Guns', 42: 'Sneak', 43: 'Speech', 44: 'Survival', 45: 'Unarmed'}
KEY_RX = re.compile(r'(?i)\b(setstage|startquest|stopquest|failquest|completequest|setplayerteammate|addtofaction|removefromfaction|'
                    r'addreputation\w*|setreputation|rewardkarma|setenemy|setally|startcombat|rewardxp|addperk|removeperk|'
                    r'startconversation|showmessage|additem|removeitem|removealltypeditems|disableplayercontrols|moveto\w*)\b')
CORE_QUESTS = {'VMS35', 'VHDLegionBattle', 'VDeadSea', 'VEFR01LegionGood2CaesarsFavor', 'VEFR00LegionMix2CaesarsHire',
               'VEFR02LegionBad2CaesarsFoe', 'vHDBattleController', 'VMQ02', 'VMQ03a', 'VMQ03b', 'VMQCaesarFail', 'VHDHouseBattle',
               'VHDIndependentBattle', 'VMQ05'}


def info_row(d, i):
    dl = d.dials.get(i['topic'], {})
    cond_av = []
    for c in i['conditions']:
        if c['func'] == 'GetActorValue' and c.get('run_on') in ('target', 'subject') and isinstance(c.get('p1'), int):
            cond_av.append({'av': AV_NAMES.get(c['p1'], 'AV%d' % c['p1']), 'op': c['op'], 'value': c['value'], 'run_on': c['run_on']})
    scripts = {k: [l.strip() for l in i[k]['source'].replace('\r', '').split('\n') if KEY_RX.search(C.classify.strip_comment(l))][:14]
               for k in ('begin', 'end') if i.get(k)}
    effects = ' '.join(' '.join(v) for v in scripts.values())
    kinds = set()
    if cond_av:
        kinds.add('skill-check')
    if re.search(r'(?i)setstage|startquest|failquest|completequest|stopquest', effects):
        kinds.add('quest-branch')
    if re.search(r'(?i)setplayerteammate|FollowerFaction|TeammateFaction|CompanionSuite', effects):
        kinds.add('companion')
    if re.search(r'(?i)addreputation|setreputation|rewardkarma|addtofaction\s+\w*Legion|removefromfaction\s+\w*Legion|setenemy|setally|setpcenemyoffaction', effects):
        kinds.add('faction-karma')
    if re.search(r'(?i)startcombat', effects):
        kinds.add('combat')
    if re.search(r'(?i)showmessage|startconversation', effects):
        kinds.add('ui-forced')
    return {'info': i['ref'], 'formid': C.runtime_id(i['ref']), 'quest': d.by_ref[i['quest']]['edid'], 'topic': i['topic'],
            'topic_edid': dl.get('edid'), 'prompt': dl.get('name'), 'speaker': d.nm(i.get('speaker')) if i.get('speaker') else None,
            'flags': i.get('flags'), 'type': i.get('type'), 'skill_checks': cond_av,
            'conditions': cond_summary(d, i['conditions'])[:6], 'says': [r['text'][:110] for r in i['responses']][:2],
            'links': len(i.get('link_to') or []), 'kinds': sorted(kinds), 'script': scripts}


def build_dialogue(d):
    rows = []
    for edid, role in C.SCOPE:
        if role == 'prereq':
            continue
        q = d.by_edid[edid]
        for i in d.infos.get(q['ref'], []):
            if i.get('type') not in ('topic', 'conversation', None) and not (i.get('begin') or i.get('end')):
                continue
            r = info_row(d, i)
            if r['kinds'] or r['skill_checks']:
                rows.append(r)
    # infos in OTHER quests that touch the Legion line (advance a core quest)
    tgt = re.compile(r'(?i)\b(setstage|startquest|failquest|completequest|stopquest)\s+(VMS35|VHDLegionBattle|VDeadSea|VMQCaesarFail|VEFR0\w+)\b')
    seen = {r['info'] for r in rows}
    for i in d.info_by_ref.values():
        if i['ref'] in seen:
            continue
        txt = ' '.join(i[k]['source'] for k in ('begin', 'end') if i.get(k))
        if txt and tgt.search(txt):
            r = info_row(d, i)
            r['cross_quest'] = True
            rows.append(r)
    rows.sort(key=lambda r: (r['quest'], r['info']))
    counts = collections.Counter(k for r in rows for k in r['kinds'])
    return {'schema': 1, 'rows': rows, 'counts': dict(counts), 'total': len(rows)}


def npc_ref_of(d, speaker_base):
    """Placed ACHR of an NPC_ base (looked up in forms.json); None if unknown."""
    for k, v in d.forms.items():
        if v.get('sig') == 'ACHR' and v.get('base') == speaker_base:
            return k
    return None


def spec_for(d, idx, sid, name, summary, tier, quests, steps_extra, info_ref, players=('host', 'p1', 'p2'), checks=()):
    i = d.info_by_ref[info_ref]
    dl = d.dials.get(i['topic'], {})
    sp = i.get('speaker')
    sp_ref = None
    if sp:
        sp_ref = npc_ref_of(d, sp)
    steps = [{'id': 'prep', 'action': 'setup', 'players': {p: [] for p in players}}]
    if sp_ref:
        steps.append({'id': 'talk_p1', 'action': 'talk', 'actor': 'p1', 'npc': d.nm(sp_ref), 'npc_ref': sp_ref,
                      'greeting_info': None, 'expect': {'opened': True, 'spectators': ['host', 'p2']}})
    steps.append({'id': 'pick_' + C.local_hex(info_ref).lower(), 'action': 'pick', 'actor': 'p1', 'topic': i['topic'],
                  'topic_edid': dl.get('edid'), 'prompt': dl.get('name'), 'info': info_ref,
                  'line': (i['responses'][0]['text'][:30] if i.get('responses') else None),
                  'expect': {'spectator_lines': [i['responses'][0]['text'][:30]] if i.get('responses') else [], 'converged': True}})
    steps += steps_extra
    return {'schema': 1, 'kind': 'dialogue', 'id': sid, 'name': name, 'plugin': 'FalloutNV.esm', 'summary': summary, 'tier': tier,
            'players': {p: n for n, p in enumerate(players)}, 'quests': quests, 'save': 'unknown (needs an L7 save at the Fort)',
            'policy_checks': list(checks), 'setup': {}, 'steps': steps}


def build_specs(d):
    specs = []
    S = lambda q, n: {'stage': {q: n}}  # noqa: E731
    specs.append(spec_for(d, 0, 'dlg_legion_caesar_speech65_tumor', 'Caesar "[Speech Check 65+]" -> Et Tumor, Brute? starts',
                          'Initiator p1 has Speech 70, host 10: the 65+ option exists only for p1; StartQuest VMS35 + SetStage 10 + 65 XP are shared and the XP goes to every player.',
                          'T4', ['VMS35', 'VDialogueCaesar'],
                          [{'id': 'assert_stage', 'action': 'assert', 'expect': {'both': {'stage': {'quest': 'VMS35', 'value': 10}}, 'converged': True}}],
                          'FalloutNV.esm:164590', checks=('dialogue-ownership', 'speech-check-initiator', 'reward-both-vs-owner')))
    specs[-1]['steps'][0]['players'] = {'p1': ['player.forceav speech 70'], 'host': ['player.forceav speech 10'], 'p2': ['player.forceav speech 10']}
    specs.append(spec_for(d, 1, 'dlg_legion_caesar_medicine75_tumor', 'Caesar "[Medicine Check 75+]" -> Et Tumor, Brute? starts',
                          'Medicine gate is per initiator; the Doctor\'s Bag objective (VMS35 11) then reads player.GetItemCount NVDoctorBag in the quest script: whose bag counts is a policy decision (rules.patch.json PartyAwareCond any-party-member).',
                          'T4', ['VMS35', 'VDialogueCaesar'],
                          [{'id': 'assert_stage', 'action': 'assert', 'expect': {'both': {'stage': {'quest': 'VMS35', 'value': 10}}, 'converged': True}}],
                          'FalloutNV.esm:16457E', checks=('dialogue-ownership', 'speech-check-initiator', 'party-aware-cond')))
    specs[-1]['steps'][0]['players'] = {'p1': ['player.forceav medicine 80'], 'host': ['player.forceav medicine 10'], 'p2': ['player.forceav medicine 10']}
    specs.append(spec_for(d, 2, 'dlg_legion_enslave_arcade', 'Lucius: "He\'s all yours." (enslave Arcade Gannon)',
                          'Karma -100 (shared), RemoveAllTypedItems of the PLAYER inventory into a single container, Arcade removed as companion (SetPlayerTeammate 0, RemovePerk BetterHealing from the player), VMS35 -> 100. Breaks the initiator-picks model for whoever recruited Arcade.',
                          'T4', ['VMS35', 'VFreeformTheFort'],
                          [{'id': 'assert', 'action': 'assert', 'expect': {'both': {'stage': {'quest': 'VMS35', 'value': 100}}, 'converged': True}}],
                          'FalloutNV.esm:164B1C', checks=('dialogue-ownership', 'companion-owner', 'reward-both-vs-owner')))
    specs.append(spec_for(d, 3, 'dlg_legion_deadsea_accept', 'Dead Sea: accept the Camp Forlorn Hope attack (VDeadSea 10)',
                          'Accepting starts the kill-all objective; the stage is set once by the host from the initiator\'s pick.',
                          'T4', ['VDeadSea'],
                          [{'id': 'assert', 'action': 'assert', 'expect': {'both': {'stage': {'quest': 'VDeadSea', 'value': 10}}, 'converged': True}}],
                          'FalloutNV.esm:127E3A', checks=('dialogue-ownership', 'kill-all-quest')))
    specs.append(spec_for(d, 4, 'dlg_legion_legate_congratulates_ending', 'Legate Lanius congratulates you (VHDLegionBattle 100, ending)',
                          'End script SetStage VHDLegionBattle 100 starts the ending slideshow (DisablePlayerControls, StartQuest VEnding). Every player must be locked, moved and released together (SlideshowEnd rule).',
                          'T4', ['VHDLegionBattle', 'VFreeformHooverDam'],
                          [{'id': 'assert', 'action': 'assert', 'expect': {'both': {'stage': {'quest': 'VHDLegionBattle', 'value': 100}}, 'controls_locked_all': True, 'converged': True}}],
                          'FalloutNV.esm:14C60F', checks=('dialogue-ownership', 'slideshow-end', 'party-lock')))
    for s in specs:
        s['generated_from'] = 'out/questdata (infos.jsonl.gz, dials.json.gz, forms.json.gz)'
    return specs


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=HERE)
    a = ap.parse_args(argv)
    d = C.Data()
    inv = build_inventory(d)
    sw = build_sweep(d, inv)
    g, sites, scripts = build_gaps(d)
    dia = build_dialogue(d)
    specs = build_specs(d)
    patch = RP.build_patch(d, sites, scripts, [e for e, r in C.SCOPE if r != 'prereq'])
    files = {
        'rules.patch.json': patch,
        'inventory.json': inv, 'sweep.json': sw, 'gaps.json': g, 'dialogue_data.json': dia,
        'dialogue_specs.json': {'schema': 1, 'specs': specs},
    }
    for name, obj in files.items():
        with open(os.path.join(a.out, name), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=False)
            f.write('\n')
    with open(os.path.join(a.out, 'gaps.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(gaps_md(g, sites))
    with open(os.path.join(a.out, 'dialogue.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(DM.render(dia, specs))
    print('inventory: %d quests, %d stages (%d owned); sweep: %d quests, %d stages swept, %d excluded; gaps: %d sites, unsupported %s; dialogue rows %d' % (
        inv['counts']['quests'], inv['counts']['stages'], inv['counts']['stages_owned'], sw['sweep_summary']['quests'],
        sw['sweep_summary']['stages_swept'], sw['sweep_summary']['stages_excluded'], g['totals']['gap_sites'],
        g['totals']['unsupported_by_severity'], dia['total']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
