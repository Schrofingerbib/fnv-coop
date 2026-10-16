"""Script-unit enumeration and statement-level call extraction for the OWB questline.

A *unit* is one executable script body: an object/quest/effect SCPT, a quest stage script, an INFO begin/end script,
a terminal menu item script or a package script. Calls are extracted with exact 1-based RAW line numbers (CRLF source
split on LF), the same convention risk.json uses (e.g. NVDLC03SinkAutoDoc:422).
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PLUGIN, strip_comment  # noqa: E402

DECL = {'scn', 'scriptname', 'short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var'}
CTRL = {'else', 'endif', 'endwhile', 'return', 'loop', 'continue', 'break'}
COND = {'if', 'elseif', 'while'}
CALL = re.compile(r'(?<![\w.])(?:(\w+)\.)?([A-Za-z_]\w*)')
PLAYER = {'player', 'playerref'}


def units(d):
    """Yield dicts {id, kind, owner, block_src, source, plugin_obj} for every OWB executable script body."""
    for x in d.scripts:
        if x.get('plugin') == PLUGIN:
            yield {'id': x['edid'], 'kind': 'script:' + x['script']['type'], 'owner': x['edid'],
                   'source': x['script']['source'], 'ref': x['ref']}
    for q in d.owb_quests():
        for s in sorted(q['stages'], key=lambda s: s['index']):
            for n, e in enumerate(s['entries']):
                if e.get('script') and e['script'].get('source'):
                    uid = '%s:stage %d' % (q['edid'], s['index']) + ('' if n == 0 else '#%d' % n)
                    yield {'id': uid, 'kind': 'stage', 'owner': q['edid'], 'source': e['script']['source'],
                           'ref': q['ref'], 'stage': s['index']}
    dial = d.dial_by_ref
    for i in d.infos:
        t = dial.get(i['topic'], {})
        for k in ('begin', 'end'):
            if i.get(k) and i[k].get('source'):
                qd = d.by_ref.get(i.get('quest'), {})
                yield {'id': 'INFO %s %s' % (i['ref'].split(':')[1], k), 'kind': 'info:' + k,
                       'owner': qd.get('edid') or i.get('quest'), 'source': i[k]['source'], 'ref': i['ref'],
                       'topic': t.get('edid') or i['topic']}
    for t in d.terms:
        if t.get('plugin') != PLUGIN:
            continue
        for n, it in enumerate(t.get('items', [])):
            if it.get('script') and it['script'].get('source'):
                yield {'id': 'TERM %s#%d' % (t['edid'], n), 'kind': 'term', 'owner': t['edid'],
                       'source': it['script']['source'], 'ref': t['ref'], 'text': it.get('text')}
    for p in d.packs:
        if p.get('plugin') != PLUGIN:
            continue
        for ev, sc in (p.get('scripts') or {}).items():
            if sc and sc.get('source'):
                yield {'id': 'PACK %s %s' % (p['edid'], ev), 'kind': 'pack', 'owner': p['edid'],
                       'source': sc['source'], 'ref': p['ref']}


def _reads(text):
    """Function-like tokens inside an If/While condition or Set expression -> [(subject, fn)]"""
    out = []
    for m in CALL.finditer(re.sub(r'"[^"]*"', '""', text)):
        out.append((m.group(1), m.group(2)))
    return out


def calls(unit, known):
    """Statement-level calls. known = set of lowercase command names (reads and effects).
    Returns list of dicts: {line, block, fn, subject, text, kind: 'effect'|'read'|'setvar'|'setglobal'}"""
    out = []
    block = None
    for ln, raw in enumerate(unit['source'].replace('\r', '').split('\n'), 1):
        s = strip_comment(raw)
        if not s:
            continue
        first = s.split(None, 1)[0].lower().rstrip(';')
        if first in DECL or first in CTRL:
            continue
        if first == 'begin':
            parts = s.split()
            block = parts[1] if len(parts) > 1 else '?'
            out.append(dict(line=ln, block=block, fn=block, subject=None, arg=parts[2] if len(parts) > 2 else None,
                            text=s[:160], kind='block'))
            continue
        if first == 'end' and len(s.split()) == 1:
            block = None
            continue
        if first == 'end':
            block = None
            continue
        if first in COND:
            for subj, fn in _reads(s[len(first):]):
                if fn.lower() in known:
                    out.append(dict(line=ln, block=block, fn=fn, subject=subj, text=s[:160], kind='read'))
            continue
        if first == 'set' or first == 'let':
            m = re.match(r'(?i)set\s+([\w.]+)\s+to\s+(.*)$', s)
            if m:
                tgt, expr = m.group(1), m.group(2)
                if '.' in tgt:
                    out.append(dict(line=ln, block=block, fn='SetQuestVar', subject=tgt.split('.')[0],
                                    var=tgt.split('.', 1)[1], text=s[:160], kind='setvar'))
                else:
                    out.append(dict(line=ln, block=block, fn='SetLocalOrGlobal', subject=None, var=tgt,
                                    text=s[:160], kind='setglobal'))
                for subj, fn in _reads(expr):
                    if fn.lower() in known:
                        out.append(dict(line=ln, block=block, fn=fn, subject=subj, text=s[:160], kind='read'))
            continue
        m = re.match(r'(?:(\w+)\.)?([A-Za-z_]\w*)', s)
        if m:
            subj, fn = m.group(1), m.group(2)
            if fn.lower() in known:
                out.append(dict(line=ln, block=block, fn=fn, subject=subj, text=s[:160], kind='effect'))
            else:
                out.append(dict(line=ln, block=block, fn=fn, subject=subj, text=s[:160], kind='unknown'))
    return out
