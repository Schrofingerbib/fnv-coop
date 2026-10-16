"""Re-read the L4/L5 sources and rewrite l5_snapshot.json (the PINNED facts run_tests.py checks fnmap.py against).

    python -I questlines/deadmoney/refresh_l5.py        # review `git diff`-style output, then update fnmap.py rows that changed

The L5/L4 lanes keep adding hooks and effect types while the questlines are being analysed, and CONTRACTS.md forbids tests that read a file another
lane is rewriting. So run_tests.py reads ONLY l5_snapshot.json; it merely warns (never fails) when the live sources differ from the pin.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
SOURCES = ['synccore/include/fnvcoop/effect.h', 'plugin/hooks/engine_hooks.cpp', 'plugin/hooks/apply_remote.cpp']
PIN = os.path.join(HERE, 'l5_snapshot.json')


def read(rel):
    with open(os.path.join(REPO, rel), encoding='utf-8') as f:
        return f.read()


def facts():
    eff = re.search(r'using Effect = std::variant<(.*?)>;', read(SOURCES[0]), re.S).group(1)
    hooks = read(SOURCES[1])
    block = set(re.findall(r'BLOCK_CMD\((\w+)\)', hooks))
    captured, blockonly = [], []
    for spec, handler in re.findall(r'HK\((\w+),\s*h_(\w+),', hooks):
        (blockonly if handler in block else captured).append(spec)
    applied = sorted(set(re.findall(r'std::get_if<(\w+)>', read(SOURCES[2]))))
    return {
        'effect_names': sorted(x.strip() for x in eff.split(',')),
        'captured_hooks': sorted(captured),
        'block_only_hooks': sorted(blockonly),
        'applied_effects': applied,
    }


def digests():
    return {rel: hashlib.sha256(read(rel).encode('utf-8')).hexdigest()[:16] for rel in SOURCES}


def main():
    f = facts()
    old = json.load(open(PIN, encoding='utf-8')) if os.path.exists(PIN) else {}
    out = dict(f, sources=digests(),
               doc='Pinned L4/L5 facts for questlines/deadmoney. Regenerate with refresh_l5.py, then review fnmap.py.')
    for k in ('effect_names', 'captured_hooks', 'block_only_hooks', 'applied_effects'):
        a, b = set(old.get(k, [])), set(f[k])
        if a != b:
            print('%s: +%s -%s' % (k, sorted(b - a), sorted(a - b)))
    with open(PIN, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=1)
        fh.write('\n')
    print('wrote %s (effects %d, captured hooks %d, block-only %d, applied %d)'
          % (os.path.basename(PIN), len(f['effect_names']), len(f['captured_hooks']), len(f['block_only_hooks']), len(f['applied_effects'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
