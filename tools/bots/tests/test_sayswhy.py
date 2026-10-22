"""A failed input action says why: the distance left, the menu in the way, the weapon, the ammo (see Bot._menu_note and friends).

Each scenario below is a real failure of the first in-game rounds (a message box eating Forward, a player standing inside the NPC so the
aim pointed at the floor, nothing equipped, an empty gun), made offline with LiveFake. `_problems(bot_class)` runs them all and lists the
scenarios whose explanation is missing: the real Bot has none, and each twin (a Bot with one explanation removed) must have exactly its own.

Run: python -I tools/bots/run_tests.py   (prints ALL OK)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bots import Bot, World  # noqa: E402
from bots.bot import ActionResult  # noqa: E402
from livefake import LiveFake  # noqa: E402

NPC, RAIDER, CLOSE, ABOVE, TERM, AMMO, INFO = 0x602, 0x603, 0x610, 0x611, 0x605, 0x501, 0x800


def ref(i):
    return f"FalloutNV.esm:{i:X}"


def make(bot_cls=Bot, **kw):
    w = World()
    w.add_actor(NPC, (300, 0, 0), topics=[INFO])
    w.add_actor(RAIDER, (1000, 1000, 0))
    w.add_actor(CLOSE, (30, 0, 0))            # the player starts at the origin: this one is "inside" it
    w.add_actor(ABOVE, (50, 0, 600))          # nearly straight up
    w.add_terminal(TERM, (0, 300, 0))
    nv = LiveFake(w, **kw)
    nv.me.items[AMMO] = 30
    return nv, bot_cls(nv, "t")


# ---- twins: the Bot before each explanation existed ----
class QuietMenuBot(Bot):
    """Twin: never asks the game which menu is open."""

    def _menu_note(self):
        return {}


class EagerStuckBot(Bot):
    """Twin: any open menu counts as blocking, so a walk that stands still under an unknown 'other' menu gives up at once."""

    def _menu_blocks(self):
        return bool(self._menu_note().get("menu_open"))


class UnguardedAimBot(Bot):
    """Twin: aim_at without the too-close and pitch refusals (the old behaviour: aims at the floor)."""

    def aim_at(self, ref, **kw):
        return super().aim_at(ref, min_dist=0.0, max_pitch=1e9, **kw)


class BlindWalkBot(Bot):
    """Twin: a failed walk says how far it still was but not how far the avatar got (the 2026-10-22 matrix runs: "857 units left" could not
    tell an avatar that never moved from one stopped by a wall)."""

    def _walk_fail(self, code, d, radius, start=None, **data):
        return super()._walk_fail(code, d, radius, start=None, **data)


class BlindFireBot(Bot):
    """Twin: a failed shot reports neither the weapon state nor the ammo."""

    def _weapon_note(self):
        return {}

    def _ammo_now(self):
        return None


def _has(r, *words):
    return not r.ok and all(w in r.reason for w in words)


def _problems(bot_cls):
    """-> the sorted names of the scenarios in which bot_cls does NOT explain its failure."""
    bad = []

    def check(name, ok):
        if not ok:
            bad.append(name)

    # a message box eats Forward: the avatar stands still. The walk says so on the first stuck window, not after the sidesteps.
    nv, b = make(bot_cls)
    nv.native_boxes = [1, 2]
    r = b.goto((0, 1500, 0), radius=80, timeout_s=30)
    check("walk_menu", r.code == "stuck" and _has(r, "units left", "message box", "2 queued") and r.data.get("menu") == "message"
          and r.data.get("msgs_queued") == 2 and r.data.get("menu_open") is True and r.data.get("distance", 0) > 1000 and r.elapsed_s < 4.0
          and nv.native_presses == [])
    # a wall, no menu: distance left, and it says no menu is open (so the cause is the wall)
    nv, b = make(bot_cls)
    nv.world.add_wall(-5000, 400, 5000, 420)
    r = b.goto((0, 1000, 0), radius=80, timeout_s=60)
    check("walk_wall", r.code == "stuck" and _has(r, "units left", "no menu is open") and r.data.get("menu_open") is False)
    # a wall half way: the avatar got somewhere, and it says how far
    nv, b = make(bot_cls)
    nv.world.add_wall(-5000, 400, 5000, 420)
    r = b.goto((0, 1000, 0), radius=80, timeout_s=60)
    check("walk_progress", _has(r, "moved") and "never left" not in r.reason and r.data.get("moved", 0) > 300)
    # boxed in at the start: nothing moves at all, and the message says the avatar never left its spot (input dead or blocked, not a long way)
    nv, b = make(bot_cls)
    nv.world.add_wall(-100, -100, 100, 100)
    r = b.goto((0, 1000, 0), radius=80, timeout_s=60)
    check("walk_boxed_in", r.code == "stuck" and _has(r, "never left its start") and r.data.get("moved", 99) < 25)
    # the same without the input driver (stepped SetPos) and a walk that runs out of time
    nv, b = make(bot_cls, input_driver=False)
    r = b.goto((50000, 0, 0), timeout_s=2)
    check("walk_timeout", r.code == "timeout" and _has(r, "units left", "no menu is open"))
    # standing inside the target
    nv, b = make(bot_cls)
    r = b.aim_at(ref(CLOSE))
    f = b.fire(ref(CLOSE))
    check("aim_too_close", r.code == "too_close" and _has(r, "30 units", "minimum 100") and f.code == "too_close"
          and not any(c[0] == "bot.fire" for c in nv.calls) and nv.me.angle["X"] == 0.0)
    # straight up
    nv, b = make(bot_cls)
    r = b.aim_at(ref(ABOVE))
    check("aim_pitch", r.code == "pitch" and _has(r, "degrees of pitch", "limit 80") and abs(r.data["pitch"]) > 80)
    # a box up: the pulses never reach the game
    nv, b = make(bot_cls)
    nv.native_boxes = [1, 2]
    r = b.fire(ref(RAIDER), expect_hit=True)
    check("fire_menu", r.code == "postcondition" and _has(r, "holstered", "message box", "2 queued") and r.data.get("menu") == "message"
          and nv.native_presses == [] and nv.native_boxes == [1, 2])
    # nothing equipped
    nv, b = make(bot_cls)
    nv.weapon_equipped = False
    r = b.fire(ref(RAIDER), expect_hit=True)
    check("fire_unarmed", _has(r, "no weapon equipped", "weapon holstered") and r.data.get("weapon_equipped") is False)
    # an empty gun, ammo known
    nv, b = make(bot_cls)
    nv.ammo_form, nv.me.items[AMMO], b.ammo_item = AMMO, 0, AMMO
    r = b.fire(ref(RAIDER), expect_hit=True)
    check("fire_ammo", _has(r, "ammo 0 -> 0") and r.data.get("ammo_before") == 0 and r.data.get("ammo_after") == 0)
    # a good shot reports the ammo before and after
    nv, b = make(bot_cls)
    nv.ammo_form, b.ammo_item = AMMO, AMMO
    r = b.fire(ref(RAIDER), shots=3, expect_hit=True)
    check("fire_ammo_ok", r.ok and r.data.get("ammo_before") == 30 and r.data.get("ammo_after") == 27)
    # talk with a box up and a dialogue that never opens
    nv, b = make(bot_cls, mutant="dialog_never")
    nv.native_boxes = [1]
    r = b.talk(ref(NPC))
    check("talk_menu", r.code == "postcondition" and _has(r, "300 units away", "message box") and r.data.get("menu") == "message")
    # Activate with a box up: the answer carries the menu so the caller can see it
    nv, b = make(bot_cls)
    nv.native_boxes = [1]
    r = b.activate_crosshair()
    check("crosshair_menu", r.ok and r.data.get("menu") == "message" and r.data.get("msgs_queued") == 1)
    # a terminal that does not open
    nv, b = make(bot_cls, mutant="activate_noop")
    nv.menu_kind = "pipboy"
    r = b.open(ref(TERM), timeout_s=1.0)
    check("open_menu", r.code == "postcondition" and _has(r, "did not open", "pipboy menu is open"))
    return sorted(bad)


class SaysWhy(unittest.TestCase):
    def test_every_failure_names_its_cause(self):
        self.assertEqual(_problems(Bot), [])

    def test_twins_lose_exactly_their_own_explanations(self):
        self.assertEqual(_problems(QuietMenuBot), ["crosshair_menu", "fire_menu", "open_menu", "talk_menu", "walk_menu", "walk_timeout", "walk_wall"])
        self.assertEqual(_problems(UnguardedAimBot), ["aim_pitch", "aim_too_close"])
        self.assertEqual(_problems(BlindFireBot), ["fire_ammo", "fire_ammo_ok", "fire_unarmed"])
        self.assertEqual(_problems(BlindWalkBot), ["walk_boxed_in", "walk_progress"])

    def test_the_short_code_survives_the_explanation(self):
        r = ActionResult(False, "stuck: 340 units left, no menu is open")
        self.assertEqual(r.code, "stuck")
        self.assertEqual(ActionResult(False, "rpc_error:boom").code, "rpc_error")
        self.assertEqual(ActionResult(False, "timeout").code, "timeout")
        nv, b = make()
        nv.world.add_actor(0x620, (30000, 0, 0))
        r = b.follow(ref(0x620), keep=150, duration_s=2)       # goto times out inside follow: follow ends on its own postcondition
        self.assertEqual(r.code, "postcondition", r)

    def test_walk_path_passes_the_explanation_on(self):
        nv, b = make()
        nv.native_boxes = [1]
        r = b.walk_path([(0, 600, 0), (0, 1200, 0)], radius=60, timeout_s=30)
        self.assertEqual(r.code, "stuck")
        self.assertIn("message box", r.reason)
        self.assertEqual(r.data["waypoint"], 0)

    def test_nothing_is_pressed_unless_asked(self):
        """The opt-in rule: explaining a failure only looks. A box that was up when the shot failed is still up (auto_dismiss off)."""
        nv, b = make()
        nv.native_boxes = [1, 2]
        for r in (b.fire(ref(RAIDER)), b.talk(ref(NPC)), b.activate_crosshair(), b.goto((0, 1500, 0), radius=80, timeout_s=10)):
            self.assertIn(r.action, ("fire", "talk", "activate_crosshair", "goto"))
        self.assertEqual((nv.native_presses, nv.native_boxes), ([], [1, 2]))
        self.assertFalse(any(c[0] == "bot.menu.press" for c in nv.calls))

    def test_auto_dismiss_still_clears_the_box_first(self):
        nv, b = make()
        nv.native_boxes = [1, 2]
        b.auto_dismiss = True
        r = b.fire(ref(RAIDER), expect_hit=True)
        self.assertTrue(r.ok, r)
        self.assertEqual((nv.native_presses, nv.native_boxes), ([0, 0], []))
        nv, b = make()
        nv.native_boxes = [1]
        b.auto_dismiss = True
        self.assertTrue(b.activate_crosshair().ok)
        self.assertEqual((nv.native_presses, nv.native_boxes), ([0], []))

    def test_a_box_that_will_not_close_is_the_reason(self):
        nv, b = make(mutant="msgbox_stuck")
        nv.native_boxes = [1]
        b.auto_dismiss = True
        r = b.fire(ref(RAIDER))
        self.assertEqual(r.code, "menu")
        self.assertIn("would not close", r.reason)

    def _wall_under_menu(self, bot_cls, kind):
        nv, b = make(bot_cls)
        nv.menu_kind = kind
        nv.world.add_wall(-5000, 400, 5000, 420)
        return b.goto((0, 1000, 0), radius=80, timeout_s=60)

    def test_an_unknown_menu_does_not_cancel_the_unstick_tries(self):
        """bot.menu says 'other' for a top menu id the plugin does not know (maybe a HUD overlay): the walk still tries its sidesteps."""
        r = self._wall_under_menu(Bot, "other")
        self.assertEqual((r.code, r.data["unstick"]), ("stuck", 5), r)
        self.assertIn("other menu is open", r.reason)
        r = self._wall_under_menu(Bot, "pipboy")          # a known blocking menu ends the walk on the first stuck window
        self.assertEqual((r.code, r.data["unstick"]), ("stuck", 0), r)
        self.assertIn("eats the input", r.reason)
        self.assertEqual(self._wall_under_menu(EagerStuckBot, "other").data["unstick"], 0)     # the twin differs

    def test_close_enough_targets_still_work_with_a_smaller_limit(self):
        nv, b = make()
        self.assertTrue(b.aim_at(ref(CLOSE), min_dist=20.0).ok)


if __name__ == "__main__":
    unittest.main()
