"""Game-free tests for the bot action layer, plus mutation twins (each injected fake defect must be caught).

Run: python -I tools/bots/run_tests.py   (prints ALL OK)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bots import ActionFailed, Bot, FakeBridge, World  # noqa: E402
from bots.bot import ActionResult, ConnectionLost, heading_deg  # noqa: E402

ITEM, AMMO, CHEST, DOOR, NPC, RAIDER, GROUND, TERM, QUEST = 0x500, 0x501, 0x600, 0x601, 0x602, 0x603, 0x604, 0x605, 0x700
INFO = 0x800


def make(mutant=None, **kw):
    w = World()
    w.add_container(CHEST, (500, 0, 0), {ITEM: 5})
    w.add_door(DOOR, (200, 200, 0))
    w.add_actor(NPC, (300, 0, 0), topics=[INFO])
    w.add_actor(RAIDER, (1000, 1000, 0))
    w.add_item(GROUND, ITEM, (400, 400, 0))
    w.add_terminal(TERM, (0, 300, 0))
    w.set_stage(QUEST, 20)
    nv = FakeBridge(w, mutant=mutant, **kw)
    nv.me.items[AMMO] = 30
    return nv, Bot(nv, "t")


def ref(i):
    return f"FalloutNV.esm:{i:X}"


class Movement(unittest.TestCase):
    def test_walk_to_coords_and_ref(self):
        nv, b = make()
        r = b.goto((600, 0, 0), radius=50)
        self.assertTrue(r.ok, r)
        self.assertLess(r.data["distance"], 50)
        r = b.goto(ref(RAIDER), radius=100)
        self.assertTrue(r.ok, r)
        self.assertGreater(r.elapsed_s, 1.0)  # walking takes virtual time, it is not a teleport

    def test_teleport(self):
        nv, b = make()
        self.assertTrue(b.goto(ref(CHEST), mode="teleport").ok)
        self.assertLess(((b.pos().x - 500) ** 2) ** .5, 100)

    def test_other_cell_refused_when_walking(self):
        nv, b = make()
        nv.world.things[CHEST].cell = 9
        r = b.goto(ref(CHEST))
        self.assertEqual(r.reason, "other_cell")
        self.assertTrue(b.goto(ref(CHEST), mode="teleport").ok)

    def test_timeout(self):
        nv, b = make()
        r = b.goto((50000, 0, 0), timeout_s=2)
        self.assertEqual(r.reason, "timeout")

    def test_follow(self):
        nv, b = make()
        self.assertTrue(b.follow(ref(NPC), keep=150, duration_s=3).ok)

    def test_not_found_and_bad_ref(self):
        nv, b = make()
        self.assertEqual(b.goto(ref(0x9999)).reason, "not_found")
        self.assertEqual(b.goto("Nope.esm:1").reason, "not_found")

    def test_hex_and_int_refs_equal(self):
        nv, b = make()
        self.assertEqual(b.pos("602"), b.pos(NPC))

    def test_wait(self):
        nv, b = make()
        self.assertGreaterEqual(b.wait(2.5).elapsed_s, 2.5)


class Combat(unittest.TestCase):
    def test_equip_unequip(self):
        nv, b = make()
        self.assertEqual(b.equip(ITEM).reason, "not_in_inventory")
        self.assertTrue(b.equip(AMMO).ok)
        self.assertTrue(b.unequip(AMMO).ok)

    def test_aim_and_fire_kills(self):
        nv, b = make()
        self.assertTrue(b.aim_at(ref(RAIDER)).ok)
        for _ in range(4):
            self.assertTrue(b.fire(ref(RAIDER), expect_hit=True).ok)
        self.assertFalse(b.alive(ref(RAIDER)))
        r = b.fire(ref(RAIDER))
        self.assertEqual(r.reason, "dead")

    def test_fire_without_bridge_method_cannot_verify(self):
        nv, b = make(bot_methods=False)
        r = b.fire()
        self.assertEqual(r.reason, "unsupported")

    def test_reload_vats(self):
        nv, b = make()
        self.assertTrue(b.reload().ok)
        self.assertTrue(b.vats_target(ref(RAIDER)).ok)
        nv2, b2 = make(bot_methods=False)
        self.assertEqual(b2.vats_target(ref(RAIDER)).reason, "unsupported")

    def test_heading_math(self):
        self.assertAlmostEqual(heading_deg((0, 0, 0), (0, 10, 0))[0], 0)
        self.assertAlmostEqual(heading_deg((0, 0, 0), (10, 0, 0))[0], 90)
        self.assertAlmostEqual(heading_deg((0, 0, 0), (0, -10, 0))[0], 180)


class Interaction(unittest.TestCase):
    def test_door_open_close_lock(self):
        nv, b = make()
        self.assertTrue(b.open(ref(DOOR)).ok)
        self.assertEqual(b.door_state(ref(DOOR)), "open")
        self.assertTrue(b.close(ref(DOOR)).ok)
        nv.world.things[DOOR].locked = True
        self.assertTrue(b.locked(ref(DOOR)))
        self.assertEqual(b.open(ref(DOOR)).reason, "locked")

    def test_take_put(self):
        nv, b = make()
        self.assertTrue(b.take(ref(CHEST), ITEM, 3).ok)
        self.assertEqual(b.inventory_count(ITEM), 3)
        self.assertEqual(b.inventory_count(ITEM, ref(CHEST)), 2)
        self.assertEqual(b.take(ref(CHEST), ITEM, 3).reason, "not_enough")
        self.assertTrue(b.put(ref(CHEST), ITEM, 1).ok)
        self.assertEqual(b.inventory_count(ITEM, ref(CHEST)), 3)

    def test_pickup_and_drop(self):
        nv, b = make()
        self.assertTrue(b.pickup(ref(GROUND), item=ITEM).ok)
        self.assertEqual(b.inventory_count(ITEM), 1)
        self.assertTrue(b.drop(ITEM, 1).ok)
        self.assertEqual(b.drop(ITEM, 1).reason, "not_enough")

    def test_talk_topic_end(self):
        nv, b = make()
        self.assertEqual(b.choose_topic(ref(INFO)).reason, "no_dialogue")
        self.assertTrue(b.talk(ref(NPC)).ok)
        self.assertTrue(b.dialogue_open())
        self.assertEqual(b.talk(ref(NPC)).reason, "already_in_dialogue")
        self.assertTrue(b.choose_topic(ref(INFO)).ok)
        self.assertFalse(b.choose_topic(ref(INFO + 1)).ok)
        self.assertTrue(b.end_dialogue().ok)
        self.assertFalse(b.dialogue_open())

    def test_open_container_terminal_needs_menu_method(self):
        nv, b = make()
        self.assertTrue(b.open(ref(CHEST)).ok)
        self.assertTrue(b.open(ref(TERM)).ok)
        nv2, b2 = make(menu_method=False)
        self.assertEqual(b2.open(ref(CHEST)).reason, "unsupported")

    def test_quest_stage_and_snapshot(self):
        nv, b = make()
        self.assertEqual(b.quest_stage(QUEST), 20)
        self.assertTrue(b.snapshot("x.json").ok)

    def test_strict_raises_and_crash_propagates(self):
        nv, b = make()
        b.strict = True
        with self.assertRaises(ActionFailed) as c:
            b.goto(ref(0x9999))
        self.assertEqual(c.exception.result.reason, "not_found")
        nv2, b2 = make("crash")
        with self.assertRaises(ConnectionLost):
            b2.goto((1000, 0, 0))

    def test_log_records_every_action(self):
        nv, b = make()
        b.wait(1)
        b.goto(ref(0x9999))
        self.assertEqual([r.ok for r in b.log], [True, False])


class InputDriver(unittest.TestCase):
    def test_walk_uses_real_locomotion_and_takes_time(self):
        nv, b = make()
        r = b.goto((0, 1500, 0), radius=80)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["via"], "input")
        self.assertGreater(r.elapsed_s, 1400 / 220.0 * 0.9)
        self.assertEqual(nv.held, set())  # released on arrival

    def test_fallback_to_step_without_driver(self):
        nv, b = make(input_driver=False)
        r = b.goto((600, 0, 0), radius=50)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["via"], "step")
        self.assertEqual(b.goto((10, 900, 0), mode="input").reason, "unsupported")

    def test_turn_rate_is_limited_so_the_avatar_turns_visibly(self):
        nv, b = make()
        yaws = []
        orig = nv.call
        def spy(method, params=None, timeout_s=35.0):
            if method == "bot.look":
                yaws.append(params["yaw"])
            return orig(method, params, timeout_s)
        nv.call = spy
        nv.me.angle["Z"] = 180.0
        self.assertTrue(b.goto((0, 800, 0), radius=80).ok)   # needs a 180 degree turn
        self.assertGreater(len(set(round(y) for y in yaws[:4])), 2)  # several distinct headings, not one jump

    def test_unsticks_around_a_pillar(self):
        nv, b = make()
        nv.world.add_wall(-30, 400, 30, 420)
        r = b.goto((0, 1000, 0), radius=80, timeout_s=40)
        self.assertTrue(r.ok, r)
        self.assertGreater(r.data["unstick"], 0)

    def test_gives_up_on_a_long_wall(self):
        nv, b = make()
        nv.world.add_wall(-5000, 400, 5000, 420)
        r = b.goto((0, 1000, 0), radius=80, timeout_s=60)
        self.assertEqual(r.reason, "stuck")
        self.assertEqual(nv.held, set())

    def test_walk_path_visits_waypoints_in_order(self):
        nv, b = make()
        r = b.walk_path([(0, 600, 0), (600, 600, 0), (600, 0, 0)], radius=60)
        self.assertTrue(r.ok, r)
        self.assertLess(b.pos().x - 600, 130)
        self.assertEqual(b.walk_path([(0, 0, 0), (1, 1, 0)], radius=60).data["waypoints"], 2)

    def test_look_stop_jump_and_controls(self):
        nv, b = make()
        self.assertTrue(b.look(yaw=90).ok)
        self.assertAlmostEqual(nv.me.angle["Z"], 90)
        self.assertTrue(b.look(ref=ref(RAIDER)).ok)
        self.assertTrue(b.jump().ok)
        self.assertTrue(b.sneak_toggle().ok)
        self.assertTrue(b.activate_crosshair().ok)
        nv.held.add("Forward")
        self.assertTrue(b.stop().ok)

    def test_driver_actions_unsupported_without_driver(self):
        nv, b = make(input_driver=False)
        for r in (b.look(yaw=1), b.jump(), b.stop(), b.sneak_toggle(), b.activate_crosshair()):
            self.assertEqual(r.reason, "unsupported")

    def test_fire_with_driver_waits_for_the_pulses_and_hits(self):
        nv, b = make()
        r = b.fire(ref(RAIDER), shots=3, expect_hit=True)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["fired"], 3)
        self.assertEqual(nv.shots_left, 0)
        self.assertEqual(nv.world.things[RAIDER].health, 25.0)


class GlueActions(unittest.TestCase):
    def test_vats_job_polled_to_done_and_failure_reported(self):
        nv, b = make()
        r = b.vats_target(ref(RAIDER))
        self.assertTrue(r.ok, r)
        self.assertEqual(nv.world.things[RAIDER].health, 60.0)
        nv2, b2 = make("vats_fail")
        r = b2.vats_target(ref(RAIDER))
        self.assertEqual(r.reason, "vats_failed")
        self.assertIn("target_not_selectable", r.data["why"])

    def test_vats_noop_is_caught_by_health_check(self):
        nv, b = make("vats_noop")
        before = b.health(ref(RAIDER))
        r = b.vats_target(ref(RAIDER))
        self.assertTrue(r.ok and r.data["hit"] is False)       # a miss is a legitimate outcome unless the caller expects a hit
        self.assertEqual(b.vats_target(ref(RAIDER), expect_hit=True).reason, "postcondition")
        self.assertEqual(b.health(ref(RAIDER)), before)

    def test_menu_dialog_inventory_queries(self):
        nv, b = make()
        self.assertEqual(b.menu_info()["kind"], "none")
        b.talk(ref(NPC))
        self.assertEqual(b.menu_info()["kind"], "dialogue")
        self.assertEqual(b.dialog_choices()[0]["formId"], INFO)
        self.assertEqual(b.inventory(items=[AMMO]), {"FalloutNV.esm:%X" % AMMO: 30})
        self.assertIn("FalloutNV.esm:%X" % AMMO, b.inventory())
        self.assertEqual(b.inventory(CHEST, items=[ITEM])["FalloutNV.esm:%X" % ITEM], 5)

    def test_press_and_consent(self):
        nv, b = make()
        self.assertEqual(b.consent("ready").reason, "rpc_error:no message box is being shown")
        nv.box = 900
        self.assertTrue(b.consent("ready").ok)
        nv.box = 901
        self.assertTrue(b.press_box(1).ok)
        self.assertEqual(nv.box_answers, [0, 1])
        nv2, b2 = make(bot_methods=False)
        nv2.box = 1
        self.assertEqual(b2.consent("ready").reason, "unsupported")

    def test_terminal_entries(self):
        nv, b = make()
        nv.world.things[TERM].topics_text = ["Read log", "Open vault door", "Exit"]
        r = b.use_terminal(ref(TERM), 1)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["used"], "Open vault door")
        self.assertEqual(b.terminal_used(ref(TERM)), [{"index": 1, "text": "Open vault door"}])
        self.assertEqual(b.use_terminal(ref(TERM), 9).reason, "rpc_error:entry index out of range")
        nv2, b2 = make("terminal_noop")
        nv2.world.things[TERM].topics_text = ["a", "b"]
        b2.use_terminal(ref(TERM), 0)
        self.assertEqual(b2.terminal_used(ref(TERM)), [])      # a click the game ignored shows up as nothing used
        nv3, b3 = make(menu_method=False)
        self.assertEqual(b3.use_terminal(ref(TERM), 0).reason, "unsupported")

    def test_fast_travel_and_count_refs(self):
        nv, b = make()
        nv.world.things[CHEST].cell = 7
        self.assertTrue(b.fast_travel(ref(CHEST)).ok)
        self.assertEqual(b.cell(), 7)
        nv.world.add_actor(0x610, (0, 0, 0), cell=7)
        nv.world.add_actor(0x611, (0, 0, 0), cell=7).base_id = 0x610
        nv.world.add_actor(0x612, (0, 0, 0), cell=7).base_id = 0x610
        self.assertEqual(b.count_refs(ref(0x610)), 3)
        nv.world.things[0x612].health = 0   # dead ones do not count
        self.assertEqual(b.count_refs(ref(0x610)), 2)
        nv2, b2 = make("setpos_ignored")
        nv2.world.things[CHEST].cell = 7
        self.assertFalse(b2.fast_travel(ref(CHEST)).ok)


class GenuineActions(unittest.TestCase):
    def test_drop_returns_the_dropped_world_ref_from_the_event(self):
        nv, b = make()
        r = b.drop(AMMO, 2)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["source"], "event")
        self.assertEqual(r.data["ref"], nv.drop_log[-1]["ref"])
        self.assertIn(r.data["ref"].split(":")[1], {f"{t:X}" for t in nv.world.things})   # it is a real ref in the world
        # a second drop of another base form is told apart by the base form, not just the order
        b.pickup(ref(GROUND), item=ITEM)
        r2 = b.drop(ITEM, 1)
        self.assertEqual(r2.data["ref"], nv.drop_log[-1]["ref"])
        self.assertNotEqual(r2.data["ref"], r.data["ref"])

    def test_drop_falls_back_to_a_nearby_ref_scan(self):
        nv, b = make(drop_events=False)
        r = b.drop(AMMO, 1)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["source"], "scan")
        self.assertEqual(r.data["ref"], f"FalloutNV.esm:{nv._next_ref:X}")
        nv2, b2 = make(bot_methods=False)           # no bot.* methods at all: dev.refsOf still answers, so the scan still finds it
        r = b2.drop(AMMO, 1)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["source"], "scan")

    def test_drop_event_for_the_wrong_ref_is_visible(self):
        nv, b = make("drop_wrong_ref")
        r = b.drop(AMMO, 1)
        self.assertNotEqual(r.data["ref"], f"FalloutNV.esm:{nv._next_ref:X}")   # the difference is observable, so the mutation twin can catch it

    def test_genuine_fast_travel_waits_for_the_arrival(self):
        nv, b = make()
        nv.world.things[CHEST].cell = 7
        r = b.fast_travel(ref(CHEST))
        self.assertTrue(r.ok, r)
        self.assertTrue(r.data["genuine"])
        self.assertEqual(b.cell(), 7)
        self.assertGreater(r.elapsed_s, 2.0)        # it really waited for the delayed move, no instant teleport

    def test_fast_travel_refusal_is_a_failure_not_a_teleport(self):
        nv, b = make(travel_refuses=True)
        nv.world.things[CHEST].cell = 7
        r = b.fast_travel(ref(CHEST))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "refused")
        self.assertEqual(b.cell(), 1)

    def test_fast_travel_falls_back_to_moveto_and_says_so(self):
        nv, b = make(genuine_travel=False)
        nv.world.things[CHEST].cell = 7
        r = b.fast_travel(ref(CHEST))
        self.assertTrue(r.ok, r)
        self.assertFalse(r.data["genuine"])
        self.assertEqual(b.cell(), 7)
        self.assertEqual(b.fast_travel(ref(CHEST), allow_fallback=False).reason, "unsupported")

    def test_fast_travel_that_never_moves_fails(self):
        nv, b = make("travel_ignored")
        nv.world.things[CHEST].cell = 7
        r = b.fast_travel(ref(CHEST), timeout_s=6.0)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "postcondition")

    def test_speech_check_flag_is_passed_through(self):
        nv, b = make()
        b.talk(ref(NPC))
        r = b.choose_topic(ref(INFO))
        self.assertTrue(r.ok, r)
        self.assertIn("speech_check", r.data)


# ---- mutation twins: a defective game must make the matching action FAIL, never report success ----
def _scenario(name):
    """-> callable(bot) returning the ActionResult that must be a failure under mutant `name`."""
    return {
        "setpos_ignored": lambda b: b.goto((600, 0, 0), timeout_s=3, mode="step"),
        "move_ignored": lambda b: b.goto((600, 0, 0), timeout_s=6),
        "look_ignored": lambda b: b.goto((800, 0, 0), timeout_s=8),
        "stop_ignored": lambda b: (b.goto((600, 0, 0)), b.stop())[1],
        "jump_noop": lambda b: b.jump(),
        "fire_noop": lambda b: b.fire(ref(RAIDER), expect_hit=True),
        "angle_ignored": lambda b: b.aim_at(ref(RAIDER)),
        "activate_noop": lambda b: b.open(ref(DOOR)),
        "transfer_noop": lambda b: b.take(ref(CHEST), ITEM, 1),
        "transfer_dup": lambda b: b.take(ref(CHEST), ITEM, 1),
        "equip_noop": lambda b: b.equip(AMMO),
        "drop_noop": lambda b: b.drop(AMMO, 1),
        "fire_miss": lambda b: b.fire(ref(RAIDER), expect_hit=True),
        "health_stuck": lambda b: b.fire(ref(RAIDER), expect_hit=True),
        "dialog_never": lambda b: b.talk(ref(NPC)),
        "drop_wrong_ref": lambda b: _drop_ref_check(b),
        "travel_ignored": lambda b: b.fast_travel(ref(CHEST), timeout_s=6.0),
        "ref_info_error": lambda b: b.pos(ref(NPC)) and None,
    }[name]


def _drop_ref_check(b):
    """Drop, then require the returned ref to be one that really exists on the ground: the wrong-ref twin must fail this."""
    r = b.drop(AMMO, 1)
    ground = b.refs_of(AMMO) or []
    if r.ok and r.data["ref"] not in ground:
        return ActionResult(False, "postcondition", {"ref": r.data["ref"], "ground": ground})
    return r


MUTANTS = ["move_ignored", "look_ignored", "stop_ignored", "jump_noop", "fire_noop", "setpos_ignored", "angle_ignored", "activate_noop", "transfer_noop", "transfer_dup", "equip_noop",
           "drop_noop", "fire_miss", "health_stuck", "dialog_never", "drop_wrong_ref", "travel_ignored"]


class MutationTwins(unittest.TestCase):
    def test_every_mutant_is_caught(self):
        for m in MUTANTS:
            with self.subTest(mutant=m):
                nv, b = make(m)
                r = _scenario(m)(b)
                self.assertFalse(r.ok, f"mutant {m} slipped through: {r}")
                nv0, b0 = make(None)
                self.assertTrue(_scenario(m)(b0).ok, f"healthy scenario for {m} should pass")

    def test_ref_info_error_reports_rpc_failure(self):
        nv, b = make("ref_info_error")
        r = b.goto(ref(NPC))
        self.assertFalse(r.ok)
        self.assertTrue(r.reason.startswith("not_found") or r.reason.startswith("rpc_error"), r.reason)

    def test_no_crash_swallowed(self):
        nv, b = make("crash")
        with self.assertRaises(ConnectionLost):
            b.wait_until(lambda: nv.exec("x"), 1)


if __name__ == "__main__":
    unittest.main()
