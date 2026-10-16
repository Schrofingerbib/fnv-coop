#!/usr/bin/env python3
"""NV:MP compatibility check: re-run every signature (patch table + server hook sites) against a client.dll and a
nvmp_storyserver.exe and report which patches still resolve. Independent Python implementation, cross-checks the C++ scanner.

usage: python -I verify_sigs.py [--client PATH] [--server PATH] [--json] [--quiet]
       (legacy: positional paths are matched by file name)

Binaries are pinned by SHA-256 in plugin/nvmp/pinned_builds.json:
  * pinned build (9.4): every signature must be unique, the site bytes must equal `expected` AND the site RVA must equal
    the table's rva94. Any deviation is a failure.
  * other build (an NV:MP update): same checks, but RVA drift is only informational (MOVED) - what matters is whether each
    patch still resolves uniquely with the expected original bytes. The summary says which patches would be applied.
Exit code: 0 = everything resolves, 1 = something does not (the message names the patches), 2 = usage/file error.
"""
import argparse, hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pe_scan import scan, sections  # noqa: E402

NVMP_RE = os.path.normpath(os.path.join(HERE, '..', '..', '..', 'third_party', 'nvmp'))
DEFAULT = {'client.dll': os.path.join(NVMP_RE, 'nvmp', 'client.dll'),
           'nvmp_storyserver.exe': os.path.join(NVMP_RE, 'nvmp_storyserver.exe')}
PIN_FILE = os.path.join(HERE, '..', 'pinned_builds.json')
REQUIRED = {'client.quested_not_localized', 'hook:encounter handler'}   # the recommended first patch + the server hook's core site


def parse_table(src):
    out = []
    for blk in src.split('PatchDef d;')[1:]:
        g = lambda k: re.search(r'd\.%s\s*=\s*(.*?);' % k, blk, re.S).group(1)
        lits = lambda e: ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', e))
        out.append(dict(
            name=lits(g('name')), module=lits(g('module')), sig=lits(g('signature')),
            offset=int(g('offset')), expected=[int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]+)', g('expected'))],
            rva=int(re.search(r'0x([0-9A-Fa-f]+)', g('rva94')).group(1), 16),
            default_on='defaultOn = true' in blk))
    return out


def hook_sites():
    """server_hook/sites.h: name, signature, copyLen, strImm, strPrefix."""
    src = open(os.path.join(HERE, '..', 'server_hook', 'sites.h')).read()
    out = []
    for m in re.finditer(r'inline const Site (\w+) = \{(.*?)\};', src, re.S):
        body = m.group(2)
        lits = re.findall(r'"([^"]*)"', body)
        sig = ' '.join(l for l in lits[1:] if re.fullmatch(r'[0-9A-F? ]+', l.strip()))
        nums = re.search(r',\s*(\d+)\s*,\s*(\d+)\s*,\s*(nullptr|"[^"]*")', body)
        prefix = None if nums.group(3) == 'nullptr' else nums.group(3).strip('"')
        out.append(dict(name=lits[0], sig=sig, copy=int(nums.group(1)), str_imm=int(nums.group(2)), prefix=prefix))
    return out


def file_bytes_at_rva(data, secs, rva, n):
    for name, va, vs, ro, rs, ch in secs:
        if va <= rva < va + rs:
            return list(data[ro + rva - va: ro + rva - va + n])
    return None


