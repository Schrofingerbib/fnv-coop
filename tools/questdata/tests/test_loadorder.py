import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

import loadorder  # noqa: E402


class LoadOrderTests(unittest.TestCase):
    def test_file_matches_agreed_order(self):
        # observed in-game 2026-10-16 (FNV sorts ESMs by file timestamp, not plugins.txt)
        self.assertEqual(loadorder.load(), ['FalloutNV.esm', 'DeadMoney.esm', 'HonestHearts.esm', 'OldWorldBlues.esm',
                                            'LonesomeRoad.esm', 'TribalPack.esm', 'MercenaryPack.esm', 'ClassicPack.esm',
                                            'CaravanPack.esm', 'GunRunnersArsenal.esm'])
        self.assertIn('observed_from', json.load(open(loadorder.PATH, encoding='utf-8')))

    def test_runtime_ids(self):
        self.assertEqual(loadorder.console_id('FalloutNV.esm:104C1C'), '104C1C')
        self.assertEqual(loadorder.console_id('FalloutNV.esm:10A214'), '10A214')
        self.assertEqual(loadorder.console_id('DeadMoney.esm:00ABCD'), '0100ABCD')
        self.assertEqual(loadorder.runtime_formid('lonesomeroad.esm:000123'), 0x04000123)
        self.assertEqual(loadorder.runtime_formid('CaravanPack.esm:000001'), 0x08000001)
        self.assertEqual(loadorder.runtime_formid('GunRunnersArsenal.esm:000001'), 0x09000001)
        self.assertEqual(loadorder.console_id('TribalPack.esm:000002'), '05000002')

    def test_mutation_unknown_plugin_and_reordered_list(self):
        with self.assertRaises(KeyError):
            loadorder.runtime_formid('Mod.esp:000001')
        swapped = ['FalloutNV.esm', 'HonestHearts.esm', 'DeadMoney.esm']
        self.assertNotEqual(loadorder.runtime_formid('DeadMoney.esm:000001', swapped),
                            loadorder.runtime_formid('DeadMoney.esm:000001'))


class VerifyTests(unittest.TestCase):
    def test_matching_and_prefix_pass(self):
        self.assertTrue(loadorder.verify(loadorder.load()))
        self.assertTrue(loadorder.verify(['falloutnv.esm', 'DeadMoney.esm']))

    def test_old_plugins_txt_order_is_rejected(self):
        old = ['FalloutNV.esm', 'DeadMoney.esm', 'HonestHearts.esm', 'OldWorldBlues.esm', 'LonesomeRoad.esm',
               'GunRunnersArsenal.esm', 'ClassicPack.esm', 'MercenaryPack.esm', 'TribalPack.esm', 'CaravanPack.esm']
        with self.assertRaises(loadorder.LoadOrderMismatch) as cm:
            loadorder.verify(old)
        self.assertIn('index 05', str(cm.exception))

    def test_extra_or_empty_rejected(self):
        with self.assertRaises(loadorder.LoadOrderMismatch):
            loadorder.verify(loadorder.load() + ['Mod.esp'])
        with self.assertRaises(loadorder.LoadOrderMismatch):
            loadorder.verify([])

    def test_mutation_verify_that_never_fails_is_caught(self):
        swapped = loadorder.load()
        swapped[5], swapped[9] = swapped[9], swapped[5]
        never = lambda observed, plugins=None: True  # noqa: E731  (broken twin)
        self.assertTrue(never(swapped))
        with self.assertRaises(loadorder.LoadOrderMismatch):
            loadorder.verify(swapped)


class ScenarioIdTests(unittest.TestCase):
    def test_steps_use_hex_formids_not_edids(self):
        import scenarios
        q = {'ref': 'DeadMoney.esm:00A001', 'edid': 'NVDLC01Test', 'formid': '0000A001', 'plugin': 'DeadMoney.esm',
             'conditions': [], 'objectives': [], 'flags': [],
             'stages': [{'index': 10, 'entries': [{'flags': [], 'conditions': [], 'text': 't'}]}]}
        rq = {'stage_findings': []}
        steps = scenarios.stage_steps(q, rq, 'NVDLC01Test')
        self.assertEqual((steps[0]['host'], steps[0]['wait']), ('setstage 0100A001 10', 'GetStageDone 0100A001 10'))
        self.assertEqual(steps[0]['host_edid'], 'setstage NVDLC01Test 10')
        for st in steps:
            self.assertNotIn('NVDLC01Test', st['host'])

    def test_generated_scenarios_have_no_edid_commands(self):
        out = os.path.join(HERE, '..', '..', '..', 'out', 'questdata', 'scenarios')
        if not os.path.isdir(out):
            self.skipTest('scenarios not generated')
        n = 0
        for fn in os.listdir(out):
            if fn.startswith('_') or not fn.endswith('.json'):
                continue
            sc = json.load(open(os.path.join(out, fn), encoding='utf-8'))
            for st in sc['steps']:
                n += 1
                self.assertRegex(st['host'].split()[1], r'^[0-9A-F]{6}([0-9A-F]{2})?$', (fn, st['host']))
                self.assertRegex(st['wait'].split()[1], r'^[0-9A-F]{6}([0-9A-F]{2})?$')
        self.assertTrue(n > 1500)


if __name__ == '__main__':
    unittest.main()
