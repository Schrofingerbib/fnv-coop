"""Shared loaders for the act2 questline (Ring-a-Ding-Ding, the shared Act 2 main quests).

Everything here is read-only over the L3/L9 data products. Reuses tools/questdata (classify, effects) so the ESM parser,
the lexicon and the effect oracle are never re-implemented.

`Data(root)` loads from any directory laid out like the repo (so run_tests can point it at a pinned copy).
"""
import gzip
import hashlib
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools' / 'questdata'))
import classify  # noqa: E402
import effects as eff_mod  # noqa: E402

SLUG = 'act2'
QUESTLINE = 'Main: Shared (Ring-a-Ding-Ding and Act 2)'
PLAYER_REFS = ('FalloutNV.esm:000014', 'FalloutNV.esm:000007')

# files (relative to a data root) that feed the generators; hashed into every output header
INPUTS = ['out/questdata/quests.json', 'out/questdata/infos.jsonl.gz', 'out/questdata/scripts.json.gz',
          'out/questdata/forms.json.gz', 'out/questdata/refnames.json.gz', 'out/questdata/effects.json.gz',
          'out/questdata/effects_native.json.gz', 'out/questdata/risk.json', 'out/questdata/terms.json.gz',
          'out/questdata/packs.json.gz', 'out/questdata/dials.json.gz', 'out/coverage/questgraph.json', 'docs/quest-policies/rules.json',
          'docs/quest-policies/policies.json', 'tools/questdata/loadorder.json']


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


def _gz(path):
    with gzip.open(path, 'rb') as f:
        return json.loads(f.read().decode('utf-8'))


class Data:
    def __init__(self, root=REPO):
        self.root = Path(root)
        r = self.root
        qd = r / 'out' / 'questdata'
        self.quests_doc, self.infos_by_quest = classify.load(str(qd))
        self.quests = self.quests_doc['quests']
        self.by_edid = {q['edid']: q for q in self.quests if q.get('edid')}
        self.by_ref = {q['ref']: q for q in self.quests}
        self.globals = {g['ref']: g for g in self.quests_doc['globals']}
        self.globals_by_edid = {g['edid'].lower(): g['ref'] for g in self.quests_doc['globals'] if g.get('edid')}
        self.extra = classify.load_extra(str(qd))
        self.forms = self.extra['forms']
        self.refnames = _gz(qd / 'refnames.json.gz')
        self.effects = _gz(qd / 'effects.json.gz')
        self.native = _gz(qd / 'effects_native.json.gz')
        self.risk = json.load(open(qd / 'risk.json', encoding='utf-8'))['quests']
        self.graph = json.load(open(r / 'out' / 'coverage' / 'questgraph.json', encoding='utf-8'))
        self.rules = json.load(open(r / 'docs' / 'quest-policies' / 'rules.json', encoding='utf-8'))
        self.policies = json.load(open(r / 'docs' / 'quest-policies' / 'policies.json', encoding='utf-8'))
        self.loadorder = json.load(open(r / 'tools' / 'questdata' / 'loadorder.json', encoding='utf-8'))['plugins']
        self.infos_all = []
        with gzip.open(qd / 'infos.jsonl.gz', 'rb') as f:
            for ln in f:
                self.infos_all.append(json.loads(ln))
        self.info_by_ref = {i['ref']: i for i in self.infos_all}
        self.kq = {q['edid'].lower() for q in self.quests if q.get('edid')}
        self.kg = {g['edid'].lower() for g in self.quests_doc['globals'] if g.get('edid')}
        self.input_hashes = {p: sha(r / p) for p in INPUTS}
        self._edid_index = None

    # ---- ids -----------------------------------------------------------------------------------------------
    def hexid(self, ref):
        """'FalloutNV.esm:1344E8' -> '001344E8' (runtime FormID using the observed load order)."""
        plugin, local = ref.rsplit(':', 1)
        return '%02X%06X' % (self.loadorder.index(plugin), int(local, 16))

    def edid_of(self, ref):
        if ref in self.by_ref:
            return self.by_ref[ref].get('edid')
        if ref in self.globals:
            return self.globals[ref].get('edid')
        f = self.forms.get(ref) or self.refnames.get(ref)
        return f.get('edid') if f else None

    def sig_of(self, ref):
        if ref in self.by_ref:
            return 'QUST'
        if ref in self.globals:
            return 'GLOB'
        f = self.forms.get(ref) or self.refnames.get(ref)
        return f.get('sig') if f else None

    def resolves(self, ref):
        """A ref resolves when quests.json (quests/globals) or forms.json.gz knows it (player constants allowed)."""
        return ref in PLAYER_REFS or ref in self.by_ref or ref in self.globals or ref in self.forms or ref in self.refnames

    def ref_of_edid(self, edid):
        """Reverse lookup EditorID -> ref across quests, globals, forms and refnames (None if unknown or ambiguous)."""
        if self._edid_index is None:
            idx = {}
            for src in (self.refnames, self.forms):
                for r, f in src.items():
                    e = f.get('edid')
                    if e:
                        idx.setdefault(e.lower(), set()).add(r)
            for r, q in self.by_ref.items():
                if q.get('edid'):
                    idx.setdefault(q['edid'].lower(), set()).add(r)
            for r, g in self.globals.items():
                if g.get('edid'):
                    idx.setdefault(g['edid'].lower(), set()).add(r)
            self._edid_index = idx
        s = self._edid_index.get(edid.lower())
        return next(iter(s)) if s and len(s) == 1 else None

    def questline_edids(self):
        q = self.rules['quests']
        return sorted(e for e, v in q.items() if v['questline'] == QUESTLINE)

    def risk_of(self, edid):
        return self.risk[self.by_edid[edid]['ref']]

    def name_map(self, sc):
        return eff_mod.name_map(sc, self.refnames)


