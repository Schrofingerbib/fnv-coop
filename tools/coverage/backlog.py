"""Live unified backlog: what still blocks the whole game from being co-op, ranked.

Inputs  : questlines/systems/census.json (game-wide function census: counts per script function)
          questlines/*/gaps.json         (per-questline gap tables; 15 different shapes, read tolerantly)
          the CURRENT source (live_status.py): capture/apply status is recomputed, never trusted from the tables
Outputs : out/coverage/backlog.json, backlog.md (and the 'backlog' section of the dashboard)

Rank = game-wide call count x severity weight, for every function whose live status is not 'supported'/'local'.
Work items group functions that one change fixes (capture T / apply T / new Effect for fn).
Per questline: % of that questline's counted calls that are synced (live), plus the remaining blockers.
"""
import collections
import glob
import importlib.util
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live_status as LS  # noqa: E402

ROOT = LS.ROOT
SEV_CANON = {'blocker': 'blocker', 'critical': 'blocker', 'high': 'high', 'major': 'high', 'medium': 'medium',
             'minor': 'low', 'low': 'low', 'info': 'none', 'none': 'none', 'ok': 'none'}
SEV_W = {'blocker': 8, 'high': 4, 'medium': 2, 'low': 1, 'none': 0}
SEV_ORDER = ['none', 'low', 'medium', 'high', 'blocker']
FN_KEYS = ('fn', 'function', 'name')
CNT_KEYS = ('count', 'occurrences', 'calls', 'n', 'uses', 'total')
SEV_KEYS = ('severity', 'sev', 'gap')
LIST_KEYS = ('functions', 'gaps', 'by_function', 'unsupported', 'rows', 'supported')
STATUS_CANON = {'supported': 'supported', 'partial': 'partial', 'missing': 'missing', 'local': 'local',
                'host-local': 'local', 'not_needed': 'local', 'n/a': 'local', 'read': 'local', 'host-only': 'local',
                'presentation': 'local'}


def sev_of(x):
    return SEV_CANON.get(str(x).lower(), None) if isinstance(x, str) else None


def max_sev(a, b):
    a, b = a or 'none', b or 'none'
    return a if SEV_ORDER.index(a) >= SEV_ORDER.index(b) else b


