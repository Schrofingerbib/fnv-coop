"""Game-free tests for tools/ingame_gate.py check logic, each with a mutation twin (a broken input the check must reject)."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(HERE.parent))
import ingame_gate as g  # noqa: E402

ORDER = ["FalloutNV.esm", "DeadMoney.esm", "HonestHearts.esm", "OldWorldBlues.esm", "LonesomeRoad.esm",
         "TribalPack.esm", "MercenaryPack.esm", "ClassicPack.esm", "CaravanPack.esm", "GunRunnersArsenal.esm"]


def snap(nq=60, obj_per=20, ng=12):
    return {"schemaVersion": 1, "meta": {"counts": {"quests": nq, "globals": ng}},
            "shared": {"quests": {f"q{i}": {"objectives": {str(j): {} for j in range(obj_per)}} for i in range(nq)},
                       "globals": {f"g{i}": {"value": 1} for i in range(ng)}}}


class GateChecks(unittest.TestCase):
    def test_loadorder(self):
        self.assertTrue(g.check_loadorder({"count": 10, "plugins": [{"index": i, "name": n} for i, n in enumerate(ORDER)]}).ok)
        sw = list(ORDER)
        sw[8], sw[9] = sw[9], sw[8]
        self.assertFalse(g.check_loadorder({"plugins": [{"index": i, "name": n} for i, n in enumerate(sw)]}).ok)

    def test_version(self):
        self.assertTrue(g.check_version({"gitHash": "abc1234", "dirty": False}, "abc1234ffff").ok)
        self.assertTrue(g.check_version({"gitHash": "abc1234ffff9999", "dirty": True}, "abc1234ffff").ok)
        r = g.check_version({"gitHash": "abc1234", "dirty": True}, "abc1234ffff")
        self.assertTrue(r.ok and r.warn)
        self.assertFalse(g.check_version({"gitHash": "def5678"}, "abc1234ffff").ok)      # stale dll
        self.assertFalse(g.check_version({"gitHash": "unknown"}, "abc1234ffff").ok)
        self.assertFalse(g.check_version({"gitHash": "abc1234", "mutant": True}, "abc1234ffff").ok)

    def test_hooks(self):
        good = {"installed": 36, "failed": 0, "criticalOk": True, "hooks": [{"name": "a", "status": "installed"}]}
        self.assertTrue(g.check_hooks(good).ok)
        for bad in ({**good, "failed": 1}, {**good, "criticalOk": False}, {**good, "installed": 0},
                    {**good, "hooks": [{"name": "x", "status": "FAILED sig"}]}):
            self.assertFalse(g.check_hooks(bad).ok)

    def test_state_thresholds(self):
        self.assertTrue(g.check_state(snap(), False).ok, g.check_state(snap(), False).detail)
        self.assertFalse(g.check_state(snap(nq=10), False).ok)
        self.assertFalse(g.check_state(snap(obj_per=1), False).ok)      # 60 objectives
        self.assertFalse(g.check_state(snap(ng=3), False).ok)
        self.assertFalse(g.check_state({}, False).ok)
        self.assertFalse(g.check_state({"meta": {"error": "access violation"}}, False).ok)

    def test_capture(self):
        ok = {"txns": [{"effects": [{"type": "SetStage", "quest": "falloutnv.esm:10A214", "stage": 10}]}]}
        self.assertTrue(g.check_capture(ok).ok)
        self.assertFalse(g.check_capture({"txns": []}).ok)
        self.assertFalse(g.check_capture({"txns": [{"effects": [{"type": "SetStage", "quest": "falloutnv.esm:10A214", "stage": 20}]}]}).ok)
        self.assertFalse(g.check_capture({"txns": [{"effects": [{"type": "SetStage", "quest": "falloutnv.esm:000001", "stage": 10}]}]}).ok)
        self.assertFalse(g.check_capture({"txns": [{"effects": [{"type": "SetGlobal", "quest": "falloutnv.esm:10A214", "stage": 10}]}]}).ok)


if __name__ == "__main__":
    unittest.main()
