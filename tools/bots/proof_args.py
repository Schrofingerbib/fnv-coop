"""Command-line checks shared by the in-game proof scripts (proof_glue.py, proof_input.py).

Exit codes of both proofs: 0 = every check passed, 1 = a check failed, 2 = the proof could not run (a bad command
line, or no game on the bridge). A bad command line stops the proof before it opens the bridge, so a typo never
leaves the game half-driven (for example, standing in a dialogue that the proof then crashed out of).
"""
import re
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional

# "Plugin.esm:HEX" (how the bridge names a form) or a bare hex runtime form id, with an optional 0x prefix.
FORM_RE = re.compile(r"(?:[^:\s][^:]*:)?(?:0[xX])?[0-9A-Fa-f]{1,8}")


def flag(dest: str) -> str:
    """argparse dest -> the option as the user types it ('other_info' -> '--other-info')."""
    return "--" + dest.replace("_", "-")


def form_number(form: str) -> int:
    """The hex part of a form id that FORM_RE accepts, as an int."""
    return int(form.rsplit(":", 1)[-1], 16)


def problems(a: Any, required: Dict[str, str], forms: Iterable[str]) -> List[str]:
    """One message per bad argument in the parsed namespace `a`; an empty list means the proof can run.

    `required` maps each required dest to what the user must give. `forms` names the dests that hold a form id:
    when one of them is given, it must match FORM_RE (a bad one would raise ValueError in the middle of the run).
    """
    out: List[str] = []
    for dest, what in required.items():
        if not getattr(a, dest, None):
            out.append(f"argument {flag(dest)} is missing: give {what}, or use --fake")
    for dest in forms:
        v = getattr(a, dest, None)
        if v and not FORM_RE.fullmatch(v):
            out.append(f"argument {flag(dest)}: {v!r} is not a form id (use Plugin.esm:HEX or a bare hex id)")
    return out


def open_bridge(connect: Callable[[int], Any], instance: int) -> Optional[Any]:
    """connect(instance), or None after a 'could not run' message on stderr (the caller then exits 2)."""
    try:
        return connect(instance)
    except Exception as e:  # noqa: BLE001  ConnectionLost when no game has the pipe, ImportError, OSError, ...
        print(f"could not run: cannot reach the game on instance {instance}: {e}", file=sys.stderr)
        return None


def connect_real(instance: int) -> Any:
    """The real bridge (tools/runner/nvbridge.py), waiting up to 10 s for the game to create the pipe."""
    from nvbridge import NvBridge
    return NvBridge.connect(instance, wait_s=10)
