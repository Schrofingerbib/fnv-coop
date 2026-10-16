#!/usr/bin/env python3
"""Find an ASCII string in a PE and list code references (imm32 operands) to it.
usage: strxref.py <pe> "<exact string>"  [--sub]"""
import struct, sys, re
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pe_scan import sections

def main():
    data = open(sys.argv[1], 'rb').read()
    needle = sys.argv[2].encode()
    sub = '--sub' in sys.argv
    base, secs = sections(data)
    def off2va(o):
        for n, va, vs, ro, rs, ch in secs:
            if ro <= o < ro + rs: return base + va + (o - ro)
    for m in re.finditer(re.escape(needle) + (b'' if sub else b'\0'), data):
        o = m.start()
        if not sub and o > 0 and data[o-1] != 0: continue
        va = off2va(o)
        if va is None: continue
        refs = []
        pat = struct.pack('<I', va)
        for n, sva, vs, ro, rs, ch in secs:
            if not ch & 0x20000000: continue
            buf = data[ro:ro+rs]
            for r in re.finditer(re.escape(pat), buf):
                refs.append(base + sva + r.start())
        print(f"str@0x{va:X} refs: {' '.join(hex(r) for r in refs)}")
main()
