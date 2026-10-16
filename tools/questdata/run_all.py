"""Full pipeline: extract -> classify -> scenarios. python tools/questdata/run_all.py [--data DIR] [--out DIR]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify, effects, extract, m1, scenarios  # noqa: E401,E402

args = sys.argv[1:]
extract.main(args)
out = args[args.index('--out') + 1] if '--out' in args else None
sub = ['--in', out, '--out', out] if out else []
effects.main(sub + (['--data', args[args.index('--data') + 1]] if '--data' in args else []))
classify.main(sub)
scenarios.main(sub)
m1.main(sub)
