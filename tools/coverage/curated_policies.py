"""Hand-written co-op policy judgment for the highest-risk story quests (keyed by EDID).

Fields: scene (what happens / why risky, grounded in the cited script lines), rule (co-op behaviour),
tether (party-presence requirement or ''), hooks (engine hooks / effect types), test (scenario policy_check).
Cited line numbers are NOT repeated here; the generator prints the live ones from risk.json.
Lore is kept to what the scripts themselves show.
"""

_TETHER = 'Party in the same cell/area (tether radius) before the stage fires; stragglers are auto-gathered.'
_ENDING = ('Ending slideshow: set-in-chargen + all controls disabled + MoveTo slideshow marker. Run once on host; every '
           'player is locked and moved in the same txn, sees the same slides, and is released together at slideshow end.')

CURATED = {
    # ------------------------------------------------------------------ prologue
    'VCG00': dict(
        scene='Intro sequence: DisablePlayerControls masks, player.MoveTo start markers, chargen-style steps.',
        rule='Each player runs chargen/intro for themself (per-player SPECIAL/skills/appearance, never shared). '
             'World state (Doc Mitchell, Goodsprings) is shared; stage advance past the intro is a barrier: '
             'SetStage fires when ALL players have finished their own intro. New joiners later start at the '
             'post-intro state.',
        tether='Barrier on stage advance, no spatial tether (players are alone in the intro).',
        hooks='SetPlayerControls (per-player), per-player chargen state (ModActorValue Recipient=target), barrier stage merge',
        test='2 players each complete chargen with different SPECIAL; assert values differ, stage advanced once.'),
    'VCG01': dict(
        scene='Doc Mitchell scene: DisablePlayerControls at stages 0/55/110, ShowRaceMenu (stage 36), sexChange in the '
              'quest script, Vault-tec test; the quest script also polls attribute values.',
        rule='Per-player: race menu, SPECIAL test, skill tags, perk (these effects target the originating player, '
             'NOT Recipient=All - the one exception to "rewards to everyone"). Shared: Doc, quest stage, items '
             'handed out as quest items (duplicated). Doc talks to whoever is nearest/initiator; others queue and '
             'run the same scene on their own stage-copy while the shared stage is max-merged.',
        tether='Scenes are per-player; shared stage advances when all players pass the corresponding step.',
        hooks='ShowRaceMenu intercept (per-player), SetPlayerControls, ModActorValue(Recipient=target), AddItem(All) for quest items',
        test='Player B starts chargen while A is mid-scene: no stage regress (max), no duplicate Doc.'),
    'VCG02': dict(
        scene='Goodsprings tutorial quest (Sunny / first steps).',
        rule='Shared stage, rewards to all, dialogue initiator-driven. No special tether beyond normal free roam.',
        tether='', hooks='Standard stage/objective sync; AddItem(All)',
        test='Reward items present for both players exactly once.'),
    'VCG03': dict(
        scene='Campfire / post-prologue transition; may move the player and hand out items.',
        rule='Shared stage; any MoveTo is a party-warp; reward items to all.', tether=_TETHER,
        hooks='MoveRef(party-warp), AddItem(All)', test='Both players end the scene in the same cell.'),
    'PressDemoQuest': dict(
        scene='Press-demo quest: MoveTo Primm/Novac/Black Mountain/Helios markers, companions teammate flips, items.',
        rule='Debug/demo content, not part of the shipped story: exclude from the story gate '
             '(mark "not required for any ending"). If ever reached, treat as host-only cheat.',
        tether='', hooks='none (excluded)', test='Not required for any ending; coverage excluded.'),
    # ------------------------------------------------------------------ main shared
    'VMQ01': dict(
        scene='"They Went That-a-Way": Sunny/Victor/Primm/Goodsprings chain; many dependent NPC and trigger scripts '
              'with player-ref checks (IsActionRef player, GetInSameCell).',
        rule='Free roam. Each player can trigger NPC dialogue; the first activator is initiator for that scene. '
             'Quest items (e.g. the Sunny/Primm hand-ins) duplicated to everyone; shared stage max-merge.',
        tether='', hooks='Initiator-scoped IsActionRef, AddItem(All)',
        test='Two players activate the same NPC within 1 s: one scene, one reward set per player.'),
    'VMQTops': dict(
        scene='Ring-a-Ding-Ding: forced Benny scene in the Tops Presidential Suite (Player.GetInCell), Fort/Caesar '
              'scripts that player.MoveTo the Legate markers, and Yes Man scripts that MoveTo the Indy battle '
              'start marker; IsKiller Player checks, speech/Medicine checks.',
        rule='Tether party into the Suite: the Benny scene starts only when all players are in the cell (or '
             'are warped in). Initiator drives all dialogue and speech/skill checks (policy: initiator\'s skill, '
             'optional best-of-party assist). IsKiller Player = any party member. Platinum Chip is a quest item: '
             'duplicated to every player. Cutscene locks every player; failure/death outcomes apply to the '
             'whole party (host-decided).',
        tether='All players in 2ETopsPresidentialSuite (or party-warp) before Benny\'s scene stage.',
        hooks='PartyTether gate, SetPlayerControls, MoveRef(party-warp), AddItem(All), party-aware IsKiller/GetInCell',
        test='Benny scene with B 500 units outside the cell: stage not fired until B gathered; chip on both.'),
    'VDialogueMrHouse': dict(
        scene='Mr. House dialogue container: control locks in INFO results, quest-state toggles, item hand-ins.',
        rule='Dialogue initiator chooses every option; outcome is broadcast. House commits/refusal choices that '
             'set faction path are host-authoritative and require the party present (Lucky 38 penthouse).',
        tether='Party in Lucky 38 control room for any path-defining line.',
        hooks='ForcedDialogue topic capture, SetPlayerControls, PartyTether',
        test='Non-initiator cannot advance dialogue; path-defining INFO result runs once.'),
    'VDialogueLucky38Entrance': dict(
        scene='Lucky 38 entrance gate dialogue.', rule='Initiator talks; entry for the whole party is granted by '
        'the result (EnableRef/unlock is world state, so everybody can enter).', tether='',
        hooks='EnableRef/Unlock effects', test='Door unlocked for both after one conversation.'),
    'VDialogueCaesar': dict(
        scene='Caesar dialogue container: control locks, party/companion checks, item hand-ins at Fort/Legate scenes.',
        rule='Initiator drives; Legion hostility outcomes share reputation (shared). Companion-restricted lines '
             'use the recruiter\'s companions.', tether='Party in the tent/scene cell.',
        hooks='ForcedDialogue, SetPlayerControls, CompanionOwner', test='Rep delta applied once, shared.'),
    'VMQ02': dict(
        scene='Render Unto Caesar: Lucky 38 elevator MoveTos (basement/suite/penthouse/control room), Fort scripts '
              'MoveTo the Legate start marker, Medicine skill gate (>=75 / 25-74).',
        rule='Elevators and Fort transfers are party-warps (the elevator teleports are MoveTo, not a cell '
             'transition: warp every player in the car/in range). Skill gate uses initiator; party-best optional. '
             'Quest items duplicated.', tether='Elevator: all players within car radius, or warped with the car.',
        hooks='MoveRef(party-warp), PartyTether, AddItem(All)',
        test='Elevator ride with 2 players in car: both arrive at the same marker.'),
    'VMQ03a': dict(
        scene='"You\'ll Know It When It Happens": Kimball protection at the Hoover Dam observation deck - sniper '
              'distance checks, GetInSameCell checks on Kimball/Graham/Rangers, quest timer tied to Player.GetInCell '
              'HooverDam, DisablePlayerControls + MoveTo teleport marker from the Colonel Moore script.',
        rule='Story moment: party must be at Hoover Dam. All "Player.Get*" checks become party-aware: GetDistance '
             '= min over party, GetInCell HooverDam == 0 only when NO party member is inside (timer pauses while '
             'any are in). Cutscene lock + teleport hits all players (party-warp). Kimball saved/killed outcome '
             'applies to the shared story.',
        tether='All players at Hoover Dam observation deck before stage 20.',
        hooks='PartyTether, party-aware condition eval, SetPlayerControls, MoveRef(party-warp)',
        test='One player leaves the cell: quest timer must not fail the quest while the other is inside.'),
    'VMQ03b': dict(
        scene='"Arizona Killer": Legion-side counterpart; shares the Hoover Dam player-ref logic and cutscene '
              'locks from the same dependent scripts.',
        rule='As VMQ03a: tether at Hoover Dam, party-aware distance/cell checks, host-decided outcome.',
        tether=_TETHER, hooks='PartyTether, SetPlayerControls, MoveRef(party-warp)', test='Mirror of VMQ03a test.'),
    'VMQ05': dict(
        scene='"For the Republic, Part 2": NCR chain with reputation changes and item rewards.',
        rule='Reputation changes are shared; rewards (caps/items) to each player; NCR gear checks '
             '(GetEquipped faction armor) evaluate for the initiator.', tether='',
        hooks='ModReputation (shared), AddItem(All)', test='Rep applied once; both get reward.'),
    'VMQCaesarFail': dict(
        scene='"Beware the Wrath of Caesar!": fail-state quest with P-INV/stat changes and teleports.',
        rule='Failure is a party-wide shared state; inventory removal hits every player\'s quest item copy.',
        tether='', hooks='RemoveItem(All), ModReputation', test='Fail transition once, items removed from both.'),
    # ------------------------------------------------------------------ yes man
    'VMQYesMan01': dict(
        scene='Wild Card: Change in Management - securitron/Yes Man takeover setup; world enable/disable churn.',
        rule='Host-run; party tethered to Lucky 38 for the takeover moment. Rewards to all.',
        tether=_TETHER, hooks='EnableRef/DisableRef, AddItem(All)', test='World state identical on both clients.'),
    'VMQYesMan01a': dict(
        scene='You and What Army?: recruiting factions to the independent path.',
        rule='Shared faction rep; dialogue initiator-driven.', tether='', hooks='ModReputation (shared)', test=''),
    'VMQYesMan02': dict(
        scene='Side Bets: faction hand-ins via dialogue.', rule='Initiator chooses; reward to all.', tether='',
        hooks='AddItem(All)', test=''),
    'VMQYesMan03': dict(
        scene='Finishing Touches: DisablePlayerControls in a dependent NPC script, Colonel Moore teleport '
              '(Player.moveto vHDOliverTeleportMarker), Yes Man mainframe MoveTo Indy battle start marker, '
              'Repair/Explosives skill gates at the vertibird encounter.',
        rule='Mainframe and Moore teleports are party-warps; control locks apply to all; skill gates use '
             'best-of-party (cooperative engineering) - decide per gate in scenario, default initiator.',
        tether='All players at the mainframe before the warp.',
        hooks='MoveRef(party-warp), SetPlayerControls, PartyTether', test='Both players land at the battle start marker.'),
    'VMQYesManFailsafe': dict(
        scene='Ace in the Hole: contingency path; companion and control state.',
        rule='Only enters via host. Companion-ownership effects target the recruiter.',
        tether='', hooks='CompanionOwner, SetPlayerControls', test=''),
    # ------------------------------------------------------------------ battles / endings
    'vHDBattleController': dict(
        scene='"Eureka!": Hoover Dam battle controller. Reads player cell/zone, drives zone progress, TRAVEL and '
              'MOVE effects; touched by all four endings.',
        rule='Host runs the controller; battle zone progress is a function of the PARTY (max zone reached). '
             'Players free-roam inside the dam but fast travel is disabled party-wide. Death of one player '
             'respawns at last zone checkpoint (no quest failure).',
        tether='Entering the battle requires the whole party at the Dam; late joiners warp to current zone.',
        hooks='PartyTether, SetGlobal-like flag (TRAVEL), party-aware GetInCell, checkpoint respawn',
        test='Zone counter advances from either player; fast travel blocked for both.'),
    'VHDHouseBattle': dict(
        scene='All or Nothing: DisablePlayerControls in quest script, player.moveto VHouseEndGameMarker, '
              'MrHouse script moves player to the Indy battle start marker, VEnding slideshow.',
        rule='Securitron-vault battle: party-warp to the start marker, free combat, host-decided outcome. ' + _ENDING,
        tether='Whole party present before the endgame warp.',
        hooks='MoveRef(party-warp), SetPlayerControls, PartyTether, SlideshowEnd (proposed)',
        test='Both players reach slideshow and are released together.'),
    'VHDIndependentBattle': dict(
        scene='No Gods, No Masters: Yes Man independent battle; teleports to start marker, control locks, '
              'securitron vault checks (bSecuritronVaultActive).',
        rule='Same battle policy as All or Nothing; securitron vault flag read from host. ' + _ENDING,
        tether='Whole party before the warp.', hooks='MoveRef(party-warp), SlideshowEnd, PartyTether',
        test='Mirror of House battle test.'),
    'VHDLegionBattle': dict(
        scene='Veni, Vidi, Vici: Legate duel (VHDLegateStartMarker teleport), Legion assault; companion-presence '
              'checks.',
        rule='Duel is the initiator\'s; other players are spectators locked out of the duel arena (or inside, '
             'non-combat) until it resolves. Outcome shared. ' + _ENDING,
        tether='Party present at the Dam; duel arena admits initiator + optional party.',
        hooks='MoveRef(party-warp), SetPlayerControls, SlideshowEnd', test='Duel outcome applied once.'),
    # ------------------------------------------------------------------ misc side
    'VDialogueCraigBoone': dict(
        scene='Boone companion dialogue container with control locks and P-STAT/INV results.',
        rule='Companion owned by recruiter; the recruiter is the initiator for companion-quest dialogue. '
             'Companion perks apply to the owner only.', tether='', hooks='CompanionOwner',
        test='Non-owner cannot start Boone\'s companion dialogue.'),
    'vDialogueEDE': dict(
        scene='ED-E companion dialogue: MOVE and CTRL-RELEASE results.', rule='As Boone: owner-driven.',
        tether='', hooks='CompanionOwner, MoveRef', test=''),
    'VMS18': dict(
        scene='Companion "in freezer" trigger scripts (flags for Boone/Cass/Veronica/Arcade) plus control locks '
              'and teleports in a ~345-INFO dialogue-heavy quest.',
        rule='Companion flags are keyed to each companion\'s owner. Trap/freezer scene: party tether, single '
             'initiator for the dialogue, locks release on host effect.',
        tether=_TETHER, hooks='CompanionOwner, SetPlayerControls, PartyTether', test=''),
    'VMS29a': dict(
        scene='Birds of a Feather: long dialogue quest with control locks, hand-ins, stat changes.',
        rule='Initiator drives dialogue; hand-in items removed from the handing player and duplicated rewards '
             'to all.', tether='', hooks='ForcedDialogue, RemoveItem(per-owner), AddItem(All)', test=''),
    'VMS46': dict(
        scene='Aba Daba Honeymoon: dialogue + stat/inventory changes + control locks.',
        rule='Initiator-driven; shared rep/karma; per-player stat results (if any) go to the targeted player.',
        tether='', hooks='ForcedDialogue, ModActorValue(target)', test=''),
    'VMS40': dict(
        scene='Bitter Springs Infirmary Blues: control locks + inventory + actor churn (battle/rescue).',
        rule='Party tether for the battle; kills credited to the party (union).', tether=_TETHER,
        hooks='SetPlayerControls, PartyTether', test=''),
    'VMS49': dict(
        scene='Vault 22 elevator door scripts use player.MoveTo floor markers 1F-5F; companion teammate resets.',
        rule='Elevator = party-warp to the same floor marker for all players in the car; companions follow '
             'their owner.', tether='All players within the car radius.',
        hooks='MoveRef(party-warp), CompanionOwner', test='Both players arrive on the same floor.'),
    'VMS45': dict(
        scene='Oh My Papa: faction hand-ins, rep/karma, P-INV.', rule='Shared rep/karma; items to all.',
        tether='', hooks='ModReputation (shared), AddItem(All)', test=''),
    'VMS30': dict(
        scene='Restoring Hope: multi-step quest with stat/inventory rewards.',
        rule='Rewards to all; stage max-merge.', tether='', hooks='AddItem(All)', test=''),
    'VMS19': dict(scene='G.I. Blues: player-ref checks and reward/karma results.',
                  rule='Initiator-scoped checks; rep/karma shared; rewards to all.', tether='',
                  hooks='Party-aware condition eval', test=''),
    'VMS21': dict(scene='How Little We Know: player-ref checks, rewards, actor churn.',
                  rule='Initiator-scoped checks; rewards to all.', tether='', hooks='AddItem(All)', test=''),
    'VMS35': dict(scene='Et Tumor, Brute?: inventory hand-ins, rep/karma.',
                  rule='Hand-in removes from the handing player only; rewards to all.', tether='',
                  hooks='RemoveItem(per-owner), AddItem(All)', test=''),
    # ------------------------------------------------------------------ DLC
    'NVDLC01MQ02': dict(
        scene='Dead Money "Trigger the Gala Event": heavy dialogue/world churn (Sierra Madre).',
        rule='Dead Money forces a solo-style companion/collar structure. Policy: whole party enters together '
             '(collars are per-player and detonate per-player); death of a collared player is a downed state '
             'until the party clears the next checkpoint.', tether='Party enters the Villa together.',
        hooks='Per-player collar state (proposed), PartyTether', test=''),
    'NVDLC01MQ03': dict(
        scene='Heist of the Centuries: control locks, teleports, inventory (gold bars).',
        rule='Vault/ending scene is a party-warp with shared outcome; gold bars follow the quest-item rule '
             '(each player gets their own full set - owner decision, documented as a balance risk).',
        tether='Party in the vault before the finale.', hooks='MoveRef(party-warp), AddItem(All), SetPlayerControls',
        test='Both players receive bars; one ending run.'),
    'NVDLC01EnemyTest': dict(scene='Dead Money enemy-test debug quest.',
                             rule='Debug content; exclude from story gating.', tether='', hooks='none', test=''),
    'NVDLC02MQ00': dict(scene='Happy Trails Expedition: intro, teleports, companion checks.',
                        rule='Party-warp onto the Zion arrival; companions follow owner.', tether=_TETHER,
                        hooks='MoveRef(party-warp), CompanionOwner', test=''),
    'NVDLC02MQ04': dict(scene='Crush the White Legs: battle with fast-travel toggles and rewards.',
                        rule='Party-wide fast-travel flag; kills union; rewards to all.', tether='',
                        hooks='TRAVEL flag, AddItem(All)', test=''),
    'NVDLC02MQ05': dict(scene='Flight from Zion: save triggers, teleports, control locks.',
                        rule='Autosave suppressed; party-warp; locks for all.', tether=_TETHER,
                        hooks='SaveSuppress, MoveRef(party-warp), SetPlayerControls', test=''),
    'NVDLC02MQ06': dict(
        scene='Chaos in Zion: NVDLC02Ending script (SetInCharGen 1, DisablePlayerControls, MoveTo slideshow '
              'marker), companion SetPlayerTeammate resets, detonator control locks.',
        rule='Party-wide ending: ' + _ENDING, tether='Whole party at Zion before the finale.',
        hooks='SlideshowEnd, MoveRef(party-warp), CompanionOwner', test='Both players released after slideshow.'),
    'NVDLC03MQ05': dict(
        scene='Old World Blues finale: Sink AutoDoc DisablePlayerControls, teleporter MoveTo (Mojave drive-in / '
              'Sink arrival marker), ThinkTank door action-ref checks.',
        rule='Teleporter is a party-warp (everyone in range); AutoDoc scene per-player (each player\'s own '
             'upgrade; stat results target that player); doors open on party-any.',
        tether='Party near the teleporter pad.', hooks='MoveRef(party-warp), SetPlayerControls, ModActorValue(target)',
        test=''),
    'NVDLC03HQBuddy': dict(
        scene='All My Friends Have Off Switches: Sink AutoDoc scene with player-controls disabled; note/stat checks.',
        rule='AutoDoc customisation is per-player; HQ buddy ownership = the player who completes the quest, '
             'shared access afterwards.', tether='', hooks='SetPlayerControls, ModActorValue(target)', test=''),
    'NVDLC03HQBuddyUpgrade': dict(
        scene='Influencing People: AutoDoc upgrade scenes (same scripts as HQBuddy).',
        rule='As HQBuddy: per-player scene, shared outcome.', tether='', hooks='SetPlayerControls', test=''),
}
