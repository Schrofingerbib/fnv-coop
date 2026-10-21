#!/usr/bin/env python3
"""Install, check, repair and uninstall the Fallout: New Vegas co-op plugin.

    python fnv_install.py find                       list the New Vegas installs found on this PC
    python fnv_install.py check   [--game DIR]       read-only: game version, xNVSE, folders (exit 1 when something blocks the install)
    python fnv_install.py install [--game DIR] [--yes] [--dry-run] [--force]
    python fnv_install.py status  [--game DIR]       what is installed, and whether the files are still the ones we put there
    python fnv_install.py uninstall [--game DIR] [--keep-backups] [--remove-logs] [--dry-run]
    python fnv_install.py restore-inis [--game DIR]  put the backed-up game INIs (Fallout.ini, FalloutPrefs.ini) back

What install does, in order, and stops at the first problem before it has changed anything:
  1. reads manifest.json of the release and checks the SHA-256 of every file in payload\\ (a damaged download stops here);
  2. checks FalloutNV.exe is version 1.4.0.525, xNVSE (6.3.0 or newer) is present, and the game is not running;
  3. backs up what it is about to replace, plus Fallout.ini and FalloutPrefs.ini, into <game>\\FNVCoOp\\backups\\<time>\\;
  4. copies the plugin and its data into <game>\\Data\\NVSE\\Plugins (each file is written next to its final name, hashed,
     then renamed, so a crash never leaves a half file); an existing nvcoop.ini is kept as it is;
  5. writes <game>\\FNVCoOp\\install.json, the record that uninstall uses.
If any copy fails, everything is put back as it was.

It never downloads anything. xNVSE and Python come from their own pages (see LINK_* in fnv_find.py).
Saves are never touched. Exit codes: 0 ok, 1 a problem was found or something failed, 2 usage error.
"""
import argparse
import ctypes
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fnv_find  # noqa: E402
import fnv_ini  # noqa: E402
import fnv_manifest  # noqa: E402
import fnv_mut  # noqa: E402
import fnv_pe  # noqa: E402

RECORD_DIR = "FNVCoOp"
RECORD_NAME = "install.json"
BACKUPS = "backups"
REMOVED = "removed"
INI_NAME = "nvcoop.ini"
PLUGINS = "Data/NVSE/Plugins"
GAME_INIS = ("Fallout.ini", "FalloutPrefs.ini")
# files the plugin writes while it runs (next to nvcoop.dll); uninstall removes them only with --remove-logs
RUNTIME_FILES = ("nvcoop.log", "nvcoop_actors_ledger.txt")
RUNTIME_DIRS = ("nvcoop_cache",)


@dataclass
class Result:
    ok: bool = True
    lines: List[str] = field(default_factory=list)

    def say(self, msg: str, log: Optional[Callable[[str], None]] = None):
        self.lines.append(msg)
        if log:
            log(msg)

    def fail(self, msg: str, log: Optional[Callable[[str], None]] = None):
        self.ok = False
        self.say(msg, log)
        return self


def _p(game: Path, rel: str) -> Path:
    return game.joinpath(*rel.split("/"))


def record_path(game: Path) -> Path:
    return game / RECORD_DIR / RECORD_NAME


def load_record(game: Path) -> Optional[dict]:
    try:
        r = json.loads(record_path(game).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return r if isinstance(r, dict) and r.get("schema") == 1 else None


def _write_record(game: Path, rec: dict) -> None:
    path = record_path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
    fnv_ini.replace_retry(tmp, path)


def inside(root: Path, path: Path) -> bool:
    """True when path (after resolving links) is root or below it. Used so a linked folder cannot send writes elsewhere."""
    r = os.path.normcase(os.path.realpath(root))
    p = os.path.normcase(os.path.realpath(path))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def my_games_dir(env: Optional[Dict[str, str]] = None) -> Path:
    """Documents\\My Games\\FalloutNV, following a redirected Documents folder like the game does (SHGetFolderPath)."""
    env = os.environ if env is None else env
    override = env.get("FNV_DOCUMENTS_DIR")
    if override:
        docs = Path(override)
    else:
        docs = None
        if os.name == "nt":
            buf = ctypes.create_unicode_buffer(260)
            try:
                if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0005, None, 0, buf) == 0 and buf.value:
                    docs = Path(buf.value)
            except (AttributeError, OSError):
                docs = None
        docs = docs or Path.home() / "Documents"
    return docs / "My Games" / "FalloutNV"


