"""Gap analysis: every script function/effect in the OWB questline mapped to a synccore Effect and its L5 status."""
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'questdata'))
import fnmap  # noqa: E402
import scan  # noqa: E402
from ctda_funcs import FUNCS  # noqa: E402

PLAYER = {'player', 'playerref'}
IMPLICIT_PLAYER = {'rewardxp', 'rewardkarma', 'addperk', 'removeperk', 'addnote', 'removenote', 'addreputation',
                   'setinchargen', 'disableplayercontrols', 'enableplayercontrols', 'showtraitmenu', 'showracemenu',
                   'showplasticsurgeonmenu', 'showbarbermenu', 'showrepairmenu', 'showbartermenu', 'showmap',
                   'setspecialpoints', 'settagskills', 'setlevel', 'resetxp', 'getplayername', 'pipboyradio',
                   'pipboyradiooff', 'incrementscriptedchallenge', 'unlockchallenge', 'addachievement',
                   'forceactivequest', 'applyimagespacemodifier', 'removeimagespacemodifier', 'showmessage',
                   'dispelallspells', 'playmusic'}
SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info']
DECL_RE = re.compile(r'(?im)^\s*(?:short|int|long|float|ref|reference)\s+(\w+)')
EXTVAR_RE = re.compile(r'\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b')

KNOWN_READS = {v[0].lower() for v in FUNCS.values()} | fnmap.EXTRA_READS


def _known():
    return set(fnmap.FN) | KNOWN_READS | {'set'}

RADIO_RE = re.compile(r'radio', re.I)
TIME_GLOBALS = {'gamehour', 'gameday', 'gamemonth', 'gameyear', 'timescale'}
ENDING_UNITS = {'NVDLC03EndingSCRIPT', 'NVDLC03SlideshowTriggerSCRIPT', 'NVDLC03NarratorSCRIPT',
                'NVDLC03TransitionToTheSinkSCRIPT', 'NVDLC03TransitionToBigEmptySCRIPT', 'NVDLC03IntroSCRIPT'}
MENU_FNS = {'showtraitmenu', 'showracemenu', 'showplasticsurgeonmenu', 'showbarbermenu', 'showrepairmenu',
            'showbartermenu'}
COMPANION_RE = re.compile(r'VNPCFollowers|FollowerFaction|HumanoidInParty|CritterInParty', re.I)


def _ctx(d, units):
    quests = {k.lower() for k in d.by_edid}
    globs = {g['edid'].lower() for g in d.globals.values() if g.get('edid')}
    # ref-script variables that other scripts read/write through `Ref.var`
    ext = collections.Counter()
    for u in units:
        for m in EXTVAR_RE.finditer(re.sub(r'"[^"]*"', '', u['source'])):
            a, b = m.group(1).lower(), m.group(2).lower()
            if a in quests or a in PLAYER or b in _known() or a in {'nvdlc03'}:
                continue
            ext[b] += 1
    return {'quests': quests, 'globs': globs, 'extvars': set(ext)}


