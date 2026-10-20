"""Command-line checks and run guards shared by the in-game proof scripts (proof_glue.py, proof_input.py).

Exit codes of both proofs: 0 = every check passed, 1 = a check failed, 2 = the proof could not run (a bad command
line, no game on the bridge, the game lost during the run, or a form id that the game's load order cannot resolve).
A bad command line stops the proof before it opens the bridge, so a typo never leaves the game half-driven (for
example, standing in a dialogue that the proof then crashed out of). main() returns these codes: it never raises
SystemExit itself.
"""
import re
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .bot import ConnectionLost

# "Plugin.esm:HEX" (how the bridge names a form) or a bare hex runtime form id, with an optional 0x prefix and at most
# 8 hex digits. Leading zeros, the 0x prefix and the case do not change which form it names (see parse_form).
FORM_RE = re.compile(r"(?:[^:\s][^:]*:)?(?:0[xX])?[0-9A-Fa-f]{1,8}")
# The low 24 bits of a runtime form id are the id inside its plugin (the local id). The top byte is the load index.
LOCAL_MASK = 0xFFFFFF

# One form, however it was spelled: (plugin name in lower case, local id).
Key = Tuple[str, int]


class CannotRun(Exception):
    """The proof cannot run on this game, for example a form id that the load order cannot resolve. Exit code 2."""


def flag(dest: str) -> str:
    """argparse dest -> the option as the user types it ('other_info' -> '--other-info')."""
    return "--" + dest.replace("_", "-")


def parse_form(form: str) -> Tuple[Optional[str], int]:
    """A form id that FORM_RE accepts -> (plugin name in lower case or None for a bare id, the number of its hex part).

    'FalloutNV.esm:0x0801' and 'falloutnv.esm:801' give the same pair. A bare id ('0801', '0x801', '00000801') gives
    (None, number): it is a runtime id, and only the load order can say which plugin it belongs to (see form_key).
    """
    plugin, _, hexpart = form.rpartition(":")
    return (plugin.lower() or None), int(hexpart, 16)


def form_key(form: Union[str, int], plugins: Sequence[str]) -> Key:
    """The form as (plugin name in lower case, local id), with the game's load order `plugins` (index 0 first).

    The rule that the dialog check uses to compare forms (numbers, never spellings):
      Plugin.esm:HEX, HEX <= FFFFFF   -> (plugin, HEX). HEX is the local id. The plugin must be loaded.
      Plugin.esm:HEX, HEX >  FFFFFF   -> a runtime id with its plugin given. Accepted only when its top byte is the load
                                         index of that plugin, then (plugin, low 24 bits).
      bare HEX or 0xHEX, or an int    -> a runtime id. The top byte is the load index, so the plugin is
                                         plugins[top byte] and the local id is the low 24 bits.
    So the low 24 bits are compared only when the plugin is known to match. A top byte outside the load order, a plugin
    that is not loaded, or a top byte that is not the load index of the given plugin raises ValueError (never a guess).
    """
    if isinstance(form, int):
        plugin, num = None, form
    elif isinstance(form, str) and FORM_RE.fullmatch(form):
        plugin, num = parse_form(form)
    else:
        raise ValueError(f"{form!r} is not a form id")
    low = [p.lower() for p in plugins]
    index = num >> 24
    if plugin is None:
        if index >= len(low):
            raise ValueError(f"{form!r} is a runtime id with load index {index:02X}, but only {len(low)} plugins are loaded")
        return low[index], num & LOCAL_MASK
    if plugin not in low:
        raise ValueError(f"{form!r} names plugin {plugin!r}, which is not loaded")
    if num > LOCAL_MASK and index != low.index(plugin):
        raise ValueError(f"{form!r} has load index {index:02X}, but {plugin!r} has load index {low.index(plugin):02X}")
    return plugin, num & LOCAL_MASK


def provably_same(x: str, y: str) -> bool:
    """True when two valid form ids name the same form without the load order (the check before the bridge opens).

    That is: the same plugin and the same number, or both bare and the same number. A bare id and a Plugin.esm:HEX id
    can name the same form only through the load order: the proof checks that pair with form_key after it opens the bridge.
    """
    return parse_form(x) == parse_form(y)


def problems(a: Any, required: Dict[str, str], forms: Iterable[str]) -> List[str]:
    """One message per bad argument in the parsed namespace `a`; an empty list means the proof can run.

    `required` maps each required dest to what the user must give. `forms` names the dests that hold a form id:
    when one of them is given, it must match FORM_RE (a bad one would raise ValueError in the middle of the run).
    An empty value is refused: the scripts test an option with `if a.x:`, so "" would turn its check off without a word.
    """
    out: List[str] = []
    missing = set()
    for dest, what in required.items():
        if not getattr(a, dest, None):
            missing.add(dest)
            out.append(f"argument {flag(dest)} is missing: give {what}, or use --fake")
    for dest in forms:
        v = getattr(a, dest, None)
        if v is None or dest in missing:
            continue
        if not v.strip():
            out.append(f"argument {flag(dest)} is empty: give a form id (Plugin.esm:HEX or a bare hex id), or leave the option out")
        elif not FORM_RE.fullmatch(v):
            out.append(f"argument {flag(dest)}: {v!r} is not a form id (use Plugin.esm:HEX or a bare hex id)")
    return out


def parse(ap: Any, argv: Optional[List[str]]) -> Tuple[Optional[Any], Optional[int]]:
    """(namespace, None), or (None, exit code) after argparse printed the help (0) or a usage error (2)."""
    try:
        return ap.parse_args(argv), None
    except SystemExit as e:  # argparse exits on --help and on a usage error, after it printed the text
        return None, e.code if isinstance(e.code, int) else 2


def refuse(ap: Any, bad: List[str]) -> int:
    """Print the usage and the problems on stderr the way argparse does, and return exit code 2."""
    ap.print_usage(sys.stderr)
    print(f"{ap.prog}: error: {'; '.join(bad)}", file=sys.stderr)
    return 2


def open_bridge(connect: Callable[[int], Any], instance: int) -> Optional[Any]:
    """connect(instance), or None after a 'could not run' message on stderr (the caller then exits 2)."""
    try:
        return connect(instance)
    except Exception as e:  # noqa: BLE001  ConnectionLost when no game has the pipe, ImportError, OSError, ...
        print(f"could not run: cannot reach the game on instance {instance}: {e}", file=sys.stderr)
        return None


def guarded(where: str, body: Callable[[], Any]) -> Optional[Any]:
    """body(), or None after a 'could not run' message on stderr (the caller then exits 2).

    None when the game is lost during the run (ConnectionLost: a game crash or a pipe loss, GameCrashed included), or
    when the run raises CannotRun. Every other exception goes through: it is a bug in the proof, not a game state.
    """
    try:
        return body()
    except ConnectionLost as e:
        print(f"could not run: lost the game on {where} during the proof: {type(e).__name__}: {e}", file=sys.stderr)
    except CannotRun as e:
        print(f"could not run: {e}", file=sys.stderr)
    return None


def connect_real(instance: int) -> Any:
    """The real bridge (tools/runner/nvbridge.py), waiting up to 10 s for the game to create the pipe."""
    from nvbridge import NvBridge
    return NvBridge.connect(instance, wait_s=10)
