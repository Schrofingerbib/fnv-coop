"""Game-free tests for the bot action layer, plus mutation twins (each injected fake defect must be caught).

Run: python -I tools/bots/run_tests.py   (prints ALL OK)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bots import ActionFailed, Bot, FakeBridge, World  # noqa: E402
from bots import primitives as P  # noqa: E402
from bots.bot import MSGBOX_CLOSING, PLAYER_REF, ActionResult, BridgeError, ConnectionLost, RpcError, heading_deg  # noqa: E402
from nvbridge import join_ref_line  # noqa: E402  (bots.bot put tools/runner on sys.path)

REPO = Path(__file__).resolve().parents[3]

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


class OldNormBot(Bot):
    """Twin: the _norm before PR #11's review fix. A created ref (load index 0xFF) is 'not in the plugin list'."""

    def _norm(self, ref):
        if isinstance(ref, int) or (isinstance(ref, str) and ":" not in ref and ref != "player"):
            rid = ref if isinstance(ref, int) else int(ref, 16)
            if rid >> 24 >= len(self._plugin_list()):
                from bots.bot import _Fail
                raise _Fail("not_found", ref=ref, why="load index not in plugin list")
        return super()._norm(ref)


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

    def test_health_falls_back_to_getav_when_world_query_fails(self):
        nv, b = make(query_av=False)
        for who in (ref(RAIDER), PLAYER_REF):      # the condition seen in game: world.query GetActorValue fails for every ref
            with self.assertRaises(RpcError):
                nv.call(P.M_QUERY, {"ref": who, "fn": "GetActorValue", "p1": P.HEALTH_AV})
        self.assertEqual(b.health(ref(RAIDER)), 100.0)
        self.assertIn(("exec", 'printc "NVCOOP_EVAL=%g" (GetAV Health)', ref(RAIDER)), nv.calls)   # the actor is the calling ref
        self.assertEqual(b.health(), 200.0)        # the player: 'player.GetAV Health'
        self.assertIn(("exec", 'printc "NVCOOP_EVAL=%g" (player.GetAV Health)'), nv.calls)
        r = b.fire(ref(RAIDER), expect_hit=True)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["health"], 75.0)

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


WAVES = ([1] * 6, [(3.0, [1, 1]), (19.0, [1, 2])])   # the first in-game run (all DLC): 6 boxes, then 2, then 2 more about 16 s later


def _boxes_left_after(dismiss):
    """Engine boxes in waves with gaps shorter than quiet_s. Run dismiss(bot), then wait past the last wave.
    -> (what dismiss returned, bridge, boxes still shown or still to come)."""
    nv, b = make()
    nv.native_boxes, nv.box_waves = list(WAVES[0]), [(t, list(n)) for t, n in WAVES[1]]
    r = dismiss(b)
    b.wait(25)
    return r, nv, len(nv.native_boxes) + sum(len(n) for _, n in nv.box_waves)


def _press_only_now(b, max_n=8, settle_s=0.3):
    """Twin: dismiss_messages before the quiet window. It pressed while bot.menu said 'message' and stopped at the first poll without one."""
    pressed = 0
    while b.menu_info().get("kind") == "message" and pressed < max_n:
        b.press_box(0)
        pressed += 1
        b.clock.sleep(settle_s)
    return pressed


def _closing_boxes(n=3, closing_s=1.0):
    """n engine boxes; after each native press the box stays the top menu for closing_s with no current message (it is closing)."""
    nv, b = make()
    nv.native_boxes, nv.box_closing_s = [1] * n, closing_s
    return nv, b


def _any_refusal_fatal(b, settle_s=0.3):
    """Twin: dismiss_messages before the closing retry. Any refusal of bot.menu.press ended it as a failure. -> (reason, presses)."""
    pressed = 0
    while b.menu_info().get("kind") == "message":
        r = b.press_box(0)
        if not r.ok:
            return r.reason, pressed
        pressed += 1
        b.clock.sleep(settle_s)
    return "", pressed


