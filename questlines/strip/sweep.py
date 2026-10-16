"""sweep.json: the m1_goodsprings.json format (L7 plan loader: quests[].edid/ref/stages[].index/tier_direct) plus a
`setup` list per quest and per stage, hazard flags and explicit exclusions.

setup = ordered console lines (hex FormIDs only, commands that exist in FNV/xNVSE) needed so that `setstage <q> <n>` runs
in the state the stage script expects. Derivation (heuristic, labelled per line in setup_meta):
  quest level   : `startquest` (when not start-enabled), cross-quest hard prerequisites from quest/stage-entry conditions
                  and the L3 scenario preconditions.
  stage level   : positive reads inside the stage's own script: GetStageDone / GetStage (same quest, lower stage; other quests),
                  GetObjectiveDisplayed/Completed == 1, GetQuestCompleted/GetQuestRunning == 1.
Reads of quest *variables* cannot be set from setstage-style lines; they are listed in `unresolved` instead of guessed.
"""
import collections
import re

import classify
import cmdtable as ct
import scope
import scriptscan

CMP = r'(==|!=|>=|<=|>|<)'
RE_STAGEDONE = re.compile(r'\bgetstagedone\s+(\w+)\s+(\d+)\s*(?:' + CMP + r'\s*(\d+))?', re.I)
RE_STAGE = re.compile(r'\bgetstage\s+(\w+)\s*' + CMP + r'\s*(\d+)', re.I)
RE_OBJ = re.compile(r'\bgetobjective(displayed|completed)\s+(\w+)\s+(\d+)\s*(?:' + CMP + r'\s*(\d+))?', re.I)
RE_QSTATE = re.compile(r'\bgetquest(completed|running)\s+(\w+)\s*(?:' + CMP + r'\s*(\d+))?', re.I)
RE_QVAR = re.compile(r'\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*(==|!=|>=|<=|>|<)\s*(-?\d+)', re.I)
ITEM_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(additem|removeitem|additemns|equipitem)\s+(\w+)(?:\s+(-?\d+))?')
TOGGLE_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(enable|disable)\b(?!\w)')
SETSTAGE_RE = re.compile(r'(?im)\bsetstage\s+(\w+)\s+(\d+)')
OBJ_RE = re.compile(r'(?im)\bsetobjective(displayed|completed)\s+(\w+)\s+(\d+)\s+([01])')
RISK_FNS = {'disableplayercontrols': 'controls-lock', 'startconversation': 'forced-dialogue', 'autosave': 'save',
            'systemsave': 'save', 'placeatme': 'spawn', 'setplayerteammate': 'companion', 'showbartermenu': 'menu',
            'movetomarkerwithfade': 'teleport', 'enablefasttravel': 'fast-travel'}


def truthy(op, val, default=True):
    """Is `READ op val` satisfied when the thing is TRUE (flag == 1)? op None = bare condition."""
    if op is None:
        return default
    v = int(val)
    return (op == '==' and v == 1) or (op == '>=' and v >= 1) or (op == '>' and v >= 0) or (op == '!=' and v == 0)


