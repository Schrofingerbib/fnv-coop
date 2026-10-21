"""nvbridge must import on a system without msvcrt / WinDLL and fail with a clear 'Windows only' message when the pipe is needed.

The module is loaded as if sys.platform were 'linux' (msvcrt and ctypes.WinDLL made unavailable), so the test means the same
on Linux and on Windows. Mutation twins at the end load a deliberately broken copy of the source; the check must reject it."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import ctypes

RUNNER = Path(__file__).resolve().parents[1]
SRC = RUNNER / "nvbridge.py"


def load_as_non_windows(source: str):
    """Exec `source` as a module on a pretend non-Windows system. Raises whatever the import raises."""
    spec = importlib.util.spec_from_loader("nvbridge_nonwin", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(SRC)
    saved_path = list(sys.path)
    try:
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(sys.modules, {"msvcrt": None}), \
                mock.patch.object(ctypes, "WinDLL", side_effect=AttributeError("no WinDLL"), create=True):
            exec(compile(source, str(SRC), "exec"), mod.__dict__)
    finally:
        sys.path[:] = saved_path
    return mod


def problems(source: str):
    """Everything wrong with `source` as a non-Windows import; empty means it behaves."""
    try:
        m = load_as_non_windows(source)
    except Exception as e:  # ImportError / AttributeError is the bug
        return [f"import failed: {type(e).__name__}: {e}"]
    out = []
    if m.WINDOWS:
        out.append("WINDOWS is true on a non-Windows system")
    for what, call in (("connect", lambda: m.NvBridge.connect(0)),
                       ("NvBridge()", lambda: m.NvBridge(object())),
                       ("_pid_alive", lambda: m._pid_alive(1))):
        try:
            call()
        except m.WindowsOnlyError as e:
            if "Windows only" not in str(e):
                out.append(f"{what}: message does not say Windows only: {e}")
        except Exception as e:
            out.append(f"{what}: raised {type(e).__name__} instead of WindowsOnlyError: {e}")
        else:
            out.append(f"{what}: did not raise")
    if not issubclass(m.WindowsOnlyError, m.BridgeError):
        out.append("WindowsOnlyError is not a BridgeError")
    # pure helpers keep working with no Windows at all
    if m.pipe_path(3) != r"\\.\pipe\nvcoop-3":
        out.append("pipe_path changed")
    return out


class PortableImport(unittest.TestCase):
    def test_imports_and_fails_clearly_off_windows(self):
        self.assertEqual(problems(SRC.read_text(encoding="utf-8")), [])

    def test_real_module_imports_here(self):
        sys.path.insert(0, str(RUNNER))
        try:
            import nvbridge
        finally:
            sys.path.remove(str(RUNNER))
        self.assertEqual(nvbridge.WINDOWS, sys.platform == "win32")


class MutationTwins(unittest.TestCase):
    def mutate(self, old: str, new: str) -> str:
        s = SRC.read_text(encoding="utf-8")
        self.assertEqual(s.count(old), 1, f"twin anchor not unique/present: {old!r}")
        return s.replace(old, new)

    def test_twin_unguarded_windows_imports(self):
        self.assertTrue(problems(self.mutate('WINDOWS = sys.platform == "win32"', "WINDOWS = True")))

    def test_twin_connect_does_not_check_platform(self):
        s = SRC.read_text(encoding="utf-8")
        anchor = "        require_windows()\n        p = path or pipe_path(instance)"
        self.assertEqual(s.count(anchor), 1)
        self.assertTrue(problems(s.replace(anchor, "        p = path or pipe_path(instance)")))

    def test_twin_message_not_clear(self):
        self.assertTrue(problems(self.mutate("is Windows only (", "is not available (")))

    def test_twin_pid_alive_unguarded(self):
        self.assertTrue(problems(self.mutate('    require_windows("checking a game process")\n', "")))


if __name__ == "__main__":
    unittest.main()
