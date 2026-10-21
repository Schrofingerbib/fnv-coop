"""Tests for the reviewed-evidence layer of live_status (tools/coverage/evidence.json): synthetic repos, plus the shipped file against the real source.

Twins: each `test_twin_*` flips one behaviour of the layer (a deleted token, a function that is not in the op table, a missing local token) and the
matching assertion must then fail; they pass here because they assert the CORRECT reaction to the broken input.
"""
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import live_status as LS  # noqa: E402

EFFECT_H = "struct SetGhostX {}; using Effect = std::variant<SetGhostX>;\n"
WORLD_H = "struct SetEssential {}; struct SetRestrained {};\nusing WorldEffect = std::variant<SetEssential,\n   SetRestrained>;\n"
CAPTURE_CPP = "void h() { emitLocal(SetEssential{ar, true, false}); }\nHK(Cmd_SetGhost, h_SetGhost, o_SetGhost, true);\nstatic void record_rc() { g_hub->record(rc); }\n"
WORLD_PLAN_CPP = "if (auto* x = std::get_if<SetEssential>(&e)) return onRef(x->actor, line);\n"
APPLY_CPP = "ApplyStatus RemoteApplier::applyRefCommand() { if (auto* x = std::get_if<RefCommand>(&e)) {} }\n"
REF_OPS_CPP = '''const RefOpSpec kStorySpecs[] = {
    {fnvcoop::kOpSetGhost, "setghost", S_(Cmd_SetGhost), "i", "i", 1, REF},
    {fnvcoop::kOpStartCombat, "startcombat", E_(Cmd_StartCombat), "o", "a", 1, REF | AI | SHARED},
};
'''


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)


def evidence(groups):
    return json.dumps({'schema': 1, 'groups': groups})


def row(fn, effect=None, static='partial', cap='player only', app='player only', count=10):
    return {'fn': fn, 'count': count, 'scripts': 1, 'effect': effect, 'l5_capture': cap, 'l5_apply': app, 'l5_status': static,
            'severity': 'high', 'hazards': [], 'note': ''}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        write(r, 'synccore/include/fnvcoop/effect.h', EFFECT_H)
        write(r, 'plugin/hooks_world/world_effects.h', WORLD_H)
        write(r, 'plugin/hooks/engine_hooks.cpp', CAPTURE_CPP)
        write(r, 'plugin/hooks_world/world_plan.cpp', WORLD_PLAN_CPP)
        write(r, 'plugin/hooks/apply_remote.cpp', APPLY_CPP)
        write(r, 'plugin/hooks/ref_ops.cpp', REF_OPS_CPP)

    def tearDown(self):
        self.tmp.cleanup()

    def src(self, groups):
        write(self.tmp.name, 'tools/coverage/evidence.json', evidence(groups))
        return LS.scan_sources(self.tmp.name)

    def status(self, src, r):
        return LS.live_status(r, src)['status']

    GROUP_TOKENS = {'fns': ['setfoo'], 'capture': 'c', 'apply': 'a',
                    'capture_tokens': [['plugin/hooks/engine_hooks.cpp', r'g_hub->record\(rc\)']],
                    'apply_tokens': [['plugin/hooks/apply_remote.cpp', r'applyRefCommand']]}

    def test_world_effect_types_are_scanned_like_effect_types(self):
        src = self.src([])
        self.assertEqual(src['types'], ['SetGhostX', 'SetEssential', 'SetRestrained'])
        self.assertIn('SetEssential', src['captured'])
        self.assertIn('SetEssential', src['applied'])
        self.assertNotIn('SetRestrained', src['captured'])

    def test_reviewed_tokens_replace_a_stale_qualifier(self):
        src = self.src([self.GROUP_TOKENS])
        r = row('setfoo', None, 'partial', 'player only', 'player only')
        self.assertEqual(self.status(src, r), 'supported')
        self.assertEqual(self.status(self.src([]), r), 'partial')            # without the reviewed entry the qualifier text keeps it partial
        self.assertTrue(LS.live_status(r, src)['changed'])

    def test_twin_deleted_token_drops_the_function_back(self):
        src = self.src([self.GROUP_TOKENS])
        write(self.tmp.name, 'plugin/hooks/apply_remote.cpp', '// the apply branch was removed\n')
        self.assertEqual(self.status(src, row('setfoo')), 'partial')         # capture still there, apply gone
        write(self.tmp.name, 'plugin/hooks/engine_hooks.cpp', '// the hook was removed\n')
        self.assertEqual(self.status(src, row('setfoo', static='missing')), 'missing')

    def test_effect_types_are_still_checked_when_named(self):
        g = {'fns': ['setess'], 'effect': 'SetEssential', 'capture': 'c', 'apply': 'a'}
        src = self.src([g])
        self.assertEqual(self.status(src, row('setess')), 'supported')
        g2 = {'fns': ['setrest'], 'effect': 'SetRestrained', 'capture': 'c', 'apply': 'a'}      # a type nobody captures or applies
        src2 = self.src([g2])
        self.assertEqual(self.status(src2, row('setrest', static='missing')), 'missing')

    def test_ref_command_functions_must_have_a_spec_row(self):
        g = {'fns': ['setghost', 'startcombat', 'nosuchop'], 'effect': 'RefCommand', 'capture': 'c', 'apply': 'a',
             'capture_tokens': [['plugin/hooks/engine_hooks.cpp', r'g_hub->record\(rc\)']],
             'apply_tokens': [['plugin/hooks/apply_remote.cpp', r'applyRefCommand']]}
        src = self.src([g])
        self.assertEqual(sorted(src['refcmd_fns']), ['setghost', 'startcombat'])
        self.assertEqual(self.status(src, row('setghost')), 'supported')
        self.assertEqual(self.status(src, row('startcombat')), 'supported')       # E_() rows (shared handlers) count too
        self.assertNotEqual(self.status(src, row('nosuchop', static='missing')), 'supported')

    def test_local_classification_needs_its_token(self):
        g = {'fns': ['swaptexture'], 'status': 'local', 'why': 'player-local screen',
             'local_tokens': [['plugin/hooks/engine_hooks.cpp', r'Cmd_SetGhost']]}
        src = self.src([g])
        self.assertEqual(self.status(src, row('swaptexture', static='partial')), 'local')
        write(self.tmp.name, 'plugin/hooks/engine_hooks.cpp', '// gone\n')
        self.assertEqual(self.status(src, row('swaptexture', static='partial')), 'partial')

    def test_an_entry_without_checkable_evidence_proves_nothing(self):
        src = self.src([{'fns': ['setbar'], 'capture': 'looks fine', 'apply': 'trust me'}])
        self.assertEqual(self.status(src, row('setbar', static='missing')), 'missing')

    def test_backlog_counts_follow(self):
        import backlog as BL
        src = self.src([self.GROUP_TOKENS])
        b = BL.build_backlog({'rows': [row('setfoo', count=30), row('other', count=10, static='missing', cap='NOT hooked', app='n/a')]}, {}, src)
        self.assertEqual(b['game_wide']['calls']['supported'], 30)
        self.assertEqual(b['game_wide']['synced_pct'], 75.0)