class Setup:
    def __init__(self, data):
        self.d = data

    def line(self, cmd, quest_edid, *args):
        q = self.d.by_edid[quest_edid]
        return ' '.join([cmd, scope.console_hex(q['ref'])] + [str(a) for a in args])

    def from_source(self, src, qedid, stage):
        """-> (lines, meta, unresolved) of prerequisites read from one stage script."""
        found, unresolved = [], []
        seen = set()

        def add(cmd, q, args, why, order):
            if q not in self.d.by_edid:
                return
            ln = self.line(cmd, q, *args)
            if ln not in seen:
                seen.add(ln)
                found.append((order, ln, dict(line=ln, why=why, confidence='heuristic', quest=q)))
        self_set = {(m.group(2).lower(), int(m.group(3))) for m in OBJ_RE.finditer(src)}
        for raw in src.replace('\r', '').split('\n'):
            text = scriptscan.strip_comment(raw)
            for m in RE_STAGEDONE.finditer(text):
                q, k, op, v = m.group(1), int(m.group(2)), m.group(3), m.group(4)
                if truthy(op, v if v is not None else 1) and not (q == qedid and k >= stage):
                    add('setstage', q, [k], f'{text.strip()[:70]}', (q != qedid, k))
            for m in RE_STAGE.finditer(text):
                q, op, k = m.group(1), m.group(2), int(m.group(3))
                if op in ('>=', '==') and not (q == qedid and k >= stage):
                    add('setstage', q, [k], f'{text.strip()[:70]}', (q != qedid, k))
            for m in RE_OBJ.finditer(text):
                kind, q, n, op, v = m.group(1), m.group(2), int(m.group(3)), m.group(4), m.group(5)
                if truthy(op, v if v is not None else 1) and (q.lower(), n) not in self_set:
                    add('setobjective' + kind.lower(), q, [n, 1], f'{text.strip()[:70]}', (q != qedid, 10 ** 6 + n))
            for m in RE_QSTATE.finditer(text):
                kind, q, op, v = m.group(1), m.group(2), m.group(3), m.group(4)
                if truthy(op, v if v is not None else 1) and q != qedid:
                    add('completequest' if kind == 'completed' else 'startquest', q, [], f'{text.strip()[:70]}', (True, 10 ** 7))
            for m in RE_QVAR.finditer(text):
                q, var, op, v = m.groups()
                if q in self.d.by_edid and not text.strip().lower().startswith(('set ', 'let ')):
                    unresolved.append(f'{q}.{var} {op} {v} (quest variable read in `{text.strip()[:60]}`)')
        found.sort(key=lambda t: t[0])
        return [ln for _, ln, _ in found], [m for _, _, m in found], sorted(set(unresolved))

    def quest_level(self, edid):
        q = self.d.by_edid[edid]
        lines, meta = [], []
        scenario = self.d.scenario(edid) or {}
        if 'start_enabled' not in (q.get('flags') or []):
            ln = self.line('startquest', edid)
            lines.append(ln)
            meta.append(dict(line=ln, why='quest is not start-enabled', confidence='certain', quest=edid))
        conds = [('quest', c) for c in q['conditions']]
        for s in q['stages']:
            for e in s['entries']:
                conds += [(f'stage {s["index"]}', c) for c in e['conditions']]
        pre = (scenario.get('preconditions') or {}).get('quest_conditions', [])
        conds += [('scenario', dict(c, run_on='subject', fn=0)) for c in pre]
        for where, c in conds:
            other = self.d.by_ref.get(c.get('p1')) if c['func'] in classify.QUEST_DEP_FUNCS else None
            if not other or other['ref'] == q['ref'] or not other.get('edid'):
                continue
            op, val, a = c.get('op'), c.get('value'), c.get('p2')
            if c['func'] == 'GetStageDone' and truthy(op, val):
                ln = self.line('setstage', other['edid'], a)
            elif c['func'] == 'GetStage' and op in ('>=', '==') and isinstance(a, int):
                ln = self.line('setstage', other['edid'], int(val))
            elif c['func'] == 'GetQuestCompleted' and truthy(op, val):
                ln = self.line('completequest', other['edid'])
            elif c['func'] == 'GetQuestRunning' and truthy(op, val):
                ln = self.line('startquest', other['edid'])
            elif c['func'] in ('GetObjectiveDisplayed', 'GetObjectiveCompleted') and truthy(op, val):
                ln = self.line('setobjective' + c['func'][12:].lower(), other['edid'], a, 1)
            else:
                continue
            if ln not in lines:
                lines.append(ln)
                meta.append(dict(line=ln, why=f'{where}: {c["func"]} {other["edid"]} {a} {op} {val}',
                                 confidence='from-conditions', quest=other['edid']))
        return lines, meta


def stage_risks(stage_scripts):
    out = set()
    for sc in stage_scripts:
        for st in scriptscan.scan(sc):
            if st['kind'] != 'cmd':
                continue
            if st['fn'] in RISK_FNS:
                out.add(RISK_FNS[st['fn']])
            if st['fn'] in ('moveto', 'movetomarker') and (st.get('subject') or '').lower() in ct.SUBJECT_PLAYER:
                out.add('player-teleport')
            if st['fn'] == 'setrestrained':
                out.add('controls-lock')
    return sorted(out)


def oracle_of(data, edid, idx):
    st = data.native['quests'].get(edid, {}).get('stages', {}).get(str(idx))
    if not st:
        return dict(unconditional=[], conditional=0)
    return dict(unconditional=sorted({e['type'] for e in st['unconditional']}), conditional=len(st['conditional']))


