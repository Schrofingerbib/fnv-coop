"""tools/runner/npc_pick.py: which NPC an in-game proof uses when the one from the manifest is not loaded (no game).

    python -I tools/runner/tests/test_npc_pick.py

Sweep 2026-10-22 .. 23: actor_port "FalloutNV.esm:104C70 is not loaded (has3D false ...)", npc_caps "the NPC cannot be read", actor_port_two_copy
"coop.owner does not know the NPC on both copies" - the hard-coded powder ganger was not loaded after the scenario jump and nothing else was tried.

Mutation twins (NPC_PICK_MUTANT=<name>, one deliberate bug each) must each make at least one test fail; Twin.test_every_twin_is_caught runs them:
  no_fallback        the hint is the only NPC ever tried
  no_wait            the first poll is the last (a cell that loads a few seconds late is NOT READY)
  dead_allowed       a dead NPC is a good NPC
  no_3d_check        an NPC without 3D is a good NPC
  player_allowed     the player is a good NPC
  listing_order      the first listed NPC is used instead of the nearest
  load_order_dropped Plugin.esm:HEX ignores the plugin's load-order index
The scripts that must use it (tools/actors/proof_actor_port*.py, tools/animcheck/npc_proof.py) are checked by Wiring.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent
REPO = HERE.parents[2]
sys.path.insert(0, str(RUNNER))
import npc_pick as P  # noqa: E402

GOOD = {"ok": True, "valid": True, "has3D": True, "dead": False, "disabled": False, "restrained": False, "creature": False}


def rd(**kw):
    return dict(GOOD, **kw)


def cand(ref, i=None):
    return {"ref": ref, "id": i}


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


class Why(unittest.TestCase):
    def test_a_loaded_living_free_character_is_fine(self):
        self.assertIsNone(P.why_unusable(cand("FalloutNV.esm:104C70"), rd()))

    def test_every_reason_is_named(self):
        c = cand("FalloutNV.esm:104C70")
        self.assertEqual(P.why_unusable(c, rd(has3D=False)), "no 3D")
        self.assertEqual(P.why_unusable(c, rd(dead=True)), "dead")
        self.assertEqual(P.why_unusable(c, rd(disabled=True)), "disabled")
        self.assertEqual(P.why_unusable(c, rd(restrained=True)), "restrained")
        self.assertIsNone(P.why_unusable(c, rd(restrained=True), allow_restrained=True))
        self.assertEqual(P.why_unusable(c, rd(creature=True)), "creature")
        self.assertEqual(P.why_unusable(c, None), "unreadable")
        self.assertEqual(P.why_unusable(c, {"ok": False, "valid": False, "has3D": False}), "not a loaded actor")

    def test_the_player_is_never_the_npc(self):
        self.assertEqual(P.why_unusable(cand("FalloutNV.esm:000014"), rd()), "the player")
        self.assertEqual(P.why_unusable(cand("x", 0x14), rd()), "the player")


class Choose(unittest.TestCase):
    def test_a_usable_hint_wins_even_when_others_are_nearer(self):
        hint = cand("FalloutNV.esm:104C70")
        near = cand("FalloutNV.esm:200")
        reads = {hint["ref"]: rd(x=900.0, y=0.0, z=0.0), near["ref"]: rd(x=1.0, y=0.0, z=0.0)}
        pick, _ = P.choose(hint, [near], reads, origin=(0.0, 0.0, 0.0))
        self.assertEqual(pick.ref, "FalloutNV.esm:104C70")
        self.assertFalse(pick.fallback)

    def test_an_unloaded_hint_falls_back_to_the_nearest_usable_character(self):
        hint = cand("FalloutNV.esm:104C70")
        far, near, dead = cand("FalloutNV.esm:200", 0x200), cand("FalloutNV.esm:300", 0x300), cand("FalloutNV.esm:400", 0x400)
        reads = {hint["ref"]: rd(has3D=False), far["ref"]: rd(x=900.0, y=0.0, z=0.0), near["ref"]: rd(x=30.0, y=0.0, z=0.0), dead["ref"]: rd(x=1.0, y=0.0, z=0.0, dead=True)}
        pick, skipped = P.choose(hint, [far, dead, near], reads, origin=(0.0, 0.0, 0.0))
        self.assertEqual(pick.ref, "FalloutNV.esm:300")
        self.assertTrue(pick.fallback)
        self.assertEqual(pick.runtime, "00000300")
        self.assertIn("FalloutNV.esm:104C70 is not usable (no 3D)", pick.note)
        self.assertEqual(skipped, {"FalloutNV.esm:104C70": "no 3D", "FalloutNV.esm:400": "dead"})

    def test_the_hint_is_not_counted_twice_when_the_cell_lists_it_too(self):
        hint = cand("FalloutNV.esm:104C70")
        pick, skipped = P.choose(hint, [cand("falloutnv.esm:00104c70")], {hint["ref"]: rd(dead=True), "falloutnv.esm:00104c70": rd(dead=True)})
        self.assertIsNone(pick)
        self.assertEqual(skipped, {"FalloutNV.esm:104C70": "dead"})

    def test_no_fallback_refuses_instead_of_choosing_another(self):
        hint, other = cand("FalloutNV.esm:104C70"), cand("FalloutNV.esm:200")
        pick, skipped = P.choose(hint, [other], {hint["ref"]: rd(has3D=False), other["ref"]: rd()}, allow_fallback=False)
        self.assertIsNone(pick)
        self.assertEqual(skipped, {"FalloutNV.esm:104C70": "no 3D"})

    def test_without_a_hint_the_nearest_is_used_and_it_is_not_a_fallback(self):
        a, b = cand("FalloutNV.esm:200"), cand("FalloutNV.esm:300")
        pick, _ = P.choose(None, [a, b], {a["ref"]: rd(x=500.0, y=0.0, z=0.0), b["ref"]: rd(x=20.0, y=0.0, z=0.0)}, origin=(0.0, 0.0, 0.0))
        self.assertEqual(pick.ref, "FalloutNV.esm:300")
        self.assertFalse(pick.fallback)

    def test_unknown_distance_and_essential_npcs_go_last(self):
        a, b, c = cand("FalloutNV.esm:200"), cand("FalloutNV.esm:300"), cand("FalloutNV.esm:400")
        reads = {a["ref"]: rd(x=1.0, y=0.0, z=0.0, baseEssential=True), b["ref"]: rd(), c["ref"]: rd(x=900.0, y=0.0, z=0.0)}
        self.assertEqual([x["ref"] for x in P.order([a, b, c], reads, (0.0, 0.0, 0.0))], ["FalloutNV.esm:400", "FalloutNV.esm:300", "FalloutNV.esm:200"])

    def test_the_player_in_the_listing_is_skipped(self):
        me, npc = cand("FalloutNV.esm:000014", 0x14), cand("FalloutNV.esm:200")
        pick, skipped = P.choose(None, [me, npc], {me["ref"]: rd(x=0.0, y=0.0, z=0.0), npc["ref"]: rd(x=5000.0, y=0.0, z=0.0)}, origin=(0.0, 0.0, 0.0))
        self.assertEqual(pick.ref, "FalloutNV.esm:200")
        self.assertEqual(skipped, {"FalloutNV.esm:000014": "the player"})

    def test_a_proof_can_add_its_own_condition(self):
        hint, other = cand("FalloutNV.esm:104C70"), cand("FalloutNV.esm:200")
        reads = {hint["ref"]: rd(), other["ref"]: rd()}
        pick, skipped = P.choose(hint, [other], reads, accept=lambda ref, _r: "owned by the client" if ref.endswith("104C70") else None)
        self.assertEqual(pick.ref, "FalloutNV.esm:200")
        self.assertEqual(skipped["FalloutNV.esm:104C70"], "owned by the client")


class Explain(unittest.TestCase):
    def test_the_text_names_the_hint_and_counts_the_reasons(self):
        hint = cand("FalloutNV.esm:104C70")
        text = P.explain(hint, {"FalloutNV.esm:104C70": "no 3D", "a": "dead", "b": "no 3D", "c": "no 3D"}, 4, "has3D false, cell 000E1AA6")
        self.assertEqual(text, "FalloutNV.esm:104C70 is not usable (no 3D, has3D false, cell 000E1AA6); world.actors lists 4 character(s), none usable (no 3D 2, dead 1)")

    def test_a_refused_fallback_says_the_others_were_not_tried(self):
        text = P.explain(cand("FalloutNV.esm:104C70"), {"FalloutNV.esm:104C70": "no 3D"}, 3, fallback=False)
        self.assertIn("the others were not tried (no fallback)", text)

    def test_an_empty_cell_is_said_plainly(self):
        self.assertIn("lists no character in the player's cell", P.explain(None, {}, 0))


class Find(unittest.TestCase):
    def run_find(self, world, hint="FalloutNV.esm:104C70", wait_s=30.0, **kw):
        """world: {ref: lambda t -> read dict | None (not loaded)}"""
        c = Clock()
        calls = {"n": 0}

        def read(cd):
            calls["n"] += 1
            f = world.get(cd["ref"])
            r = f(c.t) if f else None
            if r is None:
                raise RuntimeError("not a loaded actor")
            return r
        pick = P.find(read, lambda: [cand(r, i + 1) for i, r in enumerate(world)], cand(hint) if hint else None, wait_s=wait_s, sleep=c.sleep, now=c.now, **kw)
        return pick, c

    def test_an_npc_that_loads_a_few_seconds_late_is_waited_for(self):
        pick, c = self.run_find({"FalloutNV.esm:104C70": lambda t: rd(has3D=t >= 6.0)})
        self.assertEqual(pick.ref, "FalloutNV.esm:104C70")
        self.assertFalse(pick.fallback)
        self.assertGreaterEqual(c.t, 6.0)

    def test_the_fallback_is_used_at_once_when_the_hint_never_loads(self):
        pick, c = self.run_find({"FalloutNV.esm:104C70": lambda t: rd(has3D=False), "FalloutNV.esm:200": lambda t: rd(x=1.0, y=1.0, z=1.0)}, wait_s=20.0)
        self.assertEqual(pick.ref, "FalloutNV.esm:200")
        self.assertTrue(pick.fallback)

    def test_nothing_loaded_is_not_found_after_the_wait_and_says_why(self):
        with self.assertRaises(P.NotFound) as cm:
            self.run_find({"FalloutNV.esm:104C70": lambda t: rd(has3D=False, cell="000E1AA6"), "FalloutNV.esm:200": lambda t: rd(dead=True)}, wait_s=5.0)
        msg = str(cm.exception)
        self.assertIn("FalloutNV.esm:104C70 is not usable (no 3D, has3D false, cell 000E1AA6)", msg)
        self.assertIn("dead 1", msg)

    def test_the_wait_runs_the_whole_time(self):
        c = Clock()
        with self.assertRaises(P.NotFound):
            P.find(lambda cd: None, lambda: [], cand("FalloutNV.esm:104C70"), wait_s=12.0, sleep=c.sleep, now=c.now)
        self.assertGreaterEqual(c.t, 12.0)

    def test_a_listing_that_refuses_is_not_a_crash(self):
        c = Clock()

        def boom():
            raise RuntimeError("main thread did not service the request")
        with self.assertRaises(P.NotFound):
            P.find(lambda cd: None, boom, cand("FalloutNV.esm:104C70"), wait_s=3.0, sleep=c.sleep, now=c.now)

    def test_the_accept_condition_is_waited_for_too(self):
        c = Clock()
        pick = P.find(lambda cd: rd(), lambda: [cand("FalloutNV.esm:200")], None, accept=lambda ref, _r: None if c.t >= 4.0 else "coop.owner does not know it on the client",
                      wait_s=30.0, sleep=c.sleep, now=c.now)
        self.assertEqual(pick.ref, "FalloutNV.esm:200")
        self.assertGreaterEqual(c.t, 4.0)


class RuntimeId(unittest.TestCase):
    PLUGINS = [{"name": "FalloutNV.esm", "index": 0}, {"name": "DeadMoney.esm", "index": 2}]

    def test_plugin_and_local_id_become_the_runtime_id(self):
        self.assertEqual(P.runtime_id("FalloutNV.esm:104C70", self.PLUGINS), 0x00104C70)
        self.assertEqual(P.runtime_id("deadmoney.esm:00104C70", self.PLUGINS), 0x02104C70)

    def test_a_bare_hex_id_is_already_one(self):
        self.assertEqual(P.runtime_id("0x00104C70"), 0x00104C70)
        self.assertEqual(P.runtime_id("00104C70"), 0x00104C70)

    def test_a_plugin_that_is_not_loaded_is_an_error(self):
        with self.assertRaises(ValueError):
            P.runtime_id("Nope.esm:1", self.PLUGINS)

    def test_the_old_ref_would_have_been_read_by_the_game_as_0xFA(self):
        # anim.npc.do parses the whole string with strtoul(.., 16): "Fa" is hex, "l" ends it. Documents why the conversion exists.
        n = 0
        for ch in "FalloutNV.esm:104C70":
            if ch.lower() not in "0123456789abcdef":
                break
            n = n * 16 + int(ch, 16)
        self.assertEqual(n, 0xFA)


SCRIPTS = ["tools/actors/proof_actor_port.py", "tools/actors/proof_actor_port_two_copy.py", "tools/animcheck/npc_proof.py"]


class Wiring(unittest.TestCase):
    def test_every_npc_proof_picks_through_the_shared_module(self):
        for rel in SCRIPTS:
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertIn("npc_pick.find(", text, rel)

    def test_the_check_notices_a_script_that_stopped_using_it(self):   # twin of the test above
        for rel in SCRIPTS:
            text = (REPO / rel).read_text(encoding="utf-8").replace("npc_pick.find(", "other.find(")
            self.assertNotIn("npc_pick.find(", text)


TWINS = {
    "no_fallback": ["Choose.test_an_unloaded_hint_falls_back_to_the_nearest_usable_character", "Find.test_the_fallback_is_used_at_once_when_the_hint_never_loads"],
    "no_wait": ["Find.test_an_npc_that_loads_a_few_seconds_late_is_waited_for", "Find.test_the_wait_runs_the_whole_time"],
    "dead_allowed": ["Why.test_every_reason_is_named"],
    "no_3d_check": ["Why.test_every_reason_is_named"],
    "player_allowed": ["Why.test_the_player_is_never_the_npc"],
    "listing_order": ["Choose.test_an_unloaded_hint_falls_back_to_the_nearest_usable_character"],
    "load_order_dropped": ["RuntimeId.test_plugin_and_local_id_become_the_runtime_id"],
}


class Twin(unittest.TestCase):
    def test_every_twin_is_caught(self):
        for mutant, tests in TWINS.items():
            env = dict(os.environ, NPC_PICK_MUTANT=mutant)
            cp = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), *tests], capture_output=True, text=True, env=env)
            self.assertNotEqual(cp.returncode, 0, f"the {mutant} mutant was not noticed by {tests}")


if __name__ == "__main__":
    unittest.main(verbosity=1)
