"""Independent validators for every deliverable of the House lane. Each returns a list of error strings ([] = valid).

They re-derive facts from out/questdata with their own (simpler) code paths instead of reusing the generators' helpers, so a bug in
a generator cannot also hide in its validator."""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnmap  # noqa: E402
import gaps as gaps_mod  # noqa: E402
import qlib  # noqa: E402
import rules_patch  # noqa: E402

HEX = r'[0-9A-F]{6,8}'
CMD_RES = [
    re.compile(rf'^setstage ({HEX}) (\d+)$'),
    re.compile(rf'^(startquest|stopquest|completequest|getquestrunning) ({HEX})$'),
    re.compile(rf'^setobjective(?:displayed|completed) ({HEX}) (\d+) [01]$'),
    re.compile(rf'^set ({HEX}) to (-?\d+(?:\.\d+)?)$'),
    re.compile(rf'^player\.(?:additem|removeitem) ({HEX}) \d+$'),
    re.compile(rf'^player\.moveto ({HEX})$'),
]
STAGE_CMD = re.compile(r'(?i)\b(setstage)\s+(\w+)\s+(\d+)')
CITE = re.compile(r'`([A-Za-z0-9_]+(?:/[A-Za-z0-9]+)?):(\d+)(?:~([^`]+))?`')
FORMREF = re.compile(r'\b((?:FalloutNV|DeadMoney|HonestHearts|OldWorldBlues|LonesomeRoad|GunRunnersArsenal)\.esm):([0-9A-F]{6})\b')


def _strip(line):
    return qlib.strip_comment(line)


# ------------------------------------------------------------------------------------------------ source lookup (independent)
class Sources:
    """Resolves `Script:line` citations. Scripts are keyed by EDID (standalone SCPT), `Quest/stageN`, `Quest/infoHEXb|e`."""

    def __init__(self, d):
        self.d = d

    def text(self, name):
        d = self.d
        if '/' in name:
            q, part = name.split('/', 1)
            quest = d.q_by_edid.get(q)
            if not quest:
                return None
            m = re.fullmatch(r'stage(\d+)', part)
            if m:
                for st in quest['stages']:
                    if st['index'] == int(m.group(1)):
                        return '\n'.join((e.get('script') or {}).get('source', '') for e in st['entries'] if e.get('script'))
                return None
            m = re.fullmatch(r'info([0-9A-F]{6})([be])', part)
            if m:
                for i in d.infos_by_quest.get(quest['ref'], []):
                    if i['ref'] == f'FalloutNV.esm:{m.group(1)}':
                        sc = i.get('begin' if m.group(2) == 'b' else 'end')
                        return sc['source'] if sc else None
                return None
            return None
        s = d.s_by_edid.get(name.lower())
        if s:
            return s['script']['source']
        return None

    def line(self, name, n):
        t = self.text(name)
        if t is None:
            return None
        ls = t.replace('\r', '').split('\n')
        return ls[n - 1] if 1 <= n <= len(ls) else None


