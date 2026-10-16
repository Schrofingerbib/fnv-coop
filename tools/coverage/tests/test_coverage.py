"""Coverage lane tests on synthetic data. Run: python tools/coverage/run_tests.py"""
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import model                        # noqa: E402
import status as st                 # noqa: E402
from graph import Graph             # noqa: E402
from report import build_report, render_markdown   # noqa: E402
from dashboard import render_html   # noqa: E402
from policies import build_policies, write_policies   # noqa: E402
from critical import critical_paths, tackle_order      # noqa: E402

NAMES = ['VCG00', 'VCG01', 'VCG02', 'VCG03', 'VMQ01', 'VMQTops', 'VMQHouse', 'VHDHouseBattle',
         'VMQYesMan01', 'VHDIndependentBattle', 'VMQ03a', 'vHDBattleController', 'VMQ02', 'VHDLegionBattle',
         'SideA', 'Container1', 'PreReqX', 'Actor1']
DLC_LOCAL = 'DeadMoney.esm'
TIER = {'VMQTops': 'T4', 'VHDHouseBattle': 'T4', 'SideA': 'T3'}


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f)


def mk_quest(i, edid, scripts=None, conds=None):
    sc = (scripts or {})
    stages = [{'index': k, 'entries': [{'flags': [], 'conditions': [], 'script': {'source': v}}]}
              for k, v in sc.items()]
    return {'edid': edid, 'ref': f'T.esm:{i:06X}', 'plugin': 'T.esm', 'name': edid + ' name',
            'conditions': conds or [], 'stages': stages, 'objectives': [], 'script_data': None}


def make_world(root, extra_runs=None):
    q = []
    for i, n in enumerate(NAMES, 1):
        sc = None
        if n == 'VCG00':
            sc = {10: 'StartQuest VCG01\nSetStage VMQ01 10'}
        if n == 'VMQ01':
            sc = {10: 'StartQuest VMQTops ; comment'}
        if n == 'VMQTops':
            sc = {10: 'SetStage VMQHouse 10\nSetStage VMQYesMan01 10\nif GetStageDone VMQ02 10 == 1\nendif'}
        if n == 'VMQHouse':
            sc = {10: 'SetStage VHDHouseBattle 10'}
        if n == 'PreReqX':
            sc = {10: 'StartQuest VMQHouse'}
        q.append(mk_quest(i, n, sc))
    ref = {x['edid']: x['ref'] for x in q}
    # a condition-based gate: VHDIndependentBattle gated by VMQYesMan01 stage
    q[NAMES.index('VHDIndependentBattle')]['conditions'] = [
        {'func': 'GetStageDone', 'p1': ref['VMQYesMan01'], 'p2': 10, 'op': '==', 'value': 1.0}]
    risk = {}
    for x in q:
        n = x['edid']
        story = n not in ('Container1', 'Actor1')
        risk[x['ref']] = {'edid': n, 'name': x['name'], 'plugin': 'T.esm', 'tier': TIER.get(n, 'T1'),
                          'kind': 'story' if story else 'container', 'score': 50 if n in TIER else 1,
                          'cat_max_level': {'MOVE': 4, 'CTRL': 4} if n == 'VMQTops' else {'Q-STATE': 1},
                          'findings': ([{'line': 7, 'text': 'player.MoveTo Marker', 'cat': 'MOVE', 'level': 4,
                                         'fn': 'moveto', 'where': 'stage 10', 'player': True, 'block': None}]
                                       if n == 'VMQTops' else []),
                          'stage_findings': []}
    # objective target + script ref pointing at placed actors
    q[NAMES.index('VMQTops')]['objectives'] = [{'index': 10, 'targets': [{'ref': 'T.esm:0000A1'}], 'text': 't'}]
    q[NAMES.index('VMQ01')]['stages'][0]['entries'][0]['script']['refs'] = ['DeadMoney.esm:0000B2', 'T.esm:0000C3']
    qd = os.path.join(root, 'questdata')
    import gzip
    os.makedirs(qd, exist_ok=True)
    with gzip.open(os.path.join(qd, 'forms.json.gz'), 'wt') as zf:
        json.dump({'T.esm:0000A1': {'sig': 'ACHR', 'plugin': 'T.esm', 'edid': 'BennyREF'},
                   'DeadMoney.esm:0000B2': {'sig': 'ACRE', 'plugin': 'DeadMoney.esm', 'edid': 'GeckoREF'},
                   'T.esm:0000C3': {'sig': 'REFR', 'plugin': 'T.esm', 'edid': 'DoorREF'}}, zf)
    write(os.path.join(qd, 'quests.json'), {'quests': q})
    write(os.path.join(qd, 'risk.json'), {'quests': risk})
    for n in NAMES:
        if n not in ('SideA',):                    # SideA has no generated spec -> 'unknown'
            write(os.path.join(qd, 'scenarios', n + '.json'), {})
    return model.load_quests(qd)


