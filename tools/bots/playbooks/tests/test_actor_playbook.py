"""Game-free tests for the two-copy actor ownership playbook (pb_actors.OwnershipTransferPlaybook), with mutation twins.

Run: python -I tools/bots/playbooks/tests/run_actor_tests.py   (prints ALL OK)

The playbook itself has never run against a game. Here it runs against T6b's fake co-op world, extended (below, in this file only) with
the `coop.owner` bridge method and `GetPos`, in the shape plugin/actors' coop.owner answers. Every twin is a deliberately wrong
fake behaviour that the playbook must fail with a reason that names the problem.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(REPO, "tools", "bots"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from playbooks import core, fakecoop, pb_combat, probe  # noqa: E402
from playbooks.pb_actors import OwnershipTransferPlaybook, Trace, agreed, dist, owner_info  # noqa: E402

# own_s / handoff_s / settle_s are virtual seconds of the fake world (playbooks/clocks.py), not CPU time: see test_playbooks.py
OPTS = {"fast": True, "anim": False, "settle_s": 2.0, "settle_poll_s": 0.0, "own_s": 0.3, "handoff_s": 0.3, "repo_root": REPO, "rounds": 2}


class OwnerNv(fakecoop.FakeNv):
    """FakeNv plus coop.owner and ref.GetPos, with switchable wrong behaviour (`extra`)."""

    def __init__(self, bot, extra):
        super().__init__(bot)
        self.extra = set(extra)

    def _ref(self, text):
        return self.bot.view["refs"].get(fakecoop.norm(text))

    def call(self, method, params=None, timeout_s=0):
        if method != "coop.owner":
            return super().call(method, params, timeout_s)
        if "unsupported" in self.extra:
            return {"unsupported": True, "reason": "actor-ownership service is not in the plugin build"}
        r = self._ref(params["ref"])
        idx = self.bot.idx
        if r is None:
            return {"owner": 255, "epoch": 0, "known": False, "self": idx}
        owner, epoch = r["owner"], r["epoch"]
        if "epoch_double" in self.extra:
            epoch = epoch * 2                                   # a handoff raises the epoch by two
        ai = owner == idx
        if "ai_both" in self.extra:
            ai = True                                           # old and new owner both simulate
        if "ai_none" in self.extra:
            ai = False
        return {"owner": owner, "epoch": epoch, "known": True, "mine": owner == idx, "pending": False, "self": idx, "ai": ai, "standIn": False}

    def eval(self, expr, ref=None):
        if ref is not None:                                     # exec {cmd, ref}: `ref` is the calling reference (Plugin.esm:HEX or bare hex)
            expr = f"{probe.hexid(ref)}.{expr}"
        m = re.match(r"(\w+)\.GetPos ([XYZ])", expr)
        if not m:
            return 0.0
        k = [k for k in self.bot.view["refs"] if k.endswith(":" + m.group(1).upper().lstrip("0"))] or [k for k in self.bot.view["refs"] if probe.hexid(k) == m.group(1).upper().lstrip("0")]
        r = self.bot.view["refs"][k[0]]
        x, y, z = r["pos"]
        if "teleport" in self.extra and r["owner"] != 0 and self.bot.idx != 0:
            x += 5000.0                                         # the new owner's copy jumps when the handoff happens
        return {"X": x, "Y": y, "Z": z}[m.group(2)]


def run(bugs=(), extra=(), opts=None, n=2):
    w, refs = pb_combat.fake_handoff(n, set(bugs))
    for b in w.bots:
        b.nv = OwnerNv(b, extra)
        if "recreate" in extra:
            orig, seen = b.ref_count, {"moved": False}

            def counted(base, cell="", _o=orig, _b=b, _s=seen):   # after the first handoff one more live ref of the base exists (a clone)
                _s["moved"] = _s["moved"] or any(r.get("epoch", 1) > 1 for r in _b.view["refs"].values() if r.get("kind") == "raider")
                return _o(base, cell) + (1 if _s["moved"] else 0)
            b.ref_count = counted
    d = tempfile.mkdtemp(prefix="t-actors-")
    w.flight_dir = os.path.join(d, "flight")
    o = dict(OPTS)
    o.update(opts or {})
    try:
        return core.run_playbook(OwnershipTransferPlaybook(), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, o)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Clean(unittest.TestCase):
    def test_passes_on_a_correct_world(self):
        r = run()
        self.assertTrue(r.ok, r.reason or [(s.name, s.reason) for s in r.steps if not s.ok])
        self.assertGreaterEqual(len(r.steps), 6)

    def test_more_rounds_pass(self):
        r = run(opts={"rounds": 4})
        self.assertTrue(r.ok, r.reason or [(s.name, s.reason) for s in r.steps if not s.ok])

    def test_helpers(self):
        snap = {"a": dict(owner=0, epoch=2, known=True, pending=False), "b": dict(owner=0, epoch=2, known=True, pending=False)}
        self.assertEqual(agreed(snap), (0, 2))
        snap["b"]["epoch"] = 3
        self.assertIsNone(agreed(snap))
        snap["b"]["epoch"] = 2
        snap["b"]["pending"] = True
        self.assertIsNone(agreed(snap))
        self.assertAlmostEqual(dist((0, 0, 0), (3, 4, 0)), 5.0)
        t = Trace()
        t.rows = [dict(machine="a", epoch=3, pos=(0, 0, 0), ai=False, pending=False, mine=False),
                  dict(machine="a", epoch=2, pos=(0, 0, 0), ai=False, pending=False, mine=False)]
        self.assertIn("epoch fall", t.epochs_never_fall())
        t.rows = [dict(machine="a", epoch=1, pos=(0, 0, 0), ai=True, pending=False, mine=True),
                  dict(machine="b", epoch=1, pos=(0, 0, 0), ai=True, pending=False, mine=False)]
        self.assertIn("more than one copy", t.ai_conflict())
        t.rows = [dict(machine="a", epoch=1, pos=(0, 0, 0), ai=True, pending=True, mine=True)]
        self.assertIn("pending", t.ai_conflict())
        t.rows = [dict(machine="a", epoch=1, pos=(0, 0, 0), ai=False, pending=False, mine=False),
                  dict(machine="a", epoch=1, pos=(900, 0, 0), ai=False, pending=False, mine=False)]
        self.assertEqual(t.max_jump("a"), 900.0)


class Twins(unittest.TestCase):
    def assert_fails(self, r, *words):
        self.assertFalse(r.ok, "the playbook passed on a deliberately wrong world")
        text = (r.reason + " " + " ".join(s.reason for s in r.steps if not s.ok)).lower()
        for w in words:
            self.assertIn(w.lower(), text, text)

    def test_ai_on_both_copies(self):
        self.assert_fails(run(extra={"ai_both"}), "ai")

    def test_ai_on_nobody(self):
        self.assert_fails(run(extra={"ai_none"}), "ai")

    def test_epoch_jumps_by_two(self):
        self.assert_fails(run(extra={"epoch_double"}), "epoch")

    def test_npc_jumps_at_the_handoff(self):
        self.assert_fails(run(extra={"teleport"}), "units")

    def test_npc_recreated(self):
        self.assert_fails(run(extra={"recreate"}), "refs")

    def test_ownership_flaps_at_the_border(self):
        self.assert_fails(run(bugs={"flap_owner"}))

    def test_handoff_not_delivered(self):
        self.assert_fails(run(bugs={"owner_split"}))

    def test_service_missing_says_so(self):
        r = run(extra={"unsupported"})
        self.assertFalse(r.ok)
        self.assertIn("coop.owner", r.reason + " ".join(s.reason for s in r.steps))

    def test_probe_raises_when_unsupported(self):
        w, _ = pb_combat.fake_handoff(2, set())
        w.bots[0].nv = OwnerNv(w.bots[0], {"unsupported"})
        with self.assertRaises(probe.MissingBridge):
            owner_info(w.bots[0], "FalloutNV.esm:A00001")


if __name__ == "__main__":
    unittest.main()
