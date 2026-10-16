import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

import blocks  # noqa: E402
import effects  # noqa: E402

Q, NPC, GL, ITEM = 'P.esm:000001', 'P.esm:000002', 'P.esm:000003', 'P.esm:000004'
REFINFO = {Q: {'edid': 'MyQuest'}, NPC: {'edid': 'Bob'}, GL: {'edid': 'MyGlobal'}, ITEM: {'edid': 'Caps001'}}
GLOBALS = {'myglobal': GL}


def scpt(ref, edid, src, refs=(Q, NPC, GL, ITEM), typ='quest', vars_=('fTimer', 'bDone')):
    return {'ref': ref, 'edid': edid, 'script': {'type': typ, 'source': src, 'refs': list(refs),
                                                 'vars': [{'idx': i + 1, 'type': 'int', 'name': n} for i, n in enumerate(vars_)]}}


SRC = """scn MyQuestScript
short bDone
float fTimer

Begin GameMode
  set fTimer to fTimer + 1
  if fTimer > 5
    SetStage MyQuest 20
    set MyGlobal to 1
    Bob.Disable
  endif
End

Begin OnDeath
  SetObjectiveCompleted MyQuest 10 1
  Bob.AddScriptPackage Pk
End

Begin MenuMode 1001
  ShowMessage Msg
End
"""


class BlockTests(unittest.TestCase):
    def test_split_blocks(self):
        bl = blocks.split_blocks(SRC)
        self.assertEqual([(b[0], b[1]) for b in bl], [('GameMode', []), ('OnDeath', []), ('MenuMode', ['1001'])])
        self.assertIn('SetStage', bl[0][2])
        self.assertNotIn('Begin', bl[0][2])

    def test_analyse_blocks_and_classes(self):
        out = blocks.analyse_scripts([scpt('P.esm:000010', 'MyQuestScript', SRC)], {'P.esm:000010'}, REFINFO, GLOBALS)
        self.assertEqual([b['block'] for b in out], ['GameMode', 'OnDeath', 'MenuMode'])
        gm = out[0]
        self.assertEqual({e['type'] for e in gm['effects']}, {'SetStage', 'SetGlobal', 'DisableRef'})
        self.assertTrue(all(e['conditional'] for e in gm['effects']))
        self.assertEqual([u['fn'] for u in gm['unmodeled']], ['SetQuestVar'])   # `set fTimer` = quest-script local var write
        rows = blocks.summarise(out)
        self.assertEqual(rows[0][2], {'blockable', 'unblockable'})
        self.assertEqual(rows[1][2], {'blockable', 'unlisted'})              # AddScriptPackage is outside L5's list
        self.assertEqual(rows[2][2], set())                                  # ShowMessage is UI only

    def test_object_script_local_vars_are_not_quest_vars(self):
        out = blocks.analyse_scripts([scpt('P.esm:000011', 'ObjScript', 'Begin GameMode\nset fTimer to 1\nEnd',
                                           typ='object')], set(), REFINFO, GLOBALS)
        self.assertEqual(out, [])

    def test_report_sections_and_ondeath(self):
        out = blocks.analyse_scripts([scpt('P.esm:000010', 'MyQuestScript', SRC)], {'P.esm:000010'}, REFINFO, GLOBALS)
        md = blocks.report_md(out, 1)
        for h in ('By block type', 'Cannot be blocked by L5', 'OnDeath blocks touching quests'):
            self.assertIn(h, md)
        self.assertIn('| gamemode | 1 | 1 | 1 |', md)
        self.assertIn('**1** touch quest state', md)

    def test_mutation_without_local_var_detection_is_caught(self):
        class NoLocals(effects.Ctx):
            def __init__(self, names, globs, local_vars=None):
                super().__init__(names, globs, None)
        with mock.patch.object(effects, 'Ctx', NoLocals):
            out = blocks.analyse_scripts([scpt('P.esm:000010', 'MyQuestScript', SRC)], {'P.esm:000010'}, REFINFO, GLOBALS)
        with self.assertRaises(AssertionError):
            self.assertEqual([u['fn'] for u in out[0]['unmodeled']], ['SetQuestVar'])

    def test_mutation_blockable_set_changes_classification(self):
        out = blocks.analyse_scripts([scpt('P.esm:000010', 'MyQuestScript', SRC)], {'P.esm:000010'}, REFINFO, GLOBALS)
        with mock.patch.object(blocks, 'L5_BLOCKABLE', set()):
            rows = blocks.summarise(out)
        with self.assertRaises(AssertionError):
            self.assertIn('blockable', rows[0][2])


if __name__ == '__main__':
    unittest.main()
