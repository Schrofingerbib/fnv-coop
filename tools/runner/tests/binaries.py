"""Test binaries: copied once per test process from out/L1 into a private temp dir, so rebuilds of the
deploy dir neither race with nor are locked by running tests. A missing exe is a hard failure unless
the run is explicitly C++-free (env NVCOOP_NO_CPP=1)."""
import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path

OUT = Path(__file__).resolve().parents[3] / "out" / "L1"
NO_CPP = os.environ.get("NVCOOP_NO_CPP") == "1"
_tmp = None


def tmpdir() -> Path:
    global _tmp
    if _tmp is None:
        _tmp = Path(tempfile.mkdtemp(prefix="nvcoop-tests-"))
        atexit.register(shutil.rmtree, str(_tmp), True)
    return _tmp


def get(name: str) -> Path:
    """Private copy of out/L1/<name>.exe. Raises FileNotFoundError (test ERROR) if it was not built."""
    if NO_CPP:
        raise unittest.SkipTest("NVCOOP_NO_CPP=1")
    src = OUT / f"{name}.exe"
    if not src.exists():
        raise FileNotFoundError(f"{src} missing - run tools\\runner\\build_l1.cmd (set NVCOOP_NO_CPP=1 to skip C++ tests explicitly)")
    dst = tmpdir() / src.name
    if not dst.exists():
        shutil.copy2(src, dst)
    return dst
