"""act2 questline generator: writes inventory.json, sweep.json, gaps.json, gaps.md (and the docs tables that are data-driven).

    python -I questlines/act2/build.py [--root REPO_ROOT] [--out DIR]

Deterministic: same inputs -> byte-identical outputs (no timestamps). Output headers carry the input file hashes.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dialogue as dlg_mod  # noqa: E402
import docs_gen  # noqa: E402
import gaps as gaps_mod  # noqa: E402
import inventory as inv_mod  # noqa: E402
import lib  # noqa: E402
import policy as policy_mod  # noqa: E402
import specs as specs_mod  # noqa: E402
import sweep as sweep_mod  # noqa: E402


def dump(obj, path):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=1, sort_keys=False, ensure_ascii=False)
        f.write('\n')


def header(D):
    return {'generator': 'questlines/act2/build.py', 'inputs': D.input_hashes}


def generate(D):
    """-> dict name -> object/text (pure function of the data)."""
    edids = D.questline_edids()
    cmdtab = json.load(open(lib.REPO / 'plugin' / 'hooks' / 'tools' / 're' / 'command_table.json', encoding='utf-8'))
    g = gaps_mod.build(D, edids, cmdtab)
    drivers = lib.driver_infos(D, edids)
    inv = inv_mod.build(D, edids, drivers)
    sw = sweep_mod.build(D, edids, g)
    h = header(D)
    dials = dlg_mod.load_dials(D.root)
    cat = dlg_mod.catalog(D, edids, dials, drivers)
    sp = specs_mod.build_specs(D, dials)
    ipq = {e: len(D.infos_by_quest.get(D.by_edid[e]['ref'], [])) + sum(1 for hit in drivers.values() if e in hit) for e in edids}
    need = policy_mod.derive(D, g['rows'], edids, ipq)
    cmp_rows = policy_mod.compare(D, need, edids)
    patch = policy_mod.build_patch(D, need, cmp_rows, edids, h)
    note = 'inputs ' + ', '.join('%s=%s' % (k.split('/')[-1], v) for k, v in list(D.input_hashes.items())[:3]) + ' ...'
    out = {
        'inventory.json': {**h, **inv},
        'sweep.json': {**h, **sw},
        'gaps.json': gaps_mod.to_json(g, h),
        'gaps.md': gaps_mod.to_md(g, h),
        'rules.patch.json': patch,
        'policy_review.md': docs_gen.policy_md(D, edids, g['rows'], need, cmp_rows, patch, note, inv['main_quests_below_policy_tier']),
        'dialogue.json': {**h, 'questline': lib.QUESTLINE, 'summary': dlg_mod.summary(cat),
                          'key_infos': [c for c in cat if c in dlg_mod.key_infos(cat)],
                          'specs': sorted(sp)},
        'dialogue.md': docs_gen.dialogue_md(D, edids, cat, sp, note),
    }
    for sid, spec in sp.items():
        out['dialogue_specs/%s.json' % sid] = spec
    out['dialogue_specs/_index.json'] = {'schema': 1, 'kind': 'dialogue', 'scenarios': [
        {'id': sid, 'name': spec['name'], 'steps': len(spec['steps']), 'quests': spec['quests']} for sid, spec in sorted(sp.items())]}
    return out, g


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=str(lib.REPO))
    ap.add_argument('--out', default=str(HERE))
    a = ap.parse_args(argv)
    D = lib.Data(a.root)
    out, _ = generate(D)
    for name, obj in out.items():
        p = os.path.join(a.out, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if name.endswith('.json'):
            dump(obj, p)
        else:
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write(obj)
        print('wrote', p, os.path.getsize(p))
    return 0


if __name__ == '__main__':
    sys.exit(main())
