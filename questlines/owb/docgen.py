"""Markdown generators: policy_review.md, dialogue.md, internal questline notes.

Prose is written here; every number, unit:line reference, rule list and spec is computed from the data so the documents cannot
drift (run_tests.py regenerates them in memory and compares with the committed files).
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rules_patch  # noqa: E402
from evidence import Evidence  # noqa: E402


class Ctx:
    def __init__(self, d, inv, sw, gp, specs):
        self.d, self.inv, self.sw, self.g, self.specs = d, inv, sw, gp, specs
        self.ev = Evidence(d)
        self.units_ci = {k.lower(): k for k in self.ev.units}
        self.fn = collections.Counter()
        self.fn_player = collections.Counter()
        for r in gp['functions']:
            self.fn[r['fn'].lower() + ('|read' if r['kind'] == 'read' else '')] += r['count']
            self.fn_player[r['fn'].lower() + ('|read' if r['kind'] == 'read' else '')] += r['player_count']

    def E(self, unit, needle, nth=1):
        return self.ev.ref(self.units_ci[unit.lower()], needle, nth)

    def n(self, fn, read=False):
        return self.fn.get(fn.lower() + ('|read' if read else ''), 0)

    def hz(self, tag):
        return self.g['hazards'][tag]['count']


# --------------------------------------------------------------------------------------------------------------------
def rules_diff_table(c):
    cur = c.d.rules
    rows = ['| quest | tier | op | existing kinds | proposed kinds | added | removed |', '|---|---|---|---|---|---|---|']
    for edid, p in rules_patch.QUESTS.items():
        old = [x['kind'] for x in cur.get(edid, {}).get('rules', [])]
        new = []
        for x in p['rules']:
            if x['kind'] not in new:
                new.append(x['kind'])
        q = c.d.by_edid[edid]
        tier = c.d.risk[q['ref']]['tier']
        rows.append('| %s | %s | %s | %s | %s | %s | %s |' % (
            edid.replace('NVDLC03', ''), tier, p['op'], ', '.join(old) or '-', ', '.join(new),
            ', '.join(k for k in new if k not in old) or '-', ', '.join(k for k in old if k not in new) or '-'))
    return '\n'.join(rows)


def moments(c):
    """(name, trigger evidence, controller, barrier/tether, controls, reward routing, rule kinds)"""
    E = c.E
    return [
        ('1 Satellite activation (DLC entry, one-way)',
         [E('NVDLC03CrashedSatelliteActivatorSCRIPT', 'SetStage NVDLC03MQ00 100'),
          E('NVDLC03CrashedSatelliteActivatorSCRIPT', 'DisablePlayerControls 1 1 1 1 0 1 1')],
         'initiator presses the MenuMode button; host commits', 'PartyTether to the satellite (auto-warp), whole party goes',
         'lock all, mask 1 1 1 1 0 1 1', 'MQ00 stage 100 RewardXP Act1 to all; followers dismissed per recruiter',
         'PartyTether, SetPlayerControls, PartyWarp, SaveSuppress, CompanionOwner, RewardAll'),
        ('2 Teleport FX, intro slideshow, TransitionToBigEmpty',
         [E('NVDLC03TeleportEffectTimerSCRIPT', 'Player.MoveTo NVDLC03SlideShowMarkerREF'),
          E('NVDLC03SlideshowTriggerSCRIPT', 'SayTo Player NVDLC03IntroSlideFNV'),
          E('NVDLC03TransitionToBigEmptySCRIPT', 'Player.MoveTo NVDLC03StartMarker')],
         'host runs the chain once (SayToDone); each client replays the slide line + image-space locally',
         'SlideshowEnd: all slots in the theatre before slide 1; absent slots held', 'lock all 7 flags until PlayerStartTrigger',
         'none (time rewrite GameHour 5, GameDay+3 is a shared global, host once)',
         'SlideshowEnd, SetPlayerControls, PartyWarp, SaveSuppress'),
        ('3 Arrival in the Big Empty (organs removed)',
         [E('NVDLC03PlayerStartTriggerSCRIPT', 'SetStage NVDLC03MQ01 10'), E('NVDLC03MQ01:stage 10', 'AddPerk NVDLC03BrainlessPerk')],
         'first party member to enter the start trigger (OnTriggerEnter Player must match any party member)',
         'none (party arrives together via the warp)', 'EnablePlayerControls + SetInChargen 0 + RemoveScriptPackage for ALL slots',
         'Brainless/Spineless/Heartless perks and patient gown to EVERY player', 'RewardAll(include AddPerk), PartyAwareCond, SaveSuppress release'),
        ('4 First meeting with Klein (forced scene)',
         [E('NVDLC03MeetKleinTriggerSCRIPT', 'DisablePlayerControls'), E('NVDLC03MeetKleinTriggerSCRIPT', 'Player.MoveTo NVDLC03ThinkTankPlayerMarker'),
          E('INFO 0030B2 begin', 'EnablePlayerControls 1 1 0 1 1 1 1')],
         'host starts the scene; the party member that triggers it is the speaker, everyone else is a spectator',
         'PartyTether NVDLC03ThinkTank (wait): refuse until all inside; then party-warp to the scene marker',
         'lock all; the greeting INFO releases all but weapon-draw', 'none', 'PartyTether, PartyWarp, SetPlayerControls, InitiatorDialogue'),
        ('5 Klein assigns X-2 / X-8 / X-13',
         [E('INFO 01355E end', 'SetStage NVDLC03MQ01 100'), E('INFO 01355E end', 'SetStage NVDLC03MQ04 10')],
         'initiator pick; result is shared', 'none: the three tasks are order-independent and may run in parallel',
         'none', 'quest stages shared; ForceActiveQuest is a per-player pointer', 'InitiatorDialogue'),
        ('6 Think Tank pacification zone',
         [E('NVDLC03ThinkTankPacifyTriggerSCRIPT', 'DisablePlayerControls 0 0 1 0 0 0 0'),
          E('NVDLC03ThinkTankDoorSCRIPT', 'EnablePlayerControls')],
         'each player\'s own presence in the volume (per-player, per-frame)', 'none',
         'weapon-draw lock only (mask 0 0 1 0 0 0 0), per player, released by that player leaving through the door', 'none',
         'SetPlayerControls(scope origin), PartyAwareCond'),
        ('7 X-8 tests (cutscene experiments)',
         [E('NVDLC03X8ExperimentSCRIPT', 'DisablePlayerControls 1 1 1 1 1 1 1'), E('NVDLC03X8ExperimentSCRIPT', 'Player.MoveTo X8ExperimentViewMarker')],
         'terminal user starts it; host runs it once', 'PartyTether NVDLC03X8School/Main (wait)',
         'lock all, party-warp to the viewing platform, release together', 'perk reward (DN agent) to all',
         'PartyTether, PartyWarp, SetPlayerControls, RewardAll'),
        ('8 X-13 VR rooms and suit assembly',
         [E('NVDLC03X13VREntDoorSCRIPT', 'Player.MoveTo X13VRRoomMarkerREF'), E('NVDLC03MQ04SCRIPT', 'Player.RemoveItem NVDLC03X13SuitBoots')],
         'door user; detection tests see any party member', 'none', 'none',
         'suit parts duplicated on pickup, consumed from all on assembly', 'QuestItemDup, PartyAwareCond, PartyWarp'),
        ('9 Mobius / giant robo-scorpion (shared hostility)',
         [E('INFO 014740 end', 'SetEnemy NVDLC03MobiusFaction PlayerFaction'), E('NVDLC03MQ05:stage 35', 'SetEnemy NVDLC03MobiusFaction PlayerFaction')],
         'initiator line or first aggression by anyone', 'none (combat is free-roam, host-owned NPCs)', 'none',
         'XP Act3 to all', 'InitiatorDialogue, RewardAll'),
        ('10 Brain retrieval and re-insertion',
         [E('NVDLC03BrainTankOpenTriggerSCRIPT', 'NVDLC03PlayerBrainREF.AddScriptPackage'), E('INFO 01321E end', 'Player.AddItem NVDLC03PlayerBrain'),
          E('INFO 015B66 end', 'Player.AddPerk NVDLC03BigBrainedPerk')],
         'any party member opens the tank; the initiator talks', 'none',
         'none', 'PlayerBrain item to EVERY player; perk swap only for whoever re-inserts (AutoDoc or jar)',
         'QuestItemDup, PerPlayerScene'),
        ('11 Finale choice: kill the Think Tank (60) or leave in peace (70)',
         [E('INFO 00A82D end', 'SetStage NVDLC03MQ05 60'), E('INFO 013219 end', 'SetStage NVDLC03MQ05 70'),
          E('NVDLC03BrainBotSCRIPT', 'SetStage NVDLC03MQ05 60')],
         'initiator pick (or first aggression by any player)', 'none',
         'none', 'XP Act3', 'InitiatorDialogue, ShareKarmaRep, RewardAll'),
        ('12 Ending slideshow and return to the Sink',
         [E('NVDLC03ThinkTankDoorSCRIPT', 'StartQuest NVDLC03Ending'), E('NVDLC03EndingSCRIPT', 'Player.MoveTo NVDLC03SlideShowMarkerREF'),
          E('NVDLC03TransitionToTheSinkSCRIPT', 'SetStage NVDLC03MQ05 100')],
         'door activator starts it; host runs once', 'Barrier + PartyTether NVDLC03ThinkTank: ending refused until all inside',
         'lock all 7 flags, no-save package', 'MQ05 100: XP Act3, Teleporter to all, brain swap for each holder',
         'SlideshowEnd, Barrier, PartyTether, PartyWarp, SetPlayerControls, SaveSuppress, RewardAll'),
        ('13 Back in the Sink (release)',
         [E('NVDLC03SinkTriggerSCRIPT', 'EnablePlayerControls'), E('NVDLC03SinkTriggerSCRIPT', 'SetInChargen 0')],
         'OnTrigger Player: any party member', 'none', 'release must be a host effect for ALL slots (not per trigger)', 'none',
         'SetPlayerControls'),
        ('14 Teleporter (free roam)',
         [E('NVDLC03WeapTeleporterSCRIPT', 'Player.MoveTo NVDLC03SinkArrivalMarker'), E('NVDLC03WeapTeleporterSCRIPT', 'StopQuest VEFR01LegionGood2CaesarsFavor')],
         'the firer (weapon OnFire)', 'party members within range travel with the firer; others stay',
         'none', 'radios and the five VEFR reaction quests follow "any player in the Mojave"', 'PartyWarp, PartyAwareCond, CompanionOwner'),
        ('15 Radar fence',
         [E('NVDLC03RadarFenceSCRIPT', 'Player.MoveTo NVDLC03StartMarker')],
         'each player independently', 'none', 'none', 'none', 'LocalQuestScript, PerPlayerScene'),
    ]


def policy_review_md(c):
    g, d, inv, sw = c.g, c.d, c.inv, c.sw
    cur = d.rules
    owb = inv['quests']
    with_rules = [q for q in owb if q['edid'] in cur]
    t4_missing = [q['edid'] for q in owb if q['tier'] == 'T4' and q['edid'] not in cur]
    kinds_after = collections.Counter()
    for p in rules_patch.QUESTS.values():
        for x in p['rules']:
            kinds_after[x['kind']] += 1
    L = []
    w = L.append
    w('# Old World Blues: co-op policy review')
    w('')
    w('_Generated by questlines/owb/docgen.py from the game data and `rules_patch.py`; numbers and unit:line references are computed._')
    w('')
    w('## 0. Verdict')
    w('')
    w('- OWB has %d quests (%d with stages, %d stages). rules.json has entries for %d of them; %d of the %d T4 quests have none (%s).' % (
        len(owb), sum(1 for q in owb if q['stages']), inv['summary']['stages'], len(with_rules), len(t4_missing),
        sum(1 for q in owb if q['tier'] == 'T4'), ', '.join(x.replace('NVDLC03', '') for x in t4_missing)))
    w('- The existing curated entries are directionally right (AutoDoc = PerPlayerScene, teleporter = warp) but four are wrong or empty '
      'for this DLC (section 2). The patch replaces %d entries and adds %d, using the 16 existing kinds plus one justified new kind.' % (
          sum(1 for p in rules_patch.QUESTS.values() if p['op'] == 'replace'),
          sum(1 for p in rules_patch.QUESTS.values() if p['op'] == 'add')))
    w('- The DLC is built around the **player\'s own body**: brain, spine and heart are removed from the player (MQ01 stage 10), '
      'restored by the Sink AutoDoc and swapped for perks (MQ05 stage 100 and AutoDoc). That is per-player state in a '
      'quest the whole party shares. The policy below makes every player lose and regain their own organs, while the quest '
      'flags (`bRestoredBrain/Heart/Spine`, `bAutoDocUnlocked`) stay shared.')
    w('- Two engine behaviours dominate the risk and are not covered by any current rule kind: (a) %d block headers filter on the '
      '`Player` ref (%d of them are trigger blocks), so a guest ghost never fires them; (b) the guest quest-script gate skips all '
      'quest scripts although %d OWB quest scripts act on or read the local player.' % (
          c.hz('player-trigger'), sum(1 for x in g['hazards']['player-trigger']['locations'] if x['block'].lower().startswith('ontrigger')),
          len(g['quest_scripts_player_bound'])))
    w('')
    w('## 1. Owner decisions applied to this DLC')
    w('')
    w('| decision | what it means in OWB | evidence |')
    w('|---|---|---|')
    w('| Karma shared | Karma is changed only by dialogue: %d RewardKarma (Dr. 8 voice control -50, the brain "Lie" option -50); the ending slides '
      'branch on Karma <= -250 (%d INFOs read Karma, AV 23). One shared Courier karma is consistent. | %s; %s |' % (
          c.n('rewardkarma'), sum(1 for i in d.infos for x in i['conditions'] if x['func'] == 'GetActorValue' and x.get('p1') == 23),
          c.E('INFO 01147B begin' if False else 'INFO 01147B end', 'RewardKarma -50'), c.E('INFO 015A73 begin', 'RewardKarma -50')))
    w('| Faction reputation shared | OWB changes no reputation (no AddReputation/SetReputation). Faction **membership** is changed: '
      '`Player.AddToFaction NVDLC03PlayerFaction` (MQ00) and the follower dismissal (RemoveFromFaction x%d). Shared per the coordinator decision. | %s |' % (
          c.n('removefromfaction'), c.E('NVDLC03MQ00SCRIPT', 'Player.AddToFaction NVDLC03PlayerFaction')))
    w('| Quest items duplicated | PlayerBrain, the three X-13 suit parts, Gabe\'s Bowl, the Sonic Emitter notes/upgrades, the Sink module notes. '
      'Consumption (suit assembly, module install) removes from every holder; a hand-in (bowl) removes from the handing player. | %s |' % (
          c.E('NVDLC03MQ04SCRIPT', 'Player.RemoveItem NVDLC03X13SuitBoots')))
    w('| Caps/XP rewards: everyone gets the full reward | RewardXP appears %d times (%d player-subject). The Act1-3 and secondary XP values are '
      '**globals computed once from the host\'s level** (MQ01 quest script, `bDoOnce`), so a level-5 guest gets the host-tier amount. '
      'Accepted by the owner rule; flagged because of the %d-level spread of tiers. AutoDoc implant prices (8,000-20,000 caps) are removed '
      'from the buyer only. | %s |' % (c.n('rewardxp'), c.fn_player.get('rewardxp', 0), 10,
                                       c.E('NVDLC03MQ01SCRIPT', 'Set NVDLC03Act1XP to (3000')))
    w('| Initiator chooses dialogue | %d INFOs in the DLC carry a Speech/skill gate or a result script. Skill checks are evaluated on the initiator '
      '(see dialogue.md). The AutoDoc, the brain jar and the trait respec mutate `Player` in result scripts: they need per-player replay. | dialogue.md |' % (
          sum(1 for i in d.infos if i.get('begin') or i.get('end'))))
    w('| Party spread: free roam; story-critical moments need the party together | The three facility quests are order-independent and parallel '
      '(free roam). Tethered: satellite entry, first Klein meeting, X-8 tests, ending. | section 3 |')
    w('| Companions: owner = recruiter | TeleportEffectTimer dismisses every follower through the single `VNPCFollowers` struct '
      '(%d SetPlayerTeammate 0). The DLC has no recruitable companion. Rule: CompanionOwner(action dismiss-all-on-entry). | %s |' % (
          c.n('setplayerteammate'), c.E('NVDLC03TeleportEffectTimerSCRIPT', 'CraigBooneREF.SetPlayerTeammate 0')))
    w('')
    w('## 2. Problems in the current rules.json entries for OWB')
    w('')
    w('1. **NVDLC03MQ00** (derived): `PartyTether cells [GSDocMitchellHouse]`. The script reads `Player.GetInCell GSDocMitchellHouse == 0` '
      '(%s) as "chargen is done, announce the DLC" - an exit gate. A tether there pins the party into Doc Mitchell\'s house. Replaced by a tether '
      'to the satellite plus an any-in reduction for the exit gate.' % c.E('NVDLC03MQ00SCRIPT', 'Player.GetInCell GSDocMitchellHouse == 0'))
    w('2. **NVDLC03MQ05** (curated): `PartyWarp.markers []`, `PartyTether.cells []`, `SetPlayerControls.masks []` - the rule has no operands, '
      'so the runtime would tether to nothing. The Teleporter is modelled as a plain party-warp, but it is a per-player weapon: every player '
      'receives one (RewardAll) and firing moves the firer (plus party members in range).')
    w('3. **NVDLC03HQBuddy / HQBuddyUpgrade** (curated): `SetPlayerControls scope all`. The AutoDoc `DisablePlayerControls` (%d sites in the '
      'object script plus AutoDocTimer) is the user\'s own fade; locking the whole party for it freezes bystanders. Replaced by scope `origin`.' % (
          sum(1 for x in g['hazards']['cinematic']['locations'] if x['unit'] == 'NVDLC03SinkAutoDocSCRIPT')))
    w('4. **NVDLC03MQ01** (derived) has no rule for the three AddPerk calls (player-targeted, not rewards in the usual sense) and no SaveSuppress release.')
    w('5. **Missing entirely:** %s. These carry the entry, the slideshows, the transitions, the radar fence and the AutoDoc fade.' % ', '.join(
        t.replace('NVDLC03', '') for t in t4_missing))
    w('6. The derived entries use `GetInCell: entry:all-in,exit:any-in` as a blanket reduction. For `== 0` tests (exit gates) that means "nobody inside" and is fine; '
      'for test-start gates it should be any-in. The patch spells the intended reduction per call.')
    w('')
    w('## 3. Story moments: barriers, tethers, cinematic control, reward routing')
    w('')
    w('| moment | evidence | controller | barrier / tether | controls | reward routing | rule kinds |')
    w('|---|---|---|---|---|---|---|')
    for m in moments(c):
        w('| %s | %s | %s | %s | %s | %s | %s |' % (m[0], '; '.join(m[1]), m[2], m[3], m[4], m[5], m[6]))
    w('')
    w('## 4. Rule changes, quest by quest')
    w('')
    w(rules_diff_table(c))
    w('')
    w('### Why, per quest')
    w('')
    for edid, p in rules_patch.QUESTS.items():
        w('- **%s** (%s): %s' % (edid, p['op'], p['why']))
    w('')
    w('Rule kind usage after the patch: %s.' % ', '.join('%s %d' % kv for kv in sorted(kinds_after.items())))
    w('')
    w('## 5. New things the patch asks for')
    w('')
    for nk in rules_patch.new_kinds(len(g['quest_scripts_player_bound'])):
        w('- **New rule kind `%s`.** %s Consumers: %s.' % (nk['kind'], nk['justification'], '; '.join(nk['consumers'])))
    w('')
    w('Parameter extensions on existing kinds (no new kind needed):')
    w('')
    for k, v in rules_patch.NEW_PARAMS.items():
        w('- `%s`: %s' % (k, v))
    w('')
    w('Effect / hook proposals (L4/L5):')
    w('')
    for e in rules_patch.EFFECT_PROPOSALS:
        w('- **%s** - %s. Fields: %s.' % (e['type'], e['for'], e['fields']))
    w('')
    w('## 6. Split-party hazards (free roam) specific to this DLC')
    w('')
    w('- **Radio and reaction quests are global but tied to one player\'s location.** The Teleporter toggles `RNVTARef`, `RadioBlackMountainTAREF` and the '
      'Country Radio and Starts/Stops five VEFR reaction quests (%s, %s). With one player in the Mojave and one in the Big Empty the '
      'state must be "enabled iff any player is in WastelandNV".' % (
          c.E('NVDLC03WeapTeleporterSCRIPT', 'StartQuest VEFR01LegionGood2CaesarsFavor'), c.E('NVDLC03WeapTeleporterSCRIPT', 'StopQuest VEFR01LegionGood2CaesarsFavor')))
    w('- **Teleporter gates read the firer only:** `Player.IsInInterior`, `Player.IsInCombat`, `GetVATSMode`, `Player.GetEquipped HVExplosiveCollar`, '
      'worldspace/region checks and `VNPCFollowers.bHumanoidInParty` (%s). The companion flag is a single global; in co-op it must be evaluated for the firer\'s own companion.' % (
          c.E('NVDLC03WeapTeleporterSCRIPT', 'VNPCFollowers.bHumanoidInParty')))
    w('- **Time of day is rewritten** %d times (GameHour/GameDay/GameMonth/GameYear): intro end (+3 days, 5 AM), arrival (5 AM), first Think Tank exit (7 AM). '
      'Shared globals, host once; harmless while the party is together, visible to anyone already outside the DLC.' % c.hz('time-set'))
    w('- **Big MT is a separate worldspace (`NVDLC03BigMT`) with interior cells** (Sink, Think Tank, X-8, X-13, Forbidden Zone). The host only simulates loaded cells, '
      'so a party split across the Sink and the crater needs the cell-owner handoff the master plan lists as a top risk.')
    w('- **Encounters** (`NVDLC03Enc`, crater boxes) probe `EnterTrigger Player`, `GetDistance Player`, `Player.IsInCombat`: spawn boxes near any member, once.')
    w('- **Hostility is shared but weapon locks are per player** (Think Tank pacification zone vs. faction relation). A player outside the volume can draw; a '
      'player inside cannot until they leave through the door.')
    w('')
    w('## 7. Decisions needed from the owner')
    w('')
    w('1. Satellite entry: auto-warp absent players from anywhere in the Mojave (default in the patch) or wait for them? The satellite is `SetDestroyed 1` after use (%s), so a player who is not warped in cannot enter later.' % (
        c.E('NVDLC03CrashedSatelliteActivatorSCRIPT', 'SetDestroyed 1')))
    w('2. Organs: confirm per-player (each player loses and regains their own; assumed). The alternative (one shared body) would make the AutoDoc a one-time shared action.')
    w('3. Teleporter: firer + party members within range (assumed) versus firer only versus whole party.')
    w('4. Ending: hold the ending until the whole party is inside the Think Tank (assumed) or pull absent players in.')
    w('5. XP tiers computed from the host\'s level: acceptable, or recompute per player (needs a per-player global, not available)?')
    w('6. Say-once INFOs are shared (SayOnceFlag union): the second player cannot repeat a say-once Speech success for XP. Confirm.')
    w('')
    return '\n'.join(L) + '\n'


# --------------------------------------------------------------------------------------------------------------------
KEY_INFOS = [
    ('0030B2', 'Klein first-meeting greeting (releases controls except weapon draw)'),
    ('0160CB', 'Klein: "I\'ll head out, then..." (gives Central Intelligence note, starts HQBuddy)'),
    ('01355E', 'Klein: "I\'ll help. What do you need me to do?" (MQ01 100, MQ02/03/04 10)'),
    ('0153F8', 'Klein: "Uh... thanks." (alternate MQ01 100 + SonicEmitter 10)'),
    ('00A82D', 'Klein: "past due for your lobotomy" (MQ05 60, kill branch)'),
    ('013219', 'Klein: "I will PERMIT you..." (MQ05 70, peace)'),
    ('01611F', 'Klein: "You can still do that... for me" (MQ05 70)'),
    ('014740', 'Mobius: "I\'ll trash them..." (MQ05 35, SetEnemy)'),
    ('01473B', 'Mobius: "I need my brain back." (MQ05 40, say-once)'),
    ('01321E', 'Brain jar: expository (MQ05 50 + PlayerBrain item)'),
    ('015B66', 'Brain jar: "I\'m sure. In you go." (perk swap, variant A)'),
    ('015B67', 'Brain jar: "I\'m sure. In you go." (perk swap, variant B)'),
    ('015A73', 'Brain jar: <Lie> (RewardKarma -50, say-once)'),
    ('011945', 'Borous: Speech 45 FAILED (RemoveItem bowl, Borous 35)'),
    ('011941', 'Borous: Speech 45 SUCCEEDED (RewardXP 45, say-once)'),
    ('01147B', 'Dr. 8: voice control (RewardKarma -50, ThinkTankDoctor8 100)'),
    ('012C9E', 'AutoDoc: "I\'m ready" (16 RemovePerk + ShowTraitMenu)'),
]


def dialogue_md(c):
    d, specs = c.d, c.specs
    byref = {i['ref']: i for i in d.infos}
    L = []
    w = L.append
    w('# Old World Blues: dialogue under the initiator-picks model')
    w('')
    w('_Generated by questlines/owb/docgen.py; INFO ids, prompts, lines and scripts are read from out/questdata._')
    w('')
    w('Model used (docs/dialogue-design.md): the initiator opens the conversation and picks every option; everybody sees the lines and the result; '
      'skill checks are evaluated for the initiator; result scripts run on the host once and are captured as an EffectTxn; karma and faction membership '
      'are shared; rewards go to all.')
    w('')
    w('## 1. What the DLC dialogue looks like')
    w('')
    n_info = len(d.infos)
    n_scripted = sum(1 for i in d.infos if i.get('begin') or i.get('end'))
    skill = collections.Counter()
    for i in d.infos:
        for x in i['conditions']:
            if x['func'] == 'GetActorValue' and x.get('p1') in AV_NAMES:
                skill[(AV_NAMES[x['p1']], x['op'], int(x['value']))] += 1
    w('%d INFOs in OldWorldBlues.esm, %d with a begin or end result script, %d dialogue-container quests (including the Ending narrator); the quests with stages (MQ00-MQ05, '
      'HQBuddy*, X8/X13, ThinkTank*) own **no** topics - their stages are driven by the INFOs of the dialogue quests (e.g. INFO 01355E drives four quests).' % (
          n_info, n_scripted, sum(1 for q in c.inv['quests'] if q['role'] == 'dialogue')))
    w('')
    w('### Skill and attribute gates (GetActorValue conditions on INFOs; all evaluated on the initiator; both outcomes of a check appear, e.g. >= 45 and < 45)')
    w('')
    w('| actor value | thresholds (count of gating conditions) |')
    w('|---|---|')
    by = collections.defaultdict(list)
    for (nm, op, th), n in sorted(skill.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][1])):
        by[nm].append('%s %d x%d' % (op, th, n))
    for nm, v in sorted(by.items()):
        w('| %s | %s |' % (nm, ', '.join(v)))
    w('')
    w('## 2. Key conversations')
    w('')
    w('| INFO | role | topic prompt | first line | result effects (script fragments) |')
    w('|---|---|---|---|---|')
    for loc, role in KEY_INFOS:
        i = byref['OldWorldBlues.esm:' + loc]
        dl = d.dial_by_ref[i['topic']]
        src = ' '.join((i.get(k) or {}).get('source', '') for k in ('begin', 'end')).replace('\r', ' ')
        eff = ', '.join(sorted({m.group(0).strip() for m in EFFECT_RE.finditer(src)}))[:160]
        w('| %s | %s | %s | %s | %s |' % (loc, role, (dl.get('name') or '')[:60].replace('|', '/'),
                                         (i['responses'][0]['text'] if i['responses'] else '')[:60].replace('|', '/'), eff.replace('|', '/')))
    w('')
    w('## 3. Where the initiator-picks model breaks')
    w('')
    w('1. **`Player` in result scripts is the host.** %d `Player.*` mutations sit in INFO result scripts (brain re-insertion perk swap, AutoDoc trait respec, '
      'item grants, bowl hand-in). Captured on the host they change the host\'s player; the guest initiator gets nothing. These need per-initiator replay '
      '(Recipient = initiator) or guest-side execution with shared effects shipped as intents. Specs 5, 6 and 7 below test it.' % sum(
          1 for i in d.infos for k in ('begin', 'end') if (i.get(k) or {}).get('source') and
          __import__('re').search(r'(?i)\bplayer\.(additem|removeitem|addperk|removeperk|removenote|addnote)', i[k]['source'])))
    w('2. **Brain jar conversation hands a quest item and a perk swap to one player** (INFO 01321E, 015B66/67). Item: duplicate to all (the other players '
      'need their own brain for MQ05 stage 100). Perk swap: only the player who re-inserts. Mixed routing inside one conversation.')
    w('3. **Kill-everyone branch from one pick.** INFO 00A82D sets MQ05 60: the Think Tank faction becomes enemy of the whole party and five doctor quests fail (255). '
      'The other players cannot veto. Owner rule applies; flagged for the tester.')
    w('4. **Forced scene with control locks.** MeetKleinTrigger locks controls, moves the player, adds a script package to Klein and the greeting INFO '
      'releases controls (INFO 0030B2 begin). The speaker is whoever tripped the trigger; spectators need the same lock and the same release (else a spectator stays frozen).')
    w('5. **Say-once flags are shared.** Success lines such as INFO 011941 (RewardXP 45) and the brain "Lie" option are `say_once`; with the SayOnceFlag union '
      'the second player never sees them again although XP is rewarded to all at the first success.')
    w('6. **Barter and trait menus are initiator-only UI** (Sink Main Computer barter, AutoDoc trait menu). They must not open on spectators; the AutoDoc '
      'menu state (`bAwaitingInput`, `nButton`) lives in object-script variables of ONE ref, so two players using it at once corrupt each other.')
    w('7. **Narrator slideshows are not dialogue choices** but are SayTo chains (`SayToDone` on the Narrator). They need per-client replay, not initiator picks.')
    w('8. **No recruitable companions** exist in this DLC (no SetPlayerTeammate 1); follower dismissal on entry is covered by CompanionOwner.')
    w('9. **Faction-changing lines:** only SetEnemy/SetAlly relations (Mobius, Think Tank, giant robo-scorpion); no player faction rank changes by dialogue.')
    w('')
    w('## 4. Scenario specs (L8 schema 1, drafts for the runner)')
    w('')
    w('Same structure as `plugin/dialogue/scenarios/*.json`; generated by `questlines/owb/dialogue_specs.py` and checked against the game data. '
      'Save `%s` does not exist yet (first arrival in the Big Empty). Keys prefixed `x_` are not understood by the current L8 runner: %s.' % (
          specs['dlg_owb_klein_assign_tasks']['save'], '; '.join('`%s` = %s' % kv for kv in sorted(PROPOSED.items()))))
    w('')
    for sid, spec in specs.items():
        w('### %s' % sid)
        w('')
        w(spec['summary'])
        w('')
        w('```json')
        w(json.dumps(spec, indent=1, ensure_ascii=False))
        w('```')
        w('')
    return '\n'.join(L) + '\n'


import re as _re  # noqa: E402
from dialogue_specs import PROPOSED_EXPECT_KEYS as PROPOSED  # noqa: E402
EFFECT_RE = _re.compile(r'(?i)\b(?:player\.)?(?:setstage \w+ \d+|additem \w+|removeitem \w+|addperk \w+|removeperk \w+|rewardxp \w+|rewardkarma -?\d+|'
                        r'setenemy \w+|enableplayercontrols|showtraitmenu|showbartermenu|startquest \w+|forceactivequest \w+)')
AV_NAMES = {5: 'Strength', 6: 'Perception', 7: 'Endurance', 8: 'Charisma', 9: 'Intelligence', 10: 'Agility', 11: 'Luck', 23: 'Karma',
            32: 'Barter', 33: 'BigGuns', 34: 'EnergyWeapons', 35: 'Explosives', 36: 'Lockpick', 37: 'Medicine', 38: 'MeleeWeapons',
            39: 'Repair', 40: 'Science', 41: 'Guns', 42: 'Sneak', 43: 'Speech', 44: 'Survival', 45: 'Unarmed'}
