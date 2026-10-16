#!/usr/bin/env python3
"""Generate the M1 Goodsprings dialogue scenario specs (L3 scenario JSON, schema 1, kind "dialogue").

Every FormID, prompt and line text in the output is looked up in out/questdata (the real ESM data) and the script
aborts if anything does not match, so the specs cannot drift from the game data.

Usage: python -I plugin/dialogue/tools/gen_m1_dialogue.py [--out plugin/dialogue/scenarios]

Step actions (the L2 runner maps them onto nvbridge `talk`/`exec`/`waitFor`/`events`; see docs/dialogue-design.md 10):
  setup      {cmds:[console cmds], players:{slot:[cmds]}}            run before the scenario on the named instances
  talk       {actor, npc, npc_ref, greeting_info}                   actor activates the NPC; expect OpenMenu or Denied
  parallel   {actor, npc_ref}                                       a second start against the same NPC
  pick       {actor, topic, prompt, info, line}                     actor selects the topic, plays the line to the end
  leave      {actor}                                                 initiator presses Esc
  event      {name, npc_ref}                                        host-side trigger (combat start, death)
  assert     {...}                                                   state checks without a dialogue action
"""
import argparse, gzip, json, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
QD = os.path.join(ROOT, 'out', 'questdata')
PLUGIN = 'FalloutNV.esm'


def load():
    forms = json.load(gzip.open(os.path.join(QD, 'forms.json.gz'), 'rt'))
    dials = {d['ref']: d for d in json.load(gzip.open(os.path.join(QD, 'dials.json.gz'), 'rt'))}
    infos = {}
    for line in gzip.open(os.path.join(QD, 'infos.jsonl.gz'), 'rt', encoding='utf8'):
        r = json.loads(line)
        infos[r['ref']] = r
    return forms, dials, infos


class Gen:
    def __init__(self):
        self.forms, self.dials, self.infos = load()

    def ref(self, local):
        return '%s:%s' % (PLUGIN, local.upper())

    def edid(self, local):
        return self.forms[self.ref(local)]['edid']

    def npc_ref(self, edid):
        for k, v in self.forms.items():
            if v.get('edid') == edid and v.get('sig') == 'ACHR':
                return k
        raise SystemExit('no ACHR %s' % edid)

    def info(self, local, text_prefix=None, flags=None, end=None, begin=None, npc=None, topic=None):
        r = self.infos.get(self.ref(local))
        if r is None:
            raise SystemExit('INFO %s not in data' % local)
        txt = r['responses'][0]['text'] if r['responses'] else ''
        if text_prefix is not None and not txt.startswith(text_prefix):
            raise SystemExit('INFO %s text %r does not start with %r' % (local, txt, text_prefix))
        for f in (flags or []):
            if f not in r['flags']:
                raise SystemExit('INFO %s missing flag %s' % (local, f))
        for key, want in (('end', end), ('begin', begin)):
            if want is not None:
                src = (r[key] or {}).get('source') or ''
                if want.lower() not in src.lower():
                    raise SystemExit('INFO %s %s script lacks %r: %r' % (local, key, want, src[:200]))
        if npc is not None:
            ids = [c['p1'] for c in r['conditions'] if c['func'] == 'GetIsID']
            if self.ref(npc) not in ids:
                raise SystemExit('INFO %s is not spoken by %s' % (local, npc))
        if topic is not None and r['topic'] != self.ref(topic):
            raise SystemExit('INFO %s not under topic %s' % (local, topic))
        return r

    def prompt(self, local):
        d = self.dials.get(self.ref(local))
        if d is None:
            raise SystemExit('DIAL %s not in data' % local)
        return d['name']

    def pick(self, actor, topic, info, line, prompt=None, **expect):
        d = self.dials[self.ref(topic)]
        if prompt is not None and d['name'] != prompt:
            raise SystemExit('DIAL %s prompt %r != %r' % (topic, d['name'], prompt))
        self.info(info, line, topic=topic)
        return {'id': 'pick_%s_%s' % (actor, topic.lower()), 'action': 'pick', 'actor': actor,
                'topic': self.ref(topic), 'topic_edid': d['edid'], 'prompt': d['name'], 'info': self.ref(info),
                'line': line, 'expect': dict(expect)}

    def talk(self, actor, npc_edid, npc_base_edid, greeting_info, line, **expect):
        r = self.info(greeting_info, line)
        base = [k for k, v in self.forms.items() if v.get('edid') == npc_base_edid and v.get('sig') == 'NPC_']
        if not base or base[0] not in [c['p1'] for c in r['conditions'] if c['func'] == 'GetIsID']:
            raise SystemExit('greeting %s is not conditioned on %s' % (greeting_info, npc_base_edid))
        return {'id': 'talk_%s_%s' % (actor, npc_edid.lower()), 'action': 'talk', 'actor': actor, 'npc': npc_edid,
                'npc_ref': self.npc_ref(npc_edid), 'greeting_info': self.ref(greeting_info), 'line': line,
                'expect': dict(expect)}


