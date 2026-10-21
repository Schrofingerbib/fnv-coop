#!/usr/bin/env python3
"""nvbridge client: drive a running Fallout: New Vegas (nvcoop.dll) over its named-pipe JSON-RPC.

Protocol: newline-delimited JSON over \\\\.\\pipe\\nvcoop-<instance>. See plugin/bridge/bridge.h.

Library use::

    from nvbridge import NvBridge
    with NvBridge.connect(instance=0, wait_s=60) as nv:
        nv.load_save("goodsprings_start")
        nv.exec("setstage VMS01 10")
        nv.wait_for("GetStage VMS01", op=">=", value=10, timeout_ms=5000)
        nv.exec("GetOpenState", ref="00103E61")      # a line for a reference: never "00103E61.GetOpenState"

A console line for a reference other than the player names the reference in `ref` (exec {cmd, ref}): the game's console
does not accept "<hexFormID>.Command" (see console_ref.py). split_ref_line() converts an old "<ref>.<Func> args" line.

CLI::

    python nvbridge.py --instance 0 ping
    python nvbridge.py --instance 0 exec "player.getpos x"
    python nvbridge.py --instance 0 exec GetOpenState --ref FalloutNV.esm:103E61
    python nvbridge.py --instance 0 waitfor "GetStage VMS01" --op ">=" --value 10 --timeout-ms 5000
    python nvbridge.py --instance 0 events --since 0
"""
from __future__ import annotations

import argparse
import itertools
import json
import msvcrt
import sys
import threading
import time
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_ref import RefLineError, join_ref_line, split_ref_line  # noqa: E402,F401  (re-exported)

PROTOCOL_VERSION = 1

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]


class BridgeError(Exception):
    """Base class for client-side failures."""


class RpcError(BridgeError):
    def __init__(self, code: int, message: str, method: str = ""):
        super().__init__(f"{method}: [{code}] {message}" if method else f"[{code}] {message}")
        self.code = code
        self.message = message
        self.method = method


class ProtocolError(BridgeError):
    """The server sent something that violates the protocol (bad JSON, wrong id, ...)."""


class WaitTimeout(BridgeError):
    def __init__(self, what: str, last: Any = None):
        super().__init__(f"timed out waiting for {what} (last value: {last!r})")
        self.last = last


class ConnectionLost(BridgeError):
    pass


class GameCrashed(ConnectionLost):
    """The pipe died AND the game process (pid learned from ping) no longer exists."""

    def __init__(self, pid: int, detail: str = ""):
        super().__init__(f"game process {pid} is gone ({detail}); collect logs with crashlog.collect_crash_report()")
        self.pid = pid


def _pid_alive(pid: int) -> bool:
    h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = wintypes.DWORD()
    ok = _k32.GetExitCodeProcess(h, ctypes.byref(code))
    _k32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def pipe_path(instance: int) -> str:
    return rf"\\.\pipe\nvcoop-{int(instance)}"


