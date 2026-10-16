"""Assemble the coverage report dict + markdown summary from data, status and critical paths."""
import datetime

from model import LADDER, TIERS, story_quests, tier_of, kind_of, questline_of
from status import label, at_least
from critical import critical_paths, tackle_order

BUCKETS = ['failing'] + LADDER[::-1]      # display order: worst first, best last in bars handled by renderer
ALL_BUCKETS = ['unknown', 'spec', 'sim-pass', 'game-pass', 'playtested', 'failing']


def _count():
    return {b: 0 for b in ALL_BUCKETS}


def build_report(data, graph, status, run_info, target='game-pass', extra=None):
    rows = []
    for q in data['quests']:
        st = status[q['edid']]
        rows.append({'edid': q['edid'], 'name': q.get('name'), 'plugin': q['plugin'], 'tier': tier_of(data, q),
                     'story': kind_of(data, q) == 'story', 'line': questline_of(data, q),
                     'bucket': label(st), 'rung': st['rung'], 'failing': st['failing'],
                     'failing_level': st['failing_level']})
    story = [r for r in rows if r['story']]
    by_tier, by_line = {}, {}
    for r in story:
        by_tier.setdefault(r['tier'], _count())[r['bucket']] += 1
        by_line.setdefault(r['line'], _count())[r['bucket']] += 1
    paths = critical_paths(data, graph)
    for end, p in paths.items():
        for q in p['quests']:
            q['bucket'] = label(status[q['edid']])
        n = len(p['quests'])
        ok = sum(1 for q in p['quests'] if at_least(status[q['edid']], target))
        p['done'], p['total'], p['ready'] = ok, n, ok == n
        p['failing'] = [q['edid'] for q in p['quests'] if status[q['edid']]['failing']]
    plan = tackle_order(data, paths, status, target, at_least)
    for s in plan:
        s['bucket'] = label(status[s['edid']])
    tot = _count()
    for r in story:
        tot[r['bucket']] += 1
    rep = {
        'generated': datetime.datetime.now().isoformat(timespec='seconds'),
        'target': target, 'runs': run_info['runs'], 'unknown_run_quests': run_info['unknown_quests'],
        'story_total': len(story), 'all_total': len(rows), 'totals': tot,
        'by_tier': {t: by_tier.get(t, _count()) for t in TIERS},
        'by_line': dict(sorted(by_line.items())),
        'paths': paths, 'plan': plan, 'quests': rows,
    }
    rep.update(extra or {})
    return rep


def render_markdown(rep):
    L = ['# FNV co-op coverage', '',
         f"Generated {rep['generated']} - {rep['story_total']} story quests ({rep['all_total']} total), "
         f"{rep['runs']} run summaries read. Target rung: **{rep['target']}**.", '',
         '## Totals (story quests)', '',
         '| ' + ' | '.join(ALL_BUCKETS) + ' |', '|' + '---|' * len(ALL_BUCKETS),
         '| ' + ' | '.join(str(rep['totals'][b]) for b in ALL_BUCKETS) + ' |', '',
         '## By tier', '', '| Tier | ' + ' | '.join(ALL_BUCKETS) + ' |', '|---|' + '---|' * len(ALL_BUCKETS)]
    for t, c in rep['by_tier'].items():
        L.append(f'| {t} | ' + ' | '.join(str(c[b]) for b in ALL_BUCKETS) + ' |')
    L += ['', '## By questline', '', '| Questline | ' + ' | '.join(ALL_BUCKETS) + ' |',
          '|---|' + '---|' * len(ALL_BUCKETS)]
    for t, c in rep['by_line'].items():
        L.append(f'| {t} | ' + ' | '.join(str(c[b]) for b in ALL_BUCKETS) + ' |')
    bl, sw = rep.get('backlog'), rep.get('sweeps')
    if bl:
        g = bl['game_wide']
        L += ['', '## Script-call sync (live source)', '',
              f"{g['synced_pct']}% of counted script calls fully synced (static tables: {g['synced_pct_static']}%). "
              f"Top work items: " + '; '.join(f"{w['action']} {w['target']} ({w['score']})" for w in bl['work_items'][:5]) +
              '. Full list: backlog.md.']
    if sw:
        L += ['', '## Stage sweeps', '', '| questline | planned | pass | fail | error | not run |', '|---|---|---|---|---|---|']
        L += [f"| {k} | {q['planned']} | {q['pass']} | {q['fail']} | {q['error']} | {q['not_run']} |"
              for k, q in sw['questlines'].items()]
    L += ['', '## Critical paths per ending', '']
    for end, p in rep['paths'].items():
        flag = 'READY' if p['ready'] else f"{p['done']}/{p['total']}"
        L.append(f"### {end} ({flag})" + (f" - FAILING: {', '.join(p['failing'])}" if p['failing'] else ''))
        L.append('')
        L.append(' -> '.join(f"{q['edid']}[{q['tier']}{'*' if q['discovered'] else ''}:{q['bucket']}]"
                              for q in p['quests']))
        L.append('')
    L += ['`*` = prerequisite discovered from the script graph (confirm by hand).', '',
          '## Fastest order to "main story fully co-op"', '',
          '| # | Quest | Tier | Cost | Needed by | Status | Completes ending |', '|---|---|---|---|---|---|---|']
    for s in rep['plan']:
        L.append(f"| {s['order']} | {s['edid']} {s['name'] or ''} | {s['tier']} | {s['cost']} | "
                 f"{', '.join(s['needed_by'])} | {s['bucket']} | {s['unlocks_ending'] or ''} |")
    return '\n'.join(L) + '\n'
