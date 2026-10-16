"""Status document (internal questline notes) and the ranked top-10 blockers."""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rules_patch  # noqa: E402
from docgen import KEY_INFOS  # noqa: E402


def blockers(c):
    """Top-10 blockers, ranked. Each: (title, evidence, consequence, needed)."""
    E, g = c.E, c.g
    trig = sum(1 for x in g['hazards']['player-trigger']['locations'] if x['block'].lower().startswith('ontrigger'))
    isact = c.n('isactionref', read=True) + c.n('getactionref', read=True)
    ps = g['quest_scripts_player_bound']
    cin_autodoc = sum(1 for x in g['hazards']['cinematic']['locations'] if x['unit'] == 'NVDLC03SinkAutoDocSCRIPT')
    return [
        ('Player-filtered triggers and activations never fire for a guest',
         '%d block headers filter on the Player ref (%d are OnTrigger/OnTriggerEnter/OnTriggerLeave); %d IsActionRef/GetActionRef reads. Story gates: '
         'MQ01 10 (%s), MQ02 20 (SatelliteStationTrigger), MQ03 20 (X8Trigger), MQ04 20 (X13Trigger), MeetKlein, Slideshow, Sink release, and the '
         'ending itself (%s). L5 round 2 assumes host triggers "already see guests"; the `Player` parameter on the block excludes a ghost.' % (
             c.hz('player-trigger'), trig, isact, E('NVDLC03PlayerStartTriggerSCRIPT', 'BEGIN OnTriggerEnter Player'),
             E('NVDLC03ThinkTankDoorSCRIPT', 'If ( IsActionRef Player == 1 )')),
         'A guest cannot advance MQ00-MQ05, use the AutoDoc, open the ending door or release their own controls.',
         'Block-header Player filter must match any party member; `Player`/`PlayerRef` inside an activation run for a guest must resolve to the '
         'guest ghost (PlayerRefSubstitution), or the scene runs on the guest and ships shared effects as intents.'),
        ('Body-part perks (and every AddPerk/RemovePerk) are never captured or applied',
         'AddPerk x%d, RemovePerk x%d (%d on the player). MQ01 stage 10 (%s), MQ05 100 (%s), AutoDoc brain/heart/spine swaps and implants, '
         'trait respec (%s). BLOCK_CMD only: host pass-through, no SetPerk effect recorded; SetPerk has a codec but no capture/apply.' % (
             c.n('addperk'), c.n('removeperk'), c.fn_player.get('removeperk', 0) + c.fn_player.get('addperk', 0),
             E('NVDLC03MQ01:stage 10', 'AddPerk NVDLC03BrainlessPerk'), E('NVDLC03MQ05:stage 100', 'AddPerk NVDLC03BigBrainedPerk'),
             E('INFO 012C9E end', 'Player.RemovePerk WildWasteland')),
         'Guests never lose or regain organs; the DLC\'s central mechanic does not exist for them and the AutoDoc restore menu is meaningless.',
         'Capture AddPerk/RemovePerk into SetPerk with Recipient (All for MQ01 10, initiator for AutoDoc/jar) and apply it.'),
        ('Control locks are neither captured nor scoped per player',
         'DisablePlayerControls x%d, EnablePlayerControls x%d. Used by the satellite (%s), Klein meeting, slideshows, X-8 experiments, '
         'the Think Tank weapon-draw lock (mask 0 0 1 0 0 0 0) and %d AutoDoc fades. Cmd hooks located but not installed; apply is the all-7-flags RPC only.' % (
             c.n('disableplayercontrols'), c.n('enableplayercontrols'),
             E('NVDLC03CrashedSatelliteActivatorSCRIPT', 'DisablePlayerControls 1 1 1 1 0 1 1'), cin_autodoc),
         'Guests stay unlocked during cutscenes (or locked forever if only the lock is replicated); masks and per-player scope are lost.',
         'Hook both commands, SetPlayerControls{mask, Recipient}, apply with masks, release on resync.'),
        ('Quest scripts that drive the local player are skipped on guests',
         '%d OWB quest scripts read or mutate Player (mostly in GameMode blocks): GenericPerkQUEST (implant engine: %s), AuralStealthSuit*, HQBuddy*, X8/X13 setup, '
         'RadarFence, AutoDocTimer, TeleportEffectTimer, Intro/Ending/Transition*. The guest gate skips all quest-owned scripts.' % (
             len(ps), E('NVDLC03GenericPerkQuestSCRIPT', 'If ( Player.HasPerk NVDLC03ImplantGRXPerk == 1 )')),
         'Implants (GRX/Y-3/Y-7), Heartless/Cardiac Arrest, the radar fence, the AutoDoc fade and the stealth-suit barks never run for guests.',
         'New rule kind LocalQuestScript + allowlist in suppress_policy (run locally, shared mutators still dropped).'),
        ('Party warp and cross-worldspace MoveTo are not implemented',
         'MoveTo x%d (%d on Player): satellite/slideshow/start marker/Think Tank/Sink/X-8 viewing marker x4/X-13 VR doors/radar fence/Teleporter. '
         'MoveRef.partyWarp exists in the type but the applier supports only same-cell or marker moves; targets span WastelandNV, NVDLC03BigMT and 9 interior cells.' % (
             c.n('moveto'), c.hz('move-player')),
         'The party cannot enter the DLC together, watch the slideshow in one place or return to the Sink.',
         'PartyWarp rule engine (slot offsets, hold absent), cross-cell apply, Teleporter range rule.'),
        ('Door and gate state is not replicated',
         'Lock x%d, Unlock x%d, SetOpenState x%d (BLOCK_CMD only; SetLock codec without capture). MQ01 100 (%s) opens the Big Empty doors, '
         'MQ05 20/30 the Forbidden Zone doors, MQ02/03/04 10 the facility doors.' % (
             c.n('lock'), c.n('unlock'), c.n('setopenstate'), E('NVDLC03MQ01:stage 100', 'NVDLC03SinkToBigEmptyDoorREF.Unlock')),
         'Guests walk into locked doors after the host unlocked them (or the reverse after a reset).',
         'Capture Lock/Unlock into SetLock, add SetOpenState effect, apply.'),
        ('Shared object-script state (ref variables) is not captured',
         '%d writes to reference-script variables and %d script Activate calls. The Sink AutoDoc keeps its unlock flags, implant flags and the whole message-box '
         'state machine in ref vars of one ref (%s); Sink buddy unlock flags feed the Ending (%s).' % (
             c.n('setrefvar'), c.n('activate'), E('NVDLC03SinkAutoDocSCRIPT', 'Set bAutoDocUnlocked To 1'),
             E('NVDLC03EndingSCRIPT', 'NVDLC03SinkBioResearchStationREF.bBiologyUnlocked')),
         'Module unlocks and implant availability diverge per machine; two players in the AutoDoc menu corrupt each other.',
         'Capture ref-var writes for an allowlisted set (SCRO pre-computation) and make the menu state per-player.'),
        ('Faction relations are not an Effect',
         'SetEnemy/SetAlly/SetPCEnemyOfFaction x%d: Think Tank hostility (%s, %s), Mobius, giant robo-scorpion alliance via terminals, X-42 turrets, X-13 robots.' % (
             c.hz('faction-relation'), E('NVDLC03MQ05:stage 60', 'SetEnemy NVDLC03ThinkTankFaction PlayerFaction'),
             E('NVDLC03BrainBotSCRIPT', 'SetEnemy NVDLC03ThinkTankFaction PlayerFaction')),
         'After a kill-branch pick the doctors stay friendly on guests (or hostile on the host only).',
         'SetFactionRelation effect + capture + apply (host wins).'),
        ('Notes and equipment have no Effect type',
         'AddNote x%d, RemoveNote x%d, GetHasNote x%d, EquipItem x%d. The HQ-Buddy unlock chain is gated by notes (%s); the Teleporter is equipped at MQ05 100.' % (
             c.n('addnote'), c.n('removenote'), c.n('gethasnote', read=True), c.n('equipitem'),
             E('NVDLC03SinkAutoDocSCRIPT', 'Player.GetHasNote NVDLC03HQBuddyAutoDocNOTE')),
         'Sink module quests (HQBuddy, Upgrade, Samples) cannot be completed by guests; they do not receive duplicated notes.',
         'NoteChange effect (Recipient routing) and EquipItem capture.'),
        ('Narrator slideshows are SayTo chains with no replay path',
         'SayTo x%d (SayToDone blocks: %d) drive the intro and ending slides, plus SetInChargen x%d and the no-save package. SlideshowEnd exists as an Effect '
         'without capture. Example: %s.' % (c.n('sayto'), c.g['block_types'].get('saytodone', 0), c.n('setinchargen'),
                                             E('NVDLC03NarratorSCRIPT', 'BEGIN SayToDone NVDLC03EndingSlideFinale')),
         'Guests would see a black screen or skip the slides while the host plays them; the final MoveTo/stage 100 timing depends on the chain completing.',
         'SlideshowEnd capture on the host + per-client slide replay (L8 spectator playback).'),
    ]


