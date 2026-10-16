"""Regenerate every deliverable of the prologue questline (deterministic, game-free).

    python -I questlines/prologue/build.py            # writes inventory.json sweep.json gaps.json gaps.md dialogue_specs.json
                                                      #        dialogue.md rules.patch.json policy_review.md

run_tests.py imports `render_all()` and compares it byte for byte with the files on disk.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dialogue  # noqa: E402
import gaps  # noqa: E402
import inventory  # noqa: E402
import policy  # noqa: E402
import sweep  # noqa: E402


def dumps(o):
    return json.dumps(o, indent=1, ensure_ascii=False) + '\n'


def render_all():
    """-> {filename: text} for every generated deliverable."""
    specs = dialogue.build_specs()
    spec_infos = {st['info'] for s in specs for st in s['steps'] if st['action'] == 'pick'}
    g = gaps.build_gaps()
    inv = inventory.build_inventory()
    patch = policy.build_patch(g)
    return {
        'inventory.json': dumps(inv),
        'sweep.json': dumps(sweep.build_sweep(spec_infos)),
        'gaps.json': dumps(g),
        'gaps.md': gaps.render_md(g),
        'dialogue_specs.json': dumps(dict(schema=1, kind='dialogue-set', questline='prologue', scenarios=specs)),
        'dialogue.md': dialogue.render_md(specs),
        'rules.patch.json': dumps(patch),
        'policy_review.md': policy.build_review(g, patch, inv),
    }


def main():
    out = render_all()
    for name, text in out.items():
        with open(os.path.join(HERE, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print('wrote %-22s %7d bytes' % (name, len(text.encode('utf-8'))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
