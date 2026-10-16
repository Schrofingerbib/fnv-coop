"""Tests for the NV:MP compatibility checker (verify_sigs.py), including its mutation twins:
a binary altered the way an NV:MP update would alter it must be reported, never waved through.
Run: python -I plugin/nvmp/tools/test_compat.py   (mutated copies go under out/nvmp/tmp and are deleted)"""
import contextlib, io, os, shutil, sys, unittest, warnings
warnings.simplefilter("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify_sigs as V  # noqa: E402

ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
TMP = os.path.join(ROOT, 'out', 'nvmp', 'tmp')
HAVE = all(os.path.exists(p) for p in V.DEFAULT.values())


def mutated(src, edits, name):
    """Copy `src` to TMP with {file_offset: byte} edits applied."""
    os.makedirs(TMP, exist_ok=True)
    dst = os.path.join(TMP, name)
    data = bytearray(open(src, 'rb').read())
    for off, b in edits.items():
        data[off] = b
    open(dst, 'wb').write(data)
    return dst


def run_main(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = V.main(args)
    return rc, buf.getvalue()


def file_off(path, rva):
    from pe_scan import sections
    data = open(path, 'rb').read()
    _, secs = sections(data)
    for n, va, vs, ro, rs, ch in secs:
        if va <= rva < va + rs:
            return ro + rva - va
    raise KeyError(hex(rva))


@unittest.skipUnless(HAVE, 'NV:MP 9.4 binaries not present')
class Compat(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def test_parsers_see_every_patch_and_site(self):
        t = V.parse_table(open(os.path.join(HERE, '..', 'patches.cpp')).read())
        self.assertGreaterEqual(len(t), 8)
        self.assertIn('client.quested_not_localized', [p['name'] for p in t])
        self.assertTrue(any(p['default_on'] for p in t))
        names = [s['name'] for s in V.hook_sites()]
        self.assertEqual(names, ['encounter handler', 'hot-swap reselect', 'connection accept'])

    def test_pinned_94_resolves_everything(self):
        rep = V.check_build(V.DEFAULT, __import__('json').load(open(V.PIN_FILE)))
        self.assertTrue(rep['ok'], [r for r in rep['patches'] + rep['hooks'] if not r['resolves']])
        self.assertTrue(all(b['pinned_version'] == '9.4' for b in rep['binaries'].values()))
        self.assertTrue(all(r['status'] == 'OK' for r in rep['patches'] + rep['hooks']))
        rc, out = run_main([])
        self.assertEqual(rc, 0, out)
        self.assertIn('PINNED build 9.4', out)

    def test_changed_signature_byte_is_reported(self):          # mutation twin: update changed code near a patch
        # flip a fixed byte of the quested sig (the "6A 03" push after the log-string push)
        base = file_off(V.DEFAULT['client.dll'], 0xCCAF0 + 20)
        bad = mutated(V.DEFAULT['client.dll'], {base: 0x5A ^ 0x6A}, 'client_sigbreak.dll')
        rc, out = run_main(['--client', bad])
        self.assertEqual(rc, 1, out)
        self.assertIn('UNPINNED', out)
        self.assertIn('client.quested_not_localized', out.split('NOT RESOLVING:')[1])
        self.assertIn('REQUIRED sites broken', out)
        self.assertIn('would still apply:', out)
        self.assertNotIn('client.quested_not_localized', out.split('would still apply:')[1])
        self.assertIn('client.scripted_ref_not_localized', out.split('would still apply:')[1])   # unaffected patches still listed

    def test_site_bytes_changed_is_reported(self):               # mutation twin: signature intact, patched opcode differs
        bad = mutated(V.DEFAULT['client.dll'], {file_off(V.DEFAULT['client.dll'], 0xCCAF6): 0x75}, 'client_bytes.dll')
        rep = V.check_build({'client.dll': bad, 'nvmp_storyserver.exe': V.DEFAULT['nvmp_storyserver.exe']}, __import__('json').load(open(V.PIN_FILE)))
        r = next(x for x in rep['patches'] if x['name'] == 'client.quested_not_localized')
        self.assertEqual(r['status'], 'BYTES DIFFER')
        self.assertFalse(rep['ok'])

    def test_server_hook_site_break_is_reported(self):
        srv = V.DEFAULT['nvmp_storyserver.exe']
        bad = mutated(srv, {file_off(srv, 0x9BFD0 + 9): 0x00}, 'server_break.exe')      # inside the encounter handler signature
        rc, out = run_main(['--server', bad])
        self.assertEqual(rc, 1, out)
        self.assertIn('hook:encounter handler', out.split('NOT RESOLVING:')[1])

    def test_moved_site_is_informational_on_unpinned_but_fatal_on_pinned(self):
        pins = __import__('json').load(open(V.PIN_FILE))
        orig = V.parse_table

        def shifted(src):
            t = orig(src)
            for p in t:
                if p['name'] == 'client.scripted_ref_not_localized':
                    p['rva'] += 0x40          # pretend the table was written for another build
            return t

        V.parse_table = shifted
        try:
            rep = V.check_build(V.DEFAULT, pins)                       # pinned build: drift is a failure
            r = next(x for x in rep['patches'] if x['name'] == 'client.scripted_ref_not_localized')
            self.assertEqual(r['status'], 'RVA MISMATCH')
            self.assertFalse(rep['ok'])
            unpinned = mutated(V.DEFAULT['client.dll'], {0x10: 0x00} if False else {}, 'client_copy.dll')   # identical bytes ...
            rep2 = V.check_build({'client.dll': unpinned, 'nvmp_storyserver.exe': V.DEFAULT['nvmp_storyserver.exe']}, {})  # ... but no pins
            r2 = next(x for x in rep2['patches'] if x['name'] == 'client.scripted_ref_not_localized')
            self.assertEqual(r2['status'], 'MOVED')                    # resolves, drift only informational
            self.assertTrue(r2['resolves'] and rep2['ok'])
        finally:
            V.parse_table = orig

    def test_missing_file_is_a_usage_error(self):
        rc, _ = run_main(['--client', os.path.join(TMP, 'does_not_exist.dll')])
        self.assertEqual(rc, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
