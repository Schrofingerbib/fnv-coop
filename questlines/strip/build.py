"""Build every generated deliverable of the Q-strip questline: python -I questlines/strip/build.py"""
import json
import os
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dialogue  # noqa: E402
import facts  # noqa: E402
import gaps  # noqa: E402
import inventory  # noqa: E402
import policy  # noqa: E402
import review  # noqa: E402
import scope  # noqa: E402
import sweep  # noqa: E402


def dump(path, obj):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=False)
        f.write('\n')


def build_all(data=None):
    data = data or scope.Data()
    bad = data.resolve_scope()
    if bad:
        raise SystemExit('unresolved scope names:\n' + '\n'.join(bad))
    inv = inventory.build(data)
    gp = gaps.build(data)
    sw = sweep.build(data, inv)
    fc = facts.compute(data, gp)
    patch = policy.build_patch(data)
    md = review.build(data, gp, fc, patch, inv)
    dl = dialogue.build(data)
    sp = dialogue.specs(data, dl)
    dmd = dialogue.md(data, dl, sp, gp, fc, inv)
    return dict(inventory=inv, gaps=gp, sweep=sw, facts=fc, patch=patch, review_md=md, dialogue=dl, specs=sp, dialogue_md=dmd)


def main():
    out = build_all()
    dump(os.path.join(HERE, 'inventory.json'), out['inventory'])
    dump(os.path.join(HERE, 'gaps.json'), out['gaps'])
    dump(os.path.join(HERE, 'sweep.json'), out['sweep'])
    dump(os.path.join(HERE, 'rules.patch.json'), out['patch'])
    dump(os.path.join(HERE, 'dialogue.json'), out['dialogue'])
    dump(os.path.join(HERE, 'dialogue_specs.json'), dict(schema=1, kind='dialogue', specs=out['specs']))
    with open(os.path.join(HERE, 'dialogue.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(out['dialogue_md'])
    with open(os.path.join(HERE, 'policy_review.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(out['review_md'])
    with open(os.path.join(HERE, 'gaps.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(gaps.md(out['gaps']))
    inv, sw, g = out['inventory'], out['sweep'], out['gaps']
    print('inventory:', inv['counts'])
    print('sweep: stages', sw['summary']['stages'], 'exclusions', len(sw['exclusions']))
    print('gaps: unsupported fns', g['unsupported_functions'], 'occurrences', g['unsupported_occurrences'], g['unsupported_by_severity'])


if __name__ == '__main__':
    main()
