"""rules.patch.json + policy_review.md for the prologue questline.

The patch is a PROPOSAL (nobody applies it automatically): ops against docs/quest-policies/rules.json, evidence lines are pulled
from the gap analysis so they always point at real script:line locations. Only the 16 existing rule kinds are used, plus two
justified new kinds (`new_kinds`) and a few new params on existing kinds (listed in `param_extensions`).
"""
import collections

import gaps as gaps_mod
import inventory as inv_mod
import model

KINDS_EXISTING = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner',
                  'SaveSuppress', 'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner',
                  'ShareKarmaRep', 'InitiatorDialogue', 'ExcludeFromGate']

NEW_KINDS = [
    dict(kind='ExclusiveStages',
         params_schema={'stages': 'list[int] stage numbers that are mutually exclusive outcomes', 'resolution': '"first-wins"'},
         why='Stages merge as max (MASTER-PLAN merge rules). nVPrimmDeputyConv endings 110/120/130 and VNipton 30 (rescued) / 35 and 70 '
             '(failed) are alternatives, not progress: if two outcomes race (two initiators, or a player action against an NPC death '
             'script) the highest number wins instead of the first. No existing kind expresses this.',
         engine_work='synccore Merge: for a rule-listed stage set, the first committed SetStage in host order wins; later ones in the set '
                     'are rejected (host) / ignored (clients) and counted. Game-free, unit-testable.'),
    dict(kind='ShareMapMarkers',
         params_schema={'markers': 'list[EditorID] map marker REFRs whose ShowMap reveal is shared by the whole party'},
         why='`ShowMap <marker>` (17 occurrences in this questline, e.g. VMQ01 stage 40/50/100, Beagle journal answer) reveals a map marker '
             'and unlocks fast travel for the player. There is no Effect type, no capture hook and no applier call; without it the '
             'party sees different maps and objective targets differ. Closest existing kinds (FastTravelLock, ShareKarmaRep) do not '
             'cover discovery state.',
         engine_work='new Effect `SetMapMarkerVisible{marker, visible}` (state, host order, merge = OR), capture at the ShowMap command '
                     'handler / marker visibility flag, apply via the marker visibility byte (GetMapMarkerVisible is already read by '
                     'VMQ01QuestScript).'),
]

PARAM_EXTENSIONS = [
    dict(kind='QuestItemDup', param='items', type='list[EditorID]', why='say WHICH quest notes/items are duplicated; today the kind is '
         'quest-wide and the engine cannot tell a quest note from loot'),
    dict(kind='RemoveItemPerOwner', param='items', type='list[EditorID]', why='scope the hand-in rule to the paid items (caps, parts)'),
    dict(kind='CompanionOwner', param='npcs', type='list[EditorID]', why='which NPC refs are recruited by this quest'),
    dict(kind='InitiatorDialogue', param='forced', type='list[{npc,trigger,initiator}]', why='NPC-started conversations (door/trigger '
         'activation) need the activator bound as initiator'),
    dict(kind='PartyAwareCond', param='funcs.<fn>', type='reduction string', why='new functions: GetHasNote, IsPlayerInRegion, '
         'GetPlayerTeammate (existing vocabulary of reductions)'),
]


def _loc(g, fn, script_substr=None, limit=3, text=None):
    out = []
    for x in g['by_function']:
        if x['fn'] != fn:
            continue
        for l in x['locations']:
            if text and text.lower() not in l['text'].lower():
                continue
            if script_substr is None or script_substr.lower() in l['script'].lower():
                out.append('%s:%d %s' % (l['script'], l['line'], l['text'][:80]))
    return out[:limit]


def _read(g, fn, script_substr, limit=3):
    out = []
    for r in g['player_reads']:
        if r['fn'] == fn:
            for l in r['locations']:
                if script_substr.lower() in l['script'].lower():
                    out.append('%s:%d %s' % (l['script'], l['line'], l['text'][:80]))
    return out[:limit]


