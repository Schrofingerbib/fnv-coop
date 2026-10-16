"""Protocol conformance checks shared by the Python-fake and C++-core tests.

run(nv) raises AssertionError (or a BridgeError) on the first violated expectation. The mutation
twins assert that run() *does* fail against deliberately broken servers.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nvbridge  # noqa: E402
from nvbridge import RpcError, WaitTimeout  # noqa: E402


def expect_rpc_error(fn, code):
    try:
        fn()
    except RpcError as e:
        assert e.code == code, f"expected error {code}, got {e.code}: {e.message}"
        return e
    raise AssertionError(f"expected RpcError {code}, call succeeded")


def run(nv, expect_state_stub=True, check_load=True, connect2=None):
    # ping
    p = nv.ping()
    assert p["protocol"] == nvbridge.PROTOCOL_VERSION, p

    v = nv.version()
    assert isinstance(v["gitHash"], str) and v["gitHash"] and "buildTime" in v, v

    # exec round trip + ids stay matched over many calls
    for i in range(20):
        r = nv.exec(f"echo hello {i}")
        assert r == {"ok": True, "output": f"hello {i}"}, r
    assert nv.exec("bogus_command_xyz")["ok"] is False

    # error shapes
    expect_rpc_error(lambda: nv.call("no_such_method"), -32601)
    expect_rpc_error(lambda: nv.call("exec", {}), -32602)
    expect_rpc_error(lambda: nv.call("exec", {"cmd": 5}), -32602)
    expect_rpc_error(lambda: nv.call("waitFor", {"timeoutMs": 10}), -32602)

    # malformed input does not kill the connection and yields a parse error with null id
    nv.send_raw("{this is not json")
    msg = nv.recv_raw(5)
    assert msg["id"] is None and msg["error"]["code"] == -32700, msg
    assert nv.ping()["pong"]

    # state
    st = nv.state()
    if expect_state_stub:
        assert st == {"todo": "L7"}, st

    # events: cursor semantics
    base = nv.events(0)["next"]
    nv.exec("event conf.a")
    nv.exec('event conf.b {"n":7,"s":"x\\ny"}')
    page = nv.events(base)
    types = [e["type"] for e in page["events"]]
    assert types == ["conf.a", "conf.b"], types
    assert page["events"][1]["data"] == {"n": 7, "s": "x\ny"}, page["events"][1]
    assert page["next"] == page["events"][-1]["seq"]
    again = nv.events(page["next"])
    assert again["events"] == [], again
    assert nv.wait_event("conf.b", since=base, timeout_s=2)["data"]["n"] == 7
    try:
        nv.wait_event("conf.never", since=page["next"], timeout_s=0.3)
        raise AssertionError("wait_event should time out")
    except WaitTimeout:
        pass

    # waitFor: satisfied now, satisfied later, timeout is a clean failure, bad op rejected
    nv.exec("set stage 10")
    r = nv.wait_for("stage", op=">=", value=10, timeout_ms=2000)
    assert r["satisfied"] and r["value"] == 10, r
    nv.exec("set stage 3")
    t0 = __import__("time").monotonic()
    try:
        nv.wait_for("stage", op="==", value=99, timeout_ms=300, poll_ms=20)
        raise AssertionError("wait_for should have timed out")
    except WaitTimeout as e:
        assert e.last == 3, e.last
    raw = nv.call("waitFor", {"expr": "stage", "op": "==", "value": 99, "timeoutMs": 200, "pollMs": 20})
    assert raw["satisfied"] is False and raw["timedOut"] is True, raw
    r = nv.wait_for(cmd="echo status ready", contains="ready", timeout_ms=1000)
    assert r["satisfied"], r
    expect_rpc_error(lambda: nv.call("waitFor", {"expr": "stage", "op": "~~"}), -32602)

    if check_load:
        l = nv.load_save("quicksave1", timeout_ms=5000)
        assert l["loaded"] is True and l["name"] == "quicksave1", l
        expect_rpc_error(lambda: nv.load_save("missing", timeout_ms=1000), -32000)
        expect_rpc_error(lambda: nv.call("loadSave", {}), -32602)
        nv.wait_event("game.loaded", since=0, timeout_s=2)
        sv = nv.save_game("mysave", timeout_ms=5000)
        assert sv["saved"] is True and sv["path"].endswith("mysave.fos"), sv
        expect_rpc_error(lambda: nv.save_game("missing", timeout_ms=1000), -32000)
        # guards: never overwrite the owner's real saves
        for bad in ("autosave", "AutoSave", "quicksave", "systemsave Courier", "a/b", "..\\x", "x.fos", ""):
            expect_rpc_error(lambda b=bad: nv.save_game(b, timeout_ms=1000), -32602)
        expect_rpc_error(lambda: nv.save_game("existing", timeout_ms=1000), -32012)
        assert nv.save_game("existing", timeout_ms=5000, overwrite=True)["saved"] is True
        expect_rpc_error(lambda: nv.call("saveGame", {}), -32602)
    if connect2 is not None:
        check_lease(nv, connect2())
    return True


def check_lease(a, b):
    import time
    assert a.lease_info() == {"held": False}, a.lease_info()
    r = a.acquire_lease("laneA", ttl_ms=5000)
    assert r["held"] and r["owner"] == "laneA" and r["token"], r
    assert a.exec("echo mine")["output"] == "mine"          # holder works (token attached)
    expect_rpc_error(lambda: b.exec("echo nope"), -32011)    # others are locked out
    expect_rpc_error(lambda: b.call("loadSave", {"name": "x"}), -32011)
    b.ping(); b.events(0)                                    # read-only liveness stays open
    assert b.lease_info()["owner"] == "laneA"
    expect_rpc_error(lambda: b.acquire_lease("laneB"), -32010)
    expect_rpc_error(lambda: b.call("lock.release", {"token": "bogus"}), -32011)
    a.renew_lease(5000)
    a.release_lease()
    assert b.exec("echo free")["output"] == "free"
    # expiry frees the instance without an explicit release
    a.acquire_lease("laneA", ttl_ms=300)
    expect_rpc_error(lambda: b.exec("echo x"), -32011)
    time.sleep(0.5)
    assert b.exec("echo expired")["output"] == "expired"
    b.acquire_lease("laneB", ttl_ms=5000)
    b.release_lease()
    a.lease = None
