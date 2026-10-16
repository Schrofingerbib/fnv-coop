"""Finalize + validate the merged rules (cross-lane moves, empty tethers, EDID resolution)."""
import copy
import re

from merge_rules import ALLOWED_KINDS, SCHEMA, REDUCTIONS

# Cross-lane moves agreed by the questline agents (lead decision 2026-10-16): the Fort / Colonel Moore / Yes Man
# endgame-confirmation scripts are object scripts that risk.json attributes to the quests whose NPCs they touch.
# Their rules belong to the quest whose stage the confirmation advances.
MOVED_KINDS = ['PartyWarp', 'SetPlayerControls', 'FastTravelLock', 'SaveSuppress']
MOVE_FROM = ['VMS29', 'VMS32', 'VMS35', 'VMS45']
MOVE_TO = ['VHDLegionBattle', 'VMQ05', 'VHDIndependentBattle']
DEFAULT_PARAMS = {'SetPlayerControls': {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []},
                  'FastTravelLock': {'scope': 'party'}}


class Resolver:
    """EditorID -> signature, from refnames + forms + quests (case-insensitive)."""

    def __init__(self, data, refnames=None, forms=None):
        self.sig = {}
        for src in (refnames or {}, forms or {}):
            for v in src.values():
                if v.get('edid'):
                    self.sig.setdefault(v['edid'].lower(), v.get('sig'))
        for q in data['quests']:
            self.sig.setdefault(q['edid'].lower(), 'QUST')

    def ok(self, edid):
        return edid.lower() in self.sig

    def is_cell(self, edid):
        return self.sig.get(edid.lower()) == 'CELL'

    def is_place(self, edid):          # CELL or WRLD (a tether on a worldspace means 'anywhere in it')
        return self.sig.get(edid.lower()) in ('CELL', 'WRLD')


def _quest_texts(data, q):
    out = []
    sd = q.get('script_data')
    if sd:
        out.append(sd.get('source') or '')
    for st in q['stages']:
        for en in st['entries']:
            out.append((en.get('script') or {}).get('source') or '')
    for inf in data['infos'].get(q['ref'], []):
        for part in ('begin', 'end'):
            out.append((inf.get(part) or {}).get('source') or '')
    r = data['risk'].get(q['ref'])
    if r:
        out += [f['text'] for f in r['findings'] + r['stage_findings']]
    return '\n'.join(out)


def infer_cells(data, resolver, edid):
    q = data['by_edid'].get(edid)
    if not q:
        return []
    cells = []
    for m in re.finditer(r'GetInCell\s+(\w+)\s*((?:==|!=)\s*[01])?', _quest_texts(data, q), re.I):
        c, cmp_ = m.group(1), re.sub(r'\s+', '', m.group(2) or '')
        if cmp_ in ('==0', '!=1'):          # exit gate ("player left the cell"): not a tether target
            continue
        if resolver.is_cell(c) and c not in cells:
            cells.append(c)
    return cells[:6]


RED_RANK = {'initiator': 0, 'any': 1, 'any-party-member': 2, 'min-over-party': 3, 'entry:all-in,exit:any-in': 4}


def dedupe_funcs(funcs):
    """Case-insensitive de-dup; the more specific reduction wins, the mixed-case spelling is kept."""
    best = {}
    for fn, red in funcs.items():
        k = fn.lower()
        cur = best.get(k)
        name = fn if (cur is None or fn != fn.lower()) else cur[0]
        if cur is None or RED_RANK[red] >= RED_RANK[cur[1]]:
            best[k] = (name, red)
        else:
            best[k] = (name, cur[1])
    return {n: r for n, r in best.values()}


