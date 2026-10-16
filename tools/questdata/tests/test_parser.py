import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synth  # noqa: E402
from esm import EsmFile, ParseError, split_subrecords  # noqa: E402
import extract  # noqa: E402


class FramingTests(unittest.TestCase):
    def test_header_masters_and_counts(self):
        e = EsmFile(synth.synth_file())
        self.assertEqual(e.masters, ['Master.esm'])
        recs, grps = e.stats()
        self.assertEqual((recs, grps), (3, 3))
        self.assertEqual(recs + grps, e.record_count)

    def test_records_filter_and_compressed(self):
        e = EsmFile(synth.synth_file())
        self.assertEqual([r.sig for r in e.records()], ['QUST', 'DIAL', 'INFO'])
        q = next(e.records({'QUST'}))
        self.assertTrue(q.flags & 0x40000)
        self.assertEqual(dict(q.subrecords()[:1])['EDID'], b'TESTQ\0')

    def test_info_parent_dial(self):
        e = EsmFile(synth.synth_file())
        info = next(e.records({'INFO'}))
        self.assertEqual(info.parent, 0x01002000)

    def test_xxxx_large_subrecord(self):
        big = b'A' * 70000
        subs = split_subrecords(synth.sub('EDID', b'x\0') + synth.sub_big('DATA', big) + synth.sub('FULL', b'y\0'))
        self.assertEqual([s for s, _ in subs], ['EDID', 'DATA', 'FULL'])
        self.assertEqual(len(subs[1][1]), 70000)

    def test_formid_resolution(self):
        e = EsmFile(synth.synth_file(masters=('A.esm', 'B.esm')))
        ref = e.resolver()
        self.assertEqual(ref(0x01000042), 'B.esm:000042')
        self.assertEqual(ref(0x02000042), '<mem>:000042')
        self.assertIsNone(ref(0))

    # ---- mutation twins: each corruption must be rejected ----
    def test_mutation_subrecord_size_overrun_raises(self):
        data = bytearray(synth.sub('EDID', b'abc\0') + synth.sub('FULL', b'xy\0'))
        struct.pack_into('<H', data, 4, 200)  # EDID claims 200 bytes
        with self.assertRaises(ParseError):
            split_subrecords(bytes(data))

    def test_mutation_group_size_raises(self):
        b = bytearray(synth.synth_file())
        e0 = EsmFile(bytes(b))
        _, s, _ = next(e0.top_groups())
        struct.pack_into('<I', b, s + 4, 10_000_000)
        with self.assertRaises(ParseError):
            list(EsmFile(bytes(b)).records())

    def test_mutation_record_size_raises(self):
        b = bytearray(synth.synth_file())
        e0 = EsmFile(bytes(b))
        _, s, _ = next(e0.top_groups())
        struct.pack_into('<I', b, s + 24 + 4, 9_999_999)  # first record's data size
        with self.assertRaises(ParseError):
            list(EsmFile(bytes(b)).records())

    def test_mutation_bad_zlib_raises(self):
        b = bytearray(synth.synth_file())
        e0 = EsmFile(bytes(b))
        q = next(e0.records({'QUST'}))
        b[q._off + 8] ^= 0xFF
        q2 = next(EsmFile(bytes(b)).records({'QUST'}))
        with self.assertRaises(ParseError):
            q2.data()


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.e = EsmFile(synth.synth_file())
        self.ref = self.e.resolver()

    def test_quest(self):
        q = extract.parse_qust(next(self.e.records({'QUST'})), self.ref)
        self.assertEqual(q['edid'], 'TESTQ')
        self.assertEqual(q['name'], 'Test Quest')
        self.assertEqual(q['flags'], ['start_enabled'])
        self.assertEqual(q['priority'], 50)
        self.assertEqual([s['index'] for s in q['stages']], [10, 20])
        s10 = q['stages'][0]['entries'][0]
        self.assertEqual(s10['text'], 'Stage ten')
        self.assertIn('player.AddItem Caps001 100', s10['script']['source'])
        self.assertEqual(q['stages'][1]['entries'][0]['flags'], ['complete_quest'])
        self.assertEqual(q['objectives'][0]['index'], 10)
        self.assertEqual(q['objectives'][0]['text'], 'Do the thing')
        self.assertEqual(q['objectives'][0]['targets'][0]['ref'], 'Master.esm:002000')
        # quest-level CTDA vs objective-target CTDA stay separate
        self.assertEqual(len(q['conditions']), 1)
        self.assertEqual(q['conditions'][0]['func'], 'GetStage')
        self.assertEqual(q['conditions'][0]['op'], '>')
        self.assertEqual(len(q['objectives'][0]['targets'][0]['conditions']), 1)
        self.assertEqual(q['objectives'][0]['targets'][0]['conditions'][0]['func'], 'GetStageDone')

    def test_info_begin_end_split(self):
        f = extract.parse_info(next(self.e.records({'INFO'})), self.ref)
        self.assertEqual(f['quest'], '<mem>:001000')
        self.assertEqual(f['topic'], '<mem>:002000')
        self.assertEqual(f['responses'][0]['text'], 'Hello courier')
        self.assertIn('AddItem', f['begin']['source'])
        self.assertIn('SetStage', f['end']['source'])
        self.assertIn('DisablePlayerControls', f['end']['source'])
        self.assertEqual(f['flags'], ['goodbye'])
        self.assertEqual(len(f['conditions']), 1)

    def test_extract_plugin_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'Synth.esm')
            open(p, 'wb').write(synth.synth_file())
            name, masters, out, stats = extract.extract_plugin(p)
        self.assertEqual((len(out['QUST']), len(out['INFO']), len(out['DIAL'])), (1, 1, 1))
        self.assertEqual(extract.script_counts(out)['info_scripts'], 2)
        self.assertEqual(stats['QUST']['new'], 1)

    def test_mutation_corrupt_quest_subrecord_fails_extract(self):
        b = bytearray(synth.synth_file())
        # make the compressed QUST uncompressed & corrupt: rebuild with bad EDID size
        bad = synth.rec('QUST', 0x01001000, [synth.sub('EDID', b'abc\0')])
        bad = bytearray(bad)
        struct.pack_into('<H', bad, 24 + 4, 500)
        body = synth.grup('QUST', [bytes(bad)])
        e = EsmFile(synth.tes4(('Master.esm',), 2) + body)
        with self.assertRaises(ParseError):
            extract.parse_qust(next(e.records({'QUST'})), e.resolver())

    def _one(self, sig, subs, formid=0x01005000):
        e = EsmFile(synth.tes4(('Master.esm',), 2) + synth.grup(sig, [synth.rec(sig, formid, subs)]))
        return next(e.records({sig})), e.resolver()

    def test_term_menu_items(self):
        r, ref = self._one('TERM', [synth.sub('EDID', synth.z('TestTerm')), synth.sub('FULL', synth.z('T')),
                                    synth.sub('ITXT', synth.z('Open door')), synth.sub('RNAM', synth.z('Opened')),
                                    synth.sub('ANAM', b'\0'), *synth.script('SetStage TESTQ 20'),
                                    synth.sub('ITXT', synth.z('Back')), synth.sub('RNAM', synth.z('')),
                                    synth.sub('TNAM', struct.pack('<I', 0x01005001))])
        t = extract.parse_term(r, ref)
        self.assertEqual([i['text'] for i in t['items']], ['Open door', 'Back'])
        self.assertIn('SetStage', t['items'][0]['script']['source'])
        self.assertIsNone(t['items'][1]['script'])
        self.assertEqual(t['items'][1]['submenu'], '<mem>:005001')

    def test_pack_scripts(self):
        r, ref = self._one('PACK', [synth.sub('EDID', synth.z('TestPack')), synth.sub('POBA'),
                                    *synth.script('player.AddItem x 1'), synth.sub('POEA'),
                                    *synth.script('SetStage Q 5'), synth.sub('POCA')])
        p = extract.parse_pack(r, ref)
        self.assertEqual(sorted(p['scripts']), ['begin', 'end'])
        self.assertIn('SetStage', p['scripts']['end']['source'])

    def test_forms_lookup_resolves_placed_ref_base_and_script(self):
        import tempfile
        import forms
        refr = synth.rec('ACHR', 0x01000010, [synth.sub('NAME', struct.pack('<I', 0x01000020))])
        npc = synth.rec('NPC_', 0x01000020, [synth.sub('EDID', synth.z('Bob')), synth.sub('FULL', synth.z('Bob B')),
                                             synth.sub('SCRI', struct.pack('<I', 0x01000030))])
        data = synth.tes4(('Master.esm',), 4) + synth.grup('NPC_', [npc]) + synth.grup('CELL', [refr])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'S.esm')
            open(p, 'wb').write(data)
            out = forms.lookup([p], {'S.esm:000010', 'S.esm:000020', 'S.esm:999999'})
        self.assertEqual(out['S.esm:000010']['base'], 'S.esm:000020')
        self.assertEqual((out['S.esm:000020']['edid'], out['S.esm:000020']['name'], out['S.esm:000020']['script']),
                         ('Bob', 'Bob B', 'S.esm:000030'))
        self.assertNotIn('S.esm:999999', out)

    def test_ctda_decoding(self):
        c = extract.parse_ctda(synth.ctda(59, 0x01001000, 10, op=1, value=1.0, flags=1, run_on=2, ref=0x14)[6:], lambda x: f'R:{x:x}')
        self.assertEqual((c['func'], c['op'], c['or'], c['run_on']), ('GetStageDone', '!=', True, 'reference'))
        self.assertEqual((c['p1'], c['p2'], c['ref']), ('R:1001000', 10, 'R:14'))


if __name__ == '__main__':
    unittest.main()
