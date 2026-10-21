"""One-shot live inspector: what is each running game copy doing right now? It only looks, it never changes game state.

    python -I tools/bots/inspect_live.py --instances 0,1                    # one table, one column per copy
    python -I tools/bots/inspect_live.py --instances 0,1 --ref FalloutNV.esm:104C70   # plus distance, health and owner of that ref
    python -I tools/bots/inspect_live.py --instances 0 --json               # the raw data, for a script

Rows: role, menu kind and message queue, weapon drawn / equipped, health, position and cell, the --ref block, the co-op sync state
(lastSeq, acks, allSynced, digest ok / mismatch / repairs and the top desync entries) and the recent message boxes (ui.status).
Under the table come the problems it can name: a message box up, a copy behind the host, a mismatch, nothing equipped.

Read-only by construction: every bridge call goes through ReadOnlyBridge, which allows a fixed list of query methods and a fixed
list of expression getters and refuses everything else (exec, bot.menu.press, bot.fire, activate, ...). So a message box that is up
stays up. Use Bot.dismiss_messages or --dismiss-messages of a proof to clear it; this tool never does.

A missing method (an older build, a module off) shows as n/a in that row, a copy that cannot be reached as a column saying so.
The tool takes the game pipe for the second or two it needs: a pipe has one client at a time, so stop it while another runner is
attached to the same copy. Exit 0 when at least one copy answered, 2 when none did, 1 on bad arguments.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT / "tools"), str(ROOT / "tools" / "runner")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bots import primitives as P  # noqa: E402
from bots.bot import Bot, BridgeError, ConnectionLost, RpcError, dist  # noqa: E402

M_COOP_STATUS = "coop.status"
M_COOP_OWNER = "coop.owner"
M_UI_STATUS = "ui.status"

# Bridge methods this tool may call. All of them only read. Anything else raises ReadOnlyViolation.
READ_ONLY_METHODS = frozenset({"ping", "bridge.version", "plugins", P.M_REF_INFO, P.M_QUERY, P.M_BOT_MENU, P.M_BOT_STATUS,
                               M_COOP_STATUS, M_COOP_OWNER, M_UI_STATUS, P.M_DIALOG_OPEN})
# Console expressions it may evaluate (the function name after an optional 'player.'): getters only.
READ_ONLY_EVAL = frozenset({"isweaponout", "getequippedobject", "getav", "getitemcount", "getangle", "getequipped", "getstage",
                            "isininterior", "issneaking"})

COL_W = 40            # characters per copy column; longer lines wrap
BOX_TEXT_W = 34       # characters of a message box's text in the table


class ReadOnlyViolation(Exception):
    """The inspector tried to do something that could change the game."""


class ReadOnlyBridge:
    """Wraps a bridge (NvBridge or a fake) and lets only reads through. Deny by default."""

    def __init__(self, nv: Any):
        self._nv = nv
        self.calls: List[str] = []       # every method / expression that went out, for the tests

    @property
    def clock(self):                     # Bot takes the fake's virtual clock when there is one
        return getattr(self._nv, "clock", None)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout_s: float = 10.0) -> Any:
        if method not in READ_ONLY_METHODS:
            raise ReadOnlyViolation(f"{method} is not a read-only method")
        self.calls.append(method)
        return self._nv.call(method, params or {}, timeout_s=timeout_s)

    def plugins(self) -> List[str]:
        self.calls.append("plugins")
        return self._nv.plugins()

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        m = re.match(r"\s*(?:player\.)?(\w+)", expr)
        if not m or m.group(1).lower() not in READ_ONLY_EVAL:
            raise ReadOnlyViolation(f"expression {expr!r} is not a known getter")
        self.calls.append("eval " + expr)
        return self._nv.eval(expr) if ref is None else self._nv.eval(expr, ref=ref)

    def exec(self, cmd: str, *a: Any, **kw: Any) -> Dict[str, Any]:
        raise ReadOnlyViolation(f"console line {cmd!r}: the inspector never runs console commands")

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"{name}: not available through the read-only bridge")


# ---------------------------------------------------------------------------------------------- gathering
Missing = "n/a"


def _section(rep: Dict[str, Any], key: str, fn: Callable[[], Any]) -> None:
    """rep[key] = fn(); a method this build lacks gives n/a, any other bridge error gives 'error: ...'. A lost game is not caught here."""
    try:
        rep[key] = fn()
    except ConnectionLost:
        raise
    except RpcError as e:
        rep[key] = Missing if e.code == -32601 else f"error: {e.message}"
    except ReadOnlyViolation:
        raise
    except Exception as e:  # noqa: BLE001  (a diagnostic tool: one broken section must not hide the others)
        rep[key] = f"error: {e}"


def coop_summary(st: Dict[str, Any], top: int = 3) -> Dict[str, Any]:
    """The sync facts of one coop.status answer: role, lastSeq, acks (host), allSynced (host), digest counters (host), top desync entries."""
    host = st.get("host") if isinstance(st.get("host"), dict) else None
    client = st.get("client") if isinstance(st.get("client"), dict) else None
    out: Dict[str, Any] = {"role": st.get("role"), "running": st.get("running"), "lastSeq": int(st.get("lastSeq", 0) or 0)}
    if host is not None:
        peers = host.get("peers") if isinstance(host.get("peers"), list) else []
        out["allSynced"] = host.get("allSynced")
        out["acks"] = [{"slot": p.get("slot"), "acked": int(p.get("acked", 0) or 0), "joined": p.get("joined")} for p in peers]
        out["connected"] = host.get("connected")
    if client is not None:
        out["joined"] = client.get("joined")
        out["rebases"] = client.get("rebases")
    ds = st.get("desyncs") if isinstance(st.get("desyncs"), dict) else None
    if ds is not None:
        dg = ds.get("digests") if isinstance(ds.get("digests"), dict) else {}
        out["digests"] = {k: int(dg.get(k, 0) or 0) for k in ("ok", "mismatch", "repairs")}
        entries: List[Dict[str, Any]] = []
        for rep in reversed(ds.get("desyncs") or []):       # newest report first
            for e in rep.get("entries") or []:
                entries.append({"key": e.get("key"), "host": e.get("host"), "client": e.get("client"), "clientOnly": e.get("clientOnly"),
                                "slot": rep.get("slot"), "repaired": rep.get("repaired")})
                if len(entries) >= top:
                    break
            if len(entries) >= top:
                break
        out["desyncTop"] = entries
    return out


def boxes_summary(ui: Dict[str, Any], n: int = 4) -> List[Dict[str, Any]]:
    """The last `n` message boxes of ui.status recentBoxes (oldest first): seq, script, text."""
    rb = ui.get("recentBoxes") if isinstance(ui, dict) else None
    return [{"seq": b.get("seq"), "script": b.get("script"), "message": b.get("message"), "text": b.get("text", "")}
            for b in (rb or [])[-n:]]


def inspect_one(nv: Any, name: str, ref: Optional[str] = None, boxes: int = 4) -> Dict[str, Any]:
    """Everything the table shows for one copy, as plain data. Reads only (the bridge is wrapped)."""
    ro = nv if isinstance(nv, ReadOnlyBridge) else ReadOnlyBridge(nv)
    bot = Bot(ro, name=f"inspect-{name}")
    rep: Dict[str, Any] = {"name": name, "reachable": True}
    try:
        _section(rep, "menu", lambda: dict(ro.call(P.M_BOT_MENU)))
        _section(rep, "coop", lambda: coop_summary(ro.call(M_COOP_STATUS)))
        ui: Dict[str, Any] = {}

        def read_ui() -> Dict[str, Any]:
            ui.update(ro.call(M_UI_STATUS))
            return boxes_summary(ui, boxes)
        _section(rep, "boxes", read_ui)
        rep["role"] = (rep["coop"].get("role") if isinstance(rep.get("coop"), dict) else None) or ui.get("role")

        def weapon() -> Dict[str, Any]:
            w: Dict[str, Any] = {}
            for key, expr in (("out", P.is_weapon_out()), ("equipped", P.equipped_weapon())):
                try:
                    w[key] = ro.eval(expr) != 0
                except ConnectionLost:
                    raise
                except BridgeError:
                    w[key] = None
            return w
        _section(rep, "weapon", weapon)
        _section(rep, "health", lambda: bot.health())
        _section(rep, "player", lambda: {"pos": tuple(bot.pos()), "cell": bot.cell()})
        if ref is not None:
            def about_ref() -> Dict[str, Any]:
                me, there = bot.pos(), bot.pos(ref)
                r: Dict[str, Any] = {"form": ref, "distance": dist(me, there), "same_cell": bot.cell(ref) == bot.cell(),
                                     "alive": bot.alive(ref), "health": bot.health(ref)}
                try:
                    full = bot._norm(ref)[0]                      # noqa: SLF001 (Plugin.esm:HEX, as coop.owner wants it)
                    own = ro.call(M_COOP_OWNER, {"ref": full})
                    r["owner"] = {k: own.get(k) for k in ("owner", "epoch", "known", "mine", "unsupported") if k in own}
                except RpcError as e:
                    r["owner"] = Missing if e.code == -32601 else f"error: {e.message}"
                return r
            _section(rep, "ref", about_ref)
    except ConnectionLost as e:
        rep.clear()
        rep.update({"name": name, "reachable": False, "error": str(e)})
    return rep


# ---------------------------------------------------------------------------------------------- reading the answers
def menu_text(menu: Any) -> List[str]:
    if not isinstance(menu, dict):
        return [str(menu)]
    kind = str(menu.get("kind", "?"))
    left = max(0, int(menu.get("msgCount", 0) or 0) - int(menu.get("msgIdx", 0) or 0)) if "msgCount" in menu else None
    first = kind if left is None or kind != "message" else f"{kind} ({left} queued)"
    lines = [first]
    if "msgCount" in menu:
        lines.append(f"queue idx {menu.get('msgIdx', 0)} of {menu['msgCount']}")
    return lines


def weapon_text(w: Any) -> str:
    if not isinstance(w, dict):
        return str(w)

    def yn(v: Any, yes: str, no: str) -> str:
        return "?" if v is None else (yes if v else no)
    return f"{yn(w.get('out'), 'drawn', 'holstered')}, {yn(w.get('equipped'), 'equipped', 'NOTHING equipped')}"


def coop_rows(c: Any) -> List[Tuple[str, List[str]]]:
    """The co-op block as (label, lines): lastSeq, acks and allSynced (host), joined (client), digest counters and the top desync entries."""
    if not isinstance(c, dict):
        return [("coop", [str(c)])]
    rows: List[Tuple[str, List[str]]] = [("lastSeq", [str(c.get("lastSeq", "?"))])]
    if "acks" in c:
        seq = c.get("lastSeq", 0)
        acks = [f"slot {a['slot']}: {a['acked']}" + (f" ({seq - a['acked']} behind)" if seq > a["acked"] else "") +
                ("" if a.get("joined") in (None, True) else " left") for a in c["acks"]]
        rows.append(("acks", acks or ["no clients"]))
        rows.append(("allSynced", [str(c.get("allSynced"))]))
    elif "joined" in c:
        rows.append(("joined", [f"{c.get('joined')} (rebases {c.get('rebases')})"]))
    if "digests" in c:
        d = c["digests"]
        rows.append(("digest", [f"ok {d['ok']}  mismatch {d['mismatch']}  repairs {d['repairs']}"]))
        top = c.get("desyncTop") or []
        rows.append(("desync", [f"{e['key']}: host {e['host']} client {e['client']}" for e in top] or ["none"]))
    return rows


def box_lines(boxes: Any) -> List[str]:
    if not isinstance(boxes, list):
        return [str(boxes)]
    if not boxes:
        return ["none yet"]
    out = []
    for b in boxes:
        t = " ".join(str(b.get("text", "")).split())
        t = t if len(t) <= BOX_TEXT_W else t[:BOX_TEXT_W - 3] + "..."
        out.append(f"#{b.get('seq')} {b.get('script')} {t}".rstrip())
    return out


def ref_lines(r: Any) -> Dict[str, List[str]]:
    if not isinstance(r, dict):
        return {"ref": [str(r)]}
    o = r.get("owner")
    if isinstance(o, dict):
        own = "unknown" if not o.get("known", True) else (f"slot {o.get('owner')} epoch {o.get('epoch')}" + (" (mine)" if o.get("mine") else ""))
        if o.get("unsupported"):
            own = "n/a (no actor-ownership service)"
    else:
        own = str(o)
    return {"ref distance": [f"{r['distance']:.0f} units" + ("" if r.get("same_cell") else " (other cell)")],
            "ref health": [f"{r['health']:g} " + ("alive" if r.get("alive") else "DEAD")],
            "ref owner": [own]}


def build_rows(reports: List[Dict[str, Any]]) -> List[Tuple[str, List[List[str]]]]:
    """[(label, [lines of copy 0, lines of copy 1, ...])]. An unreachable copy has its message in the first row only."""
    rows: List[Tuple[str, List[List[str]]]] = []
    n = len(reports)

    def add(label: str, per: List[Optional[List[str]]]) -> None:
        rows.append((label, [p if p is not None else [""] for p in per]))

    add("copy", [[f"instance {r['name']}"] for r in reports])
    ok = [r for r in reports if r.get("reachable")]
    if not ok:
        add("state", [[r.get("error", "unreachable")] for r in reports])
        return rows
    add("role", [[str(r.get("role") or "?")] if r["reachable"] else [f"unreachable: {r.get('error', '')}"] for r in reports])

    def per(fn: Callable[[Dict[str, Any]], Optional[List[str]]]) -> List[Optional[List[str]]]:
        return [fn(r) if r["reachable"] else None for r in reports]
    add("menu", per(lambda r: menu_text(r["menu"]) if "menu" in r else None))
    add("weapon", per(lambda r: [weapon_text(r["weapon"])] if "weapon" in r else None))
    add("health", per(lambda r: [f"{r['health']:g}" if isinstance(r.get("health"), (int, float)) else str(r.get("health"))] if "health" in r else None))

    def where(r: Dict[str, Any]) -> Optional[List[str]]:
        p = r.get("player")
        if not isinstance(p, dict):
            return [str(p)] if p is not None else None
        x, y, z = p["pos"]
        return [f"{x:.0f} {y:.0f} {z:.0f}", f"cell {p['cell']:08X}"]
    add("position", per(where))
    if any("ref" in r for r in reports):
        labels: List[str] = []
        for r in ok:
            for k in ref_lines(r.get("ref")):
                if k not in labels:
                    labels.append(k)
        for k in labels:
            add(k, per(lambda r, k=k: ref_lines(r.get("ref")).get(k) if "ref" in r else None))
    coop_labels: List[str] = []
    cmaps: List[Dict[str, List[str]]] = []
    for r in reports:
        m = dict(coop_rows(r["coop"])) if r.get("reachable") and "coop" in r else {}
        cmaps.append(m)
        for k in m:
            if k not in coop_labels:
                coop_labels.append(k)
    for k in coop_labels:
        rows.append((k, [m.get(k, [""]) for m in cmaps]))
    add("boxes", per(lambda r: box_lines(r["boxes"]) if "boxes" in r else None))
    assert all(len(cols) == n for _, cols in rows)
    return rows


def problems(reports: List[Dict[str, Any]]) -> List[str]:
    """The things the table already shows that a person would otherwise have to spot: one plain sentence each."""
    out: List[str] = []
    seqs: Dict[str, int] = {}
    for r in reports:
        n = r["name"]
        if not r.get("reachable"):
            out.append(f"instance {n}: unreachable ({r.get('error', '')})")
            continue
        m = r.get("menu")
        if isinstance(m, dict) and m.get("kind") not in P.MENU_FREE:
            left = max(0, int(m.get("msgCount", 0) or 0) - int(m.get("msgIdx", 0) or 0)) if "msgCount" in m else 0
            what = "a message box is up" + (f" ({left} queued)" if left else "") if m.get("kind") == "message" else f"the {m.get('kind')} menu is open"
            out.append(f"instance {n}: {what}" + (", it eats all input" if m.get("kind") in P.MENU_BLOCKING else ""))
        w = r.get("weapon")
        if isinstance(w, dict) and w.get("equipped") is False:
            out.append(f"instance {n}: nothing is equipped")
        c = r.get("coop")
        if isinstance(c, dict):
            seqs[n] = c.get("lastSeq", 0)
            if c.get("allSynced") is False:
                out.append(f"instance {n}: the host says not all clients are synced")
            d = c.get("digests") or {}
            if d.get("mismatch"):
                out.append(f"instance {n}: {d['mismatch']} digest mismatch(es), {d.get('repairs', 0)} repaired")
            if c.get("joined") is False:
                out.append(f"instance {n}: the client has not joined")
        ref = r.get("ref")
        if isinstance(ref, dict) and ref.get("alive") is False:
            out.append(f"instance {n}: the ref is dead")
    if len(set(seqs.values())) > 1:
        top = max(seqs.values())
        out.append("lastSeq differs: " + ", ".join(f"instance {k} at {v}" + (f" ({top - v} behind)" if v < top else "") for k, v in seqs.items()))
    return out


def render(reports: List[Dict[str, Any]], col_w: int = COL_W) -> str:
    """The table: a label column and one column per copy, multi-line cells wrapped to col_w characters."""
    rows = build_rows(reports)
    lw = max(len(label) for label, _ in rows) + 1
    out: List[str] = []

    def line(label: str, cells: List[str]) -> str:
        return (label.ljust(lw) + "| " + " | ".join(c.ljust(col_w) for c in cells)).rstrip()
    for i, (label, cols) in enumerate(rows):
        wrapped = [[w for t in (c or [""]) for w in (textwrap.wrap(t, col_w) or [""])] for c in cols]
        h = max(len(w) for w in wrapped)
        for k in range(h):
            out.append(line(label if k == 0 else "", [w[k] if k < len(w) else "" for w in wrapped]))
        if i == 0:
            out.append("-" * lw + "+" + "+".join("-" * (col_w + 2) for _ in cols))
    probs = problems(reports)
    out.append("")
    out.append("problems: none" if not probs else "problems:")
    out.extend("  - " + p for p in probs)
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- command line
def parse_instances(text: str) -> List[int]:
    """'0,1' -> [0, 1]. One number is that one instance."""
    ids = [int(x) for x in text.split(",") if x.strip() != ""]
    if not ids or len(set(ids)) != len(ids) or any(i < 0 for i in ids):
        raise ValueError(f"--instances needs one or more different instance ids such as 0,1, got {text!r}")
    return ids


def inspect_all(bridges: Sequence[Tuple[str, Any]], ref: Optional[str] = None, boxes: int = 4) -> List[Dict[str, Any]]:
    """bridges = [(name, bridge or an Exception that stands for a failed connect)]."""
    out = []
    for name, nv in bridges:
        if isinstance(nv, Exception):
            out.append({"name": name, "reachable": False, "error": str(nv)})
        else:
            out.append(inspect_one(nv, name, ref, boxes))
    return out


def main(argv: Optional[Sequence[str]] = None, bridges: Optional[Sequence[Tuple[str, Any]]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="0,1", help="bridge instance ids, for example 0,1 (a single number is that one instance)")
    ap.add_argument("--ref", default=None, help="a form to report: Plugin.esm:HEX or a hex id (distance, health, owner)")
    ap.add_argument("--wait", type=float, default=2.0, help="seconds to wait for each pipe (default 2)")
    ap.add_argument("--boxes", type=int, default=4, help="how many recent message boxes to list (default 4)")
    ap.add_argument("--json", action="store_true", help="print the raw data as JSON instead of the table")
    a = ap.parse_args(argv)
    try:
        ids = parse_instances(a.instances)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    opened: List[Any] = []
    try:
        if bridges is None:
            from nvbridge import NvBridge      # noqa: WPS433
            made: List[Tuple[str, Any]] = []
            for i in ids:
                try:
                    nv = NvBridge.connect(instance=i, wait_s=a.wait)
                    opened.append(nv)
                    made.append((str(i), nv))
                except ConnectionLost as e:
                    made.append((str(i), e))
            bridges = made
        reports = inspect_all(bridges, a.ref, a.boxes)
    finally:
        for nv in opened:
            try:
                nv.close()
            except Exception:  # noqa: BLE001
                pass
    if a.json:
        print(json.dumps(reports, indent=1, default=str))
    else:
        print(render(reports))
    return 0 if any(r.get("reachable") for r in reports) else 2


if __name__ == "__main__":
    sys.exit(main())
