import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classify  # noqa: E402
import lexicon  # noqa: E402


def top(src, **kw):
    f = classify.classify_source(src, **kw)
    return max((x['level'] for x in f), default=0), {x['cat'] for x in f}


def check(src, level, cat):
    """Assertion helper reused by the mutation twin."""
    lv, cats = top(src)
    assert lv == level and cat in cats, f'{src!r}: got level {lv} cats {cats}, want {level} {cat}'


CASES = [
    ('SetStage VMQ01 20', 1, 'Q-STATE'),
    ('player.AddItem Caps001 500', 1, 'REWARD'),
    ('player.RemoveItem Caps001 500', 2, 'P-INV'),
    ('RewardKarma 10', 1, 'S-STAT'),
    ('ModReputation NCRRep 1 2', 1, 'S-STAT'),
    ('player.ModAV Luck 1', 2, 'P-STAT'),
    ('EnablePlayerControls', 2, 'CTRL-RELEASE'),
    ('DisablePlayerControls', 4, 'CTRL'),
    ('player.SetPlayerTeammate 1', 3, 'COMPANION'),
    ('EnableFastTravel', 1, 'TRAVEL'),
    ('player.RemoveAllItems', 3, 'P-INV'),
    ('player.MoveTo DocMitchellHouseMarker', 4, 'MOVE'),
    ('MyNPC.MoveTo player', 2, 'MOVE'),
    ('ForceWeather WeatherClear', 1, 'WORLD'),
    ('MyNPC.AddItem Caps001 5', 1, 'ACTOR'),
    ('if GetDistance player < 100\nendif', 3, 'P-REF'),
    ('ShowMessage MyMsg', 2, 'CONV'),
    ('QuitGame', 4, 'SAVE'),
    ('short x\nset x to 1', 0, None),
]