def classify(call, unit, ctx):
    """-> dict(fn, effect, capture, apply, severity, note, tags, player)"""
    fn = call['fn']
    f = fn.lower()
    subj = (call.get('subject') or '').lower()
    is_player = subj in PLAYER or (not subj and f in IMPLICIT_PLAYER)
    tags = []
    if call['kind'] == 'setvar':
        var = call['var'].lower()
        if call['subject'].lower() in ctx['quests']:
            return dict(fn='SetQuestVar', effect='SetScriptVar', capture='supported', apply='supported',
                        severity='info', note='quest variable (ScriptEventList diff)', tags=tags, player=False)
        # reference-script variable: SetScriptVar{owner=ref}; capture missing, apply ok (ExtraScript eventList)
        return dict(fn='SetRefVar', effect='SetScriptVar', capture='missing', apply='supported',
                    severity='high', note='ref/object script variable: no capture hook (L5 round 5: needs per-INFO SCRO '
                    'pre-computation or an interpreter hook)', tags=['ref-var'], player=False)
    if call['kind'] == 'setglobal':
        tgt = call['var'].lower()
        declared = {x.lower() for x in DECL_RE.findall(unit['source'])}
        if tgt in declared:
            if unit['kind'] == 'script:quest':
                return dict(fn='SetQuestVar', effect='SetScriptVar', capture='supported', apply='supported',
                            severity='info', note='quest-script local = quest variable', tags=tags, player=False)
            shared = tgt in ctx['extvars']
            return dict(fn='SetRefVar' if shared else 'SetLocalVar', effect='SetScriptVar' if shared else None,
                        capture='missing' if shared else 'n/a', apply='supported' if shared else 'n/a',
                        severity='high' if shared else 'info',
                        note=('object-script local that other scripts read via Ref.%s: shared ref var, no capture hook'
                              % '<var>') if shared else 'private object-script local (no cross-script reader)',
                        tags=['ref-var'] if shared else [], player=False)
        if tgt in ctx['globs']:
            if tgt in TIME_GLOBALS:
                tags.append('time-set')
            return dict(fn='SetGlobal', effect='SetGlobal', capture='supported', apply='supported', severity='info',
                        note='TESGlobal diff', tags=tags, player=False)
        if tgt in TIME_GLOBALS:
            return dict(fn='SetGlobal', effect='SetGlobal', capture='supported', apply='supported', severity='low',
                        note='engine time global (%s)' % call['var'], tags=['time-set'], player=False)
        return dict(fn='SetUnresolved', effect=None, capture='unknown', apply='unknown', severity='low',
                    note='assignment target not a declared var or known global: %s' % call['var'], tags=[], player=False)
    if call['kind'] == 'read':
        red, needs = fnmap.read_info(f, is_player)
        if f == 'getbuttonpressed':
            tags.append('menu')
        if f == 'getsecondspassed':
            tags.append('timer')
        if f in ('isactionref', 'getactionref'):
            return dict(fn=fn, effect='cond:IsActionRef', capture='n/a', apply='missing', severity='high',
                        note='read: initiator remap needed on host (PartyAwareCond reduction=initiator)',
                        tags=['p-ref'], player=True)
        if needs:
            tags.append('p-ref')
            return dict(fn=fn, effect='cond:' + fn, capture='n/a', apply='missing',
                        severity='medium',
                        note='read: host-side party-aware condition evaluation (reduction=%s) is proposed, not built' % red,
                        tags=tags, player=is_player)
        return dict(fn=fn, effect=None, capture='n/a', apply='n/a', severity='info', note='read of shared quest/ref state',
                    tags=tags, player=False)
    info = fnmap.FN.get(f)
    if info is None:
        return dict(fn=fn, effect=None, capture='unknown', apply='unknown', severity='low',
                    note='function not in the gap table', tags=['unmapped'], player=is_player)
    eff, cap, app, sev, note = info
    if is_player and f in ('additem', 'removeitem', 'additemhealthpercent', 'equipitem', 'unequipitem', 'addnote',
                           'removenote', 'addperk', 'removeperk', 'rewardxp', 'rewardkarma', 'setav', 'forceav', 'modav',
                           'setactorvalue', 'modactorvalue', 'restoreav', 'restoreactorvalue', 'damageav',
                           'damageactorvalue', 'addspell', 'removespell', 'dispel', 'dispelallspells',
                           'castimmediateonself', 'resethealth', 'addtofaction', 'removefromfaction', 'setfactionrank'):
        tags.append('player-effect')
    if f == 'placeatme':
        tags.append('spawn')
    if f in ('setplayerteammate',) or COMPANION_RE.search(call['text']):
        tags.append('companion')
    if f in ('moveto', 'movetomarker', 'setpos'):
        tags.append('move-player' if is_player else 'move-npc')
    if f in ('disableplayercontrols', 'enableplayercontrols', 'setinchargen'):
        tags.append('cinematic')
    if f in ('addscriptpackage', 'removescriptpackage') and is_player:
        tags.append('cinematic')
    if f in MENU_FNS:
        tags.append('menu')
    if f == 'showmessage':
        tags.append('message')
    if f in ('pipboyradio', 'pipboyradiooff', 'forceradiostationupdate') or (
            f in ('enable', 'disable') and RADIO_RE.search(call.get('subject') or '')):
        tags.append('radio')
    if f in ('kill', 'killactor'):
        tags.append('kill')
    if f in ('setenemy', 'setally', 'setpcenemyoffaction'):
        tags.append('faction-relation')
    if f in ('lock', 'unlock', 'setopenstate'):
        tags.append('gating')
    if f in ('addformtoformlist', 'additemtoleveledlist'):
        tags.append('formlist')
    if f in ('sayto', 'say') and unit['owner'] in ENDING_UNITS:
        tags.append('ending')
    if f == 'autosave' or (f == 'addscriptpackage' and 'NoSave' in call['text']):
        tags.append('save')
    if f == 'getsecondspassed':
        tags.append('timer')
    if unit['owner'] in ENDING_UNITS or unit['id'] in ENDING_UNITS:
        tags.append('ending-unit')
    # severity tweaks
    if f in ('additem', 'removeitem') and not is_player:
        cap = 'missing'
        app = 'missing'
        sev = 'medium'
        note = 'non-player container target: not captured (L5 limits)'
        tags.append('container-item')
    if f in ('kill', 'killactor') and is_player:
        sev = 'high'
    return dict(fn=fn, effect=eff, capture=cap, apply=app, severity=sev, note=note, tags=tags, player=is_player)