def build_patch(g=None):
    g = g or gaps_mod.build_gaps()
    ops = []

    def op(oid, kind_op, quest, rule_kind, params, why, evidence, status='proposed', priority='medium', tier=None, questline=None):
        d = dict(id=oid, op=kind_op, quest=quest, kind=rule_kind, params=params, why=why, evidence=evidence, status=status,
                 priority=priority)
        if tier:
            d['tier'] = tier
        if questline:
            d['questline'] = questline
        ops.append(d)

    # ---- VMQ01
    op('P01', 'merge_params', 'VMQ01', 'PartyAwareCond', {'funcs': {'GetHasNote': 'any-party-member'}},
       'VMQ01QuestScript polls `GetHasNote` in its GameMode block to advance the quest (Jessup note -> SetStage VMQ01 100 + VMQTops 10; '
       'Beagle journal -> objectives 36/38). Quest scripts run on the host only, so without a reduction the host character\'s notes decide.',
       _read(g, 'gethasnote', 'VMQ01'), priority='high')
    op('P02', 'merge_params', 'VMQ01', 'QuestItemDup',
       {'recipient': 'all', 'items': ['VMQ01JessupNote', 'VMQ01BeagleJournalNote', 'VNovacGreatKhansNote']},
       'The three notes that drive VMQ01 are picked up from bodies/containers (VMQ01NoteObjectSCRIPT OnAdd player) or added by AddNote; '
       'every player must end up with them (owner decision: quest items duplicated). Pickup is not a script command: needs the '
       'container-transfer observer (gaps.md, blockers #1 and #2).',
       _loc(g, 'addnote', limit=2) + ['VMQ01NoteObjectSCRIPT:2 BEGIN OnAdd player / Player.AddNote VNovacGreatKhansNote'], priority='high')
    op('P03', 'add_rule', 'VMQ01', 'ShareMapMarkers',
       {'markers': ['NovacMapMarker', 'BoulderCityMapMarker', 'FreesideMapMarker']},
       'VMQ01 stages 40, 50 and 100 reveal Novac, Boulder City and Freeside through ShowMap; new kind, see new_kinds.',
       _loc(g, 'showmap', 'VMQ01', 4))
    # ---- nVPrimmDeputyConv
    op('P04', 'add_rule', 'nVPrimmDeputyConv', 'CompanionOwner', {'owner': 'recruiter', 'npcs': ['PrimmDeputyRef']},
       'Persuading the hostage Beagle (Speech >= 25) makes him join the PLAYER faction and follow (BeagleFollow). He follows the recruiter, '
       'not the host. SetPlayerTeammate itself is not used; the follow is AddToFaction playerfaction + package state.',
       _loc(g, 'addtofaction', 'nVPrimmDeputyConv', 2), priority='high')
    op('P05', 'add_rule', 'nVPrimmDeputyConv', 'ExclusiveStages', {'stages': [110, 120, 130], 'resolution': 'first-wins'},
       'The three sheriff endings are alternatives (NCR sergeant, Meyers, reprogrammed Primm Slim). Max-merge would let the highest win '
       'after a race; see new_kinds.',
       ['nVPrimmDeputyConv stage 110/120/130 each: completeallobjectives + a different PrimmState (1/2/4)'], priority='medium')
    op('P06', 'add_rule', 'nVPrimmDeputyConv', 'RemoveItemPerOwner', {'items': ['FissionBattery', 'Conductor']},
       'Primm Slim repair parts are paid by the initiator who reprograms him (INFO 173886 begin), not by the whole party.',
       _loc(g, 'removeitem', 'nVPrimmDeputyConv', 2))
    op('P07', 'merge_params', 'nVPrimmDeputyConv', 'QuestItemDup',
       {'recipient': 'all', 'items': ['PrimmSheriffPetition%d' % i for i in range(6)] + ['VFreeformPrimmSheriff']},
       'The sheriff petition is a chain of notes (PrimmSheriffPetition0..5) replaced via AddNote/RemoveNote as signatures accumulate in '
       'shared quest variables (PetitionActive, NCRPetition, NoteNumber). The notes must follow the shared counter on every player.',
       _loc(g, 'removenote', 'PrimmDeputyScript', 2) + _loc(g, 'addnote', 'nVPrimmDeputyConv', 1))
    op('P08', 'add_rule', 'nVPrimmDeputyConv', 'ShareMapMarkers',
       {'markers': ['NCRCFMapMarker', 'MojaveOutpostMapMarker', 'NiptonMapMarker', 'NovacMapMarker']},
       'Stage 26 reveals the NCR Correctional Facility and Mojave Outpost; the Beagle answer to "people who passed through" (INFO 1618BD/1618BE) reveals Nipton and Novac.',
       _loc(g, 'showmap', 'nVPrimmDeputyConv', 4))
    # ---- VNipton
    op('P09', 'add_rule', 'VNipton', 'ExclusiveStages', {'stages': [30, 35, 70], 'resolution': 'first-wins'},
       'Rescued (30, complete) vs captives killed (35, stopquest) vs failure (70). The captive death scripts and the rescue trigger can '
       'race across machines.', ['vNiptonSCRIPT:30 setstage vnipton 30', 'NiptonSurvivorDeathScript:13 setStage VNipton 35',
                                 'boxcarscrippledSCRIPT:12 setstage vnipton 70'], priority='medium')
    op('P10', 'merge_params', 'VNipton', 'PartyAwareCond', {'funcs': {'GetPlayerTeammate': 'any-party-member'}},
       'CraigBoone\'s Nipton reaction (stage 30: `CraigBooneREF.SayTo player ...` gated by VNPCFollowers.bBooneHired) and his dependency script '
       'read GetPlayerTeammate; Boone belongs to whoever hired him (CompanionOwner is the companion line\'s rule).',
       _loc(g, 'sayto', 'VNipton', 1))
    # ---- Victor
    op('P11', 'merge_params', 'VES01aVictorInNovac', 'PartyAwareCond', {'funcs': {'IsPlayerInRegion': 'any'}},
       'The quest script moves Victor from Goodsprings to Novac when the player enters region vMapVictorInNovacRegion. Any party member '
       'entering the region must trigger it (host-only evaluation otherwise).',
       _read(g, 'isplayerinregion', 'VES01a', 1), priority='medium')
    # ---- dialogue containers without a rules.json entry
    op('P12', 'add_quest', 'PrimmNashDialogue', 'InitiatorDialogue', {},
       'T2 dialogue container (no rules.json entry today). Its INFO result scripts mutate shared quests (VMQ01, nVPrimmDeputyConv 20/26, '
       'VMS02 35) and take caps/parts from the player.',
       ['PrimmNashDialogue info 15EF34 begin: setobjectivecompleted VMQ01 30 1 ; setStage nvPrimmDeputyConv 20'], tier='T2',
       questline='Main: Prologue')
    op('P13', 'add_quest', 'VDialogueNovac', 'InitiatorDialogue', {},
       'T2 dialogue container with Manny Vargas (VMQ01 stage 50, bPrimmClosed) and Ranger Andy (AddPerk).',
       ['VDialogueNovac info 15FB9E end: SetStage VMQ01 50'], tier='T2', questline='Main: Prologue')
    op('P14', 'add_quest', 'VFreeformBoulderCity', 'InitiatorDialogue',
       {'forced': [{'npc': 'LieutenantMonroeRef', 'trigger': 'BoulderCityRuinsDoor', 'initiator': 'activator'}]},
       'T2 dialogue container with Jessup (VMQ01 stage 100 + VMQTops 10), Monroe and Kowalski. `BoulderCityRuinsDoor` runs '
       '`LieutenantMonroeRef.StartConversation player` on `IsActionRef player`: an NPC-started conversation, so the door activator must be '
       'the initiator and the others spectate.',
       ['VFreeformBoulderCity info 153A2D begin: SetStage VMQ01 100 ; SetStage VMQTops 10',
        'BoulderCityRuinsDoor:12 LieutenantMonroeRef.StartConversation  player (dep obj, risk.json)'], tier='T2', questline='Main: Prologue')
    op('P15', 'add_rule', 'VFreeformBoulderCity', 'PartyAwareCond', {'funcs': {'IsActionRef': 'initiator', 'GetShouldAttack': 'any-party-member'}},
       'The ruins door and Monroe read `IsActionRef player` and `LieutenantMonroeRef.GetShouldAttack player` (dep obj BoulderCityRuinsDoor:5/11).',
       ['BoulderCityRuinsDoor:5 if IsActionRef player', 'BoulderCityRuinsDoor:11 elseif (LieutenantMonroeRef.GetShouldAttack player == 0)'])
    # follow-up rules for the new T2 entries (same patch, grouped by quest)
    # (kind, params, fn, quest-script filter) -> evidence from the gap analysis; build fails when a claim has no evidence
    extra = {
        'PrimmNashDialogue': [('RewardAll', {}, 'rewardxp', None),
                              ('RemoveItemPerOwner', {'items': ['caps001', 'RadscorpionPoisonGland']}, 'removeitem', None)],
        'VDialogueNovac': [('RewardAll', {}, 'additem', 'caps'), ('ShareKarmaRep', {}, 'addreputation', None),
                           ('RemoveItemPerOwner', {'items': ['caps001']}, 'removeitem', None),
                           ('QuestItemDup', {'recipient': 'all', 'items': ['VFreeformNovacRangerStationNote', 'vNovacRoomKey']}, 'additem', 'roomkey'),
                           ('ShareMapMarkers', {'markers': ['RangerStationCharlieMapMarkerREF']}, 'showmap', None)],
        'VFreeformBoulderCity': [('RewardAll', {}, 'rewardxp', None), ('ShareKarmaRep', {}, 'addreputation', None),
                                 ('RemoveItemPerOwner', {'items': ['caps001']}, 'removeitem', 'caps'),
                                 ('QuestItemDup', {'recipient': 'all', 'items': ['VBennysLighter']}, 'additem', 'VBennysLighter'),
                                 ('ShareMapMarkers', {'markers': ['CampMcCarranMapMarker']}, 'showmap', None)],
    }
    why = {('RewardAll', 'rewardxp'): 'RewardXP in its INFO result scripts: full XP to every player',
           ('RewardAll', 'additem'): 'Player.AddItem caps001 (75/50/150) in its INFO result scripts: caps reward to every player',
           ('ShareKarmaRep', 'addreputation'): 'AddReputation (RepNV*) / RewardKarma in its INFO scripts: shared reputation and karma',
           ('RemoveItemPerOwner', 'removeitem'): 'Player.RemoveItem payments in its INFO scripts: only the initiator pays',
           ('QuestItemDup', 'additem'): 'quest item handed to the player by a result script: every player must hold it',
           ('ShareMapMarkers', 'showmap'): 'ShowMap in its INFO scripts: shared reveal'}
    n = 16
    for q, rs in extra.items():
        for k, p, fn, filt in rs:
            ev = _loc(g, fn, q, 2, text=filt)
            if fn == 'addreputation' and not ev:
                ev = _loc(g, 'rewardkarma', q, 2)
            assert ev, 'no %s evidence in %s' % (fn, q)
            op('P%02d' % n, 'add_rule', q, k, p, why[(k, fn)], ev)
            n += 1
    open_q = [
        dict(id='Q1', topic='Perk from a trainer dialogue', question='Ranger Andy teaches Ranger Takedown to the initiator (Speech 30, say-once). '
             'Does every player get the perk (rewards-to-all) or only the initiator (personal training)? Recommendation: initiator only, and make '
             'the say-once flag per player for this INFO so the others can still ask.',
             evidence=_loc(g, 'addperk', None, 1), blocks='AddPerk capture/apply (SetPerk effect) either way'),
        dict(id='Q2', topic='Where do shared map reveals stop?', question='Should ShowMap reveal the marker for every player (recommended: '
             'yes, the party is one Courier) or only the player who heard the lead?', evidence=_loc(g, 'showmap', 'VMQ01', 2),
             blocks='new kind ShareMapMarkers'),
        dict(id='Q3', topic='Companion Beagle after recruit', question='Beagle only follows after a Speech-25 pass; if the initiator fails '
             'he is simply freed (stage 25 without follow). Should the follow be offered to the other players as a later separate topic?',
             evidence=_loc(g, 'addtofaction', 'nVPrimmDeputyConv', 1), blocks='none (design)'),
    ]
    return dict(
        schema=1, questline='prologue', target='docs/quest-policies/rules.json',
        note='PROPOSAL ONLY. Ops are against rules.json (keyed by quest EditorID). `merge_params` merges into the existing rule of that kind; '
             '`add_rule` appends; `add_quest` creates the quest entry (tier/questline given) and its rules follow as add_rule ops. '
             'origin for all of these would be "proposed"/"curated" once accepted.',
        kinds_existing=KINDS_EXISTING, new_kinds=NEW_KINDS, param_extensions=PARAM_EXTENSIONS, ops=ops, open_owner_questions=open_q)


