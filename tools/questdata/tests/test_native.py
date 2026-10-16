"""effects_native.json.gz must be L7's native oracle format (plugin/snapshot/tools/check_stage.load_native)."""
import gzip
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

import effects  # noqa: E402

L7_TOOLS = os.path.abspath(os.path.join(HERE, '..', '..', '..', 'plugin', 'snapshot', 'tools'))
HAVE_L7 = os.path.exists(os.path.join(L7_TOOLS, 'check_stage.py'))

# fields L7's observer can emit per effect type (a native expectation may only use these)
OBSERVABLE_FIELDS = {
    'SetStage': {'quest', 'stage'}, 'SetObjective': {'quest', 'objective', 'flags'},
    'ClearObjective': {'quest', 'objective', 'flags'}, 'SetGlobal': {'global', 'value'}, 'EnableRef': {'ref'},
    'DisableRef': {'ref'}, 'KillActor': {'actor'}, 'ResurrectActor': {'actor'}, 'StartQuest': {'quest'},
    'StopQuest': {'quest'}, 'CompleteQuest': {'quest'}, 'FailQuest': {'quest'}, 'MoveRef': {'ref', 'cell'},
    'ModReputation': {'faction', 'fame', 'infamy', 'to'}, 'ModKarma': {'delta', 'to'},
    'AddItem': {'item', 'count', 'to'}, 'RemoveItem': {'item', 'count', 'to'}, 'ModActorValue': {'av', 'delta', 'to'},
    'SetFactionRank': {'faction', 'rank'}, 'AddPerk': {'perk', 'rank', 'to'}}


def validate_native(tc, nat):
    """Structural compatibility with L7's check_stage / effect_oracle conventions."""
    tc.assertEqual(nat['schema'], 1)
    for name, q in nat['quests'].items():
        tc.assertRegex(q['ref'], r'^[^:]+\.es[mp]:[0-9A-F]{6}$')
        for sk, ent in q['stages'].items():
            tc.assertTrue(sk.lstrip('-').isdigit(), sk)
            for bucket in ('unconditional', 'conditional'):
                for e in ent[bucket]:
                    tc.assertIn(e['type'], OBSERVABLE_FIELDS, e)
                    tc.assertLessEqual(set(e) - {'type'}, OBSERVABLE_FIELDS[e['type']], e)


def _d():
    q = {'ref': 'P.esm:000010', 'formid': '00000010', 'edid': 'Q', 'conditions': [], 'objectives': [],
         'stages': [{'index': 10, 'entries': [{'flags': ['complete_quest'], 'conditions': []}]},
                    {'index': 20, 'entries': [{'flags': [], 'conditions': []}, {'flags': [], 'conditions': []}]}]}
    return {'quests': [q]}


def _scripts():
    names = {'q': 'P.esm:000010', 'caps001': 'FalloutNV.esm:0000000F'[:-3] + '00F', 'npc': 'P.esm:000020',
             'fac': 'P.esm:000030'}
    names['caps001'] = 'FalloutNV.esm:00000F'
    ctx = effects.Ctx(names, {})
    src = ('SetObjectiveDisplayed Q 5 1\nSetObjectiveDisplayed Q 6 0\nplayer.AddItem Caps001 50\nNPC.AddItem Caps001 5\n'
           'RewardXP 100\nRewardKarma -2\nModReputation Fac 0 3\nplayer.AddToFaction Fac 1\nNPC.MoveTo Q\n'
           'if NPC.GetDead\nNPC.Disable\nendif\nSetStage Q nVar')
    eff, _ = effects.analyse(src, ctx)
    return [{'id': 'stage:P.esm:000010:10:0', 'kind': 'stage', 'effects': eff},
            {'id': 'stage:P.esm:000010:20:0', 'kind': 'stage',
             'effects': [{'type': 'StartQuest', 'quest': 'P.esm:000010', 'conditional': False, 'dynamic': False}]},
            {'id': 'info:P.esm:000040:end', 'kind': 'info_end', 'info': 'P.esm:000040', 'quest': 'P.esm:000010',
             'topic': None, 'effects': [{'type': 'KillActor', 'actor': 'P.esm:000020', 'conditional': False,
                                         'dynamic': False}]}]


