"""sweep.json: the L7 stage-sweep plan in the m1_goodsprings.json shape (plugin/snapshot/tools/goodsprings_sweep.load_plan reads
`quests[].edid/ref/stages[].index/tier`), extended with per-quest and per-stage console `setup` lists.

Console rules: hex FormIDs only (EditorIDs do not resolve), unpadded for plugin 0 exactly like the proven M1 sweep, 8 digits otherwise.
Commands used (all vanilla/xNVSE): startquest, setstage, setobjectivedisplayed, set <quest-or-global-hex>[.var] to <n>.
`set <hex>.<var> to` is the only form unverified in the sweep harness: such commands carry verify=true.
"""
import collections
import re

import fnmap
import lib

# stages we deliberately do not sweep: {(quest, stage): reason}. Every entry must carry a reason (run_tests checks).
EXCLUDE = {}

ORDER_LAST_HAZARDS = ('CONTROLS', 'TELEPORT', 'FORCED_DIALOGUE', 'SPAWN')
GUARD_RX = re.compile(r'\b(GetStageDone|GetStage|GetQuestRunning|GetQuestCompleted|GetObjectiveDisplayed|GetObjectiveCompleted|'
                      r'GetQuestVariable|GetQR|GetQC)\b', re.I)


def hexcmd(D, ref):
    return '%X' % int(D.hexid(ref), 16)


def _src(sc):
    return sc['source'] if sc else ''


def stage_sources(q, idx):
    return [e['script']['source'] for s in q['stages'] if s['index'] == idx for e in s['entries'] if e.get('script')]


def objective_chain(q, idx, depth=0, seen=None):
    """Stages of the same quest that must precede `idx` so that every objective it completes is displayed first."""
    seen = seen if seen is not None else set()
    if depth > 6 or idx in seen:
        return []
    seen.add(idx)
    edid = q['edid']
    srcs = '\n'.join(stage_sources(q, idx))
    needed = set(int(n) for n in re.findall(r'setobjectivecompleted\s+%s\s+(\d+)' % re.escape(edid), srcs, re.I))
    out = []
    for n in sorted(needed):
        cands = []
        for s in q['stages']:
            if s['index'] >= idx:
                continue
            ss = '\n'.join(stage_sources(q, s['index']))
            if re.search(r'setobjectivedisplayed\s+%s\s+%d\s+1\b' % (re.escape(edid), n), ss, re.I) or \
               re.search(r'setobjectivedisplayed\s+%s\s+%d\s*$' % (re.escape(edid), n), ss, re.I | re.M):
                cands.append(s['index'])
        if cands:
            t = min(cands)
            for x in objective_chain(q, t, depth + 1, seen) + [t]:
                if x not in out:
                    out.append(x)
    return sorted(set(out))


def cmd(op, text, why, verify=False):
    return {'cmd': text, 'op': op, 'why': why, 'verify': verify}


def entry_setup(D, q, ent):
    """Console commands that satisfy an entry's own conditions (quest vars of this quest, other quests' stages, globals)."""
    out, unmet = [], []
    for c in ent['conditions']:
        p1, fn, v, op = c.get('p1'), c['func'], c['value'], c['op']
        if fn == 'GetQuestVariable' and p1 == q['ref'] and op == '==':
            name = lib.quest_var_name(q, c['p2'])
            if name:
                out.append(cmd('set', 'set %s.%s to %d' % (hexcmd(D, q['ref']), name, int(v)),
                               'entry condition: quest var %s == %d' % (name, int(v)), True))
                continue
        if fn in ('GetStageDone',) and p1 in D.by_ref and op == '==' and v == 1:
            out.append(cmd('setstage', 'setstage %s %d' % (hexcmd(D, p1), c['p2']), 'entry condition: %s stage %d done' % (
                D.by_ref[p1]['edid'], c['p2'])))
            continue
        unmet.append('%s(%s,%s) %s %s' % (fn, D.edid_of(p1) if isinstance(p1, str) else p1, c.get('p2'), op, v))
    return out, unmet


