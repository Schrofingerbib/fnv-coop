"""Game-free tests of animcheck on synthetic probe streams. Every detector has a twin (ANIMCHECK_MUTANT=<kind>) that
run_tests.py requires to FAIL this suite."""
from __future__ import annotations

import json
import math
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from animcheck import detectors, synth  # noqa: E402
from animcheck.analyze import analyze  # noqa: E402
from animcheck.animgroups import category, name  # noqa: E402
from animcheck.collect import Collector  # noqa: E402
from animcheck.compare import angdiff, local_bones, percentile  # noqa: E402
from animcheck.config import DEFAULT  # noqa: E402
from animcheck.counters import check  # noqa: E402
from animcheck.model import Stream, parse_actor, read_probes, stream_from_probes, write_probes  # noqa: E402
from animcheck.synth import G, NPC_REF, State, make_probes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent.parent
TMP = ROOT / "out" / "T7-anim" / "tmp"
GOLDEN = ROOT / "plugin" / "animprobe" / "tests" / "golden_sample.json"
DUR = 6000.0


def one(machine, script, dur=DUR, **kw):
    return stream_from_probes(machine, make_probes(machine, {NPC_REF: script}, dur, **kw), use_rx=True)


def events(script, dur=DUR, expect=(), **kw):
    st = one("host", script, dur, **kw)
    return detectors.run_all(st.by_ref[NPC_REF], DEFAULT, expect)


def kinds(evs):
    return sorted({e.kind for e in evs})


class CleanTests(unittest.TestCase):
    def test_walk_run_idle_are_clean(self):
        self.assertEqual(events(synth.walker(120.0)), [])
        self.assertEqual(events(synth.walker(320.0)), [])
        self.assertEqual(events(synth.idler()), [])

    def test_noise_does_not_trigger(self):
        sc = synth.noisy(synth.walker(120.0), random.Random(3))
        self.assertEqual(events(sc), [])

    def test_jump_and_fall_are_not_floating(self):
        def jump(t):
            s = synth.walker(120.0)(t)
            if 2000 <= t < 3200:
                k = (t - 2000) / 1200.0
                s.pos = (s.pos[0], s.pos[1], s.pos[2] + 120 * 4 * k * (1 - k))
                s.groups = [(1, G["JumpLoop"])]
            return s
        self.assertEqual(events(jump), [])

    def test_teleport_with_cell_change_is_not_rubberband(self):
        def tele(t):
            s = synth.idler()(t)
            if t >= 3000:
                s.pos = (s.pos[0] + 5000, s.pos[1], s.pos[2] + 300)
                s.cell = 0x00000BEEF
            return s
        self.assertEqual(events(tele), [])

    def test_swimming_and_knockdown_are_not_judged(self):
        def sw(t):
            s = synth.idler()(t)
            if 2000 <= t < 5000:
                k = min(1.0, (t - 2000) / 1000.0, (5000 - t) / 1000.0)   # wade in over a second, wade out over a second
                s.swimming = True
                s.pos = (s.pos[0], s.pos[1], s.pos[2] - 60 * k)
            return s
        self.assertEqual(events(sw), [])

    def test_proper_death_is_clean(self):
        self.assertEqual(events(synth.proper_death(synth.walker(120.0), 2000.0), 9000.0), [])

    def test_first_person_flag_does_not_hide_the_third_person_body(self):
        probes = make_probes("host", {NPC_REF: lambda t: State(pos=(0, 0, 0), groups=[])}, 3000.0)
        for p in probes:
            p["actors"][0]["first_person"] = True
        st = stream_from_probes("host", probes, use_rx=True)
        self.assertEqual(kinds(detectors.run_all(st.by_ref[NPC_REF], DEFAULT)), ["tpose"])