def copy_verified(src: Path, dst: Path) -> None:
    """Copy src to dst through a temp name next to dst, check the SHA-256, then rename. dst is either the old file or the new one."""
    tmp = dst.with_name(dst.name + ".fnvcoop-new")
    try:
        shutil.copyfile(src, tmp)
        if not fnv_mut.on("no_verify_copy") and fnv_manifest.sha256_file(tmp) != fnv_manifest.sha256_file(src):
            raise OSError(f"copy of {src.name} did not match (disk problem?)")
        fnv_ini.replace_retry(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _new_backup_id(base: Path) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bid, n = stamp, 1
    while (base / bid).exists():
        n += 1
        bid = f"{stamp}-{n}"
    return bid


def _rmdir_if_empty(p: Path) -> bool:
    try:
        p.rmdir()
        return True
    except OSError:
        return False


def _backup(game: Path, bdir: Path, rels: List[str], mygames: Path) -> dict:
    """Copy rels (paths relative to the game folder) and the game INIs into bdir. Returns the backup.json content."""
    entries = []

    def put(src: Path, dst_rel: str, kind: str):
        if not src.is_file():
            return
        dst = bdir.joinpath(*dst_rel.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        h = fnv_manifest.sha256_file(src)
        if fnv_manifest.sha256_file(dst) != h:
            raise OSError(f"backup copy of {src} did not match")
        entries.append({"kind": kind, "source": str(src), "backup": dst_rel, "sha256": h})

    if not fnv_mut.on("no_backup"):
        for rel in rels:
            put(_p(game, rel), "files/" + rel, "plugin-file")
    for n in GAME_INIS:
        if fnv_mut.on("no_backup_inis"):
            break
        put(game / n, "game_inis/gamedir/" + n, "game-ini")
        put(mygames / n, "game_inis/mygames/" + n, "game-ini")
    meta = {"schema": 1, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "game_dir": str(game), "entries": entries}
    (bdir).mkdir(parents=True, exist_ok=True)
    (bdir / "backup.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def pick_game(arg: Optional[str], *, env=None, reg=None, drives=None, ask: Optional[Callable[[str], str]] = None,
              assume_yes: bool = False, log: Callable[[str], None] = print) -> Optional[fnv_find.Candidate]:
    cands = fnv_find.candidates(arg, env, reg, drives)
    if arg:
        if cands and (cands[0].source == "argument" or fnv_mut.on("pick_fallback")):
            return cands[0]    # the folder that was asked for, never another install that happens to be found
        log(f"No {fnv_find.GAME_EXE} in {arg}.")
        return None
    if not cands:
        log("Could not find Fallout: New Vegas on this PC. Run again with  --game \"<folder that holds FalloutNV.exe>\".")
        if ask:
            p = ask("Folder that holds FalloutNV.exe (empty to cancel): ").strip().strip('"')
            if p:
                c = fnv_find.candidates(p, env, reg, drives)
                if c:
                    return c[0]
                log(f"No {fnv_find.GAME_EXE} in {p}.")
        return None
    if len(cands) == 1:
        return cands[0]
    log("Found more than one install:")
    for i, c in enumerate(cands, 1):
        log(f"  {i}) {c.path}   ({c.source})")
    if ask and not assume_yes:
        a = ask(f"Which one? [1-{len(cands)}]: ").strip()
        if a.isdigit() and 1 <= int(a) <= len(cands):
            return cands[int(a) - 1]
        return None
    log("Pick one with  --game \"<folder>\".")
    return None


def check(game: Path, edition: str = "", log: Optional[Callable[[str], None]] = None,
          running: Optional[Callable[[], bool]] = None) -> "tuple[fnv_find.GameInfo, Result]":
    running = running or fnv_find.game_is_running
    info = fnv_find.inspect_game(game, edition)
    res = Result()
    res.say(f"Game folder:  {game}", log)
    res.say(f"Game version: {fnv_pe.format_version(info.exe_version)}  (needs {fnv_pe.format_version(fnv_find.SUPPORTED_GAME)})", log)
    t = fnv_find.nvse_triple(info.nvse_version)
    res.say("xNVSE:        " + ("not installed" if not (info.nvse_loader and info.nvse_dll)
                                else ("version unknown" if t is None else ".".join(map(str, t)))), log)
    rec = load_record(game)
    res.say("Co-op plugin: " + (f"installed, release {rec.get('version', '?')}" if rec else "not installed"), log)
    for w in info.warnings:
        res.say("  warning: " + w, log)
    for pr in info.problems + info.overridable:
        res.fail("  PROBLEM: " + pr, log)
    if running():
        res.say("  warning: Fallout: New Vegas is running. Close it before installing or uninstalling.", log)
    return info, res


def install(game, release_root, *, force: bool = False, dry_run: bool = False, edition: str = "",
            log: Optional[Callable[[str], None]] = None, running: Optional[Callable[[], bool]] = None,
            mygames: Optional[Path] = None, copy_file: Callable[[Path, Path], None] = copy_verified) -> Result:
    running = running or fnv_find.game_is_running
    game = Path(game)
    res = Result()
    say = lambda m: res.say(m, log)  # noqa: E731
    try:
        manifest = fnv_manifest.load(release_root)
    except fnv_manifest.ManifestError as e:
        return res.fail(f"The release is damaged: {e}", log)
    bad = fnv_manifest.verify_payload(release_root, manifest)
    if bad:
        for b in bad:
            say("  " + b)
        return res.fail("The download is damaged or incomplete (see above). Download the zip again and unpack it fresh. Nothing was changed.", log)
    info = fnv_find.inspect_game(game, edition)
    for w in info.warnings:
        say("warning: " + w)
    if info.problems:
        for pr in info.problems:
            say("PROBLEM: " + pr)
        return res.fail("Nothing was changed.", log)
    if info.overridable:
        for pr in info.overridable:
            say(("overridden by --force: " if force else "PROBLEM: ") + pr)
        if not force:
            return res.fail("Nothing was changed.", log)
    if not fnv_mut.on("ignore_running") and running():
        return res.fail("Fallout: New Vegas is running. Close the game (and the launcher) and run the installer again. Nothing was changed.", log)

    plugins = game / "Data" / "NVSE" / "Plugins"
    entries = manifest["files"]
    prior = load_record(game)
    for e in entries:
        t = _p(game, e["dest"])
        if t.is_dir() or t.is_symlink():
            return res.fail(f"{t} is a folder or a link; refusing to write through it. Nothing was changed.", log)
        if not fnv_mut.on("skip_inside_check") and not inside(game, t.parent):
            return res.fail(f"{t.parent} leads outside the game folder (a linked folder?). Nothing was changed.", log)

    prior_files = (prior or {}).get("files", {})
    originals: Dict[str, dict] = dict((prior or {}).get("originals", {}))
    to_backup: List[str] = []
    plan = []
    for e in entries:
        t = _p(game, e["dest"])
        keep = bool(e.get("keep_existing")) and not fnv_mut.on("overwrite_ini")
        exists = t.is_file()
        if exists and keep:
            plan.append((e, "keep"))
            if e["dest"] not in originals and e["dest"] not in prior_files:
                to_backup.append(e["dest"])   # the player's own ini: restored on uninstall
            continue
        if exists and fnv_manifest.sha256_file(t) == e["sha256"]:
            plan.append((e, "same"))
            if e["dest"] not in prior_files and e["dest"] not in originals:
                to_backup.append(e["dest"])
            continue
        plan.append((e, "replace" if exists else "new"))
        if exists:
            ours_unmodified = e["dest"] in prior_files and fnv_manifest.sha256_file(t) == prior_files[e["dest"]]["sha256"]
            if not ours_unmodified:
                to_backup.append(e["dest"])
    say(f"Installing release {fnv_manifest.get_version(manifest)} into {plugins}")
    for e, what in plan:
        say(f"  {what:8} {e['dest']}")
    if dry_run:
        say("Dry run: nothing was changed.")
        return res

    created_dirs = list((prior or {}).get("created_dirs", []))
    for rel in ("Data/NVSE", PLUGINS):
        d = _p(game, rel)
        if not d.exists():
            created_dirs.append(rel)
    mg = mygames if mygames is not None else my_games_dir()
    base = game / RECORD_DIR / BACKUPS
    bid = _new_backup_id(base)
    bdir = base / bid
    new_files = {e["dest"]: {"sha256": e["sha256"], "keep_existing": bool(e.get("keep_existing"))} for e in entries}
    undo: List[Callable[[], None]] = []
    try:
        meta = _backup(game, bdir, to_backup, mg)
        for rel in to_backup:
            originals[rel] = {"backup": f"{BACKUPS}/{bid}/files/{rel}", "sha256": fnv_manifest.sha256_file(_p(game, rel))}
        say(f"Backed up {len(meta['entries'])} file(s) to {bdir}")
        rec = {"schema": 1, "state": "installing", "version": fnv_manifest.get_version(manifest),
               "installed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "game_version": fnv_pe.format_version(info.exe_version), "files": new_files, "originals": originals,
               "created_dirs": created_dirs, "backup_ids": list((prior or {}).get("backup_ids", [])) + [bid]}
        plugins.mkdir(parents=True, exist_ok=True)
        _write_record(game, rec)
        for e, what in plan:
            if what in ("keep", "same"):
                continue
            t = _p(game, e["dest"])
            old = None
            if t.is_file():
                old = t.with_name(t.name + ".fnvcoop-old")
                shutil.copy2(t, old)
            if old is not None:
                undo.append(lambda t=t, old=old: fnv_ini.replace_retry(old, t))
            else:
                undo.append(lambda t=t: t.unlink() if t.exists() else None)
            copy_file(Path(release_root) / e["src"], t)
        for e, what in plan:
            t = _p(game, e["dest"])
            if what != "keep" and not fnv_mut.on("no_verify_copy") and fnv_manifest.sha256_file(t) != e["sha256"]:
                raise OSError(f"{e['dest']} does not match after copying")
        rec["state"] = "installed"
        _write_record(game, rec)
    except Exception as ex:   # noqa: BLE001  (anything: disk full, locked file, permission): put everything back
        say(f"Install failed: {ex}")
        if not fnv_mut.on("no_rollback"):
            for u in reversed(undo):
                try:
                    u()
                except OSError:
                    pass
            if prior is not None:
                _write_record(game, prior)
            else:
                for f in (record_path(game),):
                    if f.exists():
                        f.unlink()
            for rel in reversed(created_dirs):
                if rel not in (prior or {}).get("created_dirs", []):
                    _rmdir_if_empty(_p(game, rel))
            shutil.rmtree(bdir, ignore_errors=True)
            _rmdir_if_empty(base)
            _rmdir_if_empty(game / RECORD_DIR)
            for old in plugins.glob("*.fnvcoop-old"):
                old.unlink()
        hint = ("Windows did not allow the change. Right-click Install.cmd and choose 'Run as administrator' (a game under Program Files can need it), "
                "and close programs that may hold the files open." if isinstance(ex, PermissionError)
                else "Close other programs that may hold the files and try again.")
        return res.fail("Everything was put back as it was. " + hint, log)
    for old in plugins.glob("*.fnvcoop-old"):
        try:
            old.unlink()
        except OSError:
            pass
    say("Installed. Start the launcher to host or join a game.")
    return res


def uninstall(game, *, keep_backups: bool = False, remove_logs: bool = False, dry_run: bool = False,
              log: Optional[Callable[[str], None]] = None, running: Optional[Callable[[], bool]] = None) -> Result:
    running = running or fnv_find.game_is_running
    game = Path(game)
    res = Result()
    say = lambda m: res.say(m, log)  # noqa: E731
    rec = load_record(game)
    if rec is None:
        say("The co-op plugin was not installed by this installer (no FNVCoOp\\install.json). Nothing to do.")
        return res
    if not fnv_mut.on("ignore_running") and running():
        return res.fail("Fallout: New Vegas is running. Close the game and try again. Nothing was changed.", log)
    plugins = game / "Data" / "NVSE" / "Plugins"
    stamp = _new_backup_id(game / RECORD_DIR / REMOVED)
    removed_dir = game / RECORD_DIR / REMOVED / stamp
    files: Dict[str, dict] = rec.get("files", {})
    originals: Dict[str, dict] = rec.get("originals", {})
    actions = []
    for rel, info in sorted(files.items()):
        t = _p(game, rel)
        if not t.is_file():
            continue
        if info.get("keep_existing"):
            actions.append(("restore" if rel in originals else "delete", rel))
        elif fnv_manifest.sha256_file(t) == info["sha256"] or fnv_mut.on("uninstall_delete_modified"):
            actions.append(("delete", rel))
        else:
            actions.append(("keep-copy", rel))
    for rel in originals:
        if not any(a[1] == rel for a in actions) and not _p(game, rel).is_file():
            actions.append(("restore", rel))
    for kind, rel in actions:
        say(f"  {kind:9} {rel}")
    if dry_run:
        say("Dry run: nothing was changed.")
        return res
    if not fnv_mut.on("uninstall_keep_files"):
        for kind, rel in actions:
            t = _p(game, rel)
            if kind == "keep-copy":
                dst = removed_dir.joinpath(*rel.split("/"))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(t, dst)
                say(f"  {rel} was changed after install; a copy is in {removed_dir}")
            if kind in ("delete", "keep-copy", "restore") and t.is_file():
                t.unlink()
    if not fnv_mut.on("uninstall_no_restore"):
        for rel, o in originals.items():
            src = game / RECORD_DIR / Path(*o["backup"].split("/"))
            if not src.is_file():
                say(f"  warning: the backup of {rel} is missing; it cannot be restored")
                continue
            if fnv_manifest.sha256_file(src) != o["sha256"]:
                say(f"  warning: the backup of {rel} is damaged; it was not restored (kept at {src})")
                continue
            t = _p(game, rel)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, t)
            say(f"  restored  {rel} (your file from before the install)")
    if fnv_mut.on("uninstall_remove_foreign") and plugins.is_dir():
        for f in plugins.glob("*.dll"):
            f.unlink()
    if remove_logs:
        for n in RUNTIME_FILES:
            f = plugins / n
            if f.is_file():
                f.unlink()
        for n in RUNTIME_DIRS:
            d = plugins / n
            if d.is_dir() and inside(plugins, d):
                shutil.rmtree(d, ignore_errors=True)
    for rel in reversed(rec.get("created_dirs", [])):
        _rmdir_if_empty(_p(game, rel))
    base = game / RECORD_DIR / BACKUPS
    if not keep_backups:
        shutil.rmtree(base, ignore_errors=True)
    try:
        record_path(game).unlink()
    except OSError:
        pass
    _rmdir_if_empty(removed_dir)
    _rmdir_if_empty(game / RECORD_DIR / REMOVED)
    _rmdir_if_empty(game / RECORD_DIR)
    say("Uninstalled. Your saves were not touched." + (f" Backups are kept in {base}." if keep_backups else ""))
    return res


def status(game, log: Optional[Callable[[str], None]] = None) -> Result:
    game = Path(game)
    res = Result()
    rec = load_record(game)
    if rec is None:
        res.say("Not installed (no FNVCoOp\\install.json).", log)
        return res
    res.say(f"Release {rec.get('version', '?')}, installed {rec.get('installed_utc', '?')}, state {rec.get('state', '?')}", log)
    if rec.get("state") != "installed":
        res.fail("The last install did not finish. Run install again.", log)
    for rel, info in sorted(rec.get("files", {}).items()):
        t = _p(game, rel)
        if not t.is_file():
            res.fail(f"  MISSING   {rel}", log)
        elif info.get("keep_existing"):
            res.say(f"  user file {rel}", log)
        elif fnv_manifest.sha256_file(t) != info["sha256"]:
            res.fail(f"  CHANGED   {rel}", log)
        else:
            res.say(f"  ok        {rel}", log)
    return res


def restore_inis(game, mygames: Optional[Path] = None, backup_id: Optional[str] = None,
                 log: Optional[Callable[[str], None]] = None) -> Result:
    """Copy the Fallout.ini / FalloutPrefs.ini that were saved at install time back to where they came from."""
    game = Path(game)
    res = Result()
    base = game / RECORD_DIR / BACKUPS
    ids = sorted(p.name for p in base.glob("*") if (p / "backup.json").is_file()) if base.is_dir() else []
    if backup_id:
        ids = [backup_id] if backup_id in ids else []
    if not ids:
        return res.fail("There is no INI backup to restore.", log)
    mg = mygames if mygames is not None else my_games_dir()
    # the oldest backup is the closest to the player's original INIs
    meta = json.loads((base / ids[0] / "backup.json").read_text(encoding="utf-8"))
    n = 0
    for ent in meta["entries"]:
        if ent["kind"] != "game-ini":
            continue
        src = base / ids[0] / Path(*ent["backup"].split("/"))
        dst = (mg if "/mygames/" in ent["backup"] else game) / Path(ent["backup"]).name
        if not src.is_file() or fnv_manifest.sha256_file(src) != ent["sha256"]:
            res.fail(f"  backup of {dst.name} is missing or damaged", log)
            continue
        if dst.is_file():
            shutil.copy2(dst, dst.with_name(dst.name + ".before-restore"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        res.say(f"  restored {dst}", log)
        n += 1
    if n == 0 and res.ok:
        res.say("The backup holds no game INIs.", log)
    return res


def default_release_root() -> Optional[Path]:
    return fnv_manifest.find_release_root(Path(__file__))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("find", "check", "install", "status", "uninstall", "restore-inis"):
        s = sub.add_parser(name)
        if name != "find":
            s.add_argument("--game", help="folder that holds FalloutNV.exe (default: find it)")
        if name in ("install", "uninstall"):
            s.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
            s.add_argument("--yes", action="store_true", help="do not ask for confirmation")
        if name == "install":
            s.add_argument("--force", action="store_true", help="install although the game or xNVSE version is not the supported one")
            s.add_argument("--release", help="release folder that holds manifest.json (default: this tool's release folder)")
        if name == "uninstall":
            s.add_argument("--keep-backups", action="store_true", help="keep FNVCoOp\\backups in the game folder")
            s.add_argument("--remove-logs", action="store_true", help="also delete nvcoop.log and the other files the plugin wrote while running (kept by default: they help bug reports)")
    a = ap.parse_args(argv)
    interactive = sys.stdin is not None and sys.stdin.isatty()
    ask = input if interactive else None
    if a.cmd == "find":
        c = fnv_find.candidates()
        for x in c:
            print(f"{x.path}   ({x.source})")
        if not c:
            print("No Fallout: New Vegas install found.")
        return 0 if c else 1
    cand = pick_game(a.game, ask=ask, assume_yes=getattr(a, "yes", False))
    if cand is None:
        return 2
    game = cand.path
    if a.cmd == "check":
        _info, r = check(game, cand.edition, log=print)
        return 0 if r.ok else 1
    if a.cmd == "status":
        return 0 if status(game, log=print).ok else 1
    if a.cmd == "restore-inis":
        return 0 if restore_inis(game, log=print).ok else 1
    if a.cmd == "uninstall":
        print(f"Game folder: {game}")
        if not a.yes and not a.dry_run and ask and ask("Uninstall the co-op plugin? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return 1
        return 0 if uninstall(game, keep_backups=a.keep_backups, remove_logs=a.remove_logs, dry_run=a.dry_run, log=print).ok else 1
    root = Path(a.release) if a.release else default_release_root()
    if root is None:
        print("This is not a release folder (manifest.json not found). Unzip the release and run Install.cmd from there, "
              "or pass --release <folder>.")
        return 2
    print(f"Game folder: {game}   ({cand.source})")
    if not a.yes and not a.dry_run and ask and ask("Install the co-op plugin here? [Y/n] ").strip().lower() in ("n", "no"):
        print("Cancelled.")
        return 1
    r = install(game, root, force=a.force, dry_run=a.dry_run, edition=cand.edition, log=print)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