def run(root, run_id, kind, results, ts):
    write(os.path.join(root, 'runs', run_id, 'summary.json'),
          {'schema': 1, 'run_id': run_id, 'kind': kind, 'finished': ts,
           'results': [{'quest': a, 'status': b} for a, b in results]})


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.data = make_world(self.root)
        self.runs = os.path.join(self.root, 'runs')

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        s, info = st.build_status(self.data, self.runs)
        return s, info

    def test_ladder(self):
        run(self.root, 'a', 'sim', [('VCG00', 'pass'), ('VCG01', 'pass'), ('VCG02', 'pass')], '2026-10-16T01:00:00')
        run(self.root, 'b', 'e2e', [('VCG01', 'pass'), ('VCG02', 'pass')], '2026-10-16T02:00:00')
        run(self.root, 'c', 'playtest', [('VCG02', 'pass')], '2026-10-16T03:00:00')
        s, info = self.status()
        self.assertEqual(s['SideA']['rung'], 'unknown')
        self.assertEqual(s['VMQ01']['rung'], 'spec')
        self.assertEqual(s['VCG00']['rung'], 'sim-pass')
        self.assertEqual(s['VCG01']['rung'], 'game-pass')
        self.assertEqual(s['VCG02']['rung'], 'playtested')
        self.assertEqual(info['runs'], 3)

    def check_failing_overrides_old_pass(self, fold=st.fold):
        """Shared by the real test and its mutation twin."""
        run(self.root, 'old', 'e2e', [('VMQTops', 'pass'), ('VMQ01', 'pass')], '2026-10-16T01:00:00')
        run(self.root, 'new', 'e2e', [('VMQTops', 'fail')], '2026-10-16T02:00:00')
        runs = st.load_runs(self.runs)
        latest = fold(runs)
        s = st.quest_status('VMQTops', latest, True)
        self.assertTrue(s['failing'], 'a later failing run must flag the quest failing')
        self.assertEqual(s['failing_level'], 'game')
        self.assertNotEqual(s['rung'], 'game-pass')
        self.assertEqual(st.label(s), 'failing')
        self.assertEqual(st.quest_status('VMQ01', latest, True)['rung'], 'game-pass')   # untouched

    def test_game_pass_with_failing_run_is_reported_failing(self):
        self.check_failing_overrides_old_pass()

    def test_mutation_twin_pass_masks_fail(self):
        def broken_fold(runs):                   # "any pass wins" / first-seen wins
            out = {}
            for r in runs:
                for quest, status_, lvl, raw in r['results']:
                    cur = out.setdefault(quest, {}).get(lvl)
                    if cur is None or (status_ == 'pass' and cur['status'] != 'pass'):
                        out[quest][lvl] = {'status': status_, 'run_id': r['run_id'], 'ts': r['ts'],
                                           'failed_steps': [], 'steps': None}
            return out
        with self.assertRaises(AssertionError):
            self.check_failing_overrides_old_pass(broken_fold)

    def test_fail_then_later_pass_recovers(self):
        run(self.root, 'a', 'e2e', [('VMQTops', 'fail')], '2026-10-16T01:00:00')
        run(self.root, 'b', 'e2e', [('VMQTops', 'pass')], '2026-10-16T02:00:00')
        s, _ = self.status()
        self.assertFalse(s['VMQTops']['failing'])
        self.assertEqual(s['VMQTops']['rung'], 'game-pass')

    def test_sim_fail_blocks_higher_rungs(self):
        run(self.root, 'a', 'sim', [('VCG00', 'fail')], '2026-10-16T02:00:00')
        run(self.root, 'b', 'playtest', [('VCG00', 'pass')], '2026-10-16T01:00:00')
        s, _ = self.status()
        self.assertTrue(s['VCG00']['failing'])
        self.assertEqual(s['VCG00']['failing_level'], 'sim')
        self.assertFalse(st.at_least(s['VCG00'], 'spec'))

    def test_skip_does_not_mask_and_garbage_tolerated(self):
        run(self.root, 'a', 'e2e', [('VMQ01', 'pass')], '2026-10-16T01:00:00')
        run(self.root, 'b', 'e2e', [('VMQ01', 'skipped')], '2026-10-16T02:00:00')
        os.makedirs(os.path.join(self.runs, 'junk'))
        open(os.path.join(self.runs, 'junk', 'summary.json'), 'w').write('{not json')
        write(os.path.join(self.runs, 'weird', 'summary.json'), [1, 2])
        write(os.path.join(self.runs, 'alias', 'summary.json'),
              {'run_id': '2026-10-16T03', 'kind': 'sim', 'quests': {'VCG03': {'status': 'passed'}, 'Nope': 'pass'}})
        s, info = self.status()
        self.assertEqual(s['VMQ01']['rung'], 'game-pass')
        self.assertEqual(s['VCG03']['rung'], 'sim-pass')
        self.assertEqual(info['unknown_quests'], ['Nope'])

    def test_no_runs_dir(self):
        s, info = st.build_status(self.data, os.path.join(self.root, 'does-not-exist'))
        self.assertEqual(info['runs'], 0)
        self.assertEqual(s['VCG00']['rung'], 'spec')


class GraphAndPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = make_world(self.tmp.name)
        self.g = Graph(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def edge(self, s, t, k):
        return any(e[:3] == (s, t, k) for e in self.g.edges)

    def test_edges(self):
        self.assertTrue(self.edge('VCG00', 'VCG01', 'start'))
        self.assertTrue(self.edge('VCG00', 'VMQ01', 'stage'))
        self.assertTrue(self.edge('VMQ01', 'VMQTops', 'start'))      # trailing comment ignored
        self.assertTrue(self.edge('VMQ02', 'VMQTops', 'gate'))       # GetStageDone read in script
        self.assertTrue(self.edge('VMQYesMan01', 'VHDIndependentBattle', 'gate'))   # quest condition
        self.assertFalse(self.edge('VMQTops', 'VMQTops', 'start'))

    def test_paths_and_plan(self):
        paths = critical_paths(self.data, self.g)
        house = [q['edid'] for q in paths['House']['quests']]
        self.assertEqual(house[:2], ['VCG00', 'VCG01'])
        self.assertEqual(house[-1], 'VHDHouseBattle')
        self.assertLess(house.index('VMQTops'), house.index('VMQHouse'))
        s, _ = st.build_status(self.data, 'nope')
        plan = tackle_order(self.data, paths, s, 'game-pass', st.at_least)
        self.assertEqual(plan[0]['edid'], 'VCG00')
        ends = [p['unlocks_ending'] for p in plan if p['unlocks_ending']]
        self.assertEqual(sorted(ends), ['House', 'Legion', 'NCR', 'Yes Man'])
        self.assertEqual(len(set(p['edid'] for p in plan)), len(plan))     # no duplicates


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.data = make_world(self.root)
        self.g = Graph(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        s, info = st.build_status(self.data, os.path.join(self.root, 'runs'))
        return build_report(self.data, self.g, s, info)

    def test_counts_and_ready(self):
        everything = [(n, 'pass') for n in NAMES]
        run(self.root, 'e', 'e2e', everything, '2026-10-16T01:00:00')
        rep = self.report()
        self.assertEqual(rep['story_total'], len(NAMES) - 2)
        self.assertEqual(rep['totals']['game-pass'], len(NAMES) - 2)
        self.assertTrue(rep['paths']['House']['ready'])
        self.assertEqual(rep['by_tier']['T4']['game-pass'], 2)

    def test_failing_quest_reported_failing_everywhere(self):
        run(self.root, 'old', 'e2e', [(n, 'pass') for n in NAMES], '2026-10-16T01:00:00')
        run(self.root, 'new', 'e2e', [('VMQTops', 'fail')], '2026-10-16T02:00:00')
        rep = self.report()
        self.assertEqual(rep['totals']['failing'], 1)
        self.assertEqual(rep['by_tier']['T4']['failing'], 1)
        for end, p in rep['paths'].items():
            self.assertFalse(p['ready'], end)
            self.assertIn('VMQTops', p['failing'])
        row = [r for r in rep['quests'] if r['edid'] == 'VMQTops'][0]
        self.assertEqual((row['bucket'], row['failing_level']), ('failing', 'game'))
        md = render_markdown(rep)
        self.assertIn('FAILING: VMQTops', md)
        html = render_html(rep)
        self.assertRegex(html, r'"bucket": "failing"')

    def test_html_is_self_contained(self):
        html = render_html(self.report())
        self.assertNotRegex(html, r'(src|href)\s*=\s*["\']?https?:')
        self.assertNotIn('<script src', html)
        self.assertNotIn('@import', html)
        m = re.search(r'const R=(\{.*?\}), C=', html, re.S)
        self.assertTrue(m)
        json.loads(m.group(1))                   # embedded data is valid JSON


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = make_world(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_t3_t4_story_and_exact_cites(self):
        pols = build_policies(self.data)
        ids = [p['edid'] for p in pols]
        self.assertEqual(sorted(ids), ['SideA', 'VHDHouseBattle', 'VMQTops'])
        tops = [p for p in pols if p['edid'] == 'VMQTops'][0]
        self.assertEqual([p['tier'] for p in pols], ['T4', 'T4', 'T3'])    # tier desc ordering
        self.assertEqual(tops['cites'][0]['where'], 'stage 10')
        self.assertEqual(tops['cites'][0]['line'], 7)
        self.assertIn('MoveRef(player, party-warp)', tops['hooks'])
        self.assertTrue(tops['curated'])
        self.assertFalse([p for p in pols if p['edid'] == 'SideA'][0]['curated'])

    def test_write_files(self):
        out = os.path.join(self.tmp.name, 'pol')
        write_policies(self.data, out)
        self.assertTrue(os.path.exists(os.path.join(out, 'README.md')))
        txt = ''.join(open(os.path.join(out, f), encoding='utf-8').read() for f in os.listdir(out) if f.endswith('.md'))
        self.assertIn('`stage 10:7` `player.MoveTo Marker`', txt)


class TwinTests(unittest.TestCase):
    """Mutation twins for graph, policy generator, rules and host-owned list."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = make_world(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # --- graph: removing the StartQuest edge must change the critical path
    def check_prereq_discovered(self):
        g = Graph(self.data)
        house = {q['edid']: q for q in critical_paths(self.data, g)['House']['quests']}
        self.assertIn('PreReqX', house, 'StartQuest VMQHouse in PreReqX must make it a House prerequisite')
        self.assertTrue(house['PreReqX']['discovered'])
        order = list(house)
        self.assertLess(order.index('PreReqX'), order.index('VMQHouse'))

    def test_graph_startquest_edge_changes_path(self):
        self.check_prereq_discovered()

    def test_mutation_twin_graph_drops_startquest(self):
        import graph
        saved = dict(graph.CMD_KIND)
        graph.CMD_KIND.pop('startquest')
        try:
            with self.assertRaises(AssertionError):
                self.check_prereq_discovered()
        finally:
            graph.CMD_KIND.update(saved)

    # --- policy generator: exact cites + tier filter
    def check_policy_generator(self):
        pols = build_policies(self.data)
        self.assertEqual(sorted(p['edid'] for p in pols), ['SideA', 'VHDHouseBattle', 'VMQTops'])
        tops = [p for p in pols if p['edid'] == 'VMQTops'][0]
        self.assertEqual((tops['cites'][0]['where'], tops['cites'][0]['line']), ('stage 10', 7))
        self.assertTrue(tops['curated'])

    def test_policy_generator(self):
        self.check_policy_generator()

    def test_mutation_twin_policy_off_by_one_cite(self):
        import policies
        real = policies.cites

        def bad(risk, limit=10):
            return [dict(c, line=c['line'] + 1) for c in real(risk, limit)]
        policies.cites = bad
        try:
            with self.assertRaises(AssertionError):
                self.check_policy_generator()
        finally:
            policies.cites = real

    def test_mutation_twin_policy_tier_filter(self):
        import policies
        for r in self.data['risk'].values():
            r['tier'] = 'T3'           # everything becomes T3: the T1/T2 filter test needs a mix
        self.data['risk'][self.data['by_edid']['VCG01']['ref']]['tier'] = 'T1'
        ids = [p['edid'] for p in policies.build_policies(self.data)]
        self.assertNotIn('VCG01', ids)
        self.assertIn('VCG00', ids)

    # --- machine rules
    def check_rules(self):
        p = {x['edid']: x for x in build_policies(self.data)}
        kinds = {r['kind']: r for r in p['VMQTops']['machine_rules']}
        self.assertEqual(kinds['PartyWarp']['params']['markers'], ['Marker'])
        self.assertIn('PartyTether', kinds)
        self.assertEqual(kinds['PartyTether']['origin'], 'curated')
        side = {r['kind'] for r in p['SideA']['machine_rules']}
        self.assertEqual(side, set())          # SideA only has Q-STATE

    def test_rules(self):
        self.check_rules()

    def test_mutation_twin_rules_ignore_moveto(self):
        import rules
        real = rules._params

        def bad(kind, risk, edid):
            out = real(kind, risk, edid)
            if kind == 'PartyWarp':
                out['markers'] = []
            return out
        rules._params = bad
        try:
            with self.assertRaises(AssertionError):
                self.check_rules()
        finally:
            rules._params = real

    def test_rules_json_written_and_loadable(self):
        out = os.path.join(self.tmp.name, 'pol')
        write_policies(self.data, out)
        j = json.load(open(os.path.join(out, 'rules.base.json')))
        self.assertEqual(j['schema'], 1)
        self.assertIn('PartyWarp', [r['kind'] for r in j['quests']['VMQTops']['rules']])

    # --- host-owned
    def check_hostowned(self):
        import hostowned
        rows = hostowned.candidates(self.data, hostowned.load_forms(self.data['qdir']),
                                    ['T.esm', 'DeadMoney.esm'])
        ids = [r['formid'] for r in rows]
        self.assertEqual(ids, ['000000A1', '010000B2'])      # DLC id resolved to load-order index 01
        self.assertNotIn('000000C3', ids)                    # REFR (door) is not an actor

    def test_hostowned(self):
        self.check_hostowned()

    def test_load_order_matches_shared_file(self):
        import hostowned
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'questdata'))
        import loadorder
        self.assertEqual(hostowned.DEFAULT_LOAD_ORDER, loadorder.load())
        # mutation twin: a stale hardcoded order must be detected by the same comparison
        stale = ['FalloutNV.esm', 'DeadMoney.esm', 'HonestHearts.esm', 'OldWorldBlues.esm', 'LonesomeRoad.esm',
                 'GunRunnersArsenal.esm', 'ClassicPack.esm', 'MercenaryPack.esm', 'TribalPack.esm', 'CaravanPack.esm']
        self.assertNotEqual(stale, loadorder.load())
        with open(os.path.join(self.tmp.name, 'ho'), 'w'):
            pass
        rows = hostowned.write(self.data, self.tmp.name)
        head = open(os.path.join(self.tmp.name, 'hostowned.txt')).read().splitlines()[1]
        self.assertIn('09=GunRunnersArsenal.esm', head)

    def test_mutation_twin_hostowned_wrong_index(self):
        import hostowned
        real = hostowned.resolve
        hostowned.resolve = lambda ref, order: int(ref.split(':')[1], 16)    # forgets the plugin index
        try:
            with self.assertRaises(AssertionError):
                self.check_hostowned()
        finally:
            hostowned.resolve = real



class RealRulesContract(unittest.TestCase):
    """Pins what L4's synccore/tests/test_rules.cpp reads from the real rules.json. Change => tell L4."""

    def test_real_rules_contract(self):
        if not os.path.exists(os.path.join(model.QDIR, 'risk.json')):
            self.skipTest('no real questdata')
        data = model.load_quests()
        with tempfile.TemporaryDirectory() as tmp:
            write_policies(data, tmp)
            q = json.load(open(os.path.join(tmp, 'rules.base.json')))['quests']
        self.assertGreater(len(q), 100)
        kinds = lambda e: [r['kind'] for r in q[e]['rules']]
        self.assertIn('Barrier', kinds('VCG01'))
        self.assertEqual(sum('Barrier' in kinds(e) for e in q), 2)
        self.assertEqual(len([r for r in q['VCG01']['rules'] if r['kind'] == 'SetPlayerControls'][0]['params']['masks']), 3)
        funcs = [r for r in q['VMQTops']['rules'] if r['kind'] == 'PartyAwareCond'][0]['params']['funcs']
        self.assertEqual(funcs['GetInCell'], 'entry:all-in,exit:any-in')


if __name__ == '__main__':
    unittest.main()