# ------------------------------------------------------------------------------------------------ inventory
def validate_inventory(doc, d):
    err = []
    if doc.get('slug') != qlib.SLUG:
        err.append('slug')
    want = qlib.CORE + list(qlib.SYSTEM) + list(qlib.SHARED) + list(qlib.PREREQ)
    got = [q['edid'] for q in doc['quests']]
    if sorted(got) != sorted(want):
        err.append(f'quest set differs: missing {sorted(set(want) - set(got))} extra {sorted(set(got) - set(want))}')
    src = Sources(d)
    for q in doc['quests']:
        e = q['edid']
        rq = d.q_by_edid.get(e)
        if not rq:
            err.append(f'{e}: not in quests.json')
            continue
        if q['ref'] != rq['ref']:
            err.append(f'{e}: ref {q["ref"]} != {rq["ref"]}')
        try:
            h = qlib.loadorder.console_id(rq['ref'], d.plugins)
        except KeyError:
            h = None
        if q['formid'] != h:
            err.append(f'{e}: formid {q["formid"]} != console id {h}')
        if q['name'] != rq.get('name'):
            err.append(f'{e}: name')
        if q['role'] != qlib.role_of(e):
            err.append(f'{e}: role')
        want_stages = [s['index'] for s in sorted(rq['stages'], key=lambda s: s['index'])]
        got_stages = [s['index'] for s in q['stages']]
        if got_stages != want_stages:
            err.append(f'{e}: stages {got_stages} != {want_stages}')
        for s in q['stages']:
            ents = next((x for x in rq['stages'] if x['index'] == s['index']), None)
            if ents is None:
                continue
            fl = sorted({f for en in ents['entries'] for f in en.get('flags', [])})
            if s['flags'] != fl:
                err.append(f'{e} stage {s["index"]}: flags {s["flags"]} != {fl}')
            for sb in s['set_by']:
                if sb['by'] == 'script':
                    ln = src.line(sb['owner'], sb['line'])
                elif sb['by'] == 'stage':
                    ln = src.line(f'{sb["owner"]}/stage{sb["where"].split()[1]}', sb['line'])
                else:
                    m = re.match(r'info ([0-9A-F]{6}) (begin|end)', sb['where'])
                    # owner = quest edid that holds the INFO
                    ln = src.line(f'{sb["owner"]}/info{m.group(1)}{m.group(2)[0]}', sb['line']) if m else None
                if ln is None:
                    err.append(f'{e} stage {s["index"]}: set_by {sb["owner"]} {sb["where"]}:{sb["line"]} does not exist')
                    continue
                m = STAGE_CMD.search(_strip(ln))
                if not m or m.group(2).lower() != e.lower() or int(m.group(3)) != s['index']:
                    err.append(f'{e} stage {s["index"]}: set_by {sb["owner"]} {sb["where"]}:{sb["line"]} is not `setstage {e} {s["index"]}` ({ln.strip()[:60]!r})')
        want_obj = [o['index'] for o in sorted(rq['objectives'], key=lambda o: o['index'])]
        got_obj = [o['index'] for o in q['objectives']]
        if got_obj != want_obj:
            err.append(f'{e}: objectives {got_obj} != {want_obj}')
        real_targets = {o['index']: [t['ref'] for t in o['targets']] for o in rq['objectives']}
        for o in q['objectives']:
            if [t['ref'] for t in o['targets']] != real_targets.get(o['index']):
                err.append(f'{e} objective {o["index"]}: targets differ from quests.json')
            for t in o['targets']:
                # the ref comes from quests.json itself; the EditorID (when the refnames table knows one) must match, otherwise it stays null
                if t['edid'] != d.ref_edid(t['ref']):
                    err.append(f'{e} objective {o["index"]}: target {t["ref"]} edid {t["edid"]!r} != {d.ref_edid(t["ref"])!r}')
        risk = d.risk[rq['ref']]
        if q['tier'] != risk['tier'] or q['tier_direct'] != risk['tier_direct']:
            err.append(f'{e}: tier')
        dlg = q['dialogue']
        if dlg['infos'] != len(d.infos_by_quest.get(rq['ref'], [])) or dlg['topics'] != len(rq.get('topics', [])):
            err.append(f'{e}: dialogue counts')
    owned = [q for q in doc['quests'] if q['role'] in ('core', 'system')]
    if doc['summary']['stages_owned'] != sum(len(q['stages']) for q in owned):
        err.append('summary.stages_owned')
    if doc['summary']['stages_without_setter'] != sum(1 for q in owned for s in q['stages'] if not s['set_by']):
        err.append('summary.stages_without_setter')
    return err


# ------------------------------------------------------------------------------------------------ sweep
def _check_cmd(c, d, where, err):
    cmd = c['cmd']
    m = None
    for rx in CMD_RES:
        m = rx.match(cmd)
        if m:
            break
    if not m:
        err.append(f'{where}: command not in the allowed console grammar: {cmd!r}')
        return
    refs = c.get('refs', [])
    for r in refs:
        if not d.resolves(r):
            err.append(f'{where}: ref {r} does not resolve in quests.json / forms.json.gz / refnames.json.gz')
            return
    try:
        want = [qlib.loadorder.console_id(r, d.plugins) for r in refs]
    except KeyError:
        err.append(f'{where}: ref with a plugin outside the load order')
        return
    tok = cmd.split()[1]
    if not want or tok != want[0]:
        err.append(f'{where}: hex {tok} != console id of {refs[:1]} ({want[:1]})')
    if c.get('kind') == 'global' and not c.get('fallback', '').startswith('set '):
        err.append(f'{where}: global command without an EditorID fallback')


