#!/usr/bin/env python3
"""Provision isolated Fallout: New Vegas instances that cost ~0 disk (L2).

Each instance lives in <root>/<i>/ and mirrors the read-only Steam install with hardlinks
(fallback: symlinks, then copies). Only the INIs, Saves/, Data/NVSE/Plugins/ and a few
marker files are per-instance, writable, real files. The game dir is only ever READ.

    python instances.py provision --n 2 [--nvse-dir DIR] [--plugin-dll out/L1/nvcoop.dll]
                                  [--core-dll out/L1split/nvcoop_mod.dll] [--ini SECTION.KEY=VALUE ...]
    python instances.py show --n 2
    python instances.py destroy --n 2

provision rewrites Data/NVSE/Plugins/nvcoop.ini every time, so hand edits are lost. Put extra entries on the
command line instead: --ini is repeatable, e.g. --ini coop.enable=1 --ini dev.bots=1 --ini dev.animprobe=1.
One --ini value applies to every instance. So with --n 2 or more, the per-instance keys bridge.instance,
coop.instanceId, coop.pipe and coop.role are an error (exit 2). provision sets them for each instance.
nvcoop.ini is in the ANSI code page (the plugin reads it with GetPrivateProfileStringA). An --ini entry that
the ANSI code page cannot encode is an error (exit 2).
--plugin-dll not given: out/L1/nvcoop.dll if that file exists, else no plugin. --plugin-dll "": no plugin.
A --plugin-dll or --core-dll path that does not exist is an error (exit 2). --core-dll installs the split
build's reloadable core next to the loader and writes [loader] core=<its file name> (docs/tools/T3a-loader.md).
--core-dll needs an explicit --plugin-dll (the split loader), else it is an error (exit 2). The default
out/L1/nvcoop.dll can be the monolith, which never reads [loader] core.

Environment overrides: FNV_GAME_DIR, FNV_INSTANCES_ROOT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

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


def _base_ini(name: str) -> str:
    """Start from the user's My Games ini (has the DLC archive list etc.), else the game default."""
    mg = Path.home() / "Documents" / "My Games" / "FalloutNV" / name
    if mg.exists():
        return mg.read_text(encoding="latin-1")
    if name.lower() == "fallout.ini":
        d = game_dir() / "Fallout_default.ini"
        if d.exists():
            return d.read_text(encoding="latin-1")
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


def plugin_plan(plugin_dll: Optional[Path] = None, core_dll: Optional[Path] = None,
                plugin_files: Optional[List[Path]] = None) -> "tuple[List[Path], Dict[str, Dict[str, str]]]":
    """The files to copy into Data/NVSE/Plugins, and the nvcoop.ini entries that they need.

    provision calls this before it writes anything. A named file that does not exist is an error (before, a
    missing --plugin-dll installed nothing and gave no message). Two files with the same name are an error,
    because one would overwrite the other. core_dll is the reloadable core of the split build (nvcoop_mod.dll,
    docs/tools/T3a-loader.md). It keeps its file name, and [loader] core= names it, so the loader finds it."""
    named = [("plugin dll (--plugin-dll)", plugin_dll), ("core dll (--core-dll)", core_dll)]
    named += [("plugin file", f) for f in plugin_files or []]
    files: List[Path] = []
    seen: Dict[str, Path] = {}
    for what, f in named:
        if not f:
            continue
        f = Path(f)
        if not f.is_file():
            raise FileNotFoundError(f"{what} not found: {f}")
        if f.name.lower() in seen:
            raise ValueError(f"{what} {f} has the same file name as {seen[f.name.lower()]}; "
                             "one would overwrite the other in Data/NVSE/Plugins")
        seen[f.name.lower()] = f
        files.append(f)
    ini = {"loader": {"core": Path(core_dll).name}} if core_dll else {}
    return files, ini


def provision(i: int, *, role: str = "host", nvse_dir: Optional[Path] = None,
              plugin_dll: Optional[Path] = None, plugin_files: Optional[List[Path]] = None,
              autoload: str = "", autoload_delay_frames: int = 600, modes=LINK_MODES,
              root: Optional[Path] = None, appid: Optional[str] = STEAM_APPID,
              extra_ini: Optional[Dict[str, Dict[str, str]]] = None, core_dll: Optional[Path] = None) -> Dict:
    # Check every input before the first write: a missing dll or a bad ini entry must not leave a half instance.
    sources, plan_ini = plugin_plan(plugin_dll, core_dll, plugin_files)
    ini_extra = provision_ini(plan_ini, extra_ini)
    check_ini_entry("bridge", "autoload", autoload)  # autoload goes into nvcoop.ini too
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
    for p in sources:
        _replace(p, plugins / p.name)
        installed.append("Data/NVSE/Plugins/" + p.name)
    cfg = write_plugin_ini(d, i, role, autoload, autoload_delay_frames, ini_extra)
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
    merge_ini(cfg, extra)
    text = ""
    for s, kv in cfg.items():
        text += f"[{s}]\n" + "".join(f"{k}={v}\n" for k, v in kv.items()) + "\n"
    pdir = d / "Data" / "NVSE" / "Plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    ini = pdir / "nvcoop.ini"
    if ini.exists() or ini.is_symlink():
        ini.unlink()  # never write through a hardlink into the game dir
    ini.write_text(text, encoding=ini_encoding())  # check_ini_entry checks with the same encoding
    return cfg


