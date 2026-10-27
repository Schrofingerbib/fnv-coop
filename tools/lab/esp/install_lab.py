#!/usr/bin/env python3
"""Install NVCoopLab.esp into a test copy (an L2 instance folder), or take it out again.

    python -I tools/lab/esp/install_lab.py --instance-dir <dir> [--esp out/lab/NVCoopLab.esp] [--plugins-txt <file>]
    python -I tools/lab/esp/install_lab.py --instance-dir <dir> --uninstall [--plugins-txt <file>]

What install does, all inside the instance folder:
  1. copies the esp to <dir>/Data/NVCoopLab.esp (through a temporary name, so a hardlinked file is never written through);
  2. makes the copy load it: it sets the first free STestFile slot (2..10) in <dir>/Fallout.ini [General] to NVCoopLab.esp. The L2
     instances read their INIs from their own folder (bUseMyGamesDirectory=0) and have no plugins.txt of their own: the game reads
     plugins.txt from %LOCALAPPDATA%\\FalloutNV, shared with the owner's real game, and the repo rule is never to modify it. sTestFile
     is the per-copy way to load a plugin. STestFile1 (FalloutNV.esm) is never touched.
  3. with --plugins-txt FILE, also lists NVCoopLab.esp in that plugins.txt (one name per line). The shared one under
     %LOCALAPPDATA% is refused.
It refuses the stock game folder (FNV_GAME_DIR, or the Steam default), a folder without Data\\ and Fallout.ini, and a Data folder that
is a link leading outside the instance. provision (tools/runner/instances.py) rewrites Fallout.ini, so install again after it.
Uninstall removes the file, the STestFile entry and the plugins.txt line, and nothing else.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PLUGIN = "NVCoopLab.esp"
DEFAULT_GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas"
MUTANT = os.environ.get("NVLABESP_MUTANT", "")   # test-only: one deliberate bug per name (tools/lab/esp/run_tests.py)


class InstallError(RuntimeError):
    pass


def game_dir() -> Path:
    return Path(os.environ.get("FNV_GAME_DIR", DEFAULT_GAME))


def shared_plugins_txt() -> Optional[Path]:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "FalloutNV" / "plugins.txt" if base else None


def same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


def inside(child: Path, parent: Path) -> bool:
    c, p = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def check_instance(d: Path) -> Tuple[Path, Path]:
    """(Data folder, Fallout.ini) of a real instance copy, or InstallError."""
    d = Path(d)
    if not d.is_dir():
        raise InstallError("instance folder %s does not exist" % d)
    if MUTANT != "game_dir_allowed":
        g = game_dir()
        if same_path(d, g) or inside(d, g):
            raise InstallError("%s is the stock game folder: the lab plugin goes into test copies only" % d)
    data, ini = d / "Data", d / "Fallout.ini"
    if not data.is_dir() or not ini.is_file():
        raise InstallError("%s is not an instance copy (needs Data\\ and Fallout.ini)" % d)
    if not inside(data, d):
        raise InstallError("%s leads outside the instance (%s): refusing to write there" % (data, os.path.realpath(data)))
    return data, ini


# --- Fallout.ini -------------------------------------------------------------------------------------------------------

def read_text(p: Path) -> str:
    return p.read_bytes().decode("latin-1")


def write_text(p: Path, text: str) -> None:
    p.write_bytes(text.encode("latin-1"))


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


SLOT = re.compile(r"^(\s*)STestFile(\d+)\s*=\s*(.*?)\s*$", re.I)


def ini_install(text: str) -> Tuple[str, int]:
    """Set the first free STestFile slot (2..10) to PLUGIN. Returns (new text, slot). Idempotent."""
    lines = text.splitlines(keepends=True)
    eol = _eol(text)
    section, slots, general_end = None, {}, None
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*\[(.+?)\]\s*$", ln)
        if m:
            if section and section.lower() == "general" and general_end is None:
                general_end = i
            section = m.group(1)
            continue
        if section and section.lower() == "general":
            sm = SLOT.match(ln.rstrip("\r\n"))
            if sm:
                slots[int(sm.group(2))] = (i, sm.group(3))
    if section and section.lower() == "general" and general_end is None:
        general_end = len(lines)
    if general_end is None:
        raise InstallError("Fallout.ini has no [General] section")
    for n, (i, val) in slots.items():
        if val.lower() == PLUGIN.lower():
            return text, n
    free = [n for n in range(2, 11) if n not in slots or not slots[n][1]]
    if MUTANT == "clobbers_slot1":
        free = [1]
    if not free:
        raise InstallError("all STestFile slots 2..10 are in use")
    n = free[0]
    if n in slots:
        i = slots[n][0]
        lines[i] = "STestFile%d=%s%s" % (n, PLUGIN, eol)
    else:
        if general_end > 0 and not lines[general_end - 1].endswith(("\n", "\r")):
            lines[general_end - 1] += eol
        lines.insert(general_end, "STestFile%d=%s%s" % (n, PLUGIN, eol))
    return "".join(lines), n


def ini_uninstall(text: str) -> Tuple[str, bool]:
    """Empty the STestFile slot that holds PLUGIN (the key stays, as in a stock Fallout.ini)."""
    if MUTANT == "keeps_ini":
        return text, False
    lines = text.splitlines(keepends=True)
    changed = False
    for i, ln in enumerate(lines):
        sm = SLOT.match(ln.rstrip("\r\n"))
        if sm and sm.group(3).lower() == PLUGIN.lower():
            tail = ln[len(ln.rstrip("\r\n")):]
            lines[i] = "STestFile%s=%s" % (sm.group(2), tail)
            changed = True
    return "".join(lines), changed


# --- plugins.txt (only when asked for) ---------------------------------------------------------------------------------

def check_plugins_txt(p: Path) -> Path:
    p = Path(p)
    s = shared_plugins_txt()
    if s is not None and same_path(p, s):
        raise InstallError("%s is the plugins.txt shared with the real game: refusing (repo rule); pass a private file" % p)
    return p


def plugins_txt_add(p: Path) -> bool:
    lines = read_text(p).splitlines() if p.exists() else []
    if any(l.strip().lstrip("*").lower() == PLUGIN.lower() for l in lines):
        return False
    lines.append(PLUGIN)
    write_text(p, "\r\n".join(lines) + "\r\n")
    return True


def plugins_txt_remove(p: Path) -> bool:
    if not p.exists():
        return False
    lines = read_text(p).splitlines()
    keep = [l for l in lines if l.strip().lstrip("*").lower() != PLUGIN.lower()]
    if len(keep) == len(lines):
        return False
    write_text(p, "\r\n".join(keep) + ("\r\n" if keep else ""))
    return True


# --- install / uninstall -----------------------------------------------------------------------------------------------

def default_esp() -> Path:
    return Path("out") / "lab" / PLUGIN


def install(instance_dir, esp=None, plugins_txt=None) -> dict:
    data, ini = check_instance(instance_dir)
    esp = Path(esp) if esp else default_esp()
    if not esp.is_file():
        raise InstallError("%s not found: run tools/lab/esp/build_lab.py first" % esp)
    if plugins_txt:
        plugins_txt = check_plugins_txt(plugins_txt)
    dst = data / PLUGIN
    tmp = data / (PLUGIN + ".tmp")
    shutil.copyfile(esp, tmp)
    os.replace(tmp, dst)   # a replace, not a write through: an existing hardlink to another file is left alone
    new, slot = ini_install(read_text(ini))
    write_text(ini, new)
    out = {"esp": str(dst), "ini": str(ini), "slot": slot, "plugins_txt": None}
    if plugins_txt:
        plugins_txt_add(plugins_txt)
        out["plugins_txt"] = str(plugins_txt)
    return out


def uninstall(instance_dir, plugins_txt=None) -> dict:
    data, ini = check_instance(instance_dir)
    if plugins_txt:
        plugins_txt = check_plugins_txt(plugins_txt)
    dst = data / PLUGIN
    removed = False
    if dst.exists() and MUTANT != "keeps_esp":
        dst.unlink()
        removed = True
    new, changed = ini_uninstall(read_text(ini))
    if changed:
        write_text(ini, new)
    pt = False
    if plugins_txt:
        pt = plugins_txt_remove(plugins_txt)
    return {"esp_removed": removed, "ini_cleared": changed, "plugins_txt_cleared": pt}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance-dir", required=True, help="an L2 instance folder, for example fnv-instances/0")
    ap.add_argument("--esp", default=None, help="the plugin to copy (default out/lab/NVCoopLab.esp)")
    ap.add_argument("--plugins-txt", default=None, help="also list the plugin in this (private) plugins.txt")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = uninstall(a.instance_dir, a.plugins_txt) if a.uninstall else install(a.instance_dir, a.esp, a.plugins_txt)
    except InstallError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    for k, v in r.items():
        print("%s: %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
