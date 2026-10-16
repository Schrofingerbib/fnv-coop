"""python -I questlines/systems/generate.py  -> writes every deliverable into questlines/systems/."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402
import docgen  # noqa: E402
import qlib  # noqa: E402
import rulegen  # noqa: E402
import scope  # noqa: E402


def compute():
    recs = scope.build_scope()
    inv = build.build_inventory(recs)
    types = build.build_types(recs)
    comp = build.build_companions()
    cen, locs = build.build_census(recs)
    gaps = build.build_gaps(recs, locs, cen)
    sweep = build.build_sweep(recs)
    patch = rulegen.build_rules_patch(recs)
    return dict(recs=recs, inventory=inv, types=types, companions=comp, census=cen, gaps=gaps, sweep=sweep, patch=patch)


def write_all(d):
    def wj(name, obj):
        with open(os.path.join(qlib.HERE, name), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(obj, f, indent=1, ensure_ascii=False)
            f.write('\n')

    def wt(name, text):
        with open(os.path.join(qlib.HERE, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)

    wj('inventory.json', d['inventory'])
    wj('types.json', d['types'])
    wt('types.md', docgen.render_types(d['types'], d['recs']))
    wj('companions.json', d['companions'])
    wt('companions.md', docgen.render_companions(d['companions']))
    wj('census.json', d['census'])
    wt('census.md', docgen.render_census(d['census']))
    wj('gaps.json', d['gaps'])
    wt('gaps.md', docgen.render_gaps(d['gaps'], d['census']))
    wj('sweep.json', d['sweep'])
    wj('rules.patch.json', d['patch'])
    wt('policy_review.md', docgen.render_policy_review(d['recs'], d['patch'], d['companions'], d['census'], d['gaps']))
    wt('dialogue.md', docgen.render_dialogue(d['recs'], d['companions']))


if __name__ == '__main__':
    d = compute()
    write_all(d)
    print('wrote', len(d['recs']), 'quests;', d['census']['distinct_functions'], 'functions;', d['gaps']['counts']['unsupported_calls'], 'gap calls')