class GlitchTests(unittest.TestCase):
    def test_sliding_moves_without_locomotion(self):
        evs = events(synth.inject_sliding(synth.idler(), 1000, 2500))
        self.assertIn("sliding", kinds(evs))
        self.assertEqual(kinds(evs), ["sliding"])
        e = [x for x in evs if x.kind == "sliding"][0]
        self.assertTrue(900 <= e.t0 <= 1300 and e.t1 <= 2600, (e.t0, e.t1))

    def test_sliding_speed_outside_animation_band(self):
        def too_fast(t):
            s = synth.walker(120.0)(t)
            if t >= 1000:
                s.pos = (s.pos[0] + 700.0 * (t - 1000) / 1000.0, s.pos[1], s.pos[2])
            return s
        self.assertIn("sliding", kinds(events(too_fast, 3000.0)))

    def test_tpose_no_sequence(self):
        evs = events(synth.inject_tpose(synth.idler(), 1000, 2000))
        self.assertEqual(kinds(evs), ["tpose"])

    def test_tpose_arms_out(self):
        evs = events(synth.inject_tpose(synth.idler(), 1000, 2000, mode="arms"))
        self.assertEqual(kinds(evs), ["tpose"])

    def test_floating(self):
        evs = events(synth.inject_float(synth.idler(), 1500, 3500, 90.0))
        self.assertEqual(kinds(evs), ["floating"])

    def test_sinking(self):
        evs = events(synth.inject_float(synth.idler(), 1500, 3500, -90.0))
        self.assertEqual(kinds(evs), ["sinking"])

    def test_jitter(self):
        evs = events(synth.inject_jitter(synth.idler(), 1000, 2500, 4.0))
        self.assertEqual(kinds(evs), ["jitter"])

    def test_rubberband_with_snapback(self):
        evs = events(synth.inject_rubber(synth.walker(120.0), 2000, 400.0, 200.0))
        rb = [e for e in evs if e.kind == "rubberband"]
        self.assertGreaterEqual(len(rb), 1)
        self.assertIn("snapped back", rb[0].detail)
        self.assertEqual(kinds(evs), ["rubberband"])

    def test_stuck_only_when_movement_is_expected(self):
        self.assertEqual(events(synth.idler()), [])
        evs = events(synth.idler(), expect=[(1000.0, 5000.0)])
        self.assertEqual(kinds(evs), ["stuck"])
        self.assertEqual(events(synth.walker(120.0), expect=[(1000.0, 5000.0)]), [])

    def test_deathpose_alive_animation_on_dead_actor(self):
        evs = events(synth.inject_death_alive(synth.idler(), 1000.0), 8000.0)
        self.assertEqual(kinds(evs), ["deathpose"])

    def test_deathpose_death_animation_on_live_actor(self):
        def wrong(t):
            s = synth.idler()(t)
            if t >= 1000:
                s.groups = [(0, G["Death"])]
            return s
        self.assertEqual(kinds(events(wrong, 8000.0)), ["deathpose"])

    def test_suppress_rule_hides_a_deliberate_teleport(self):
        sc = synth.inject_rubber(synth.walker(120.0), 2000, 400.0, 200.0)
        st = one("host", sc)
        rep = analyze({"host": st})
        self.assertGreaterEqual(rep["counters"]["glitch.rubberband"], 1)
        rep2 = analyze({"host": st}, suppress=[{"kind": "rubberband", "ref": NPC_REF, "t0": 1500, "t1": 2600}])
        self.assertEqual(rep2["counters"]["glitch.rubberband"], 0)


