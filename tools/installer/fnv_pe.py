"""Read the file version and the machine type of a Windows program or DLL without running it or loading it.

Pure Python, works on any OS, never raises on a bad file (returns None). Used by the installer to check that the game
is FalloutNV.exe 1.4.0.525 and to read the xNVSE version, and by the packager to check that nvcoop.dll is a 32-bit DLL.
"""
import os
import struct
from typing import Optional, Tuple

import fnv_mut

Version = Tuple[int, int, int, int]

RT_VERSION = 16
MACHINE_I386 = 0x014C
_MAX_SECTIONS = 96
_MAX_RES_ENTRIES = 256
_MAX_BLOB = 1 << 16


def format_version(v: Optional[Version]) -> str:
    return "unknown" if v is None else ".".join(str(x) for x in v)


def parse_version(text: str) -> Optional[Version]:
    """'1.4.0.525' or '0, 6, 4, 9' -> (1, 4, 0, 525). None when it is not four numbers."""
    parts = [p.strip() for p in text.replace(",", ".").split(".") if p.strip()]
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


class _Pe:
    def __init__(self, f):
        self.f = f
        self.size = os.fstat(f.fileno()).st_size
        self.sections = []   # (virtual_address, virtual_size, raw_pointer, raw_size)
        self.machine = 0
        self.resource_rva = 0
        self.resource_size = 0

    def read(self, off: int, n: int) -> Optional[bytes]:
        if off < 0 or n < 0 or off + n > self.size:
            return None
        self.f.seek(off)
        b = self.f.read(n)
        return b if len(b) == n else None

    def rva_to_off(self, rva: int) -> Optional[int]:
        for va, vsize, ptr, rsize in self.sections:
            if va <= rva < va + max(vsize, rsize):
                off = ptr + (rva - va)
                return off if off < self.size else None
        return None

    def parse_headers(self) -> bool:
        dos = self.read(0, 64)
        if not dos or dos[:2] != b"MZ":
            return False
        e_lfanew = struct.unpack_from("<I", dos, 60)[0]
        sig = self.read(e_lfanew, 24)
        if not sig or sig[:4] != b"PE\0\0":
            return False
        machine, nsec, _ts, _sym, _nsym, opt_size, _chars = struct.unpack_from("<HHIIIHH", sig, 4)
        self.machine = machine
        if nsec == 0 or nsec > _MAX_SECTIONS or opt_size < 96:
            return False
        opt = self.read(e_lfanew + 24, opt_size)
        if not opt:
            return False
        magic = struct.unpack_from("<H", opt, 0)[0]
        dd_off = 96 if magic == 0x10B else 112 if magic == 0x20B else -1
        if dd_off < 0:
            return False
        if opt_size >= dd_off + 3 * 8:
            self.resource_rva, self.resource_size = struct.unpack_from("<II", opt, dd_off + 2 * 8)
        table = self.read(e_lfanew + 24 + opt_size, nsec * 40)
        if not table:
            return False
        for i in range(nsec):
            vsize, va, rsize, ptr = struct.unpack_from("<IIII", table, i * 40 + 8)
            self.sections.append((va, vsize, ptr, rsize))
        return True

    def _dir(self, base: int, off: int):
        """Entries (id_or_name, offset_field) of the resource directory at base+off; None if unreadable."""
        head = self.read(base + off, 16)
        if not head:
            return None
        nnamed, nid = struct.unpack_from("<HH", head, 12)
        n = nnamed + nid
        if n > _MAX_RES_ENTRIES:
            return None
        raw = self.read(base + off + 16, n * 8)
        if raw is None:
            return None
        return [struct.unpack_from("<II", raw, i * 8) for i in range(n)]

    def version_blob(self) -> Optional[bytes]:
        if not self.resource_rva:
            return None
        base = self.rva_to_off(self.resource_rva)
        if base is None:
            return None
        root = self._dir(base, 0)
        if not root:
            return None
        node = None
        for name, off in root:
            if name == RT_VERSION and off & 0x80000000:
                node = off & 0x7FFFFFFF
        if node is None:
            return None
        for _ in range(2):   # name level, then language level
            entries = self._dir(base, node)
            if not entries:
                return None
            _name, off = entries[0]
            if off & 0x80000000:
                node = off & 0x7FFFFFFF
            else:
                node = off
                break
        data = self.read(base + node, 16)
        if not data:
            return None
        rva, size, _cp, _res = struct.unpack("<IIII", data)
        if size == 0 or size > _MAX_BLOB:
            return None
        off = self.rva_to_off(rva)
        return None if off is None else self.read(off, size)


def _open(path):
    try:
        return open(path, "rb")
    except OSError:
        return None


def machine_type(path) -> Optional[int]:
    """IMAGE_FILE_MACHINE of a PE file (0x14C = 32-bit x86), None when the file is not a PE image."""
    f = _open(path)
    if f is None:
        return None
    with f:
        pe = _Pe(f)
        return pe.machine if pe.parse_headers() else None


def file_version(path) -> Optional[Version]:
    """The numeric FileVersion (VS_FIXEDFILEINFO) of a PE file, None when it has none or the file is not readable."""
    f = _open(path)
    if f is None:
        return None
    with f:
        try:
            pe = _Pe(f)
            if not pe.parse_headers():
                return None
            blob = pe.version_blob()
        except (OSError, struct.error):
            return None
    if not blob:
        return None
    i = blob.find(b"\xbd\x04\xef\xfe", 0, 256)   # VS_FIXEDFILEINFO signature 0xFEEF04BD
    if i < 0 or i + 16 > len(blob):
        return None
    ms, ls = struct.unpack_from("<II", blob, i + 8)
    if fnv_mut.on("pe_swap_words"):
        ms, ls = ls, ms
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
