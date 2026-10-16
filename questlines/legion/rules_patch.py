"""Derive the co-op rules each Legion-scope quest needs from its script gaps and diff them against docs/quest-policies/rules.json.

Only the 16 existing rule kinds are used (tools/coverage/rules.py). Output: rules.patch.json (op merge/add per quest).
Human judgment lives in CURATED below (cells, reductions, notes); everything else is mechanical and cites gap sites.
"""
import collections
import re

import common as C
import fnmap

KINDS = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner',
         'SaveSuppress', 'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner', 'ShareKarmaRep',
         'InitiatorDialogue', 'ExcludeFromGate']

CASE = {  # canonical case for PartyAwareCond keys (rules.json uses the game's function case)
    'getdistance': 'GetDistance', 'getincell': 'GetInCell', 'getinsamecell': 'GetInSameCell', 'getactionref': 'GetActionRef',
    'isactionref': 'IsActionRef', 'getisreference': 'GetIsReference', 'getplayerteammate': 'GetPlayerTeammate',
    'getequipped': 'GetEquipped', 'getitemcount': 'GetItemCount', 'getactorvalue': 'GetActorValue', 'isincombat': 'IsInCombat',
    'issneaking': 'IsSneaking', 'isplayerinregion': 'IsPlayerInRegion', 'isweaponout': 'IsWeaponOut', 'getdetected': 'GetDetected',
    'gethasnote': 'GetHasNote', 'isplayermovingintonewspace': 'IsPlayerMovingIntoNewSpace', 'isininterior': 'IsInInterior',
    'getinworldspace': 'GetInWorldspace', 'iskiller': 'IsKiller', 'gettalkedtopc': 'GetTalkedToPC',
}

# --- human judgment ----------------------------------------------------------------------------------------------
# tether cells: only EDIDs that resolve (CELL/WRLD) in forms.json.gz / refnames.json.gz; confidence stated, never guessed silently
CURATED = {
    'VHDLegionBattle': {
        'PartyTether': {'cells': ['HooverDam', 'HooverDamExt', 'HooverDamIntODeck', 'HooverDamIntPowerPlant'], 'gather': 'auto-warp'},
        'cells_confidence': 'unknown: which of these CELL records the Legate camp and the east bank report in must be read in game (GetParentCell at VHDLegionLegateTeleportMarker); '
                            'rules.json currently has cells [] which makes the tether a no-op',
        'markers_extra': ['VHDLegionLegateTeleportMarker', 'VHDLegateStartMarker', 'vHDOliverTeleportMarker'],
        'why': 'stage 10 is fired by an OnTrigger player block inside the Legate tent and then does Player.MoveTo VHDLegionLegateTeleportMarker; the whole party must be present or be warped',
    },
    'VMS35': {
        'PartyTether': {'cells': ['TheFortSum'], 'gather': 'wait'},
        'cells_confidence': 'unknown: TheFortSum is the cell VMQ02 curated rules use for the Fort; FortCaesarRef belongs to TheFortWorld (WRLD) per the data and the surgery cell was not verified',
        'reductions': {'GetItemCount': 'any-party-member', 'GetActorValue': 'initiator'},
        'why': 'FortCaesarScript MenuMode 1001 decides the surgery from player.GetAV Medicine and the button that the activating player pressed; the bag/tools check is player.GetItemCount',
    },
    'vCCmovetothefort': {
        'markers_extra': ['TheFortEntryMarker', 'FromTheFortPlayerMarker'],
        'reductions': {'GetInSameCell': 'entry:all-in,exit:any-in'},
        'why': 'barge ride into/out of the Fort: player.getinsamecell nvccbargeref gates player.moveto TheFortEntryMarker for one player ref; the party must board together',
    },
    'vHDBattleController': {'why': 'stage 30 is the NCR ending (rewardxp 4000, DisablePlayerControls, StartQuest VEnding); shared by the House/Independent endings'},
    'VLegateShowdown': {'why': 'Legate final fight container: no stages, 400+ line quest script reading the player'},
    'VDeadSea': {'why': 'kill-everyone objective at Camp Forlorn Hope: any party member\'s kills count (GetDead checks on the host)'},
}
CTRL_MASK = re.compile(r'(?i)disableplayercontrols\s+([01 ]+)\s*$')
MOVE_MARKER = re.compile(r'(?i)\bmove\s*to\s*(?:marker\s+)?(\w+)')
ENDING_RX = re.compile(r'(?i)\bstartquest\s+vending\b|\bsetinchargen\s+1\b|\bendgame\b')