class CompareTests(unittest.TestCase):
    def pair(self, host_sc, client_sc, th=DEFAULT, **kw):
        host = one("host", host_sc, **kw)
        client = one("client", client_sc, **kw)
        return analyze({"host": host, "client": client}, th)

    def test_clean_delayed_client_is_in_sync(self):
        host = synth.walker(120.0)
        rep = self.pair(host, synth.delayed(host, 100.0))
        c = rep["counters"]
        self.assertLess(c["pose_err_p95"], 2.0, c)
        self.assertEqual(c["wrong_weapon"], 0)
        self.assertEqual(c["wrong_facing"], 0)
        self.assertEqual(c["group_mismatch_episodes"], 0)
        self.assertEqual(c["glitches"], 0)
        self.assertTrue(rep["ok"], rep["failures"])
        self.assertGreater(c["compared"], 50)

    def test_latency_jitter_inside_allowance(self):
        host = synth.walker(120.0)
        rep = self.pair(host, synth.delayed(host, 190.0))   # 100 ms buffer + 90 ms network
        self.assertEqual(rep["counters"]["group_mismatch_episodes"], 0)
        self.assertLess(rep["counters"]["pos_err_p95"], 12.0, rep["counters"])

    def test_far_too_late_client_is_caught(self):
        host = synth.walker(120.0)
        rep = self.pair(host, synth.delayed(host, 900.0))
        self.assertGreater(rep["counters"]["pos_err_p95"], 30.0, rep["counters"])

    def test_pose_defect_is_scored(self):
        host = synth.walker(120.0)

        def bad(t):
            s = synth.delayed(host, 100.0)(t)
            s.arm_offset = 150.0
            return s
        rep = self.pair(host, bad, th=DEFAULT.with_(pose_tol_units=5.0))
        self.assertGreater(rep["counters"]["pose_err_p95"], 8.0, rep["counters"])
        self.assertFalse(rep["ok"])
        self.assertTrue(any("pose_err_p95" in f for f in rep["failures"]))

    def test_wrong_weapon_needs_to_persist(self):
        host = synth.idler()

        def long_bad(t):
            s = host(t)
            if 2000 <= t < 4000:
                s.weapon_out, s.weapon_form = True, 0x0000FACE
            return s

        def blip(t):
            s = host(t)
            if 2000 <= t < 2150:
                s.weapon_out, s.weapon_form = True, 0x0000FACE
            return s
        self.assertGreaterEqual(self.pair(host, long_bad)["counters"]["wrong_weapon"], 1)
        self.assertEqual(self.pair(host, blip)["counters"]["wrong_weapon"], 0)

    def test_wrong_weapon_form_while_drawn(self):
        def drawn(form):
            def f(t):
                s = synth.idler()(t)
                s.weapon_out, s.weapon_form = True, form
                return s
            return f
        self.assertGreaterEqual(self.pair(drawn(0x1111), drawn(0x2222))["counters"]["wrong_weapon"], 1)
        self.assertEqual(self.pair(drawn(0x1111), drawn(0x1111))["counters"]["wrong_weapon"], 0)

    def test_wrong_facing(self):
        host = synth.idler()

        def bad(t):
            s = host(t)
            if 2000 <= t < 4000:
                s.rotz += 1.5
            return s
        c = self.pair(host, bad)["counters"]
        self.assertGreaterEqual(c["wrong_facing"], 1)
        self.assertGreater(c["facing_err_p95_deg"], 60.0)

    def test_group_mismatch(self):
        host = synth.walker(120.0)

        def bad(t):
            s = synth.delayed(host, 100.0)(t)
            if 2000 <= t < 4000:
                s.groups = [(0, G["Idle"])]
            return s
        c = self.pair(host, bad)["counters"]
        self.assertGreaterEqual(c["group_mismatch_episodes"], 1)
        self.assertGreater(c["group_mismatch_pct"], 10.0)

    def test_ref_map_for_puppets(self):
        host = stream_from_probes("host", make_probes("host", {0x14: synth.walker(120.0)}, DUR), use_rx=True)
        cl = stream_from_probes("client", make_probes("client", {0x00FF0001: synth.delayed(synth.walker(120.0), 100.0)}, DUR), use_rx=True)
        none = analyze({"host": host, "client": cl})
        self.assertEqual(none["counters"]["compared"], 0)
        mapped = analyze({"host": host, "client": cl}, ref_maps={"client": {0x14: 0x00FF0001}})
        self.assertGreater(mapped["counters"]["compared"], 50)
        self.assertLess(mapped["counters"]["pose_err_p95"], 2.0)

    def test_different_clocks_need_offset_estimate(self):
        walker = synth.walker(120.0)
        host = stream_from_probes("host", make_probes("host", {NPC_REF: walker}, DUR, rx=False), use_rx=False)
        cl = stream_from_probes("client", make_probes("client", {NPC_REF: synth.delayed(walker, 100.0)}, DUR, rx=False,
                                                       clock_offset=1750.0), use_rx=False)
        bad = analyze({"host": host, "client": cl})
        self.assertGreater(bad["counters"]["pos_err_p95"], 100.0)
        good = analyze({"host": host, "client": cl}, auto_offset=True)
        self.assertLess(good["counters"]["pos_err_p95"], 5.0, good["pairs"])

    def test_pose_math(self):
        b = synth.bones_for(State(pos=(10, 20, 0), rotz=0.7))
        a = parse_actor({"ref": "0x1", "pos": [10, 20, 0], "rot": [0, 0, 0.7], "bones": b}, 0.0)
        c = parse_actor({"ref": "0x1", "pos": [500, -40, 0], "rot": [0, 0, -2.0],
                         "bones": synth.bones_for(State(pos=(500, -40, 0), rotz=-2.0))}, 0.0)
        la, lc = local_bones(a.bones, a.rotz), local_bones(c.bones, c.rotz)
        for k in la:
            self.assertAlmostEqual(math.dist(la[k], lc[k]), 0.0, delta=0.05)
        self.assertAlmostEqual(angdiff(3.0, -3.0), 2 * math.pi - 6.0, places=6)
        self.assertEqual(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10)
        self.assertEqual(percentile([], 50), 0.0)


