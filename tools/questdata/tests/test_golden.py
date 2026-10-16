"""Golden checks against the real game files (skipped when the Steam install is absent)."""
import gzip
import json
import os
import struct
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from esm import EsmFile, ParseError, split_subrecords  # noqa: E402
import extract  # noqa: E402
import classify  # noqa: E402
import scenarios  # noqa: E402
import effects  # noqa: E402

DATA = extract.DEFAULT_DATA
FNV = os.path.join(DATA, 'FalloutNV.esm')
HAVE = os.path.exists(FNV)


@unittest.skipUnless(HAVE, 'game files not installed')
class GoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name, cls.masters, cls.out, cls.stats = extract.extract_plugin(FNV)
        cls.quests = {q['edid']: q for q in cls.out['QUST'] if q.get('edid')}

    def test_framing_matches_hedr_for_every_plugin(self):
        for p in extract.PLUGINS:
            path = os.path.join(DATA, p)
            if not os.path.exists(path):
                continue
            e = EsmFile.open(path)
            try:
                recs, grps = e.stats()
                self.assertEqual(recs + grps, e.record_count, p)
            finally:
                e.close()

    def test_base_counts_stable(self):
        self.assertEqual(self.stats['QUST']['new'], 436)
        self.assertEqual(sum(len(q['stages']) for q in self.out['QUST']), 1028)
        self.assertEqual(self.stats['INFO']['records'], 23247)
        self.assertEqual(self.stats['SCPT']['records'], 2576)

    def test_vcg01_kick_in_the_head(self):
        q = self.quests['VCG01']
        self.assertEqual(q['name'], "Ain't That a Kick in the Head")
        idx = [s['index'] for s in q['stages']]
        self.assertEqual(idx, sorted(idx))
        for want in (0, 10, 100, 200):
            self.assertIn(want, idx)
        self.assertEqual(len(idx), 38)
        self.assertEqual(len(q['objectives']), 4)
        self.assertTrue(q['script'])

    def test_goodsprings_quests_present(self):
        names = {q.get('name') for q in self.quests.values()}
        for n in ("Ain't That a Kick in the Head", 'Back in the Saddle', 'By a Campfire on the Trail',
                  'They Went That-a-Way'):
            self.assertIn(n, names)
        self.assertEqual([s['index'] for s in self.quests['VMQ01']['stages']], [10, 15, 40, 50, 60, 100])

    def test_vms01_stage_walk(self):
        q = self.quests['VMS01']
        self.assertEqual([s['index'] for s in q['stages']], [10, 15, 20, 30, 40, 50, 90, 100, 101, 102])
        self.assertTrue(any(en.get('script') for s in q['stages'] for en in s['entries']))

    def test_every_stage_script_has_source(self):
        n = extract.script_counts(self.out)
        self.assertEqual(n['stage_scripts'], n['stage_scripts_with_source'])

    def test_classifier_calibration(self):
        kq = {q.lower() for q in self.quests}
        infos = {}
        for i in self.out['INFO']:
            infos.setdefault(i.get('quest'), []).append(i)
        scpt = {s['ref']: s for s in self.out['SCPT']}
        qs = [q for q in self.out['QUST'] if q.get('edid') in ('VCG01', 'VMS01')]
        for q in qs:
            if q.get('script') in scpt:
                q['script_data'] = scpt[q['script']]['script']
        r = classify.rollup(qs, infos, kq, set())
        by = {v['edid']: v for v in r.values()}
        self.assertEqual(by['VCG01']['tier'], 'T4')  # prologue: control lock / player teleport / cutscene
        self.assertTrue(by['VCG01']['has_cutscene'])
        self.assertLessEqual(by['VMS01']['max_level'], 3)

    def test_scenario_covers_every_stage_in_order(self):
        q = self.quests['VMS01']
        rq = classify.rollup([q], {}, {'vms01'}, set())[q['ref']]
        steps = scenarios.stage_steps(q, rq, 'VMS01')
        self.assertEqual([s['host'] for s in steps], [f'setstage 080664 {i}' for i in (10, 15, 20, 30, 40, 50, 90, 100, 101, 102)])
        self.assertEqual(steps[0]['host_edid'], 'setstage VMS01 10')
        # twin: a spec missing a stage must not satisfy the coverage assertion
        broken = steps[:3] + steps[4:]
        with self.assertRaises(AssertionError):
            self.assertEqual([s['host'] for s in broken], [s['host'] for s in steps])

    def test_mutation_corrupt_real_subrecord_size(self):
        e = EsmFile.open(FNV)
        try:
            rec = next(r for r in e.records({'QUST'}) if r.formid == int(self.quests['VCG01']['formid'], 16))
            d = bytearray(rec.data())
            struct.pack_into('<H', d, 4, 0xFFF0)  # first subrecord (EDID) claims 65k bytes
            with self.assertRaises(ParseError):
                split_subrecords(bytes(d))
        finally:
            e.close()


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(HAVE, 'game files not installed')
class M1Tests(unittest.TestCase):
    """Runs the whole pipeline into a temp dir (about 15 s) and checks the M1 Goodsprings target list."""

    @classmethod
    def setUpClass(cls):
        import contextlib
        import io
        import json
        import tempfile
        import m1
        cls.m1 = m1
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        with contextlib.redirect_stdout(io.StringIO()):
            extract.main(['--out', t])
            effects.main(['--in', t, '--out', t])
            classify.main(['--in', t, '--out', t])
            m1.main(['--in', t, '--out', t])
        cls.d = json.load(open(os.path.join(t, 'm1_goodsprings.json'), encoding='utf-8'))
        cls.risk = json.load(open(os.path.join(t, 'risk.json'), encoding='utf-8'))['quests']

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_core_quests_and_counts(self):
        q = {x['edid']: x for x in self.d['quests']}
        for k in ('VCG01', 'VCG02', 'VCG03', 'VMS16'):
            self.assertEqual(q[k]['role'], 'core')
        self.assertEqual([len(q[k]['stages']) for k in ('VCG01', 'VCG02', 'VCG03', 'VMS16')], [38, 10, 2, 10])
        self.assertEqual(q['VMS16']['name'], 'Ghost Town Gunfight')

    def test_dependency_set_contents(self):
        s = self.d['summary']
        names = {n.get('name') for n in s['npcs']}
        for n in ('Doc Mitchell', 'Trudy', 'Sunny Smiles', 'Joe Cobb', 'Ringo', 'Chet', 'Easy Pete'):
            self.assertIn(n, names)
        self.assertIn('Caps001', s['items'])
        self.assertIn('TrudyRef', s['enable_disable_subjects'])
        self.assertIn('VStoryState', s['globals'])
        self.assertIn('VMQ01', {t['edid'] for t in self.d['touched_quests']})

    def test_terms_packs_and_deps_in_rollup(self):
        import json
        import gzip
        t = self.tmp.name
        terms = json.loads(gzip.open(os.path.join(t, 'terms.json.gz')).read())
        packs = json.loads(gzip.open(os.path.join(t, 'packs.json.gz')).read())
        self.assertEqual((len(terms), len(packs)), (515, 4888))
        self.assertTrue(sum(1 for x in terms for i in x['items'] if i['script']) > 100)
        self.assertTrue(any(r['dep_scripts'] > 0 for r in self.risk.values()))
        self.assertTrue(all(r['tier_direct'] <= r['tier_auto'] for r in self.risk.values()))

    def test_effects_oracle_golden(self):
        import gzip
        e = json.loads(gzip.open(os.path.join(self.tmp.name, 'effects.json.gz')).read())
        self.assertEqual(e['meta']['scripts'], 9938)
        by = {s['id']: s for s in e['scripts']}
        s = by['stage:FalloutNV.esm:080664:10:0']  # VMS01 stage 10
        self.assertEqual([(x['type'], x['objective'], x['flags']) for x in s['effects']], [('SetObjective', 5, 1)])
        self.assertEqual(e['meta']['unmodeled_ranking'][0]['fn'], 'SetQuestVar')
        types = {x['type'] for sc in e['scripts'] for x in sc['effects']}
        self.assertTrue({'SetStage', 'AddItem', 'EnableRef', 'MoveRef', 'ModKarma', 'SetGlobal'} <= types)
        # every unconditional static effect passes the oracle against a perfect capture, fails against an empty one
        sc = next(x for x in e['scripts'] if any(not y['conditional'] and not y['dynamic'] for y in x['effects']))
        self.assertEqual(effects.oracle_missing(sc['effects'], sc['effects']), [])
        self.assertTrue(effects.oracle_missing(sc['effects'], []))

    def test_native_effects_loads_in_l7_checker(self):
        import test_native as tn
        if not tn.HAVE_L7:
            self.skipTest('L7 tools absent')
        sys.path.insert(0, tn.L7_TOOLS)
        import check_stage
        nat = check_stage.load_native(os.path.join(self.tmp.name, 'effects_native.json.gz'))
        tn.validate_native(self, nat)
        self.assertEqual(len(nat['quests']), 228)  # quests that have stages
        v = nat['quests']['VMS01']
        self.assertEqual(v['ref'], 'FalloutNV.esm:080664')
        self.assertEqual(v['stages']['10']['unconditional'],
                         [{'type': 'SetObjective', 'quest': 'FalloutNV.esm:080664', 'objective': 5, 'flags': 1}])
        self.assertTrue(len(nat['infos']) > 5000)
        n = sum(len(st['unconditional']) for q in nat['quests'].values() for st in q['stages'].values())
        self.assertTrue(n > 2000, n)

    def test_check_stage_on_vcg01_synthetic_pair(self):
        import copy
        import test_native as tn
        if not tn.HAVE_L7:
            self.skipTest('L7 tools absent')
        sys.path.insert(0, tn.L7_TOOLS)
        import check_stage
        import snaplib
        nat = check_stage.load_native(os.path.join(self.tmp.name, 'effects_native.json.gz'))
        ref = nat['quests']['VCG01']['ref']
        self.assertEqual(ref, 'FalloutNV.esm:104C1C')
        exp = nat['quests']['VCG01']['stages']['60']['unconditional']
        self.assertEqual(exp, [{'type': 'SetObjective', 'quest': ref, 'objective': 10, 'flags': 2}])
        gold = os.path.join(os.path.dirname(tn.L7_TOOLS), '..', '..', 'tests', 'goldens', 'snapshot', 'base.json')
        base = snaplib.load(os.path.normpath(gold))
        before = copy.deepcopy(base)
        template = copy.deepcopy(next(iter(base['shared']['quests'].values())))  # keeps whatever keys the schema has
        template.update({'completed': False, 'completedStages': [55], 'currentStage': 55, 'edid': 'VCG01', 'failed': False,
                         'name': "Ain't That a Kick in the Head", 'running': True,
                         'objectives': {'10': {'completed': False, 'displayed': True}}})
        before['shared']['quests'][ref] = template
        after = copy.deepcopy(before)
        q = after['shared']['quests'][ref]
        q['currentStage'] = 60
        q['completedStages'] = [55, 60]
        q['objectives']['10']['completed'] = True
        code, rep = check_stage.check(nat, 'VCG01', 60, before, after)
        self.assertEqual((code, rep['missing']), (0, []), rep)
        # twin: the objective completion never happened -> the checker must FAIL on the missing effect
        broken = copy.deepcopy(after)
        broken['shared']['quests'][ref]['objectives']['10']['completed'] = False
        code, rep = check_stage.check(nat, 'VCG01', 60, before, broken)
        self.assertEqual(code, 1)
        self.assertEqual(rep['missing'], exp)

    def test_reputation_mapping_pinned_to_vanilla_lines(self):
        # vanilla stage scripts: `ModReputation <rep> 1 5` (fame) in 15ECC9 stage 100, `... 0 5` (infamy) in 167F0F stage 1
        src = {s['id']: s for s in json.loads(gzip.open(os.path.join(self.tmp.name, 'effects.json.gz')).read())['scripts']}
        nat = json.loads(gzip.open(os.path.join(self.tmp.name, 'effects_native.json.gz')).read())
        q = json.load(open(os.path.join(self.tmp.name, 'quests.json'), encoding='utf-8'))['quests']
        by = {x['ref']: x for x in q}
        for qref, stage, args, want in (('FalloutNV.esm:15ECC9', 100, ['1', '5'], {'fame': 5, 'infamy': 0}),
                                        ('FalloutNV.esm:167F0F', 1, ['0', '5'], {'fame': 0, 'infamy': 5})):
            rich = [e for e in src[f'stage:{qref}:{stage}:0']['effects'] if e['type'] == 'ModReputation'
                    and e['args'] == args]
            self.assertEqual(len(rich), 1)
            self.assertIn('reputation', by[qref]['stages'][[s['index'] for s in by[qref]['stages']].index(stage)]
                          ['entries'][0]['script']['source'].lower())
            cond = nat['quests'][by[qref]['edid']]['stages'][str(stage)]['conditional']
            hit = [c for c in cond if c['type'] == 'ModReputation' and c['faction'] == rich[0]['faction']]
            self.assertEqual({k: hit[0][k] for k in ('fame', 'infamy')}, want)

    def test_guest_suppression_blocks_golden(self):
        e = json.loads(gzip.open(os.path.join(self.tmp.name, 'effects.json.gz')).read())
        bl = e['blocks']
        self.assertTrue(4500 < len(bl) < 6500, len(bl))
        types = {b['block'].lower() for b in bl}
        self.assertTrue({'gamemode', 'ondeath', 'onactivate', 'ontriggerenter', 'menumode'} <= types)
        vcg = [b for b in bl if b['edid'] == 'VCG01SCRIPT']
        self.assertTrue(vcg and vcg[0]['owner'] == 'quest')
        self.assertTrue(any(u['fn'] == 'SetQuestVar' for b in vcg for u in b['unmodeled']))
        md = open(os.path.join(self.tmp.name, 'guest_suppression_report.md'), encoding='utf-8').read()
        for h in ('By block type', 'Cannot be blocked by L5', 'OnDeath blocks touching quests'):
            self.assertIn(h, md)
        od = [b for b in bl if b['block'].lower() == 'ondeath']
        self.assertTrue(300 < len(od) < 600, len(od))

    def test_reachability_filter_applied(self):
        self.assertTrue(any(r['dep_scripts_untouched'] > 0 for r in self.risk.values()))
        self.assertTrue(all(r['tier_all_deps'] >= r['tier_auto'] for r in self.risk.values()))

    def test_prologue_override_applied(self):
        r = next(v for v in self.risk.values() if v['edid'] == 'VCG01')
        self.assertEqual(r['tier'], 'T4')
        self.assertTrue(r['override_notes'])

    def test_mutation_wrong_core_name_is_rejected(self):
        with unittest.mock.patch.dict(self.m1.CORE, {'VCG02': 'Wrong Name'}):
            with self.assertRaises(AssertionError):
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    self.m1.main(['--in', self.tmp.name, '--out', self.tmp.name])
