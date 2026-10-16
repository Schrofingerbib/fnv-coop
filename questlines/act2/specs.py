"""L8-style dialogue scenario specs (plugin/dialogue/scenarios format: schema 1, kind "dialogue") for the key act2 conversations.

Every FormID, prompt and line prefix is checked against the INFO data; any mismatch raises SpecError, so specs cannot drift from the
ESM data. Setup commands are derived from the INFO conditions only (what could not be derived is listed under setup.unmet).
"""
import lib


class SpecError(Exception):
    pass


class Specs:
    def __init__(self, D, dials):
        self.D, self.dials = D, dials

    def ref(self, local):
        return 'FalloutNV.esm:%s' % local.upper()

    def info(self, local, prefix=None, flags=(), speaker=None, topic=None, begin=None, end=None):
        D = self.D
        r = D.info_by_ref.get(self.ref(local))
        if r is None:
            raise SpecError('INFO %s not in data' % local)
        txt = next((x['text'] for x in r['responses'] if x.get('text')), '')
        if prefix is not None and not txt.startswith(prefix):
            raise SpecError('INFO %s text %r does not start with %r' % (local, txt[:60], prefix))
        for f in flags:
            if f not in r['flags']:
                raise SpecError('INFO %s lacks flag %s' % (local, f))
        if speaker is not None:
            ids = {D.edid_of(c['p1']) for c in r['conditions'] if c['func'] == 'GetIsID'}
            if speaker not in ids and D.edid_of(r.get('speaker') or '') != speaker:
                raise SpecError('INFO %s not spoken by %s (has %s)' % (local, speaker, sorted(i for i in ids if i)))
        if topic is not None and r['topic'] != self.ref(topic):
            raise SpecError('INFO %s not under topic %s' % (local, topic))
        for key, want in (('begin', begin), ('end', end)):
            if want is not None and want.lower() not in ((r.get(key) or {}).get('source') or '').lower():
                raise SpecError('INFO %s %s script lacks %r' % (local, key, want))
        return r

    def conds_setup(self, r):
        """Console setup that satisfies the INFO's quest/global/item conditions (only what is derivable; the rest is reported)."""
        D = self.D
        cmds, unmet = [], []
        for c in r['conditions']:
            fn, p1, p2, op, v = c['func'], c['p1'], c['p2'], c['op'], c['value']
            q = D.by_ref.get(p1) if isinstance(p1, str) else None
            hx = '%X' % int(D.hexid(p1), 16) if isinstance(p1, str) and D.resolves(p1) else None
            if fn == 'GetQuestVariable' and q and op == '==':
                name = lib.quest_var_name(q, p2)
                if name:
                    cmds.append('set %s.%s to %d' % (hx, name, int(v)))
                    continue
            elif fn == 'GetStage' and q and op in ('>=', '==') and v > 0:
                cmds.append('setstage %s %d' % (hx, int(v)))
                continue
            elif fn == 'GetStageDone' and q and op == '==' and v == 1:
                cmds.append('setstage %s %d' % (hx, p2))
                continue
            elif fn == 'GetObjectiveDisplayed' and q and op == '==' and v == 1:
                cmds.append('setobjectivedisplayed %s %d 1' % (hx, p2))
                continue
            elif fn == 'GetObjectiveCompleted' and q and op == '==' and v == 1:
                cmds.append('setobjectivecompleted %s %d 1' % (hx, p2))
                continue
            elif fn == 'GetGlobalValue' and hx and op == '==':
                cmds.append('set %s to %s' % (hx, int(v) if v == int(v) else v))
                continue
            elif fn == 'GetItemCount' and hx and op in ('>=', '==') and v >= 1:
                cmds.append('player.additem %s %d' % (hx, int(v)))
                continue
            if fn not in ('GetIsID', 'GetIsVoiceType', 'GetActorValue'):
                unmet.append('%s(%s,%s) %s %s' % (fn, D.edid_of(p1) if isinstance(p1, str) else p1, p2, op, v))
        return cmds, unmet

    def npc_ref(self, edid):
        for src in (self.D.forms, self.D.refnames):
            for k, v in sorted(src.items()):
                if v.get('edid') == edid and v.get('sig') in ('ACHR', 'ACRE'):
                    return k
        raise SpecError('no placed ref %s' % edid)

    def pick(self, actor, topic, info, line, **expect):
        d = self.dials[self.ref(topic)]
        self.info(info, line, topic=topic)
        return {'id': 'pick_%s_%s' % (actor, info.lower()), 'action': 'pick', 'actor': actor, 'topic': self.ref(topic),
                'topic_edid': d['edid'], 'prompt': d['name'], 'info': self.ref(info), 'line': line, 'expect': dict(expect)}

    def talk(self, actor, npc_edid, speaker_base, greeting, line, **expect):
        r = self.info(greeting, line, speaker=speaker_base)
        return {'id': 'talk_%s_%s' % (actor, npc_edid.lower()), 'action': 'talk', 'actor': actor, 'npc': npc_edid,
                'npc_ref': self.npc_ref(npc_edid), 'greeting_info': self.ref(greeting), 'line': line, 'expect': dict(expect)}, r

    def scenario(self, sid, name, summary, steps, quests, setup, policy, extra=None):
        out = {'schema': 1, 'kind': 'dialogue', 'id': sid, 'name': name, 'plugin': 'FalloutNV.esm', 'summary': summary, 'tier': 'T4',
               'players': {'host': 0, 'p1': 1, 'p2': 2}, 'quests': list(quests), 'save': None,
               'save_note': 'unknown: no act2 save exists yet (needs a save where the INFO conditions hold; setup.cmds derives what the '
                            'conditions require, setup.unmet lists what it could not)',
               'policy_checks': policy, 'setup': setup, 'steps': steps,
               'invariants': ['no_duplicate_reward', 'one_conversation_per_npc', 'result_script_ran_once_on_host',
                              'host.shared == client.shared'],
               'oracle': 'docs/dialogue-design.md'}
        out.update(extra or {})
        return out


