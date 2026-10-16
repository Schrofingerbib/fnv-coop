#!/usr/bin/env python3
"""Stage the NV:MP 9.4 client package (and, for instance 0, the story server) into private L2 instances.

    FNV_INSTANCES_ROOT=<root> python -I -X utf8 plugin/nvmp/live/stage_nvmp.py [--n 2] [--src DIR] [--dry-run]

* Reads only from the NV:MP package dir (default third_party/nvmp) and writes only into <root>/<i>/.
* Copies are real files (never hardlinks into the Steam install); every file is verified by size and the two pinned
  binaries by SHA-256 against plugin/nvmp/pinned_builds.json before anything is written.
* The server (nvmp_storyserver.exe) goes ONLY to instance 0, at exactly <root>/0/nvmp_storyserver.exe (the firewall rule path).
* Never launches anything.
"""
import argparse, hashlib, json, os, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NV = HERE.parent
REPO = NV.parents[1]
DEFAULT_SRC = REPO / "third_party" / "nvmp"
ROOT_FILES = ["nvmp_start.exe", "OpenAL32.dll", "discord_game_sdk.dll", "libcrypto-3.dll", "libssl-3.dll", ".nvmp_version"]
SERVER = "nvmp_storyserver.exe"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def plan(src: Path, n: int, root: Path):
    steps = []   # (src, dst)
    for i in range(n):
        d = root / str(i)
        for name in ROOT_FILES:
            steps.append((src / name, d / name))
        for f in sorted((src / "nvmp").rglob("*")):
            if f.is_file() and not f.name.endswith(".strings.txt"):
                steps.append((f, d / "nvmp" / f.relative_to(src / "nvmp")))
    steps.append((src / SERVER, root / "0" / SERVER))
    return steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    root = Path(os.environ.get("FNV_INSTANCES_ROOT", ""))
    src = Path(a.src)
    if not root.is_dir():
        print("FNV_INSTANCES_ROOT must point at the provisioned instance root", file=sys.stderr)
        return 2
    pins = json.loads((NV / "pinned_builds.json").read_text())["9.4"]
    for name, rel in (("client.dll", "nvmp/client.dll"), (SERVER, SERVER)):
        got = sha(src / rel)
        if got != pins[name]:
            print(f"REFUSING: {rel} sha256 {got[:16]}... is not the pinned 9.4 build", file=sys.stderr)
            return 3
    steps = plan(src, a.n, root)
    missing = [str(s) for s, _ in steps if not s.exists()]
    if missing:
        print("missing source files:", *missing[:10], sep="\n  ", file=sys.stderr)
        return 3
    total = sum(s.stat().st_size for s, _ in steps)
    print(f"{len(steps)} files, {total / 2**20:.1f} MiB into {root} (instances 0..{a.n - 1}); server only at {root / '0' / SERVER}")
    for s, d in steps:
        if not str(d.resolve()).lower().startswith(str(root.resolve()).lower()):
            print("REFUSING: destination outside the instance root:", d, file=sys.stderr)
            return 3
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    for s, d in steps:
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists() or d.is_symlink():
            if os.stat(d).st_nlink > 1:        # a hardlink into the Steam install: never write through it
                d.unlink()
            elif d.stat().st_size == s.stat().st_size and sha(d) == sha(s):
                continue
        shutil.copy2(s, d)
    bad = [d for s, d in steps if not d.exists() or d.stat().st_size != s.stat().st_size]
    if bad:
        print("verification failed:", *bad[:5], file=sys.stderr)
        return 4
    for i in range(a.n):
        assert sha(root / str(i) / "nvmp" / "client.dll") == pins["client.dll"]
    assert sha(root / "0" / SERVER) == pins[SERVER]
    print("staged and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
