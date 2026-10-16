#!/usr/bin/env python3
"""IDA-style pattern scanner over a PE's executable sections (mirror of sigscan.cpp).
usage: pe_scan.py <pe> "<pattern>" [--ctx N]   (prints RVA of every match)"""
import struct, sys

def sections(data):
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    base = struct.unpack_from('<I', data, pe + 24 + 28)[0]
    off = pe + 24 + opt
    out = []
    for i in range(nsec):
        name, vs, va, rs, ro, *_r, ch = struct.unpack_from('<8sIIIIIIHHI', data, off + 40 * i)
        out.append((name.rstrip(b'\0').decode(), va, vs, ro, rs, ch))
    return base, out

def parse(pat):
    return [None if t in ('?', '??') else int(t, 16) for t in pat.split()]

def scan(data, pat, only_exec=True):
    base, secs = sections(data)
    toks = parse(pat)
    hits = []
    for name, va, vs, ro, rs, ch in secs:
        if only_exec and not (ch & 0x20000000):
            continue
        buf = data[ro:ro + min(rs, vs)]
        # build regex
        import re
        rx = b''.join(re.escape(bytes([t])) if t is not None else b'.' for t in toks)
        for m in re.finditer(b'(?=' + rx + b')', buf, re.S):
            hits.append(va + m.start())
    return base, hits

if __name__ == '__main__':
    data = open(sys.argv[1], 'rb').read()
    base, hits = scan(data, sys.argv[2])
    print(f"{len(hits)} match(es):", ' '.join(f"rva=0x{h:X}(va=0x{base+h:X})" for h in hits[:20]))