class MessageBoxes(unittest.TestCase):
    def test_dismiss_retries_a_closing_box_without_counting_a_press(self):
        nv, b = _closing_boxes()
        r = b.dismiss_messages(max_closing=4)
        self.assertTrue(r.ok, r)
        self.assertEqual((r.data["pressed"], nv.native_presses, nv.native_boxes), (3, [0, 0, 0], []))
        self.assertGreater(r.data["closing"], 4)        # about 3 refusals per box: passes only because the bound starts again per press
        self.assertEqual(b.menu_info()["kind"], "none")
        nv, b = _closing_boxes(n=1)                       # the last box closes and no next box comes
        r = b.dismiss_messages()
        self.assertTrue(r.ok, r)
        self.assertEqual((r.data["pressed"], nv.native_boxes), (1, []))
        self.assertGreater(r.data["closing"], 0)

    def test_twin_any_refusal_fatal_fails_on_a_closing_box(self):
        nv, b = _closing_boxes()
        reason, pressed = _any_refusal_fatal(b)
        self.assertEqual(pressed, 1)
        self.assertIn(MSGBOX_CLOSING, reason)

    def test_dismiss_gives_up_on_a_box_that_stays_closing(self):
        nv, b = _closing_boxes(n=2, closing_s=1000.0)
        r = b.dismiss_messages(max_closing=10)
        self.assertEqual(r.reason, "postcondition")
        self.assertEqual((r.data["pressed"], r.data["closing"]), (1, 11))
        self.assertIn("stayed closing", r.data["why"])

    def test_dismiss_keeps_other_refusals_fatal(self):
        nv, b = make()
        nv.native_boxes = [1]
        r = b.dismiss_messages(button=1)
        self.assertTrue(r.reason.startswith("rpc_error:native message box: button 1 is not shown"), r)
        self.assertEqual(nv.native_boxes, [1])

    def test_closing_text_is_the_plugins(self):
        src = (REPO / "plugin" / "bots" / "bot_engine.h").read_text(encoding="utf-8")
        self.assertIn('"' + MSGBOX_CLOSING + '"', src)

    def test_dismiss_clears_queued_engine_boxes_with_button_0(self):
        nv, b = make()
        r = b.dismiss_messages()
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data, {"pressed": 0, "paths": [], "waited_s": 20.0, "quiet_s": 20.0, "closing": 0})   # nothing shown: a quiet window, no press
        nv.native_boxes = [1, 2, 1]                                    # three queued engine boxes (the DLC notices after a load)
        self.assertEqual(b.menu_info()["kind"], "message")
        r = b.dismiss_messages()
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data, {"pressed": 3, "paths": ["native"] * 3, "waited_s": 20.9, "quiet_s": 20.0, "closing": 0})   # 3 x 0.3 s, then 20 s quiet
        self.assertEqual(nv.native_presses, [0, 0, 0])
        self.assertEqual(b.menu_info()["kind"], "none")

    def test_dismiss_presses_every_wave_until_a_quiet_window(self):
        r, nv, left = _boxes_left_after(lambda b: b.dismiss_messages())
        self.assertTrue(r.ok, r)
        self.assertEqual((left, r.data["pressed"], r.data["quiet_s"]), (0, 10, 20.0))
        self.assertEqual(nv.native_presses, [0] * 10)
        self.assertGreaterEqual(r.data["waited_s"], 19.0 + 20.0)     # it waited out the last wave, then the quiet window
        self.assertLess(r.data["waited_s"], 19.0 + 20.0 + 2.0)

    def test_twin_press_only_now_leaves_a_later_wave(self):
        n, nv, left = _boxes_left_after(_press_only_now)
        self.assertEqual((n, left), (6, 4))                             # the first wave only: 2 + 2 boxes come later and stay
        self.assertEqual(_boxes_left_after(lambda b: b.dismiss_messages(quiet_s=0))[2], 4)   # quiet_s=0 is that old behaviour

    def test_dismiss_gives_up_when_boxes_keep_coming(self):
        nv, b = make()
        nv.box_waves = [(5.0 + 10.0 * i, [1]) for i in range(40)]      # a box every 10 s: never 20 s without one
        r = b.dismiss_messages(max_s=60)
        self.assertEqual(r.reason, "timeout")
        self.assertEqual((r.data["pressed"], r.data["waited_s"]), (6, 60.0))   # boxes at 5, 15, .. 55 s pressed, then it stops at max_s

    def test_dismiss_answers_the_ui_layers_box_first(self):
        nv, b = make()
        nv.box = 900
        nv.native_boxes = [1]
        r = b.dismiss_messages()
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["paths"], ["ui", "native"])
        self.assertEqual(nv.box_answers, [0])

    def test_dismiss_gives_up_after_max_n(self):
        nv, b = make()
        nv.native_boxes = [1] * 12
        r = b.dismiss_messages(max_n=8)
        self.assertEqual(r.reason, "postcondition")
        self.assertEqual(r.data["pressed"], 8)
        self.assertEqual(len(nv.native_boxes), 4)

    def test_press_reply_names_the_path(self):
        nv, b = make()
        nv.native_boxes = [2]
        r = b.press_box(1)
        self.assertTrue(r.ok, r)
        self.assertEqual((r.data["path"], r.data["value"]), ("native", 1))
        nv.native_boxes = [2]
        self.assertTrue(b.press_box(2).reason.startswith("rpc_error:native message box: button 2 is not shown"))
        nv.box = 900
        self.assertEqual(b.press_box(0).data["path"], "ui")


