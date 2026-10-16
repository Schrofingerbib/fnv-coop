"""Facts that policy_review.md / the status doc quote, computed from the quest data (so the prose cannot drift from the numbers).
Also `cite()`: every script:line citation in the prose is verified against the real script text at build time."""
import collections
import re

import cmdtable as ct
import gaps as gaps_mod
import scope
import scriptscan

PAY_ITEM = re.compile(r'^(caps001|.*chip.*)$', re.I)
MUTATORS = {'setstage', 'setobjectivedisplayed', 'setobjectivecompleted', 'startquest', 'stopquest', 'completequest',
            'additem', 'removeitem', 'enable', 'disable', 'lock', 'unlock', 'moveto', 'movetomarker', 'kill', 'killactor',
            'rewardxp', 'rewardkarma', 'addreputation', 'addtofaction', 'removefromfaction', 'addperk', 'removeperk',
            'activate', 'setplayerteammate', 'placeatme', 'setrestrained'}


def script_text(data, edid):
    """Source of a script by SCPT editor id, `info:HEX` (any INFO), `stage:Q:N` or `quest_script:Q`."""
    if edid.startswith('info:'):
        ref = 'FalloutNV.esm:' + edid.split(':')[1].upper().zfill(6)
        i = data.all_infos.get(ref)
        if not i:
            return None
        return '\n'.join((i[k] or {}).get('source') or '' for k in ('begin', 'end'))
    x = data.scpt_by_edid.get(edid.lower())
    return x['script'].get('source') if x else None


def cite(data, edid, line, needle):
    """True if line `line` of the named script contains `needle` (case-insensitive). INFO citations use begin+end joined
    with the same numbering as gaps.json (block-local line numbers): for `info:` ids the needle is searched in both blocks."""
    src = script_text(data, edid)
    if src is None:
        return False
    lines = src.replace('\r', '').split('\n')
    if edid.startswith('info:'):
        return any(needle.lower() in l.lower() for l in lines)
    return 0 < line <= len(lines) and needle.lower() in lines[line - 1].lower()


def compute(data, g):
    f = {}
    scripts = gaps_mod.collect_scripts(data)
    # ---- F1: dialogue payments -------------------------------------------------------------------------------
    pay_by_container = collections.Counter()
    pay_infos = []
    for label, sc in scripts.items():
        if sc['kind'] != 'info':
            continue
        sts = scriptscan.scan(sc['source'])
        pays = [s for s in sts if s['kind'] == 'cmd' and s['fn'] == 'removeitem' and (s['subject'] or '').lower() == 'player'
                and s['args'] and PAY_ITEM.match(s['args'][0])]
        if pays:
            pay_by_container[sc['quest'] or '?'] += 1
            pay_infos.append(dict(info=sc['info'], quest=sc['quest'], block=sc['block'], line=pays[0]['line'], item=pays[0]['args'][0]))
    f['payments'] = dict(total=len(pay_infos), by_container=dict(pay_by_container.most_common()), infos=pay_infos)
    # ---- F2/F3: guest-originated activation + message-box branches -------------------------------------------
    act, btn, trig = [], [], []
    for label, sc in scripts.items():
        if sc['kind'] not in ('dep', 'casino_obj'):
            continue
        sts = scriptscan.scan(sc['source'], set(ct.READS) | ct.SHARED_STATE_READS)
        blocks = collections.defaultdict(list)
        for s in sts:
            blocks[s['block']].append(s)
        def mut(v):
            return any(x['kind'] == 'cmd' and x['fn'] in MUTATORS or x['kind'] == 'setq' for x in v)
        has_act = any((b or '').lower() == 'onactivate' and mut(v) for b, v in blocks.items())
        has_trig = any((b or '').lower() == 'ontriggerenter' and mut(v) for b, v in blocks.items())
        has_btn = any(x['kind'] == 'read' and x['fn'] == 'getbuttonpressed' for x in sts)
        if has_act:
            act.append(dict(script=sc['edid'], label=label, quests=sorted(sc['quests'])))
        if has_trig:
            trig.append(dict(script=sc['edid'], label=label, quests=sorted(sc['quests'])))
        if has_btn:
            first = next(x for x in sts if x['kind'] == 'read' and x['fn'] == 'getbuttonpressed')
            btn.append(dict(script=sc['edid'], line=first['line'], quests=sorted(sc['quests'])))
    f['activate_scripts'] = act
    f['trigger_scripts'] = trig
    f['button_scripts'] = btn
    # ---- F12: player-filtered event blocks (Begin OnDeath player ...)
    rx = re.compile(r'(?im)^\s*begin\s+(\w+)\s+(?:player|playerref)\b')
    blk, blk_ex = collections.Counter(), collections.defaultdict(list)
    for label, sc in scripts.items():
        for m in rx.finditer(sc['source']):
            ev = m.group(1).lower()
            blk[ev] += 1
            blk_ex[ev].append(dict(script=sc['edid'], quests=sorted(sc['quests'])))
    f['player_blocks'] = dict(blk.most_common())
    f['player_block_examples'] = {k: v[:8] for k, v in blk_ex.items()}
    # ---- F5: player teleports -------------------------------------------------------------------------------
    tp = g['hazards'][ct.H_TELEPORT]
    f['teleports'] = [dict(fn=l['fn'], script=l['script'], line=l['line'], text=l['text'], quests=l['quests'])
                      for l in tp['locations'] if l['sev'] != 'low']
    # ---- F9: rules hygiene ------------------------------------------------------------------------------------
    inert, unresolved_edids = [], []
    forms_edids = {v.get('edid') for v in data.forms.values() if v.get('edid')} | set(data.by_edid)
    for e, grp, _ in data.members():
        r = data.rules['quests'].get(e)
        if not r:
            continue
        for x in r['rules']:
            if x['kind'] == 'PartyTether' and not x['params'].get('cells'):
                inert.append(e)
            for key in ('cells', 'markers'):
                for name in x['params'].get(key, []) or []:
                    if name not in forms_edids:
                        unresolved_edids.append((e, x['kind'], name))
    f['inert_tethers'] = sorted(set(inert))
    f['unresolved_rule_params'] = unresolved_edids
    # ---- F10/F12: kills, saves, timers ---------------------------------------------------------------------
    kills = collections.Counter()
    for l in g['hazards'][ct.H_KILL]['locations']:
        for q in l['quests']:
            kills[q] += 1
    saves = []
    for l in g['hazards'][ct.H_SAVE]['locations']:
        saves.append(dict(fn=l['fn'], script=l['script'], line=l['line'], quests=l['quests']))
    f['kills_by_quest'] = dict(kills.most_common())
    f['saves'] = saves
    f['timers'] = {k: v for k, v in g['hazards'][ct.H_TIMER]['by_fn'].items()}
    ts = collections.Counter(l['script'] for l in g['hazards'][ct.H_TIMER]['locations'] if l['fn'] in ('gamedayspassed', 'gamehour'))
    f['timer_scripts'] = dict(ts.most_common(6))
    f['lucky38_moves'] = sum(1 for t in f['teleports'] if t['script'].startswith('Lucky38'))
    f['companion_stmts'] = g['hazards'][ct.H_COMPANION]['count']
    f['casino_reads'] = g['hazards'][ct.H_CASINO]['count']
    return f
