#!/usr/bin/env python3
"""Check the engine facts of plugin/splitscreen/split_addrs.h against an exe (read only). A second, independent reader of the same facts the C++ test checks.

    python -I tools/splitscreen/re_pins.py [--exe <reference exe>]

Checks: every pinned prologue (16 bytes at its address), the two XINPUT imports (ordinals 2 and 3 at the pinned import-table slots), that the
exe does not import XInputGetCapabilities or GetForegroundWindow, and that the thunks at 0x9F996E / 0xEE820A jump through those slots.
Prints ALL OK, or one line per mismatch (exit 1). Without --exe it uses <reference exe> and exits 0 with SKIP when that file is absent.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HEADER = REPO / "plugin" / "splitscreen" / "split_addrs.h"
DEFAULT_EXE = REPO / "out" / "L5" / "re" / "fnv_reference.exe"
BASE = 0x400000


def load_pins(text: str):
    pins = []
    for m in re.finditer(r'\{"([^"]+)",\s*(0x[0-9A-Fa-f]+),\s*\{([^}]*)\}\}', text):
        data = bytes(int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]{2}", m.group(3)))
        pins.append((m.group(1), int(m.group(2), 16), data))
    return pins


def constant(text: str, name: str) -> int:
    m = re.search(name + r"\s*=\s*(0x[0-9A-Fa-f]+)(?:\s*-\s*(kImageBase|0x[0-9A-Fa-f]+))?", text)
    if not m:
        raise ValueError(f"{name} not found in split_addrs.h")
    v = int(m.group(1), 16)
    if m.group(2):
        v -= BASE if m.group(2) == "kImageBase" else int(m.group(2), 16)
    return v


class Pe:
    def __init__(self, data: bytes):
        self.d = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        n = struct.unpack_from("<H", data, pe + 6)[0]
        opt = pe + 24
        self.opt = opt
        so = opt + struct.unpack_from("<H", data, pe + 20)[0]
        self.secs = [struct.unpack_from("<8sIIII", data, so + i * 40) for i in range(n)]

    def off(self, rva: int):
        for _, vs, va, rs, rp in self.secs:
            if va <= rva < va + max(vs, rs):
                return rva - va + rp
        return None

    def read_va(self, va: int, n: int) -> bytes:
        o = self.off(va - BASE)
        return b"" if o is None else self.d[o:o + n]

    def cstr(self, off: int) -> str:
        return self.d[off:self.d.index(b"\0", off)].decode("latin-1")

    def imports(self):
        rva = struct.unpack_from("<I", self.d, self.opt + 96 + 8)[0]
        o = self.off(rva)
        out = []
        while True:
            oft, _, _, name, ft = struct.unpack_from("<IIIII", self.d, o)
            if not name:
                break
            dll = self.cstr(self.off(name))
            t, slot = self.off(oft or ft), ft
            while True:
                v = struct.unpack_from("<I", self.d, t)[0]
                if not v:
                    break
                fn = f"#{v & 0xFFFF}" if v & 0x80000000 else self.cstr(self.off(v) + 2)
                out.append((dll, fn, slot))
                t += 4
                slot += 4
            o += 20
        return out


def check(exe: Path):
    text = HEADER.read_text(encoding="utf-8")
    pe = Pe(exe.read_bytes())
    bad = []
    pins = load_pins(text)
    if len(pins) < 5:
        bad.append(f"only {len(pins)} pins parsed from split_addrs.h")
    for name, va, want in pins:
        got = pe.read_va(va, 16)
        if got != want:
            bad.append(f"pin {name} at {va:#x}: exe has {got.hex()}, header has {want.hex()}")
    imps = pe.imports()
    xi = [(d, f, s) for d, f, s in imps if d.lower().startswith("xinput")]
    get_rva, set_rva = constant(text, "XInputGetStateIatRva"), constant(text, "XInputSetStateIatRva")
    if sorted((f, s) for _, f, s in xi) != sorted([("#2", get_rva), ("#3", set_rva)]):
        bad.append(f"XINPUT imports are {[(f, hex(s)) for _, f, s in xi]}, expected #2 at {get_rva:#x} and #3 at {set_rva:#x}")
    for fn in ("XInputGetCapabilities", "GetForegroundWindow"):
        if any(f == fn for _, f, _ in imps):
            bad.append(f"the exe imports {fn}: the design notes in split_addrs.h are out of date")
    if not any(f == "GetActiveWindow" for _, f, _ in imps):
        bad.append("GetActiveWindow is not imported")
    for thunk, slot_rva in ((0x9F996E, get_rva), (0xEE820A, set_rva)):
        b = pe.read_va(thunk, 6)
        if b[:2] != b"\xff\x25" or struct.unpack_from("<I", b, 2)[0] != BASE + slot_rva:
            bad.append(f"thunk {thunk:#x} does not jump through slot {BASE + slot_rva:#x}: {b.hex()}")
    return bad, len(pins), len(imps)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", default="")
    a = ap.parse_args(argv)
    exe = Path(a.exe) if a.exe else DEFAULT_EXE
    if not exe.is_file():
        if a.exe:
            print(f"error: {exe} not found", file=sys.stderr)
            return 2
        print(f"SKIP: {exe} not found (a reference exe is a local file, see docs/tools/splitscreen.md)")
        return 0
    bad, npins, nimps = check(exe)
    for line in bad:
        print("MISMATCH " + line)
    if bad:
        return 1
    print(f"re_pins: {npins} pins and {nimps} imports checked - ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
