"""tools/bots/inspect_live.py over fake bridges: what the table shows, that it never changes game state, and the twins that prove both.

Run: python -I tools/bots/run_tests.py   (prints ALL OK)
"""
import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bots import Bot, World, inspect_live as IL  # noqa: E402
from bots.bot import ConnectionLost  # noqa: E402
from livefake import LiveFake, coop_status_client, coop_status_host  # noqa: E402

NPC = 0x602
NPC_REF = "FalloutNV.esm:602"


def make_pair():
    """Host: idle, at (100,200,30), health 180. Client: two engine message boxes up, nothing equipped, 2 behind the host."""
    w = World()
    w.add_actor(NPC, (300, 0, 0), health=65.0)
    host, client = LiveFake(w, role="host"), LiveFake(w, role="client")
    host.me.pos, host.me.health = [100.0, 200.0, 30.0], 180.0
    client.native_boxes, client.weapon_equipped = [1, 2], False
    client.ui["recentBoxes"] = [
        {"seq": 6, "script": "00001111", "thisObj": "0", "message": "FalloutNV.esm:1", "text": "Old news"},
        {"seq": 7, "script": "00001234", "thisObj": "0", "message": "FalloutNV.esm:2", "text": "You have found a hidden cache of caps worth far too many words to show"},
    ]
    return host, client


def run_inspect(host, client, **kw):
    return IL.inspect_all([("0", host), ("1", client)], **kw)


def _state(nv):
    """Everything an inspector could disturb, plus the virtual clock (it must not wait either)."""
    return (list(nv.me.pos), dict(nv.me.angle), dict(nv.me.items), sorted(nv.me.equipped), list(nv.native_boxes), list(nv.native_presses),
            sorted(nv.held), nv.shots_left, nv.me.health, nv.sneaking, nv.box, list(nv.box_answers), nv.me.in_dialogue, nv.clock.t,
            nv.me.ammo, nv.weapon_out, {i: (t.health, t.disabled, list(t.pos)) for i, t in nv.world.things.items()})


def _table_errors(reports):
    """-> the things the table should show for make_pair() that it does not (empty = complete)."""
    text = IL.render(reports)
    want = {
        "menu": ["message (2 queued)", "queue idx 0 of 2"],
        "weapon": ["holstered, NOTHING equipped", "holstered, equipped"],
        "health": ["180", "200"],
        "player": ["100 200 30", "cell 00000001"],
        "coop": ["lastSeq", "120", "118", "slot 1: 118 (2 behind)", "allSynced", "False", "ok 12  mismatch 1  repairs 1",
                 "quest:00104C70:stage: host 20 client 10", "True (rebases 0)"],
        "boxes": ["#7 00001234 You have found a hidden", "..."],
        "ref": ["ref distance", "284 units", "300 units", "ref health", "65 alive", "ref owner", "slot 0 epoch 2"],
        "role": ["host", "client"],
        "problems": ["a message box is up (2 queued), it eats all input", "nothing is equipped", "lastSeq differs",
                     "1 digest mismatch(es)", "not all clients are synced"],
    }
    return [f"{k}: {w!r}" for k, ws in want.items() for w in ws if w not in text]


# ---- twins ----
class OpenBridge(IL.ReadOnlyBridge):
    """Twin: a guard that lets everything through."""

    def call(self, method, params=None, timeout_s=10.0):
        return self._nv.call(method, params or {}, timeout_s=timeout_s)

    def exec(self, cmd, *a, **kw):
        return self._nv.exec(cmd, *a, **kw)

    def eval(self, expr, ref=None):
        return self._nv.eval(expr) if ref is None else self._nv.eval(expr, ref=ref)


def _guard_holds(bridge_cls):
    nv, _ = make_pair()
    g = bridge_cls(nv)
    probes = (lambda: g.call("bot.menu.press", {"button": 0}), lambda: g.call("bot.fire", {"shots": 1}), lambda: g.exec("player.additem 1 1"),
              lambda: g.eval("player.AddItem 1 1"), lambda: g.call("coop.leave", {}))
    for p in probes:
        try:
            p()
        except IL.ReadOnlyViolation:
            continue
        except Exception:  # noqa: BLE001  (the call reached the game and the game refused it: the guard let it out)
            return False
        return False
    return True


