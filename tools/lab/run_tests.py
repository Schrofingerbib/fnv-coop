"""One command for the lab runner: the offline tests, then the mutation twins. Prints ALL OK when everything holds.

    python -I tools/lab/run_tests.py

A twin runs the tests with one deliberate bug in the RUNNER switched on (LAB_MUTANT=<name>); the tests that guard that rule must then FAIL.
The bugs of the fake WORLD (tools/lab/labfake.py BUGS, 31 of them) are twins too: test_lab.py checks that each one fails its room at its check,
so a room that stopped noticing one would fail the unit tests directly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PY = [sys.executable, "-I", "-X", "utf8"]
TEST = "tools/lab/tests/test_lab.py"

# name -> (what the bug does, the tests that must notice it)
TWINS = {
    "checks_never_fail": ("every check passes, whatever it compared", ["FakeLab.test_each_bug_fails_its_room_at_its_check"]),
    "no_fail_fast": ("a room goes on after its first failed check", ["FakeLab.test_each_bug_fails_its_room_at_its_check"]),
    "no_time_box": ("a room is not stopped by its time box", ["Boxes.test_a_room_over_its_time_box_is_a_timeout_with_the_checks_so_far"]),
    "no_run_budget": ("the whole-run budget is ignored by rooms that have not started", ["Boxes.test_the_run_budget_stops_the_rooms_that_have_not_started",
                                                                                         "Boxes.test_a_room_never_runs_longer_than_what_is_left_of_the_run"]),
    "no_clean_rule": ("a PASS with digest repairs still passes", ["FakeLab.test_each_bug_fails_its_room_at_its_check", "FakeLab.test_counters_that_moved_are_listed"]),
    "summary_unbounded": ("summary.md is not kept under 5 KB", ["Reports.test_the_summary_stays_under_5kb_even_with_long_texts", "Reports.test_summary_is_short_and_has_the_table_the_blocks_and_the_comparison"]),
    "digest_wait_unbounded": ("spawn waits 30 s for a first ok digest even after one was judged bad", ["FakeLab.test_a_digest_that_never_agrees_fails_spawn_fast_with_the_divergence"]),
    "enable_all_at_once": ("the fight room enables all its hostiles together again", ["FakeLab.test_the_fight_room_enables_one_hostile_at_a_time_so_none_walks_up_to_the_shooter"]),
    "no_weaken": ("the fight room shoots its targets at full health again", ["FakeLab.test_the_fight_room_weakens_each_target_through_the_host_before_it_is_shot"]),
    "compare_blind": ("the comparison with the previous run shows nothing", ["Reports.test_write_run_makes_both_files_and_compares_with_the_newest_earlier_run"]),
}


def run(args, env_extra=None, timeout=900):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(PY + args, cwd=ROOT, capture_output=True, text=True, env=env, timeout=timeout)


def main() -> int:
    bad = 0
    cp = run([TEST])
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-3:]
    print("unit tests:", " | ".join(tail))
    if cp.returncode:
        print((cp.stdout + cp.stderr)[-4000:])
        bad += 1
    for name, (what, tests) in TWINS.items():
        t = run([TEST] + tests, {"LAB_MUTANT": name})
        caught = t.returncode != 0
        print(f"twin {name:18s} {'caught' if caught else 'NOT CAUGHT'}   ({what})")
        if not caught:
            bad += 1
    print("RESULT: " + (f"{bad} problem(s)" if bad else "ALL OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
