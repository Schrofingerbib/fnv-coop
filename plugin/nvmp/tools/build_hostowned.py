#!/usr/bin/env python3
"""Build hostowned.txt (FormIDs the story-server hook keeps host-owned) from L3's quest data.

Default selection = union of
  * every ACHR/ACRE ref listed under 'npcs' of each quest in out/questdata/m1_goodsprings.json (M1 scope), and
  * every ACHR/ACRE ref in out/questdata/forms.json.gz (all persistent actors the quest/script/dialogue data touched).
FormIDs on the server are load-order FormIDs, so the plugin -> load-order index matters:
  --loadorder <plugins.txt|loadorder.txt>   host's active plugin order (lines; '*' prefixes and '#' comments ok)
Without it the canonical FNV Ultimate order is assumed (FalloutNV, DeadMoney, HonestHearts, OldWorldBlues,
LonesomeRoad, GunRunnersArsenal, ... ) and a warning is printed: a wrong order silently flags the wrong refs.

usage: build_hostowned.py [--out PATH] [--m1-only] [--types ACHR,ACRE] [--loadorder FILE] [--questdata DIR]
       build_hostowned.py --selftest
"""
import argparse, gzip, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
SHARED_ORDER_FILE = os.path.join(ROOT, 'tools', 'questdata', 'loadorder.json')
# Last resort only (shared file missing): the order observed in game on 2026-10-16 (ESMs sorted by file time, not plugins.txt).
FALLBACK_ORDER = ['FalloutNV.esm', 'DeadMoney.esm', 'HonestHearts.esm', 'OldWorldBlues.esm', 'LonesomeRoad.esm',
                  'TribalPack.esm', 'MercenaryPack.esm', 'ClassicPack.esm', 'CaravanPack.esm', 'GunRunnersArsenal.esm']


def shared_order():
    """The project-wide runtime load order (tools/questdata/loadorder.json, owned by L3)."""
    try:
        with open(SHARED_ORDER_FILE, encoding='utf-8') as f:
            return json.load(f)['plugins']
    except (OSError, KeyError, ValueError):
        print('WARNING: %s unreadable, using the built-in fallback order' % SHARED_ORDER_FILE, file=sys.stderr)
        return list(FALLBACK_ORDER)


DEFAULT_ORDER = shared_order()


def order_differences(order, shared):
    """Human-readable differences between a supplied order and the shared one ('' if identical)."""
    o, s = [p.lower() for p in order], [p.lower() for p in shared]
    n = max(len(o), len(s))
    diffs = []
    for i in range(n):
        a = order[i] if i < len(order) else '(none)'
        b = shared[i] if i < len(shared) else '(none)'
        if a.lower() != b.lower():
            diffs.append('index %02X: supplied %s, shared %s' % (i, a, b))
    return '; '.join(diffs)


def read_loadorder(path):
    if path.lower().endswith('.json'):
        with open(path, encoding='utf-8') as f:
            return json.load(f)['plugins']
    out = []
    for line in open(path, encoding='utf-8-sig', errors='replace'):
        line = line.split('#')[0].strip().lstrip('*').strip()
        if line and line.lower().endswith(('.esm', '.esp', '.esl')):
            out.append(line)
    return out


def parse_ref(ref, index):
    """'FalloutNV.esm:104C6D' -> 0x00104C6D using the plugin's load-order index; None if the plugin is not loaded."""
    plugin, _, local = ref.rpartition(':')
    if plugin.lower() not in index:
        return None
    return (index[plugin.lower()] << 24) | (int(local, 16) & 0xFFFFFF)


def collect(questdata, types, m1_only):
    """Returns {ref_string: (sig, edid, why)}"""
    found = {}
    m1 = json.load(open(os.path.join(questdata, 'm1_goodsprings.json'), encoding='utf-8'))
    for q in m1.get('quests', []):
        for n in q.get('npcs', []):
            if n.get('sig') in types:
                found.setdefault(n['ref'], (n['sig'], n.get('edid') or '', 'm1:' + q.get('edid', '?')))
    if not m1_only:
        forms = json.load(gzip.open(os.path.join(questdata, 'forms.json.gz'), 'rt', encoding='utf-8'))
        for ref, v in forms.items():
            if v.get('sig') in types:
                found.setdefault(ref, (v['sig'], v.get('edid') or '', 'forms'))
    return found


