"""Run the chaos tests, then the mutation twins (each broken behaviour must make the tests fail).

python -I tools/chaos/run_tests.py [--no-twins]   prints ALL OK when everything passes and every mutant is caught.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# mutant name -> a test it must break (named to keep twin runs short)
MUTANTS = {
    "no_loss": "ModelTests.test_loss_matches_expected_per_profile",
    "no_burst": "ModelTests.test_burst_loss_is_clustered",
    "half_latency": "ModelTests.test_latency_matches_per_profile",
    "no_reorder": "ModelTests.test_reorder_and_duplicate",
    "no_duplicate": "ModelTests.test_reorder_and_duplicate",
    "no_bandwidth": "ModelTests.test_bandwidth_cap",
    "no_partition": "ModelTests.test_partition_is_one_way_and_reversible",
}
UDP_MUTANTS = {
    "client_partition_both": "ClientModuleTests.test_partition_direction_and_self_heal",
    "no_loss": "UdpTests.test_profiles_over_real_udp",
    "half_latency": "UdpTests.test_profiles_over_real_udp",
    "no_partition": "UdpTests.test_midrun_changes_one_way_partition_and_disconnect",
}


PAIR_MUTANTS = {       # tools/chaos/chaos_pair.py (a pair joined through the relay), tested in test_chaos_pair.py
    "pair_joins_direct": "PairTests.test_setup_joins_through_the_relay_and_waits_for_joined",
    "pair_no_rejoin": "PairTests.test_teardown_puts_the_client_back_on_the_real_port",
}


def run(args, mutant="", test_file="test_chaos.py"):
    env = dict(os.environ, NVCHAOS_MUTANT=mutant)
    return subprocess.run([sys.executable, "-I", str(HERE / test_file), *args], cwd=HERE, env=env,
                          capture_output=True, text=True, errors="replace")


def main():
    for f in ("test_chaos.py", "test_chaos_pair.py"):
        p = run([], test_file=f)
        print(f + ": " + ((p.stdout + p.stderr).strip().splitlines()[-1] if (p.stdout + p.stderr).strip() else "no output"))
        if p.returncode != 0:
            print(p.stdout + p.stderr)
            print("RESULT: FAIL (real tests)")
            return 1
    if "--no-twins" in sys.argv:
        print("ALL OK (twins skipped)")
        return 0
    bad = []
    for table, f in ((MUTANTS, "test_chaos.py"), (UDP_MUTANTS, "test_chaos.py"), (PAIR_MUTANTS, "test_chaos_pair.py")):
        for m, test in table.items():
            q = run([test], m, f)
            caught = q.returncode != 0
            print(f"twin {m:13s} via {test.split('.')[-1]}: {'caught' if caught else 'NOT CAUGHT'}")
            if not caught:
                bad.append(m)
    if bad:
        print("RESULT: FAIL, uncaught mutants:", bad)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