def setup_for(S, *infos):
    cmds, unmet = [], []
    for r in infos:
        c, u = S.conds_setup(r)
        cmds += [x for x in c if x not in cmds]
        unmet += [x for x in u if x not in unmet]
    return {'cmds': cmds, 'unmet': unmet}


def build_specs(D, dials):
    S = Specs(D, dials)
    out = {}
    prep = lambda **players: {'id': 'prep', 'action': 'setup', 'players': players}  # noqa: E731

    # 1. Benny at the Tops: Speech >= 50 prevents the ambush; the initiator's skill decides, the story changes for everyone
    t, g = S.talk('p1', 'BennyREF', 'Benny', '1194AD', '{FIRST TALK}', opened=True, spectators=['host', 'p2'])
    r = S.info('147851', '[SUCCEEDED]', flags=['goodbye'], speaker='Benny', begin='set VMQTops.NoAmbush to 1', end='RewardXP 200')
    pk = S.pick('p1', '147802', '147851', '[SUCCEEDED]', spectator_lines=['[SUCCEEDED]'], check_evaluated_on='p1',
                script_var={'VMQTops.NoAmbush': 1}, objective_displayed={'VMQTops': 40}, xp_each_player=200, converged=True,
                closed_after='Goodbye')
    out['dlg_act2_benny_speech_no_ambush'] = S.scenario(
        'dlg_act2_benny_speech_no_ambush', 'Benny at the Tops: Speech >= 50 prevents the ambush (initiator skill)',
        'p1 has Speech 60, host and p2 have Speech 10. The speech option exists and succeeds only per p1; the begin script (NoAmbush, Benny '
        'leaves for the Fort, ambush trigger deleted) runs once on the host and +200 XP goes to every player. Benny leaving changes the '
        'shared story for all players.',
        [prep(p1=['player.forceav speech 60'], host=['player.forceav speech 10'], p2=['player.forceav speech 10']), t, pk],
        ['VMQTops'], setup_for(S, g, r),
        ['dialogue-ownership', 'player-reference-mapping', 'reward-both-vs-owner', 'speech-check-initiator', 'story-branch-shared'])

    # 2. Benny in the Fort: Stealth Boy + lockpick hand-in removed from the initiator only; XP + karma shared
    r = S.info('153AFE', 'Baby...', flags=['goodbye'], speaker='VFortBenny', begin='player.removeitem stealthboy 1', end='RewardKarma 300')
    pk = S.pick('p2', '153AE4', '153AFE', 'Baby... your generosity', items_removed={'p2': ['StealthBoy', 'Lockpick'], 'host': [], 'p1': []},
                karma_shared=300, xp_each_player=300, script_var={'VFreeformTheFort.bHelpedBennyEscape': 1}, converged=True)
    out['dlg_act2_fort_benny_handin'] = S.scenario(
        'dlg_act2_fort_benny_handin', 'Fort Benny: hand in a Stealth Boy and Lockpick (hand-in per owner, karma and relations shared)',
        'p2 (a client) answers the VFortBenny topic. GetItemCount conditions are evaluated on p2; player.RemoveItem takes the items from p2 only '
        '(RemoveItemPerOwner) while +300 XP goes to every player and +300 karma is shared. SetAlly/SetEnemy against PlayerFaction change the '
        'faction table for the whole party.',
        [prep(p2=['(runner resolves StealthBoy/Lockpick ids from the INFO GetItemCount conditions) player.additem <id> 1']), pk],
        ['VMQ02', 'VMQTops'], setup_for(S, r), ['dialogue-ownership', 'reward-both-vs-owner', 'handin-per-owner', 'karma-shared'],
        extra={'known_gaps': ['SetAlly/SetEnemy PlayerFaction have no effect type (gaps.json FACTION_RELATION)']})

    # 3 + 4. Caesar [Speech Check 65+]: initiator skill; VMS35 starts for everyone
    for tag, spk1, spkh, info, line in (('pass', 70, 10, '164590', '[SUCCEEDED]'), ('fail_host_high', 20, 90, '164591', '[FAILED]')):
        r = S.info(info, line, speaker='FortCaesar')
        expect = {'spectator_lines': [line], 'check_evaluated_on': 'p1', 'converged': True}
        if tag == 'pass':
            expect.update(stage={'VMS35': 10}, xp_each_player=65)
        else:
            expect.update(script_var={'VDialogueCaesar.CaesarTumor': 3})
        pk = S.pick('p1', '164559', info, line, **expect)
        out['dlg_act2_caesar_speech_' + tag] = S.scenario(
            'dlg_act2_caesar_speech_' + tag, 'Caesar "[Speech Check 65+]" (%s)' % tag,
            'p1 has Speech %d, the host has Speech %d: only p1\'s skill counts (speech-check-initiator). On success StartQuest VMS35 / '
            'SetStage VMS35 10 run once on the host and the quest exists for every player.' % (spk1, spkh),
            [prep(p1=['player.forceav speech %d' % spk1], host=['player.forceav speech %d' % spkh], p2=['player.forceav speech 10']), pk],
            ['VDialogueCaesar', 'VMS35'], setup_for(S, r), ['dialogue-ownership', 'speech-check-initiator', 'reward-both-vs-owner'])

    # 5. Mortimer / freezer: recruiter != initiator
    r = S.info('129A37', 'Wonderful.', flags=['say_once'], speaker='VMS18Mortimer', begin='CraigBooneREF.SetPlayerTeammate 0')
    pk = S.pick('p1', '129A36', '129A37', 'Wonderful.', karma_shared=-100, perk_removed_from='recruiter (p2)',
                companion_disabled='CraigBooneREF', converged=True)
    out['dlg_act2_freezer_companion_owner'] = S.scenario(
        'dlg_act2_freezer_companion_owner', 'Mortimer: companion left in the freezer (recruiter != initiator)',
        'p2 recruited Boone; p1 (initiator) answers Mortimer. The result script removes the perk "Spotting" from `player`, disables Boone, '
        'removes FollowerFaction and gives -100 karma. Policy: the perk and the companion belong to the recruiter (CompanionOwner), karma is '
        'shared. The initiator-picks model alone applies the perk removal to the wrong player.',
        [prep(p2=['CraigBooneREF.SetPlayerTeammate 1', 'player.addperk Spotting']), pk],
        ['VMS18'], setup_for(S, r), ['dialogue-ownership', 'companion-owner', 'karma-shared'],
        extra={'known_break': 'RemovePerk/SetPlayerTeammate are not captured (gaps.json COMPANION, PERK) and `player` resolves to the initiator'})

    # 6. Markland: split inventory
    r = S.info('133F16', '{Excited}Both of them?', flags=['say_once'], speaker='VBSLieutenantMarkland',
               begin='Player.RemoveItem VBSPediatricsBook 1')
    pk = S.pick('p1', '13304C', '133F16', '{Excited}Both of them?', objective_completed={'VMS40': [30, 50]},
                items_removed={'p1': ['VBSPediatricsBook', 'VBSPsychologyBook'], 'p2': []}, converged=True)
    out['dlg_act2_markland_split_books'] = S.scenario(
        'dlg_act2_markland_split_books', 'Markland: both books handed in (split inventory)',
        'p1 loots the Psychology book, p2 loots the Pediatrics book. GetItemCount conditions run on the initiator p1, so the "both books" line '
        'only exists if the quest items were duplicated to every player on pickup (OnAdd player scripts VBSPsychologyBookSCRIPT / '
        'VBSPediatricsBookSCRIPT also advance VMS40 objectives).',
        [prep(p1=['(runner resolves VBSPsychologyBook id) player.additem <id> 1'], p2=['(runner resolves VBSPediatricsBook id) player.additem <id> 1']), pk],
        ['VMS40'], setup_for(S, r), ['dialogue-ownership', 'quest-item-dup', 'handin-per-owner'],
        extra={'known_break': 'loot pickup is not a captured effect (gaps.json GUEST_EVENT: begin onadd player)'})

    # 7. Kimball: "let's rest" locks every player and advances the clock
    g = S.info('1446A2', "Good. The President doesn't arrive", flags=['goodbye'], speaker='VHDCaptainGraham', begin='DisablePlayerControls')
    t = S.pick('p2', '144687', '1446A2', "Good. The President doesn't arrive", spectator_lines=["Good. The President doesn't arrive"],
               controls_locked='all players', objective_completed={'VMQ03a': 10}, converged=True, closed_after='Goodbye')
    out['dlg_act2_kimball_rest_lock'] = S.scenario(
        'dlg_act2_kimball_rest_lock', 'Captain Graham: "Let\'s get some rest" (control lock for the whole party)',
        'p2 (client) answers Graham. The begin script runs DisablePlayerControls and completes VMQ03a 10; the end script starts VMQ03aTimer. '
        'Policy: the lock applies to every player (SetPlayerControls scope all, host-effect release) and the party must be tethered at Hoover Dam.',
        [prep(host=['(all players moved to HooverDam by PartyTether)']), t], ['VMQ03a'], setup_for(S, g), ['dialogue-ownership', 'cutscene-lock-all', 'tether'],
        extra={'known_gaps': ['DisablePlayerControls is not captured from scripts (gaps.json CONTROLS)']})

    # 8. Victor elevator: floor choice moves everyone in the car
    t, g = S.talk('p1', 'Lucky38VictorElevatorREF', 'Lucky38VictorElevator', '165103', 'What floor, pardner?', opened=True,
                  spectators=['host', 'p2'], party_warp='players within the car arrive at the chosen floor marker')
    out['dlg_act2_lucky38_elevator_floor'] = S.scenario(
        'dlg_act2_lucky38_elevator_floor', 'Victor elevator: "What floor, pardner?" (party warp)',
        'p1 activates the Lucky 38 elevator; the floor choice is made by p1 and player.MoveTo markers (Lucky38VictorElevatorScript) must move '
        'every player in the car to the same marker (PartyWarp), players outside the car stay.',
        [t], ['VDialogueLucky38Entrance'], setup_for(S, g), ['dialogue-ownership', 'teleport-scope'],
        extra={'known_gaps': ['player.MoveTo only moves the host player today (gaps.json TELEPORT)', 'floor menu is GetButtonPressed (MESSAGE_CHOICE)']})

    # 9. Crandon: "I'm a friend, trust me." Speech 45
    for tag, spk1, spkh, info, line in (('pass', 60, 10, '0F529B', '[SUCCEEDED]'), ('fail_host_high', 20, 90, '0F241B', '[FAILED]')):
        r = S.info(info, line, speaker='NorthVegasCrandon')
        pk = S.pick('p1', '0F240E', info, line, spectator_lines=[line], check_evaluated_on='p1', converged=True)
        out['dlg_act2_crandon_speech_' + tag] = S.scenario(
            'dlg_act2_crandon_speech_' + tag, 'Crandon "I\'m a friend, trust me." (%s)' % tag,
            'Speech 45 gate evaluated on the initiator p1 (Speech %d) not the host (Speech %d); consequences are shared.' % (spk1, spkh),
            [prep(p1=['player.forceav speech %d' % spk1], host=['player.forceav speech %d' % spkh], p2=['player.forceav speech 10']), pk],
            ['VDialogueVegasNorth'], setup_for(S, r), ['dialogue-ownership', 'speech-check-initiator'])
    return out