def validate_sweep(doc, d):
    err = []
    if doc.get('slug') != qlib.SLUG:
        err.append('slug')
    owned = qlib.CORE + list(qlib.SYSTEM)
    qs = {q['edid']: q for q in doc['quests']}
    if sorted(qs) != sorted(owned):
        err.append(f'sweep quests {sorted(qs)} != owned {sorted(owned)}')
    excluded = {(x['quest'], x['stage']): x for x in doc['excluded']}
    for x in doc['excluded']:
        if not x.get('reason') or not x.get('owner'):
            err.append(f'excluded {x["quest"]} {x["stage"]}: no reason/owner')
    for e in owned:
        rq = d.q_by_edid[e]
        q = qs.get(e)
        if not q:
            continue
        if q['ref'] != rq['ref'] or q['formid'] != qlib.loadorder.console_id(rq['ref'], d.plugins):
            err.append(f'{e}: ref/formid')
        want = sorted(s['index'] for s in rq['stages'])
        got = [s['index'] for s in q['stages']]
        miss = [i for i in want if i not in got and (e, i) not in excluded]
        if miss:
            err.append(f'{e}: stages {miss} neither swept nor excluded with a reason')
        if got != sorted(got) or len(set(got)) != len(got):
            err.append(f'{e}: stage order/duplicates {got}')
        for c in q['setup']:
            _check_cmd(c, d, f'{e} setup', err)
        for s in q['stages']:
            ws = f'{e} stage {s["index"]}'
            exp = f'setstage {qlib.loadorder.console_id(rq["ref"], d.plugins)} {s["index"]}'
            if s['cmd'] != exp:
                err.append(f'{ws}: cmd {s["cmd"]!r} != {exp!r}')
            if s.get('step') and s['step'].get('host') != exp:
                err.append(f'{ws}: scenario step host {s["step"].get("host")!r} != {exp!r}')
            prev = -1
            for c in s['setup']:
                _check_cmd(c, d, f'{ws} setup', err)
                m = re.match(rf'^setstage ({HEX}) (\d+)$', c['cmd'])
                if m and m.group(1) == qlib.loadorder.console_id(rq['ref'], d.plugins):
                    if int(m.group(2)) >= s['index']:
                        err.append(f'{ws}: prerequisite stage {m.group(2)} is not earlier than the swept stage')
                    if int(m.group(2)) <= prev:
                        err.append(f'{ws}: prerequisite stages out of order')
                    prev = int(m.group(2))
            # stage entry conditions that read a global must be satisfied by a setup command
            for en in next(x for x in rq['stages'] if x['index'] == s['index'])['entries']:
                for c in en.get('conditions', []):
                    if c['func'] == 'GetGlobalValue':
                        h = qlib.loadorder.console_id(c['p1'], d.plugins)
                        if not any(sc['cmd'].startswith(f'set {h} to ') for sc in s['setup']):
                            err.append(f'{ws}: entry condition GetGlobalValue {d.ref_edid(c["p1"])} not satisfied by any setup command')
    for k in list(qlib.SHARED) + list(qlib.PREREQ):
        rq = d.q_by_edid[k]
        if rq['stages']:
            for s in rq['stages']:
                if (k, s['index']) not in excluded:
                    err.append(f'{k} stage {s["index"]}: not in excluded')
        elif (k, None) not in excluded and k in qlib.SHARED:
            err.append(f'{k}: stage-less shared container not in excluded')
    for p in doc.get('probes', []):
        for c in p['steps']:
            _check_cmd(c, d, f'probe {p["id"]}', err)
    sm = doc['summary']
    if sm['stages'] != sum(len(q['stages']) for q in doc['quests']) or sm['quests'] != len(doc['quests']):
        err.append('summary counts')
    return err


# ------------------------------------------------------------------------------------------------ gaps
def _effect_h_types():
    t = open(os.path.join(qlib.ROOT, 'synccore', 'include', 'fnvcoop', 'effect.h'), encoding='utf-8').read()
    m = re.search(r'using Effect = std::variant<([^>]*)>', t, re.S)
    return [x.strip() for x in m.group(1).split(',')]