def build(D, edids, gap_res):
    rows_by_stage = collections.defaultdict(list)
    for r in gap_res['rows']:
        u, d = r['unit'], r['d']
        if u['scope'] != 'stage' or d['severity'] not in ('high', 'medium'):
            continue
        if fnmap.overall(d['capture'], d['apply']) in ('missing', 'partial'):
            rows_by_stage[(u['quest'], u['stage'])].append(r)
    quests, excluded = [], []
    reset = set()
    cross = collections.Counter()
    n_stage = 0
    for e in edids:
        q = D.by_edid[e]
        reset.add(q['ref'])
        qsetup = []
        if 'start_enabled' not in (q.get('flags') or []):
            qsetup.append(cmd('startquest', 'startquest %s' % hexcmd(D, q['ref']), 'quest is not start_enabled'))
        stages = []
        phase = 'normal'
        for s in sorted(q['stages'], key=lambda s: s['index']):
            idx = s['index']
            srcs = stage_sources(q, idx)
            sc_refs = {r for en in s['entries'] if en.get('script') for r in en['script']['refs'] if r in D.by_ref and r != q['ref']}
            for r in sc_refs:
                reset.add(r)
                cross[D.by_ref[r]['edid']] += 1
            chain = objective_chain(q, idx)
            setup = [cmd('setstage', 'setstage %s %d' % (hexcmd(D, q['ref']), t),
                         'stage %d displays an objective that stage %d completes' % (t, idx)) for t in chain]
            variants, unmet_all = [], []
            multi = len(s['entries']) > 1
            for ei, en in enumerate(s['entries']):
                es, unmet = entry_setup(D, q, en)
                unmet_all += unmet
                if multi:
                    variants.append({'entry': ei, 'log': en.get('text'), 'setup': es, 'default': ei == 0 or not en['conditions']})
                else:
                    setup += es
            guards = []
            for src in srcs:
                for n, raw in enumerate(src.splitlines(), 1):
                    line = raw.split(';')[0].strip()
                    if re.match(r'(?i)(if|elseif)\b', line) and GUARD_RX.search(line):
                        guards.append({'line': n, 'text': line[:140]})
            hz = sorted({h for r in rows_by_stage.get((e, idx), ()) for h in r['d']['hazards']})
            if set(hz) & set(ORDER_LAST_HAZARDS):
                phase = 'last'
            ex = EXCLUDE.get((e, idx))
            st = {'index': idx, 'log': next((en.get('text') for en in s['entries'] if en.get('text')), None),
                  'flags': sorted({f for en in s['entries'] for f in en['flags']}),
                  'has_script': bool(srcs), 'setup': setup, 'variants': variants, 'branch_guards': guards[:8],
                  'unmet_entry_conditions': unmet_all, 'hazards': hz}
            if ex:
                st['excluded'] = ex
                excluded.append({'quest': e, 'stage': idx, 'reason': ex})
            stages.append(st)
            n_stage += 1
        quests.append({'edid': e, 'ref': q['ref'], 'formid': D.hexid(q['ref']), 'name': q.get('name'),
                       'role': 'core', 'tier': D.risk[q['ref']]['tier'], 'tier_direct': D.risk[q['ref']]['tier_direct'],
                       'priority': q.get('priority'), 'flags': q.get('flags'), 'phase': phase, 'setup': qsetup,
                       'stages': stages})
    quests.sort(key=lambda q: (q['phase'] == 'last', q['edid']))
    return {
        'schema': 1, 'milestone': 'Act 2 shared main quests (act2)', 'core': {e: D.by_edid[e].get('name') for e in edids},
        'system_quests': sorted(cross),
        'reset_quests': sorted(hexcmd(D, r) for r in reset),
        'reset_quests_note': 'pass to test.quest.reset: the 14 quests plus every other quest their stage scripts touch',
        'format': {
            'quests[].setup': 'ordered console commands to run once per quest from a clean state (before any stage setup)',
            'quests[].stages[].setup': 'ordered console commands run before `setstage <quest> <index>` to reach that stage',
            'quests[].stages[].variants': 'stages with several log entries: per-entry condition setup (default entry has default=true)',
            'quests[].stages[].branch_guards': 'if/elseif lines in the stage script that read other quest state (branch coverage hints)',
            'quests[].phase': '"last" = stages with control locks/teleports/forced dialogue/spawns, run after the normal quests',
            'excluded': 'stages deliberately not swept, each with a reason',
        },
        'summary': {'quests': len(quests), 'stages': n_stage, 'stages_swept': n_stage - len(excluded), 'excluded': len(excluded)},
        'excluded': excluded,
        'quests': quests,
    }
