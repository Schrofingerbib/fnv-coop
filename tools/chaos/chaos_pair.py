#!/usr/bin/env python3
"""Run a test with a booted two-copy pair joined THROUGH the chaos relay (docs/tools/T5-chaos.md), then put the pair back.

    python -I tools/chaos/chaos_pair.py --instances 0,1 --info-file runs/chaos.json -- \\
        tools/bots/run_fight.py --instances 0,1 --which fight --chaos bad_wifi --chaos-seed 7 --chaos-rpc runs/chaos.json

What it does, in order:
 1. reads the host's real co-op port from the host copy's nvcoop.ini ([coop] port, default 27960);
 2. starts a seeded UDP relay on 127.0.0.1 that forwards to that port, with the clean `lan` profile (the test picks the impaired
    profile itself through the RPC info file: run_fight.py / run_coop.py `--chaos P --chaos-seed S --chaos-rpc <info file>`);
 3. writes the info file `{"listen": port, "rpc": port, "log": path}` (the format of `chaos.py relay --info-file`);
 4. tells the CLIENT copy over its bridge to `coop.leave` and `coop.join {host: 127.0.0.1, port: <relay>}`, and waits until
    `coop.status` says `client.joined` (the join itself goes through the relay, so a broken relay shows here);
 5. runs the command after `--` (a repo relative .py is run with this Python) and passes its exit code on;
 6. always, last: the client leaves the relay and joins the host's real port again, and the relay stops.

Exit: the wrapped command's code; 2 when a game copy cannot be reached ("cannot reach the game instances", which prove.py classes as
ENV); 1 when the client did not join through the relay (a real failure of the relay path); 3 never. Nothing here launches or stops a
game copy. The game-free tests are tools/chaos/test_chaos_pair.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for _p in (str(HERE), str(REPO / "tools" / "runner")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MUTANT = os.environ.get("NVCHAOS_MUTANT", "")   # test-only: one deliberate bug per name (tools/chaos/run_tests.py)
DEFAULT_PORT = 27960
LOOPBACK = "127.0.0.1"


def err(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def host_port(ini_text: str) -> int:
    """[coop] port of an nvcoop.ini text (DEFAULT_PORT when the key is absent). ValueError when it is not a usable port."""
    sec = ""
    for ln in ini_text.splitlines():
        s = ln.split(";", 1)[0].strip()
        m = re.match(r"^\[(.+)\]$", s)
        if m:
            sec = m.group(1).strip().lower()
            continue
        k, eq, v = s.partition("=")
        if eq and sec == "coop" and k.strip().lower() == "port":
            port = int(v.strip())
            if not 1 <= port <= 65535:
                raise ValueError(f"[coop] port {port} is not a UDP port")
            return port
    return DEFAULT_PORT


def joined(status: Any) -> bool:
    """True when a client's coop.status says it is in the session."""
    if not isinstance(status, dict):
        return False
    c = status.get("client")
    return bool(isinstance(c, dict) and c.get("joined") is True and status.get("running") is not False)


class SetupError(Exception):
    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code