def load_aliases(root=ROOT):
    """canonical-name map from the systems census module (read-only import); {} if unavailable."""
    p = os.path.join(root, 'questlines', 'systems', 'census.py')
    if not os.path.exists(p):
        return {}
    try:
        import sys
        sys.path.insert(0, os.path.dirname(p))
        spec = importlib.util.spec_from_file_location('systems_census_ro', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return dict(mod.ALIASES)
    except Exception:      # noqa: BLE001  (another lane's module; never let it break the dashboard)
        return {}


def lane_rows(gaps, aliases):
    """-> {canon_fn: {'count': int, 'sev': str|None, 'static': str|None, 'read': bool}} for one gaps.json."""
    out = {}

    def add(row):
        fn = next((row[k] for k in FN_KEYS if isinstance(row.get(k), str)), None)
        if not fn:
            return
        cnt = 0
        for k in CNT_KEYS + ('counts',):
            v = row.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                cnt = int(v)
                break
            if isinstance(v, dict):
                cnt = int(sum(x for x in v.values() if isinstance(x, (int, float))))
                break
        read = fn.startswith('read:') or row.get('kind') == 'read'
        fn = fn.split(':', 1)[1] if fn.startswith('read:') else fn
        c = aliases.get(fn.lower(), fn.lower())
        sev = next((sev_of(row[k]) for k in SEV_KEYS if sev_of(row.get(k))), None)
        st = next((STATUS_CANON.get(str(row[k]).lower()) for k in ('status', 'overall', 'l5_status')
                   if isinstance(row.get(k), str) and str(row[k]).lower() in STATUS_CANON), None)
        e = out.setdefault(c, {'count': 0, 'sev': None, 'static': None, 'read': read})
        e['count'] += cnt
        e['sev'] = max_sev(e['sev'], sev) if sev else e['sev']
        e['static'] = e['static'] or st
        e['read'] = e['read'] and read
    for key in LIST_KEYS:
        v = gaps.get(key)
        if isinstance(v, dict):
            v = list(v.values())
        if isinstance(v, list):
            for r in v:
                if isinstance(r, dict):
                    add(r)
    if not out:                                   # per-occurrence shapes (towns/systems 'items')
        agg = collections.Counter()
        sev = {}
        for r in gaps.get('items', []) or []:
            if isinstance(r, dict) and isinstance(r.get('fn'), str):
                c = aliases.get(r['fn'].lower(), r['fn'].lower())
                agg[c] += 1
                sev[c] = max_sev(sev.get(c), sev_of(r.get('severity')))
        for c, n in agg.items():
            out[c] = {'count': n, 'sev': sev.get(c), 'static': None, 'read': False}
    return out


def load_lanes(root=ROOT):
    lanes = {}
    for f in sorted(glob.glob(os.path.join(root, 'questlines', '*', 'gaps.json'))):
        slug = os.path.basename(os.path.dirname(f))
        try:
            lanes[slug] = json.load(open(f, encoding='utf-8'))
        except (OSError, ValueError):
            continue
    return lanes


def _action(live, row):
    ets = live['effect_types']
    if live['status'] in ('supported', 'local'):
        return None
    if not ets:
        return ('new-effect', row['fn'], 'needs an Effect type + capture + apply for %s' % row['fn'])
    cap, app = live.get('captured'), live.get('applied')
    if not cap and not app:
        return ('capture+apply', '/'.join(ets), 'add capture hook and apply branch for %s' % '/'.join(ets))
    if not cap:
        return ('capture', '/'.join(ets), 'add capture hook for %s' % '/'.join(ets))
    if not app:
        return ('apply', '/'.join(ets), 'add apply branch for %s' % '/'.join(ets))
    return ('qualifier', '/'.join(ets), 'wired both ways but static note says partial: %s' % (row.get('note') or '')[:90])


def build_backlog(census, lanes, src, aliases=None, top_blockers=6):
    aliases = aliases or {}
    rows = {r['fn']: r for r in census['rows']}
    live = {fn: LS.live_status(r, src) for fn, r in rows.items()}
    funcs = []
    gw = {'live': collections.Counter(), 'static': collections.Counter()}
    for fn, r in rows.items():
        lv = live[fn]
        gw['live'][lv['status']] += r['count']
        gw['static'][lv['static']] += r['count']
        sev = sev_of(r.get('severity')) or 'none'
        w = SEV_W[sev]
        funcs.append({'fn': fn, 'count': r['count'], 'scripts': r.get('scripts'), 'severity': sev,
                      'static': lv['static'], 'live': lv['status'], 'changed': lv['changed'],
                      'effect_types': lv['effect_types'], 'captured': lv['captured'], 'applied': lv['applied'],
                      'score': r['count'] * w if lv['status'] not in ('supported', 'local') else 0,
                      'hazards': r.get('hazards', []), 'note': r.get('note', '')[:160]})
    funcs.sort(key=lambda f: (-f['score'], -f['count'], f['fn']))
    for i, f in enumerate([x for x in funcs if x['score'] > 0], 1):
        f['rank'] = i
    # work items: one change that fixes several functions
    items = {}
    for f in funcs:
        if f['score'] <= 0:
            continue
        act = _action(live[f['fn']], rows[f['fn']])
        if not act:
            continue
        k = (act[0], act[1])
        it = items.setdefault(k, {'action': act[0], 'target': act[1], 'what': act[2], 'score': 0, 'calls': 0,
                                  'functions': [], 'max_severity': 'none'})
        it['score'] += f['score']
        it['calls'] += f['count']
        it['functions'].append(f['fn'])
        it['max_severity'] = max_sev(it['max_severity'], f['severity'])
    work = sorted(items.values(), key=lambda i: (-i['score'], i['target']))
    for i, w in enumerate(work, 1):
        w['rank'] = i
    # per questline
    ql = {}
    for slug, g in lanes.items():
        lr = lane_rows(g, aliases)
        tot = collections.Counter()
        remaining = []
        for fn, e in lr.items():
            if e['read']:
                continue
            lv = live.get(fn)
            st = lv['status'] if lv else (e['static'] or 'unknown')
            tot[st] += e['count']
            sev = max_sev(e['sev'], sev_of(rows[fn].get('severity')) if fn in rows else None)
            if st in ('partial', 'missing') and e['count']:
                remaining.append({'fn': fn, 'count': e['count'], 'severity': sev, 'live': st,
                                  'score': e['count'] * SEV_W[sev]})
        remaining.sort(key=lambda r: (-r['score'], -r['count'], r['fn']))
        denom = tot['supported'] + tot['partial'] + tot['missing']
        gap_only = tot['supported'] == 0              # table lists gaps only: no denominator, no honest percentage
        ql[slug] = {'calls': dict(tot), 'counted_calls': denom, 'gap_only_table': gap_only,
                    'synced_pct': None if gap_only or not denom else round(100.0 * tot['supported'] / denom, 1),
                    'partial_pct': None if gap_only or not denom else round(100.0 * tot['partial'] / denom, 1),
                    'blockers': [r for r in remaining if r['severity'] in ('blocker', 'high')][:top_blockers],
                    'remaining': remaining[:top_blockers], 'blocker_calls': sum(
                        r['count'] for r in remaining if r['severity'] == 'blocker')}
    denom = gw['live']['supported'] + gw['live']['partial'] + gw['live']['missing']
    sdenom = gw['static']['supported'] + gw['static']['partial'] + gw['static']['missing']
    return {'schema': 1,
            'source': {'effect_types': len(src['types']), 'files_scanned': src['files'],
                       'captured_types': sorted(src['captured']), 'applied_types': sorted(src['applied']),
                       'hooked_commands': len(src['cmd_hooks'])},
            'game_wide': {'calls': dict(gw['live']), 'calls_static': dict(gw['static']),
                          'synced_pct': round(100.0 * gw['live']['supported'] / denom, 1) if denom else None,
                          'synced_pct_static': round(100.0 * gw['static']['supported'] / sdenom, 1) if sdenom else None,
                          'functions_changed': sum(1 for f in funcs if f['changed'])},
            'functions': funcs, 'work_items': work, 'questlines': ql}


def render_md(b, limit=40):
    gw = b['game_wide']
    L = ['# Live co-op backlog', '',
         f"Recomputed from source ({b['source']['files_scanned']} files, {b['source']['effect_types']} Effect types, "
         f"{b['source']['hooked_commands']} hooked commands). Game-wide script calls synced: **{gw['synced_pct']}%** "
         f"(static census tables said {gw['synced_pct_static']}%; {gw['functions_changed']} functions changed status).", '',
         '| status | calls (live) | calls (static tables) |', '|---|---|---|']
    for s in ('supported', 'partial', 'missing', 'local'):
        L.append(f"| {s} | {gw['calls'].get(s, 0)} | {gw['calls_static'].get(s, 0)} |")
    L += ['', '## Work items (one change fixes several functions), by game-wide calls x severity', '',
          '| # | action | target | score | calls | severity | functions |', '|---|---|---|---|---|---|---|']
    for w in b['work_items'][:limit]:
        L.append(f"| {w['rank']} | {w['action']} | {w['target']} | {w['score']} | {w['calls']} | {w['max_severity']} | "
                 f"{', '.join(w['functions'][:6])}{'...' if len(w['functions']) > 6 else ''} |")
    L += ['', '## Functions still not fully synced', '',
          '| # | fn | calls | severity | static -> live | effect | cap | apply |', '|---|---|---|---|---|---|---|---|']
    for f in [x for x in b['functions'] if x['score'] > 0][:limit]:
        L.append(f"| {f['rank']} | {f['fn']} | {f['count']} | {f['severity']} | {f['static']} -> {f['live']} | "
                 f"{'/'.join(f['effect_types']) or '-'} | {f['captured']} | {f['applied']} |")
    L += ['', '## Per questline', '', '| questline | counted calls | synced % | partial % | blocker calls | top blockers |',
          '|---|---|---|---|---|---|']
    for slug, q in sorted(b['questlines'].items()):
        top = ', '.join(f"{r['fn']}({r['count']})" for r in q['blockers'][:4])
        L.append(f"| {slug} | {q['counted_calls']} | {q['synced_pct']} | {q['partial_pct']} | {q['blocker_calls']} | {top} |")
    return '\n'.join(L) + '\n'


def load_census(root=ROOT):
    p = os.path.join(root, 'questlines', 'systems', 'census.json')
    return json.load(open(p, encoding='utf-8'))


def write(outdir, root=ROOT, census=None, lanes=None, src=None):
    census = census or load_census(root)
    lanes = lanes if lanes is not None else load_lanes(root)
    src = src or LS.scan_sources(root)
    b = build_backlog(census, lanes, src, load_aliases(root))
    os.makedirs(outdir, exist_ok=True)
    json.dump(b, open(os.path.join(outdir, 'backlog.json'), 'w', encoding='utf-8'), indent=1)
    open(os.path.join(outdir, 'backlog.md'), 'w', encoding='utf-8').write(render_md(b))
    return b


if __name__ == '__main__':
    b = write(os.path.join(ROOT, 'out', 'coverage'))
    print('backlog: %d functions, %d work items; game-wide synced %s%% (static %s%%)' % (
        len(b['functions']), len(b['work_items']), b['game_wide']['synced_pct'], b['game_wide']['synced_pct_static']))
