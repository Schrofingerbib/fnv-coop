"""Sweep 2026-10-22 13:50, vats_classes / vats_world_moves: the bridge refuses the word 'player' as an exec ref, and printc refuses a Form.

  * exec {cmd, ref:"player"} -> -32602 "bad ref 'player': 'player' is not a hex number": the playbook primitives (seed_items, set_health, ...) take
    "player" as a holder, so they must send the console's own "player.<cmd>" line with no ref;
  * `printc "%g" (player.GetEquippedObject 5)` -> "Invalid expression for parameter 2. Expected variable (got Form)": the expression must be a number;
  * run_vats --weapon: the console cannot resolve an editor id ("WeapNV9mmPistol" in params.local.json), only Plugin.esm:HEX.
Each rule has a twin: the code as it was, which these tests must fail on.
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parents[1]))
from bots import Bot, FakeBridge, World  # noqa: E402
from bots import primitives as P  # noqa: E402
from bots.bot import RpcError  # noqa: E402
import run_vats  # noqa: E402
from playbooks import fakecoop, pb_vats, probe  # noqa: E402
from playbooks.probe import Result  # noqa: E402


def old_exec_ref(bot, ref, cmd):
    r = bot.nv.exec(cmd, ref=ref)
    return Result(bool(r.get("ok", True)), "")


def old_eval_ref(bot, ref, fn):
    return bot.nv.eval(fn, ref=ref)


class _Bot:
    def __init__(self, nv):
        self.nv = nv


def fresh():
    nv = FakeBridge(World())
    return _Bot(nv), nv


class PlayerRef(unittest.TestCase):
    def test_the_fake_refuses_player_as_a_ref_like_the_bridge(self):
        _, nv = fresh()
        with self.assertRaises(RpcError) as cm:
            nv.exec("additem 5 1", ref="player")
        self.assertEqual(cm.exception.code, -32602)
        self.assertIn("bad ref 'player'", str(cm.exception))

    def test_the_old_primitives_hit_that_error(self):
        b, _ = fresh()
        for call in (lambda: old_exec_ref(b, "player", "additem 5 1"), lambda: old_eval_ref(b, "player", "GetItemCount 5")):
            with self.assertRaises(RpcError):
                call()

    def test_seed_items_and_set_health_on_the_player_send_a_player_line(self):
        b, nv = fresh()
        self.assertTrue(probe.seed_items(b, "player", "FalloutNV.esm:0E3778", 1).ok)
        self.assertIn(("exec", "player.additem E3778 1"), nv.calls)
        probe._exec_ref(b, "Player", "setav Health 100")   # the fake may not know the verb: the call is what counts
        self.assertIn(("exec", "player.setav Health 100"), nv.calls)
        self.assertFalse(any(len(c) == 3 and c[2].lower() == "player" for c in nv.calls if c[0] == "exec"), "no call may carry ref='player'")

    def test_eval_on_the_player_reads_a_player_expression(self):
        b, nv = fresh()
        nv.me.items[0x500] = 3
        self.assertEqual(probe._eval_ref(b, "player", "GetItemCount 500"), 3.0)

    def test_a_real_ref_still_goes_as_a_ref(self):
        b, nv = fresh()
        nv.world.add_actor(0x602, (1, 2, 3))
        probe._exec_ref(b, "FalloutNV.esm:602", "setav Health 100")
        self.assertIn(("exec", "setav Health 100", "FalloutNV.esm:602"), nv.calls)


class EquippedWeaponExpression(unittest.TestCase):
    def test_both_copies_of_the_expression_agree_and_are_a_number(self):
        self.assertEqual(P.equipped_weapon(), probe.EQUIPPED_WEAPON_EXPR)
        self.assertIn("&&", probe.EQUIPPED_WEAPON_EXPR)      # a Form alone is refused by printc

    def test_the_fake_world_refuses_the_bare_form_like_the_game(self):
        w, _refs = pb_vats.fake_vats_class("gun")(2, set(), lag=1)
        nv = fakecoop.make_machines(w)[0].bot.nv
        with self.assertRaises(Exception) as cm:
            nv.eval("player.GetEquippedObject 5")
        self.assertIn("Expected variable (got Form)", str(cm.exception))
        self.assertEqual(nv.eval(probe.EQUIPPED_WEAPON_EXPR), 0.0)

    def test_a_game_that_cannot_tell_does_not_turn_into_no_weapon(self):
        class Refuses:
            def eval(self, expr, ref=None):
                raise RuntimeError("cannot evaluate: Invalid expression")
        self.assertTrue(pb_vats.has_weapon_equipped(_Bot(Refuses())))

    def test_the_read_only_inspector_lets_the_number_form_through(self):
        from bots import inspect_live as IL
        class Nv:
            def eval(self, expr, ref=None):
                return 0.0
        ro = IL.ReadOnlyBridge(Nv())
        self.assertEqual(ro.eval(P.equipped_weapon()), 0.0)
        with self.assertRaises(IL.ReadOnlyViolation):
            ro.eval("(SetStage 100 10) && 1")

    def test_the_expression_that_is_sent_is_the_number_form(self):
        seen = []

        class Nv:
            def eval(self, expr, ref=None):
                seen.append(expr)
                return 0.0
        self.assertFalse(pb_vats.has_weapon_equipped(_Bot(Nv())))
        self.assertEqual(seen, [probe.EQUIPPED_WEAPON_EXPR])


class WeaponArgument(unittest.TestCase):
    def test_form_ids_pass_and_editor_ids_are_refused_with_the_reason(self):
        for ok in ("FalloutNV.esm:0E3778", "FalloutNV.esm:E3778", "0E3778", "0x4335"):
            self.assertEqual(run_vats.weapon_ref(ok), ok)
        for bad in ("WeapNV9mmPistol", "WeapLaserPistol", "Stimpak"):
            with self.assertRaises(ValueError) as cm:
                run_vats.weapon_ref(bad)
            self.assertIn("editor ids", str(cm.exception))

    def test_an_editor_id_is_resolved_when_the_game_data_table_knows_it(self):
        self.assertEqual(run_vats.weapon_ref("WeapNV9mmPistol", {"WeapNV9mmPistol": "FalloutNV.esm:0E3778"}), "FalloutNV.esm:0E3778")

    def test_the_cli_refuses_an_editor_id_before_touching_a_game(self):
        import io
        from contextlib import redirect_stderr
        import tempfile
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, redirect_stderr(err):
            rc = run_vats.main(["--instances", "0,1", "--playbook", "vats_world_moves", "--weapon", "any=NoSuchWeaponEverMade", "--outdir", d])
        self.assertEqual(rc, 2)
        self.assertIn("editor ids", err.getvalue())


if __name__ == "__main__":
    unittest.main()
