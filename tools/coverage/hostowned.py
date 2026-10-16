"""Host-owned actor candidates for L6's server hook.

Candidate = a placed actor reference (ACHR/ACRE) that a story quest points at: an objective target, or a ref
named by the quest script, a stage script or an INFO result/begin script. FormIDs are resolved against the
load order (default: vanilla + all official DLC, in release order) so the hook can compare raw runtime ids.

Outputs: hostowned.txt (one 8-digit hex FormID per line, '#' comments allowed) and hostowned.json (detail).
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'questdata'))
import loadorder  # noqa: E402  (shared single source of truth, owned by L3)

DEFAULT_LOAD_ORDER = loadorder.load()
ACTOR_SIGS = {'ACHR', 'ACRE'}


def resolve(ref, order):
    plugin, local = ref.split(':')
    return (order.index(plugin) << 24) | (int(local, 16) & 0xFFFFFF)


def load_forms(qdir):
    p = os.path.join(qdir, 'forms.json.gz')
    if not os.path.exists(p):
        return {}
    with gzip.open(p, 'rt', encoding='utf-8') as f:
        return json.load(f)


def candidates(data, forms, order=None, story_only=True):
    order = order or DEFAULT_LOAD_ORDER
    found = {}   # ref -> {'why': set, 'quests': set}

    def note(ref, quest, why):
        f = forms.get(ref)
        if not f or f.get('sig') not in ACTOR_SIGS:
            return
        e = found.setdefault(ref, {'why': set(), 'quests': set()})
        e['why'].add(why)
        e['quests'].add(quest)

    for q in data['quests']:
        r = data['risk'].get(q['ref'])
        if story_only and (not r or r['kind'] != 'story'):
            continue
        e = q['edid']
        for o in q['objectives']:
            for t in o.get('targets', []):
                note(t['ref'], e, 'objective-target')
        sd = q.get('script_data') or {}
        for ref in sd.get('refs', []):
            note(ref, e, 'quest-script')
        for st in q['stages']:
            for en in st['entries']:
                for ref in (en.get('script') or {}).get('refs', []):
                    note(ref, e, 'stage-script')
        for inf in data['infos'].get(q['ref'], []):
            for part in ('begin', 'end'):
                for ref in (inf.get(part) or {}).get('refs', []):
                    note(ref, e, 'info-script')
    rows = []
    for ref, v in found.items():
        if ref.split(':')[0] not in order:
            continue
        f = forms[ref]
        rows.append({'formid': '%08X' % resolve(ref, order), 'ref': ref, 'sig': f['sig'], 'edid': f.get('edid'),
                     'base': f.get('base'), 'why': sorted(v['why']), 'quests': sorted(v['quests'])})
    rows.sort(key=lambda r: r['formid'])
    return rows


def write(data, outdir, order=None):
    order = order or DEFAULT_LOAD_ORDER
    rows = candidates(data, load_forms(data['qdir']), order)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, 'hostowned.txt'), 'w', encoding='ascii', newline='\n') as f:
        f.write('# host-owned actor candidates (story-quest objective targets + script-referenced actors)\n')
        f.write('# load order: ' + ', '.join(f'{i:02X}={p}' for i, p in enumerate(order)) + '\n')
        for r in rows:
            f.write(r['formid'] + '\n')
    json.dump({'schema': 1, 'load_order': order, 'count': len(rows), 'actors': rows},
              open(os.path.join(outdir, 'hostowned.json'), 'w', encoding='utf-8'), indent=1)
    return rows