INI_FORM = "SECTION.KEY=VALUE"


def ini_encoding() -> str:
    """The encoding of nvcoop.ini: the ANSI code page, because the plugin reads the file with
    GetPrivateProfileStringA. write_plugin_ini writes with this encoding and check_ini_entry checks with it."""
    getencoding = getattr(locale, "getencoding", None)  # Python 3.11+: the ANSI code page, also in UTF-8 mode
    return getencoding() if getencoding else locale.getpreferredencoding(False)


def check_ini_encodable(where: str, what: str, text: str) -> None:
    """Raise ValueError if ini_encoding() cannot encode text. Without this check, write_plugin_ini fails with
    UnicodeEncodeError after provision has copied the dlls, and it leaves a 0-byte nvcoop.ini."""
    enc = ini_encoding()
    try:
        str(text).encode(enc)
    except UnicodeEncodeError:
        raise ValueError(f"{where}: the {what} {text!r} has characters that the ANSI code page ({enc}) cannot "
                         "encode, and the plugin reads nvcoop.ini in that code page") from None


def check_ini_entry(section: str, key: Optional[str] = None, value: str = "") -> None:
    """Raise ValueError if the entry cannot be written as one line under one [section] of nvcoop.ini, in the
    encoding of write_plugin_ini. With key None, check only the section name."""
    where = f"{section}.{key}" if key is not None else f"[{section}]"
    if not str(section).strip() or any(c in str(section) for c in "[]\r\n\0"):
        raise ValueError(f"{where}: bad section name {section!r} (it is empty or contains [ ] or a line break)")
    check_ini_encodable(where, "section name", section)
    if key is None:
        return
    if not str(key).strip() or any(c in str(key) for c in "=[]\r\n\0") or str(key).lstrip()[:1] in (";", "#"):
        raise ValueError(f"{where}: bad key {key!r} (it is empty, contains = [ ] or a line break, or starts a comment)")
    check_ini_encodable(where, "key", key)
    if any(c in str(value) for c in "\r\n\0"):
        raise ValueError(f"{where}: the value {value!r} contains a line break")
    check_ini_encodable(where, "value", value)


def parse_ini_override(spec: str) -> "tuple[str, str, str]":
    """Parse one --ini SECTION.KEY=VALUE into (section, key, value). VALUE can be empty and can contain '='.
    Raise ValueError with the reason if the text is malformed."""
    if "=" not in spec:
        raise ValueError(f"{spec!r}: expected {INI_FORM}, the '=' is missing")
    name, value = spec.split("=", 1)
    if "." not in name:
        raise ValueError(f"{spec!r}: expected {INI_FORM}, the '.' between section and key is missing")
    section, key = name.split(".", 1)
    section, key, value = section.strip(), key.strip(), value.strip()
    check_ini_entry(section, key, value)
    return section, key, value


def ini_arg(spec: str) -> str:
    """argparse type for --ini (instances.py and run.py): reject a malformed entry with a clear message."""
    try:
        parse_ini_override(spec)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return spec


def ini_overrides(specs: Optional[List[str]]) -> Dict[str, Dict[str, str]]:
    """--ini SECTION.KEY=VALUE strings -> the extra_ini dict of provision(). A later entry wins."""
    out: Dict[str, Dict[str, str]] = {}
    for spec in specs or []:
        s, k, v = parse_ini_override(spec)
        merge_ini(out, {s: {k: v}})
    return out


def merge_ini(cfg: Dict[str, Dict[str, str]], extra: Optional[Dict[str, Dict[str, str]]]) -> Dict[str, Dict[str, str]]:
    """Merge extra into cfg in place and return cfg. Each entry is checked with check_ini_entry.

    Section and key names match case-insensitively, as in GetPrivateProfileString, so an override replaces the
    entry and keeps its first spelling. A case-sensitive merge wrote 'Role=client' after 'role=host'. The plugin
    modules then disagree: GetPrivateProfileString reads the first entry, and the coop parser reads the last."""
    for s, kv in (extra or {}).items():
        check_ini_entry(s)
        sec = next((x for x in cfg if x.lower() == str(s).lower()), str(s))
        dst = cfg.setdefault(sec, {})
        for k, v in kv.items():
            check_ini_entry(sec, k, v)
            key = next((x for x in dst if x.lower() == str(k).lower()), str(k))
            dst[key] = str(v)
    return cfg