class GenuineActions(unittest.TestCase):
    def test_drop_returns_the_dropped_world_ref_from_the_event(self):
        nv, b = make()
        r = b.drop(AMMO, 2)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["source"], "event")
        self.assertEqual(r.data["ref"], nv.drop_log[-1]["ref"])
        self.assertIn(int(r.data["ref"].split(":")[-1], 16), nv.world.things)   # it is a real ref in the world (a created ref: bare runtime id)
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
        self.assertEqual(r.data["ref"], f"{nv._next_ref:08X}")   # a created ref: the plugin names it by its runtime id
        nv2, b2 = make(bot_methods=False)           # no bot.* methods at all: dev.refsOf still answers, so the scan still finds it
        r = b2.drop(AMMO, 1)
        self.assertTrue(r.ok, r)
        self.assertEqual(r.data["source"], "scan")

    def test_a_spawned_actor_is_usable_in_ref_scoped_calls(self):
        # PR #11 review: placeatme makes FF000001. dev.refsOf names it by its runtime id (nameOfForm cannot give it a plugin),
        # and a ref-scoped call on it must work: eval("GetAV Health", ref=...) through Bot.health.
        nv, b = make(query_av=False)
        nv.exec("placeatme 00000777 1", ref=ref(NPC))
        spawned = b.refs_of(0x777)
        self.assertEqual(spawned, ["FF000001"])
        self.assertEqual(b.health(spawned[0]), 100.0)
        self.assertIn(("exec", 'printc "NVCOOP_EVAL=%g" (GetAV Health)', "FF000001"), nv.calls)
        self.assertEqual(b.health(0xFF000001), 100.0)                  # an int runtime id works the same way
        # twin: the old _norm (load index 0xFF is "not in the plugin list") refuses the spawned actor
        with self.assertRaises(Exception) as c:
            OldNormBot(nv, "old").health(spawned[0])
        self.assertEqual(getattr(c.exception, "reason", None), "not_found")

    def test_ref_scoped_exec_replies_carry_the_runtime_id(self):
        # PR #11 review: bridge.cpp adds the resolved 8-digit runtime id to every exec {cmd, ref} reply, also when the line fails
        nv, b = make()
        self.assertEqual(nv.exec("GetOpenState", ref=ref(DOOR))["ref"], f"{DOOR:08X}")
        bad = nv.exec("NoSuchCommand", ref=ref(DOOR))
        self.assertEqual((bad["ok"], bad["ref"]), (False, f"{DOOR:08X}"))
        self.assertNotIn("ref", nv.exec("player.GetAngle Z"))       # no ref asked, no ref in the reply

    def test_drop_event_for_the_wrong_ref_is_visible(self):
        nv, b = make("drop_wrong_ref")
        r = b.drop(AMMO, 1)
        self.assertNotEqual(r.data["ref"], f"{nv._next_ref:08X}")   # the difference is observable, so the mutation twin can catch it

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