def leaky_inspect(nv, name="1"):
    """Twin: an inspector that also clears the message boxes it finds (what a well-meaning tool might add)."""
    rep = IL.inspect_one(nv, name)
    Bot(nv, "leak").dismiss_messages(quiet_s=0.0)
    return rep


class Table(unittest.TestCase):
    def test_table_shows_every_row(self):
        host, client = make_pair()
        reports = run_inspect(host, client, ref=NPC_REF)
        self.assertEqual(_table_errors(reports), [])
        out = IL.render(reports)
        self.assertLess(max(len(x) for x in out.splitlines()), 110)       # compact: two copies fit a normal terminal
        self.assertEqual(IL.build_rows(reports)[0][0], "copy")

    def test_each_row_is_needed_by_the_check(self):
        """Twin: drop one section of the data; the completeness check must notice (so it is not passing on leftovers)."""
        for key in ("menu", "weapon", "health", "player", "coop", "boxes", "ref"):
            with self.subTest(dropped=key):
                host, client = make_pair()
                reports = run_inspect(host, client, ref=NPC_REF)
                for r in reports:
                    r.pop(key, None)
                self.assertNotEqual(_table_errors(reports), [], f"table check ignores {key}")

    def test_problems_are_named_in_plain_words(self):
        host, client = make_pair()
        probs = IL.problems(run_inspect(host, client))
        self.assertIn("instance 1: a message box is up (2 queued), it eats all input", probs)
        self.assertIn("instance 1: nothing is equipped", probs)
        self.assertIn("lastSeq differs: instance 0 at 120, instance 1 at 118 (2 behind)", probs)
        self.assertIn("instance 0: 1 digest mismatch(es), 1 repaired", probs)
        host.coop = coop_status_host(120, acked=(120,), all_synced=True, mismatch=0, repairs=0)
        client.coop = coop_status_client(120)
        client.native_boxes, client.weapon_equipped = [], True
        self.assertEqual(IL.problems(run_inspect(host, client)), [])

    def test_ref_block_is_optional_and_other_cell_is_marked(self):
        host, client = make_pair()
        self.assertNotIn("ref distance", IL.render(run_inspect(host, client)))
        host.world.things[NPC].cell = 9
        self.assertIn("(other cell)", IL.render(run_inspect(host, client, ref=NPC_REF)))
        host.world.things[NPC].health = 0
        reports = run_inspect(host, client, ref=NPC_REF)
        self.assertIn("instance 0: the ref is dead", IL.problems(reports))
        self.assertIn("0 DEAD", IL.render(reports))

    def test_top_desync_entries_newest_first_and_capped(self):
        entries = [{"key": f"k{i}", "host": "a", "client": "b"} for i in range(5)]
        st = coop_status_host(entries=entries)
        st["desyncs"]["desyncs"].insert(0, {"id": 0, "slot": 1, "repaired": False, "entries": [{"key": "old", "host": "1", "client": "2"}]})
        top = IL.coop_summary(st)["desyncTop"]
        self.assertEqual([e["key"] for e in top], ["k0", "k1", "k2"])        # the newest report first, three at most
        self.assertEqual(IL.coop_summary(st, top=4)["desyncTop"][-1]["key"], "k3")

    def test_old_build_without_modules_shows_na(self):
        host, client = make_pair()
        host.coop = host.ui = host.owner = None
        host.menu_method = False
        reports = run_inspect(host, client, ref=NPC_REF)
        out = IL.render(reports)
        self.assertEqual(reports[0]["coop"], "n/a")
        self.assertEqual(reports[0]["menu"], "n/a")
        self.assertEqual(reports[0]["ref"]["owner"], "n/a")
        self.assertIn("n/a", out)
        self.assertEqual(reports[1]["role"], "client")          # the other copy is untouched
        self.assertIsNone(reports[0]["role"])

    def test_unreachable_copy_is_a_column_not_a_crash(self):
        host, _ = make_pair()
        reports = IL.inspect_all([("0", host), ("1", ConnectionLost("cannot open pipe"))])
        out = IL.render(reports)
        self.assertIn("unreachable: cannot open pipe", out)
        self.assertTrue(any(p.startswith("instance 1: unreachable") for p in IL.problems(reports)))
        self.assertTrue(reports[0]["reachable"])
        none = IL.inspect_all([("0", ConnectionLost("x"))])
        self.assertIn("x", IL.render(none))

    def test_a_game_lost_midway_marks_that_copy(self):
        host, client = make_pair()
        def dies(method, params=None, timeout_s=35.0, _real=client.call):
            if method == "coop.status":
                raise ConnectionLost("pipe closed")
            return _real(method, params, timeout_s)
        client.call = dies
        reports = run_inspect(host, client, ref=NPC_REF)
        self.assertTrue(reports[0]["reachable"])
        self.assertFalse(reports[1]["reachable"])


