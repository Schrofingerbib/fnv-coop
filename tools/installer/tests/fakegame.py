"""Builders for the installer tests: a fake Windows program with a version resource, a fake New Vegas folder, a fake release.

make_pe() writes the smallest 32-bit PE file that has an RT_VERSION resource (one .rsrc section), so the real version reader
runs on realistic bytes. Nothing here needs the game.
"""
import json
import struct
from pathlib import Path
from typing import Dict, Optional, Tuple

import fnv_manifest


def _version_blob(v: Tuple[int, int, int, int]) -> bytes:
    key = "VS_VERSION_INFO".encode("utf-16le") + b"\0\0"
    head = struct.pack("<HHH", 0, 52, 0) + key
    head += b"\0" * ((-len(head)) % 4)
    fixed = struct.pack("<IIIIIIIIIIIII", 0xFEEF04BD, 0x00010000, (v[0] << 16) | v[1], (v[2] << 16) | v[3], (v[0] << 16) | v[1],
                        (v[2] << 16) | v[3], 0x3F, 0, 0x00040004, 1, 0, 0, 0)
    blob = head + fixed
    return struct.pack("<H", len(blob)) + blob[2:]


def make_pe(version: Optional[Tuple[int, int, int, int]] = (1, 4, 0, 525), machine: int = 0x14C, tail: bytes = b"") -> bytes:
    """A tiny valid PE32. version=None: no resource directory. tail: extra bytes appended (for example export names)."""
    sect_va, raw_ptr = 0x1000, 0x200
    rsrc = b""
    if version is not None:
        blob = _version_blob(version)
        data_rva = sect_va + 96
        rsrc = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 16, 0x80000000 | 24)
        rsrc += struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1, 0x80000000 | 48)
        rsrc += struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 0x409, 72)
        rsrc += struct.pack("<IIII", data_rva, len(blob), 0, 0)
        rsrc += b"\0" * (96 - len(rsrc)) + blob
    raw = rsrc + tail
    raw += b"\0" * ((-len(raw)) % 0x200 or (0x200 if not raw else 0))
    dos = b"MZ" + b"\0" * 58 + struct.pack("<I", 64)
    coff = b"PE\0\0" + struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, 224, 0x2102)
    opt = struct.pack("<H", 0x10B) + b"\0" * 94          # PE32 magic + the rest of the standard + windows fields (zeros)
    dirs = bytearray(16 * 8)
    if version is not None:
        struct.pack_into("<II", dirs, 2 * 8, sect_va, len(rsrc))
    sec = b".rsrc\0\0\0" + struct.pack("<IIIIIIHHI", max(len(raw), 1), sect_va, len(raw), raw_ptr, 0, 0, 0, 0, 0x40000040)
    head = dos + coff + opt + bytes(dirs) + sec
    head += b"\0" * (raw_ptr - len(head))
    return head + raw


def make_game(root, exe_version=(1, 4, 0, 525), nvse_version=(0, 6, 3, 4), nvse=True, steam=True, esm=True, data=True) -> Path:
    g = Path(root)
    g.mkdir(parents=True, exist_ok=True)
    (g / "FalloutNV.exe").write_bytes(make_pe(exe_version) if exe_version else b"not a program")
    if data:
        (g / "Data").mkdir(exist_ok=True)
        if esm:
            (g / "Data" / "FalloutNV.esm").write_bytes(b"TES4")
    if nvse:
        (g / "nvse_loader.exe").write_bytes(make_pe(nvse_version))
        (g / "nvse_1_4.dll").write_bytes(make_pe(nvse_version))
        if steam:
            (g / "nvse_steam_loader.dll").write_bytes(make_pe(nvse_version))
    (g / "Fallout.ini").write_text("[General]\nuGridsToLoad=5\n", encoding="latin-1")
    (g / "Saves").mkdir(exist_ok=True)
    (g / "Saves" / "Save 1.fos").write_bytes(b"save-bytes")
    return g


def make_mygames(root) -> Path:
    m = Path(root) / "My Games" / "FalloutNV"
    m.mkdir(parents=True, exist_ok=True)
    (m / "Fallout.ini").write_text("[General]\nbUseMyGamesDirectory=1\n", encoding="latin-1")
    (m / "FalloutPrefs.ini").write_text("[Display]\nbFull Screen=1\n", encoding="latin-1")
    return m


PAYLOAD_FILES = {
    "nvcoop.dll": b"MZ-fake-dll-v1",
    "nvcoop_rules.json": b'{"rules": []}',
    "nvcoop_edids.json": b"{}",
    "nvcoop.ini": b"[coop]\r\nenable=0\r\n",
}


def make_release(root, version: str = "1.0.0", files: Optional[Dict[str, bytes]] = None, dests: Optional[Dict[str, str]] = None) -> Path:
    """A release folder with manifest.json and payload\\ (nvcoop.ini is keep_existing)."""
    r = Path(root)
    (r / "payload").mkdir(parents=True, exist_ok=True)
    entries = []
    for name, data in (files or PAYLOAD_FILES).items():
        (r / "payload" / name).write_bytes(data)
        entries.append({"src": f"payload/{name}", "dest": (dests or {}).get(name, f"Data/NVSE/Plugins/{name}"),
                        "sha256": fnv_manifest.sha256_bytes(data), "size": len(data), "keep_existing": name == "nvcoop.ini"})
    (r / "manifest.json").write_text(json.dumps({"schema": 1, "name": "FNV-CoOp", "version": version, "files": entries}), encoding="utf-8")
    return r


def tree(root) -> Dict[str, str]:
    """Every file under root with its SHA-256, plus every folder (marked 'dir'): a snapshot to compare before and after."""
    out = {}
    root = Path(root)
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = fnv_manifest.sha256_file(p) if p.is_file() else "dir"
    return out
