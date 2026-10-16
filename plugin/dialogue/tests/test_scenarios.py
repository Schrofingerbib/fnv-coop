"""Tests for the M1 dialogue scenario specs (python -I plugin/dialogue/tests/test_scenarios.py)."""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', 'tools')
sys.path.insert(0, TOOLS)
import gen_m1_dialogue as G  # noqa: E402

SPEC_DIR = os.path.join(HERE, '..', 'scenarios')


class ScenarioSpecs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = G.build()

    def test_expected_scenarios_exist(self):
        for sid in ('dlg_doc_mitchell_word_test', 'dlg_sunny_accept', 'dlg_sunny_decline', 'dlg_trudy_speech_pass',
                    'dlg_trudy_speech_fail_host_high', 'dlg_cobb_side_with_powder_gangers',
                    'dlg_ringo_hostile_combat', 'dlg_initiator_disconnect'):
            self.assertIn(sid, self.specs)

    def test_committed_files_match_generator(self):
        for sid, spec in self.specs.items():
            with open(os.path.join(SPEC_DIR, sid + '.json')) as f:
                self.assertEqual(json.load(f), spec, sid + ' is stale: rerun gen_m1_dialogue.py')

    def test_schema(self):
        for sid, spec in self.specs.items():
            self.assertEqual(spec['schema'], 1)
            self.assertEqual(spec['kind'], 'dialogue')
            ids = [s['id'] for s in spec['steps']]
            self.assertEqual(len(ids), len(set(ids)), sid + ' duplicate step ids')
            self.assertTrue(any(s['action'] == 'talk' for s in spec['steps']))
            for s in spec['steps']:
                self.assertIn(s['action'], ('setup', 'talk', 'parallel', 'pick', 'pick_info', 'pick_nowait', 'leave', 'event', 'assert'))
                if s['action'] in ('talk', 'pick', 'pick_info'):
                    self.assertTrue(s.get('info') or s.get('greeting_info'))
            self.assertIn('no_duplicate_reward', spec['invariants'])

    def test_every_form_resolves(self):
        g = G.Gen()
        for sid, spec in self.specs.items():
            for s in spec['steps']:
                for key in ('info', 'greeting_info', 'topic'):
                    if key in s:
                        self.assertTrue(s[key] in g.infos or s[key] in g.dials, '%s %s %s' % (sid, key, s[key]))
                if 'npc_ref' in s:
                    self.assertIn(s['npc_ref'], g.forms)

    def test_speech_scenarios_use_different_initiator_and_host_skill(self):
        for sid, want in (('dlg_trudy_speech_pass', (30, 10)), ('dlg_trudy_speech_fail_host_high', (10, 100))):
            cmds = self.specs[sid]['steps'][0]['players']
            self.assertEqual(cmds['p1'], ['player.forceav speech %d' % want[0]])
            self.assertEqual(cmds['host'], ['player.forceav speech %d' % want[1]])

    # ---- mutation twins: a spec that disagrees with the game data must be refused
    def test_twin_wrong_line_text_is_rejected(self):
        with self.assertRaises(SystemExit):
            G.Gen().info('105583', 'Mouse.')

    def test_twin_wrong_speaker_is_rejected(self):
        with self.assertRaises(SystemExit):
            G.Gen().info('105583', 'House.', npc='104E84')  # Sunny, not Doc

    def test_twin_wrong_prompt_is_rejected(self):
        with self.assertRaises(SystemExit):
            G.Gen().pick('p1', '105560', '105583', 'House.', prompt='Dog.')

    def test_twin_missing_script_is_rejected(self):
        with self.assertRaises(SystemExit):
            G.Gen().info('15ED4D', end='SetStage VMS16b 99')

    def test_regeneration_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            sys.argv = ['x', '--out', d]
            G.main()
            for sid in self.specs:
                with open(os.path.join(d, sid + '.json')) as a, open(os.path.join(SPEC_DIR, sid + '.json')) as b:
                    self.assertEqual(a.read(), b.read())


class CompanionData(unittest.TestCase):
    def test_follower_var_table_matches_quest_script(self):
        import gen_companion_topics as C
        self.assertEqual(C.check_vars(), [])

    def test_owner_only_topics(self):
        import gen_companion_topics as C
        rows = {name: cls for cls, _, name in C.classify()}
        for n in ('Wait here.', "Let's trade equipment.", 'Stay close to me.', "It's time for us to part ways.",
                  "I'd like you to go back to the Lucky 38 for now."):
            self.assertEqual(rows.get(n), 'ownerOnly', n)
        self.assertEqual(rows.get('I think we should travel together.'), 'recruit')   # hiring is open to anyone
        self.assertNotIn('HealthQuarter', rows)                                         # barks are not topics

    def test_committed_topic_file_is_current(self):
        import gen_companion_topics as C, tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 't.txt')
            sys.argv = ['x', '--out', out]
            C.main()
            self.assertEqual(open(out).read(), open(os.path.join(HERE, '..', 'data', 'companion_topics.txt')).read())

    def test_twin_wrong_var_index_is_detected(self):
        import gen_companion_topics as C
        orig = C.HEADER
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, 'companions.h')
            open(bad, 'w').write(open(orig).read().replace('kHumanoidInParty = 47', 'kHumanoidInParty = 46'))
            C.HEADER = bad
            try:
                self.assertTrue(C.check_vars())
            finally:
                C.HEADER = orig


if __name__ == '__main__':
    unittest.main(verbosity=1)