def build_gaps(d):
    units = list(scan.units(d))
    ctx = _ctx(d, units)
    known = _known()
    fnrec = {}
    hazards = collections.defaultdict(list)
    by_unit = collections.Counter()
    loc_total = 0
    unmapped = collections.Counter()
    blocks = collections.Counter()
    unit_tags = collections.defaultdict(collections.Counter)
    unit_kind = {}
    for u in units:
        for c in scan.calls(u, known):
            if c['kind'] == 'block':
                blocks[c['fn'].lower()] += 1
                if (c.get('arg') or '').lower() in PLAYER:
                    hazards['player-trigger'].append(dict(unit=u['id'], line=c['line'], block=c['block'],
                                                          text=c['text'], fn=c['fn'], severity='high'))
                if c['fn'].lower() == 'menumode':
                    hazards['menumode-block'].append(dict(unit=u['id'], line=c['line'], block=c['block'],
                                                          text=c['text'], fn=c['fn'], severity='high'))
                continue
            if c['kind'] == 'unknown':
                unmapped[c['fn'].lower()] += 1
                continue
            k = classify(c, u, ctx)
            loc = dict(unit=u['id'], line=c['line'], block=c['block'], text=c['text'])
            if c.get('subject'):
                loc['subject'] = c['subject']
            key = ('read:' if c['kind'] == 'read' else '') + k['fn'].lower() + '|' + k['capture'] + '|' + k['apply']
            r = fnrec.setdefault(key, dict(fn=k['fn'], kind='read' if c['kind'] == 'read' else 'effect',
                                           effect=k['effect'], capture=k['capture'], apply=k['apply'],
                                           severity=k['severity'], note=k['note'], count=0, player_count=0,
                                           locations=[]))
            r['count'] += 1
            if k['player']:
                r['player_count'] += 1
            r['locations'].append(loc)
            by_unit[u['id']] += 1
            for t in k['tags']:
                unit_tags[u['id']][t] += 1
            unit_kind[u['id']] = u['kind']
            loc_total += 1
            for t in k['tags']:
                hazards[t].append(dict(loc, fn=k['fn'], severity=k['severity']))
    funcs = sorted(fnrec.values(), key=lambda r: (SEVERITY_ORDER.index(r['severity']), -r['count'], r['fn'].lower()))
    for r in funcs:
        r['status'] = _status(r)
        if r['status'] in ('supported', 'n/a') and len(r['locations']) > 5:
            r['locations_truncated'] = len(r['locations']) - 5
            r['locations'] = r['locations'][:5]
    summary = collections.Counter(r['status'] for r in funcs)
    occ = collections.Counter()
    for r in funcs:
        occ[r['status']] += r['count']
    sev_occ = collections.Counter()
    for r in funcs:
        if r['status'] in ('unsupported', 'partial'):
            sev_occ[r['severity']] += r['count']
    return {
        'schema': 1,
        'scope': 'all executable scripts of OldWorldBlues.esm: %d units (script %d, stage %d, info %d, term %d, pack %d)' % (
            len(units), *[sum(1 for u in units if u['kind'].split(':')[0] == k) for k in ('script', 'stage', 'info', 'term', 'pack')]),
        'line_convention': '1-based raw source line (source split on LF, CRLF stripped), same as risk.json',
        'units': len(units),
        'calls': loc_total,
        'functions_total': len(funcs),
        'function_status': dict(summary),
        'occurrence_status': dict(occ),
        'unsupported_or_partial_occurrences_by_severity': dict(sev_occ),
        'statement_level_calls_not_in_table': dict(unmapped),
        'block_types': dict(blocks.most_common()),
        'functions': funcs,
        'hazards': {t: dict(count=len(v), locations=v) for t, v in sorted(hazards.items())},
        'units_ranked': by_unit.most_common(40),
        'quest_scripts_player_bound': sorted(
            ({'unit': u, 'player_effect': t.get('player-effect', 0), 'p_ref': t.get('p-ref', 0),
              'cinematic': t.get('cinematic', 0), 'move_player': t.get('move-player', 0), 'timer': t.get('timer', 0)}
             for u, t in unit_tags.items() if unit_kind.get(u) == 'script:quest' and (
                 t.get('player-effect', 0) or t.get('p-ref', 0) or t.get('cinematic', 0) or t.get('move-player', 0))),
            key=lambda r: -(r['player_effect'] + r['p_ref'] + r['cinematic'] + r['move_player'])),
    }


