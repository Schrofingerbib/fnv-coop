#!/usr/bin/env python3
"""L10 in-game single-instance test (needs the game; ONE instance, minimized, never touches the desktop).

    python -I -X utf8 plugin/coop/tools/ingame_host_test.py [--quest 10A214 --stage 10]

Private instance root <repo>/../fnv-instances-l10/0 (provisioned with out/L10/plugin/nvcoop.dll, autoload=autosave,
nvcoop.ini [coop] enable=1 role=host port=27961). The game runs as HOST; a fake client process (coop_peer.exe, real
UDP transport) joins it. `exec setstage <quest> <stage>` on the host must arrive at the client as ONE EffectTxn
containing SetStage <quest> <stage> (+ its result-script effects).
"""
import argparse, json, os, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "runner"))
sys.path.insert(0, str(REPO / "plugin" / "coop" / "tests"))
os.environ["FNV_INSTANCES_ROOT"] = str(REPO.parent / "fnv-instances-l10")
import nvbridge, run as runner, winproc  # noqa: E402
from test_integration import Peer  # noqa: E402

PORT = 27971


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quest", default="10A214")
    ap.add_argument("--stage", type=int, default=10)
    ap.add_argument("--baseline", action="store_true", help="host starts with baseline=1 (L7 engine dump of the shared world)")
    ap.add_argument("--expect-reject", action="store_true", help="handshake=1 on the game host: the fake client (different build) must be REJECTED with a reason text")
    ap.add_argument("--out", default=str(REPO / "out" / "L10" / "ingame"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    foreign = winproc.list_pids("FalloutNV.exe")
    if foreign:
        sys.exit("FalloutNV.exe already running (pids %s); refusing to launch" % foreign)
    ini = REPO.parent / "fnv-instances-l10" / "0" / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
    lines = ini.read_text().splitlines()
    lines = [("baseline=%d" % (1 if a.baseline else 0)) if l.startswith("baseline=") else l for l in lines]
    ini.write_text(chr(10).join(lines) + chr(10))
    lines = ini.read_text().splitlines()
    lines = [("handshake=%d" % (1 if a.expect_reject else 0)) if l.startswith("handshake=") else l for l in lines]
    if not any(l.startswith("handshake=") for l in lines):
        lines.append("handshake=%d" % (1 if a.expect_reject else 0))
    ini.write_text(chr(10).join(lines) + chr(10))
    pid = runner.launch_instance(0)
    st = runner.load_state(); st["pids"]["0"] = pid; runner.save_state(st)
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    peer = None
    ok = False
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        base = nv.ping()["frames"]
        while nv.ping()["frames"] - base < 600:
            time.sleep(0.25)
        t0 = time.time()
        while a.baseline and not nv.call("coop.status").get("running") and time.time() - t0 < 120:
            time.sleep(0.5)
        s = nv.call("coop.status")
        print("host coop.status:", json.dumps(s))
        assert s["running"] and s["role"] == "host", s
        print("hooks:", json.dumps({k: v for k, v in nv.call("hooks.status").items() if k != "hooks"}))
        peer = Peer("coop_peer.exe", "--role", "client", "--host", "127.0.0.1", "--port", str(PORT), "--token", "777")
        if a.expect_reject:
            time.sleep(6)
            cs = peer.status()["client"]
            print("fake client status:", json.dumps({k: cs.get(k) for k in ("joined", "rejected", "rejectReason", "rejectText")}))
            ok = cs.get("rejected") and cs.get("rejectReason") in (5, 6, 7, 8) and bool(cs.get("rejectText"))
            print("PASS" if ok else "FAIL")
            sys.exit(0 if ok else 1)
        if not peer.wait("wait_joined", 1, 15000):
            print("JOIN FAILED. host status:", json.dumps(nv.call("coop.status")))
            print("client status:", json.dumps(peer.status()))
            raise AssertionError("fake client did not join the in-game host")
        print("client joined; host status:", json.dumps(nv.call("coop.status")["host"]))
        nv.exec("setstage %s %d" % (a.quest, a.stage))
        got = peer.wait("wait_seq", 1, 20000)
        time.sleep(1.0)
        txns = [t for t in peer.txns if t["dir"] == "apply"]
        print("client received %d txn(s):" % len(txns))
        for t in txns:
            print(json.dumps(t))
        (out / "client_txns.json").write_text(json.dumps(txns, indent=1))
        (out / "host_log.json").write_text(json.dumps(nv.call("coop.log", {"n": 16}), indent=1))
        (out / "host_status.json").write_text(json.dumps(nv.call("coop.status"), indent=1))
        want = {"type": "SetStage", "quest": "falloutnv.esm:" + a.quest.upper().zfill(6), "stage": a.stage}
        has = any(want in t["effects"] for t in txns)
        print("RESULT: client got txn with %s: %s; seq-wait=%s; applyStatus=%s" % (
            want, has, got, [t.get("results") for t in txns]))
        ok = has and got
    finally:
        if peer:
            peer.close()
        try:
            nv.quit()
        except Exception:
            pass
        for _ in range(60):
            if not winproc.pid_alive(pid):
                break
            time.sleep(0.5)
        else:
            winproc.kill_pid(pid)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
