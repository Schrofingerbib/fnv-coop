#!/usr/bin/env python3
"""Hot DLL swap: put a freshly built core DLL into running game copies without restarting them.

    python -I tools/runner/run.py swap --instances 0,1 [--dll out/L1split/nvcoop_mod.dll]
    python -I tools/runner/hotswap.py --instances 0,1            (the same, without run.py)

Needs the SPLIT build in the running copies: nvcoop.dll is the loader and stays in the game, nvcoop_mod.dll is the
core that the loader can unload and load again (docs/tools/T3a-loader.md, T3b-state.md). The loader already copies
the core to nvcoop_cache\\ before it maps it, so the build output can be replaced while the game runs.

Per copy: (1) ask dev.status where its core file is and which build runs; (2) copy the new DLL beside it under a
temporary name and rename it over the core file (the loader never sees a half-written file); (3) call dev.reload
(unload, save state, map the new file, restore state); (4) wait until the pipe answers again; (5) check that the loader
now runs exactly the DLL we copied (build id = CRC-32 and size, the loader reports it as dev.status buildId; an older
loader without that field is checked by reading its cache copy instead), the generation went up, nothing was rolled
back, and no reload participant failed or leaked; the data files nvcoop_*.json / .txt that the build left beside the DLL are
copied too when they differ (the loader mirrors them into its cache on every reload; --no-data skips this); (6) wait for a running co-op session to come back (the transport is
closed and reopened by a reload, peers rejoin).

All copies are checked first and nothing is touched unless every copy can hot reload. A swap stops at the first copy
that fails (the others are reported as skipped).

Exit 0: every copy runs the new build (or already did). Exit 1: a copy failed (the report says which check). Exit 2: refused
before anything was changed (no pipe, the copy runs the monolithic nvcoop.dll or a loader without dev.reload, the DLL is not
a core, the core file is outside the instance folder). JSON report on stdout, progress on stderr.

Not done here: a change to the LOADER itself (nvcoop.dll) needs one normal restart; the loader is never unloaded.
It does not launch, stop or lease a game, and it never touches a process: it only talks to the bridge pipe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import instances  # noqa: E402
from nvbridge import ConnectionLost, GameCrashed  # noqa: E402

REPO = HERE.parents[1]
DEFAULT_CORE_DLL = REPO / "out" / "L1split" / "nvcoop_mod.dll"
METHOD_NOT_FOUND = -32601
CORE_EXPORT = b"NvCore_Init"
TMP_SUFFIX = ".swap"   # not ".dll": xNVSE loads every *.dll in the plugins folder, and the loader's watcher looks at the core only
DATA_SUFFIXES = (".json", ".txt")


class Refused(Exception):
    """Nothing was changed and nothing will be: the caller exits 2."""


class SwapFailed(Exception):
    """One copy did not end up on the new build: the caller exits 1. rep: what is known about that copy so far."""

    def __init__(self, msg: str, rep: Optional[Dict[str, Any]] = None):
        super().__init__(msg)
        self.rep = rep


# ----------------------------------------------------------------------------------- build id
def build_id_of(path: Path) -> str:
    """CRC-32 (8 hex digits), a dash and the size in bytes. The loader computes the same text for the file it maps
    (BuildIdOfFile in plugin/loader/loader_host.cpp), so equal ids mean equal bytes for practical purposes."""
    crc, size = 0, 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return f"{crc & 0xFFFFFFFF:08x}-{size}"


def core_dll_problem(path: Path) -> str:
    """"" when the file looks like a core DLL of the split build, else the reason it does not. The core exports
    NvCore_Init (loader_abi.h); the loader nvcoop.dll and the monolithic build do not, and loading either as the core
    would leave the copy without a core."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return f"cannot read {path}: {e}"
    if len(data) < 1024 or data[:2] != b"MZ":
        return f"{path} is not a DLL"
    if CORE_EXPORT not in data:
        return f"{path} has no NvCore_Init export: it is not the split build's core (nvcoop_mod.dll); is it the loader or the monolithic nvcoop.dll?"
    return ""


# ----------------------------------------------------------------------------------- reading a copy
def _is_method_missing(exc: Exception) -> bool:
    return getattr(exc, "code", None) == METHOD_NOT_FOUND or "unknown method" in str(exc).lower()