def build(data, inventory):
    su = Setup(data)
    risk = data.risk
    qout, agg_globals, agg_items = [], set(), {}
    agg_npcs, agg_objs, agg_toggle, agg_cross = {}, {}, {}, set()
    glob_names = {g['edid'].lower(): g['edid'] for g in data.d['globals'] if g.get('edid')}
    touched = {}
    exclusions = []
    members = [e for e, _, _ in data.members()]
    for qinv in inventory['quests']:
        edid = qinv['edid']
        q = data.by_edid[edid]
        infos = data.infos_by_quest.get(q['ref'], [])
        rq = risk[q['ref']]
        scr = []
        for rec in data.scripts_of_quest(edid):
            scr.append((rec['label'], dict(source=rec['source'], refs=rec['refs'])))
        gl, items, toggles, subj, setstages, objs = set(), {}, {}, set(), set(), []
        for where, sc in scr:
            src = sc['source']
            for m in re.finditer(r'(?im)^\s*set\s+(\w+)\s+to\b', src):
                if m.group(1).lower() in glob_names:
                    gl.add(glob_names[m.group(1).lower()])
            for r in sc['refs']:
                if r in data.globals and data.globals[r].get('edid'):
                    gl.add(data.globals[r]['edid'])
            for who, fn, item, n in ITEM_RE.findall(src):
                key = (item, fn.lower(), (who or 'self').lower())
                items[key] = items.get(key, 0) + 1
            for who, fn in TOGGLE_RE.findall(src):
                toggles.setdefault((who or 'self'), set()).add(fn.lower())
            subj.update(x for x in re.findall(r'(?m)\b([A-Za-z_]\w*)\.(?=[A-Za-z])', src) if x.lower() not in ('player', 'playerref'))
            for qn, idx in SETSTAGE_RE.findall(src):
                if qn != edid:
                    setstages.add((qn, int(idx)))
            if where.startswith('stage'):
                for kind, qn, n, v in OBJ_RE.findall(src):
                    objs.append(dict(stage=where.replace(f'{edid}:', ''), kind=kind.lower(), quest=qn, objective=int(n), value=int(v)))
        npcs = {}
        for i in infos:
            if i.get('speaker'):
                npcs[i['speaker']] = i['speaker']
        for o in q['objectives']:
            for t in o['targets']:
                if t['ref']:
                    npcs[t['ref']] = t['ref']
        for k, nref in npcs.items():
            f = data.forms.get(nref) or {}
            npcs[k] = dict(ref=nref, sig=f.get('sig'), edid=f.get('edid'), name=f.get('name'))
        agg_globals |= gl
        for (item, fn, who), n in items.items():
            agg_items.setdefault(item, {}).setdefault(f'{who}.{fn}', 0)
            agg_items[item][f'{who}.{fn}'] += n
        for w, fns in toggles.items():
            agg_toggle.setdefault(w, set()).update(fns)
        for r, v in npcs.items():
            (agg_npcs if (v.get('sig') in ('NPC_', 'CREA', 'ACHR', 'ACRE')) else agg_objs)[r] = v
        agg_cross |= {f'{a} {b}' for a, b in setstages}
        # ---- setup ----------------------------------------------------------------------------------------------
        qlines, qmeta = su.quest_level(edid)
        stage_entries = []
        stage_scripts = collections.defaultdict(list)
        for s in sorted(q['stages'], key=lambda s: s['index']):
            for e in s['entries']:
                if e.get('script') and e['script'].get('source'):
                    stage_scripts[s['index']].append(e['script']['source'])
        for sinv in qinv['stages']:
            idx = sinv['index']
            srcs = stage_scripts.get(idx, [])
            lines, meta, unres = [], [], []
            for src in srcs:
                l_, m_, u_ = su.from_source(src, edid, idx)
                for ln, mm in zip(l_, m_):
                    if ln not in lines:
                        lines.append(ln)
                        meta.append(mm)
                unres += u_
            stage_entries.append(dict(
                index=idx, log=sinv['log'], flags=sinv['flags'], has_script=sinv['has_script'],
                findings=[dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text'])
                          for f in rq['stage_findings'] if f['where'] == f'stage {idx}'],
                objective_effects=[o for o in objs if o['stage'] == f'stage {idx}'],
                sweep=True, exclude_reason=None, risk=stage_risks(srcs), oracle=oracle_of(data, edid, idx),
                setup=lines, setup_meta=meta, unresolved=sorted(set(unres)),
                command=f'setstage {qinv["console_id"]} {idx}'))
        if not stage_entries:
            exclusions.append(dict(quest=edid, stage=None, reason='container quest: no stages or objectives (dialogue / scripts only); '
                                   'covered by gaps.json, dialogue.md and the casino/system scenarios'))
        cutscene = any(set(se['risk']) & {'controls-lock', 'player-teleport', 'forced-dialogue'} for se in stage_entries)
        qout.append(dict(
            edid=edid, ref=q['ref'], formid=qinv['formid'], console_id=qinv['console_id'], name=q.get('name'),
            role={'core': 'core', 'extended': 'extended', 'system': 'system'}[qinv['group']],
            tier=rq['tier'], tier_direct=rq['tier_direct'], cats=rq['cats'], priority=q.get('priority'), flags=q.get('flags'),
            script=q.get('script'), order_last=cutscene, setup=qlines, setup_meta=qmeta,
            stages=stage_entries,
            objectives=[dict(index=o['index'], text=o.get('text'), targets=[t['ref'] for t in o['targets']]) for o in q['objectives']],
            globals=sorted(gl), items=[dict(item=i, fn=f, subject=w, occurrences=n) for (i, f, w), n in sorted(items.items())],
            enable_disable={w: sorted(f) for w, f in sorted(toggles.items())}, script_subjects=sorted(subj),
            setstage_other_quests=sorted(f'{a} {b}' for a, b in setstages),
            npcs=sorted(npcs.values(), key=lambda v: v['ref']),
            dialogue=dict(infos=len(infos), scripted_infos=sum(1 for i in infos if i.get('begin') or i.get('end')),
                          speakers=sorted({i['speaker'] for i in infos if i.get('speaker')}), topics=len(q.get('topics', [])),
                          related_infos=qinv['dialogue']['related_infos'])))
    # one-hop touched quests (same rule as tools/questdata/m1.py)
    by_ref = data.by_ref
    for qo in qout:
        q = data.by_edid[qo['edid']]
        for rec in data.scripts_of_quest(qo['edid']):
            for r in rec['refs']:
                if r in by_ref and r != q['ref']:
                    touched.setdefault(by_ref[r]['edid'], set()).add(f'{qo["edid"]}: script ref')
            for qn, _ in SETSTAGE_RE.findall(rec['source']):
                if qn in data.by_edid and qn != qo['edid']:
                    touched.setdefault(qn, set()).add(f'{qo["edid"]}: setstage')
    for e in members:
        touched.pop(e, None)
    summary = dict(
        quests=len(qout), stages=sum(len(q['stages']) for q in qout), objectives=sum(len(q['objectives']) for q in qout),
        infos=sum(q['dialogue']['infos'] for q in qout), globals=sorted(agg_globals),
        items={k: v for k, v in sorted(agg_items.items())},
        enable_disable_subjects={k: sorted(v) for k, v in sorted(agg_toggle.items())},
        cross_quest_setstage=sorted(agg_cross), npcs=sorted(agg_npcs.values(), key=lambda v: v['ref']),
        objects=sorted(agg_objs.values(), key=lambda v: v['ref']))
    return dict(
        milestone='Q-strip Freeside/Strip', slug=scope.SLUG,
        core={q['edid']: q['name'] for q in qout if q['role'] in ('core', 'extended') and q['stages']},
        system_quests=[q['edid'] for q in qout if q['role'] == 'system'],
        touched_quests=[dict(edid=e, name=data.by_edid[e].get('name'), stages=len(data.by_edid[e]['stages']), why=sorted(w))
                        for e, w in sorted(touched.items())],
        summary=summary, exclusions=exclusions,
        notes=['setup lines use hex FormIDs (console cannot resolve EditorIDs); DLC forms would carry their load-order prefix '
               '(none in this questline)',
               'setup is heuristic: see setup_meta[].confidence; quest-variable gates are in stages[].unresolved, never guessed',
               'order_last=true marks quests whose stages teleport/lock the player or force dialogue (run them last, as M1 does for VCG01/VCG00)'],
        quests=qout)