def status_md(c, test_output):
    inv, sw, g = c.inv, c.sw, c.g
    sev_f = collections.Counter(r['severity'] for r in g['functions'] if r['status'] in ('unsupported', 'partial'))
    sev_o = g['unsupported_or_partial_occurrences_by_severity']
    L = []
    w = L.append
    w('# Questline Q-owb: Old World Blues (OldWorldBlues.esm, prefix 03)')
    w('')
    w('_Generated by `python -I questlines/owb/gen_all.py`; deliverables live in `questlines/owb/`. No game launched, no commits, no edits outside `questlines/owb/` and this file._')
    w('')
    w('## Counts')
    w('')
    w('| item | value |')
    w('|---|---|')
    w('| quests in the DLC | %d (main %d, side %d, system %d, dialogue %d) |' % (
        inv['summary']['quests'], inv['summary']['by_role'].get('main', 0), inv['summary']['by_role'].get('side', 0),
        inv['summary']['by_role'].get('system', 0), inv['summary']['by_role'].get('dialogue', 0)))
    w('| quests with stages / without | %d / %d (the %d without are script, bark or dialogue containers) |' % (
        sw['summary']['quests'], sw['summary']['unswept_quests'], sw['summary']['unswept_quests']))
    w('| stages / objectives | %d / %d |' % (inv['summary']['stages'], inv['summary']['objectives']))
    w('| stages in sweep.json (swept) | %d of %d, `excluded_stages` = %d; %d stages carry branch variants |' % (
        sw['summary']['stages_swept'], inv['summary']['stages'], len(sw['excluded_stages']), sw['summary']['stages_with_variants']))
    w('| tiers | %s |' % ', '.join('%s %d' % kv for kv in inv['summary']['by_tier'].items()))
    w('| executable script bodies analysed | %d (%s); %d statement-level calls, %d distinct functions/reads |' % (
        g['units'], g['scope'].split('(')[1].rstrip(')'), g['calls'], g['functions_total']))
    w('| function status | %s (occurrences: %s) |' % (g['function_status'], g['occurrence_status']))
    w('| unsupported or partial functions by severity | %s (occurrences %s) |' % (dict(sev_f), sev_o))
    w('| rules.json | %d of %d OWB quests have entries today; patch touches %d (%d replace, %d add); %d new rule kind |' % (
        sum(1 for q in inv['quests'] if q['policy_rules']), len(inv['quests']), len(rules_patch.QUESTS),
        sum(1 for p in rules_patch.QUESTS.values() if p['op'] == 'replace'),
        sum(1 for p in rules_patch.QUESTS.values() if p['op'] == 'add'), len(rules_patch.NEW_KINDS)))
    w('| dialogue specs | %d L8-style scenarios, %d key INFOs tabled |' % (len(c.specs), len(KEY_INFOS)))
    w('')
    w('## Top 10 blockers for co-op in this questline (ranked)')
    w('')
    for n, (title, ev, cons, need) in enumerate(blockers(c), 1):
        w('%d. **%s.** %s' % (n, title, ev))
        w('   - Consequence: %s' % cons)
        w('   - Needed: %s' % need)
    w('')
    w('Also material, below the cut: ShowMessage x%d with no notification effect; AddFormToFormList/AddItemToLeveledList x%d (saved world state set by quest scripts the '
      'guest gate skips); PlaceAtMe x%d (explosions, no Effect); AddItem on non-player containers x%d; XP globals computed from the host\'s level; radios and the '
      'five VEFR reaction quests toggled by the Teleporter; Enc crater spawns keyed on `EnterTrigger Player`.' % (
          c.n('showmessage'), c.n('addformtoformlist') + c.n('additemtoleveledlist'), c.n('placeatme'),
          c.n('additem') - c.fn_player.get('additem', 0)))
    w('')
    w('## Files')
    w('')
    for f, what in (('inventory.json', 'every quest, stage, objective, prerequisite, dialogue count, tier'),
                    ('sweep.json', 'L7 sweep config (m1_goodsprings format + ordered setup/variants, hex FormIDs)'),
                    ('gaps.json / gaps.md', 'every function mapped to Effect type + L5 status, exact unit:line, hazards'),
                    ('policy_review.md / rules.patch.json', 'owner-decision review and the proposed rules.json changes'),
                    ('dialogue.md', 'key conversations, initiator-model breakage, 7 L8-style specs'),
                    ('run_tests.py', 'one command, prints ALL OK; includes mutation twins')):
        w('- `questlines/owb/%s`: %s' % (f, what))
    w('')
    w('## Honest limits')
    w('')
    w('- Console grammar for object-script variables (`set <hexRef>.<var> to v`) and globals by hex FormID is used only in variants and is **unverified**; everything else '
      'is plain `setstage`/`startquest`/`player.moveto`/`player.additem`/`player.addperk`.')
    w('- Stage setup assumes `setstage` on a stopped quest starts it (L5 round 3: engine auto SetRunning). Preconditions inside object scripts (e.g. MQ05 stage 50 branch on the AutoDoc) are covered only by variants.')
    w('- `refnames.json.gz`-only refs (e.g. NVDLC03SinkArrivalMarker) are never emitted into sweep setup; objective-target refs come from quests.json. IDs by source: %s.' % sw['summary']['ids_by_source'])
    w('- Gap statuses are derived from reading L5 sources (engine_hooks.cpp, apply_remote.h, internal lane notes), not from a game run. Reads are classified by name; the first matching rule wins.')
    w('- Quest-level prerequisites come from the questgraph and from SetStage/StartQuest text in OWB scripts only (no script in another plugin references NVDLC03 except comments).')
    w('')
    w('## Test output')
    w('')
    w('```')
    w(test_output.strip() if test_output else '(run `python -I questlines/owb/run_tests.py`, save its output to questlines/owb/last_test_output.txt, then rerun gen_all.py)')
    w('```')
    w('')
    return '\n'.join(L) + '\n'
