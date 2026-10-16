"""Streaming, read-only parser for Bethesda TES4-family master files (Fallout: New Vegas).

Format per xEdit wbDefinitionsFNV.pas: 24-byte record/GRUP headers, subrecords with u16
size (XXXX override for oversized), zlib-compressed records (flag 0x00040000).
Works over any bytes-like object (mmap for real files, bytes for tests).
Strict: any framing inconsistency raises ParseError.
"""
import mmap
import struct
import zlib

COMPRESSED = 0x00040000
DELETED = 0x00000020


class ParseError(Exception):
    pass


class Record:
    __slots__ = ('sig', 'flags', 'formid', 'version', 'parent', '_buf', '_off', '_size')

    def __init__(self, sig, flags, formid, version, parent, buf, off, size):
        self.sig, self.flags, self.formid, self.version = sig, flags, formid, version
        self.parent = parent  # label (formid) of enclosing topic-children GRUP (type 7), else None
        self._buf, self._off, self._size = buf, off, size

    @property
    def deleted(self):
        return bool(self.flags & DELETED)

    def data(self):
        raw = bytes(self._buf[self._off:self._off + self._size])
        if self.flags & COMPRESSED:
            if len(raw) < 4:
                raise ParseError(f'{self.sig} {self.formid:08X}: short compressed record')
            want = struct.unpack_from('<I', raw)[0]
            try:
                raw = zlib.decompress(raw[4:])
            except zlib.error as e:
                raise ParseError(f'{self.sig} {self.formid:08X}: bad zlib: {e}')
            if len(raw) != want:
                raise ParseError(f'{self.sig} {self.formid:08X}: decompressed {len(raw)} != {want}')
        return raw

    def subrecords(self):
        return split_subrecords(self.data(), f'{self.sig} {self.formid:08X}')


def split_subrecords(d, what='record'):
    out = []
    o, n, big = 0, len(d), None
    while o < n:
        if o + 6 > n:
            raise ParseError(f'{what}: truncated subrecord header at {o}')
        sig = d[o:o + 4].decode('latin1')
        sz = struct.unpack_from('<H', d, o + 4)[0]
        o += 6
        if sig == 'XXXX':
            if sz != 4 or o + 4 > n:
                raise ParseError(f'{what}: bad XXXX')
            big = struct.unpack_from('<I', d, o)[0]
            o += 4
            continue
        if big is not None:
            sz, big = big, None
        if o + sz > n:
            raise ParseError(f'{what}: subrecord {sig} size {sz} overruns record ({n - o} left)')
        out.append((sig, d[o:o + sz]))
        o += sz
    if big is not None:
        raise ParseError(f'{what}: dangling XXXX')
    return out


class EsmFile:
    def __init__(self, buf, name='<mem>'):
        self.buf, self.name = buf, name
        n = len(buf)
        if n < 24 or bytes(buf[0:4]) != b'TES4':
            raise ParseError(f'{name}: no TES4 header')
        hsize, hflags, _, _, _, _ = struct.unpack_from('<IIIIHH', buf, 4)
        self._hdr_end = 24 + hsize
        if self._hdr_end > n:
            raise ParseError(f'{name}: TES4 overruns file')
        subs = split_subrecords(bytes(buf[24:self._hdr_end]), 'TES4')
        self.masters = [d.split(b'\0')[0].decode('cp1252') for s, d in subs if s == 'MAST']
        self.is_master = bool(hflags & 1)
        self.record_count = next((struct.unpack_from('<I', d, 4)[0] for s, d in subs if s == 'HEDR'), None)

    @classmethod
    def open(cls, path):
        f = open(path, 'rb')
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        e = cls(mm, path.replace('\\', '/').rsplit('/', 1)[-1])
        e._f = f
        return e

    def close(self):
        if hasattr(self.buf, 'close'):
            self.buf.close()
        if getattr(self, '_f', None):
            self._f.close()

    def top_groups(self):
        """Yield (label, start, end) of each top-level GRUP (label = record signature)."""
        buf, o, n = self.buf, self._hdr_end, len(self.buf)
        while o < n:
            if o + 24 > n or bytes(buf[o:o + 4]) != b'GRUP':
                raise ParseError(f'{self.name}: expected GRUP at {o}')
            size = struct.unpack_from('<I', buf, o + 4)[0]
            label = bytes(buf[o + 8:o + 12]).decode('latin1')
            if size < 24 or o + size > n:
                raise ParseError(f'{self.name}: GRUP {label} size {size} invalid at {o}')
            yield label, o, o + size
            o += size

    def records(self, sigs=None, groups=None):
        """Yield Records whose sig is in `sigs` (None = all) from top-level groups in `groups`
        (None = all). Nested groups are descended."""
        for label, s, e in self.top_groups():
            if groups is not None and label not in groups:
                continue
            yield from self._walk(s + 24, e, sigs, None)

    def _walk(self, o, end, sigs, parent):
        buf = self.buf
        while o < end:
            if o + 24 > end:
                raise ParseError(f'{self.name}: truncated header at {o}')
            sig = bytes(buf[o:o + 4])
            size = struct.unpack_from('<I', buf, o + 4)[0]
            if sig == b'GRUP':
                if size < 24 or o + size > end:
                    raise ParseError(f'{self.name}: nested GRUP size {size} invalid at {o}')
                gtype = struct.unpack_from('<I', buf, o + 12)[0]
                lab = struct.unpack_from('<I', buf, o + 8)[0]
                yield from self._walk(o + 24, o + size, sigs, lab if gtype == 7 else parent)
                o += size
                continue
            if o + 24 + size > end:
                raise ParseError(f'{self.name}: record {sig!r} size {size} overruns group at {o}')
            s = sig.decode('latin1')
            if sigs is None or s in sigs:
                flags, formid, _, ver, _ = struct.unpack_from('<IIIHH', buf, o + 8)
                yield Record(s, flags, formid, ver, parent, buf, o + 24, size)
            o += 24 + size

    def stats(self):
        """(records, groups) excluding TES4; records+groups equals the TES4 HEDR record count."""
        recs = grps = 0

        def walk(o, end):
            nonlocal recs, grps
            buf = self.buf
            while o < end:
                if o + 24 > end:
                    raise ParseError(f'{self.name}: truncated header at {o}')
                size = struct.unpack_from('<I', buf, o + 4)[0]
                if bytes(buf[o:o + 4]) == b'GRUP':
                    if size < 24 or o + size > end:
                        raise ParseError(f'{self.name}: GRUP size {size} invalid at {o}')
                    grps += 1
                    walk(o + 24, o + size)
                    o += size
                else:
                    if o + 24 + size > end:
                        raise ParseError(f'{self.name}: record overruns at {o}')
                    recs += 1
                    o += 24 + size
        walk(self._hdr_end, len(self.buf))
        return recs, grps

    def resolver(self):
        masters, me = self.masters, self.name

        def ref(fid):
            if fid == 0:
                return None
            i = fid >> 24
            return f'{masters[i] if i < len(masters) else me}:{fid & 0xFFFFFF:06X}'
        return ref