class ClassifierTests(unittest.TestCase):
    def test_golden_cases(self):
        for src, lv, cat in CASES:
            with self.subTest(src=src):
                if cat is None:
                    self.assertEqual(top(src)[0], 0)
                else:
                    check(src, lv, cat)

    def test_comments_ignored(self):
        self.assertEqual(top('; player.AddItem Caps001 5\nSetStage Q 1 ; DisablePlayerControls')[0], 1)

    def test_string_semicolon_not_comment(self):
        self.assertEqual(top('ShowMessage "a;b" player')[0] >= 2, True)

    def test_player_alias_one_level(self):
        lv, cats = top('ref rP\nset rP to player\nrP.RemoveItem Caps001 5')
        self.assertEqual((lv, 'P-INV' in cats), (2, True))

    def test_global_and_questvar_assignment(self):
        f = classify.classify_source('set MyGlobal to 1\nset MyQuest.x to 2', {'myquest'}, {'myglobal'})
        self.assertEqual([x['cat'] for x in f], ['Q-STATE', 'Q-STATE'])

    def test_hex_player_ref(self):
        self.assertEqual(top('0x14.RemoveItem Caps001 1')[0] >= 2, True)

    def test_block_tracking(self):
        f = classify.classify_source('Begin MenuMode\nDisablePlayerControls\nEnd')
        self.assertEqual(f[0]['block'], 'MenuMode')

    def test_rollup_max_over_nodes(self):
        q = {'ref': 'P:1', 'plugin': 'P', 'edid': 'Q', 'name': 'q', 'conditions': [], 'objectives': [],
             'stages': [{'index': 10, 'entries': [{'flags': [], 'conditions': [],
                                                   'script': {'source': 'SetStage Q 20', 'refs': []}}]}]}
        info = {'ref': 'P:2', 'conditions': [], 'begin': None,
                'end': {'source': 'StartCinematic', 'refs': []}, 'flags': []}
        r = classify.rollup([q], {'P:1': [info]}, {'q'}, set())['P:1']
        self.assertEqual((r['tier'], r['has_cutscene'], r['infos']), ('T4', True, 1))
        r0 = classify.rollup([q], {}, {'q'}, set())['P:1']
        self.assertEqual(r0['tier'], 'T1')

    def test_dialogue_lock_rule(self):
        f = classify.classify_source('DisablePlayerControls', ctx='dialogue')
        self.assertEqual((f[0]['cat'], f[0]['level']), ('CTRL-DIALOG', 2))
        f = classify.classify_source('DisablePlayerControls')
        self.assertEqual((f[0]['cat'], f[0]['level']), ('CTRL', 4))
        f = classify.classify_source('ForceThirdPerson', ctx='dialogue')  # only the plain lock is generic
        self.assertEqual(f[0]['level'], 4)

    def test_mutation_dialogue_lock_rule_removed_is_caught(self):
        with mock.patch.object(classify, 'DIALOG_LOCK', set()):
            f = classify.classify_source('DisablePlayerControls', ctx='dialogue')
            with self.assertRaises(AssertionError):
                self.assertEqual(f[0]['level'], 2)

    def test_shared_policy_levels(self):
        # owner decisions: karma/rep shared (1), rewards routed to all (1); taking items stays per-player (2)
        self.assertEqual(top('player.AddItem Caps001 5')[0], 1)
        self.assertEqual(top('RewardXP 100')[0], 1)
        self.assertEqual(top('player.RemoveItem Caps001 5')[0], 2)

    def test_overrides_applied(self):
        q = {'ref': 'P:1', 'plugin': 'P', 'edid': 'Q', 'name': 'q', 'conditions': [], 'objectives': [],
             'stages': [{'index': 10, 'entries': [{'flags': [], 'conditions': [],
                                                   'script': {'source': 'SetStage Q 20', 'refs': []}}]}]}
        r = classify.rollup([q], {}, {'q'}, set(), overrides={'Q': {'tier': 'T4', 'notes': 'n'}})['P:1']
        self.assertEqual((r['tier'], r['tier_auto'], r['override_notes']), ('T4', 'T1', 'n'))
        # twin: ignoring overrides must be distinguishable
        r0 = classify.rollup([q], {}, {'q'}, set())['P:1']
        self.assertNotEqual(r0['tier'], r['tier'])

    def test_dependency_scripts_roll_up(self):
        q = {'ref': 'P:1', 'plugin': 'P', 'edid': 'Q', 'name': 'q', 'conditions': [], 'stages': [],
             'objectives': [{'index': 1, 'targets': [{'ref': 'P:REF', 'conditions': []}]}],
             'dep_refs': ['P:REF', 'P:TERM', 'P:PACK']}
        extra = {'forms': {'P:REF': {'sig': 'ACHR', 'base': 'P:NPC'}, 'P:NPC': {'sig': 'NPC_', 'edid': 'Bob', 'script': 'P:S1'}},
                 'scpt': {'P:S1': {'script': {'source': 'player.MoveTo Marker', 'refs': []}}},
                 'terms': {'P:TERM': {'items': [{'script': {'source': 'player.RemoveItem x 1', 'refs': []}}]}},
                 'packs': {'P:PACK': {'scripts': {'begin': {'source': 'SetStage Q 1', 'refs': []}}}}}
        r = classify.rollup([q], {}, {'q'}, set(), extra)['P:1']
        self.assertEqual((r['tier'], r['tier_direct'], r['dep_scripts']), ('T4', 'T0', 3))
        r0 = classify.rollup([q], {}, {'q'}, set(), {})['P:1']
        self.assertEqual((r0['tier'], r0['dep_scripts']), ('T0', 0))

    # ---- mutation twin: weakening the lexicon must be caught by the golden assertions ----
    def test_mutation_weakened_lexicon_is_caught(self):
        weak = dict(lexicon.L)
        weak['disableplayercontrols'] = ('CTRL', 0, 0)
        with mock.patch.object(classify, 'L', weak):
            with self.assertRaises(AssertionError):
                check('DisablePlayerControls', 4, 'CTRL')

    def test_mutation_player_blind_classifier_is_caught(self):
        with mock.patch.object(classify, 'PLAYER_NAMES', set(), create=True), \
                mock.patch.object(lexicon, 'PLAYER_NAMES', set()):
            # classify_source builds aliases from the module-level name imported at load; patch it too
            with mock.patch.object(classify, 'PLAYER_NAMES', set()):
                with self.assertRaises(AssertionError):
                    check('player.RemoveItem Caps001 500', 2, 'P-INV')


if __name__ == '__main__':
    unittest.main()