UNAMBIGUOUS = ['moveto', 'swaptexture', 'showmessage', 'setdestroyed', 'disableplayercontrols', 'enableplayercontrols', 'enablefasttravel',
               'autosave', 'systemsave', 'startconversation', 'placeatme', 'sayto', 'addperk', 'removeperk', 'setally', 'setenemy',
               'rewardxp', 'setstage', 'startquest', 'stopquest', 'addachievement', 'setinchargen']


def validate_gaps(doc, d, uni):
    err = []
    by_id = {u['id']: u for u in uni}
    by_disp = collections.defaultdict(list)
    for u in uni:
        by_disp[(u['script'], u['where'])].append(u)
    total = 0
    for f in doc['functions']:
        spec = fnmap.lookup(f['fn'])
        if spec is None:
            err.append(f'{f["fn"]}: not in fnmap')
            continue
        total += f['count']
        sbc = f['status_by_call']
        if sum(sbc.values()) != f['count']:
            err.append(f'{f["fn"]}: status_by_call sums to {sum(sbc.values())} != count {f["count"]}')
        worst = max(sbc, key=lambda s: gaps_mod.STATUS_RANK[s])
        if f['status'] != worst:
            err.append(f'{f["fn"]}: status {f["status"]} != worst per-call {worst}')
        if f['effects'] != spec['effects'] or f['mode'] != spec['mode']:
            err.append(f'{f["fn"]}: effects/mode differ from fnmap')
        if f['status'] == 'supported' and (not f['capture'] or not f['apply']):
            err.append(f'{f["fn"]}: supported without capture/apply evidence')
        if f['status'] not in ('supported', 'partial', 'missing', 'n/a'):
            err.append(f'{f["fn"]}: bad status')
        if sum(f['count_by_lane'].values()) != f['count']:
            err.append(f'{f["fn"]}: count_by_lane')
        if len(f['locations']) != f['count'] - sbc.get('supported', 0):
            err.append(f'{f["fn"]}: {len(f["locations"])} locations listed but {f["count"] - sbc.get("supported", 0)} calls are not supported')
        for l in f['locations']:
            cands = [u for u in by_disp.get((l['script'], l['where']), []) if u['quest'] == l['quest']] or by_disp.get((l['script'], l['where']), [])
            if not cands:
                # object scripts are listed under their EDID with the dep label as `where`; match on display name only
                cands = [u for u in uni if u['script'] == l['script']]
            if not cands:
                err.append(f'{f["fn"]}: location script {l["script"]!r} not in the universe')
                continue
            ok = False
            for u in cands:
                ls = u['source'].replace('\r', '').split('\n')
                if 1 <= l['line'] <= len(ls):
                    line = _strip(ls[l['line'] - 1]).strip().lower()
                    tokens = {f['fn'], f['fn'].replace(' ', '')}
                    if f['fn'] in ('set questvar', 'set global', 'set refvar'):
                        ok |= line.startswith('set ')
                    elif f['fn'] == 'read clock':
                        ok |= any(c in line for c in qlib.CLOCKS)
                    else:
                        al = [a for a, c in qlib.ALIASES.items() if c == f['fn']] + [f['fn']]
                        ok |= any(re.search(rf'\b{re.escape(a)}\b', line) for a in al)
            if not ok:
                err.append(f'{f["fn"]}: location {l["script"]}:{l["line"]} does not contain the function')
    if total != doc['summary']['calls']:
        err.append(f'sum of counts {total} != summary.calls {doc["summary"]["calls"]}')
    # independent completeness: naive token counts for functions that cannot be confused with variables
    naive = collections.Counter()
    for u in uni:
        for raw in u['source'].replace('\r', '').split('\n'):
            line = _strip(raw)
            line = re.sub(r'"[^"]*"', '""', line).lower()
            for fn in UNAMBIGUOUS:
                naive[fn] += len(re.findall(rf'\b{fn}\b', line))
    got = {f['fn']: f['count'] for f in doc['functions']}
    for fn in UNAMBIGUOUS:
        if naive[fn] != got.get(fn, 0):
            err.append(f'completeness: `{fn}` naive count {naive[fn]} != gaps.json {got.get(fn, 0)}')
    # effect vocabulary
    et = set(_effect_h_types())
    have = {e['effect'] for e in doc['effect_vocabulary']}
    if et - have:
        err.append(f'effect.h types missing from effect_vocabulary: {sorted(et - have)}')
    for e in doc['oracle_effects']:
        if e['status'] == 'unknown':
            err.append(f'oracle effect {e["effect"]} has no L5 status')
    for t in ('TELEPORT', 'CINEMATIC', 'MENU', 'SAVE', 'FAST-TRAVEL', 'SPAWN', 'ENDING', 'KILL-ALL', 'COMPANION', 'AMBIENT', 'TIMER', 'FACTION', 'RADIO'):
        if t not in doc['hazards']:
            err.append(f'hazard tag {t} absent (the corpus contains it)')
    for u in doc['scripts']:
        if u['id'] not in by_id:
            err.append(f'scripts[]: {u["id"]} not in universe')
    return err


