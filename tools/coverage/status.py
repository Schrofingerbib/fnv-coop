"""Coverage status model: fold runs/*/summary.json (L2 runner output) into a per-quest rung.

Rungs (LADDER in model.py):  unknown -> spec -> sim-pass -> game-pass -> playtested
plus a `failing` flag.  The LATEST run per (quest, level) wins; an old pass never masks a newer failure
(this is the property the mutation twin pins down).  Missing/corrupt run data is tolerated.

Accepted summary.json (schema 1, documented in internal lane notes):
  {"schema":1, "run_id":"2026-10-16T0900-e2e", "kind":"sim|e2e|playtest", "started":"ISO8601",
   "finished":"ISO8601", "results":[{"quest":"VMQ03a","status":"pass|fail|error|skip",
   "steps":12, "failed_steps":["stage_20"], "duration_s":31.2, "kind":"(optional per-result override)"}]}
Alias: top-level "quests": {"VMQ03a": {"status": "pass"}} instead of "results".
"""
import glob
import json
import os
import re

from model import LADDER, scenario_exists

KIND_ALIAS = {'sim': 'sim', 'synccore': 'sim', 'sim-pass': 'sim',
              'e2e': 'game', 'game': 'game', 'game-pass': 'game', '2-instance': 'game', 'twoinstance': 'game',
              'playtest': 'playtest', 'playtested': 'playtest', 'manual': 'playtest'}
STATUS_ALIAS = {'pass': 'pass', 'passed': 'pass', 'ok': 'pass', 'success': 'pass', 'green': 'pass',
                'fail': 'fail', 'failed': 'fail', 'failure': 'fail', 'error': 'fail', 'timeout': 'fail',
                'crash': 'fail', 'red': 'fail',
                'skip': 'skip', 'skipped': 'skip'}
LEVELS = ['sim', 'game', 'playtest']
LEVEL_RUNG = {'sim': 'sim-pass', 'game': 'game-pass', 'playtest': 'playtested'}


def _ts(s):
    """sortable timestamp string; ISO strings sort lexicographically. Non-strings -> ''."""
    return s if isinstance(s, str) else ''


def load_runs(runs_dir):
    """-> list of run dicts (normalised), tolerant of garbage. Each: run_id, kind, ts, results[(quest, status, extra)]."""
    runs = []
    if not runs_dir or not os.path.isdir(runs_dir):
        return runs
    for p in sorted(glob.glob(os.path.join(runs_dir, '*', 'summary.json'))):
        try:
            d = json.load(open(p, encoding='utf-8'))
            if not isinstance(d, dict):
                continue
        except (OSError, ValueError):
            continue
        run_id = str(d.get('run_id') or os.path.basename(os.path.dirname(p)))
        kind = KIND_ALIAS.get(str(d.get('kind', '')).lower())
        ts = _ts(d.get('finished')) or _ts(d.get('started'))
        if not ts:
            m = re.match(r'(\d{4}-\d{2}-\d{2}T?\d{0,4})', run_id)
            ts = m.group(1) if m else ''
        if not ts:
            ts = '~mtime%020.3f' % os.path.getmtime(p)
        res = []
        raw = d.get('results')
        if raw is None and isinstance(d.get('quests'), dict):
            raw = [dict(v, quest=k) if isinstance(v, dict) else {'quest': k, 'status': v}
                   for k, v in d['quests'].items()]
        for r in raw if isinstance(raw, list) else []:
            if not isinstance(r, dict) or not r.get('quest'):
                continue
            st = STATUS_ALIAS.get(str(r.get('status', '')).lower())
            rk = KIND_ALIAS.get(str(r.get('kind', '')).lower()) or kind
            if st and rk:
                res.append((str(r['quest']), st, rk, r))
        runs.append({'run_id': run_id, 'kind': kind, 'ts': ts, 'results': res, 'path': p})
    return runs


def fold(runs):
    """-> {quest: {level: {'status','run_id','ts','detail'}}} latest non-skip result per level."""
    latest = {}
    for run in runs:
        for quest, st, lvl, raw in run['results']:
            if st == 'skip':
                continue
            cur = latest.setdefault(quest, {}).get(lvl)
            key = (run['ts'], run['run_id'])
            if cur is None or key >= (cur['ts'], cur['run_id']):
                latest[quest][lvl] = {'status': st, 'run_id': run['run_id'], 'ts': run['ts'],
                                      'failed_steps': raw.get('failed_steps') or [],
                                      'steps': raw.get('steps')}
    return latest


def quest_status(edid, latest, has_spec):
    """-> dict(rung, failing, failing_level, detail)"""
    rung = 'spec' if has_spec else 'unknown'
    failing = None
    levels = latest.get(edid, {})
    for lvl in LEVELS:
        r = levels.get(lvl)
        if not r:
            continue
        if r['status'] == 'fail':
            failing = lvl
            break
        rung = LEVEL_RUNG[lvl]
    return {'rung': rung, 'failing': failing is not None, 'failing_level': failing,
            'detail': {k: v for k, v in levels.items()}}


def build_status(data, runs_dir):
    runs = load_runs(runs_dir)
    latest = fold(runs)
    out = {}
    for q in data['quests']:
        out[q['edid']] = quest_status(q['edid'], latest, scenario_exists(data, q['edid']))
    unknown_quests = sorted(set(latest) - set(out))
    return out, {'runs': len(runs), 'unknown_quests': unknown_quests}


def label(st):
    """dashboard bucket: failing beats the rung."""
    return 'failing' if st['failing'] else st['rung']


def at_least(st, rung):
    return (not st['failing']) and LADDER.index(st['rung']) >= LADDER.index(rung)