def read_status(nv) -> Dict[str, Any]:
    """dev.status of one copy. Raises Refused when the copy cannot hot reload at all."""
    try:
        st = nv.call("dev.status", timeout_s=10)
    except Exception as e:
        if _is_method_missing(e):
            raise Refused("this copy has no dev.reload: it runs the monolithic nvcoop.dll or an old loader. Provision it with "
                          "--plugin-dll out/L1split/nvcoop.dll --core-dll out/L1split/nvcoop_mod.dll and restart it once") from e
        raise Refused(f"dev.status failed: {e}") from e
    if not isinstance(st, dict) or "generation" not in st:
        raise Refused(f"dev.status answered something that is not a loader status: {st!r}")
    return st


def running_build_id(st: Dict[str, Any]) -> Optional[str]:
    """The build id of the running core: the loader's own report, or (older loader) the CRC of the cache copy it mapped."""
    bid = st.get("buildId")
    if isinstance(bid, str) and bid:
        return bid
    cache = st.get("cachePath")
    if st.get("loaded") and cache:
        try:
            return build_id_of(Path(cache))
        except OSError:
            return None
    return None


def check_core_path(st: Dict[str, Any], inst_dir: Path) -> Path:
    """The core file the copy loads, as a Path, if it sits inside the instance folder. Refused otherwise: this tool
    overwrites that file, and it must never be one that belongs to another install."""
    raw = st.get("corePath")
    if not isinstance(raw, str) or not raw:
        raise Refused("dev.status names no corePath")
    dest = Path(raw)
    try:
        inside = os.path.commonpath([os.path.normcase(str(dest.resolve())), os.path.normcase(str(inst_dir.resolve()))]) == \
            os.path.normcase(str(inst_dir.resolve()))
    except ValueError:
        inside = False
    if not inside:
        raise Refused(f"the copy's core file {dest} is outside its instance folder {inst_dir}; not overwriting it")
    return dest


# ----------------------------------------------------------------------------------- decisions (kept small so tests can break them)
def needs_swap(before_id: Optional[str], expected_id: str, force: bool) -> bool:
    """False when the copy already runs these bytes: a reload would change nothing."""
    return force or before_id != expected_id


def verify_after(before: Dict[str, Any], after: Dict[str, Any], expected_id: str, before_id: Optional[str]) -> Tuple[bool, str, str]:
    """(ok, how the build was proven, detail). The proof is the id of the running core, never just 'dev.reload answered'."""
    last = after.get("last") if isinstance(after.get("last"), dict) else {}
    probs: List[str] = []
    if not after.get("loaded"):
        probs.append("the loader reports no core loaded")
    if last.get("coreDead"):
        probs.append("core is dead after the reload")
    if last.get("rolledBack"):
        probs.append(f"the new core failed to start and the old one runs again ({last.get('error')})")
    elif last.get("ok") is False:
        probs.append(f"last reload failed: {last.get('error')}")
    gb, ga = int(before.get("generation", -1)), int(after.get("generation", -1))
    if ga <= gb:
        probs.append(f"generation did not advance ({gb} -> {ga})")
    now_id = running_build_id(after)
    how = "loader buildId" if after.get("buildId") else "cache copy crc"
    if now_id is None:
        probs.append("cannot tell which build runs (no buildId and the cache copy is unreadable)")
    elif now_id != expected_id:
        probs.append(f"running build {now_id} is not the one that was copied ({expected_id})")
    elif before_id is not None and before_id == expected_id and ga > gb:
        how += " (unchanged, forced)"
    return (not probs, how, "; ".join(probs) or f"build {now_id}, generation {gb} -> {ga}")


def reload_state_problems(nv) -> str:
    """Failures or leaks the core's reload registry reports. Best effort: a core without reload.state says nothing."""
    try:
        rs = nv.call("reload.state", timeout_s=10)
    except Exception:
        return ""
    if not isinstance(rs, dict):
        return ""
    bits = []
    if rs.get("failures"):
        bits.append(f"failures={rs['failures']}")
    if rs.get("leaks"):
        bits.append(f"leaks={rs['leaks']}")
    if rs.get("chainedHooks"):
        bits.append(f"chainedHooks={rs['chainedHooks']}")
    return "; ".join(bits)


def coop_running(nv) -> Optional[bool]:
    """True/False when the copy has a co-op session status, None when it has none (co-op off in this ini)."""
    try:
        st = nv.call("coop.status", timeout_s=10)
    except Exception:
        return None
    return bool(st.get("running")) if isinstance(st, dict) else None