class LegacyBot(Bot):
    """Twin of the old bot: it builds '<ref>.<Func>' console lines again (what failed in game on 2026-10-21)."""

    def _old(self, line):
        if not isinstance(line, P.Line) or line.ref is None:
            return line
        return join_ref_line(line.cmd, self._norm(line.ref)[1])     # "00000601.GetOpenState", as the old code sent it

    def _exec(self, cmd, what=""):
        return super()._exec(self._old(cmd), what)

    def _eval(self, expr):
        return super()._eval(self._old(expr))


def no_activate_method(nv):
    """The build has no activate.ref: Bot falls back to the console line 'Activate player 1' on the ref."""
    orig = nv.call

    def call(method, params=None, timeout_s=35.0):
        if method == P.M_ACTIVATE:
            raise RpcError(-32601, "unknown method: " + method, method)
        return orig(method, params, timeout_s)
    nv.call = call


class ConsoleRefLines(unittest.TestCase):
    """exec {cmd, ref}: the console has no '<hexFormID>.Command' form; a ref-targeted line names its reference in ref."""

    def test_fake_console_behaves_like_the_game(self):
        nv, _ = make()
        r = nv.exec("00000600.RemoveItemTarget 00000500 player 1 1")
        self.assertEqual(r, {"ok": False, "output": 'Script command "00000600.RemoveItemTarget" not found'})
        with self.assertRaises(BridgeError):
            nv.eval("00000601.GetOpenState")
        self.assertEqual(nv.eval("GetOpenState", ref="FalloutNV.esm:601"), 3.0)
        self.assertEqual(nv.eval("GetOpenState", ref="00000601"), 3.0)
        self.assertEqual(nv.eval("GetItemCount 00000500", ref="0x600"), 5.0)
        self.assertEqual(nv.eval("player.GetItemCount 00000501"), 30.0)       # player. lines need no ref
        with self.assertRaises(BridgeError):
            nv.eval("GetOpenState")                                            # no calling reference
        with self.assertRaises(RpcError) as cm:
            nv.eval("GetOpenState", ref="FalloutNV.esm:9999")
        self.assertEqual(cm.exception.code, -32013)
        with self.assertRaises(RpcError) as cm:
            nv.eval("GetOpenState", ref="Nope.esm:601")
        self.assertEqual(cm.exception.code, -32013)
        with self.assertRaises(RpcError) as cm:
            nv.eval("GetOpenState", ref="door")
        self.assertEqual(cm.exception.code, -32602)

    def test_bot_sends_ref_lines_never_hex_prefixes(self):
        nv, b = make()
        self.assertTrue(b.take(ref(CHEST), ITEM, 2).ok)
        self.assertTrue(b.put(ref(CHEST), ITEM, 1).ok)
        self.assertTrue(b.open(ref(DOOR)).ok)
        self.assertTrue(b.close(ref(DOOR)).ok)
        self.assertFalse(b.locked(ref(DOOR)))
        self.assertEqual(b.inventory_count(ITEM, ref(CHEST)), 4)
        lines = [c for c in nv.calls if c[0] == "exec"]
        self.assertIn(("exec", "RemoveItemTarget 00000500 player 2 1", ref(CHEST)), lines)
        self.assertIn(("exec", "player.RemoveItemTarget 00000500 00000600 1 1"), lines)
        self.assertIn(("exec", 'printc "NVCOOP_EVAL=%g" (GetOpenState)', ref(DOOR)), lines)
        self.assertIn(("exec", 'printc "NVCOOP_EVAL=%g" (GetLocked)', ref(DOOR)), lines)
        bad = [c for c in lines if re.search(r"(^|[\s(])[0-9A-Fa-f]{8}\.\w", c[1])]
        self.assertEqual(bad, [], "a console line still names a reference with '<hex>.'")

    def test_console_activate_fallback_uses_the_ref(self):
        nv, b = make()
        no_activate_method(nv)
        self.assertTrue(b.open(ref(DOOR)).ok)
        self.assertIn(("exec", "Activate player 1", ref(DOOR)), nv.calls)

    def test_legacy_hex_prefix_lines_fail_like_in_game(self):
        """Twin: a bot that still builds '<hex>.Func' lines must fail on the fake, as it did in game."""
        nv, b = make()
        b = LegacyBot(nv, "legacy")
        self.assertFalse(b.take(ref(CHEST), ITEM, 1).ok)
        self.assertFalse(b.open(ref(DOOR)).ok)
        with self.assertRaises(BridgeError):
            b.door_state(ref(DOOR))
        with self.assertRaises(BridgeError):
            b.inventory_count(ITEM, ref(CHEST))
        nv2, b2 = make()
        no_activate_method(nv2)
        r = LegacyBot(nv2, "legacy").activate(ref(DOOR))
        self.assertFalse(r.ok)
        self.assertIn("not found", r.data.get("output", ""))
        self.assertEqual(nv2.world.things[DOOR].open_state, 3)


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
        "exec_ref_ignored": lambda b: b.take(ref(CHEST), ITEM, 1),
        "msgbox_stuck": lambda b: _dismiss_two_boxes(b),
        "msgbox_ui_only": lambda b: _dismiss_two_boxes(b),
    }[name]