class ReadOnly(unittest.TestCase):
    def test_inspecting_changes_nothing_and_waits_for_nothing(self):
        host, client = make_pair()
        before = (_state(host), _state(client))
        run_inspect(host, client, ref=NPC_REF)
        self.assertEqual((_state(host), _state(client)), before)
        self.assertEqual(client.native_boxes, [1, 2])      # the box that is up stays up

    def test_only_reads_go_out(self):
        host, client = make_pair()
        run_inspect(host, client, ref=NPC_REF)
        for nv in (host, client):
            for c in nv.calls:
                if c[0] == "exec":
                    self.assertTrue(c[1].startswith('printc "NVCOOP_EVAL='), c)       # an expression, never a command
                elif c[0] != "eval":
                    self.assertIn(c[0], IL.READ_ONLY_METHODS, c)
        names = {c[0] for c in client.calls}
        self.assertTrue({"bot.menu", "coop.status", "ui.status", "ref.info", "coop.owner"} <= names, names)

    def test_the_guard_refuses_everything_else(self):
        self.assertTrue(_guard_holds(IL.ReadOnlyBridge))
        host, _ = make_pair()
        g = IL.ReadOnlyBridge(host)
        with self.assertRaises(IL.ReadOnlyViolation):
            g.call("bot.menu.press", {"button": 0})
        with self.assertRaises(IL.ReadOnlyViolation):
            g.exec("player.additem f 1")
        with self.assertRaises(AttributeError):
            g.nv_secret
        self.assertEqual(host.native_presses, [])

    def test_twin_open_guard_is_caught(self):
        self.assertFalse(_guard_holds(OpenBridge))

    def test_twin_inspector_that_dismisses_boxes_is_caught(self):
        host, client = make_pair()
        before = _state(client)
        leaky_inspect(client)
        self.assertNotEqual(_state(client), before)         # the state check sees the pressed boxes
        self.assertEqual(client.native_presses, [0, 0])
        host, client = make_pair()
        before = _state(client)
        IL.inspect_one(client, "1")
        self.assertEqual(_state(client), before)

    def test_opt_in_rule_holds_for_the_failure_explanations_too(self):
        """Bot actions that explain a failure only look at the menu: with auto_dismiss off nothing is pressed."""
        host, client = make_pair()
        b = Bot(client, "x")
        self.assertFalse(b.auto_dismiss)
        b.goto((0, 1500, 0), radius=80, timeout_s=10)
        self.assertEqual((client.native_presses, client.native_boxes), ([], [1, 2]))


class CommandLine(unittest.TestCase):
    def _main(self, argv, bridges=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = IL.main(argv, bridges=bridges)
        return code, buf.getvalue()

    def test_table_and_exit_codes(self):
        host, client = make_pair()
        code, out = self._main(["--instances", "0,1", "--ref", NPC_REF], [("0", host), ("1", client)])
        self.assertEqual(code, 0)
        self.assertEqual(_table_errors(run_inspect(host, client, ref=NPC_REF)), [])
        self.assertIn("instance 0", out)
        self.assertIn("problems:", out)
        code, out = self._main(["--instances", "0"], [("0", ConnectionLost("no pipe"))])
        self.assertEqual(code, 2)
        self.assertIn("no pipe", out)

    def test_json_output(self):
        host, client = make_pair()
        code, out = self._main(["--json"], [("0", host), ("1", client)])
        data = json.loads(out)
        self.assertEqual((code, [d["name"] for d in data]), (0, ["0", "1"]))
        self.assertEqual(data[1]["menu"]["kind"], "message")

    def test_instances_parsing(self):
        self.assertEqual(IL.parse_instances("0,1"), [0, 1])
        self.assertEqual(IL.parse_instances("3"), [3])
        for bad in ("", "0,0", "-1,2", "a"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                IL.parse_instances(bad)
        code, _ = self._main(["--instances", "1,1"], [])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
