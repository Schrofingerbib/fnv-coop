"""Markdown generators: policy_review.md and dialogue.md (data-driven tables + the reviewed prose)."""
import collections

import dialogue as dlg
import fnmap
import lib
import policy

# story moments: (title, quests, scripts that implement the moment, what the party must do)
MOMENTS = [
    ('Tops Presidential Suite: Benny scene and ambush (Ring-a-Ding-Ding stages 20-30)', ['VMQTops'],
     ['BennySCRIPT', 'VMQTopsQuestScript', 'VMQTopsYesManScript'],
     'The suite scene reads Player.GetInCell 2ETopsPresidentialSuite (BennySCRIPT:35) and the quest var BennyGotoPresidential selects the stage-20 '
     'variant ("wait for him" vs "go up with him"). All players must be in the suite before stage 30 (the ambush); GetInCell must read "all in" '
     'for entry.'),
    ('Lucky 38 elevators (Victor, Mr. House, penthouse)', ['VMQ02', 'VDialogueLucky38Entrance'],
     ['Lucky38ElevatorDoorScript', 'Lucky38MrHouseElevatorDoorScript', 'Lucky38VictorElevatorScript', 'Lucky38MrHouseTerminalCodeSCRIPT'],
     'Floor choice is a modal button menu (GetButtonPressed) answered by the activating player; player.MoveTo moves one player. Free-roam transfer: '
     'move only the players in the car.'),
    ('The Fort: Caesar audience, Benny in the cage, equipment confiscation', ['VMQ02', 'VMQ04', 'VMQTops'],
     ['NVCCTheFortGateSCRIPT', 'FortCaesarScript', 'VFortBennyScript', 'NVCCBargeSCRIPT', 'CaesarTentFlapInteriorScript', 'VLegateScript'],
     'Gate/barge/tent-flap scripts act on IsActionRef player; Benny\'s cage reacts to Player.GetInCell TheFortSum. The audience is one conversation '
     'with the initiator; the others must be inside the Fort (TheFortSum) or be gathered.'),
    ('Battle of Hoover Dam start, Legion branch (point of no return)', ['VMQ02', 'VMQ04', 'VMQTops'],
     ['FortCaesarScript', 'FortLuciusScript'],
     'A modal "VEndGameWarningMsgLegion" prompt (GetButtonPressed), then player.MoveTo VHDLegateStartMarker, companions MoveTo, EnableFastTravel 0, '
     'autosave. Needs party consent before the prompt, a party warp, and a single host-owned autosave.'),
    ('Battle of Hoover Dam start, NCR branch (point of no return)', ['VMQ03a', 'VMQ03b', 'VMQCaesarFail', 'VMQNCRFail'],
     ['VHDColonelMooreSCRIPT'],
     'Same pattern at Colonel Moore: "VEndGameWarningMsgNCR" prompt (GetButtonPressed), player.MoveTo vHDOliverTeleportMarker, DisablePlayerControls '
     '1 1 1 1 0 1 1, companions MoveTo, autosave, VStoryState 50.'),
    ('Independent / Yes Man battle start', ['VMQTops'], ['YesManDeadSCRIPT', 'VMQTopsYesManScript'],
     'player.MoveTo VHDHouseIndyBattleStartMarker, control release, autosave, radio quest start.'),
    ('Kimball protection / assassination at the Hoover Dam observation deck', ['VMQ03a', 'VMQ03b'],
     ['VHDKimballScript', 'VHDKimballMPScript', 'VHDKimballMP01SCRIPT', 'VHDCaptainGrahamSCRIPT', 'VHDRangerKimballGuard01SCRIPT',
      'VHDRangerKimballGuard02SCRIPT', 'VHDKimballRangerAmbushedSCRIPT', 'VHDWatsonsHelmetScript', 'VHDKimballVertibirdScript'],
     'EnableFastTravel 0 for the whole scene, sniper/guard distance checks against the player, quest timers tied to Player.GetInCell HooverDam, '
     '"rest until tomorrow" dialogue with DisablePlayerControls. The quest fails if the (host) player leaves: with a party it must fail only when '
     'everyone has left.'),
    ('Van Graff warehouse (Birds of a Feather)', ['VMS29a'], ['VFSGloriaVanGraffScript', 'VFSSimonScript', 'RoseofSharonCassidyScript'],
     'player.MoveTo VFSPlayerWarehouseMarkerREF, DisablePlayerControls 1 1 1 1 0 1 1, Cassidy recruited with perks (AddPerk CompanionSuite/WhiskeyRose).'),
    ('Bitter Springs infirmary (Infirmary Blues)', ['VMS40'], ['VBSLtMarklandSCRIPT'],
     'Lock + fade + set GameHour +4 while a doctor companion treats patients (INFO 133F1A); books are hand-ins from the initiator.'),
    ('Ultra-Luxe dinner and freezer (Beyond the Beef)', ['VMS18'], ['VMS18MembersOnlyDoorEnterSCRIPT', 'VMS18TedGundersonSCRIPT',
                                                                   'VMS18CompanionFreezerExitSCRIPT'],
     'IsActionRef/GetInCell gates, SetPCEnemyOfFaction, companion lock-in (Boone/Cass/Veronica/Arcade flags) and perk removal on hand-in.'),
]


