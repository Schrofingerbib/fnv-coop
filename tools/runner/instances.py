#!/usr/bin/env python3
"""Provision isolated Fallout: New Vegas instances that cost ~0 disk (L2).

Each instance lives in <root>/<i>/ and mirrors the read-only Steam install with hardlinks
(fallback: symlinks, then copies). Only the INIs, Saves/, Data/NVSE/Plugins/ and a few
marker files are per-instance, writable, real files. The game dir is only ever READ.

    python instances.py provision --n 2 [--nvse-dir DIR] [--plugin-dll out/L1/nvcoop.dll]
    python instances.py show --n 2
    python instances.py destroy --n 2

Environment overrides: FNV_GAME_DIR, FNV_INSTANCES_ROOT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mygames  # noqa: E402

DEFAULT_GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas"
DEFAULT_ROOT = str(Path(__file__).resolve().parents[3] / "fnv-instances")
STEAM_APPID = "22380"

# files that must be real, per-instance (the game writes them)
PER_INSTANCE = {"fallout.ini", "falloutprefs.ini", "rendererinfo.txt", "steam_appid.txt"}
SKIP_DIRS = {"saves", "screenshots"}  # created empty per instance
PINNED_FILES = ["FalloutNV.exe", "Data/FalloutNV.esm"]

LINK_MODES = ("hardlink", "symlink", "copy")


def game_dir() -> Path:
    return Path(os.environ.get("FNV_GAME_DIR", DEFAULT_GAME))


def instances_root() -> Path:
    return Path(os.environ.get("FNV_INSTANCES_ROOT", DEFAULT_ROOT))


def instance_dir(i: int, root: Optional[Path] = None) -> Path:
    return Path(root or instances_root()) / str(int(i))


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- INI editing
def ini_set(text: str, section: str, key: str, value: str) -> str:
    """Set key in [section] (case-insensitive), adding section/key if missing. Keeps CRLF."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl) if text else []
    sec_re = re.compile(r"^\s*\[(.+?)\]\s*$")
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=", re.I)
    cur = None
    sec_end = None  # index after last non-blank line of target section
    for idx, ln in enumerate(lines):
        m = sec_re.match(ln)
        if m:
            if cur is not None and cur.lower() == section.lower():
                break
            cur = m.group(1)
            if cur.lower() == section.lower():
                sec_end = idx + 1
            continue
        if cur is not None and cur.lower() == section.lower():
            if key_re.match(ln):
                lines[idx] = f"{key}={value}"
                return nl.join(lines)
            if ln.strip():
                sec_end = idx + 1
    if sec_end is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", f"{key}={value}"]
    else:
        lines.insert(sec_end, f"{key}={value}")
    return nl.join(lines)


def ini_get(text: str, section: str, key: str) -> Optional[str]:
    cur = None
    for ln in text.splitlines():
        m = re.match(r"^\s*\[(.+?)\]\s*$", ln)
        if m:
            cur = m.group(1)
            continue
        if cur and cur.lower() == section.lower():
            m = re.match(r"^\s*" + re.escape(key) + r"\s*=\s*(.*?)\s*$", ln, re.I)
            if m:
                return m.group(1)
    return None


# Fallout.ini (read from the game dir because [General] bUseMyGamesDirectory=0; the string
# "bUseMyGamesDirectory:General" is at file offset 0xc81cc0 of FalloutNV.exe 1.4.0.525)
FALLOUT_INI_EDITS = [
    ("General", "bUseMyGamesDirectory", "0"),   # INIs + Saves live in the instance dir
    ("General", "bAlwaysActive", "1"),          # keep ticking when the window is unfocused
    ("General", "SLocalSavePath", "Saves\\"),
    ("General", "SLocalMasterPath", "Data\\"),
    ("Controls", "bBackground Mouse", "1"),
    ("Controls", "bBackground Keyboard", "1"),
]
PREFS_EDITS = [
    ("Display", "bFull Screen", "0"),
    ("Display", "iSize W", "640"),
    ("Display", "iSize H", "480"),
    ("Display", "iPresentInterval", "0"),
    ("Display", "iMultiSample", "0"),
]


class PrefsUnavailable(RuntimeError):
    pass