class ChaosPair:
    """Everything that touches a game copy or a socket comes in as an argument, so the tests run without a game."""

    def __init__(self, *, host_ini: Callable[[], str], bridge: Callable[[], Any], relay_factory: Callable[[int], Any],
                 info_file: Optional[Path] = None, join_wait_s: float = 60.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, rpc_factory: Optional[Callable[[Any], Any]] = None):
        self.host_ini, self.bridge, self.relay_factory = host_ini, bridge, relay_factory
        self.info_file, self.join_wait_s, self.sleep, self.clock = info_file, join_wait_s, sleep, clock
        self.rpc_factory = rpc_factory
        self.relay: Any = None
        self.rpc: Any = None
        self.real_port = DEFAULT_PORT
        self.rejoined = False

    # -- the client copy
    def _client_call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout_s: float = 35.0) -> Any:
        try:
            with self.bridge() as nv:
                return nv.call(method, params or {}, timeout_s=timeout_s)
        except Exception as e:          # noqa: BLE001  BridgeError, RpcError, OSError: the caller decides what it means
            e.args = (f"{method}: {e}",) + tuple(e.args[1:])
            raise

    def _wait_joined(self) -> bool:
        t0 = self.clock()
        while True:
            try:
                if joined(self._client_call("coop.status")):
                    return True
            except Exception:           # noqa: BLE001  keep polling: the session is being rebuilt
                pass
            if self.clock() - t0 >= self.join_wait_s:
                return False
            self.sleep(1.0)

    def setup(self) -> None:
        try:
            self.real_port = host_port(self.host_ini())
        except (OSError, ValueError) as e:
            raise SetupError(f"cannot read the host's nvcoop.ini ({e})", 1)
        self.relay = self.relay_factory(self.real_port)
        if self.rpc_factory:
            self.rpc = self.rpc_factory(self.relay)
        if self.info_file:
            self.info_file.parent.mkdir(parents=True, exist_ok=True)
            self.info_file.write_text(json.dumps({"listen": self.relay.port, "rpc": getattr(self.rpc, "port", 0),
                                                  "log": getattr(self.relay, "log_path", None)}), encoding="utf-8")
        try:
            try:
                self._client_call("coop.leave")
            except Exception:           # noqa: BLE001  "not in a session" is fine: join builds one; a dead pipe fails the join below too
                pass
            self._client_call("coop.join", {"host": LOOPBACK, "port": self.real_port if MUTANT == "pair_joins_direct" else self.relay.port})
        except Exception as e:          # noqa: BLE001
            raise SetupError(f"cannot reach the game instances: {e}", 2)
        if not self._wait_joined():
            raise SetupError(f"FAIL chaos setup: the client did not join through the relay (127.0.0.1:{self.relay.port} -> "
                             f"{LOOPBACK}:{self.real_port}) within {self.join_wait_s:g} s", 1)

    def teardown(self) -> None:
        """Best effort, never raises: the client joins the host's real port again; the relay stops."""
        if self.relay is not None and not self.rejoined and MUTANT != "pair_no_rejoin":
            try:
                try:
                    self._client_call("coop.leave")
                except Exception:       # noqa: BLE001
                    pass
                self._client_call("coop.join", {"host": LOOPBACK, "port": self.real_port})
                self.rejoined = True
            except Exception as e:      # noqa: BLE001
                err(f"chaos_pair: could not put the client back on the host's port: {e}")
        for obj, name in ((self.rpc, "shutdown"), (self.relay, "close")):
            if obj is not None:
                try:
                    getattr(obj, name)()
                except Exception:       # noqa: BLE001
                    pass
        self.relay = self.rpc = None


def wrapped_argv(cmd: Sequence[str], python: Optional[Sequence[str]] = None, repo: Path = REPO) -> List[str]:
    py = list(python or [sys.executable, "-I", "-X", "utf8"])
    cmd = list(cmd)
    if cmd and cmd[0].endswith(".py"):
        p = Path(cmd[0])
        return py + [str(p if p.is_absolute() else repo / p)] + cmd[1:]
    return cmd


def run(pair: ChaosPair, cmd: Sequence[str], runner: Callable[[List[str]], int] = lambda argv: subprocess.call(argv, cwd=str(REPO))) -> int:
    try:
        pair.setup()
    except SetupError as e:
        print(str(e), flush=True)
        pair.teardown()
        return e.code
    try:
        return runner(wrapped_argv(cmd))
    finally:
        pair.teardown()


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd: List[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", required=True, help="host,client instance numbers, for example 0,1")
    ap.add_argument("--info-file", required=True, help="where to write the relay's {listen, rpc, log} JSON (give the same path to the test's --chaos-rpc)")
    ap.add_argument("--log", default="", help="packet log of the relay (default: next to the info file)")
    ap.add_argument("--join-wait", type=float, default=60.0, help="seconds to wait for the client to join through the relay")
    a = ap.parse_args(argv)
    if not cmd:
        ap.error("give the command to run after --")
    try:
        host_i, client_i = (int(x) for x in a.instances.split(","))
    except ValueError:
        ap.error("--instances needs two numbers: host,client")
    import instances as inst                       # noqa: WPS433  tools/runner
    import nvbridge                                # noqa: WPS433
    from relay import Relay, RpcServer             # noqa: WPS433
    info = Path(a.info_file)
    log = a.log or str(info.with_suffix(".packets.jsonl"))

    def make_relay(port: int):
        return Relay((LOOPBACK, port), 0, "lan", None, log)
    pair = ChaosPair(host_ini=lambda: (inst.instance_dir(host_i) / "Data" / "NVSE" / "Plugins" / "nvcoop.ini").read_text(encoding="mbcs", errors="replace"),
                     bridge=lambda: nvbridge.NvBridge.connect(client_i, wait_s=10), relay_factory=lambda p: make_relay(p).start(),
                     info_file=info, join_wait_s=a.join_wait, rpc_factory=lambda r: RpcServer(r, 0).start())
    return run(pair, cmd)


if __name__ == "__main__":
    sys.exit(main())