def provision_ini(plan_ini: Optional[Dict[str, Dict[str, str]]],
                  extra_ini: Optional[Dict[str, Dict[str, str]]]) -> Dict[str, Dict[str, str]]:
    """The extra nvcoop.ini entries of provision(): the entries of plugin_plan, then extra_ini over them.
    So an explicit entry (--ini loader.core=...) wins over the [loader] core that --core-dll writes."""
    return merge_ini(merge_ini({}, plan_ini), extra_ini)


# Keys that provision sets to a different value in each instance (write_plugin_ini). Lower case.
PER_INSTANCE_INI_KEYS = (("bridge", "instance"), ("coop", "instanceid"), ("coop", "pipe"), ("coop", "role"))


def check_per_instance_ini(extra_ini: Optional[Dict[str, Dict[str, str]]], n: int) -> None:
    """Raise ValueError if extra_ini sets a per-instance key and n > 1. One --ini value applies to every
    instance, so all n instances would get the same instance number, pipe or role."""
    if n <= 1:
        return
    for s, kv in (extra_ini or {}).items():
        for k in kv:
            if (str(s).lower(), str(k).lower()) in PER_INSTANCE_INI_KEYS:
                raise ValueError(f"--ini {s}.{k}: this is a per-instance key, and one --ini value applies to all "
                                 f"{n} instances. Remove it (provision sets it for each instance), or use --n 1")


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


DEFAULT_PLUGIN_DLL = Path(__file__).resolve().parents[2] / "out" / "L1" / "nvcoop.dll"


def plugin_args(plugin_dll: Optional[str], core_dll: Optional[str]) -> "tuple[Optional[Path], Optional[Path]]":
    """--plugin-dll and --core-dll of provision -> (plugin dll, core dll). None means: do not install that file.

    --plugin-dll not given (None): DEFAULT_PLUGIN_DLL if it exists, else no plugin. --plugin-dll "": no plugin.
    --core-dll needs an explicit, non-empty --plugin-dll, else ValueError. The default out/L1/nvcoop.dll can be
    the monolith, which never reads [loader] core, so the core would never load."""
    if core_dll and not plugin_dll:
        raise ValueError("--core-dll needs an explicit --plugin-dll (the split loader), for example "
                         "--plugin-dll out/L1split/nvcoop.dll --core-dll out/L1split/nvcoop_mod.dll. "
                         "The default out/L1/nvcoop.dll can be the monolith, which never reads [loader] core")
    if plugin_dll is None:
        dll = DEFAULT_PLUGIN_DLL if DEFAULT_PLUGIN_DLL.is_file() else None
    else:
        dll = Path(plugin_dll) if plugin_dll else None
    return dll, (Path(core_dll) if core_dll else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("provision")
    p.add_argument("--n", type=int, default=2)
    p.add_argument("--nvse-dir")
    p.add_argument("--plugin-dll", default=None,
                   help="plugin dll to install; a path that does not exist is an error; \"\" means no plugin "
                        "(default: out/L1/nvcoop.dll if it exists, else no plugin)")
    p.add_argument("--core-dll", default=None,
                   help="split build only: the reloadable core (nvcoop_mod.dll) to install next to the loader; "
                        "writes [loader] core=<file name>; needs an explicit --plugin-dll (the split loader)")
    p.add_argument("--ini", action="append", default=[], type=ini_arg, metavar=INI_FORM,
                   help="extra nvcoop.ini entry, repeatable, e.g. --ini coop.enable=1 --ini dev.bots=1; "
                        "with --n 2 or more, the per-instance keys bridge.instance, coop.instanceId, coop.pipe "
                        "and coop.role are an error")
    p.add_argument("--autoload", default="")
    p.add_argument("--no-appid", action="store_true")
    p.add_argument("--modes", default=",".join(LINK_MODES))
    for c in ("show", "destroy"):
        q = sub.add_parser(c)
        q.add_argument("--n", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "provision":
        try:  # fail before the first write
            dll, core = plugin_args(a.plugin_dll, a.core_dll)
            _, plan_ini = plugin_plan(dll, core)
            extra_ini = ini_overrides(a.ini)
            check_per_instance_ini(extra_ini, a.n)
            provision_ini(plan_ini, extra_ini)  # checks the [loader] core entry too
            check_ini_entry("bridge", "autoload", a.autoload)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
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
                          plugin_dll=dll, core_dll=core, extra_ini=extra_ini,
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
