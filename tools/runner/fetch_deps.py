#!/usr/bin/env python
"""Fetch the pinned third-party runtime files the game copies need into out/cache/deps (never into the game folder).

    python -I tools/runner/fetch_deps.py            # download what is missing
    python -I tools/runner/fetch_deps.py --force    # download again

  out/cache/deps/dxvk/d3d9.dll        DXVK 3.1.1, 32-bit. provision installs it (with a 60 fps cap) unless --no-dxvk.
  out/cache/deps/nvse/*               xNVSE 6.4.9: pass --nvse-dir out/cache/deps/nvse to provision.

Why: on an AMD RX 6600 under NVSE, the game's own D3D9 path ran exteriors at about 6 fps with props and foliage jumping, and a black
screen when loading (tools/diag/ has the profiler that showed the main thread stuck in the driver creating and destroying GPU
allocations). DXVK on the same copy: 60+ fps. xNVSE 6.3.4 (the install shipped with the Steam folder) is older than the 6.4.9 the
plugin is built against.
"""
import argparse, os, shutil, sys, tarfile, tempfile, urllib.request
from pathlib import Path

DXVK_VERSION = "3.1.1"
DXVK_URL = f"https://github.com/doitsujin/dxvk/releases/download/v{DXVK_VERSION}/dxvk-{DXVK_VERSION}.tar.gz"
NVSE_VERSION = "6.4.9"
NVSE_URL = f"https://github.com/xNVSE/NVSE/releases/download/{NVSE_VERSION}/nvse_6_4_9.7z"
NVSE_FILES = ("nvse_1_4.dll", "nvse_loader.exe", "nvse_steam_loader.dll")


def deps_dir() -> Path:
    return Path(os.environ.get("NVCOOP_DEPS_DIR") or Path(__file__).resolve().parents[2] / "out" / "cache" / "deps")


def download(url: str, dst: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "nvcoop-fetch-deps"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)


def fetch_dxvk(root: Path, force: bool) -> str:
    out = root / "dxvk" / "d3d9.dll"
    if out.is_file() and not force:
        return f"dxvk: already there ({out})"
    with tempfile.TemporaryDirectory() as t:
        tgz = Path(t) / "dxvk.tar.gz"
        download(DXVK_URL, tgz)
        with tarfile.open(tgz) as tf:
            member = next(m for m in tf.getmembers() if m.name.endswith("/x32/d3d9.dll"))
            out.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return f"dxvk: {DXVK_VERSION} -> {out} ({out.stat().st_size} bytes)"


def fetch_nvse(root: Path, force: bool) -> str:
    out = root / "nvse"
    if all((out / f).is_file() for f in NVSE_FILES) and not force:
        return f"nvse: already there ({out})"
    seven = shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
    if not seven:
        return "nvse: needs 7z on PATH to unpack the .7z (download " + NVSE_URL + " and put the three files in " + str(out) + ")"
    import subprocess
    with tempfile.TemporaryDirectory() as t:
        arc = Path(t) / "nvse.7z"
        download(NVSE_URL, arc)
        subprocess.run([seven, "x", str(arc), f"-o{t}/x", "-y"], check=True, capture_output=True)
        out.mkdir(parents=True, exist_ok=True)
        for f in NVSE_FILES:
            shutil.copy2(Path(t) / "x" / f, out / f)
    return f"nvse: {NVSE_VERSION} -> {out}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    root = deps_dir()
    for fn in (fetch_dxvk, fetch_nvse):
        try:
            print(fn(root, a.force))
        except Exception as e:
            print(f"{fn.__name__}: FAILED: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
