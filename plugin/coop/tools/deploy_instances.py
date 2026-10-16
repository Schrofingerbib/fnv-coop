#!/usr/bin/env python3
"""Deploy the built plugin + its generated data next to nvcoop.dll in the private instances (never touches the game dir).
Source: out/L10/plugin/{nvcoop.dll, nvcoop_rules.json, nvcoop_edids.json, nvcoop_infoquest.json} (produced by plugin/coop/build_plugin.cmd).
    python -I plugin/coop/tools/deploy_instances.py [--instances 0 1] [--root ../fnv-instances-l10]
"""
import argparse, shutil, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "out" / "L10" / "plugin"


def set_ini_key(ini, key, value):
    lines = ini.read_text().splitlines() if ini.exists() else []
    out, seen = [], False
    for l in lines:
        if l.split("=", 1)[0].strip().lower() == key:
            out.append("%s=%s" % (key, value)); seen = True
        else:
            out.append(l)
    if not seen:
        out.append("%s=%s" % (key, value))
    ini.write_text(chr(10).join(out) + chr(10))


def deploy(instances=(0, 1, 10, 11), root=None, handshake=False):
    root = Path(root) if root else REPO.parent / "fnv-instances-l10"
    done = []
    for i in instances:
        dst = root / str(i) / "Data" / "NVSE" / "Plugins"
        if not dst.is_dir():
            continue
        # private test instances talk to fake peers that have no real build identity: handshake off unless asked for
        set_ini_key(dst / "nvcoop.ini", "handshake", "1" if handshake else "0")
        for f in [SRC / "nvcoop.dll", *sorted(SRC.glob("nvcoop_*.json"))]:
            if f.exists():
                shutil.copy2(f, dst / f.name)
                done.append((i, f.name))
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", nargs="*", type=int, default=[0, 1, 10, 11])
    ap.add_argument("--root")
    ap.add_argument("--handshake", action="store_true")
    a = ap.parse_args()
    d = deploy(a.instances, a.root, a.handshake)
    print("deployed %d files: %s" % (len(d), d))
    sys.exit(0 if d else 1)
