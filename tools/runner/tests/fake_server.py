"""A fake nvbridge server in pure Python, speaking the real protocol over a real Windows named pipe.

Lets the client library be tested without the game or the C++ plugin. Console grammar, load order and forms mirror
plugin/bridge/fakehost_main.cpp (set / printc "NVCOOP_EVAL=%g" (name|refid) / echo / event / getref / player.<line>;
"<X>.<word>" fails like the game: Script command "<X>.<word>" not found). exec {cmd, ref} follows plugin/bridge/bridge.cpp.

mutant= selects a deliberate defect the conformance suite must detect:
  wrong_id        replies carry id+1
  events_no_since events ignores `since` (always returns everything)
  drop_error      errors are reported as successful empty results
  waitfor_no_timeout  waitFor claims satisfied when it timed out
  lock_ignore     lease is granted but never enforced
  save_guard      saveGame accepts reserved names / overwrites existing saves
  exec_ref_ignored  exec {cmd, ref} checks the ref, then runs the line with no calling reference
"""
from __future__ import annotations

import ctypes
import json
import re
import threading
import time
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple

PLUGINS = ["FalloutNV.esm", "DeadMoney.esm"]                  # load order of the fake game
FORM_TYPES = {0x00000014: 0x3B, 0x00103E61: 0x3A, 0x01000ABC: 0x3C, 0x0000000F: 0x1F}   # player, door, creature, Caps001
REFERENCE_TYPES = (0x3A, 0x3B, 0x3C)                           # REFR, ACHR, ACRE


def parse_ref(text: str) -> Tuple[Optional[str], int]:
    """The bridge's ParseRefSpec: 'Plugin.esm:HEX' -> (plugin, local id), 'HEX' / '0xHEX' -> (None, runtime id).
    Raises ValueError for anything else."""
    plugin, colon, hexpart = text.rpartition(":")
    if colon and not plugin:
        raise ValueError("the plugin name before ':' is empty")
    if len(hexpart) > 2 and hexpart[:2] in ("0x", "0X"):
        hexpart = hexpart[2:]
    if not re.fullmatch(r"[0-9A-Fa-f]{1,8}", hexpart):
        raise ValueError("the form id must have 1 to 8 hex digits")
    v = int(hexpart, 16)
    if colon and v > 0xFFFFFF:
        raise ValueError("the id inside a plugin is at most FFFFFF")
    return (plugin if colon else None), v

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateNamedPipeW.restype = wintypes.HANDLE
k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
INVALID = wintypes.HANDLE(-1).value
PIPE_ACCESS_DUPLEX = 3


