"""Reader for the FNV / FO3 TES4 plugin format (records, GRUPs, subrecords).

Header sizes (FNV, form version 15): a record header is 24 bytes (type, dataSize, flags, formId, vcInfo,
formVersion u16, unknown u16); a GRUP header is 24 bytes (b'GRUP', groupSize including the header, label,
groupType, stamp u16 + unknown u16, version u16 + unknown u16). Compressed records (flag 0x00040000) carry a u32
decompressed size followed by a zlib stream. Subrecords are (type, u16 size, data); a preceding XXXX subrecord
gives the real u32 size of the one that follows.

stdlib only. Everything is read from a bytes-like buffer (a file read, or an mmap for the 245 MB FalloutNV.esm).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

FLAG_ESM = 0x00000001
FLAG_DELETED = 0x00000020
FLAG_PERSISTENT = 0x00000400   # on REFR/ACHR/ACRE; on a CELL it is "interior with no persistent-only grouping" (unused)
FLAG_INITIALLY_DISABLED = 0x00000800
FLAG_COMPRESSED = 0x00040000

# GRUP types
G_TOP, G_WORLD_CHILDREN, G_INT_BLOCK, G_INT_SUBBLOCK, G_EXT_BLOCK, G_EXT_SUBBLOCK = 0, 1, 2, 3, 4, 5
G_CELL_CHILDREN, G_TOPIC_CHILDREN, G_CELL_PERSISTENT, G_CELL_TEMPORARY, G_CELL_VWD = 6, 7, 8, 9, 10


@dataclass
class Sub:
    type: str
    data: bytes


@dataclass
class Record:
    type: str
    flags: int
    form_id: int
    vc_info: int = 0
    version: int = 15
    unknown: int = 0
    subs: List[Sub] = field(default_factory=list)
    offset: int = -1

    def get(self, t: str) -> Optional[bytes]:
        for s in self.subs:
            if s.type == t:
                return s.data
        return None

    def get_all(self, t: str) -> List[bytes]:
        return [s.data for s in self.subs if s.type == t]

    def edid(self) -> str:
        d = self.get("EDID")
        return d.split(b"\0", 1)[0].decode("latin-1") if d else ""


@dataclass
class Group:
    label: bytes
    gtype: int
    stamp: int = 0
    unknown: int = 0
    version: int = 0
    unknown2: int = 0
    children: list = field(default_factory=list)   # Record | Group
    offset: int = -1

    def label_str(self) -> str:
        return self.label.decode("latin-1") if self.gtype == G_TOP else self.label.hex()


def parse_subs(data: bytes) -> List[Sub]:
    subs: List[Sub] = []
    i, n = 0, len(data)
    big = None
    while i + 6 <= n:
        t = data[i:i + 4].decode("latin-1")
        size = struct.unpack_from("<H", data, i + 4)[0]
        i += 6
        if t == "XXXX":
            big = struct.unpack_from("<I", data, i)[0]
            i += size
            continue
        if big is not None:
            size, big = big, None
        subs.append(Sub(t, bytes(data[i:i + size])))
        i += size
    if i != n:
        raise ValueError("subrecord stream does not end on a boundary (%d of %d)" % (i, n))
    return subs


def encode_subs(subs: List[Sub]) -> bytes:
    out = bytearray()
    for s in subs:
        if len(s.data) > 0xFFFF:
            out += b"XXXX" + struct.pack("<HI", 4, len(s.data)) + s.type.encode("latin-1") + struct.pack("<H", 0)
        else:
            out += s.type.encode("latin-1") + struct.pack("<H", len(s.data))
        out += s.data
    return bytes(out)


def read_record_at(buf, pos: int, decode: bool = True) -> Tuple[Record, int]:
    """Parse the record whose header starts at pos. Returns (record, position after it)."""
    typ = bytes(buf[pos:pos + 4]).decode("latin-1")
    size, flags, fid, vc, ver, unk = struct.unpack_from("<IIIIHH", buf, pos + 4)
    body = bytes(buf[pos + 24:pos + 24 + size])
    rec = Record(typ, flags, fid, vc, ver, unk, offset=pos)
    if decode:
        if flags & FLAG_COMPRESSED:
            (usize,) = struct.unpack_from("<I", body, 0)
            body = zlib.decompress(body[4:])
            if len(body) != usize:
                raise ValueError("compressed record %08X: size %d != %d" % (fid, len(body), usize))
        rec.subs = parse_subs(body)
    return rec, pos + 24 + size


def read_group_header(buf, pos: int) -> Tuple[Group, int]:
    """Returns (empty group, end position of the whole group)."""
    magic = bytes(buf[pos:pos + 4])
    if magic != b"GRUP":
        raise ValueError("not a GRUP at %d: %r" % (pos, magic))
    size, label, gtype, stamp, unk, ver, unk2 = struct.unpack_from("<I4sIHHHH", buf, pos + 4)
    return Group(label, gtype, stamp, unk, ver, unk2, offset=pos), pos + size


def iter_children(buf, start: int, end: int) -> Iterator[Tuple[str, int, int]]:
    """Cheap scan of one level: yields ('GRUP'|record type, pos, end) without decoding bodies."""
    pos = start
    while pos < end:
        magic = bytes(buf[pos:pos + 4])
        size = struct.unpack_from("<I", buf, pos + 4)[0]
        if magic == b"GRUP":
            yield "GRUP", pos, pos + size
            pos += size
        else:
            yield magic.decode("latin-1"), pos, pos + 24 + size
            pos += 24 + size


def read_tree(buf, start: int, end: int, decode: bool = True) -> list:
    out = []
    for kind, p, e in iter_children(buf, start, end):
        if kind == "GRUP":
            g, _ = read_group_header(buf, p)
            g.children = read_tree(buf, p + 24, e, decode)
            out.append(g)
        else:
            out.append(read_record_at(buf, p, decode)[0])
    return out


@dataclass
class Plugin:
    header: Record
    top: list   # top-level Group list

    @property
    def masters(self) -> List[str]:
        return [d.split(b"\0", 1)[0].decode("latin-1") for d in self.header.get_all("MAST")]

    def top_group(self, label: str) -> Optional[Group]:
        for g in self.top:
            if isinstance(g, Group) and g.label == label.encode("latin-1"):
                return g
        return None


def read_plugin(buf) -> Plugin:
    hdr, pos = read_record_at(buf, 0)
    if hdr.type != "TES4":
        raise ValueError("not a TES4 plugin (first record %r)" % hdr.type)
    return Plugin(hdr, read_tree(buf, pos, len(buf)))


# ---- encoding ---------------------------------------------------------------------------------------------

def encode_record(r: Record) -> bytes:
    body = encode_subs(r.subs)
    flags = r.flags
    if flags & FLAG_COMPRESSED:
        body = struct.pack("<I", len(body)) + zlib.compress(body)
    return (r.type.encode("latin-1") + struct.pack("<IIIIHH", len(body), flags, r.form_id, r.vc_info, r.version, r.unknown) + body)


def encode_group(g: Group) -> bytes:
    body = b"".join(encode_group(c) if isinstance(c, Group) else encode_record(c) for c in g.children)
    return (b"GRUP" + struct.pack("<I4sIHHHH", 24 + len(body), g.label, g.gtype, g.stamp, g.unknown, g.version, g.unknown2) + body)


def encode_plugin(p: Plugin) -> bytes:
    return encode_record(p.header) + b"".join(encode_group(g) if isinstance(g, Group) else encode_record(g) for g in p.top)
