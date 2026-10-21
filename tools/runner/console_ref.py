"""Console lines that name a reference, and how the bridge runs them (exec {cmd, ref}).

The game's console script compiler (FalloutNV.exe 1.4.0.525, through NVSEConsoleInterface::RunScriptLine2) does not
accept "<hexFormID>.Command". In game on 2026-10-21 the line "00103E61.GetOpenState" got the reply
'Script command "00103E61.GetOpenState" not found', and the EditorID of a non-persistent ref got 'Invalid reference ...'.
Only "player." works as a prefix (player is a valid script reference). So a line for any other reference is sent as
exec {cmd: "GetOpenState", ref: "00103E61"}: the bridge looks the reference up and runs the line with it as the
console's CALLING REFERENCE (the way plugin/hooks/apply_lines.h runs Track A's lines).

split_ref_line() turns an old "<ref>.<Func> args" line into ("Func args", ref) so that callers can move to the ref
parameter one call site at a time. Pure functions, no Windows dependency: tools/scenarios/scenlib.py imports it too.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

# A reference as the bridge's exec `ref` accepts it: "Plugin.esm:HEX" (HEX = the id inside the plugin) or a runtime form
# id "HEX" / "0xHEX" (1 to 8 hex digits). A plugin name here has no white space (tools never put one in a line). A bare
# id must contain a decimal digit (or the 0x prefix), so that an EditorID made of the letters A-F ("Bed", "Cafe") is not
# taken for a form id.
_HEX = r"(?:0[xX][0-9A-Fa-f]{1,8}|(?=[0-9A-Fa-f]{0,7}[0-9])[0-9A-Fa-f]{1,8})"
_REF = r"(?:[^\s().:][^\s():]*\.es[mpl]:(?:0[xX])?[0-9A-Fa-f]{1,8}|" + _HEX + r")"
_FUNC = r"[A-Za-z_][A-Za-z0-9_]*"
# "<ref>.<Func> args" at the start of a line
LEAD_RE = re.compile(r"^\s*(?P<ref>" + _REF + r")\.(?P<cmd>" + _FUNC + r"(?:\s.*)?)$", re.S)
# a "<ref>.<Func>" anywhere: after the start, white space or an operator / bracket
ANY_RE = re.compile(r"(?:^|(?<=[\s(),+\-*/=<>!&|]))" + _REF + r"\." + _FUNC + r"\b")
PLAYER_RE = re.compile(r"^\s*player\.", re.I)
# "set <owner>.<variable> to <value>": a script variable of a quest, not a command on a calling reference
SET_VAR_RE = re.compile(r"^\s*set\s+\S+\.\w+\s+to\s", re.I)


class RefLineError(ValueError):
    """The line names a reference in a place that exec {cmd, ref} cannot express (not at the start, or twice)."""


def split_ref_line(line: str) -> Tuple[str, Optional[str]]:
    """'<ref>.<Func> args' -> ('<Func> args', '<ref>'). Any other line -> (line, None), unchanged.

    'player.X' lines stay as they are: player is a valid script reference in the console. <ref> is a runtime hex id
    ('00103E61', '0x103E61') or 'Plugin.esm:HEX'; it goes to exec(cmd, ref=<ref>) / eval(expr, ref=<ref>).
    'set <quest>.<var> to N' stays as it is: the quest owns a variable, it is not a calling reference (a calling
    reference cannot change how the console reads that line).
    Raises RefLineError when a '<ref>.<Func>' is not at the start of the line or when a second one follows, for example
    'printc "%g" (00103E61.GetOpenState)': the console cannot run that and exec has only one calling reference.
    """
    if SET_VAR_RE.match(line):
        return line, None
    if PLAYER_RE.match(line):
        rest = line[PLAYER_RE.match(line).end():]
        if ANY_RE.search(rest):
            raise RefLineError("%r: a '<ref>.<Func>' after the player prefix; give that reference its own line" % line)
        return line, None
    m = LEAD_RE.match(line)
    if not m:
        if ANY_RE.search(line):
            raise RefLineError("%r: a '<ref>.<Func>' that is not at the start of the line; the console cannot run it. "
                               "Write '<Func> args' and pass the reference as ref=" % line)
        return line, None
    ref, cmd = m.group("ref"), m.group("cmd").strip()
    if ANY_RE.search(cmd):
        raise RefLineError("%r: names a second reference; exec runs a line on one calling reference only" % line)
    return cmd, ref


def join_ref_line(cmd: str, ref: Optional[str]) -> str:
    """The inverse of split_ref_line, for logs and labels only: ('GetDead', '00104C70') -> '00104C70.GetDead'.
    Never send the result to the console."""
    return cmd if ref is None else "%s.%s" % (ref, cmd)
