"""PNG encoder used by the screenshot method: decode with zlib + check CRCs; mutant must be rejected."""
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import binaries  # noqa: E402
SIG = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"


def decode(data):
    assert data[:8] == SIG, "bad signature"
    pos, idat, hdr = 8, b"", None
    while pos < len(data):
        n, = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        crc, = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])
        assert zlib.crc32(typ + body) == crc, f"bad CRC in {typ}"
        if typ == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)  # raises on a bad adler32
    w, h = hdr[0], hdr[1]
    rows = [raw[y * (w * 3 + 1) + 1:(y + 1) * (w * 3 + 1)] for y in range(h)]
    return w, h, rows


def run(exe):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.png"
        subprocess.run([str(exe), str(p)], check=True, timeout=20)
        return decode(p.read_bytes())


class Png(unittest.TestCase):
    def test_roundtrip(self):
        w, h, rows = run(binaries.get("nvbridge_pngtest"))
        self.assertEqual((w, h), (3, 2))
        self.assertEqual(rows[0], bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]))
        self.assertEqual(rows[1], bytes([10, 20, 30, 40, 50, 60, 250, 251, 252]))

    def test_mutant_rejected(self):
        with self.assertRaises(Exception):
            run(binaries.get("nvbridge_pngtest_mutant"))


if __name__ == "__main__":
    unittest.main()
