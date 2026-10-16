"""Quest dependency graph derived from scripts, info results and conditions.

Edge kinds (src -> dst):
  start/stage/complete/fail/var : src's script (quest script, stage script, or - 'soft' - an INFO result
                                  script) starts / sets a stage of / completes / writes a variable of dst.
  gate                          : dst's conditions or scripts READ src's state (GetStageDone, GetQuestCompleted,
                                  GetQuestRunning, GetStage, Objective*, src.variable), i.e. src gates dst.
Strength 'hard' = quest-level conditions/script, stage conditions/scripts. 'soft' = INFO conditions/results
(dialogue gating; noisier, still reported in questgraph.json).
"""
import re

from model import story_quests, tier_of, questline_of

QUEST_FUNCS = {'GetStageDone', 'GetStage', 'GetQuestCompleted', 'GetQuestRunning', 'GetQuestVariable',
               'GetObjectiveCompleted', 'GetObjectiveDisplayed'}
CMD_KIND = {'startquest': 'start', 'setstage': 'stage', 'completequest': 'complete', 'failquest': 'fail',
            'stopquest': 'stop', 'resetquest': 'stop', 'setobjectivecompleted': 'stage',
            'setobjectivedisplayed': 'stage'}
GATE_CMDS = {'getstagedone', 'getstage', 'getquestcompleted', 'getquestrunning', 'getobjectivecompleted',
             'getobjectivedisplayed'}


def _strip(src):
    for ln, line in enumerate(src.replace('\r', '').split('\n'), 1):
        line = line.split(';', 1)[0].strip()
        if line:
            yield ln, line


class Graph:
    def __init__(self, data):
        self.data = data
        self.edges = {}  # (src, dst, kind, strength) -> {count, evidence[]}
        names = sorted(data['by_edid'], key=len, reverse=True)
        self.low = {n.lower(): n for n in names}
        self.rx = re.compile(r'\b(' + '|'.join(re.escape(n) for n in names) + r')\b', re.I)
        self._build()

    def _add(self, src, dst, kind, strength, where, text):
        if src == dst:
            return
        e = self.edges.setdefault((src, dst, kind, strength), {'count': 0, 'evidence': []})
        e['count'] += 1
        if len(e['evidence']) < 3:
            e['evidence'].append({'where': where, 'text': text[:120]})

    def _scan_script(self, owner, src, where, strength):
        if not src:
            return
        for ln, line in _strip(src):
            toks = line.split()
            first = toks[0].lower()
            if first in CMD_KIND and len(toks) > 1:
                m = self.low.get(toks[1].lower())
                if m and CMD_KIND[first] != 'stop':
                    self._add(owner, m, CMD_KIND[first], strength, f'{where}:{ln}', line)
                continue
            for m in self.rx.finditer(line):
                name = self.low[m.group(1).lower()]
                if name == owner:
                    continue
                pre = line[:m.start()].lower().split()
                after = line[m.end():m.end() + 1]
                if pre and pre[-1].strip('(') in GATE_CMDS:
                    self._add(name, owner, 'gate', strength, f'{where}:{ln}', line)
                elif after == '.':
                    if re.match(r'set\s+' + re.escape(m.group(1)) + r'\.', line, re.I):
                        self._add(owner, name, 'var', strength, f'{where}:{ln}', line)
                    else:
                        self._add(name, owner, 'gate', strength, f'{where}:{ln}', line)

    def _scan_conds(self, owner, conds, where, strength):
        for c in conds or []:
            if c.get('func') in QUEST_FUNCS:
                t = self.data['by_ref'].get(c.get('p1'))
                if t and t['edid'] != owner:
                    self._add(t['edid'], owner, 'gate', strength, where, f"{c['func']} {t['edid']}")

    def _build(self):
        d = self.data
        for q in d['quests']:
            e = q['edid']
            self._scan_conds(e, q.get('conditions'), 'quest.cond', 'hard')
            sd = q.get('script_data')
            if sd:
                self._scan_script(e, sd.get('source'), 'quest script', 'hard')
            for st in q['stages']:
                for en in st['entries']:
                    self._scan_conds(e, en.get('conditions'), f"stage {st['index']} cond", 'hard')
                    s = en.get('script')
                    if s:
                        self._scan_script(e, s.get('source'), f"stage {st['index']}", 'hard')
            for inf in d['infos'].get(q['ref'], []):
                self._scan_conds(e, inf.get('conditions'), 'info cond', 'soft')
                for part in ('begin', 'end'):
                    s = inf.get(part)
                    if s:
                        self._scan_script(e, s.get('source'), f"info {inf['formid']} {part}", 'soft')

    def adjacency(self, strengths=('hard',), kinds=None):
        """src -> {dst}: 'src makes dst reachable' (start/stage/... and gate edges alike)."""
        adj = {}
        for (s, t, k, st) in self.edges:
            if st in strengths and (kinds is None or k in kinds):
                adj.setdefault(s, set()).add(t)
        return adj

    def to_json(self):
        story = {q['edid'] for q in story_quests(self.data)}
        nodes = [{'id': q['edid'], 'name': q.get('name'), 'plugin': q['plugin'], 'tier': tier_of(self.data, q),
                  'story': q['edid'] in story, 'line': questline_of(self.data, q)} for q in self.data['quests']]
        edges = [{'src': s, 'dst': t, 'kind': k, 'strength': st, 'count': v['count'], 'evidence': v['evidence']}
                 for (s, t, k, st), v in sorted(self.edges.items())]
        return {'schema': 1, 'nodes': nodes, 'edges': edges}


def depth_from(adj, roots):
    dist = {r: 0 for r in roots}
    fr = list(roots)
    while fr:
        nxt = []
        for n in fr:
            for m in adj.get(n, ()):
                if m not in dist:
                    dist[m] = dist[n] + 1
                    nxt.append(m)
        fr = nxt
    return dist


def ancestors(g, seeds, allowed, strengths=('hard',), max_depth=None):
    """Reverse closure over edges restricted to `allowed`; returns {edid: hops_from_seed}."""
    rev = {}
    for (s, t, k, st) in g.edges:
        if st in strengths and s in allowed and t in allowed:
            rev.setdefault(t, set()).add(s)
    seen = {s: 0 for s in seeds}
    fr = list(seeds)
    while fr:
        nxt = []
        for n in fr:
            if max_depth is not None and seen[n] >= max_depth:
                continue
            for m in rev.get(n, ()):
                if m not in seen:
                    seen[m] = seen[n] + 1
                    nxt.append(m)
        fr = nxt
    return seen
