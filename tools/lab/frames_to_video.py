"""Turn the bridge's frame grabs (stored-deflate RGB PNGs, which ffmpeg's PNG reader rejects) into JPG stills and an MP4.

    python -I tools/lab/frames_to_video.py out/lab/shots [--fps 24] [--gamma 1.0]
Writes <dir>/<name>.jpg for every still, <dir>/orbit.mp4 from orbit_NNN.png, and <dir>/sheet.jpg (the stills in a grid). Needs ffmpeg on PATH.
"""
import argparse, struct, subprocess, sys, tempfile, zlib
from pathlib import Path


def read_png_rgb(path: Path):
    """(width, height, raw RGB bytes) of an 8-bit RGB PNG with filter 0 rows (what plugin/bridge/screenshot.cpp writes)."""
    b = path.read_bytes()
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name}: not a PNG")
    i, idat, w, h = 8, bytearray(), 0, 0
    while i + 8 <= len(b):
        n, = struct.unpack(">I", b[i:i + 4])
        t = b[i + 4:i + 8]
        if i + 12 + n > len(b):
            raise ValueError(f"{path.name}: truncated ({len(b)} bytes; a chunk needs {i + 12 + n})")
        if t == b"IHDR":
            w, h = struct.unpack(">II", b[i + 8:i + 16])
        elif t == b"IDAT":
            idat += b[i + 8:i + 8 + n]
        i += 12 + n
    raw = zlib.decompress(bytes(idat))
    row = w * 3
    out = bytearray()
    for y in range(h):
        if raw[y * (row + 1)] != 0:
            raise ValueError(f"{path.name}: row filter {raw[y * (row + 1)]} is not supported")
        out += raw[y * (row + 1) + 1:(y + 1) * (row + 1)]
    return w, h, bytes(out)


def to_ppm(src: Path, dst: Path) -> None:
    w, h, rgb = read_png_rgb(src)
    dst.write_bytes(b"P6 %d %d 255\n" % (w, h) + rgb)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--gamma", type=float, default=1.0)
    a = ap.parse_args()
    d = Path(a.dir)
    vf = f"eq=gamma={a.gamma}," if a.gamma != 1.0 else ""
    ff = ["ffmpeg", "-loglevel", "error", "-y"]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        stills = sorted(p for p in d.glob("*.png") if not p.name.startswith("orbit_"))
        for p in stills:
            to_ppm(p, t / (p.stem + ".ppm"))
            subprocess.run(ff + ["-i", str(t / (p.stem + ".ppm")), "-vf", vf + "null", "-q:v", "3", str(d / (p.stem + ".jpg"))], check=True)
        if stills:
            ins = sum((["-i", str(d / (p.stem + ".jpg"))] for p in stills), [])
            cols = 3 if len(stills) > 4 else 2
            lay = "|".join(f"{'+'.join(['w0'] * (k % cols)) or '0'}_{'+'.join(['h0'] * (k // cols)) or '0'}" for k in range(len(stills)))
            subprocess.run(ff + ins + ["-filter_complex", f"xstack=inputs={len(stills)}:layout={lay}:fill=black,scale=1440:-2", "-q:v", "3",
                                       str(d / "sheet.jpg")], check=True)
        orbit = sorted(d.glob("orbit_*.png"))
        for k, p in enumerate(orbit):
            to_ppm(p, t / f"o_{k:03d}.ppm")
        if orbit:
            subprocess.run(ff + ["-framerate", str(a.fps), "-i", str(t / "o_%03d.ppm"), "-vf", vf + "scale=1280:-2,format=yuv420p", "-c:v", "libx264",
                                 "-crf", "20", str(d / "orbit.mp4")], check=True)
    print(f"stills {len(stills)}, orbit frames {len(orbit)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