def _status(r):
    if r['kind'] == 'read':
        return 'n/a' if r['apply'] == 'n/a' else 'unsupported'
    cap, app = r['capture'], r['apply']
    if cap == 'n/a' and app == 'n/a':
        return 'n/a'
    if cap == 'supported' and app == 'supported':
        return 'supported'
    if cap == 'missing' and app == 'missing':
        return 'unsupported'
    if 'unknown' in (cap, app):
        return 'unknown'
    return 'partial' if (cap in ('supported', 'partial') or app in ('supported', 'partial')) else 'unsupported'


def to_markdown(g):
    L = []
    w = L.append
    w('# OWB gap analysis (generated by questlines/owb/gaps.py; do not hand-edit)')
    w('')
    w('Scope: %s.' % g['scope'])
    w('Line numbers: %s.' % g['line_convention'])
    w('')
    w('%d statement-level calls over %d distinct functions/reads. Function status: %s. Occurrence status: %s.' % (
        g['calls'], g['functions_total'], g['function_status'], g['occurrence_status']))
    w('')
    w('Unsupported or partial occurrences by severity: %s.' % g['unsupported_or_partial_occurrences_by_severity'])
    w('')
    w('Columns: n = occurrences, player = occurrences whose subject is the player (explicit `Player.` or implied by the command). ')
    w('')
    w('Status legend: supported = captured on host AND applicable on a client; partial = one side only or lossy; '
      'unsupported = neither (no Effect type, or Effect type without capture/apply); n/a = no cross-machine state.')
    w('')
    w('## Unsupported and partial functions (every location)')
    w('')
    w('| sev | function | n | player | Effect | capture | apply | note |')
    w('|---|---|---:|---:|---|---|---|---|')
    for r in g['functions']:
        if r['status'] in ('unsupported', 'partial', 'unknown'):
            w('| %s | %s%s | %d | %d | %s | %s | %s | %s |' % (
                r['severity'], r['fn'], ' (read)' if r['kind'] == 'read' else '', r['count'], r['player_count'],
                r['effect'] or '-', r['capture'], r['apply'], r['note'].replace('|', '/')))
    w('')
    w('### Locations (script EDID or stage/INFO id : line)')
    w('')
    for r in g['functions']:
        if r['status'] not in ('unsupported', 'partial', 'unknown') or r['severity'] == 'info':
            continue
        w('**%s%s** (%s, %d)' % (r['fn'], ' read' if r['kind'] == 'read' else '', r['severity'], r['count']))
        locs = r['locations']
        shown = locs if len(locs) <= 60 else locs[:60]
        w('- ' + '; '.join('%s:%d' % (x['unit'], x['line']) for x in shown) +
          ('' if len(locs) <= 60 else '; ... +%d more (full list in gaps.json)' % (len(locs) - 60)))
        w('')
    w('## Co-op hazards by class')
    w('')
    w('| hazard | occurrences | worst severity | first locations |')
    w('|---|---:|---|---|')
    for t, v in g['hazards'].items():
        sev = min((SEVERITY_ORDER.index(x['severity']) for x in v['locations']), default=4)
        w('| %s | %d | %s | %s |' % (t, v['count'], SEVERITY_ORDER[sev],
                                     '; '.join('%s:%d' % (x['unit'], x['line']) for x in v['locations'][:4])))
    w('')
    w('## Quest scripts that act on or read the player (guest quest-script gate skips ALL of these on a guest)')
    w('')
    w('| quest script | player effects | player reads | cinematic | MoveTo player | timers |')
    w('|---|---:|---:|---:|---:|---:|')
    for r in g['quest_scripts_player_bound']:
        w('| %s | %d | %d | %d | %d | %d |' % (r['unit'], r['player_effect'], r['p_ref'], r['cinematic'], r['move_player'], r['timer']))
    w('')
    w('## Supported functions (counts only)')
    w('')
    w(', '.join('%s=%d' % (r['fn'], r['count']) for r in g['functions'] if r['status'] == 'supported'))
    w('')
    w('## Not-applicable functions (local presentation or shared-state reads)')
    w('')
    w(', '.join('%s=%d' % (r['fn'], r['count']) for r in g['functions'] if r['status'] == 'n/a'))
    w('')
    return '\n'.join(L) + '\n'
