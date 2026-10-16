"""Regenerate every generated deliverable of the NCR lane, in dependency order.

python -I questlines/ncr/build_all.py          # inventory.json, gaps.json/md, sweep.json, dialogue_specs.json (+ stats block in dialogue.md)
python -I questlines/ncr/build_all.py --status # also run the tests and rewrite internal questline notes (status summary with pasted output)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dialogue  # noqa: E402
import gaps  # noqa: E402
import inventory  # noqa: E402
import sweep  # noqa: E402


def main(argv):
    for mod in (inventory, gaps, sweep, dialogue):
        rc = mod.main()
        if rc:
            return rc
    if '--status' in argv:
        import status
        return status.main()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
