"""Shared loaders for the `systems` questline (read-only on out/questdata, docs/quest-policies, out/coverage).

Run everything with `python -I`; -I drops the script dir from sys.path, so callers do
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
before `import qlib`.
"""
import gzip
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
QD = os.path.join(ROOT, 'out', 'questdata')

_cache = {}


def _jgz(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


def quests():
    if 'quests' not in _cache:
        with open(os.path.join(QD, 'quests.json'), encoding='utf-8') as f:
            _cache['quests'] = json.load(f)['quests']
    return _cache['quests']


def quest_by_edid():
    return {q['edid']: q for q in quests()}


def quest_by_ref():
    return {q['ref']: q for q in quests()}


def infos():
    if 'infos' not in _cache:
        with gzip.open(os.path.join(QD, 'infos.jsonl.gz'), 'rt', encoding='utf-8') as f:
            _cache['infos'] = [json.loads(l) for l in f]
    return _cache['infos']


def scripts():
    if 'scripts' not in _cache:
        _cache['scripts'] = _jgz('scripts.json.gz')
    return _cache['scripts']


def packs():
    return _jgz('packs.json.gz')


def terms():
    return _jgz('terms.json.gz')


def forms():
    if 'forms' not in _cache:
        _cache['forms'] = _jgz('forms.json.gz')
    return _cache['forms']


def refnames():
    return _jgz('refnames.json.gz')


def effects():
    return _jgz('effects.json.gz')


def risk():
    with open(os.path.join(QD, 'risk.json'), encoding='utf-8') as f:
        return json.load(f)['quests']


def questgraph():
    with open(os.path.join(ROOT, 'out', 'coverage', 'questgraph.json'), encoding='utf-8') as f:
        return json.load(f)


def rules():
    with open(os.path.join(ROOT, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8') as f:
        return json.load(f)


def strip_comment(line):
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
        if ch == ';' and not q:
            break
        out.append(ch)
    return ''.join(out)


# ---------------------------------------------------------------- script iteration
def iter_all_scripts():
    """Yield (owner_kind, owner_id, location, source) for EVERY script body in the dataset:
    standalone SCPT, quest stage result scripts, INFO begin/end, PACK scripts, TERM menu item scripts."""
    for s in scripts():
        sc = s.get('script') or {}
        if sc.get('source'):
            yield 'scpt:' + str(sc.get('type')), s['edid'], 'script', sc['source']
    for q in quests():
        for st in q['stages']:
            for n, e in enumerate(st['entries']):
                sc = e.get('script')
                if sc and sc.get('source'):
                    yield 'stage', q['edid'], 'stage %s' % st['index'] + ('.%d' % n if n else ''), sc['source']
    seen = {}
    for i in infos():
        n = seen[i['ref']] = seen.get(i['ref'], 0) + 1      # the dataset holds a few INFO refs twice (override records)
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc.get('source'):
                yield 'info', i['ref'] + ('#%d' % n if n > 1 else ''), 'info ' + k, sc['source']
    for p in packs():
        for k, sc in (p.get('scripts') or {}).items():
            if sc and sc.get('source'):
                yield 'pack', p['edid'], 'pack ' + k, sc['source']
    for t in terms():
        for n, it in enumerate(t.get('items') or []):
            sc = it.get('script')
            if sc and sc.get('source'):
                yield 'term', t['edid'], 'term item %d' % n, sc['source']


STMT_SKIP = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'return', 'begin', 'end', 'scn', 'scriptname',
             'short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var', 'label', 'goto',
             'continue', 'break', 'foreach', 'loop', 'let'}
_TOK = re.compile(r'"[^"]*"|\S+')


def statements(src):
    """Yield (lineno, fn, subject_or_None, args[list], text). `set X to` is reported as fn 'set' (see classify_set)."""
    for n, raw in enumerate(src.splitlines(), 1):
        line = strip_comment(raw).strip()
        if not line:
            continue
        toks = _TOK.findall(line)
        t0 = toks[0]
        l0 = t0.lower()
        if l0 in STMT_SKIP:
            continue
        if l0 == 'set':
            yield n, 'set', None, toks[1:], line
            continue
        subj, cmd = None, t0
        if '.' in t0 and not re.match(r'^[+-]?\d', t0):
            subj, cmd = t0.split('.', 1)
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', cmd):
            continue
        yield n, cmd, subj, toks[1:], line