class FakeServer:
    def __init__(self, instance: int, mutant: Optional[str] = None):
        self.name = rf"\\.\pipe\nvcoop-{instance}"
        self.mutant = mutant
        self.vars: Dict[str, float] = {}
        self.events: List[Dict[str, Any]] = []
        self.loaded = 0
        self.lease = None  # (owner, token, expires)
        self._seq = 0
        self.quit_called = threading.Event()
        self._stop = False
        self._t = threading.Thread(target=self._accept, daemon=True)

    def start(self) -> "FakeServer":
        # create the first pipe instance synchronously so clients can connect right away
        self._first = self._new_pipe()
        self._t.start()
        return self

    def stop(self) -> None:
        self._stop = True
        try:  # unblock ConnectNamedPipe
            open(self.name, "r+b", buffering=0).close()
        except OSError:
            pass

    def _new_pipe(self):
        h = k32.CreateNamedPipeW(self.name, PIPE_ACCESS_DUPLEX, 0, 255, 65536, 65536, 0, None)
        if h == INVALID or h is None:
            raise OSError(ctypes.get_last_error(), "CreateNamedPipe failed")
        return h

    def _accept(self) -> None:
        h = self._first
        while not self._stop:
            k32.ConnectNamedPipe(h, None)
            if self._stop:
                break
            threading.Thread(target=self._serve, args=(h,), daemon=True).start()
            h = self._new_pipe()

    def _serve(self, h) -> None:
        buf = b""
        chunk = ctypes.create_string_buffer(4096)
        n = wintypes.DWORD()
        while True:
            if not k32.ReadFile(h, chunk, 4096, ctypes.byref(n), None) or n.value == 0:
                break
            buf += chunk.raw[: n.value]
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                out = (json.dumps(self._handle(line.decode())) + "\n").encode()
                w = wintypes.DWORD()
                k32.WriteFile(h, out, len(out), ctypes.byref(w), None)
        k32.DisconnectNamedPipe(h)
        k32.CloseHandle(h)

    # ---- protocol ----
    def _reply(self, rid, result=None, error=None) -> Dict[str, Any]:
        if self.mutant == "wrong_id" and isinstance(rid, int):
            rid += 1
        if error is not None:
            if self.mutant == "drop_error":
                return {"id": rid, "result": {}}
            return {"id": rid, "error": error}
        return {"id": rid, "result": result}

    def _push(self, type_: str, data: Any = None) -> None:
        self.events.append({"seq": len(self.events) + 1, "tMs": int(time.monotonic() * 1000), "type": type_, "data": data})

    def _exec(self, line: str, self_ref: int = 0) -> Dict[str, Any]:
        """One console line; self_ref is the calling reference (0 = none)."""
        parts = line.split(None, 1)
        cmd = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if "." in cmd[1:]:
            head, _, tail = cmd.partition(".")
            if head.lower() == "player":
                return self._exec(line[line.find(cmd) + len(head) + 1:], 0x14)
            return {"ok": False, "output": 'Script command "%s" not found' % cmd}   # the game's reply to <hexFormID>.Command
        if cmd == "getref":
            if not self_ref:
                return {"ok": False, "output": "getref: no calling reference"}
            return {"ok": True, "output": "REF=%08X" % self_ref}
        if cmd == "set":
            n, v = rest.rsplit(None, 1)
            self.vars[n] = float(v)
            return {"ok": True, "output": ""}
        if cmd == "printc":
            name = rest[rest.find("(") + 1: rest.rfind(")")]
            if name == "refid":
                if not self_ref:
                    return {"ok": False, "output": "refid: no calling reference"}
                return {"ok": True, "output": "NVCOOP_EVAL=%g" % self_ref}
            return {"ok": True, "output": "NVCOOP_EVAL=%g" % self.vars.get(name, 0.0)}
        if cmd == "echo":
            return {"ok": True, "output": rest}
        if cmd == "event":
            t, _, d = rest.partition(" ")
            self._push(t, json.loads(d) if d else None)
            return {"ok": True, "output": ""}
        return {"ok": False, "output": "unknown command"}

    def _handle(self, line: str) -> Dict[str, Any]:
        try:
            req = json.loads(line)
        except ValueError as e:
            return self._reply(None, error={"code": -32700, "message": f"parse error: {e}"})
        if not isinstance(req, dict) or not isinstance(req.get("method"), str):
            return self._reply(req.get("id") if isinstance(req, dict) else None,
                               error={"code": -32600, "message": "missing method"})
        rid, m, p = req.get("id"), req["method"], req.get("params") or {}
        if m.startswith("lock."):
            return self._lock(rid, m, p)
        if m not in ("ping", "events") and self.lease and time.monotonic() < self.lease[2]                 and p.get("lease") != self.lease[1] and self.mutant != "lock_ignore":
            return self._reply(rid, error={"code": -32011, "message": "locked by " + self.lease[0]})
        if m == "bridge.version":
            return self._reply(rid, {"gitHash": "fake", "dirty": False, "buildTime": "n/a", "protocol": 1, "pid": 0})
        if m == "ping":
            return self._reply(rid, {"pong": True, "protocol": 1, "instance": 0, "frames": 1,
                                     "mainLoopAgeMs": 0, "menuReady": True, "loadedCount": self.loaded, "pid": 0})
        if m == "exec":
            if not isinstance(p.get("cmd"), str):
                return self._reply(rid, error={"code": -32602, "message": "exec requires string param 'cmd'"})
            if "ref" in p:
                return self._exec_ref(rid, p["cmd"], p["ref"])
            return self._reply(rid, self._exec(p["cmd"]))
        if m == "state":
            return self._reply(rid, {"todo": "L7"})
        if m == "events":
            since = 0 if self.mutant == "events_no_since" else int(p.get("since", 0))
            evs = [e for e in self.events if e["seq"] > since]
            return self._reply(rid, {"events": evs, "next": evs[-1]["seq"] if evs else since,
                                     "dropped": 0, "last": len(self.events)})
        if m == "waitFor":
            if "expr" not in p and "cmd" not in p:
                return self._reply(rid, error={"code": -32602, "message": "waitFor requires 'expr' or 'cmd'"})
            if p.get("op", "truthy") not in ("truthy", "==", "!=", "<", "<=", ">", ">="):
                return self._reply(rid, error={"code": -32602, "message": "waitFor: bad 'op'"})
            deadline = time.monotonic() + p.get("timeoutMs", 10000) / 1000
            op, rhs = p.get("op", "truthy"), p.get("value", 0)
            while True:
                if "expr" in p:
                    v = self.vars.get(p["expr"], 0.0)
                else:
                    out = self._exec(p["cmd"])["output"]
                    v = out
                if "contains" in p:
                    ok = p["contains"] in str(v)
                else:
                    ok = {"truthy": lambda: v != 0, "==": lambda: v == rhs, "!=": lambda: v != rhs,
                          "<": lambda: v < rhs, "<=": lambda: v <= rhs, ">": lambda: v > rhs,
                          ">=": lambda: v >= rhs}[op]()
                if ok:
                    return self._reply(rid, {"satisfied": True, "timedOut": False, "value": v, "elapsedMs": 0})
                if time.monotonic() >= deadline:
                    sat = self.mutant == "waitfor_no_timeout"
                    return self._reply(rid, {"satisfied": sat, "timedOut": not sat, "value": v, "elapsedMs": 0})
                time.sleep(0.02)
        if m == "loadSave":
            if not isinstance(p.get("name"), str) or not p["name"]:
                return self._reply(rid, error={"code": -32602, "message": "loadSave requires string param 'name'"})
            if p.get("name") == "missing":
                return self._reply(rid, error={"code": -32000, "message": "no such save"})
            self.loaded += 1
            self._push("game.loaded")
            return self._reply(rid, {"loaded": True, "name": p.get("name")})
        if m == "saveGame":
            if not isinstance(p.get("name"), str) or not p["name"]:
                return self._reply(rid, error={"code": -32602, "message": "saveGame requires string param 'name'"})
            import re as _re
            low = p["name"].lower()
            if self.mutant != "save_guard":
                if not _re.fullmatch(r"[A-Za-z0-9_\- ]+", p["name"]) or low in ("autosave", "quicksave") \
                        or low.startswith(("systemsave", "quicksave")):
                    return self._reply(rid, error={"code": -32602, "message": "refusing reserved/odd save name"})
                if p["name"] == "existing" and not p.get("overwrite"):
                    return self._reply(rid, error={"code": -32012, "message": "already exists"})
            if p["name"] == "missing":
                return self._reply(rid, error={"code": -32000, "message": "cannot save"})
            return self._reply(rid, {"saved": True, "path": "Saves\\" + p["name"] + ".fos"})
        if m == "screenshot":
            return self._reply(rid, error={"code": -32000, "message": "screenshot not implemented"})
        if m == "quit":
            self.quit_called.set()
            return self._reply(rid, {"quitting": True})
        return self._reply(rid, error={"code": -32601, "message": f"unknown method: {m}"})

    def _exec_ref(self, rid, cmd: str, text: Any) -> Dict[str, Any]:
        """exec {cmd, ref}, as plugin/bridge/bridge.cpp does it: parse, resolve the plugin, check the form type, run."""
        if not isinstance(text, str):
            return self._reply(rid, error={"code": -32602, "message": "exec: param 'ref' must be a string"})
        try:
            plugin, rid_ = parse_ref(text)
        except ValueError as e:
            return self._reply(rid, error={"code": -32602, "message": "exec: bad ref '%s': %s" % (text, e)})
        if plugin is not None:
            low = [x.lower() for x in PLUGINS]
            if plugin.lower() not in low:
                return self._reply(rid, error={"code": -32013, "message": "exec: ref '%s': plugin '%s' is not loaded" % (text, plugin)})
            rid_ |= low.index(plugin.lower()) << 24
        t = FORM_TYPES.get(rid_)
        if t is None:
            return self._reply(rid, error={"code": -32013, "message": "exec: ref '%s' (form %08X) is not a loaded form" % (text, rid_)})
        if t not in REFERENCE_TYPES:
            return self._reply(rid, error={"code": -32013, "message": "exec: ref '%s' (form %08X, form type 0x%02X) is not a reference"
                                           % (text, rid_, t)})
        out = self._exec(cmd, 0 if self.mutant == "exec_ref_ignored" else rid_)
        return self._reply(rid, dict(out, ref="%08X" % rid_))

    def _lock(self, rid, m, p):
        now = time.monotonic()
        active = bool(self.lease and now < self.lease[2])
        if m == "lock.info":
            return self._reply(rid, {"held": active, "owner": self.lease[0]} if active else {"held": False})
        ttl = p.get("ttlMs", 60000) / 1000
        if m == "lock.acquire":
            owner = p.get("owner")
            if not isinstance(owner, str) or not owner:
                return self._reply(rid, error={"code": -32602, "message": "owner required"})
            if active and self.lease[0] != owner and not p.get("force"):
                return self._reply(rid, error={"code": -32010, "message": "held by " + self.lease[0]})
            if not (active and self.lease[0] == owner):
                self._seq += 1
                self.lease = (owner, "T%d" % self._seq, 0)
            self.lease = (self.lease[0], self.lease[1], now + ttl)
            return self._reply(rid, {"held": True, "owner": owner, "token": self.lease[1]})
        if not active or (self.lease[1] != p.get("token") and not p.get("force")):
            return self._reply(rid, error={"code": -32011, "message": "no matching lease"})
        if m == "lock.release":
            self.lease = None
            return self._reply(rid, {"held": False})
        self.lease = (self.lease[0], self.lease[1], now + ttl)
        return self._reply(rid, {"held": True})
