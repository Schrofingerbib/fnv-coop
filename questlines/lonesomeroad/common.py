"""Shared loaders for the Lonesome Road questline (slug `lonesomeroad`, plugin LonesomeRoad.esm, load-order prefix 04).

Everything here is read-only over `out/questdata/` and `tools/questdata/` (reuses the L3 loaders; no ESM parsing here).
Run scripts with `python -I`: -I drops the script directory from sys.path, so every script inserts it explicitly.
"""
import gzip
import json
import os
import re
import sys

sys.dont_write_bytecode = True   # never write .pyc into other lanes' directories (tools/questdata)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
QD = os.path.join(REPO, 'out', 'questdata')
sys.path.insert(0, os.path.join(REPO, 'tools', 'questdata'))
import classify  # noqa: E402
import ctda_funcs  # noqa: E402

PLUGIN = 'LonesomeRoad.esm'
LOADORDER = json.load(open(os.path.join(REPO, 'tools', 'questdata', 'loadorder.json'), encoding='utf-8'))['plugins']
PREFIX = LOADORDER.index(PLUGIN)          # 4
PREFIX_HEX = '%02X' % PREFIX              # '04'


def rid(ref):
    """'LonesomeRoad.esm:003615' -> runtime console FormID '04003615' (plugin must be in the load order)."""
    plugin, local = ref.rsplit(':', 1)
    return '%02X%06X' % (LOADORDER.index(plugin), int(local, 16) & 0xFFFFFF)


def gz(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


def gzl(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return [json.loads(x) for x in f]


class Data:
    """Lazy bundle of everything the questline tools need."""

    def __init__(self):
        self.d, self.infos_by_quest = classify.load(QD)
        self.extra = classify.load_extra(QD)
        self.quests = [q for q in self.d['quests'] if q['plugin'] == PLUGIN]
        self.by_edid = {q['edid']: q for q in self.d['quests'] if q.get('edid')}
        self.by_ref = {q['ref']: q for q in self.d['quests']}
        self.risk = json.load(open(os.path.join(QD, 'risk.json'), encoding='utf-8'))['quests']
        self.forms = gz('forms.json.gz')
        self.refnames = gz('refnames.json.gz')
        self.scripts = gz('scripts.json.gz')
        self.dials = {x['ref']: x for x in gz('dials.json.gz')}
        self.effects_native = gz('effects_native.json.gz')
        self.graph = json.load(open(os.path.join(REPO, 'out', 'coverage', 'questgraph.json'), encoding='utf-8'))
        self.rules = json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))
        self.policies = {p['edid']: p for p in json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'policies.json'), encoding='utf-8'))}
        self.hostowned = json.load(open(os.path.join(REPO, 'out', 'coverage', 'hostowned.json'), encoding='utf-8'))
        # EDID -> ref over every table we have (quests, forms, refnames, scripts)
        self.edid_ref = {}
        for tbl in (self.forms, self.refnames):
            for k, v in tbl.items():
                if v.get('edid'):
                    self.edid_ref.setdefault(v['edid'].lower(), k)
        for q in self.d['quests']:
            if q.get('edid'):
                self.edid_ref.setdefault(q['edid'].lower(), q['ref'])
        for s in self.scripts:
            self.edid_ref.setdefault(s['edid'].lower(), s['ref'])
        for r_, v in self.dials.items():
            if v.get('edid'):
                self.edid_ref.setdefault(v['edid'].lower(), r_)
        self.ref_edid = {}
        for tbl in (self.forms, self.refnames):
            for k, v in tbl.items():
                if v.get('edid'):
                    self.ref_edid.setdefault(k, v['edid'])
        for q in self.d['quests']:
            if q.get('edid'):
                self.ref_edid.setdefault(q['ref'], q['edid'])
        for s in self.scripts:
            self.ref_edid.setdefault(s['ref'], s['edid'])
        self.lr_scripts = [s for s in self.scripts if s['plugin'] == PLUGIN]
        self.lr_infos = [i for q in self.quests for i in self.infos_by_quest.get(q['ref'], [])]

    def resolve_edid(self, name):
        return self.edid_ref.get(name.lower())

    def name_of(self, ref):
        return self.ref_edid.get(ref)


TOK = re.compile(r'"[^"]*"|[A-Za-z_][A-Za-z0-9_]*')
IDENT_RE = re.compile(r'\bNVDLC04\w*')
HEX_RE = re.compile(r'\b04[0-9A-Fa-f]{6}\b')


def script_lines(src):
    """Yield (1-based line, text without comments, stripped)."""
    for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
        t = classify.strip_comment(raw).strip()
        if t:
            yield n, t


def clip(text, n):
    """Cut text to at most n chars on a token boundary (never inside an identifier)."""
    if len(text) <= n:
        return text
    cut = text[:n]
    m = re.search(r'[A-Za-z0-9_]*$', cut)
    if m and m.start() > 0 and text[n:n + 1].isalnum() or (text[n:n + 1] == '_'):
        cut = cut[:m.start()] if m else cut
    return cut.rstrip()


def clean(data, text, n=140):
    """Source-line quote safe to emit: clipped on a token boundary, and every NVDLC04* identifier that does not resolve in the
    questdata tables (forms/refnames/quests/scripts/dials) is masked, so no unresolved EditorID is ever emitted."""
    t = clip(text, n)

    def sub(m):
        return m.group(0) if data.resolve_edid(m.group(0)) else '~unresolved~'
    return IDENT_RE.sub(sub, t)


def mask(data, name):
    """An identifier safe to emit: unresolved NVDLC04* names are masked."""
    if name and IDENT_RE.match(name) and data.resolve_edid(name) is None:
        return '~unresolved~'
    return name
