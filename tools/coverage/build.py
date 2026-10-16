"""Build coverage artifacts.

  python -I tools/coverage/build.py [--runs runs] [--out out/coverage] [--policies docs/quest-policies]
                                    [--target game-pass] [--no-policies]
Writes: <out>/index.html, <out>/summary.md, <out>/questgraph.json, <out>/status.json, <policies>/*.md
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model                         # noqa: E402
from graph import Graph              # noqa: E402
from status import build_status      # noqa: E402
from report import build_report, render_markdown   # noqa: E402
from dashboard import render_html    # noqa: E402
from policies import write_policies  # noqa: E402
import build_rules               # noqa: E402
import hostowned                 # noqa: E402
import backlog as backlog_mod    # noqa: E402
import sweeps as sweeps_mod      # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--qdir', default=model.QDIR)
    ap.add_argument('--runs', default=os.path.join(model.ROOT, 'runs'))
    ap.add_argument('--out', default=os.path.join(model.ROOT, 'out', 'coverage'))
    ap.add_argument('--policies', default=os.path.join(model.ROOT, 'docs', 'quest-policies'))
    ap.add_argument('--target', default='game-pass', choices=model.LADDER[1:])
    ap.add_argument('--no-policies', action='store_true')
    a = ap.parse_args(argv)

    data = model.load_quests(a.qdir)
    g = Graph(data)
    status, run_info = build_status(data, a.runs)
    extra = {}
    try:
        extra['backlog'] = backlog_mod.write(a.out)
    except (OSError, ValueError, KeyError) as e:          # census.json missing etc.: dashboard still builds
        print('backlog skipped:', e)
    extra['sweeps'] = sweeps_mod.build()
    rep = build_report(data, g, status, run_info, a.target, extra)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, 'index.html'), 'w', encoding='utf-8').write(render_html(rep))
    open(os.path.join(a.out, 'summary.md'), 'w', encoding='utf-8').write(render_markdown(rep))
    json.dump(g.to_json(), open(os.path.join(a.out, 'questgraph.json'), 'w', encoding='utf-8'))
    json.dump({'report': {k: v for k, v in rep.items() if k != 'quests'}, 'status': status},
              open(os.path.join(a.out, 'status.json'), 'w', encoding='utf-8'), indent=1)
    n = 0
    if not a.no_policies:
        n = len(write_policies(data, a.policies))
        rules, errs, _rrep, _mm = build_rules.write_all(data, a.policies)
        print(f"rules.json: {len(rules['quests'])} quests, {len(errs)} validation errors")
    ho = hostowned.write(data, a.out)
    print(f"hostowned: {len(ho)} actors -> {a.out}")
    print(f"coverage: {rep['story_total']} story quests, totals {rep['totals']}; "
          f"{len(g.edges)} graph edges; {n} policies -> {a.out}")
    return rep


if __name__ == '__main__':
    main()