class ShippedEvidenceTests(unittest.TestCase):
    """The shipped evidence.json against the real source tree: every token must match NOW, so the file cannot go stale unnoticed."""

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(LS.ROOT, LS.EVIDENCE_FILE)

    @unittest.skipUnless(os.path.exists(os.path.join(LS.ROOT, LS.EVIDENCE_FILE)), 'no evidence.json')
    def test_every_token_matches_the_current_source(self):
        data = json.load(open(self.path, encoding='utf-8'))
        seen = set()
        for g in data['groups']:
            self.assertTrue(g['fns'], g)
            for fn in g['fns']:
                self.assertEqual(fn, fn.lower())
                self.assertNotIn(fn, seen, 'function listed in two groups: ' + fn)
                seen.add(fn)
            if g.get('status') == 'local':
                self.assertTrue(g.get('why') and g.get('local_tokens'), g['fns'])
                toks = g['local_tokens']
            else:
                self.assertTrue(g.get('capture') and g.get('apply'), g['fns'])
                self.assertTrue(g.get('effect') or (g.get('capture_tokens') and g.get('apply_tokens')), g['fns'])
                toks = g.get('capture_tokens', []) + g.get('apply_tokens', [])
            for f, rx in toks:
                re.compile(rx)
                if f.startswith('questlines/') and not os.path.exists(os.path.join(LS.ROOT, f)):
                    continue   # generated from the game's own data and not in the public repository (census, sweeps): nothing to check
                self.assertTrue(LS.token_ok(LS.ROOT, f, rx), '%s: token %r not found in %s' % (g['fns'][:2], rx, f))

    @unittest.skipUnless(os.path.exists(os.path.join(LS.ROOT, LS.EVIDENCE_FILE)), 'no evidence.json')
    def test_ref_command_functions_all_have_spec_rows(self):
        data = json.load(open(self.path, encoding='utf-8'))
        table = LS.ref_command_fns(LS.ROOT)
        self.assertGreater(len(table), 60)
        for g in data['groups']:
            if g.get('effect') == 'RefCommand':
                for fn in g['fns']:
                    self.assertIn(fn, table, fn)
        listed = {fn for g in data['groups'] if g.get('effect') == 'RefCommand' for fn in g['fns']}
        self.assertEqual(sorted(table - listed), [], 'ops with a spec row but no evidence entry')


if __name__ == '__main__':
    unittest.main()