class PlumbingTests(unittest.TestCase):
    def test_counters_check(self):
        rep = {"counters": {"pose_err_p95": 5.0, "glitches": 0}}
        self.assertEqual(check(rep, {"pose_err_p95": "<=12", "glitches": 0}), [])
        self.assertEqual(len(check(rep, {"pose_err_p95": "<=3"})), 1)
        self.assertEqual(len(check(rep, {"nope": "==0"})), 1)
        self.assertEqual(len(check(rep, {"glitches": "garbage"})), 1)
        self.assertEqual(check({"counters": {"a": 2}}, {"a": ">1"}), [])

    def test_all_counter_keys_exist_and_clean_report_passes(self):
        rep = analyze({"host": one("host", synth.walker(120.0))})
        for k in ("pose_err_p50", "pose_err_p95", "pose_err_max", "group_mismatch_pct", "wrong_weapon", "wrong_facing",
                  "glitches", "glitch.sliding", "glitch.tpose", "glitch.floating", "glitch.sinking", "glitch.jitter",
                  "glitch.rubberband", "glitch.stuck", "glitch.deathpose", "samples", "actors"):
            self.assertIn(k, rep["counters"])
        self.assertTrue(rep["ok"])
        self.assertEqual(check(rep, {"glitches": "==0", "samples": ">10"}), [])

    def test_glitch_counter_reaches_report(self):
        rep = analyze({"host": one("host", synth.inject_float(synth.idler(), 1500, 3500))})
        self.assertEqual(rep["counters"]["glitch.floating"], 1)
        self.assertEqual(rep["counters"]["glitches"], 1)
        self.assertFalse(rep["ok"])
        self.assertEqual(check(rep, {"glitches": "==0"})[0].split(":")[0], "glitches")

    def test_jsonl_roundtrip(self):
        TMP.mkdir(parents=True, exist_ok=True)
        probes = make_probes("host", {NPC_REF: synth.walker(120.0)}, 1000.0)
        p = TMP / "rt.probe.jsonl"
        self.assertEqual(write_probes(p, probes), len(probes))
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n# comment\nnot json\n")
        back = read_probes(p)
        self.assertEqual(len(back), len(probes))
        self.assertEqual(back[3]["actors"][0]["pos"], probes[3]["actors"][0]["pos"])

    def test_cli_analyze_exit_codes(self):
        import subprocess
        TMP.mkdir(parents=True, exist_ok=True)
        host, cl = TMP / "cli_host.jsonl", TMP / "cli_client.jsonl"
        write_probes(host, make_probes("host", {NPC_REF: synth.walker(120.0)}, 3000.0))
        write_probes(cl, make_probes("client", {NPC_REF: synth.delayed(synth.walker(120.0), 100.0)}, 3000.0))
        cli = str(ROOT / "tools" / "animcheck" / "cli.py")
        env = {k: v for k, v in os.environ.items() if k != "ANIMCHECK_MUTANT"}
        env["ANIMCHECK_MUTANT"] = os.environ.get("ANIMCHECK_MUTANT", "")
        r = subprocess.run([sys.executable, "-I", cli, "analyze", f"host={host}", f"client={cl}", "--html", str(TMP / "cli.html"),
                            "--json", str(TMP / "cli.json"), "--nvfr", str(TMP / "cli.nvfr")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)
        self.assertTrue((TMP / "cli.html").read_text(encoding="utf-8").startswith("<!doctype html>"))
        self.assertEqual(json.loads((TMP / "cli.json").read_text(encoding="utf-8"))["schema"], "animcheck.report.v1")
        r2 = subprocess.run([sys.executable, "-I", cli, "analyze", f"host={host}", "--assert", "samples<5"], capture_output=True, text=True, env=env)
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        r3 = subprocess.run([sys.executable, "-I", cli, "analyze", f"host={host}", "--assert", "nonsense"], capture_output=True, text=True, env=env)
        self.assertEqual(r3.returncode, 2)

    def test_nvfr_export(self):
        flight = ROOT / "tools" / "flight" / "nvfr.py"
        if not flight.exists():
            self.skipTest("tools/flight/nvfr.py not present")
        from animcheck.nvfr_export import report_to_records
        rep = analyze({"host": one("host", synth.inject_float(synth.idler(), 1500, 3500))})
        recs = report_to_records(rep)
        texts = [r["text"] for r in recs]
        self.assertTrue(any(t.startswith("animcheck {") for t in texts))
        self.assertTrue(any(t.startswith("animcheck.floating ") for t in texts))
        self.assertTrue(any(t.startswith("animcheck failed:") for t in texts))
        from animcheck.nvfr_export import write_trace
        sys.path.insert(0, str(ROOT / "tools" / "flight"))
        import nvfr
        TMP.mkdir(parents=True, exist_ok=True)
        path = TMP / "export.nvfr"
        n = write_trace(path, rep)
        back = nvfr.load(str(path))
        marks = [r for r in back["records"] if r["type"] == nvfr.T_MARKER]
        self.assertEqual(len(marks), n)
        self.assertFalse(back["warnings"], back["warnings"])
        self.assertEqual(marks[0]["kind"], 4)
        self.assertTrue(marks[0]["text"].startswith("animcheck {"))
        self.assertTrue(any(m["kind"] == 1 and m["text"].startswith("animcheck.floating ") for m in marks))

    def test_collector_with_fake_bridge(self):
        class Clock:
            t = 100.0

            def __call__(self):
                return Clock.t

            @staticmethod
            def sleep(d):
                Clock.t += d

        class Bridge:
            def __init__(self, script, delay):
                self.script, self.delay, self.n = script, delay, 0

            def call(self, method, params=None):
                assert method == "anim.probe"
                self.n += 1
                t = (Clock.t - 100.0) * 1000.0
                return {"v": 1, "frame": self.n, "t_ms": t, "cost_us": 30.0,
                        "actors": [synth.actor_dict(NPC_REF, self.script(max(0, t - self.delay)), True, t)]}

        host_sc = synth.walker(120.0)
        c = Collector({"host": Bridge(host_sc, 0.0), "client": Bridge(host_sc, 100.0)}, clock=Clock())
        c.run(seconds=3.0, hz=20, sleep=Clock.sleep)
        self.assertGreaterEqual(len(c.probes["host"]), 55)
        rep = analyze(c.streams())
        self.assertGreater(rep["counters"]["compared"], 40)
        self.assertEqual(rep["counters"]["glitches"], 0, rep["machines"])
        self.assertLess(rep["counters"]["pose_err_p95"], 2.0)

        class Broken:
            def call(self, m, p=None):
                raise OSError("pipe closed")
        c2 = Collector({"a": Broken(), "b": Bridge(host_sc, 0.0)}, clock=Clock())
        self.assertEqual(c2.poll_once(), 1)
        self.assertEqual(c2.errors["a"], 1)

    def test_collect_asks_for_the_player(self):
        # In game 2026-10-21: anim.probe {near} walks the player's cell list, which does not hold the player, so `collect` never saw it.
        from animcheck.collect import probe_params

        class EngineLike:
            """Answers like the plugin: `near` gives the NPCs only, `refs` adds the named refs."""
            def __init__(self):
                self.params = []

            def call(self, method, params=None):
                self.params.append(params)
                acts = [{"ref": "0x0014C44A"}] if params and "near" in params else []
                if params and "player" in params.get("refs", []):
                    acts.insert(0, {"ref": "0x00000014", "player": True})
                return {"v": 1, "frame": 1, "t_ms": 0.0, "cost_us": 1.0, "actors": acts}

        b = EngineLike()
        c = Collector({"host": b})
        c.poll_once()                                         # the default parameters
        c.poll_once(probe_params(1500, 16))                   # what `cli.py collect` sends
        for p in b.params:
            self.assertIn("player", p["refs"])
            self.assertIn("near", p)
        self.assertEqual(probe_params(1500, 16)["max"], 16)
        # twin: the old request (near only) gets no player from the same engine
        self.assertFalse(any(a.get("player") for a in b.call("anim.probe", {"near": {"radius": 1500}, "max": 16})["actors"]))

    def test_animgroups(self):
        self.assertEqual(name(G["Forward"]), "Forward")
        self.assertEqual(category(G["Forward"]), "walk")
        self.assertEqual(category(G["FastForward"]), "run")
        self.assertEqual(category(G["Idle"]), "idle")
        self.assertEqual(category(G["Death"]), "death")
        self.assertEqual(category(G["ReloadA"]), "reload")
        self.assertEqual(category(G["AttackRight"]), "attack")
        self.assertEqual(category(G["JumpLoop"]), "jump")
        self.assertEqual(category(9999), "unknown")
        self.assertEqual(len(synth.NAMES), 245)

    def test_move_flags_as_measured_in_game(self):
        # 2026-10-21, player in game: idle in run mode 0x240, walk 0x141, run 0x241, sneak idle 0x640, sneak walk 0x541
        from animcheck import model
        cases = {0x240: False, 0x141: False, 0x241: False, 0x640: True, 0x541: True}
        for flags, sneaking in cases.items():
            a = parse_actor({"ref": "0x14", "player": True, "move_flags": flags}, 0.0)
            self.assertEqual(a.sneaking, sneaking, hex(flags))
        # twin: the old constants (sneak = 1 << 9) call plain run mode "sneaking"
        self.assertTrue(bool(0x241 & (1 << 9)))
        self.assertEqual(model.MOVE_SNEAK, 0x400)

    def test_cpp_golden_sample_parses(self):
        """plugin/animprobe/tests/golden_sample.json is what the C++ side emits (checked byte for byte by its test)."""
        if not GOLDEN.exists():
            self.skipTest("golden sample not generated yet")
        a = parse_actor(json.loads(GOLDEN.read_text(encoding="utf-8")), 0.0)
        self.assertEqual(a.ref, 0x0010A2B3)
        self.assertTrue(a.player)
        self.assertEqual(a.pos, (1.5, -2.0, 3.0))
        self.assertEqual([s.group for s in a.seqs], [12])
        self.assertEqual(a.move_flags, 0x100)
        self.assertEqual(a.bones["Bip01 Head"], (1.0, 2.0, 3.0))
        self.assertEqual(a.weapon_form, 0x00001234)
        self.assertTrue(a.in_combat)


if __name__ == "__main__":
    unittest.main()