# ---------------------------------------------------------------------------------------------------------------------
def build_review(g=None, patch=None, inv=None):
    g = g or gaps_mod.build_gaps()
    patch = patch or build_patch(g)
    inv = inv or inv_mod.build_inventory()
    import dialogue as dlg
    S = dlg.survey()
    cnt = {x['fn']: x['count'] for x in g['by_function']}
    n_init = sum(1 for q in inv['quests'] if 'InitiatorDialogue' in q['policy']['rule_kinds'])
    L = ['# Prologue: policy review', '',
         'Reviewed against `docs/MASTER-PLAN.md` (owner decisions, locked 2026-10-15), `docs/quest-policies/main-prologue.md` / '
         '`main-shared-ring-a-ding-ding-and-act-2.md` / `side-primm.md` / `side-novac.md` / `side-nipton.md` / `side-boulder-city.md`, and '
         '`rules.json` (16 kinds). Generated by `questlines/prologue/build.py`; evidence lines come from `gaps.json`. '
         'Proposals are in `rules.patch.json` (ids P01...).', '']
    L += ['## 1. Verdict in one paragraph', '',
          'This questline contains **no cutscene, no control lock, no player teleport and no scripted fast-travel lock**: the checklist '
          'in gaps.md finds 0 hits for DisablePlayerControls/PlayBink/PlaceAtMe/SetEssential/EndGame/radio/save and no `player.MoveTo` '
          '(the only MoveTo calls move NPCs). So the barrier/tether/cinematic-control questions the brief raises are answered "not '
          'needed here" for the in-scope stages, and the real risks are elsewhere: (a) quest scripts that poll the HOST\'s notes and '
          'region, (b) quest-item notes obtained by looting (not a script command), (c) faction/perk/note effects that nothing captures, '
          '(d) mutually exclusive endings under max-merge, (e) shared map reveals. The existing rules already cover rewards, karma/rep, '
          'quest items and initiator dialogue for the T3 quests; three T2 dialogue containers have no rules entry at all.', '']
    L += ['## 2. Owner decisions, applied to this questline', '',
          '| decision | applies how here | status |', '|---|---|---|',
          '| Karma shared | `RewardKarma` x%d in scope, `AddReputation` x%d: captured as ModKarma/ModReputation, applied to everyone '
          '(`ShareKarmaRep`) | covered; Novac/Boulder City containers lack the rule entry (P13-P14) |' % (cnt.get('rewardkarma', 0), cnt.get('addreputation', 0)),
          '| Faction reputation shared | RepNV* gains in the same scripts | covered by the same rule |',
          '| Quest items duplicated | notes: VMQ01JessupNote, VMQ01BeagleJournalNote, VNovacGreatKhansNote, petition notes, ranger/sniper notes | rule exists, '
          'but cannot see looted notes (gaps) -> P02, P07 |',
          '| Caps/XP quest rewards to everyone | RewardXP x%d (VMQ01 100: 1000, nVPrimmDeputyConv endings: 300 each, VNipton 30: 100) | `RewardAll` present on '
          'VMQ01, nVPrimmDeputyConv, VNipton; missing on the 3 containers (P16+) |' % cnt.get('rewardxp', 0),
          '| Dialogue: initiator chooses | %d skill-gated + %d stage-writing INFOs, see dialogue.md | `InitiatorDialogue` present on %d of %d quests; P12-P14 |' % (len(S['checks']), len(S['branch']), n_init, len(inv['quests'])),
          '| Party spread: free roam, story moments tether | no tether-worthy moment found before Benny is located | no rule needed (section 4) |',
          '| Faction membership/rank shared | only NPC faction changes in scope (Beagle -> playerfaction, Kowalski / NCR sniffer swaps in Boulder City); no player membership | n/a, but '
          'AddToFaction/RemoveFromFaction are still uncaptured (gaps) |',
          '| Companions owned by recruiter | Beagle (Speech 25), Boone (Nipton reaction) | P04, P10 |',
          '| Max players 4 (open) | nothing here depends on it | n/a |', '']
    L += ['## 3. Per-quest review against rules.json', '',
          '| quest | tier | existing rule kinds | what the scripts need | verdict / patch |', '|---|---|---|---|---|']
    need = {
        'VMQ01': 'RewardXP 1000 (stage 100), ShowMap in stages 40/50/100, notes polled by quest script (GetHasNote), 3 object scripts write it (note pickup, '
                 'Victor, Tops intro trigger)',
        'PrimmNashDialogue': 'Nash trade (caps/glands), VMQ01 30/34 + nVPrimmDeputyConv 20/26 + VMS02 35 writes, barter menu',
        'nVPrimmDeputyConv': 'Beagle hostage (Speech 25) + follow, sheriff endings 110/120/130 (+300 XP each), petition notes, Primm Slim parts, '
                             '2 ShowMap, NPC MoveTo/MoveToFade',
        'VES01aVictorInNovac': 'Victor Resurrect + MoveTo Novac, IsPlayerInRegion poll in the quest script',
        'VDialogueNovac': 'Manny answer (VMQ01 50 + bPrimmClosed), Ranger Andy perk, 3 AddNote, caps rewards 50-150 + room key, 5 RepNVNovac gains, ShowMap',
        'VNipton': 'rescue/fail outcomes 30/35/70, +100 XP, Powder Ganger rep, Boone SayTo reaction, captive death scripts (Legion alarm)',
        'VFreeformBoulderCity': 'Jessup chip answer (VMQ01 100 + VMQTops 10), Monroe/Jessup bribes (400/500 caps), Kowalski rep + hostility, '
                                'forced Monroe conversation at the ruins door',
        'VMQTops': 'lead-in only: stage 5 (Securitron message triggers), stage 10 (Benny intro trigger, also forces VMQ01 100)',
    }
    verdict = {
        'VMQ01': 'P01, P02, P03', 'PrimmNashDialogue': 'P12 + P16-P17 (new entry)', 'nVPrimmDeputyConv': 'P04-P08',
        'VES01aVictorInNovac': 'P11', 'VDialogueNovac': 'P13 + P18-P22 (new entry); open question Q1',
        'VNipton': 'P09, P10', 'VFreeformBoulderCity': 'P14, P15 + P23-P26 (new entry)',
        'VMQTops': 'no change for the lead-in; PartyTether/Warp/Controls stay with the Ring-a-Ding-Ding line',
    }
    for q in inv['quests']:
        pol = q['policy']
        L.append('| %s | %s | %s | %s | %s |' % (q['edid'], q['risk']['tier'],
                                                ', '.join(pol['rule_kinds']) if pol['in_rules_json'] else '(no entry)',
                                                need[q['edid']], verdict[q['edid']]))
    L += ['', '## 4. Barriers, tethers, cinematic control, reward routing', '',
          '**Barriers.** `Barrier` (shared stage waits for all players) is for per-player scenes (chargen). Every stage written here is advanced by a '
          'single conversation or a trigger, none by a per-player scene, so no barrier. Max-merge of stages is right for the chain '
          '(VMQ01 10 -> 40 -> 50 -> 100) and WRONG for the exclusive endings (P05, P09).', '',
          '**Tethers.** The brief asks for "party in the same area" at story moments. Candidates checked: Jessup\'s answer (Boulder City), '
          'Manny\'s answer (Novac), the Tops intro trigger. None locks controls, moves the player or starts a scene, so the party may stay '
          'spread (owner decision: free roam). Two spatial semantics matter: `VTopsBennyIntroTriggerSCRIPT` (OnTriggerEnter, `IsActionRef player`) '
          'fires for whoever enters first and finishes VMQ01 for everyone (correct, = "initiator"), and `VES01aVictorInNovac` reacts to ANY '
          'player entering a region (P11). The real tether starts at VMQTops stage 20 (the Presidential Suite scene), which is the next questline.', '',
          '**Who controls cinematics.** Nothing in scope disables controls. The only forced conversations are the Monroe door at the Boulder City '
          'ruins (`StartConversation player`, P14) and Boone\'s Nipton reaction (`SayTo player`, stage 30, only when Boone is hired). In both the '
          'initiator controls the conversation (vanilla locks only the initiator\'s movement in dialogue); spectators are not locked.', '',
          '**Reward routing.** XP: `RewardXP` goes to every player in full. Karma/reputation: shared. Quest notes: duplicated. Payments '
          '(`Player.RemoveItem caps001 50..500`, glands, parts, morphine): the initiator\'s inventory only (`RemoveItemPerOwner`). Perks '
          '(`Player.AddPerk RangerTakedown`): UNDECIDED (open question Q1), and the hook neither captures nor applies it. Achievements '
          '(`AddAchievement 32`): local per player, intentionally not synced.', '']
    # reward lines table
    L += ['### Reward/penalty lines in the questline scripts', '', '| fn | count | effect type | capture | apply |', '|---|---:|---|---|---|']
    for fn in ('rewardxp', 'rewardkarma', 'addreputation', 'additem', 'removeitem', 'addnote', 'removenote', 'addperk'):
        for x in g['by_function']:
            if x['fn'] == fn:
                L.append('| %s%s | %d | %s | %s | %s |' % (fn, ' (NPC target)' if x['capture'] == 'missing' and fn in ('additem', 'removeitem') else '',
                                                           x['count'], x['effect'] or '-', x['capture'], x['apply']))
    L += ['', '## 5. Cross-line couplings (this questline writes stages owned by others)', '']
    for q in inv['quests']:
        if q['cross_quest_stage_writes']:
            L.append('- **%s** -> %s' % (q['edid'], ', '.join('%s (x%d)' % kv for kv in sorted(q['cross_quest_stage_writes'].items()))))
    L += ['', 'Each of these is an external trigger for the owning line: VMS02 (I Fought the Law), VMS04, VMS01 (Come Fly With Me) and the Vulpes '
          'dialogue quest are driven from Primm/Novac/Nipton conversations scanned here.', '',
          '## 6. Proposals (rules.patch.json)', '', '| id | op | quest | kind | priority | why |', '|---|---|---|---|---|---|']
    for o in patch['ops']:
        L.append('| %s | %s | %s | %s | %s | %s |' % (o['id'], o['op'], o['quest'], o['kind'], o['priority'], o['why'][:140].replace('|', '/')))
    L += ['', '### New rule kinds (justified)', '']
    for k in patch['new_kinds']:
        L += ['- **%s**: %s Engine work: %s' % (k['kind'], k['why'], k['engine_work'])]
    L += ['', '### New params on existing kinds', '']
    for p in patch['param_extensions']:
        L.append('- `%s.%s` (%s): %s' % (p['kind'], p['param'], p['type'], p['why']))
    L += ['', '### Open owner questions', '']
    for q in patch['open_owner_questions']:
        L.append('- **%s %s**: %s Evidence: %s. Blocks: %s.' % (q['id'], q['topic'], q['question'], '; '.join(q['evidence']) or 'n/a', q['blocks']))
    L.append('')
    return '\n'.join(L)
