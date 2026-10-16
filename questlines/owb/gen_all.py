"""Regenerate every deliverable of the OWB questline.

    python -I questlines/owb/gen_all.py [--check]

--check writes nothing and exits 1 when a committed file differs from what would be generated (used by run_tests.py).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
import dialogue_specs  # noqa: E402
import docgen  # noqa: E402
import docgen_status  # noqa: E402
import gaps  # noqa: E402
import inventory  # noqa: E402
import rules_patch  # noqa: E402
import sweep  # noqa: E402

STATUS_PATH = os.path.join(common.ROOT, 'docs', 'questlines', 'owb.md')
TEST_OUTPUT = os.path.join(HERE, 'last_test_output.txt')


def jdump(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False) + '\n'


def patch_json(n_player_bound):
    return {
        'schema': 1, 'target': 'docs/quest-policies/rules.json', 'slug': 'owb',
        'note': 'Proposal only (rules.json is owned by L9). op=replace: quest already has an entry; op=add: it has none.',
        'base_kinds': rules_patch.BASE_KINDS, 'edid_param_keys': list(rules_patch.EDID_PARAM_KEYS),
        'quests': rules_patch.QUESTS, 'new_kinds': rules_patch.new_kinds(n_player_bound), 'new_params': rules_patch.NEW_PARAMS,
        'effect_proposals': rules_patch.EFFECT_PROPOSALS,
    }


def build_all(d=None, test_output=None):
    """-> {path: text}"""
    d = d or common.Data()
    inv = inventory.build_inventory(d)
    sw = sweep.build_sweep(d, inv)
    gp = gaps.build_gaps(d)
    specs = dialogue_specs.build(d)
    c = docgen.Ctx(d, inv, sw, gp, specs)
    if test_output is None:
        test_output = open(TEST_OUTPUT, encoding='utf-8').read() if os.path.exists(TEST_OUTPUT) else ''
    return {
        os.path.join(HERE, 'inventory.json'): jdump(inv),
        os.path.join(HERE, 'sweep.json'): jdump(sw),
        os.path.join(HERE, 'gaps.json'): jdump(gp),
        os.path.join(HERE, 'gaps.md'): gaps.to_markdown(gp),
        os.path.join(HERE, 'rules.patch.json'): jdump(patch_json(len(gp['quest_scripts_player_bound']))),
        os.path.join(HERE, 'policy_review.md'): docgen.policy_review_md(c),
        os.path.join(HERE, 'dialogue.md'): docgen.dialogue_md(c),
        STATUS_PATH: docgen_status.status_md(c, test_output),
    }, (d, inv, sw, gp, specs, c)


def main(argv):
    out, _ = build_all()
    bad = []
    for path, text in out.items():
        if '--check' in argv:
            cur = open(path, encoding='utf-8', newline='').read() if os.path.exists(path) else None
            if cur != text:
                bad.append(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
            print('wrote %s (%d bytes)' % (os.path.relpath(path, common.ROOT), len(text.encode('utf-8'))))
    if bad:
        print('STALE: ' + ', '.join(os.path.relpath(b, common.ROOT) for b in bad))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
