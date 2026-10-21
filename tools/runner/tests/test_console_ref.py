"""console_ref.split_ref_line: the helper that moves old "<ref>.<Func> args" lines to exec {cmd, ref}, plus mutation twins.

The game's console has no "<hexFormID>.Command" form (in game, 2026-10-21: Script command "00103E61.GetOpenState" not
found), so every such line must leave the helper as (cmd, ref). "player." lines stay as they are.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_ref  # noqa: E402
import nvbridge  # noqa: E402
from console_ref import RefLineError, join_ref_line, split_ref_line  # noqa: E402

SPLIT = [
    ("00103E61.GetOpenState", ("GetOpenState", "00103E61")),
    ("00103E37.additem 00015169 3", ("additem 00015169 3", "00103E37")),
    ("  00104C70.setav Health 100", ("setav Health 100", "00104C70")),
    ("0x103E61.lock 50", ("lock 50", "0x103E61")),
    ("104C1C.GetDead", ("GetDead", "104C1C")),
    ("FalloutNV.esm:103E61.GetLocked", ("GetLocked", "FalloutNV.esm:103E61")),
    ("01000ABC.Activate player 1", ("Activate player 1", "01000ABC")),
]
UNCHANGED = [
    "player.moveto 00103E61",
    "Player.GetItemCount 0000000F",
    "player.GetDistance 00104C70",
    "player.SetPos X 12.50",
    "setstage 0010A214 10",
    "GetStage 104C1C",
    "SetObjectiveCompleted 0010A214 10 1",
    "Bed.Activate player",                              # an EditorID made of the letters A-F, not a form id
    "GSDocMitchellHouseIntDoorREF.GetOpenState",        # an EditorID: the bridge cannot resolve it, the line stays as written
    'printc "NVCOOP_EVAL=%g" (GetStage 104C1C)',
    "set 000842DD.bPrimmClosed to 1",                   # a quest variable (L7 sweep), not a calling reference
]
REFUSED = [
    'printc "NVCOOP_EVAL=%g" (00103E61.GetOpenState)',  # the old eval line: the ref is inside the expression
    "00104C70.GetAV Health + 00104C71.GetAV Health",    # two calling references in one line
    "player.GetDistance 00104C70 + 00104C71.GetDead",
    "GetStage 104C1C + 00103E61.GetLocked",
]


def defects(split):
    """The table that every splitter must pass. Returns the list of failures (empty = correct)."""
    out = []
    for line, want in SPLIT:
        try:
            got = split(line)
        except Exception as e:  # noqa: BLE001
            got = repr(e)
        if got != want:
            out.append("split %r -> %r, wanted %r" % (line, got, want))
    for line in UNCHANGED:
        try:
            got = split(line)
        except Exception as e:  # noqa: BLE001
            got = repr(e)
        if got != (line, None):
            out.append("%r must stay unchanged, got %r" % (line, got))
    for line in REFUSED:
        try:
            got = split(line)
        except RefLineError:
            continue
        out.append("%r must be refused, got %r" % (line, got))
    return out


class SplitRefLine(unittest.TestCase):
    def test_table(self):
        self.assertEqual(defects(split_ref_line), [])

    def test_nvbridge_reexports_the_helper(self):
        self.assertIs(nvbridge.split_ref_line, split_ref_line)
        self.assertIs(nvbridge.RefLineError, RefLineError)

    def test_join_is_the_inverse_for_labels(self):
        for line, (cmd, ref) in SPLIT[:2]:
            self.assertEqual(join_ref_line(cmd, ref), line)
        self.assertEqual(join_ref_line("player.GetDead", None), "player.GetDead")


class MutationTwins(unittest.TestCase):
    """Each twin is a plausible wrong splitter; the table must reject it."""

    def test_no_split_is_caught(self):
        self.assertNotEqual(defects(lambda line: (line, None)), [])

    def test_player_taken_as_a_reference_is_caught(self):
        def bad(line):
            if line.lower().startswith("player."):
                return line[7:], "player"
            return split_ref_line(line)
        self.assertNotEqual(defects(bad), [])

    def test_inner_reference_passed_through_is_caught(self):
        def bad(line):
            m = console_ref.LEAD_RE.match(line)
            return (m.group("cmd").strip(), m.group("ref")) if m and not console_ref.PLAYER_RE.match(line) else (line, None)
        self.assertNotEqual(defects(bad), [])

    def test_quest_variable_refused_is_caught(self):
        import re
        orig = console_ref.SET_VAR_RE
        console_ref.SET_VAR_RE = re.compile(r"(?!)")       # the twin: no exemption for 'set <quest>.<var> to'
        try:
            self.assertNotEqual(defects(split_ref_line), [])
        finally:
            console_ref.SET_VAR_RE = orig

    def test_hex_words_taken_as_ids_is_caught(self):
        import re
        lead = re.compile(r"^\s*([0-9A-Fa-f]{1,8})\.(\w.*)$")

        def bad(line):
            m = lead.match(line)
            return (m.group(2), m.group(1)) if m else split_ref_line(line)
        self.assertNotEqual(defects(bad), [])


if __name__ == "__main__":
    unittest.main()
