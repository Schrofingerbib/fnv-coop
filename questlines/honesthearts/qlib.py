"""Shared loaders for the Honest Hearts questline tools (read-only over out/questdata and docs/quest-policies).

Everything here is game-free. FormIDs are derived from `plugin:LOCALID` refs through tools/questdata/loadorder.py
(runtime id = load-order index << 24 | local id; HonestHearts.esm is index 02). The `formid` field inside quests.json is
NOT used: it was written with a different plugin index (01...) and must not be trusted for console commands.
"""
import collections
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
QD = os.path.join(REPO, 'out', 'questdata')
sys.path.insert(0, os.path.join(REPO, 'tools', 'questdata'))

import loadorder  # noqa: E402
from classify import TOKEN, strip_comment, dep_scripts, load_extra  # noqa: E402,F401

PLUGIN = 'HonestHearts.esm'
SLUG = 'honesthearts'
PREFIX = 'NVDLC02'


def jload(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def gz(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


class Data:
    """Lazy bundle of the inputs this questline needs."""

    def __init__(self):
        self.quests_doc = jload(os.path.join(QD, 'quests.json'))
        self.quests = [q for q in self.quests_doc['quests'] if q['plugin'] == PLUGIN]
        self.by_edid = {q['edid']: q for q in self.quests}
        self.by_ref = {q['ref']: q for q in self.quests}
        self.all_quests = {q['ref']: q for q in self.quests_doc['quests']}
        self.risk = jload(os.path.join(QD, 'risk.json'))['quests']
        self.forms = gz('forms.json.gz')
        self.refnames = gz('refnames.json.gz')
        self.eff = gz('effects.json.gz')
        self.native = gz('effects_native.json.gz')
        self.extra = load_extra(QD)
        self.plugins = loadorder.load()
        self._infos = None
        self._dials = None
        self._graph = None
        self._rules = None
        self._policies = None

    @property
    def infos(self):
        if self._infos is None:
            d = collections.defaultdict(list)
            with gzip.open(os.path.join(QD, 'infos.jsonl.gz'), 'rt', encoding='utf-8') as f:
                for ln in f:
                    i = json.loads(ln)
                    if i.get('quest') and i['quest'].startswith(PLUGIN + ':'):
                        d[i['quest']].append(i)
            self._infos = d
        return self._infos

    @property
    def dials(self):
        if self._dials is None:
            self._dials = {d['ref']: d for d in gz('dials.json.gz') if d.get('plugin') == PLUGIN}
        return self._dials

    @property
    def graph(self):
        if self._graph is None:
            self._graph = jload(os.path.join(REPO, 'out', 'coverage', 'questgraph.json'))
        return self._graph

    @property
    def rules(self):
        if self._rules is None:
            self._rules = jload(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'))
        return self._rules

    @property
    def policies(self):
        if self._policies is None:
            self._policies = jload(os.path.join(REPO, 'docs', 'quest-policies', 'policies.json'))
        return self._policies

    def fid(self, ref):
        """Console hex FormID for a `plugin:LOCAL` ref (8 digits for DLC)."""
        return loadorder.console_id(ref, self.plugins)

    def edid_of(self, ref):
        f = self.forms.get(ref)
        if f and f.get('edid'):
            return f['edid']
        q = self.all_quests.get(ref)
        return q['edid'] if q else None

    def resolves(self, ref):
        return ref in self.forms or ref in self.all_quests


def stage_scripts(q):
    """Yield (stage_index, entry_index, source) for every stage result script of a quest."""
    for st in sorted(q['stages'], key=lambda s: s['index']):
        for ei, e in enumerate(st['entries']):
            sc = e.get('script')
            if sc and sc.get('source'):
                yield st['index'], ei, sc['source']


def clip(text, n):
    """Cut text to at most n characters without leaving half an identifier at the end."""
    if len(text) <= n:
        return text
    cut = text[:n]
    if cut[-1].isalnum() or cut[-1] == '_':
        cut = re.sub(r'[A-Za-z0-9_]+$', '', cut)
    return cut.rstrip()


def is_player_prefixed(line):
    return bool(re.match(r'(?i)^\s*player\.', line))


def identifier_universe(d):
    """Lower-cased identifiers that exist in the data: EDIDs of forms / refs / quests / scripts plus every identifier token of the
    Honest Hearts script, stage and INFO sources (covers perk, message, package and variable names that forms.json does not list)."""
    u = set()
    for f in d.forms.values():
        if f.get('edid'):
            u.add(f['edid'].lower())
    for f in d.refnames.values():
        if f.get('edid'):
            u.add(f['edid'].lower())
    for q in d.all_quests.values():
        if q.get('edid'):
            u.add(q['edid'].lower())
    srcs = []
    for ref, s in d.extra['scpt'].items():
        if ref.startswith(PLUGIN + ':'):
            u.add((s.get('edid') or '').lower())
            srcs.append((s.get('script') or {}).get('source') or '')
    for q in d.quests:
        srcs.append((q.get('script_data') or {}).get('source') or '')
        for _st, _ei, src in stage_scripts(q):
            srcs.append(src)
    for lst in d.infos.values():
        for i in lst:
            for k in ('begin', 'end'):
                if i.get(k) and i[k].get('source'):
                    srcs.append(i[k]['source'])
    tok = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
    for src in srcs:
        for line in src.replace(chr(13), '').split(chr(10)):
            for t in tok.findall(strip_comment(line)):
                u.add(t.lower())
    return u