HAZARD_KIND = [('PLAYER_TELEPORT', 'PartyWarp'), ('CONTROLS', 'SetPlayerControls'), ('FAST_TRAVEL', 'FastTravelLock'), ('SAVE', 'SaveSuppress'),
               ('MESSAGE_CHOICE', 'ModalChoice (new kind)'), ('POINT_OF_NO_RETURN', 'ModalChoice (new kind)'),
               ('GUEST_ACTIVATION', 'PartyAwareCond'), ('PLAYER_REF_COND', 'PartyAwareCond'), ('getincell', 'PartyTether'),
               ('COMPANION', 'CompanionOwner'), ('GUEST_EVENT', 'GuestEventRelay (new kind)')]


def _rows_by_script(rows):
    out = collections.defaultdict(list)
    for r in rows:
        if r['unit']['touched'] and fnmap.overall(r['d']['capture'], r['d']['apply']) in ('missing', 'partial'):
            out[r['unit']['script_edid']].append(r)
    return out


def policy_md(D, edids, rows, need, cmp_rows, patch, header_note, low_tier=()):
    by_script = _rows_by_script(rows)
    base = D.rules['quests']
    md = ['# act2 policy review: Ring-a-Ding-Ding and the shared Act 2 quests', '',
          'Generated by `questlines/act2/build.py` from the quest data, the script evidence and `docs/quest-policies/rules.json` '
          '(%s). The companion machine-readable output is `rules.patch.json`.' % header_note, '',
          'Owner decisions (docs/MASTER-PLAN.md, final): karma and faction reputation shared; faction membership/rank shared; quest items duplicated '
          'to every party member; caps/XP quest rewards in full to every player; the dialogue initiator chooses and everybody sees the line and the '
          'result; free roam but story-critical moments need the party in one area; companions owned by the recruiter (open); max 4 players (open).', '',
          '## 1. Method', '',
          'For every touched script of the %d quests (quest, stage, INFO begin/end, the %d driver INFOs of other quests, and the dependent object '
          'scripts) the function-level evidence from `gaps.json` is mapped to the rule kind that must exist for the owner decision to hold. A '
          'kind is **needed** when at least one script line of the quest produces its hazard, **missing** when `rules.json` has no such rule for '
          'the quest. Shared object scripts (FortCaesar, VHDColonelMoore, ...) are attributed to every quest that depends on them, so a missing '
          'rule can be reported for several quests with the same evidence line. Only the 16 existing kinds are used for the patch; two kinds and two '
          'parameter extensions are proposed separately in section 7.' % (
              len(edids), len(lib.driver_infos(D, edids))), '',
          '## 2. Owner decisions vs rules.json', '', '| decision | rule kind | quests whose scripts need it and have no such rule |', '|---|---|---|']
    for label, kind, q in policy.owner_checks(D, need, cmp_rows):
        md.append('| %s | %s | %s |' % (label, kind, ', '.join(q) if q else '-'))
    md += ['', '## 3. Per-quest verdict', '',
           '| quest | tier | rules.json | needed by the scripts | missing (proposed add) | params to extend |', '|---|---|---|---|---|---|']
    for row in cmp_rows:
        mods = '; '.join('%s.%s +%s' % (m['kind'], m['param'], m['add'] if isinstance(m['add'], list) else sorted(m['add']))
                         for m in row['modify']) or '-'
        md.append('| %s | %s | %s (%s) | %s | %s | %s |' % (
            row['quest'], row['tier'], ', '.join(row['have']) or '(none)', 'curated' if row['curated'] else 'derived',
            ', '.join(row['need']) or '-', ', '.join(row['missing']) or '-', mods))
    md += ['', 'Evidence for every proposed add (script, line, text) is in `rules.patch.json` under `quests.<EDID>.add[].evidence`.', '',
           '### Quests with no rule or no curated judgement', '']
    for e in edids:
        if not base[e]['curated']:
            md.append('- `%s` (%s): rules are the skeleton derived from risk categories, never reviewed by hand.' % (e, base[e]['tier']))
        if not base[e]['rules']:
            md.append('- `%s` (%s) has **no rules at all** although it is a T4 quest with %d player-teleport and %d forced-dialogue/message lines.' % (
                e, base[e]['tier'], sum(1 for r in rows if e in r['unit']['attrib'] and 'PLAYER_TELEPORT' in r['d']['hazards']),
                sum(1 for r in rows if e in r['unit']['attrib'] and 'FORCED_DIALOGUE' in r['d']['hazards'])))
    md += ['', '## 4. Barriers and tethers at story moments', '',
           'Every row is a moment the scripts themselves make single-player assumptions about. "Gaps" counts unsupported (missing/partial) '
           'script lines in the listed scripts. The tether is the area the party must share before the host lets the scene run.', '',
           '| moment | quests | key script lines | unsupported lines | current rule kinds (union) | verdict |', '|---|---|---|---|---|---|']
    for title, qs, scripts, note in MOMENTS:
        lines = []
        for sname in scripts:
            for r in sorted(by_script.get(sname, ()), key=lambda r: r['line']):
                if set(r['d']['hazards']) & {'PLAYER_TELEPORT', 'CONTROLS', 'MESSAGE_CHOICE', 'POINT_OF_NO_RETURN', 'FAST_TRAVEL_LOCK_ON',
                                              'GUEST_ACTIVATION', 'SAVE'} or r['fn'] == 'getincell':
                    lines.append('%s:%d %s' % (sname, r['line'], r['fn']))
        have = sorted({k for q in qs for k in cmp_row(cmp_rows, q)['have']})
        gap_n = sum(len(by_script.get(s, ())) for s in scripts)
        needs = set()
        for sname in scripts:
            for r in by_script.get(sname, ()):
                hz = set(r['d']['hazards'])
                for hazard, kind in HAZARD_KIND:
                    if hazard in hz or (hazard == 'getincell' and r['fn'] == 'getincell'):
                        needs.add(kind)
        missing = sorted(k for k in needs if k not in have)
        if not any(by_script.get(s) for s in scripts):
            verdict = 'no script evidence found'
        elif missing:
            verdict = 'missing: ' + ', '.join(missing)
        else:
            verdict = 'rule kinds present (params to verify)'
        md.append('| %s | %s | %s | %d | %s | %s |' % (title, ', '.join(qs), ('; '.join(lines[:6]) + (' ...' if len(lines) > 6 else '')) or '-',
                                                    gap_n, ', '.join(have) or '(none)', verdict))
    md += ['']
    for title, qs, scripts, note in MOMENTS:
        md.append('- **%s**: %s' % (title, note))
    md += ['',
           '### Decisions for barriers/tethers', '',
           '1. **Tether before the stage fires, not after.** For every moment above the host defers the stage/INFO result (RulesEngine '
           '`onStageRequested` -> Deferred) until all players are in the tether area, then gathers stragglers with `auto-warp`. The point-of-no-return '
           'prompts (Fort, Lucius, Colonel Moore) additionally need explicit party consent (proposed kind `ModalChoice`, consent=party).',
           '2. **Entry = all in, exit = any in.** The Hoover Dam quest timers (VMQ03a/b: "left the area or waited") must use GetInCell `entry:all-in,exit:any-in` '
           'exactly as rules.json already says for VMQ03a/b; the quest must fail only when the last player has left, never because the host stepped out.',
           '3. **No Barrier rule is needed in this questline.** Barrier is for per-player steps that every player passes individually (chargen). Every '
           'moment here is a shared scene, so PartyTether is the right gate. The one candidate (VMQ04 / Securitron vault) has no start path in the data '
           '(see section 9).',
           '4. **Empty tether cell lists are inert or unknown.** VDialogueCaesar, VMQNCRFail, VMS18 and VMS40 carry `PartyTether` with `cells: []`; '
           'the scripts name no cell for VMS40/VMQNCRFail (no `GetInCell`), so the correct cell has to come from the objective target REFRs\' parent cells, '
           'which are not in `out/questdata` (unknown). VMS18 has one: `ULKitchen` (freezer) is proposed in the patch.', '',
           '## 5. Who controls cinematics', '',
           '- **The host runs the script; the initiator is whoever triggered it.** Quest, stage and INFO result scripts run only on the host (Guest gate). '
           'A cutscene lock (`DisablePlayerControls`) is therefore a host event that must be broadcast: `SetPlayerControls` scope `all`, release by the '
           'host\'s `EnablePlayerControls` effect, 120 s failsafe, unlock on reconnect (rules.json already says this). **Gap:** neither lock nor release '
           'is captured from scripts today (gaps.json CONTROLS: 44 lines), so the rule has nothing to trigger on.',
           '- **Mask granularity matters.** The scripts use three forms: six of seven flags set (`1 1 1 1 0 1 1`, Hoover Dam/Van Graff), a single flag '
           '(`0 0 0 1 0 0 0`, Tops/Yes Man) and bare `DisablePlayerControls` (INFO scripts). The applier today realises one coarse all-controls '
           'flag (coop_module `g_scriptedLock`), so the single-flag scenes would wrongly freeze everything. (Flag order not asserted here: per-flag '
           'meaning is an L5 TODO.)',
           '- **The initiator drives dialogue and skill checks; the others spectate.** During an INFO the other players\' input is locked by the dialogue '
           'lock, not by DisablePlayerControls. Cutscene-style INFOs (Kimball "rest", Silver Rush brawl, Fort audience) call DisablePlayerControls in their '
           'begin script, so the lock must cover every player for the INFO\'s duration.',
           '- **Teleports end cutscenes.** Every battle-start script locks, teleports (`player.MoveTo`), moves companions, autosaves and releases in '
           'that order; PartyWarp must apply in the same transaction as the lock so no player is left outside.', '',
           '## 6. Reward routing', '']
    r_all = sum(1 for r in rows if 'PLAYER_REWARD' in r['d']['hazards'] and r['fn'] == 'rewardxp')
    fr = collections.Counter(r['fn'] for r in rows if 'FACTION_RELATION' in r['d']['hazards'] and r['unit']['touched'])
    md += ['- **XP/caps/items to every player (RewardAll).** %d `RewardXP` lines occur in the touched scripts; every one must reach every player in full. '
           'Quests that have XP lines but no RewardAll rule are listed in section 2.' % r_all,
           '- **Quest items duplicated (QuestItemDup).** `player.AddItem` of quest objects (Silver Rush package, Platinum Chip, keycards) must be '
           'Recipient=All. **Not covered by any rule:** items that reach the player by loot/trade rather than by a script command. The Platinum Chip '
           'is the critical case: `VPlatinumChipSCRIPT begin OnAdd player` runs `SetStage VMQTops 80` + `CompleteAllObjectives VMQTops` '
           '(Ring-a-Ding-Ding completes when the chip enters the player inventory), `BennySCRIPT:5` puts it on Benny, and `player.RemoveItem '
           'VPlatinumChip` hand-ins exist in VDialogueMrHouse/VMQTops INFOs. Without a guest-event relay a guest who loots Benny completes nothing.',
           '- **Hand-ins remove from the owner only (RemoveItemPerOwner)** (Markland books, Fort Benny Stealth Boy + lockpick, Simon\'s rifle and '
           'armor) **but** quest items such as the Platinum Chip are duplicated, so their removal must hit every copy. The rule table cannot express '
           '"this item is duplicated, so its removal is global"; proposal: RemoveItemPerOwner params `except_quest_items: true` (QuestItemDup wins).',
           '- **Karma and reputation shared (ShareKarmaRep)**: `RewardKarma`/`AddReputation` lines apply once on the host and broadcast the absolute '
           'result. The negative branches matter as much: `RewardKarma -100` (freezer), `AddReputationExact RepNVCaesarsLegion 0 1000` (VDialogueCaesar '
           'stage 110), `SetReputation RepNVCaesarsLegion 0 9999` (VMQCaesarFail 5) are shared, one-shot, irreversible.',
           '- **Faction membership and hostility are shared** (owner decision). Player-subject `AddToFaction/RemoveFromFaction` is rare in this '
           'questline (companions only), but `SetPCEnemyOfFaction` (%d lines) and `SetEnemy/SetAlly PlayerFaction` (%d + %d lines) change the party\'s '
           'standing; no existing kind carries them (parameter extension in section 7).' % (
               fr['setpcenemyoffaction'], fr['setenemy'], fr['setally']), '',
           '## 7. Proposals beyond the 16 kinds', '']
    for nk in patch['new_kinds']:
        md += ['### New kind `%s`' % nk['kind'], '', nk['summary'], '', '- params: `%s`' % nk['params'], '- why no existing kind fits: %s' % nk['why_not_existing'],
               '- evidence: %s' % nk['evidence'], '- quests: %s' % ', '.join(nk['quests']), '']
    for pe in patch['param_extensions']:
        md += ['### Parameter extension `%s.%s`' % (pe['kind'], pe['param']), '', pe['why'], '', '- proposed: `%s`; quests: %s' % (
            pe.get('proposed_value'), ', '.join(pe['quests'])), '']
    md += ['## 8. Things rules cannot fix (engine/hook work)', '',
           'Rules route and gate effects that exist. These gaps are below the rule layer and are tracked in `gaps.md`: script-level control lock '
           'capture, `SetPlayerTeammate`/`AddPerk`/`SetFactionRank` capture and apply, `SetFastTravelLock` capture and apply, `ForcedDialogue` capture, '
           'guest-originated Activate/OnAdd/OnTriggerEnter events, `PlaceAtMe` spawns, player-reference reads over the party, and PartyWarp being inactive '
           'without `RulesEngine::Options::playerRef`.', '',
           '## 9. Unknowns (not guessed)', '',
           '- **VMQ04 "Bright and Shiny" has no start path in the data.** Checked: stage `next_quest` links, quest conditions, and the reference tables of '
           'every quest, stage, INFO and object script. Nothing starts or advances it except its own quest script (`SetStage VMQ04 20`). It may be '
           'cut content or started by a mechanism the extractor does not see. Policy: do not gate any '
           'ending on it; L9 should decide `ExcludeFromGate` after an in-game check.',
           '- **Tether cells for VMS40 / VMQNCRFail / VDialogueCaesar** (empty lists) cannot be derived from the scripts.',
           '- **VStoryState / VStoryEvent\\* globals** (set at battle starts) are host-authoritative SetGlobal; whether any client script reads a stale copy '
           'between the set and the next resync is not verified.',
           '- **Whether `RulesEngine` honours `absent: skip`/`radius`** (section 7) is unknown; the rules loader keeps unknown scalars but the engine '
           'ignores them today.',
           ] + ['- **%s "%s"** (%d stages, %d objectives, tier %s) is a main-line (VMQ*) quest below the policy tiers: no rules.json entry, so only the generic '
                'stage sync applies. No rule is proposed; listed so the line is not mistaken for complete.' % (
                    x['edid'], x['name'], x['stages'], x['objectives'], x['tier']) for x in low_tier] + ['']
    return '\n'.join(md)