def display_adapter_name() -> Optional[str]:
    """The primary display adapter's name (EnumDisplayDevicesW), which is what the game's launcher writes as sD3DDevice."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class DisplayDevice(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                    ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]

    first, i = None, 0
    while True:
        dd = DisplayDevice()
        dd.cb = ctypes.sizeof(dd)
        if not ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        if dd.DeviceString and first is None:
            first = dd.DeviceString
        if dd.StateFlags & 0x4:  # DISPLAY_DEVICE_PRIMARY_DEVICE
            return dd.DeviceString
        i += 1
    return first


def default_prefs(adapter=None) -> str:
    """FalloutPrefs.ini for an owner who has none (the game never ran here): Fallout_default.ini, the game's own low.ini
    preset over it, and sD3DDevice. FalloutNV.exe 1.4.0.525 starts FalloutNVLauncher.exe instead of the game when the
    prefs have no sD3DDevice (checked in game on 2026-10-21: a 6-line prefs file and a defaults-only one both opened the
    launcher on the owner's desktop; the same defaults plus sD3DDevice started the game)."""
    g = game_dir()
    d = g / "Fallout_default.ini"
    text = d.read_text(encoding="latin-1") if d.exists() else ""
    low = g / "low.ini"
    if low.exists():
        sec = None
        for line in low.read_text(encoding="latin-1").splitlines():
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                sec = s[1:-1]
            elif "=" in s and sec and not s.startswith(";"):
                k, v = s.split("=", 1)
                text = ini_set(text, sec, k.strip(), v.strip())
    name = (adapter or display_adapter_name)()
    if not name:
        raise PrefsUnavailable("no FalloutPrefs.ini in My Games and the display adapter name is unknown: start Fallout: New "
                               "Vegas once through Steam so its launcher writes FalloutPrefs.ini, then provision again")
    return ini_set(text, "Display", "sD3DDevice", f'"{name}"')


def _base_ini(name: str) -> str:
    """Start from the user's My Games ini (has the DLC archive list etc.), else the game default."""
    mg = mygames.my_games_dir() / name
    if mg.exists():
        return mg.read_text(encoding="latin-1")
    if name.lower() == "fallout.ini":
        d = game_dir() / "Fallout_default.ini"
        if d.exists():
            return d.read_text(encoding="latin-1")
    if name.lower() == "falloutprefs.ini":
        return default_prefs()
    return ""


def render_inis() -> Dict[str, str]:
    fo = _base_ini("Fallout.ini")
    for s, k, v in FALLOUT_INI_EDITS:
        fo = ini_set(fo, s, k, v)
    pr = _base_ini("FalloutPrefs.ini")
    for s, k, v in PREFS_EDITS:
        pr = ini_set(pr, s, k, v)
    return {"Fallout.ini": fo, "FalloutPrefs.ini": pr}


# ---------------------------------------------------------------- linking
def _link(src: Path, dst: Path, modes=LINK_MODES) -> str:
    last = None
    for m in modes:
        try:
            if m == "hardlink":
                os.link(src, dst)
            elif m == "symlink":
                os.symlink(src, dst)
            else:
                shutil.copy2(src, dst)
            return m
        except OSError as e:
            last = e
    raise OSError(f"cannot link {src} -> {dst}: {last}")


def mirror_tree(src_root: Path, dst_root: Path, modes=LINK_MODES) -> Dict[str, int]:
    """Link every file of src_root into dst_root (real dirs). Idempotent. Counts per mode."""
    counts: Dict[str, int] = {m: 0 for m in LINK_MODES}
    counts["existing"] = 0
    for dirpath, dirnames, filenames in os.walk(src_root):
        rel = Path(dirpath).relative_to(src_root)
        dirnames[:] = [d for d in dirnames if not (rel == Path(".") and d.lower() in SKIP_DIRS)]
        out = dst_root / rel
        out.mkdir(parents=True, exist_ok=True)
        for fn in filenames:
            if rel == Path(".") and fn.lower() in PER_INSTANCE:
                continue
            if rel == Path(".") and fn.lower().endswith((".log", ".dmp", ".mdmp")):
                continue  # runtime output of the main install: never share (writes would go through the link)
            if [x.lower() for x in rel.parts[:3]] == ["data", "nvse", "plugins"]:
                continue  # plugins are per-instance copies (lanes test different dll builds)
            d = out / fn
            if d.exists() or d.is_symlink():
                counts["existing"] += 1
                continue
            counts[_link(Path(dirpath) / fn, d, modes)] += 1
    return counts


def _replace(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    shutil.copy2(src, dst)


def provision(i: int, *, role: str = "host", nvse_dir: Optional[Path] = None,
              plugin_dll: Optional[Path] = None, plugin_files: Optional[List[Path]] = None,
              autoload: str = "", autoload_delay_frames: int = 600, modes=LINK_MODES,
              root: Optional[Path] = None, appid: Optional[str] = STEAM_APPID,
              extra_ini: Optional[Dict[str, Dict[str, str]]] = None) -> Dict:
    g = game_dir()
    if not (g / "FalloutNV.exe").exists():
        raise FileNotFoundError(f"game not found at {g}")
    d = instance_dir(i, root)
    d.mkdir(parents=True, exist_ok=True)
    counts = mirror_tree(g, d, modes)
    (d / "Saves").mkdir(exist_ok=True)
    plugins = d / "Data" / "NVSE" / "Plugins"
    for p in (d / "Data" / "NVSE", plugins):
        if p.is_symlink():
            raise RuntimeError(f"{p} is a symlink; refusing to write plugins into the game install")
    plugins.mkdir(parents=True, exist_ok=True)
    for name, text in render_inis().items():
        (d / name).write_text(text, encoding="latin-1", newline="")
    if appid:
        (d / "steam_appid.txt").write_text(appid, encoding="ascii")
    installed: List[str] = []
    if nvse_dir:  # xNVSE: loader + dlls at the instance root
        for f in Path(nvse_dir).iterdir():
            if f.is_file() and f.suffix.lower() in (".exe", ".dll") and f.name.lower().startswith(("nvse", "xnvse")):
                _replace(f, d / f.name)
                installed.append(f.name)
    for p in ([plugin_dll] if plugin_dll else []) + list(plugin_files or []):
        _replace(Path(p), plugins / Path(p).name)
        installed.append("Data/NVSE/Plugins/" + Path(p).name)
    cfg = write_plugin_ini(d, i, role, autoload, autoload_delay_frames, extra_ini)
    meta = {
        "instance": i, "role": role, "dir": str(d), "pipe": rf"\\.\pipe\nvcoop-{i}",
        "exe": str(d / "FalloutNV.exe"), "link_counts": counts, "installed": installed,
        "ini": cfg, "game_dir": str(g),
        "pinned": {p: sha256_file(d / p) for p in PINNED_FILES if (d / p).exists()},
    }
    (d / "instance.json").write_text(json.dumps(meta, indent=1))
    return meta


def write_plugin_ini(d: Path, i: int, role: str, autoload: str = "", delay: int = 600,
                     extra: Optional[Dict[str, Dict[str, str]]] = None) -> Dict:
    """nvcoop.ini read by plugin/main.cpp ([bridge] instance/autoload/autoloadDelayFrames); [coop] is ours."""
    cfg = {
        "bridge": {"instance": str(i), "autoload": autoload, "autoloadDelayFrames": str(delay), "minimized": "1", "multiInstance": "1"},
        "coop": {"instanceId": str(i), "role": role, "pipe": rf"\\.\pipe\nvcoop-{i}"},
    }
    for s, kv in (extra or {}).items():
        cfg.setdefault(s, {}).update(kv)
    text = ""
    for s, kv in cfg.items():
        text += f"[{s}]\n" + "".join(f"{k}={v}\n" for k, v in kv.items()) + "\n"
    pdir = d / "Data" / "NVSE" / "Plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    ini = pdir / "nvcoop.ini"
    if ini.exists() or ini.is_symlink():
        ini.unlink()  # never write through a hardlink into the game dir
    ini.write_text(text)
    return cfg


def load_meta(i: int, root: Optional[Path] = None) -> Dict:
    return json.loads((instance_dir(i, root) / "instance.json").read_text())


def destroy(i: int, root: Optional[Path] = None) -> None:
    """Remove an instance dir (removes link entries only; originals untouched)."""
    d = instance_dir(i, root)
    if not d.exists():
        return

    def onerr(fn, path, exc):  # read-only attribute on linked files
        os.chmod(path, 0o666)
        fn(path)
    shutil.rmtree(d, onerror=onerr)


def verify_pins(i: int, root: Optional[Path] = None, extra: Optional[List[str]] = None) -> List[str]:
    """Problems: pinned file missing / hash differs from the hash recorded at provisioning."""
    d = instance_dir(i, root)
    meta = load_meta(i, root)
    bad = []
    for rel, h in meta["pinned"].items():
        p = d / rel
        if not p.exists():
            bad.append(f"{rel}: missing")
        elif sha256_file(p) != h:
            bad.append(f"{rel}: hash changed")
    for rel in extra or []:
        if not (d / rel).exists():
            bad.append(f"{rel}: missing")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("provision")
    p.add_argument("--n", type=int, default=2)
    p.add_argument("--nvse-dir")
    p.add_argument("--plugin-dll", default=str(Path(__file__).resolve().parents[2] / "out" / "L1" / "nvcoop.dll"))
    p.add_argument("--autoload", default="")
    p.add_argument("--no-appid", action="store_true")
    p.add_argument("--modes", default=",".join(LINK_MODES))
    for c in ("show", "destroy"):
        q = sub.add_parser(c)
        q.add_argument("--n", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "provision":
        root = instances_root()
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        print(f"free disk: {free / 2**30:.1f} GiB")
        if free < 2 * 2**30:
            print("refusing: <2 GiB free", file=sys.stderr)
            return 2
        for i in range(a.n):
            m = provision(i, role="host" if i == 0 else "client",
                          nvse_dir=Path(a.nvse_dir) if a.nvse_dir else None,
                          plugin_dll=Path(a.plugin_dll) if a.plugin_dll and Path(a.plugin_dll).exists() else None,
                          autoload=a.autoload, modes=tuple(a.modes.split(",")),
                          appid=None if a.no_appid else STEAM_APPID)
            print(f"instance {i}: {m['dir']} links={m['link_counts']}")
    elif a.cmd == "show":
        for i in range(a.n):
            print(json.dumps(load_meta(i), indent=1))
    else:
        for i in range(a.n):
            destroy(i)
            print("destroyed", i)
    return 0


if __name__ == "__main__":
    sys.exit(main())
