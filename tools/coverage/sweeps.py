"""Stage-sweep progress per questline: stages passed / failed / not run.

Plans   : questlines/<slug>/sweep.json (quests[].stages[].index) and out/questdata/m1_goodsprings.json ('goodsprings')
Results : out/sweeps/<slug>/<run-id>/stages/<QUEST>_<STAGE>[__variant].json  (L7 sweep driver, kind "game")
          out/goodsprings_sweep/<run-id>/stages/*.json                         (the original Goodsprings run)
Each stage file: {"quest", "stage", "status": pass|fail|error|noent, ...}.  The latest file (mtime) per
(quest, stage) wins; variants of one stage are folded (any fail -> fail, else any error -> error, else pass).
"""
import glob
import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STATES = ['pass', 'fail', 'error', 'noent']


def _stage_index(s):
    if isinstance(s, dict):
        return s.get('index')
    return s if isinstance(s, int) else None


def load_plans(root=ROOT):
    """-> {slug: {'name', 'stages': {(quest, stage)}, 'quests': {quest: [stages]}}}"""
    plans = {}
    files = sorted(glob.glob(os.path.join(root, 'questlines', '*', 'sweep.json')))
    m1 = os.path.join(root, 'out', 'questdata', 'm1_goodsprings.json')
    srcs = [(os.path.basename(os.path.dirname(f)), f) for f in files]
    if os.path.exists(m1):
        srcs.append(('goodsprings', m1))
    for slug, f in srcs:
        try:
            d = json.load(open(f, encoding='utf-8'))
        except (OSError, ValueError):
            continue
        qs = {}
        for q in d.get('quests', []):
            idx = [i for i in (_stage_index(s) for s in q.get('stages', [])) if i is not None]
            if idx:
                qs[q['edid']] = sorted(set(idx))
        plans[slug] = {'quests': qs, 'stages': {(q, i) for q, ix in qs.items() for i in ix}}
    return plans


def load_results(root=ROOT):
    """-> {(quest, stage): {'status', 'file', 'mtime', 'reasons'}}  (latest wins, variants folded)"""
    dirs = glob.glob(os.path.join(root, 'out', 'sweeps', '*', '*', 'stages')) + \
        glob.glob(os.path.join(root, 'out', 'goodsprings_sweep', '*', 'stages'))
    raw = {}
    for d in dirs:
        if re.search(r'probe', d, re.I):
            continue
        for f in glob.glob(os.path.join(d, '*.json')):
            try:
                j = json.load(open(f, encoding='utf-8'))
                key = (j['quest'], int(j['stage']))
                st = str(j.get('status', '')).lower()
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if st not in STATES:
                continue
            variant = j.get('variant') or ('__' in os.path.basename(f))
            raw.setdefault(key, []).append((os.path.getmtime(f), st, f, j.get('reasons') or [], variant))
    out = {}
    for key, lst in raw.items():
        newest = max(m for m, *_ in lst)
        # variants of one stage live in the same run dir: fold the newest run only (a later pass beats an old fail)
        newest_dir = os.path.dirname(max(lst)[2])
        cur = [x for x in lst if os.path.dirname(x[2]) == newest_dir]
        sts = {x[1] for x in cur}
        st = 'fail' if 'fail' in sts else 'error' if 'error' in sts else 'noent' if sts == {'noent'} else 'pass'
        latest = max(cur)
        out[key] = {'status': st, 'file': os.path.relpath(latest[2], root).replace('\\', '/'), 'mtime': newest,
                    'reasons': sorted({r for x in cur for r in x[3]})[:6]}
    return out


def summarize(plans, results):
    out = {}
    for slug, p in sorted(plans.items()):
        c = {s: 0 for s in STATES}
        c['not_run'] = 0
        per_quest = []
        for q, stages in sorted(p['quests'].items()):
            qc = {s: 0 for s in STATES}
            qc['not_run'] = 0
            bad = []
            for i in stages:
                r = results.get((q, i))
                if not r:
                    qc['not_run'] += 1
                else:
                    qc[r['status']] += 1
                    if r['status'] in ('fail', 'error'):
                        bad.append({'stage': i, 'status': r['status'], 'reasons': r['reasons']})
            for k, v in qc.items():
                c[k] += v
            per_quest.append(dict(quest=q, planned=len(stages), failing=bad[:8], **qc))
        planned = len(p['stages'])
        out[slug] = dict(planned=planned, pass_pct=round(100.0 * c['pass'] / planned, 1) if planned else None,
                         quests=per_quest, **c)
    return out


def build(root=ROOT):
    plans = load_plans(root)
    res = load_results(root)
    return {'schema': 1, 'questlines': summarize(plans, res), 'results_total': len(res)}


def render_md(s):
    L = ['# Sweep progress per questline', '', '| questline | planned | pass | fail | error | noent | not run | pass % |',
         '|---|---|---|---|---|---|---|---|']
    for slug, q in s['questlines'].items():
        L.append(f"| {slug} | {q['planned']} | {q['pass']} | {q['fail']} | {q['error']} | {q['noent']} | "
                 f"{q['not_run']} | {q['pass_pct']} |")
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    s = build()
    print(render_md(s))