def derive(d, sites, scripts, edid):
    """-> {kind: {'params': {...}, 'evidence': [str]}} for quest `edid`, from its gap sites."""
    role = dict(C.SCOPE).get(edid)
    # object/NPC scripts reached through dep_refs are story machinery only for core/battle quests; dialogue and system containers
    # reach dozens of unrelated NPC scripts, so for them only their own quest/stage/INFO scripts count
    direct = ('quest_script', 'stage', 'info_begin', 'info_end')
    mine = [s for s in sites if edid in s['quests'] and (s['kind'] in direct or role in ('core', 'battle'))]
    # The Fort-to-Dam confirmation (GameMode block of FortLuciusScript / FortCaesarScript) is the start of the Legion battle scene, not part of
    # Et Tumor, Brute? (agreed with Q-strip, who dropped these derived rules from VMS35/VMS32): it is attributed to VHDLegionBattle only.
    def endgame(s):
        return s['where'] in ('FortLuciusScript', 'FortCaesarScript') and (s['block'] or '').lower() == 'gamemode'
    mine = [s for s in mine if (edid == 'VHDLegionBattle') == endgame(s) or not endgame(s)]
    if edid == 'VHDLegionBattle':
        mine += [s for s in sites if endgame(s) and s not in mine]
    out = collections.OrderedDict()

    def ev(s):
        return '%s:%d %s' % (s['where'], s['line'], s['text'][:60])

    def add(kind, params, evid):
        cur = out.setdefault(kind, {'params': params, 'evidence': []})
        if evid and len(cur['evidence']) < 4:
            cur['evidence'].append(evid)
        return cur
    for s in mine:
        c, tag = s['cmd'], s['tag']
        if tag == 'cinematic' and c in ('disableplayercontrols', 'enableplayercontrols'):
            cur = add('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, ev(s))
            m = CTRL_MASK.search(s['text'])
            if m and ' '.join(m.group(1).split()) not in cur['params']['masks']:
                cur['params']['masks'].append(' '.join(m.group(1).split()))
        elif tag == 'move' and (s['player'] or s['subject'] in C.PLAYER) and s['subject'] in C.PLAYER:
            m = MOVE_MARKER.search(s['text'].replace('.', ' '))
            cur = add('PartyWarp', {'markers': [], 'mode': 'slot-offset', 'absent': 'hold'}, ev(s))
            if m and m.group(1).lower() not in ('player', 'to') and m.group(1) not in cur['params']['markers']:
                cur['params']['markers'].append(m.group(1))
        elif tag == 'travel':
            add('FastTravelLock', {'scope': 'party'}, ev(s))
        elif tag == 'save':
            add('SaveSuppress', {}, ev(s))
        elif tag == 'companion':
            add('CompanionOwner', {'owner': 'recruiter'}, ev(s))
        elif tag == 'condition-read':
            cur = add('PartyAwareCond', {'funcs': {}}, ev(s))
            cur['params']['funcs'][CASE.get(c, c)] = fnmap.PREF_READ[c].split(' ')[0]
        elif tag in ('reward', 'player-target') and c in ('rewardxp', 'addnote') or (c == 'additem' and s['player']):
            add('RewardAll', {}, ev(s))
        elif tag == 'karma-rep':
            add('ShareKarmaRep', {}, ev(s))
        if c in ('removeitem', 'removealltypeditems') and s['player']:
            add('RemoveItemPerOwner', {}, ev(s))
    q = d.by_edid[edid]
    if d.infos.get(q['ref']):
        add('InitiatorDialogue', {}, '%d INFOs' % len(d.infos[q['ref']]))
    for sc in scripts:
        if edid in sc['quests'] and sc['kind'] in ('stage', 'info_end', 'info_begin', 'quest_script') and ENDING_RX.search(sc['source']):
            add('SlideshowEnd', {'run': 'host-once', 'lock': 'all', 'release': 'together'}, sc['label'])
            break
    # curated overlays
    cur = CURATED.get(edid, {})
    if 'PartyTether' in cur:
        out['PartyTether'] = {'params': cur['PartyTether'], 'evidence': [cur.get('why', '')]}
    if cur.get('markers_extra'):
        w = out.setdefault('PartyWarp', {'params': {'markers': [], 'mode': 'slot-offset', 'absent': 'hold'}, 'evidence': []})
        for m in cur['markers_extra']:
            if m not in w['params']['markers']:
                w['params']['markers'].append(m)
    for fn, red in (cur.get('reductions') or {}).items():
        w = out.setdefault('PartyAwareCond', {'params': {'funcs': {}}, 'evidence': []})
        w['params']['funcs'][fn] = red
    return out


def build_patch(d, sites, scripts, quests):
    existing = d.rules['quests']
    patch = {'schema': 1, 'target': 'docs/quest-policies/rules.json', 'kinds_used_from': sorted(KINDS),
             'note': 'merge = append rules whose kind is missing, replace params of kinds listed under update; add = new quest entry. '
                     'origin is always "curated". Evidence cites gap sites (script:line). No new rule kinds are proposed.',
             'quests': collections.OrderedDict()}
    for edid in quests:
        q = d.by_edid[edid]
        want = derive(d, sites, scripts, edid)
        have = {r['kind']: r for r in existing.get(edid, {}).get('rules', [])}
        add_rules, update = [], []
        for kind, w in want.items():
            if kind not in have:
                add_rules.append({'kind': kind, 'params': w['params'], 'origin': 'curated', 'evidence': w['evidence']})
            else:
                hp = have[kind]['params']
                wp = w['params']
                merged = _merge_params(kind, hp, wp)
                if merged != hp:
                    update.append({'kind': kind, 'params': merged, 'origin': 'curated', 'was': hp, 'evidence': w['evidence']})
        if not add_rules and not update and not (have.get('PartyTether') and have['PartyTether']['origin'] == 'derived'
                                                  and not have['PartyTether']['params'].get('cells') and 'PartyTether' not in want):
            continue
        remove = []
        hp = have.get('PartyTether')
        if hp and hp['origin'] == 'derived' and not hp['params'].get('cells') and 'PartyTether' not in want:
            remove.append({'kind': 'PartyTether', 'was': hp['params'], 'why': 'derived rule with cells [] is a no-op and no script of the quest itself needs the party together'})
        ent = {'op': 'merge' if edid in existing else 'add', 'add_rules': add_rules, 'update_rules': update, 'remove_rules': remove}
        if edid not in existing:
            rk = d.risk[q['ref']]
            ent.update({'tier': rk['tier'], 'questline': 'Main: Legion', 'curated': True})
        ent['rationale'] = CURATED.get(edid, {}).get('why', '')
        if CURATED.get(edid, {}).get('cells_confidence'):
            ent['cells_confidence'] = CURATED[edid]['cells_confidence']
        patch['quests'][edid] = ent
    return patch


def _merge_params(kind, have, want):
    """Union lists, overwrite empty/no-op cells, add function reductions without changing existing ones."""
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in have.items()}
    for k, v in want.items():
        if isinstance(v, list):
            base = out.get(k) or []
            for x in v:
                if x not in base:
                    base.append(x)
            out[k] = base
        elif isinstance(v, dict):
            base = out.get(k) or {}
            for kk, vv in v.items():
                base.setdefault(kk, vv)
            out[k] = base
        else:
            out.setdefault(k, v)
    # a curated tether replaces an empty cell list wholesale (cells [] is a no-op tether)
    if kind == 'PartyTether' and not have.get('cells') and want.get('cells'):
        out['cells'] = list(want['cells'])
        out['gather'] = want.get('gather', out.get('gather'))
    return out