def _dismiss_two_boxes(b):
    """Two engine message boxes are up: dismiss_messages must clear both, or fail."""
    b.nv.native_boxes = [1, 2]
    return b.dismiss_messages()


def _drop_ref_check(b):
    """Drop, then require the returned ref to be one that really exists on the ground: the wrong-ref twin must fail this."""
    r = b.drop(AMMO, 1)
    ground = b.refs_of(AMMO) or []
    if r.ok and r.data["ref"] not in ground:
        return ActionResult(False, "postcondition", {"ref": r.data["ref"], "ground": ground})
    return r


MUTANTS = ["move_ignored", "look_ignored", "stop_ignored", "jump_noop", "fire_noop", "setpos_ignored", "angle_ignored", "activate_noop", "transfer_noop", "transfer_dup", "equip_noop",
           "drop_noop", "fire_miss", "health_stuck", "dialog_never", "drop_wrong_ref", "travel_ignored", "exec_ref_ignored", "msgbox_stuck", "msgbox_ui_only"]


class MutationTwins(unittest.TestCase):
    def test_every_mutant_is_caught(self):
        for m in MUTANTS:
            with self.subTest(mutant=m):
                nv, b = make(m)
                r = _scenario(m)(b)
                self.assertFalse(r.ok, f"mutant {m} slipped through: {r}")
                nv0, b0 = make(None)
                self.assertTrue(_scenario(m)(b0).ok, f"healthy scenario for {m} should pass")

    def test_message_box_twins_fail_for_the_right_reason(self):
        nv, b = make("msgbox_stuck")
        r = _dismiss_two_boxes(b)
        self.assertEqual((r.reason, r.data["pressed"]), ("postcondition", 8))   # pressed, re-checked, gave up: never looped forever
        nv, b = make("msgbox_ui_only")
        self.assertEqual(_dismiss_two_boxes(b).reason, "rpc_error:no message box is being shown")   # the bug seen in game

    def test_health_fallback_does_not_hide_defects(self):
        """world.query GetActorValue fails (game, 2026-10-21): health() reads GetAV through eval {ref}. The fallback must
        read the target's real health, so a stuck health, a missed shot or a lost calling ref still fails the shot."""
        for m in ("health_stuck", "fire_miss", "exec_ref_ignored"):
            with self.subTest(mutant=m):
                nv, b = make(m, query_av=False)
                r = b.fire(ref(RAIDER), expect_hit=True)
                self.assertFalse(r.ok, f"mutant {m} slipped through the health fallback: {r}")

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
