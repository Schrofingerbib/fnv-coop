"""Tests for live_status / backlog / sweeps / dashboard integration (synthetic repo trees)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import live_status as LS          # noqa: E402
import backlog as BL              # noqa: E402
import sweeps as SW               # noqa: E402
import dashboard                  # noqa: E402
from report import render_markdown   # noqa: E402

EFFECT_H = """
struct SetPerk {}; struct SetLock {}; struct AddNote {}; struct CompanionOwner {};
using Effect = std::variant<SetPerk, SetLock, AddNote,
                            CompanionOwner>;
"""
CAPTURE_CPP = """
void h_AddPerk() { g_hub->record(SetPerk{pr, 1, Recipient::dflt()}); }
void h_Note() { g_hub->record(AddNote{n}); }
void h_Team() { g_hub->record(CompanionOwner{a, b}); }
static const Hook kHooks[] = {
    HK(Cmd_AddPerk, h_AddPerk, o_AddPerk, true),
    HK(Cmd_Lock, h_Lock, o_Lock, true),
};
"""
APPLY_CPP = """
if (auto* x = std::get_if<SetPerk>(&e)) { apply(x); }
if (auto* y = std::get_if<AddNote>(&e)) { apply(y); }
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def make_repo(root):
    write(os.path.join(root, 'synccore', 'include', 'fnvcoop', 'effect.h'), EFFECT_H)
    write(os.path.join(root, 'plugin', 'hooks', 'engine_hooks.cpp'), CAPTURE_CPP)
    write(os.path.join(root, 'plugin', 'hooks', 'apply_remote.cpp'), APPLY_CPP)
    write(os.path.join(root, 'plugin', 'hooks', 'effect_json.cpp'), 'if (auto* x = std::get_if<SwapTexture>(&e)) {}\n'
          'out = SwapTexture{};\n')           # the codec must NOT count as capture/apply evidence


def row(fn, effect, static, cap='', app='', count=100, sev='high'):
    return {'fn': fn, 'count': count, 'scripts': 1, 'effect': effect, 'l5_capture': cap, 'l5_apply': app,
            'l5_status': static, 'severity': sev, 'hazards': [], 'note': ''}


class LiveStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_repo(self.tmp.name)
        self.src = LS.scan_sources(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def st(self, r):
        return LS.live_status(r, self.src)['status']

    def test_scan(self):
        self.assertEqual(self.src['types'], ['SetPerk', 'SetLock', 'AddNote', 'CompanionOwner'])
        self.assertIn('SetPerk', self.src['captured'])
        self.assertIn('SetPerk', self.src['applied'])
        self.assertNotIn('SwapTexture', self.src['types'])
        self.assertEqual(self.src['cmd_hooks'], {'addperk', 'lock'})

    def check_statuses(self, scan_src=None):
        src = scan_src or self.src
        L = lambda r: LS.live_status(r, src)['status']
        # stale static 'NOT hooked' claims are resolved by the source
        self.assertEqual(L(row('addperk', 'SetPerk', 'partial', 'BLOCK_CMD only', 'no engine apply')), 'supported')
        # a real qualifier in the static text stays partial even though both halves exist
        self.assertEqual(L(row('addperk', 'SetPerk', 'partial', 'player only', 'player only')), 'partial')
        # no Effect in the census row but the type exists by name (AddNote)
        self.assertEqual(L(row('addnote', None, 'missing', 'NOT hooked', 'n/a')), 'supported')
        # captured but never applied
        self.assertEqual(L(row('setplayerteammate', 'CompanionOwner', 'missing', 'NOT hooked', 'n/a')), 'partial')

    def test_statuses(self):
        self.check_statuses()

    def test_mutation_twin_apply_side_ignored(self):
        broken = dict(self.src, applied={})
        with self.assertRaises(AssertionError):
            self.check_statuses(broken)

    def test_regression_and_unknowns(self):
        # static says supported but the source has nothing: missing, not silently trusted
        self.assertEqual(self.st(row('foo', 'SetLock', 'supported')), 'missing')
        # neither half found: static partial/missing stands
        self.assertEqual(self.st(row('foo', 'SetLock', 'partial')), 'partial')
        # hooked command without any Effect type -> at best partial
        self.assertEqual(self.st(row('lock', None, 'missing')), 'partial')
        self.assertEqual(self.st(row('bar', None, 'missing')), 'missing')
        self.assertEqual(self.st(row('showmessage', None, 'local')), 'local')

    def test_function_level_exceptions(self):
        # "NOT hooked (X only)" is a function-specific claim: stays uncaptured without its own hook
        r = row('restoreactorvalue', 'SetPerk', 'missing', 'NOT hooked (Set/Force/Mod/Damage only)', 'n/a')
        self.assertNotEqual(LS.live_status(r, self.src)['captured'], True)
        self.assertEqual(self.st(row('setobjectvar', 'SetScriptVar', 'missing', 'NOT captured', 'n/a')), 'missing')

    def test_codec_is_not_evidence(self):
        self.assertNotIn('SwapTexture', self.src['captured'])
        self.assertNotIn('SwapTexture', self.src['applied'])


def census(rows):
    return {'rows': rows}


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_repo(self.tmp.name)
        self.src = LS.scan_sources(self.tmp.name)
        self.rows = [
            row('addperk', 'SetPerk', 'partial', 'BLOCK_CMD only', 'no engine apply', count=300, sev='high'),
            row('unlock', 'SetLock', 'partial', 'NOT hooked', 'n/a', count=50, sev='blocker'),
            row('placeatme', None, 'missing', 'NOT hooked', 'n/a', count=40, sev='low'),
            row('evp', None, 'missing', 'NOT hooked', 'n/a', count=5000, sev='low'),
            row('showmessage', None, 'local', count=999, sev='none'),
            row('setplayerteammate', 'CompanionOwner', 'missing', 'NOT hooked', 'n/a', count=20, sev='blocker'),
        ]
        self.lanes = {
            'alpha': {'functions': [
                {'fn': 'addperk', 'count': 30, 'severity': 'high', 'status': 'missing'},
                {'fn': 'unlock', 'count': 10, 'severity': 'blocker', 'status': 'missing'},
                {'fn': 'placeatme', 'count': 5, 'severity': 'blocker', 'status': 'missing'},
                {'fn': 'read:getbuttonpressed', 'count': 99, 'severity': 'blocker', 'kind': 'read'}]},
            'beta': {'gaps': [{'function': 'placeatme', 'occurrences': 3, 'sev': 'critical'}],
                     'supported': [{'fn': 'addperk', 'count': 7}]},
            'gaponly': {'by_function': [{'fn': 'placeatme', 'count': 4, 'severity': 'blocker'}]},
            'items_only': {'items': [{'fn': 'unlock', 'severity': 'low'}] * 3},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, rows=None, lanes=None):
        return BL.build_backlog(census(rows or self.rows), lanes if lanes is not None else self.lanes, self.src)

    def test_game_wide_and_rank(self):
        b = self.build()
        gw = b['game_wide']
        self.assertEqual(gw['calls']['supported'], 300)         # addperk resolved by the source
        self.assertEqual(gw['calls_static'].get('supported', 0), 0)
        self.assertEqual(gw['functions_changed'], 2)            # addperk -> supported, setplayerteammate -> partial
        ranked = [f['fn'] for f in b['functions'] if f['score'] > 0]
        self.assertEqual(ranked, ['evp', 'unlock', 'setplayerteammate', 'placeatme'])
        self.assertEqual(b['functions'][0]['score'], 5000 * 1)
        wi = {(w['action'], w['target']): w for w in b['work_items']}
        self.assertIn(('new-effect', 'placeatme'), wi)
        self.assertIn(('capture+apply', 'SetLock'), wi)
        self.assertIn(('apply', 'CompanionOwner'), wi)

    def test_mutation_twin_severity_ignored(self):
        saved = dict(BL.SEV_W)
        for k in BL.SEV_W:
            BL.SEV_W[k] = 1
        try:
            ranked = [f['fn'] for f in self.build()['functions'] if f['score'] > 0]
            self.assertNotEqual(ranked, ['evp', 'unlock', 'setplayerteammate', 'placeatme'])
        finally:
            BL.SEV_W.update(saved)

    def test_questline_shapes(self):
        q = self.build()['questlines']
        a = q['alpha']
        self.assertEqual(a['calls'], {'supported': 30, 'partial': 10, 'missing': 5})   # read:* excluded; addperk live-supported
        self.assertAlmostEqual(a['synced_pct'], 66.7, places=1)
        self.assertAlmostEqual(a['partial_pct'], 22.2, places=1)
        self.assertEqual({r['fn'] for r in a['blockers']}, {'unlock', 'placeatme'})
        b = q['beta']
        self.assertEqual(b['calls'], {'missing': 3, 'supported': 7})
        self.assertEqual(b['blockers'][0]['severity'], 'blocker')       # 'critical' canonicalised
        self.assertTrue(q['gaponly']['gap_only_table'])
        self.assertIsNone(q['gaponly']['synced_pct'])
        self.assertEqual(q['items_only']['calls'], {'partial': 3})

    def test_unknown_functions_not_in_denominator(self):
        lanes = {'x': {'functions': [{'fn': 'addperk', 'count': 10}, {'fn': 'weirdpseudo', 'count': 90}]}}
        q = self.build(lanes=lanes)['questlines']['x']
        self.assertEqual(q['calls'].get('unknown'), 90)
        self.assertEqual(q['counted_calls'], 10)
        self.assertEqual(q['synced_pct'], 100.0)

    def test_write_and_render(self):
        out = os.path.join(self.tmp.name, 'o')
        BL.write(out, root=self.tmp.name, census=census(self.rows), lanes=self.lanes, src=self.src)
        j = json.load(open(os.path.join(out, 'backlog.json')))
        self.assertEqual(j['schema'], 1)
        md = open(os.path.join(out, 'backlog.md')).read()
        self.assertIn('## Work items', md)
        self.assertIn('placeatme', md)

    @unittest.skipUnless(os.path.exists(os.path.join(BL.ROOT, 'questlines', 'systems', 'census.json')), 'no real census')
    def test_real_data(self):
        b = BL.write(os.path.join(self.tmp.name, 'real'))
        self.assertGreater(b['game_wide']['synced_pct'], b['game_wide']['synced_pct_static'] - 0.001)
        self.assertGreater(len(b['questlines']), 10)
        # Track A 2026-10-22: UseWeapon was the last open script function; the backlog has no work items left, and it must not list it
        self.assertNotIn('useweapon', json.dumps(b['work_items']).lower())


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        write(os.path.join(r, 'questlines', 'ncr', 'sweep.json'), json.dumps({'quests': [
            {'edid': 'QA', 'stages': [{'index': 10}, {'index': 20}, {'index': 30}]},
            {'edid': 'QB', 'stages': [{'index': 5}]}]}))
        write(os.path.join(r, 'questlines', 'house', 'sweep.json'), json.dumps({'quests': [
            {'edid': 'QA', 'stages': [{'index': 10}]}, {'edid': 'QC', 'stages': [{'index': 1}]}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self, run, quest, stage, status, mtime, name=None, **extra):
        p = os.path.join(self.tmp.name, 'out', 'sweeps', 'ncr', run, 'stages', name or f'{quest}_{stage}.json')
        write(p, json.dumps(dict({'quest': quest, 'stage': stage, 'status': status}, **extra)))
        os.utime(p, (mtime, mtime))

    def test_counts_latest_wins_and_variants(self):
        t = time.time()
        self.stage('run1', 'QA', 10, 'fail', t - 1000, reasons=['missing_capture:1'])
        self.stage('run2', 'QA', 10, 'pass', t - 10)                 # later run fixes it
        self.stage('run1', 'QA', 20, 'pass', t - 900)
        self.stage('run1', 'QA', 20, 'fail', t - 900, name='QA_20__variantB.json')    # variant failure folds in
        self.stage('run1', 'QB', 5, 'error', t - 800)
        write(os.path.join(self.tmp.name, 'out', 'sweeps', 'ncr', 'probe-x', 'stages', 'QA_30.json'),
              json.dumps({'quest': 'QA', 'stage': 30, 'status': 'pass'}))     # probe dirs are ignored
        s = SW.build(self.tmp.name)['questlines']
        n = s['ncr']
        self.assertEqual((n['planned'], n['pass'], n['fail'], n['error'], n['not_run']), (4, 1, 1, 1, 1))
        qa = [q for q in n['quests'] if q['quest'] == 'QA'][0]
        self.assertEqual(qa['failing'][0]['stage'], 20)
        h = s['house']                                               # shares QA/10 with ncr; QC never ran
        self.assertEqual((h['planned'], h['pass'], h['not_run']), (2, 1, 1))

    def test_mutation_twin_oldest_wins(self):
        t = time.time()
        self.stage('run1', 'QA', 10, 'fail', t - 1000)
        self.stage('run2', 'QA', 10, 'pass', t - 10)
        real = os.path.getmtime
        SW.os.path.getmtime = lambda p: -real(p)       # inverts "latest": the oldest file now wins
        try:
            n = SW.build(self.tmp.name)['questlines']['ncr']
            self.assertEqual(n['fail'], 1)             # mutant behaviour...
        finally:
            SW.os.path.getmtime = real
        n = SW.build(self.tmp.name)['questlines']['ncr']
        self.assertEqual(n['fail'], 0)                 # ...and the real code differs, so the first test would catch it
        self.assertEqual(n['pass'], 1)

    def test_garbage_tolerated(self):
        p = os.path.join(self.tmp.name, 'out', 'sweeps', 'ncr', 'r', 'stages', 'bad.json')
        write(p, '{nope')
        write(os.path.join(self.tmp.name, 'out', 'sweeps', 'ncr', 'r', 'stages', 'odd.json'),
              json.dumps({'quest': 'QA', 'stage': 10, 'status': 'weird'}))
        n = SW.build(self.tmp.name)['questlines']['ncr']
        self.assertEqual(n['not_run'], 4)


class DashboardIntegrationTests(unittest.TestCase):
    def rep(self):
        pending = {k: 0 for k in ('unknown', 'spec', 'sim-pass', 'game-pass', 'playtested', 'failing')}
        return {'generated': 'x', 'target': 'game-pass', 'runs': 0, 'unknown_run_quests': [], 'story_total': 1,
                'all_total': 1, 'totals': dict(pending, spec=1), 'by_tier': {'T0': dict(pending, spec=1)},
                'by_line': {'L': dict(pending, spec=1)},
                'paths': {'House': {'quests': [{'edid': 'Q', 'name': 'q', 'tier': 'T0', 'discovered': True,
                                                'bucket': 'spec'}], 'done': 0, 'total': 1, 'ready': False,
                                    'failing': [], 'ending_quests': ['Q']}},
                'plan': [{'order': 1, 'edid': 'Q', 'name': 'q', 'tier': 'T0', 'cost': 1, 'needed_by': ['House'],
                          'done': False, 'unlocks_ending': 'House', 'bucket': 'spec'}],
                'quests': [{'edid': 'Q', 'name': 'q', 'plugin': 'p', 'tier': 'T0', 'story': True, 'line': 'L',
                            'bucket': 'spec', 'rung': 'spec', 'failing': False, 'failing_level': None}]}

    def test_tabs_and_no_external_refs(self):
        rep = self.rep()
        rep['backlog'] = {'game_wide': {'synced_pct': 50.0, 'synced_pct_static': 40.0, 'functions_changed': 1,
                                        'calls': {'supported': 5, 'partial': 3, 'missing': 2, 'local': 1}},
                          'source': {'files_scanned': 1, 'effect_types': 1, 'hooked_commands': 1},
                          'work_items': [], 'functions': [], 'questlines': {}}
        rep['sweeps'] = {'results_total': 0, 'questlines': {}}
        html = dashboard.render_html(rep)
        for tab in ("['backlog','Backlog']", "['sweeps','Sweeps']", 'id="p-backlog"', 'id="p-sweeps"'):
            self.assertIn(tab, html)
        self.assertNotRegex(html, r'(src|href)\s*=\s*["\']?https?:')
        md = render_markdown(dict(rep, runs=0, story_total=1, all_total=1))
        self.assertIn('Script-call sync', md)

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_dom_smoke_runs_without_exceptions(self):
        rep = self.rep()
        rep['backlog'] = None
        rep['sweeps'] = None
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'i.html')
            write(p, dashboard.render_html(rep))
            r = subprocess.run(['node', os.path.join(HERE, 'dom_smoke.js'), p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(shutil.which('node') and os.path.exists(os.path.join(BL.ROOT, 'out', 'coverage', 'index.html')),
                         'no node or no built dashboard')
    def test_real_dashboard_dom_smoke(self):
        r = subprocess.run(['node', os.path.join(HERE, 'dom_smoke.js'),
                            os.path.join(BL.ROOT, 'out', 'coverage', 'index.html')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