def check_build(paths, pins):
    """Returns (report dict). Pure function so the tests can call it on mutated copies."""
    rep = {'binaries': {}, 'patches': [], 'hooks': [], 'ok': True}
    blobs, secsmap, bases = {}, {}, {}
    for mod, path in paths.items():
        data = open(path, 'rb').read()
        sha = hashlib.sha256(data).hexdigest()
        version = next((v for v, m in pins.items() if m.get(mod) == sha), None)
        rep['binaries'][mod] = {'path': path, 'sha256': sha, 'pinned_version': version}
        blobs[mod] = data
        base, secs = sections(data)
        secsmap[mod], bases[mod] = secs, base
    pinned = {m: rep['binaries'][m]['pinned_version'] for m in blobs}
    table = parse_table(open(os.path.join(HERE, '..', 'patches.cpp')).read())
    for p in table:
        mod = p['module']
        data = blobs[mod]
        _, hits = scan(data, p['sig'])
        r = {'name': p['name'], 'module': mod, 'default_on': p['default_on'], 'matches': len(hits), 'status': None, 'detail': ''}
        if len(hits) != 1:
            r['status'] = 'NOT FOUND' if not hits else 'AMBIGUOUS'
        else:
            site = hits[0] + p['offset']
            found = file_bytes_at_rva(data, secsmap[mod], site, len(p['expected']))
            r['rva'] = site
            if found != p['expected']:
                r['status'] = 'BYTES DIFFER'
                r['detail'] = 'found %s expected %s' % (' '.join('%02X' % b for b in found or []), ' '.join('%02X' % b for b in p['expected']))
            elif site != p['rva']:
                r['status'] = 'MOVED' if not pinned[mod] else 'RVA MISMATCH'
                r['detail'] = '0x%X -> 0x%X (%+d)' % (p['rva'], site, site - p['rva'])
            else:
                r['status'] = 'OK'
        r['resolves'] = r['status'] in ('OK', 'MOVED')
        if not r['resolves']:
            rep['ok'] = False
        rep['patches'].append(r)
    srv = blobs.get('nvmp_storyserver.exe')
    for s in hook_sites():
        _, hits = scan(srv, s['sig'])
        want_twins = s['prefix'] is not None
        chosen = None
        if want_twins:   # structural twins: pick the one whose pushed string matches
            good = []
            for h in hits:
                imm = file_bytes_at_rva(srv, secsmap['nvmp_storyserver.exe'], h + s['str_imm'], 4)
                if not imm:
                    continue
                va = int.from_bytes(bytes(imm), 'little')
                text = file_bytes_at_rva(srv, secsmap['nvmp_storyserver.exe'], va - bases['nvmp_storyserver.exe'], len(s['prefix']))
                if text is not None and bytes(text).decode('latin1') == s['prefix']:
                    good.append(h)
            ok = len(good) == 1
            chosen = good[0] if good else None
            detail = '%d candidates, %d match string %r' % (len(hits), len(good), s['prefix'])
        else:
            ok = len(hits) == 1
            chosen = hits[0] if hits else None
            detail = '%d matches' % len(hits)
        r = {'name': 'hook:' + s['name'], 'module': 'nvmp_storyserver.exe', 'matches': len(hits), 'status': 'OK' if ok else ('NOT FOUND' if not hits else 'AMBIGUOUS'),
             'detail': detail, 'resolves': ok, 'rva': chosen}
        if not ok:
            rep['ok'] = False
        rep['hooks'].append(r)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--client')
    ap.add_argument('--server')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('legacy', nargs='*')
    a = ap.parse_args(argv)
    paths = dict(DEFAULT)
    if a.client: paths['client.dll'] = a.client
    if a.server: paths['nvmp_storyserver.exe'] = a.server
    for p in a.legacy:
        paths[os.path.basename(p)] = p
    for m, p in paths.items():
        if not os.path.exists(p):
            print('missing %s: %s' % (m, p), file=sys.stderr)
            return 2
    pins = json.load(open(PIN_FILE))
    rep = check_build(paths, pins)
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0 if rep['ok'] else 1
    for mod, b in rep['binaries'].items():
        tag = ('PINNED build %s' % b['pinned_version']) if b['pinned_version'] else 'UNPINNED build (an NV:MP update?)'
        print('%-22s sha256=%s...  %s' % (mod, b['sha256'][:16], tag))
    for r in rep['patches'] + rep['hooks']:
        flag = 'OK ' if r['status'] == 'OK' else ('~~ ' if r['resolves'] else 'BAD')
        if a.quiet and flag == 'OK ':
            continue
        extra = '  rva=0x%X' % r['rva'] if r.get('rva') is not None else ''
        print('%s %-36s %-12s%s %s' % (flag, r['name'], r['status'], extra, r['detail']))
    bad = [r['name'] for r in rep['patches'] + rep['hooks'] if not r['resolves']]
    req_bad = [n for n in bad if n in REQUIRED]
    if not bad:
        moved = [r['name'] for r in rep['patches'] if r['status'] == 'MOVED']
        print('all signatures resolve uniquely' + ('; %d moved (informational)' % len(moved) if moved else ''))
        return 0
    print('%d NOT RESOLVING: %s' % (len(bad), ', '.join(bad)))
    if req_bad:
        print('REQUIRED sites broken: %s -> do not apply patches/hook on this build' % ', '.join(req_bad))
    still = [r['name'] for r in rep['patches'] if r['resolves']]
    print('would still apply: %s' % (', '.join(still) or 'nothing'))
    return 1


if __name__ == '__main__':
    sys.exit(main())