def finalize(m, data, resolver):
    """Apply cross-lane moves, resolve ids, fix/drop empty tethers and warps. Returns a report dict."""
    rep = {'moved': [], 'tether_fixed': [], 'tether_dropped': [], 'warp_dropped': [], 'unresolved_dropped': []}
    for q in MOVE_FROM:
        e = m.entries.get(q)
        if not e:
            continue
        for k in MOVED_KINDS:
            if any(r['kind'] == k for r in e['rules']):
                e['rules'] = [r for r in e['rules'] if r['kind'] != k]
                rep['moved'].append({'quest': q, 'kind': k})
    for tgt in MOVE_TO:
        e = m._entry(tgt)
        for k in ('SetPlayerControls', 'FastTravelLock', 'SaveSuppress'):
            if not m._find(e, k):
                e['rules'].append({'kind': k, 'params': copy.deepcopy(DEFAULT_PARAMS.get(k, {})),
                                   'origin': 'curated'})
                rep['moved'].append({'quest': tgt, 'kind': k, 'added': True})
    for q, e in m.entries.items():
        for r in e['rules']:
            if r['kind'] == 'PartyAwareCond':
                r['params']['funcs'] = dedupe_funcs(r['params'].get('funcs', {}))
        keep = []
        for r in e['rules']:
            for p in ('cells', 'markers'):
                if p in r['params']:
                    bad = [x for x in r['params'][p] if not resolver.ok(x)]
                    if bad:
                        rep['unresolved_dropped'].append({'quest': q, 'kind': r['kind'], 'ids': bad})
                    r['params'][p] = [x for x in r['params'][p] if resolver.ok(x)]
            if r['kind'] == 'PartyTether' and not r['params'].get('cells'):
                cells = infer_cells(data, resolver, q)
                if cells:
                    r['params']['cells'] = cells
                    rep['tether_fixed'].append({'quest': q, 'cells': cells})
                else:
                    rep['tether_dropped'].append(q)
                    m.pending_rules.setdefault(q, []).append(
                        {'kind': 'PartyTether', 'params': r['params'], 'lane': 'finalize',
                         'why': 'no CELL resolvable from the quest data; needs tether anchor/radius (proposal)'})
                    continue
            if r['kind'] == 'PartyWarp' and not r['params'].get('markers'):
                rep['warp_dropped'].append(q)
                m.pending_rules.setdefault(q, []).append(
                    {'kind': 'PartyWarp', 'params': r['params'], 'lane': 'finalize', 'why': 'no resolvable marker'})
                continue
            keep.append(r)
        e['rules'] = keep
    return rep


def validate(rules, resolver, data):
    """-> list of error strings (empty = valid)."""
    errs = []
    if rules.get('schema') != 1:
        errs.append('schema != 1')
    for q, e in rules['quests'].items():
        if q not in data['by_edid']:
            errs.append(f'{q}: quest EDID does not resolve')
        seen = set()
        for r in e['rules']:
            k = r.get('kind')
            if k not in ALLOWED_KINDS:
                errs.append(f'{q}: unknown kind {k}')
                continue
            if k in seen:
                errs.append(f'{q}: duplicate kind {k}')
            seen.add(k)
            sch = SCHEMA[k]
            for p, v in r['params'].items():
                spec = sch.get(p)
                if spec is None:
                    errs.append(f'{q}.{k}: param {p} not in vocabulary')
                elif isinstance(spec, set) and v not in spec:
                    errs.append(f'{q}.{k}.{p}: value {v!r} not allowed')
                elif spec == 'list' and not isinstance(v, list):
                    errs.append(f'{q}.{k}.{p}: expected list')
                elif spec == 'funcs':
                    for fn, red in v.items():
                        if red not in REDUCTIONS:
                            errs.append(f'{q}.{k}.funcs.{fn}: reduction {red!r} not implemented')
            if k == 'PartyTether':
                cells = r['params'].get('cells')
                if not cells:
                    errs.append(f'{q}: PartyTether with empty cells')
                for c in cells or []:
                    if not resolver.is_place(c):
                        errs.append(f'{q}: tether cell {c} is not a resolvable CELL/WRLD')
            if k == 'PartyWarp':
                if not r['params'].get('markers'):
                    errs.append(f'{q}: PartyWarp with no markers')
                for x in r['params'].get('markers', []):
                    if not resolver.ok(x):
                        errs.append(f'{q}: marker {x} does not resolve')
            if k == 'PartyAwareCond':
                low = [f.lower() for f in r['params'].get('funcs', {})]
                if len(low) != len(set(low)):
                    errs.append(f'{q}: PartyAwareCond has case-duplicate funcs')
    return errs