def build(found, order, named=True):
    index = {p.lower(): i for i, p in enumerate(order)}
    lines, skipped = [], {}
    for ref in sorted(found, key=lambda r: (parse_ref(r, index) is None, parse_ref(r, index) or 0)):
        fid = parse_ref(ref, index)
        sig, edid, why = found[ref]
        if fid is None:
            plugin = ref.rpartition(':')[0]
            skipped[plugin] = skipped.get(plugin, 0) + 1
            continue
        if named:
            plugin, _, local = ref.rpartition(':')
            lines.append('%s:%06X  # %s %s [%s]' % (plugin, int(local, 16) & 0xFFFFFF, sig, edid, why))
        else:
            lines.append('%08X  # %s %s [%s]' % (fid, sig, edid, why))
    return lines, skipped


def selftest():
    order = ['FalloutNV.esm', 'DeadMoney.esm']
    found = {'FalloutNV.esm:104C6D': ('ACHR', 'TrudyRef', 'm1:VCG02'), 'DeadMoney.esm:001234': ('ACHR', 'X', 'forms'),
             'Missing.esm:000001': ('ACHR', 'Y', 'forms')}
    lines, skipped = build(found, order, named=False)
    nl, _ = build(found, order)
    assert nl[0].startswith("FalloutNV.esm:104C6D  # ACHR TrudyRef") and nl[1].startswith("DeadMoney.esm:001234"), nl
    assert lines[0].startswith('00104C6D  # ACHR TrudyRef'), lines
    assert lines[1].startswith('01001234'), lines
    assert skipped == {'Missing.esm': 1}, skipped
    assert parse_ref('falloutnv.esm:ABCDEF', {'falloutnv.esm': 0}) == 0xABCDEF
    assert order_differences(['A.esm','B.esm'], ['A.esm','B.esm']) == ''
    assert 'index 01' in order_differences(['A.esm','C.esm'], ['A.esm','B.esm'])
    assert shared_order()[0] == 'FalloutNV.esm' and 'GunRunnersArsenal.esm' in shared_order()
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'nvmp', 'hostowned.txt'))
    ap.add_argument('--questdata', default=os.path.join(ROOT, 'out', 'questdata'))
    ap.add_argument('--types', default='ACHR,ACRE')
    ap.add_argument('--m1-only', action='store_true')
    ap.add_argument('--hex', action='store_true', help='legacy: emit runtime FormIDs instead of Plugin.esm:LOCALID (needs a correct --loadorder)')
    ap.add_argument('--loadorder')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    order = read_loadorder(a.loadorder) if a.loadorder else DEFAULT_ORDER
    if not a.loadorder:
        print('using the shared load order from %s (%s ...)' % (SHARED_ORDER_FILE, ', '.join(order[:4])), file=sys.stderr)
    else:
        d = order_differences(order, shared_order())
        if d:
            print('WARNING: --loadorder %s differs from the shared tools/questdata/loadorder.json: %s' % (a.loadorder, d), file=sys.stderr)
            print('         (FNV sorts ESMs by file time, not plugins.txt order: verify against the game before trusting it)', file=sys.stderr)
    found = collect(a.questdata, set(a.types.split(',')), a.m1_only)
    lines, skipped = build(found, order, named=not a.hex)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# hostowned.txt: FormIDs of refs the story-server hook keeps host-owned (generated, do not hand-edit)\n')
        f.write('# load order: %s\n' % ', '.join('%02X=%s' % (i, p) for i, p in enumerate(order)))
        f.write('# source: %s, types %s, plugins %s\n' % ('m1 only' if a.m1_only else 'm1 npcs + forms.json.gz', a.types, ','.join(order[:6])))
        f.write('\n'.join(lines) + '\n')
    print('wrote %d FormIDs to %s' % (len(lines), a.out))
    if skipped:
        print('skipped (plugin not in load order): %s' % ', '.join('%s x%d' % kv for kv in sorted(skipped.items())), file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main() or 0)