class NativeFormatTests(unittest.TestCase):
    def test_conversion(self):
        nat = effects.to_native(_scripts(), _d())
        ent = nat['quests']['Q']['stages']['10']
        unc = ent['unconditional']
        self.assertIn({'type': 'SetObjective', 'quest': 'P.esm:000010', 'objective': 5, 'flags': 1}, unc)
        self.assertIn({'type': 'ClearObjective', 'quest': 'P.esm:000010', 'objective': 6, 'flags': 1}, unc)
        self.assertIn({'type': 'AddItem', 'item': 'FalloutNV.esm:00000F', 'count': 50}, unc)
        self.assertIn({'type': 'ModActorValue', 'av': 24, 'delta': 100}, unc)
        self.assertIn({'type': 'ModKarma', 'delta': -2, 'to': 'shared'}, unc)
        self.assertIn({'type': 'ModReputation', 'faction': 'P.esm:000030', 'infamy': 3, 'fame': 0, 'to': 'shared'},
                      ent['conditional'])  # type 0 = infamy; reputation stays conditional until L5 verifies deltas
        self.assertNotIn('ModReputation', [x['type'] for x in unc])
        self.assertIn({'type': 'SetFactionRank', 'faction': 'P.esm:000030', 'rank': 1}, unc)
        self.assertIn({'type': 'CompleteQuest', 'quest': 'P.esm:000010'}, unc)  # from the QSDT complete flag
        self.assertIn({'type': 'DisableRef', 'ref': 'P.esm:000020'}, ent['conditional'])
        self.assertIn({'type': 'SetStage', 'quest': 'P.esm:000010'}, ent['conditional'])  # SetStage Q nVar: stage unknown
        self.assertEqual(len(ent['unobservable']), 1)  # NPC.AddItem: not visible in a player snapshot
        s20 = nat['quests']['Q']['stages']['20']       # two log entries -> conditional
        self.assertEqual((s20['unconditional'], s20['conditional']), ([], [{'type': 'StartQuest', 'quest': 'P.esm:000010'}]))
        self.assertEqual(nat['infos']['P.esm:000040']['end']['unconditional'], [{'type': 'KillActor', 'actor': 'P.esm:000020'}])
        validate_native(self, nat)

    def _rep(self, typ, amt):
        names = {'fac': 'P.esm:000030'}
        eff, _ = effects.analyse(f'ModReputation Fac {typ} {amt}', effects.Ctx(names, {}))
        nat = effects.to_native([{'id': 'stage:P.esm:000010:10:0', 'kind': 'stage', 'effects': eff}], _d())
        return nat['quests']['Q']['stages']['10']

    def test_reputation_type_mapping_vanilla(self):
        # vanilla: type 1 = fame/positive, type 0 = infamy/negative; 2/3 unobservable; always conditional
        fame = self._rep(1, 5)
        self.assertEqual(fame['conditional'][0], {'type': 'ModReputation', 'faction': 'P.esm:000030', 'fame': 5,
                                                  'infamy': 0, 'to': 'shared'})
        self.assertEqual(self._rep(0, 5)['conditional'][0]['infamy'], 5)
        for typ in (2, 3):
            st = self._rep(typ, 1)
            self.assertEqual((st['conditional'], len(st['unobservable'])), ([], 1))
        self.assertNotIn('ModReputation', [x['type'] for x in self._rep(1, 5)['unconditional']])

    def test_mutation_inverted_reputation_mapping_is_caught(self):
        real = effects._conv

        def inverted(e):
            n, b = real(e)
            if n and n['type'] == 'ModReputation':
                n = dict(n, fame=n['infamy'], infamy=n['fame'])  # the old, wrong mapping
            return n, b
        with unittest.mock.patch.object(effects, '_conv', inverted):
            st = self._rep(1, 5)
        with self.assertRaises(AssertionError):
            self.assertEqual(st['conditional'][0]['fame'], 5)

    def test_av_codes_in_sync_with_l7(self):
        if not HAVE_L7:
            self.skipTest('L7 tools absent')
        sys.path.insert(0, L7_TOOLS)
        import effect_oracle
        self.assertEqual(effects.AV_CODES, {k.lower(): v for k, v in effect_oracle.AV_CODES.items()})

    def test_l7_loader_and_matcher_accept_output(self):
        if not HAVE_L7:
            self.skipTest('L7 tools absent')
        sys.path.insert(0, L7_TOOLS)
        import check_stage
        nat = effects.to_native(_scripts(), _d())
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'effects_native.json.gz')
            with gzip.open(p, 'wt', encoding='utf-8') as f:
                json.dump(nat, f)
            loaded = check_stage.load_native(p)
        exp = loaded['quests']['Q']['stages']['10']['unconditional']
        observed = {'type': 'SetObjective', 'quest': 'P.esm:000010', 'objective': 5, 'flags': 3}  # superset flags ok
        self.assertTrue(any(check_stage.matches(e, observed) for e in exp))
        self.assertFalse(any(check_stage.matches(e, dict(observed, objective=7)) for e in exp))
        add = {'type': 'AddItem', 'item': 'FalloutNV.esm:00000F', 'count': 50, 'to': 'p1'}  # `to` omitted = wildcard
        self.assertTrue(any(check_stage.matches(e, add) for e in exp))

    def test_mutation_malformed_native_rejected_by_l7_loader(self):
        if not HAVE_L7:
            self.skipTest('L7 tools absent')
        sys.path.insert(0, L7_TOOLS)
        import check_stage
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'bad.json')
            with open(p, 'w') as f:
                json.dump({'schema': 1, 'scenes': {}}, f)
            with self.assertRaises(check_stage.OracleError):
                check_stage.load_native(p)

    def test_mutation_unobservable_field_is_caught_by_validator(self):
        nat = effects.to_native(_scripts(), _d())
        nat['quests']['Q']['stages']['10']['unconditional'].append({'type': 'AddItem', 'item': 'x', 'count': 1, 'kind': 'bogus'})
        with self.assertRaises(AssertionError):
            validate_native(self, nat)


if __name__ == '__main__':
    unittest.main()