def wait_coop(nv, settle_s: float, sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> bool:
    """Wait up to settle_s for a session that ran before the reload to run again."""
    t0 = now()
    while True:
        if coop_running(nv):
            return True
        if now() - t0 >= settle_s:
            return False
        sleep(0.5)


# ----------------------------------------------------------------------------------- the file swap
def install_dll(src: Path, dest: Path, expected_id: str, sleep: Callable[[float], None] = time.sleep,
                replace: Optional[Callable[[str, str], None]] = None) -> None:
    """Copy src to dest through a temporary name in the same folder and rename it over dest, so a reader (the loader's
    file watcher, the loader's own copy) sees the old file or the whole new one. Verifies the copy before and after."""
    tmp = dest.with_name(dest.name + TMP_SUFFIX)
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                chunk = fi.read(1 << 20)
                if not chunk:
                    break
                fo.write(chunk)
        if build_id_of(tmp) != expected_id:
            raise SwapFailed(f"the copy {tmp} differs from {src} (disk full or file changed while copying)")
        last: Optional[Exception] = None
        for _ in range(50):   # the loader holds the file for a moment while it copies it to its cache
            try:
                (replace or os.replace)(str(tmp), str(dest))
                last = None
                break
            except PermissionError as e:
                last = e
                sleep(0.1)
        if last is not None:
            raise SwapFailed(f"cannot replace {dest}: {last}")
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    if build_id_of(dest) != expected_id:
        raise SwapFailed(f"{dest} does not hold the new build after the rename")


def data_dir_of(st: Dict[str, Any], dest: Path, inst_dir: Path) -> Path:
    """Where the loader reads nvcoop_*.json / .txt from (configDir of dev.status, else the core's folder). Never outside the instance."""
    raw = st.get("configDir")
    if isinstance(raw, str) and raw:
        d = Path(raw)
        try:
            if os.path.commonpath([os.path.normcase(str(d.resolve())), os.path.normcase(str(inst_dir.resolve()))]) ==                     os.path.normcase(str(inst_dir.resolve())):
                return d
        except ValueError:
            pass
    return dest.parent


def stale_data_files(dll: Path, target_dir: Path) -> List[Path]:
    """The nvcoop_*.json / .txt data files that the build put beside the core DLL and that differ from the copy's own.
    `provision` installs the same files; the loader mirrors them into its cache on every reload, so a new rules file
    reaches the new core without a restart. nvcoop.ini is not one of them: it is written per instance."""
    out: List[Path] = []
    for f in sorted(Path(dll).parent.glob("nvcoop_*")):
        if not f.is_file() or f.suffix.lower() not in DATA_SUFFIXES:
            continue
        dst = target_dir / f.name
        try:
            if not dst.is_file() or dst.read_bytes() != f.read_bytes():
                out.append(f)
        except OSError:
            out.append(f)
    return out


def sync_data_files(files: List[Path], target_dir: Path) -> List[str]:
    done: List[str] = []
    for f in files:
        dst = target_dir / f.name
        data = f.read_bytes()
        try:
            dst.write_bytes(data)
        except PermissionError:   # a read-only ini-style file: clear the flag, as the loader's own config copy does
            os.chmod(dst, 0o666)
            dst.write_bytes(data)
        done.append(f.name)
    return done


# ----------------------------------------------------------------------------------- one copy
def connect_default(instance: int, wait_s: float):
    from nvbridge import NvBridge
    return NvBridge.connect(instance=instance, wait_s=wait_s)


def reconnect(connect: Callable[[int, float], Any], instance: int, wait_s: float, sleep: Callable[[float], None],
              now: Callable[[], float]) -> Any:
    """Open the pipe again after a dropped call and wait until the loader answers dev.status."""
    t0 = now()
    last: Optional[Exception] = None
    while now() - t0 < wait_s:
        try:
            nv = connect(instance, 1.0)
            read_status(nv)
            return nv
        except Exception as e:  # not up yet (no pipe, or the loader is still starting)
            last = e
            sleep(0.5)
    raise SwapFailed(f"the pipe of instance {instance} did not answer again within {wait_s:.0f} s ({last}); did the game crash? check nvcoop.log and crash dumps")


class Plan:
    def __init__(self, instance: int, nv, status: Dict[str, Any], dest: Path, before_id: Optional[str], coop_before: Optional[bool]):
        self.instance, self.nv, self.status, self.dest = instance, nv, status, dest
        self.before_id, self.coop_before = before_id, coop_before


def swap_one(plan: Plan, src: Path, expected_id: str, *, reload_timeout_s: float, wait_s: float, force: bool,
             connect: Callable[[int, float], Any], sleep: Callable[[float], None], now: Callable[[], float],
             log: Callable[[str], None], inst_dir: Optional[Path] = None, sync_data: bool = True) -> Dict[str, Any]:
    i, nv = plan.instance, plan.nv
    rep: Dict[str, Any] = {"instance": i, "core": str(plan.dest), "buildBefore": plan.before_id,
                           "generationBefore": plan.status.get("generation")}
    data_dir = data_dir_of(plan.status, plan.dest, inst_dir) if inst_dir is not None else plan.dest.parent
    stale = stale_data_files(src, data_dir) if sync_data else []
    if not needs_swap(plan.before_id, expected_id, force) and not stale:
        rep.update(status="unchanged", buildAfter=plan.before_id, generationAfter=plan.status.get("generation"),
                   detail="already runs this build; nothing to do (--force reloads anyway)")
        log(f"instance {i}: already on build {expected_id}")
        return rep
    t0 = now()
    try:
        install_dll(src, plan.dest, expected_id, sleep=sleep)
        if stale:
            rep["dataFiles"] = sync_data_files(stale, data_dir)
        log(f"instance {i}: copied {expected_id} over {plan.dest.name}" + (f" and {len(stale)} data file(s)" if stale else "") + "; reloading")
        t1 = now()
        reply: Any = None
        try:
            # timeoutMs lifts the bridge's own 30 s wait for a main-thread request; timeout_s is our wait for the answer.
            reply = nv.call("dev.reload", {"reason": "hotswap", "timeoutMs": int(reload_timeout_s * 1000)}, timeout_s=reload_timeout_s)
        except GameCrashed as e:
            raise SwapFailed(f"the game process died during the reload: {e}") from e
        except ConnectionLost as e:
            log(f"instance {i}: lost the pipe during the reload ({e}); waiting for it")
            plan.nv = nv = reconnect(connect, i, wait_s, sleep, now)
        except Exception as e:
            hint = " (another client holds the instance lease; release it or run the swap from that job)" if getattr(e, "code", None) == -32011 else ""
            raise SwapFailed(f"dev.reload refused: {e}{hint}. The old core still runs; the new DLL is already in place for the next try") from e
        after = read_status(nv)
        ok, how, detail = verify_after(plan.status, after, expected_id, plan.before_id)
        rep.update(msReload=round((now() - t1) * 1000), msTotal=round((now() - t0) * 1000), buildAfter=running_build_id(after),
                   generationAfter=after.get("generation"), verifiedBy=how, reply=reply)
        if not ok:
            raise SwapFailed(detail)
        problems = reload_state_problems(nv)
        if problems:
            raise SwapFailed(f"the core came up but its reload registry reports: {problems}")
    except SwapFailed as e:
        rep.update(status="failed", detail=str(e))
        e.rep = rep
        raise
    except Refused as e:   # dev.status unreadable after the reload
        rep.update(status="failed", detail=str(e))
        raise SwapFailed(str(e), rep) from e
    rep.update(status="swapped", detail=detail)
    log(f"instance {i}: now on build {rep['buildAfter']}, generation {rep['generationAfter']}, {rep['msReload']} ms")
    return rep


# ----------------------------------------------------------------------------------- all copies
def run_swap(instance_ids: List[int], dll: Path, *, connect: Callable[[int, float], Any] = connect_default,
             inst_dir_of: Callable[[int], Path] = instances.instance_dir, force: bool = False, dry_run: bool = False, sync_data: bool = True,
             reload_timeout_s: float = 90.0, wait_s: float = 60.0, settle_s: float = 20.0,
             sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic,
             log: Callable[[str], None] = lambda s: print(s, file=sys.stderr)) -> Tuple[int, Dict[str, Any]]:
    report: Dict[str, Any] = {"ok": False, "dll": str(dll), "instances": []}
    plans: List[Plan] = []
    conns: List[Any] = []
    try:
        # ---- preflight: nothing is changed until every copy has been checked ----
        try:
            problem = core_dll_problem(dll)
            if problem:
                raise Refused(problem)
            expected = build_id_of(dll)
            report["buildId"] = expected
            for i in instance_ids:
                try:
                    nv = connect(i, 3.0)
                except Exception as e:
                    raise Refused(f"instance {i}: cannot connect to the bridge pipe: {e}") from e
                conns.append(nv)
                try:
                    st = read_status(nv)
                    inst_dir = inst_dir_of(i)
                    if not Path(inst_dir).is_dir():
                        raise Refused(f"no instance folder {inst_dir}")
                    dest = check_core_path(st, Path(inst_dir))
                    if Path(dll).resolve() == dest.resolve():
                        raise Refused(f"--dll is the copy's own core file {dest}; build to out/L1split and pass that")
                except Refused as e:
                    raise Refused(f"instance {i}: {e}") from e
                plans.append(Plan(i, nv, st, dest, running_build_id(st), coop_running(nv)))
        except Refused as e:
            report.update(refused=str(e))
            log(f"refused: {e}")
            return 2, report
        if dry_run:
            for p in plans:
                report["instances"].append({"instance": p.instance, "core": str(p.dest), "buildBefore": p.before_id,
                                            "generationBefore": p.status.get("generation"),
                                            "status": "would swap" if needs_swap(p.before_id, expected, force) or
                                            (sync_data and stale_data_files(dll, data_dir_of(p.status, p.dest, Path(inst_dir_of(p.instance))))) else "unchanged"})
            report["ok"] = True
            return 0, report
        # ---- swap one copy after the other; stop at the first failure ----
        failed = False
        for p in plans:
            if failed:
                report["instances"].append({"instance": p.instance, "status": "skipped", "detail": "an earlier copy failed"})
                continue
            try:
                report["instances"].append(swap_one(p, dll, expected, reload_timeout_s=reload_timeout_s, wait_s=wait_s, force=force,
                                                    connect=connect, sleep=sleep, now=now, log=log,
                                                    inst_dir=Path(inst_dir_of(p.instance)), sync_data=sync_data))
            except SwapFailed as e:
                failed = True
                report["instances"].append(e.rep or {"instance": p.instance, "status": "failed", "detail": str(e)})
                log(f"instance {p.instance}: FAILED: {e}")
            if p.nv not in conns:
                conns.append(p.nv)
        # ---- a co-op session that ran before must run again (the reload closed and reopened the transport) ----
        if not failed:
            for p in plans:
                row = next(r for r in report["instances"] if r["instance"] == p.instance)
                if row["status"] == "swapped" and p.coop_before:
                    back = wait_coop(p.nv, settle_s, sleep, now)
                    row["coopBack"] = back
                    if not back:
                        row.update(status="failed", detail=f"co-op session of instance {p.instance} did not run again within {settle_s:.0f} s of the reload")
                        log(f"instance {p.instance}: FAILED: {row['detail']}")
                        failed = True
        report["ok"] = not failed
        return (0 if report["ok"] else 1), report
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass


# ----------------------------------------------------------------------------------- command line
def instance_list(text: str) -> List[int]:
    try:
        ids = [int(t) for t in text.split(",") if t.strip() != ""]
    except ValueError:
        raise argparse.ArgumentTypeError(f"--instances wants numbers like 0,1 (got {text!r})")
    if not ids or len(set(ids)) != len(ids) or any(i < 0 for i in ids):
        raise argparse.ArgumentTypeError(f"--instances wants distinct numbers like 0,1 (got {text!r})")
    return ids


def add_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--instances", type=instance_list, default=[0, 1], help="running copies to swap, e.g. 0,1 (default 0,1)")
    ap.add_argument("--dll", default=str(DEFAULT_CORE_DLL), help="the freshly built core (default out/L1split/nvcoop_mod.dll)")
    ap.add_argument("--force", action="store_true", help="reload even when the copy already runs this build")
    ap.add_argument("--no-data", action="store_true", help="do not copy nvcoop_*.json / .txt data files from the DLL's folder")
    ap.add_argument("--dry-run", action="store_true", help="check every copy and report what would happen; change nothing")
    ap.add_argument("--reload-timeout-s", type=float, default=90.0, help="how long to wait for dev.reload to answer")
    ap.add_argument("--wait-s", type=float, default=60.0, help="how long to wait for the pipe after a dropped call")
    ap.add_argument("--settle-s", type=float, default=20.0, help="how long a co-op session may take to run again (0 = do not wait)")
    ap.add_argument("--out", default="", help="also write the JSON report to this file")


def run_cli(a: argparse.Namespace) -> int:
    code, rep = run_swap(a.instances, Path(a.dll), force=a.force, dry_run=a.dry_run, sync_data=not a.no_data, reload_timeout_s=a.reload_timeout_s,
                         wait_s=a.wait_s, settle_s=a.settle_s)
    text = json.dumps(rep, indent=2)
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    return code


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(ap)
    return run_cli(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