# ------------------------------------------------------------------------------------------------ rules patch
def validate_patch(patch, d):
    err = []
    import hashlib
    base_bytes = open(rules_patch.RULES_JSON, 'rb').read()
    if patch.get('base_sha256') != hashlib.sha256(base_bytes).hexdigest():
        err.append('rules.json changed since the patch was generated (base_sha256 differs): regenerate and re-review')
    base = json.loads(base_bytes.decode('utf-8'))
    try:
        out = rules_patch.apply(base, patch)
    except (ValueError, KeyError) as ex:
        return err + [f'apply failed: {ex}']
    touched = sorted({o['quest'] for o in patch['ops']})
    err += rules_patch.check_resolves(d, out, touched)
    for q in touched:
        kinds = [r['kind'] for r in out['quests'][q]['rules']]
        if len(kinds) != len(set(kinds)):
            err.append(f'{q}: duplicate rule kinds {kinds}')
    new_kinds = {r['kind'] for q in touched for r in out['quests'][q]['rules']} - set(rules_patch.KINDS)
    declared = {k['kind'] for k in patch.get('proposed_kinds', [])}
    if new_kinds - declared:
        err.append(f'kinds outside the 16 not declared in proposed_kinds: {sorted(new_kinds - declared)}')
    for k in patch.get('proposed_kinds', []):
        if not k.get('justification') or not k.get('code_change'):
            err.append(f'proposed kind {k["kind"]}: needs justification and code_change')
        if k['kind'] in rules_patch.KINDS:
            err.append(f'proposed kind {k["kind"]} already exists')
    params_used = collections.defaultdict(set)
    for q in touched:
        for r in out['quests'][q]['rules']:
            for p in r['params']:
                params_used[r['kind']].add(p)
    declared_params = {(p['kind'].split('/')[0], p['param']) for p in patch.get('proposed_params', [])}
    known = {k: {p for qq in base['quests'].values() for r in qq['rules'] if r['kind'] == k for p in r['params']} for k in rules_patch.KINDS}
    for kind, ps in params_used.items():
        if kind in known:
            for p in ps - known[kind]:
                if (kind, p) not in declared_params and not any(kind in pp['kind'] and pp['param'] == p for pp in patch.get('proposed_params', [])):
                    err.append(f'{kind}.{p}: param never used in rules.json and not declared in proposed_params')
    return err


# ------------------------------------------------------------------------------------------------ hand-written docs
def validate_doc(text, d, name):
    """Citations (`Script:line~needle`) and FormRefs in a markdown file must resolve."""
    err = []
    src = Sources(d)
    all_infos = {i['ref'] for infos in d.infos_by_quest.values() for i in infos}
    n = 0
    for m in CITE.finditer(text):
        n += 1
        who, ln, needle = m.group(1), int(m.group(2)), m.group(3)
        line = src.line(who, ln)
        if line is None:
            err.append(f'{name}: citation `{who}:{ln}` does not resolve (unknown script or line out of range)')
            continue
        if not line.strip():
            err.append(f'{name}: citation `{who}:{ln}` points at a blank line')
        if needle and needle.lower() not in line.lower():
            err.append(f'{name}: citation `{who}:{ln}` does not contain {needle!r} (line is {line.strip()[:70]!r})')
    for m in FORMREF.finditer(text):
        ref = f'{m.group(1)}:{m.group(2)}'
        if not d.resolves(ref) and ref not in d.q_by_ref and ref not in all_infos:
            err.append(f'{name}: FormRef {ref} does not resolve')
    return err, n