# ---- script units -------------------------------------------------------------------------------------------------
def info_id(info):
    return info['ref'].split(':')[1]


def dep_script_refs(data, q):
    """(script_ref, edid, label, touched) for the object/terminal scripts the quest depends on (same selection as risk.json)."""
    out, seen = [], set()
    touched = set(data.effects['quest_touched'].get(q['ref'], [])) | {t['ref'] for o in q['objectives'] for t in o['targets']}
    scpt, forms = data.extra['scpt'], data.extra['forms']
    for dr in q.get('dep_refs', ()):
        cands = [dr]
        f = forms.get(dr)
        if f and f.get('base'):
            cands.append(f['base'])
        for c in cands:
            fc = forms.get(c) or {}
            sref = fc.get('script')
            if sref and sref in scpt and 'script' in scpt[sref] and sref not in seen and scpt[sref]['script'].get('type') != 'quest':
                seen.add(sref)
                out.append((sref, scpt[sref].get('edid') or sref, 'obj ' + (fc.get('edid') or c), dr in touched,
                            fc.get('edid') or c, dr))
    return out


def units_for_quest(data, q):
    """All scripts that run for this quest. Each unit: dict(scope, loc, script_edid, source, refs, touched, ...)."""
    out = []
    sd = q.get('script_data')
    if sd and sd.get('source', '').strip():
        out.append({'scope': 'quest_script', 'loc': 'quest_script', 'script_edid': sd.get('edid') or (q['edid'] + 'QuestScript'),
                    'sc': sd, 'touched': True})
    for s in sorted(q['stages'], key=lambda s: s['index']):
        for ei, e in enumerate(s['entries']):
            sc = e.get('script')
            if sc and sc['source'].strip():
                out.append({'scope': 'stage', 'loc': 'stage %d' % s['index'], 'stage': s['index'], 'entry': ei,
                            'script_edid': '%s stage %d%s' % (q['edid'], s['index'], '' if ei == 0 else '.%d' % ei), 'sc': sc,
                            'touched': True})
    for i in data.infos_by_quest.get(q['ref'], ()):
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc['source'].strip():
                out.append({'scope': 'info_' + k, 'loc': 'info %s %s' % (info_id(i), k), 'info': i['ref'],
                            'script_edid': 'INFO %s %s' % (info_id(i), k), 'sc': sc, 'touched': True})
    for sref, sedid, label, touched, obj, dref in dep_script_refs(data, q):
        out.append({'scope': 'dep_obj', 'loc': 'dep ' + label, 'script_edid': sedid, 'sc': data.extra['scpt'][sref]['script'],
                    'touched': touched, 'obj': obj, 'script_ref': sref})
    return out


ARG = re.compile(r'"[^"]*"|\S+')


def stmt_cmd(text):
    """Command name of one statement line (matches effects.analyse parsing) -> (subject or None, cmd)."""
    toks = ARG.findall(text)
    if not toks:
        return None, ''
    subj, cmd = None, toks[0]
    if '.' in toks[0] and not eff_mod.NUM.match(toks[0]):
        subj, cmd = toks[0].split('.', 1)
    return subj, cmd


def statements(data, unit):
    """Statement-level calls of a unit: list of dict(line, fn, subject, conditional, modeled, effect, text)."""
    sc = unit['sc']
    ctx = eff_mod.Ctx(data.name_map(sc), data.globals_by_edid)
    effs, unm = eff_mod.analyse(sc['source'], ctx)
    lines = sc['source'].splitlines()
    out = []
    for e in effs:
        text = classify.strip_comment(lines[e['line'] - 1]).strip()
        subj, cmd = stmt_cmd(text)
        fn = cmd if text.lower().split()[0] not in ('set', 'let') else 'set'
        out.append({'line': e['line'], 'fn': fn.lower(), 'subject': subj, 'conditional': e['conditional'], 'modeled': True,
                    'effect': e['type'], 'dynamic': e.get('dynamic', False), 'text': text[:140]})
    for u in unm:
        subj, cmd = stmt_cmd(classify.strip_comment(lines[u['line'] - 1]).strip())
        fn = 'setquestvar' if u['fn'] == 'SetQuestVar' else (cmd or u['fn'])
        out.append({'line': u['line'], 'fn': fn.lower(), 'subject': subj, 'conditional': u['conditional'], 'modeled': False,
                    'effect': None, 'dynamic': False, 'text': u['text'][:140]})
    out.sort(key=lambda s: (s['line'], s['fn']))
    return out


def expr_findings(data, unit):
    """Expression/condition-level findings (player-ref reads, P-REF/CONV/... lexicon hits) from the risk classifier."""
    ctx = 'dialogue' if unit['scope'].startswith('info') else None
    return classify.classify_source(unit['sc']['source'], data.kq, data.kg, ctx)


def driver_infos(data, edids):
    """INFOs outside the given quests whose result scripts touch them (stage/objective/start/var writes or any mention).
    -> {info_ref: sorted list of the quests' EDIDs it mentions}"""
    mine = {data.by_edid[e]['ref'] for e in edids}
    rx = re.compile(r'\b(%s)\b' % '|'.join(re.escape(e) for e in edids), re.I)
    canon = {e.lower(): e for e in edids}
    out = {}
    for i in data.infos_all:
        if i.get('quest') in mine:
            continue
        hit = set()
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc:
                for line in sc['source'].splitlines():
                    hit.update(canon[m.lower()] for m in rx.findall(classify.strip_comment(line)))
        if hit:
            out[i['ref']] = sorted(hit)
    return out


def quest_var_name(q, idx):
    for v in (q.get('script_data') or {}).get('vars', []):
        if v['idx'] == idx:
            return v['name']
    return None