def scenario(sid, name, summary, steps, setup=None, quests=(), players=3, saves=None, policy=None):
    return {'schema': 1, 'kind': 'dialogue', 'id': sid, 'name': name, 'plugin': PLUGIN, 'summary': summary,
            'tier': 'T2', 'players': {'host': 0, 'p1': 1, 'p2': 2} if players == 3 else {'host': 0, 'p1': 1},
            'quests': list(quests), 'save': saves or 'm1_goodsprings_start',
            'policy_checks': policy or ['dialogue-ownership', 'player-reference-mapping', 'reward-both-vs-owner'],
            'setup': setup or {}, 'steps': steps,
            'invariants': ['no_duplicate_reward', 'one_conversation_per_npc', 'result_script_ran_once_on_host',
                           'host.shared == client.shared'],
            'oracle': 'docs/dialogue-design.md'}


def build():
    g = Gen()
    out = {}

    # ---- 1. Doc Mitchell word-association test, client initiator, parallel start rejected, spectators see lines
    s = [
        g.talk('p1', 'DocMitchellREF', 'DocMitchell', '10558F', 'All right. I',
               opened=True, spectators=['host', 'p2'], lock='DocMitchellREF'),
        {'id': 'race_p2', 'action': 'parallel', 'actor': 'p2', 'npc_ref': g.npc_ref('DocMitchellREF'),
         'expect': {'denied': 'Busy', 'owner': 'p1', 'menu_opened': False}},
        g.pick('p1', '105560', '105583', 'House.', prompt='Cat.',
               spectator_lines=['House.'], script_runs={'begin': 1, 'end': 0},
               script_var={'VCG01Test.nSneak': 1}, converged=True),
        g.pick('p1', '10555A', '10559C', 'Night.', prompt='Shelter.',
               spectator_lines=['Night.'], script_var={'VCG01Test.nSurvival': 1}, converged=True),
        {'id': 'leave_p1', 'action': 'leave', 'actor': 'p1',
         'expect': {'closed': 'InitiatorLeft', 'lock_released': 'DocMitchellREF', 'spectator_menu_closed': True}},
        g.talk('p2', 'DocMitchellREF', 'DocMitchell', '10558F', 'All right. I', opened=True),
    ]
    out['dlg_doc_mitchell_word_test'] = scenario(
        'dlg_doc_mitchell_word_test', 'Doc Mitchell word-association test (client initiator)',
        'Client p1 talks to Doc Mitchell; host and p2 see each line; p2 cannot start a parallel conversation; begin '
        'scripts that only set VCG01Test.n* vars replicate to every machine (SetScriptVar effect).', s,
        setup={'cmds': ['coc GSDocMitchellHouse'], 'note': 'new-game start: VCG01 running, all players in the surgery'},
        quests=['VCG01'])

    # ---- 2. Sunny Smiles: accept vs decline (initiator decides, everybody sees the result)
    for tag, topic, info, line, extra in (
            ('accept', '10A1DE', '10A1E5', 'Follow me.', {}),
            ('decline', '10A1DD', '10A1E2', 'Suit yourself.', {})):
        g.info('10A1EC', 'Well, that', npc='104E84', end='bShootingTutorialActive')
        s = [
            {'id': 'prep', 'action': 'setup', 'cmds': ['SetObjectiveCompleted VCG02 10 1']},
            g.talk('p2', 'SunnyRef', 'GSSunnySmiles', '10A1EC', 'Well, that', opened=True, spectators=['host', 'p1']),
            g.pick('p2', topic, info, line,
                   spectator_lines=[line], converged=True,
                   closed_after='Goodbye' if 'goodbye' in g.infos[g.ref(info)]['flags'] else None),
        ]
        out['dlg_sunny_' + tag] = scenario(
            'dlg_sunny_' + tag, 'Sunny Smiles gecko hunt: %s' % tag,
            'Initiator p2 (a client, not the quest owner) answers Sunny; the choice and the end script '
            '(VCG02.bShootingTutorialActive=0) are visible on every machine.', s, quests=['VCG02'])

    # ---- 3. Trudy: argument explanation + Speech 25 check evaluated against the INITIATOR only
    base_steps = [
        g.talk('p1', 'TrudyRef', 'GSTrudy', '104C5B', 'Well, you', opened=True, spectators=['host', 'p2']),
        g.pick('p1', '104C37', '104C54', 'It looks like our little town', prompt=None,
               spectator_lines=['It looks like our little town'],
               stage={'VMS16': 5}, script_var={'VFreeformGoodsprings.bTrudyExplainedArgument': 1}, converged=True),
    ]
    for tag, spk_p1, spk_host, info, line, expect in (
            ('speech_pass', 30, 10, '15D4E9', '[SUCCEEDED]',
             {'objective_completed': {'VMS16': 20}, 'script_var': {'VMS16.bTrudyHelp': 1},
              'xp_each_player': 25}),
            ('speech_fail_host_high', 10, 100, '15D4EA', '[FAILED]', {'objective_completed_not': {'VMS16': 20}})):
        g.info(info, line)
        steps = [{'id': 'prep', 'action': 'setup', 'players': {
            'p1': ['player.forceav speech %d' % spk_p1], 'host': ['player.forceav speech %d' % spk_host],
            'p2': ['player.forceav speech 10']}}]
        steps += [dict(x) for x in base_steps]
        steps.append(g.pick('p1', '15D4E8', info, line, prompt='< Speech 25 >',
                            spectator_lines=[line], converged=True, check_evaluated_on='p1', **expect))
        out['dlg_trudy_' + tag] = scenario(
            'dlg_trudy_' + tag, 'Trudy "< Speech 25 >" (%s)' % tag,
            'The initiator p1 has Speech %d, the host has Speech %d: the option exists/succeeds only per p1. '
            'Quest consequences and XP are shared by every player.' % (spk_p1, spk_host), steps,
            quests=['VMS16', 'VFreeformGoodsprings'],
            policy=['dialogue-ownership', 'player-reference-mapping', 'reward-both-vs-owner', 'speech-check-initiator'])

    # ---- 4. Cobb vs Ringo: the faction-defining choice, shared reputation
    cobb = [
        g.talk('p1', 'GSJoeCobbRef', 'GSJoeCobb', '104C59', 'What do you want?', opened=True, spectators=['host', 'p2']),
        g.pick('p1', '15ED41', '15ED4D', "Good. We'll still need a plan", prompt="Ringo's dead. What now?",
               stage={'VMS16b': 30}, objective_completed={'VMS16b': 15},
               reputation_shared={'RepNVPowderGanger': 'identical on all players'}, converged=True,
               closed_after='Goodbye'),
    ]
    out['dlg_cobb_side_with_powder_gangers'] = scenario(
        'dlg_cobb_side_with_powder_gangers', 'Cobb: "Ringo\'s dead" (Powder Gangers branch)',
        'End script AddReputation RepNVPowderGanger / SetStage VMS16b 30 runs once on the host and every player '
        'ends with the same reputation (shared) and stage.', cobb,
        setup={'cmds': ['SetStage VMS16b 10', 'player.kill GSRingo'], 'note': 'objective VMS16b 15 displayed'},
        quests=['VMS16b'])
    ringo = [
        g.talk('p2', 'RingoRef', 'GSRingo', '104C5F', "That's close enough", opened=True, spectators=['host', 'p1']),
        {'id': 'ringo_turns_hostile', 'action': 'pick_info', 'actor': 'p2', 'info': g.ref('106A77'),
         'topic': g.ref('106A70'), 'line': 'What? No!',
         'expect': {'script': 'StartCombat player', 'closed_after': 'Goodbye'}},
        {'id': 'combat_mid_dialogue', 'action': 'event', 'name': 'npc_combat', 'npc_ref': g.npc_ref('RingoRef'),
         'expect': {'closed': 'Combat', 'lock_released': 'RingoRef', 'spectator_menu_closed': True}},
    ]
    g.info('106A77', 'What? No!', flags=['goodbye'], end=None, npc='104C7C')
    out['dlg_ringo_hostile_combat'] = scenario(
        'dlg_ringo_hostile_combat', 'Ringo turns hostile: combat starts mid/after dialogue',
        'Choosing "I\'ve decided to kill you for Joe Cobb" ends with StartCombat: the conversation closes cleanly for '
        'initiator and spectators, the NPC lock is released, nothing is left locked.', ringo, quests=['VMS16'],
        setup={'cmds': ['SetStage VMS16 7'], 'note': 'GetQuestVariable VMS16 7 == 1 enables the choice'})

    # ---- 5. initiator drops mid-line
    drop = [
        g.talk('p1', 'DocMitchellREF', 'DocMitchell', '10558F', 'All right. I', opened=True),
        {'id': 'start_line', 'action': 'pick_nowait', 'actor': 'p1', 'topic': g.ref('105560'), 'info': g.ref('105583'),
         'line': 'House.'},
        {'id': 'kill_p1', 'action': 'event', 'name': 'disconnect', 'actor': 'p1',
         'expect': {'closed': 'Disconnect', 'end_script_ran': g.ref('105583'), 'lock_released': 'DocMitchellREF',
                    'spectator_menu_closed': True}},
        g.talk('host', 'DocMitchellREF', 'DocMitchell', '10558F', 'All right. I', opened=True),
    ]
    out['dlg_initiator_disconnect'] = scenario(
        'dlg_initiator_disconnect', 'Initiator disconnects while the NPC is speaking',
        'Begin script already ran, so the end script is flushed, spectators get Closed(Disconnect) and the NPC is '
        'immediately usable by someone else.', drop, quests=['VCG01'])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(ROOT, 'plugin', 'dialogue', 'scenarios'))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    specs = build()
    index = []
    for sid, spec in sorted(specs.items()):
        with open(os.path.join(a.out, sid + '.json'), 'w', newline='\n') as f:
            json.dump(spec, f, indent=1)
            f.write('\n')
        index.append({'id': sid, 'name': spec['name'], 'steps': len(spec['steps']), 'quests': spec['quests']})
    with open(os.path.join(a.out, '_index.json'), 'w', newline='\n') as f:
        json.dump({'schema': 1, 'kind': 'dialogue', 'scenarios': index}, f, indent=1)
        f.write('\n')
    print('wrote %d dialogue scenarios to %s' % (len(specs), a.out))


if __name__ == '__main__':
    main()