def cmp_row(cmp_rows, q):
    return next(r for r in cmp_rows if r['quest'] == q)


def dialogue_md(D, edids, cat, specs, header_note):
    s = dlg.summary(cat)
    key = dlg.key_infos(cat)
    tags = s['by_tag']
    lines = ['# act2 dialogue: key conversations and where initiator-picks breaks', '',
             'Generated by `questlines/act2/build.py` (%s). Machine-readable: `dialogue.json` (every key INFO), `dialogue_specs/*.json` '
             '(%d L8-style scenario specs, schema 1, kind "dialogue", same step vocabulary as `plugin/dialogue/scenarios`).' % (header_note, len(specs)), '',
             '## 1. What is in scope', '',
             '%d INFOs: %d belong to the 14 quests\' own dialogue containers and %d are *driver* INFOs in other quests (VDialogueBenny, '
             'VHDAssassinationDialog, VFreeformTheFort, VDialogueMrHouse, ...) whose result scripts start/advance a questline quest. '
             'Most quests of the line have no INFOs of their own (VMQ01, VMQ02, VMQ03a, VMQ03b, VMQ04, VMQCaesarFail, VMQNCRFail: 0); the conversations that '
             'drive them live in the driver quests (Benny, Caesar, Mr. House, Kimball, the Fort freeform quests). Ring-a-Ding-Ding (VMQTops) is the '
             'exception with its own 350 INFOs (Swank, Yes Man, Benny follow-ups).' % (s['infos'], s['own'], s['driver']), '',
             'Tags are derived only from INFO conditions and begin/end scripts: ' + ', '.join('%s %d' % (k, v) for k, v in tags.items()) + '.',
             '%d INFOs carry a co-op-relevant tag (skill/speech check, quest branch, companion, faction, control lock, combat, hand-in, ...).' % s['key_infos'],
             '', '## 2. Default model and where it breaks', '',
             'Default (owner decision): the initiator chooses; every player sees the line and the result; conditions on the player are evaluated for '
             'the initiator; the result script runs once on the host; skill checks use the initiator\'s skill (best-of-party assist is optional). '
             'The model breaks, or needs extra rules, in these classes (counts are INFOs in scope):', '',
             '| # | break | INFOs | examples | needed |', '|---|---|---|---|---|']

    def ids(tag, n=4, containers=None):
        sel = [c for c in cat if tag in c['tags'] and (not containers or c['container'] in containers)]
        return len(sel), ', '.join(c['id'] for c in sel[:n])
    brk = [
        ('Speech/skill check decides a quest branch (SUCCEEDED/FAILED pair)', 'speech', 'initiator skill; a low-skill initiator can lock the whole party out of a branch; optional best-of-party assist (policy switch)'),
        ('Hand-in removes items/perks from `player`', 'handin', 'RemoveItemPerOwner vs quest-item duplication; recruiter-owned perks (`RemovePerk`)'),
        ('Companion recruit / dismiss / freezer', 'companion', 'CompanionOwner: effects target the recruiter, not the initiator'),
        ('Result script locks controls or runs a cutscene', 'controls', 'lock every player for the INFO duration; release on host effect'),
        ('Result script forces NPC speech to the player (SayTo/StartConversation)', 'forced', 'ForcedDialogue with an initiator; spectators need playback'),
        ('Combat starts right after the line', 'combat', 'the conversation must close cleanly for all; combat vs which player is NV:MP\'s call'),
        ('Condition reads another player-relative state (cell, distance, same cell, worldspace)', 'party_cond', 'evaluate for the initiator; entry gates all-in'),
        ('Condition reads the initiator inventory (GetItemCount/GetEquipped/GetHasNote)', 'item_check', 'split inventories: the line exists only if the initiator holds the item; quest items must be duplicated'),
        ('Sex-dependent lines (GetIsSex on the player)', 'sex', 'initiator\'s sex; spectators see the initiator\'s variant'),
        ('Barter / companion container menus', 'barter', 'per-player UI; initiator only'),
        ('Scripted teleport inside the INFO', 'teleport', 'PartyWarp in the same transaction'),
        ('Perk conditions', 'perk', 'initiator perks'),
        ('Reputation / karma lines', 'karma', 'shared; apply once'),
        ('Starts another quest', 'start_quest', 'quest exists for everyone'),
    ]
    for n, (title, tag, needed) in enumerate(brk, 1):
        c, ex = ids(tag)
        lines.append('| %d | %s | %d | %s | %s |' % (n, title, c, ex, needed))
    n_b = sum(1 for c in cat if c['type'] == 'conversation')
    lines += ['', 'Additionally %d INFOs are NPC-to-NPC `conversation` type and run on the host without a menu session (no initiator), and combat/detection '
              'barks need no lock (dialogue-design.md 7.4).' % n_b, '',
              '### Conversations the initiator-picks model breaks (needs a rule or hook, not just policy)', '',
              '1. **Platinum Chip loot (Ring-a-Ding-Ding completion).** Not a conversation, but it is what finishes the dialogue chain: '
              '`VPlatinumChipSCRIPT OnAdd player` sets VMQTops stage 80. The player that loots Benny may be a guest; no dialogue initiator exists.',
              '2. **Companion hand-in at the freezer (VMS18 INFO 129A37).** `player.RemovePerk`/`SetPlayerTeammate 0` use `player`; with recruiter != initiator '
              'the perk is taken from the wrong player (spec `dlg_act2_freezer_companion_owner`).',
              '3. **Split inventories (VMS40 INFOs 133F16, 133F19, 13A02F, 13A031).** Item-count conditions are evaluated on the initiator only '
              '(spec `dlg_act2_markland_split_books`).',
              '4. **Elevator floor menus** (Lucky38ElevatorDoorScript, Lucky38VictorElevatorScript INFO 165103 "What floor, pardner?"): the choice is a '
              '`GetButtonPressed` modal button menu answered by one player, then `player.MoveTo` moves one player (spec `dlg_act2_lucky38_elevator_floor`).',
              '5. **Endgame prompts** (`VEndGameWarningMsgLegion`/`NCR`) are not dialogue but ShowMessage buttons that start the final battle; '
              'a single player\'s button press must not commit the party.',
              '6. **Forced speeches at Hoover Dam** (Kimball/Moore: `SayTo VHDKimballSpeech02` chains, `OnPackageDone`) have no player initiator at all; '
              'they run for whoever stands near and lock controls (`DisablePlayerControls 1 1 1 1 0 1 1`).',
              '7. **Speech-gated branches that change the shared story** (Benny `<Speech check to really convince him.>` INFO 147851 removes the ambush '
              'and moves Benny to the Fort for everyone; Caesar `[Speech Check 65+]` INFO 164590 starts VMS35). A weak initiator permanently takes '
              'the branch from the party: needs the optional best-of-party assist or an explicit "party chooses the speaker" UI. Owner decision pending.', '',
              '## 3. Key conversations by quest (clusters = same speaker + same prompt)', '']
    W = {'speech': 3, 'skill': 3, 'perk': 1, 'stage': 1, 'start_quest': 3, 'companion': 4, 'faction': 2, 'reputation': 1, 'karma': 2, 'controls': 3,
         'combat': 3, 'kill': 3, 'handin': 2, 'barter': 1, 'forced': 2, 'teleport': 2}
    clusters = collections.defaultdict(list)
    for c in key:
        clusters[(c['container'], c['speakers'][0] if c['speakers'] else '-', c['prompt'] or '')].append(c)
    per = collections.defaultdict(list)
    for k, v in clusters.items():
        per[k[0]].append((sum(W.get(t, 0) for c in v for t in set(c['tags'])), k, v))
    order = sorted(per, key=lambda c: (-sum(len(v) for _, _, v in per[c]), c))
    for cont in order:
        qs = sorted({q for _, _, v in per[cont] for c in v for q in c['quests']})
        lines += ['### %s  (%s; %d key INFOs)' % (cont, 'drives ' + ', '.join(qs) if cont not in edids else 'own container',
                                                   sum(len(v) for _, _, v in per[cont])), '',
                  '| speaker | prompt | INFOs | tags | checks | first line |', '|---|---|---|---|---|---|']
        for sc, k, v in sorted(per[cont], key=lambda x: (-x[0], x[1]))[:8]:
            tg = collections.Counter(t for c in v for t in c['tags'] if t in dlg.KEY_TAGS)
            chk = sorted({'%s %s %g' % (x['av'], x['op'], x['value']) for c in v for x in c['checks']})
            lines.append('| %s | %s | %s%s | %s | %s | %s |' % (
                k[1].replace('FalloutNV.esm:', '#') + (' (no EDID in data)' if k[1].startswith('FalloutNV.esm:') else ''), (k[2] or '')[:48].replace('|', '/'), ' '.join(c['id'] for c in v[:4]),
                ' (+%d)' % (len(v) - 4) if len(v) > 4 else '', ', '.join('%s %d' % kv for kv in tg.most_common(5)), '; '.join(chk[:3]) or '-',
                v[0]['line'][:44].replace('|', '/')))
        lines.append('')
    lines += ['## 4. Scenario specs', '',
              'Each spec was generated by `questlines/act2/specs.py`, which aborts when a FormID, prompt, speaker or script fragment disagrees with '
              'the ESM data. Setup commands are derived from the INFO conditions only; what could not be derived is under `setup.unmet`. '
              'No act2 save exists, so `save` is null (unknown).', '',
              '| spec | quests | steps | policy checks | known gap/break |', '|---|---|---|---|---|']
    for sid, sp in sorted(specs.items()):
        lines.append('| %s | %s | %d | %s | %s |' % (sid, ', '.join(sp['quests']), len(sp['steps']), ', '.join(sp['policy_checks']),
                                                    sp.get('known_break') or '; '.join(sp.get('known_gaps', [])) or '-'))
    lines += ['', '## 5. Open questions for L8 / the owner', '',
              '1. Best-of-party speech/skill assist: off by default? (changes who can unlock the Benny and Caesar branches.)',
              '2. Say-once INFOs (`say_once` flag: %d in scope) share their spoken state (SayOnceFlag, union) - default shared.' % sum(
                  1 for c in cat if 'say_once' in c['flags']),
              '3. A forced scene with no initiator (Kimball speech, Moore): which player counts as `player` for `SayTo player`? Proposal: the player '
              'nearest to the speaker at trigger time, all others spectate.',
              '4. Barter/companion container menus (%d INFOs): per-player UI; no sync needed except the resulting inventory.' % tags.get('barter', 0), '']
    return '\n'.join(lines)
