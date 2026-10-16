"""Generate every machine-made deliverable of the Mr. House questline lane into questlines/house/.

    python -I questlines/house/gen.py            # writes inventory.json sweep.json gaps.json gaps.md rules.patch.json
    python -I questlines/house/gen.py --check    # regenerate in memory and fail if the files on disk differ

Hand-written deliverables (policy_review.md, dialogue.md, internal questline notes) are validated by run_tests.py, not generated.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gaps  # noqa: E402
import inventory  # noqa: E402
import qlib  # noqa: E402
import rules_patch  # noqa: E402
import sweep  # noqa: E402
import universe  # noqa: E402


def dumps(doc, compact=False):
    if compact:
        return json.dumps(doc, separators=(',', ':'), ensure_ascii=False) + '\n'
    return json.dumps(doc, indent=1, ensure_ascii=False) + '\n'


def build_all(d=None):
    d = d or qlib.data()
    uni = universe.build(d)
    g = gaps.build(d, uni)
    return {
        'inventory.json': dumps(inventory.build(d)),
        'sweep.json': dumps(sweep.build(d)),
        'gaps.json': dumps(g, compact=True),
        'gaps.md': gaps.to_md(g),
        'rules.patch.json': dumps(rules_patch.build(d)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args(argv)
    out = build_all()
    bad = []
    for name, text in out.items():
        p = os.path.join(HERE, name)
        if a.check:
            cur = open(p, encoding='utf-8').read() if os.path.exists(p) else None
            if cur != text:
                bad.append(name)
        else:
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
            print(f'wrote {name} ({len(text)} bytes)')
    if bad:
        print('STALE: ' + ', '.join(bad))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
