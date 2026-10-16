"""Shared loaders for the OWB questline tooling (read-only inputs under out/ and docs/)."""
import gzip
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
QD = os.path.join(ROOT, 'out', 'questdata')
PLUGIN = 'OldWorldBlues.esm'
PREFIX = '03'
SLUG = 'owb'


def _gz(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


def _json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class Data:
    """Everything the generators need, loaded once."""

    def __init__(self):
        q = _json(os.path.join(QD, 'quests.json'))
        self.quests_all = q['quests']
        self.by_edid = {x['edid']: x for x in self.quests_all if x.get('edid')}
        self.by_ref = {x['ref']: x for x in self.quests_all}
        self.risk = _json(os.path.join(QD, 'risk.json'))['quests']
        self.forms = _gz('forms.json.gz')
        self.refnames = _gz('refnames.json.gz')
        self.scripts = _gz('scripts.json.gz')
        self.terms = _gz('terms.json.gz')
        self.packs = _gz('packs.json.gz')
        self.dials = _gz('dials.json.gz')
        self.dial_by_ref = {d['ref']: d for d in self.dials}
        self.loadorder = _json(os.path.join(ROOT, 'tools', 'questdata', 'loadorder.json'))['plugins']
        self.graph = _json(os.path.join(ROOT, 'out', 'coverage', 'questgraph.json'))
        self.rules = _json(os.path.join(ROOT, 'docs', 'quest-policies', 'rules.json'))['quests']
        self.effects_native = _gz('effects_native.json.gz')
        self.globals = {g['ref']: g for g in q['globals']}
        self._infos = None

    @property
    def infos(self):
        """OWB-plugin INFO records (list of dict)."""
        if self._infos is None:
            out = []
            with gzip.open(os.path.join(QD, 'infos.jsonl.gz'), 'rt', encoding='utf-8') as f:
                for line in f:
                    o = json.loads(line)
                    if o.get('plugin') == PLUGIN:
                        out.append(o)
            self._infos = out
        return self._infos

    def owb_quests(self):
        """OWB-owned quests, in plugin/FormID order (the 2 overrides live in quest_overrides, not here)."""
        return sorted((x for x in self.quests_all if x['plugin'] == PLUGIN), key=lambda x: int(x['formid'], 16))

    def runtime_id(self, ref):
        """'OldWorldBlues.esm:002FCC' -> '03002FCC' (load-order index prefix, FNV console needs hex FormIDs)."""
        plug, local = ref.split(':')
        return '%02X%s' % (self.loadorder.index(plug), local.zfill(6)[-6:])

    def resolve(self, ref):
        """-> (source, record) where source is 'quests.json' | 'forms.json.gz' | 'refnames.json.gz' | None."""
        if ref in self.by_ref:
            return 'quests.json', self.by_ref[ref]
        if ref in self.forms:
            return 'forms.json.gz', self.forms[ref]
        if ref in self.refnames:
            return 'refnames.json.gz', self.refnames[ref]
        return None, None

    def edid_to_ref(self):
        """Lowercase EDID -> list of refs across quests/forms/refnames (for resolving script identifiers)."""
        m = {}
        for r, x in self.by_ref.items():
            if x.get('edid'):
                m.setdefault(x['edid'].lower(), set()).add(r)
        for src in (self.forms, self.refnames):
            for r, x in src.items():
                if x.get('edid'):
                    m.setdefault(x['edid'].lower(), set()).add(r)
        for r, g in self.globals.items():
            if g.get('edid'):
                m.setdefault(g['edid'].lower(), set()).add(r)
        return m


def is_owb_ref(ref):
    return isinstance(ref, str) and ref.startswith(PLUGIN + ':')


def dump(obj, path, indent=1):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False, sort_keys=False)
        f.write('\n')


LINE_COMMENT = re.compile(r'''^((?:[^;"]|"[^"]*")*)(;.*)?$''')


def strip_comment(line):
    m = LINE_COMMENT.match(line)
    return (m.group(1) if m else line).strip()
