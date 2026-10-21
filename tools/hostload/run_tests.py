"""Host-load revert tests: pre-images for SetWeather, SetTimeScale, RefScriptVar, SetMapMarker, locks, ObjectState (open bit),
RefAddItem / RefRemoveItem / RemoveAllItems and SpawnRef / DespawnRef, plus a twin per effect (a deliberately broken variant the
test must catch).

    python -I tools/hostload/run_tests.py [--no-build]

Builds only fnvcoop_tests in the shared tree build/all (tools/build/build.py: out/locks/build.lock held only while compiling), runs the unit group, the
1000 host-load sim seeds, the save/load sim scenarios and every host-load twin, and prints ALL OK when everything passes and every
twin is caught. Game-free: no game is launched.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "build" / "all" / "synccore" / "fnvcoop_tests.exe"

UNIT_GROUPS = ["hostload", "round3", "baseline", "saveload"]
SIM_TESTS = ["host_load_seeds_1000_converge", "host_and_client_save_load_scenarios_converge"]
TWINS = [
    "hostload_unit_set_weather", "hostload_unit_set_time_scale", "hostload_unit_ref_script_var", "hostload_unit_set_map_marker",
    "hostload_unit_lock", "hostload_unit_object_state_open", "hostload_unit_object_state_sticky_open", "hostload_unit_ref_add_item", "hostload_unit_ref_remove_item",
    "hostload_unit_remove_all_items", "hostload_unit_clear_pre_travels", "hostload_sim_clear_pre_travels", "hostload_unit_spawn_ref", "hostload_unit_despawn_ref",
    "hostload_sim_set_weather", "hostload_sim_set_time_scale", "hostload_sim_ref_script_var", "hostload_sim_set_map_marker",
    "hostload_sim_object_state_open", "hostload_sim_ref_inventory", "hostload_sim_spawn_despawn",
    "round3_origins_ignored", "round3_angle_preimage_ignored", "round3_ref_inv_preimage_ignored", "round3_spawn_rebase_ignored",
]


def build():
    """Builds fnvcoop_tests in the SHARED tree (build/all) through the build driver, which holds out/locks/build.lock only while compiling."""
    p = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "build" / "build.py"), "--target", "fnvcoop_tests", "--label", "hostload tests"],
                       cwd=ROOT, capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        print((p.stdout + p.stderr)[-4000:])
    return p.returncode == 0


def run(*args):
    p = subprocess.run([str(EXE), *args], capture_output=True, text=True, errors="replace")
    return p.returncode == 0, p.stdout + p.stderr


def main():
    if "--no-build" not in sys.argv and not build():
        print("RESULT: FAIL (build)")
        return 1
    bad = []
    for g in UNIT_GROUPS:
        ok, out = run("--group", g)
        print(f"group {g}: {out.strip().splitlines()[-1]}")
        if not ok:
            print(out)
            bad.append("group " + g)
    for t in SIM_TESTS:
        ok, out = run("--group", "sim", "--name", t)
        lines = [l for l in out.strip().splitlines() if "seeds" in l or "save/load" in l]
        print(f"sim {t}: {'ok' if ok else 'FAIL'}  {lines[0].strip() if lines else ''}")
        if not ok:
            print(out)
            bad.append("sim " + t)
    for t in TWINS:
        ok, out = run("--group", "twin", "--name", t)
        print(f"twin {t}: {'caught' if ok else 'NOT CAUGHT'}")
        if not ok:
            bad.append("twin " + t)
    if bad:
        print("RESULT: FAIL:", bad)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
