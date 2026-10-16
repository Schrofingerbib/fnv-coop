import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classify  # noqa: E402
import effects  # noqa: E402

Q, Q2, ITEM, NPC, MARK, GL = ('P.esm:%06X' % i for i in range(1, 7))
NAMES = {'q': Q, 'q2': Q2, 'caps001': ITEM, 'npc': NPC, 'marker': MARK}
GLOBALS = {'gl': GL}


def run(src):
    ctx = effects.Ctx(NAMES, GLOBALS)
    eff, unm = effects.analyse(src, ctx)
    return eff, unm, ctx


class AnalyseTests(unittest.TestCase):
    def test_basic_vocabulary(self):
        eff, unm, _ = run('SetStage Q 20\nSetObjectiveCompleted Q 10 1\nplayer.AddItem Caps001 500\n'
                          'NPC.Disable\nNPC.Enable\nNPC.MoveTo Marker\nStartQuest Q2\nStopQuest Q2\nset GL to 3\n'
                          'NPC.Kill\nNPC.Resurrect\nRewardKarma -5')
        self.assertEqual([e['type'] for e in eff], ['SetStage', 'SetObjective', 'AddItem', 'DisableRef', 'EnableRef',
                                                     'MoveRef', 'StartQuest', 'StopQuest', 'SetGlobal', 'KillActor',
                                                     'ResurrectActor', 'ModKarma'])
        self.assertEqual((eff[0]['quest'], eff[0]['stage']), (Q, 20))
        self.assertEqual((eff[1]['flags'], eff[1]['kind']), (2, 'completed'))
        self.assertEqual((eff[2]['item'], eff[2]['count'], eff[2]['to']), (ITEM, 500, effects.PLAYER_REF))
        self.assertEqual(eff[5]['dest'], MARK)
        self.assertEqual((eff[8]['global'], eff[8]['value']), (GL, 3))
        self.assertEqual(eff[11]['delta'], -5)
        self.assertTrue(all(not e['conditional'] and not e['dynamic'] for e in eff))

    def test_conditional_branches(self):
        eff, _, _ = run('SetStage Q 1\nif NPC.GetDead\n  SetStage Q 2\nelseif 1\n  SetStage Q 3\nelse\n  SetStage Q 4\n'
                        'endif\nSetStage Q 5')
        self.assertEqual([(e['stage'], e['conditional']) for e in eff],
                         [(1, False), (2, True), (3, True), (4, True), (5, False)])

    def test_return_inside_if_makes_rest_conditional(self):
        eff, _, _ = run('if NPC.GetDead\n  return\nendif\nSetStage Q 9')
        self.assertTrue(eff[0]['conditional'])
        eff, _, _ = run('SetStage Q 1\nreturn\nSetStage Q 2')
        self.assertEqual([e['stage'] for e in eff], [1])

    def test_dynamic_and_unresolved(self):
        eff, _, _ = run('SetStage Q nStage\nSetStage Nope 5\nrVar.Enable')
        self.assertTrue(all(e['dynamic'] for e in eff))
        self.assertIsNone(eff[1]['quest'])

    def test_unmodeled(self):
        eff, unm, _ = run('ShowMessage Msg\nSet Q.fTimer to 2\nNPC.AddScriptPackage Pk\nif x\nStartCombat NPC\nendif\n'
                          'short n\nset n to 2')
        self.assertEqual([(u['fn'], u['conditional']) for u in unm],
                         [('ShowMessage', False), ('SetQuestVar', False), ('AddScriptPackage', False), ('StartCombat', True)])
        self.assertEqual(eff, [])

    def test_comments_and_touched(self):
        eff, unm, ctx = run('; NPC.Disable\nNPC.Activate Marker ; x')
        self.assertEqual(eff, [])
        self.assertEqual(ctx.touched, {NPC, MARK})
        _, _, ctx = run('if NPC.GetDead\nendif\nSetStage Q 1')
        self.assertEqual(ctx.touched, set())  # conditions only read; quest args are not touched objects

    def test_oracle_subset_check_and_twin(self):
        eff, _, _ = run('SetStage Q 20\nplayer.AddItem Caps001 5\nif NPC.GetDead\nNPC.Disable\nendif')
        captured = [dict(e) for e in eff if e['type'] != 'DisableRef']  # conditional effect may be absent
        self.assertEqual(effects.oracle_missing(eff, captured), [])
        # twin: capture drops an unconditional effect -> must be reported
        self.assertEqual([e['type'] for e in effects.oracle_missing(eff, captured[:1])], ['AddItem'])

    def test_mutation_blind_conditional_detection_is_caught(self):
        src = 'if NPC.GetDead\nSetStage Q 2\nendif'
        real = classify.strip_comment

        def blind(line):  # drops `if` lines, so nesting depth never rises
            return '' if line.strip().lower().startswith('if') else real(line)
        with mock.patch.object(classify, 'strip_comment', blind):
            eff, _, _ = run(src)
        with self.assertRaises(AssertionError):
            self.assertTrue(eff[0]['conditional'])


class ReachabilityTests(unittest.TestCase):
    def _q(self):
        return {'ref': 'P:1', 'plugin': 'P', 'edid': 'Q', 'name': 'q', 'conditions': [], 'stages': [],
                'objectives': [], 'dep_refs': ['P:A', 'P:B']}

    def _extra(self, touched):
        return {'forms': {'P:A': {'sig': 'NPC_', 'edid': 'A', 'script': 'P:S1'},
                          'P:B': {'sig': 'NPC_', 'edid': 'B', 'script': 'P:S2'}},
                'scpt': {'P:S1': {'script': {'source': 'ShowMessage m', 'refs': []}},
                         'P:S2': {'script': {'source': 'player.MoveTo Marker', 'refs': []}}},
                'touched': touched}

    def test_untouched_dependency_not_charged(self):
        r = classify.rollup([self._q()], {}, {'q'}, set(), self._extra({'P:1': ['P:A']}))['P:1']
        self.assertEqual((r['tier'], r['tier_all_deps'], r['dep_scripts'], r['dep_scripts_untouched']),
                         ('T2', 'T4', 1, 1))

    def test_mutation_no_filter_charges_everything(self):
        ex = {k: v for k, v in self._extra({}).items() if k != 'touched'}
        r = classify.rollup([self._q()], {}, {'q'}, set(), ex)['P:1']
        self.assertEqual(r['tier'], 'T4')  # twin: without the touched map the T4 object script is charged


class FactionPolicyTests(unittest.TestCase):
    def test_faction_membership_is_shared_level_1(self):
        for src in ('player.AddToFaction NCRFaction 0', 'player.SetFactionRank NCRFaction 2',
                    'player.RemoveFromFaction NCRFaction'):
            f = classify.classify_source(src)
            self.assertEqual((f[0]['cat'], f[0]['level']), ('S-STAT', 1), src)

    def test_mutation_old_policy_would_fail(self):
        import lexicon
        old = dict(lexicon.L)
        old['addtofaction'] = ('P-STAT', 2, 2)
        with mock.patch.object(classify, 'L', old):
            f = classify.classify_source('player.AddToFaction NCRFaction 0')
        with self.assertRaises(AssertionError):
            self.assertEqual((f[0]['cat'], f[0]['level']), ('S-STAT', 1))


if __name__ == '__main__':
    unittest.main()
