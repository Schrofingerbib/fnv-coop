#!/usr/bin/env python3
"""prove.py piece for `public_tree_build`: export the public tree to a scratch folder, scan it, run its quick tests, build it. Never pushes.

    python -I tools/proof/drivers/public_tree_build.py [--work DIR] [--nvse DIR] [--skip-build] [--skip-tests] [--keep]

  1. export   `tools/publish/export_public.py --dest <work>/export` (outside this checkout; nothing is initialised as a git repo or pushed);
              its own scan must print SCAN CLEAN
  2. rescan   the export is scanned again with the same patterns (personal paths, mail address, owner handle, em dash, DRM wording) and
              must hold no .git folder, no out/ or build/ folder, no dll or exe: what a push would publish carries nothing of ours
  3. tests    `python -I tools/test_all.py --quick` run INSIDE the export (its own copy of every tool; the exporter's skip patch applies)
  4. build    `tools/build/build.py --target plugin/all --label public` inside the export, with the xNVSE sources given by --nvse
              (default third_party/NVSE of this checkout; missing: exit 3, BLOCKED), then the built nvcoop.dll must be a 32-bit plugin
              DLL (tools/installer/package_release.check_dll)
  5. the scratch folder is removed (--keep leaves it for a look).
This is a build check only. Publishing stays a separate step with its own identity check. Exit 0 all pass, 1 a check failed, 3 xNVSE missing.
The tests (no build, fake runners) are in tools/proof/drivers/tests.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
import drv_common as D  # noqa: E402

PY = [sys.executable, "-I", "-X", "utf8"]
Call = Callable[[List[str], Path, float], Tuple[int, str]]
FORBIDDEN_DIRS = (".git", "out", "build")
FORBIDDEN_SUFFIXES = (".dll", ".exe", ".pdb", ".obj", ".lib", ".esm", ".esp", ".fos")


def real_call(argv: List[str], cwd: Path, timeout: float) -> Tuple[int, str]:
    try:
        cp = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout:.0f} s"
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def tail(text: str, n: int = 3) -> str:
    return " | ".join(l.strip() for l in text.strip().splitlines()[-n:])[:200]


def scan_hits(dest: Path) -> List[str]:
    """The exporter's own scan over a folder (imported, so there is one list of patterns), plus what must not be in a public tree."""
    sys.path.insert(0, str(REPO / "tools" / "publish"))
    import export_public  # noqa: WPS433
    hits = list(export_public.scan(dest))
    if D.MUTANT == "no_forbidden_scan":
        return hits
    for p in dest.rglob("*"):
        rel = p.relative_to(dest)
        top = rel.parts[0] if rel.parts else ""
        if top in FORBIDDEN_DIRS:
            hits.append(f"{rel.as_posix()}: a public tree must not hold {top}/")
        elif p.is_file() and p.suffix.lower() in FORBIDDEN_SUFFIXES:
            hits.append(f"{rel.as_posix()}: binary or game file in the public tree")
    return hits


def run(a, call: Call = real_call, out: Callable[[str], None] = lambda s: print(s, flush=True),
        rescan: Callable[[Path], List[str]] = scan_hits, dll_problems: Optional[Callable[[Path], List[str]]] = None) -> int:
    c = D.Checks(out)
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="fnv-public-"))
    work.mkdir(parents=True, exist_ok=True)
    export = work / "export"
    try:
        if REPO in export.resolve().parents or export.resolve() == REPO:
            return D.blocked_exit("the work folder must be outside this checkout", out)
        code, text = call(PY + [str(REPO / "tools" / "publish" / "export_public.py"), "--dest", str(export)], REPO, 600)
        if not c.check("export: export_public.py ran", code == 0, f"exit {code}: {tail(text)}"):
            return D.EXIT_FAIL
        c.check("export: its own scan says SCAN CLEAN", "SCAN CLEAN" in text, tail(text))
        hits = rescan(export)
        c.check("rescan: no personal path, address, owner handle, em dash, DRM wording, git folder or binary in the export", not hits,
                f"{len(hits)} hit(s), first: {hits[0] if hits else ''}")
        if not c.ok:
            return D.EXIT_FAIL
        if not a.skip_tests:
            code, text = call(PY + ["tools/test_all.py", "--quick"], export, 3000)
            c.check("tests: test_all.py --quick passes inside the export", code == 0, f"exit {code}: {tail(text)}")
        if not a.skip_build:
            nvse = Path(a.nvse) if a.nvse else REPO / "third_party" / "NVSE"
            if not (nvse / "nvse").is_dir():
                out(c.summary())
                return D.blocked_exit(f"xNVSE sources not found at {nvse} (pass --nvse DIR or clone xNVSE into third_party/NVSE)", out)
            code, text = call(PY + ["tools/build/build.py", "--target", "plugin/all", "--label", "public",
                                    f"--cmake-arg=-DFNVCOOP_NVSE_DIR={nvse.as_posix()}"], export, 3000)
            if c.check("build: plugin/all builds from the export", code == 0, f"exit {code}: {tail(text)}"):
                dlls = sorted(export.rglob("nvcoop.dll"))
                if c.check("build: nvcoop.dll was produced", bool(dlls), "no nvcoop.dll under the export"):
                    check = dll_problems or _dll_problems
                    probs = check(dlls[0])
                    c.check("build: nvcoop.dll is a 32-bit xNVSE plugin", not probs, "; ".join(probs)[:200])
        out(c.summary())
        return c.exit_code()
    finally:
        if not a.keep and not a.work:
            shutil.rmtree(work, ignore_errors=True)


def _dll_problems(path: Path) -> List[str]:
    sys.path.insert(0, str(REPO / "tools" / "installer"))
    import package_release  # noqa: WPS433
    return package_release.check_dll(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default="", help="scratch folder (default: a new temp folder, removed at the end); must be outside this checkout")
    ap.add_argument("--nvse", default="", help="xNVSE sources (default third_party/NVSE of this checkout)")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--keep", action="store_true")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