class NvBridge:
    def __init__(self, stream, name: str = ""):
        self._f = stream
        self.name = name
        self._ids = itertools.count(1)
        self._buf = b""
        self._lock = threading.Lock()
        self._closed = False
        self.game_pid = 0
        self.lease: Optional[str] = None  # token of the lease we hold; attached to every call
        # A synchronous pipe handle serialises I/O: a thread blocked in ReadFile would block our
        # WriteFile. So no reader thread; recv polls PeekNamedPipe and only reads what is available.
        self._h = msvcrt.get_osfhandle(stream.fileno())

    # ---- connection ----
    @classmethod
    def connect(cls, instance: int = 0, wait_s: float = 0.0, path: Optional[str] = None) -> "NvBridge":
        """Open the pipe. With wait_s>0, retry until the game creates it (or time runs out)."""
        p = path or pipe_path(instance)
        deadline = time.monotonic() + wait_s
        while True:
            try:
                f = open(p, "r+b", buffering=0)
                nv = cls(f, p)
                try:
                    nv.ping()  # learns the game pid so a later crash is reported as GameCrashed
                except BridgeError:
                    pass
                return nv
            except OSError as e:  # FileNotFoundError (no pipe yet) or busy (231)
                if time.monotonic() >= deadline:
                    raise ConnectionLost(f"cannot open {p}: {e}") from e
                time.sleep(0.25)

    def close(self) -> None:
        self._closed = True
        try:
            self._f.close()
        except Exception:
            pass

    def __enter__(self) -> "NvBridge":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    def _available(self) -> int:
        avail = wintypes.DWORD(0)
        if not _k32.PeekNamedPipe(self._h, None, 0, None, ctypes.byref(avail), None):
            raise ConnectionLost("pipe closed by server")
        return avail.value

    # ---- raw ----
    def send_raw(self, line: str) -> None:
        try:
            self._f.write(line.encode("utf-8") + b"\n")
        except OSError as e:
            raise ConnectionLost(f"write failed: {e}") from e

    def recv_raw(self, timeout_s: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        delay = 0.0005
        while b"\n" not in self._buf:
            n = self._available()
            if n:
                self._buf += self._f.read(min(n, 1 << 20))
                delay = 0.0005
                continue
            if time.monotonic() >= deadline:
                raise ConnectionLost(f"no reply within {timeout_s}s")
            time.sleep(delay)
            delay = min(delay * 2, 0.01)
        line, self._buf = self._buf.split(b"\n", 1)
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError as e:
            raise ProtocolError(f"invalid JSON from server: {line[:200]!r}") from e
        if not isinstance(msg, dict):
            raise ProtocolError(f"reply is not an object: {msg!r}")
        return msg

    # ---- rpc ----
    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout_s: float = 35.0) -> Any:
        with self._lock:
            rid = next(self._ids)
            req: Dict[str, Any] = {"id": rid, "method": method}
            params = dict(params) if params else {}
            if self.lease and not method.startswith("lock.") and method not in ("ping", "events"):
                params.setdefault("lease", self.lease)
            if params:
                req["params"] = params
            try:
                self.send_raw(json.dumps(req))
                msg = self.recv_raw(timeout_s)
            except ConnectionLost as e:
                if self.game_pid and not _pid_alive(self.game_pid):
                    raise GameCrashed(self.game_pid, str(e)) from e
                raise
        if msg.get("id") != rid:
            raise ProtocolError(f"reply id {msg.get('id')!r} does not match request id {rid} ({method})")
        if "error" in msg:
            e = msg["error"] or {}
            raise RpcError(int(e.get("code", 0)), str(e.get("message", "")), method)
        if "result" not in msg:
            raise ProtocolError(f"reply has neither result nor error: {msg!r}")
        return msg["result"]

    # ---- lease: share one game instance between lanes ----
    def acquire_lease(self, owner: str, ttl_ms: int = 60000, force: bool = False) -> Dict[str, Any]:
        """Take the instance lease (raises RpcError -32010 if someone else holds it). While we hold it
        every call carries our token; other clients' state-changing calls get -32011 until release/expiry."""
        r = self.call("lock.acquire", {"owner": owner, "ttlMs": ttl_ms, "force": force}, timeout_s=5)
        self.lease = r["token"]
        return r

    def renew_lease(self, ttl_ms: int = 60000) -> Dict[str, Any]:
        return self.call("lock.renew", {"token": self.lease, "ttlMs": ttl_ms}, timeout_s=5)

    def release_lease(self, force: bool = False) -> Dict[str, Any]:
        r = self.call("lock.release", {"token": self.lease or "", "force": force}, timeout_s=5)
        self.lease = None
        return r

    def lease_info(self) -> Dict[str, Any]:
        return self.call("lock.info", timeout_s=5)

    def ping(self) -> Dict[str, Any]:
        r = self.call("ping", timeout_s=5)
        if r.get("pong") is not True:
            raise ProtocolError(f"bad ping reply: {r!r}")
        self.game_pid = int(r.get("pid", 0) or 0)
        return r

    def exec(self, cmd: str, timeout_ms: Optional[int] = None, *, ref: Optional[str] = None) -> Dict[str, Any]:
        """Run a console line on the main thread. Returns {'ok': bool, 'output': str} (plus 'ref': the runtime id as
        8 hex digits, when `ref` is given).

        ref: run the line with this reference as the console's calling reference: "Plugin.esm:HEX" (the bridge adds the
        plugin's load-order index) or a runtime id "HEX" / "0xHEX". The game's console does not accept
        "<hexFormID>.Command", so exec("GetOpenState", ref="00103E61"), never exec("00103E61.GetOpenState").
        "player.X" lines need no ref. A ref that names no loaded reference raises RpcError -32013."""
        p: Dict[str, Any] = {"cmd": cmd}
        if ref is not None:
            p["ref"] = ref
        if timeout_ms is not None:
            p["timeoutMs"] = timeout_ms
        return self.call("exec", p, timeout_s=(timeout_ms or 30000) / 1000 + 5)

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        """Evaluate an NVSE expression once (printc) and return it as a number. Use hex FormIDs
        (e.g. 'GetStage 104C1C'): editor IDs do not resolve in the console parser. With `ref` the expression runs with
        that reference as the calling reference: eval("GetOpenState", ref="00103E61") (see exec)."""
        line = 'printc "NVCOOP_EVAL=%g" (' + expr + ')'
        out = (self.exec(line) if ref is None else self.exec(line, ref=ref))["output"]
        i = out.rfind("NVCOOP_EVAL=")
        if i < 0:
            raise BridgeError(f"cannot evaluate {expr!r}: {out!r}")
        return float(out[i + 12:].split()[0])

    # Quest helpers: `q` is a hex FormID string such as "104C1C" (editor IDs do not resolve).
    def stage(self, q: str) -> int:
        return int(self.eval(f"GetStage {q}"))

    def stage_done(self, q: str, stage: int) -> bool:
        return self.eval(f"GetStageDone {q} {stage}") != 0

    def quest_running(self, q: str) -> bool:
        return self.eval(f"GetQuestRunning {q}") != 0

    def quest_completed(self, q: str) -> bool:
        return self.eval(f"GetQuestCompleted {q}") != 0

    def quest_failed(self, q: str) -> bool:
        return self.eval(f"GetQuestFailed {q}") != 0

    def objective_displayed(self, q: str, obj: int) -> bool:
        return self.eval(f"GetObjectiveDisplayed {q} {obj}") != 0

    def objective_completed(self, q: str, obj: int) -> bool:
        return self.eval(f"GetObjectiveCompleted {q} {obj}") != 0

    def wait_stage(self, q: str, stage: int, timeout_ms: int = 10000) -> Dict[str, Any]:
        return self.wait_for(f"GetStage {q}", op="==", value=stage, timeout_ms=timeout_ms)

    def state_to_file(self, path: str, sections: Optional[List[str]] = None) -> Dict[str, Any]:
        """Dump the L7 snapshot to `path` (feed it to plugin/snapshot/tools/snapdiff.py)."""
        p: Dict[str, Any] = {"path": path}
        if sections:
            p["sections"] = sections
        return self.call("state", p, timeout_s=120)

    def version(self) -> Dict[str, Any]:
        """Which build is this? {gitHash, dirty, buildTime, protocol, pid}. Record it in every run's meta."""
        return self.call("bridge.version", timeout_s=5)

    def plugins(self) -> List[str]:
        """Loaded mod names in load-index order (index 0 first), from the in-game DataHandler."""
        r = self.call("plugins")
        return [p["name"] for p in sorted(r["plugins"], key=lambda p: p["index"])]

    def state(self, sections: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.call("state", {"sections": sections} if sections else None)

    def wait_for(self, expr: Optional[str] = None, *, cmd: Optional[str] = None, op: str = "truthy",
                 value: float = 0, contains: Optional[str] = None, timeout_ms: int = 10000,
                 poll_ms: int = 100) -> Dict[str, Any]:
        """Poll `expr` (an NVSE expression, evaluated through printc) or `cmd` (a console line) on the
        main thread until the condition holds. Raises WaitTimeout if it never does."""
        if (expr is None) == (cmd is None):
            raise ValueError("pass exactly one of expr= or cmd=")
        p: Dict[str, Any] = {"timeoutMs": timeout_ms, "pollMs": poll_ms, "op": op, "value": value}
        if expr is not None:
            p["expr"] = expr
        else:
            p["cmd"] = cmd
        if contains is not None:
            p["contains"] = contains
        r = self.call("waitFor", p, timeout_s=timeout_ms / 1000 + 5)
        if not r.get("satisfied"):
            raise WaitTimeout(expr or cmd or "", r.get("value"))
        return r

    def events(self, since: int = 0, limit: int = 0) -> Dict[str, Any]:
        p: Dict[str, Any] = {"since": since}
        if limit:
            p["limit"] = limit
        return self.call("events", p, timeout_s=5)

    def wait_event(self, type_: str, since: int = 0, timeout_s: float = 30.0, poll_s: float = 0.1) -> Dict[str, Any]:
        """Wait for an event whose type == type_ (seq > since). Returns the event dict."""
        deadline = time.monotonic() + timeout_s
        cur = since
        while True:
            page = self.events(cur)
            for ev in page["events"]:
                if ev["type"] == type_:
                    return ev
            cur = page["next"]
            if time.monotonic() >= deadline:
                raise WaitTimeout(f"event {type_!r}", cur)
            time.sleep(poll_s)

    def screenshot(self, path: str = "") -> Dict[str, Any]:
        return self.call("screenshot", {"path": path} if path else None)

    def load_save(self, name: str, timeout_ms: int = 180000) -> Dict[str, Any]:
        return self.call("loadSave", {"name": name, "timeoutMs": timeout_ms}, timeout_s=timeout_ms / 1000 + 5)

    def save_game(self, name: str, timeout_ms: int = 30000, overwrite: bool = False) -> Dict[str, Any]:
        """Ask the engine to save. Name must start with 'autosave' (engine menu check); the reserved names autosave/quicksave/systemsave*, odd characters and existing saves are refused unless overwrite=True (never target the owner's real saves); returns {'saved':True,'path':<.fos>} when NVSE's SaveGame message fires."""
        p: Dict[str, Any] = {"name": name, "timeoutMs": timeout_ms}
        if overwrite:
            p["overwrite"] = True
        return self.call("saveGame", p, timeout_s=timeout_ms / 1000 + 5)

    def quit(self) -> Dict[str, Any]:
        return self.call("quit", timeout_s=10)


# ------------------------------------------------------------------------------------------------
def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="nvbridge", description="Drive a running FNV instance via nvcoop.dll")
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--wait", type=float, default=0.0, help="seconds to wait for the pipe to appear")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    s = sub.add_parser("exec"); s.add_argument("line")
    s.add_argument("--ref", help="calling reference: Plugin.esm:HEX or a runtime id HEX (the console has no <hex>.Command)")
    s = sub.add_parser("state"); s.add_argument("sections", nargs="*")
    s = sub.add_parser("waitfor")
    s.add_argument("expr"); s.add_argument("--op", default="truthy"); s.add_argument("--value", type=float, default=0)
    s.add_argument("--timeout-ms", type=int, default=10000); s.add_argument("--as-cmd", action="store_true",
                                                                           help="treat EXPR as a console line, not an expression")
    s.add_argument("--contains")
    s = sub.add_parser("events"); s.add_argument("--since", type=int, default=0); s.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("screenshot"); s.add_argument("path", nargs="?", default="")
    s = sub.add_parser("load"); s.add_argument("name"); s.add_argument("--timeout-ms", type=int, default=180000)
    sub.add_parser("quit")
    s = sub.add_parser("call"); s.add_argument("method"); s.add_argument("params", nargs="?", default="{}")
    a = ap.parse_args(argv)

    try:
        with NvBridge.connect(a.instance, wait_s=a.wait) as nv:
            if a.cmd == "ping":
                out = nv.ping()
            elif a.cmd == "exec":
                out = nv.exec(a.line, ref=a.ref)
            elif a.cmd == "state":
                out = nv.state(a.sections or None)
            elif a.cmd == "waitfor":
                kw = dict(op=a.op, value=a.value, timeout_ms=a.timeout_ms, contains=a.contains)
                out = nv.wait_for(cmd=a.expr, **kw) if a.as_cmd else nv.wait_for(a.expr, **kw)
            elif a.cmd == "events":
                out = nv.events(a.since, a.limit)
            elif a.cmd == "screenshot":
                out = nv.screenshot(a.path)
            elif a.cmd == "load":
                out = nv.load_save(a.name, a.timeout_ms)
            elif a.cmd == "quit":
                out = nv.quit()
            else:
                out = nv.call(a.method, json.loads(a.params))
    except RpcError as e:
        print(json.dumps({"error": {"code": e.code, "message": e.message}}), file=sys.stderr)
        return 2
    except WaitTimeout as e:
        print(str(e), file=sys.stderr)
        return 3
    except BridgeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
