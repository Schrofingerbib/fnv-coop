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


DATA = os.path.join(HERE, '..', 'data')


def _read(path):
    with open(path, encoding='utf8') as f:
        return f.read()


class WaveOneData(unittest.TestCase):
    """Gaps G05 (say-once, facts), G07 (MenuMode scripts), G08 (consent forks), G33 (follower rules): the generated tables are current and mean what the owner decisions say."""

    def test_follower_rules_are_current_and_cover_the_dlc_follower_quests(self):
        import gen_follower_rules as R
        lines, problems = R.generate()
        self.assertEqual(problems, [])
        self.assertEqual('\n'.join(lines) + '\n', _read(os.path.join(DATA, 'follower_rules.txt')))
        text = '\n'.join(lines)
        for q in ('NVDLC01Followers', 'NVDLC02FollowersQuest', 'VNPCFollowers'):
            self.assertIn(q, text)
        for c in ('Dean', 'Christine', 'Dog', 'FollowsChalk', 'WakingCloud', 'JoshuaGraham', 'Boone', 'EDE'):
            self.assertIn('companion %s ' % c, text)

    def test_twin_follower_var_name_that_the_script_lacks_is_rejected(self):
        import gen_follower_rules as R
        orig = list(R.QUESTS)
        try:
            q = orig[1]
            comps = [('Dean', 'humanoid', ['NVDLC01Dean'], 'bDeanHiredTYPO', 'bDeanFired', 'bDeanAvailable')] + list(q[3][1:])
            R.QUESTS[:] = [orig[0], (q[0], q[1], q[2], comps), orig[2]]
            _, problems = R.generate()
            self.assertTrue(any('bDeanHiredTYPO' in p for p in problems))
        finally:
            R.QUESTS[:] = orig

    def test_player_data_is_current_and_sayonce_is_shared_except_speech_xp_and_trainers(self):
        import gen_player_data as P
        rows, forms = P.load()
        so = P.sayonce(rows, forms)
        self.assertEqual({k for k, _ in so}, {'speech', 'trainer'})
        text = '\n'.join('%s %s' % x for x in so)
        self.assertIn('FalloutNV.esm:08F070', text)                       # Ranger Takedown trainer
        self.assertNotIn('FalloutNV.esm:1258AA', text)                     # an ordinary say-once greeting stays shared
        committed = [l for l in _read(os.path.join(DATA, 'sayonce_per_player.txt')).splitlines() if l and not l.startswith('#')]
        self.assertEqual(committed, ['%s %s' % x for x in so])
        facts = P.facts(rows, forms)
        committed_f = [l for l in _read(os.path.join(DATA, 'info_facts.txt')).splitlines() if l and not l.startswith('#')]
        self.assertEqual(committed_f, ['%s %s' % (r, ','.join(facts[r])) for r in sorted(facts)])

    def test_twin_trainer_rule_that_matches_everything_changes_the_table(self):
        import gen_player_data as P
        import re
        rows, forms = P.load()
        base = P.sayonce(rows, forms)
        orig = P.TRAINER
        try:
            P.TRAINER = re.compile('.')
            self.assertGreater(len(P.sayonce(rows, forms)), len(base))      # the committed file would be stale: the current-table test above catches it
        finally:
            P.TRAINER = orig

    def test_menumode_script_table_is_current(self):
        import gen_menumode_scripts as M
        import gzip, json
        scr = json.load(gzip.open(os.path.join(M.QD, 'scripts.json.gz'), 'rt'))
        want = sorted('%s:%s %s' % (s['ref'].rpartition(':')[0], s['ref'].rpartition(':')[2].upper(), s['edid'])
                      for s in scr if s.get('ref') and 1001 in {int(m.group(1)) for m in M.BLOCK.finditer((s.get('script') or {}).get('source') or '') if m.group(1)})
        got = [l for l in _read(os.path.join(DATA, 'menumode_scripts.txt')).splitlines() if l and not l.startswith('#')]
        self.assertEqual(got, want)
        self.assertTrue(any(l.endswith('FortCaesarScript') for l in got))     # Caesar's surgery polls the button in a MenuMode block

    def test_consent_forks_resolve_and_carry_the_owner_decision_modes(self):
        import gen_consent_messages as C
        rows, problems = C.scan_forks(C.find_data())
        self.assertEqual(problems, [])
        by = {r[2]: r for r in rows}
        self.assertEqual(by['FortCaesarComaMsg'][6], 'actor')                 # surgery: the activating player decides
        self.assertEqual(by['NVDLC04TempleNukeAllConfirmMSG'][6], 'vote')      # nuke: party vote, the host breaks ties
        self.assertEqual(by['INFO_010718'][6:8], ('vote', 'info'))             # Joshua or Daniel
        self.assertEqual(by['INFO_011C68'][6:8], ('vote', 'info'))             # Elijah's offer
        self.assertEqual(by['VMS42MicroclineMsg'][3], '*')
        text = _read(os.path.join(DATA, 'consent_messages.txt'))
        for r in rows:
            self.assertIn('%s:%06X %s ' % (r[0], r[1], r[2]), text)
        self.assertEqual(len([l for l in text.splitlines() if 'VEndGameWarningMsg' in l]), 5)   # the five end-game boxes are still unanimous rows

    def test_twin_fork_with_a_missing_form_aborts(self):
        import gen_consent_messages as C
        orig = list(C.FORKS)
        try:
            C.FORKS.append(('FalloutNV.esm', 'mesg', 'NoSuchMessageEver', 'vote', 1, 0, 'x'))
            _, problems = C.scan_forks(C.find_data())
            self.assertTrue(problems)
        finally:
            C.FORKS[:] = orig

    def test_response_layout_facts_match_the_game_exe(self):
        exe = os.path.join(HERE, '..', '..', '..', 'out', 'L5', 're', 'fnv_reference.exe')
        if not os.path.exists(exe):
            self.skipTest('no reference exe')
        import struct
        d = open(exe, 'rb').read()
        e = struct.unpack_from('<I', d, 0x3C)[0]
        nsec = struct.unpack_from('<H', d, e + 6)[0]
        opt = struct.unpack_from('<H', d, e + 20)[0]
        secs = []
        for i in range(nsec):
            o = e + 24 + opt + i * 40
            vs, va, rs, rp = struct.unpack_from('<IIII', d, o + 8)
            secs.append((va + 0x400000, max(vs, rs), rp))

        def rd(a, n):
            for va, sz, rp in secs:
                if va <= a < va + sz:
                    return d[rp + a - va: rp + a - va + n]
        # TESTopicInfo::GetResponses 0x61E780 (SEH frame, ret 4) and MarkSaid 0x61F220 (thiscall, plain ret)
        self.assertEqual(rd(0x61E780, 6).hex(), '558bec6aff68')
        self.assertEqual(rd(0x61F220, 4).hex(), '558bec51')
        # the INFO constructor clears +0x48 (mov dword ptr [eax+48h], 0) and never stores a response chain there: the chain is lazy
        ctor = rd(0x61CF40, 0x60)
        self.assertIn(bytes.fromhex('c74048') + b'\x00\x00\x00\x00', ctor)
        # a response node's constructor clears its `next` at +0x28 (mov dword ptr [edx+28h], 0): the chain is intrusive, headed by a cell
        self.assertIn(bytes.fromhex('c74228') + b'\x00\x00\x00\x00', rd(0x616CE0, 0x70))


if __name__ == '__main__':
    unittest.main(verbosity=1)
