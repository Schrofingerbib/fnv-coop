"""Pool daemon: a Pool plus a 127.0.0.1 TCP JSON-lines control server and a tick thread.

    python -I tools/pool/daemon.py --size 1 [--fake] [--recycle-after 25] [--base-save NAME] [--grids 3] [--audio-off] [--no-lowcost]

Writes runs/pool/pool.json {port, pid, token} once the instances are warm (env POOL_DIR overrides runs/pool).
Request:  {"op": "...", "token": "...", ...}   Reply: {"ok": true, ...} or {"ok": false, "error": "...", "kind": "busy|error"}
ops: status, lease(owner,n,ttl_s,timeout_s), release(id,dirty), renew(id,ttl_s), dirty(id,instance,reason), down
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend as be  # noqa: E402
import core  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def pool_dir() -> Path:
    return Path(os.environ.get("POOL_DIR") or REPO / "runs" / "pool")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, pool: core.Pool, token: str):
        super().__init__(("127.0.0.1", 0), Handler)
        self.pool, self.token = pool, token
        self.stop = threading.Event()


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            try:
                req = json.loads(raw)
                rep = self.dispatch(req)
            except core.PoolBusy as e:
                rep = {"ok": False, "kind": "busy", "error": str(e)}
            except Exception as e:  # noqa: BLE001
                rep = {"ok": False, "kind": "error", "error": f"{type(e).__name__}: {e}"}
            self.wfile.write((json.dumps(rep) + "\n").encode())
            self.wfile.flush()

    def dispatch(self, r: dict) -> dict:
        srv: Server = self.server  # type: ignore[assignment]
        if r.get("token") != srv.token:
            return {"ok": False, "kind": "error", "error": "bad token"}
        p, op = srv.pool, r.get("op")
        if op == "status":
            return {"ok": True, **p.status()}
        if op == "lease":
            return {"ok": True, "lease": p.lease(str(r["owner"]), int(r.get("n", 1)), float(r.get("ttl_s", 900)),
                                                 float(r.get("timeout_s", 600)))}
        if op == "release":
            return {"ok": True, "released": p.release(r["id"], dirty_all=bool(r.get("dirty")), reason=r.get("reason", ""))}
        if op == "renew":
            return {"ok": True, "renewed": p.renew(r["id"], r.get("ttl_s"))}
        if op == "dirty":
            return {"ok": True, "marked": p.mark_dirty(r["id"], int(r["instance"]), str(r.get("reason", "")))}
        if op == "down":
            threading.Thread(target=srv.stop.set, daemon=True).start()
            return {"ok": True}
        return {"ok": False, "kind": "error", "error": f"unknown op {op!r}"}


def run(a) -> int:
    pdir = pool_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    info_file = pdir / "pool.json"
    if info_file.exists():
        try:
            old = json.loads(info_file.read_text())
            import socket
            socket.create_connection(("127.0.0.1", old["port"]), timeout=1).close()
            print("pool already running on port", old["port"], file=sys.stderr)
            return 2
        except (OSError, ValueError, KeyError):
            info_file.unlink(missing_ok=True)   # stale
    if a.fake:
        backend, slots = be.FakeBackend(), be.FakeSlots()
    else:
        backend = be.RealBackend(boot_timeout_s=a.boot_timeout, base_save=a.base_save, grids=a.grids,
                                 audio_off=a.audio_off, lowcost=not a.no_lowcost)
        slots = be.RealSlots(ttl_s=a.slot_ttl)
    pool = core.Pool(backend, slots, size=a.size, recycle_after=a.recycle_after, ws_growth_mb=a.ws_growth_mb,
                     ws_max_mb=a.ws_max_mb, slot_ttl_s=a.slot_ttl, state_path=pdir / "state.json")
    try:
        ids = pool.up()
    except core.PoolError as e:
        print("pool up failed:", e, file=sys.stderr)
        pool.down()
        return 3
    srv = Server(pool, secrets.token_hex(8))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def ticker() -> None:
        while not srv.stop.is_set():
            try:
                pool.tick()
            except Exception as e:  # noqa: BLE001 - the loop must survive a bad tick
                print("tick error:", type(e).__name__, e, file=sys.stderr)
            srv.stop.wait(a.tick_s)
    threading.Thread(target=ticker, daemon=True).start()
    info_file.write_text(json.dumps({"port": srv.server_address[1], "pid": os.getpid(), "token": srv.token,
                                     "instances": ids, "fake": a.fake}))
    print(f"pool ready: instances {ids} port {srv.server_address[1]}", flush=True)
    try:
        srv.stop.wait()
    except KeyboardInterrupt:
        pass
    info_file.unlink(missing_ok=True)
    srv.shutdown()
    pool.down()
    print("pool down", flush=True)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=1, help="warm instances, 1..2")
    ap.add_argument("--fake", action="store_true", help="in-memory fake game (tests); never launches anything")
    ap.add_argument("--recycle-after", type=int, default=25, help="recycle an instance after K leases")
    ap.add_argument("--ws-growth-mb", type=int, default=1200, help="recycle when working set exceeds boot size + this")
    ap.add_argument("--ws-max-mb", type=int, default=3300, help="recycle above this working set (32-bit game)")
    ap.add_argument("--base-save", default="", help="save to load into each fresh instance")
    ap.add_argument("--grids", type=int, help="opt-in uGridsToLoad (see lowcost.py)")
    ap.add_argument("--audio-off", action="store_true")
    ap.add_argument("--no-lowcost", action="store_true",
                    help="start each instance with the INIs that provisioning writes (removes an earlier low-cost profile)")
    ap.add_argument("--boot-timeout", type=float, default=300)
    ap.add_argument("--slot-ttl", type=int, default=3600)
    ap.add_argument("--tick-s", type=float, default=5.0)
    return ap


if __name__ == "__main__":
    sys.exit(run(parser().parse_args()))
