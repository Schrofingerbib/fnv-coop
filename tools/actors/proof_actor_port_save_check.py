"""In-game proof of the actor port's save check (the NVSE cosave record 'ACLG'), fully scripted (docs/INGAME-TESTS.md "Step 1-2 Enemy sync:
actor-port" item 6, docs/PROOF-PLAN.md 4.5). No human step: bridge calls only (actors.dev, actors.port, saveGame, coop.leave, loadSave, exec/eval).

    python -I tools/actors/proof_actor_port_save_check.py --instance 0 --ref Plugin.esm:HEX --inst-dir <instance dir> [--save-name autosaveXYZ] [--outdir DIR]
    python -I tools/actors/proof_actor_port_save_check.py --selftest

Setup: ONE game copy, nvcoop.ini `[dev] actorport=1` and `[actors] engine=1`, a save loaded next to an ordinary living NPC (--ref). A co-op session is
not needed; if the copy IS in a session the script ends it with coop.leave (the "end the session" step). The save it writes is named by --save-name
(default autosaveaclgcheck, overwritten on every run; it never touches another save).

What it asserts, in order (each line of the report is one check):
  setup     actors.port and actors.dev answer; the NPC is NOT restrained to begin with.
  restrain  the port restrains the NPC (actors.dev ai off): GetRestrained 1 and the ledger counts it. (This is the state the save must capture.)
  save      saveGame returns saved:true while the NPC is restrained and the ledger holds it (the cosave record is written by that save).
  end       the session is over: if actors.port said session=true, coop.leave is called and actors.port reports session=false; then the live ledger is
            emptied (actors.dev release: the port forgets everything, like a run that ended long ago) and the live NPC is not restrained. The saved game
            still holds the NPC restrained and only its cosave remembers that.
  load      loadSave of that save, then wait until the game is ready again (actors.port worldReady and a console eval works).
  verdict   the instance log (Data/NVSE/Plugins/nvcoop.log, read from the byte where it stood before the load) gets the line
            'actors: cosave of the loaded game added N ledger entries to undo' with N >= 1; GetRestrained on the NPC is 0 (the port undid it: it is polled
            for UNDO_TIMEOUT_S because the undo runs on the next world change); actors.port ledger restrained is back to 0; no console line was refused
            (stats.consoleFailed did not rise) during the load. The rise of stats.ledgerReleased is reported but not judged.

Exit code: 0 pass, 1 a check failed, 2 usage / cannot reach the game, 3 not ready (nothing judged: no actors.port / actors.dev, the NPC is already
restrained, the log cannot be read). Every timeout is a first guess.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "runner"))
from proof_actor_port import NotReady, Result  # noqa: E402

DEFAULT_SAVE = "autosaveaclgcheck"
COSAVE_LINE = re.compile(r"actors: cosave of the loaded game added (\d+) ledger entries")
READY_TIMEOUT_S = 90.0
BOOT_READY_TIMEOUT_S = 240.0     # a freshly booted copy: the main thread serves nothing until the game has loaded
NPC_READ_TIMEOUT_S = 20.0
LOADING_MARKS = ("main thread did not service", "is the game loading", "timed out", "timeout")
UNDO_TIMEOUT_S = 30.0
LOG_TIMEOUT_S = 15.0
LEAVE_TIMEOUT_S = 25.0
POLL_S = 0.5
LEDGER_KEYS = ("restrained", "standIn", "disabled", "essential", "stale")


class LogTail:
    """Reads what an instance's nvcoop.log gained since mark(). A log that shrank (rotated or truncated) is read from its start."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.pos = 0

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return -1

    def mark(self) -> None:
        self.pos = max(0, self.size())

    def since(self) -> str:
        n = self.size()
        if n < 0:
            return ""
        if n < self.pos:
            self.pos = 0
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            return f.read().decode("utf-8", "replace")


class Proof:
    """`bridge` is NvBridge-like: call(method, params), eval(expr, ref=...), exec(cmd, ref=...), load_save(name), save_game(name, overwrite=...)."""

    def __init__(self, bridge: Any, res: Result, npc: str, log: Any, save_name: str = DEFAULT_SAVE,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> None:
        self.b, self.r, self.npc, self.log, self.save_name = bridge, res, npc, log, save_name
        self.sleep, self.now = sleep, now

    # ---- reads ---------------------------------------------------------------------------------------------------------------------
    def port(self) -> Dict[str, Any]:
        return self.b.call("actors.port", {})

    def led(self, st: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        return {k: int((st or self.port()).get("ledger", {}).get(k, 0)) for k in LEDGER_KEYS}

    def restrained(self) -> Optional[int]:
        try:
            return int(self.b.eval("GetRestrained", ref=self.npc))
        except Exception:  # noqa: BLE001
            return None

    def poll(self, pred: Callable[[], bool], timeout_s: float) -> bool:
        end = self.now() + timeout_s
        while True:
            try:
                if pred():
                    return True
            except Exception:  # noqa: BLE001 - the game is busy loading: try again
                pass
            if self.now() >= end:
                return False
            self.sleep(POLL_S)

    # ---- steps ---------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def loading(e: BaseException) -> bool:
        """The bridge answered "main thread did not service the request (is the game loading?)" (-32001) or timed out: the game is not up yet."""
        return getattr(e, "code", None) == -32001 or any(m in str(e).lower() for m in LOADING_MARKS)

    def wait_ready(self) -> Dict[str, Any]:
        """Wait until the game has finished loading (actors.port answers and says worldReady) instead of calling NOT READY on a copy that was
        booted a moment ago. Any other error (a method that does not exist) is NOT READY at once. -> the actors.port answer."""
        end = self.now() + BOOT_READY_TIMEOUT_S
        last: Any = "no answer yet"
        while True:
            try:
                st = self.port()
                if st.get("worldReady", True):
                    return st
                last = "actors.port says worldReady=false"
            except Exception as e:  # noqa: BLE001
                if not self.loading(e):
                    raise NotReady(f"actors.port is not registered ({e}): nvcoop.dll without plugin/actors/engine") from e
                last = e
            if self.now() >= end:
                raise NotReady(f"the game did not finish loading within {BOOT_READY_TIMEOUT_S:.0f} s ({last})")
            self.sleep(POLL_S)

    def setup(self) -> None:
        st = self.wait_ready()
        if not st.get("enabled"):
            raise NotReady("the actor port is disabled ([actors] engine=0)")
        try:
            self.b.call("actors.read", {"ref": self.npc})
        except Exception as e:  # noqa: BLE001
            raise NotReady(f"actors.read / actors.dev are not registered ({e}): set nvcoop.ini [dev] actorport=1") from e
        self.poll(lambda: self.restrained() is not None, NPC_READ_TIMEOUT_S)       # the NPC's cell finishes loading a moment after the world
        if self.restrained() != 0:
            raise NotReady(f"{self.npc} is restrained (or unreadable) before the test: pick an ordinary free NPC with --ref")
        if self.led(st)["restrained"] != 0:
            raise NotReady(f"the port's ledger already holds restrained actors {self.led(st)}: start from a clean boot")
        self.r.check("setup.npc_free", True, self.npc)

    def restrain(self) -> None:
        c = self.r.check
        self.b.call("actors.dev", {"op": "ai", "ref": self.npc, "on": False})
        c("restrain.getrestrained_1", self.restrained() == 1, f"GetRestrained = {self.restrained()}")
        led = self.led()
        c("restrain.ledger_has_it", led["restrained"] == 1, f"ledger {led}")

    def save(self) -> None:
        c = self.r.check
        self.log.mark()
        try:
            res = self.b.save_game(self.save_name, overwrite=True)
            c("save.saved", bool(res.get("saved", True)), str(res))
        except Exception as e:  # noqa: BLE001
            c("save.saved", False, f"saveGame failed: {e}")

    def end_session(self) -> None:
        c = self.r.check
        st = self.port()
        if st.get("session"):
            try:
                self.b.call("coop.leave", {})
                left = self.poll(lambda: not self.port().get("session"), LEAVE_TIMEOUT_S)
            except Exception as e:  # noqa: BLE001
                left = False
                self.r.info["leave_error"] = str(e)
            c("end.session_left", left, "coop.leave did not end the session (actors.port session still true)")
        else:
            self.r.info["session"] = "none (single copy)"
        self.b.call("actors.dev", {"op": "release"})
        led = self.led()
        c("end.ledger_empty", all(v == 0 for v in led.values()), f"ledger {led}")
        c("end.live_npc_free", self.restrained() == 0, f"GetRestrained = {self.restrained()}")

    def load(self) -> None:
        c = self.r.check
        before = self.port()
        self.r.info["released_before"] = int(before.get("stats", {}).get("ledgerReleased", 0))
        self.r.info["failed_before"] = int(before.get("stats", {}).get("consoleFailed", 0))
        self.log.mark()
        try:
            self.b.load_save(self.save_name)
            c("load.accepted", True, self.save_name)
        except Exception as e:  # noqa: BLE001
            c("load.accepted", False, f"loadSave failed: {e}")
            return
        ready = self.poll(lambda: bool(self.port().get("worldReady", True)) and self.b.eval("GetRestrained", ref=self.npc) in (0.0, 1.0), READY_TIMEOUT_S)
        c("load.game_ready", ready, "actors.port worldReady / a console eval never worked again after the load")

    def verdict(self) -> None:
        c = self.r.check
        found: List[int] = []

        def see_line() -> bool:
            found[:] = [int(m.group(1)) for m in COSAVE_LINE.finditer(self.log.since())]
            return bool(found)
        got = self.poll(see_line, LOG_TIMEOUT_S)
        c("verdict.cosave_line_logged", got, "no 'actors: cosave of the loaded game added N ledger entries' line in nvcoop.log since the load")
        if got:
            c("verdict.cosave_added_entries", found[-1] >= 1, f"the line said N = {found[-1]}")
        free = self.poll(lambda: self.restrained() == 0, UNDO_TIMEOUT_S)
        c("verdict.npc_not_frozen", free, f"GetRestrained on the NPC = {self.restrained()} {UNDO_TIMEOUT_S:.0f} s after the load")
        free = self.poll(lambda: self.led()["restrained"] == 0, UNDO_TIMEOUT_S)
        c("verdict.ledger_restrained_zero", free, f"ledger {self.led()}")
        st = self.port()
        stats = st.get("stats", {})
        c("verdict.console_clean", int(stats.get("consoleFailed", 0)) == self.r.info["failed_before"],
          f"consoleFailed {self.r.info['failed_before']} -> {stats.get('consoleFailed', 0)}")
        self.r.info["ledgerReleased_delta"] = int(stats.get("ledgerReleased", 0)) - self.r.info["released_before"]

    def run(self) -> Result:
        self.r.info.update({"npc": self.npc, "save": self.save_name})
        self.setup()
        self.restrain()
        self.save()
        self.end_session()
        self.load()
        self.verdict()
        return self.r


# ---- offline self-test: a fake single-copy game with a save, a cosave and a log --------------------------------------------------------
class FakeLog:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.pos = 0

    def mark(self) -> None:
        self.pos = len(self.lines)

    def since(self) -> str:
        return "\n".join(self.lines[self.pos:])


class FakeGame:
    """One game copy. A save keeps the NPC's restrained flag (the engine's own data) and, in its cosave, the port's ledger. Loading it logs the cosave
    line and the undo runs UNDO_AFTER seconds later (it runs on a world change). Faults each break one promise."""

    READY_AFTER = 2.0
    BOOT_AFTER = 30.0     # fault "slow_boot": a freshly booted copy answers nothing for this long
    UNDO_AFTER = 4.0

    def __init__(self, faults: Optional[set] = None, session: bool = False) -> None:
        self.f = faults or set()
        self.t = 0.0
        self.session = session
        self.restr = False
        self.ledger = 0
        self.saves: Dict[str, Tuple[bool, int]] = {}
        self.ready_at = 0.0 if "slow_boot" not in self.f else self.BOOT_AFTER
        self.undo_at: Optional[float] = None
        self.failed = 0
        self.released = 0
        self.log = FakeLog()

    def sleep(self, s: float) -> None:
        self.t += s
        self._tick()

    def now(self) -> float:
        return self.t

    def _tick(self) -> None:
        if self.undo_at is not None and self.t >= self.undo_at:
            self.undo_at = None
            if "undo_fails" not in self.f:
                self.restr = False
            if "ledger_leak" not in self.f:
                self.ledger = 0
            self.released += 1
            if "console_refused" in self.f:
                self.failed += 2

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        self._tick()
        if self.t < self.ready_at:
            raise RuntimeError("loading")
        if expr == "GetRestrained":
            return 1.0 if self.restr else 0.0
        raise KeyError(expr)

    def exec(self, cmd: str, ref: Optional[str] = None) -> dict:
        return {"ok": True, "output": ""}

    def save_game(self, name: str, timeout_ms: int = 0, overwrite: bool = False) -> dict:
        if "save_refused" in self.f:
            return {"saved": False}
        self.saves[name] = (self.restr, self.ledger)
        return {"saved": True, "path": name + ".fos"}

    def load_save(self, name: str, timeout_ms: int = 0) -> dict:
        restr, led = self.saves[name]
        self.restr = restr
        self.ledger = 0
        self.ready_at = self.t + self.READY_AFTER
        if "never_ready" in self.f:
            self.ready_at = 1e9
        if "no_cosave" not in self.f and led:
            added = 0 if "cosave_zero" in self.f else led
            self.ledger = added
            self.log.lines.append(f"[nvcoop] actors: cosave of the loaded game added {added} ledger entries to undo")
        elif "no_cosave" in self.f:
            self.ledger = 0
        self.undo_at = self.t + self.UNDO_AFTER
        return {"loaded": True, "name": name}

    def call(self, method: str, params: Optional[dict] = None) -> Any:
        p = params or {}
        self._tick()
        if "slow_boot" in self.f and self.t < self.BOOT_AFTER:
            raise RuntimeError("[-32001] main thread did not service the request (is the game loading?)")
        if method == "actors.port":
            return {"enabled": True, "session": self.session, "worldReady": self.t >= self.ready_at, "stats": {"consoleFailed": self.failed, "ledgerReleased": self.released},
                    "ledger": {"restrained": self.ledger, "standIn": 0, "disabled": 0, "essential": 0, "stale": 0}}
        if method == "actors.read":
            return {"ref": "00104C70"}
        if method == "actors.dev":
            if p["op"] == "ai" and not p.get("on", True):
                if "not_restrained" not in self.f:
                    self.restr = True
                self.ledger = 1
            elif p["op"] == "release":
                if "release_leak" not in self.f:
                    self.restr = False
                    self.ledger = 0
            return {"ok": True}
        if method == "coop.leave":
            self.session = False
            return {"ok": True}
        raise KeyError(method)


TWINS = {   # fault -> the check that must fail
    "not_restrained": "restrain.getrestrained_1", "save_refused": "save.saved", "release_leak": "end.ledger_empty", "never_ready": "load.game_ready",
    "no_cosave": "verdict.cosave_line_logged", "cosave_zero": "verdict.cosave_added_entries", "undo_fails": "verdict.npc_not_frozen",
    "ledger_leak": "verdict.ledger_restrained_zero", "console_refused": "verdict.console_clean",
}


def run_fake(faults: set, session: bool = False) -> Result:
    g = FakeGame(faults, session)
    res = Result()
    with contextlib.redirect_stdout(io.StringIO()):
        Proof(g, res, "FalloutNV.esm:104C70", g.log, sleep=g.sleep, now=g.now).run()
    return res


def selftest() -> int:
    ok = True
    for session in (False, True):
        good = run_fake(set(), session)
        if good.failures:
            print(f"selftest: a correct game (session={session}) failed the proof:", good.failures)
            ok = False
        else:
            print(f"selftest: a correct game (session={session}) passes all {len(good.checks)} checks")
    for fault, expect in TWINS.items():
        res = run_fake({fault})
        names = [f.split(":")[0] for f in res.failures]
        if expect not in names:
            print(f"selftest: TWIN SURVIVED {fault!r}: expected {expect} to fail, failures were {names}")
            ok = False
        else:
            print(f"selftest: twin {fault!r} caught by {expect}")
    # a copy that stays in its session must be told to leave: a coop.leave that does nothing is caught
    class StuckSession(FakeGame):
        def call(self, method: str, params: Optional[dict] = None) -> Any:
            if method == "coop.leave":
                return {"ok": True}
            return super().call(method, params)

    g = StuckSession(set(), True)
    res = Result()
    with contextlib.redirect_stdout(io.StringIO()):
        Proof(g, res, "FalloutNV.esm:104C70", g.log, sleep=g.sleep, now=g.now).run()
    if "end.session_left" not in [f.split(":")[0] for f in res.failures]:
        print("selftest: TWIN SURVIVED 'coop.leave does nothing'")
        ok = False
    else:
        print("selftest: twin 'coop.leave does nothing' caught by end.session_left")
    # a copy booted a moment ago answers "is the game loading?" for a while: the proof waits, it does not call that NOT READY ...
    slow = run_fake({"slow_boot"})
    if slow.failures or not slow.checks:
        print("selftest: a slow booting game was not waited for:", slow.failures)
        ok = False
    else:
        print("selftest: a slow booting game is waited for and passes")
    # ... and a proof without that wait is caught by this very test (twin)
    real_wait = Proof.wait_ready
    def no_wait(self):
        try:
            return self.port()
        except Exception as e:  # noqa: BLE001
            raise NotReady(f"actors.port is not registered ({e})") from e
    Proof.wait_ready = no_wait
    try:
        run_fake({"slow_boot"})
        print("selftest: TWIN SURVIVED 'no wait for the game to load'")
        ok = False
    except NotReady:
        print("selftest: twin 'no wait for the game to load' caught (NOT READY on a booting game)")
    finally:
        Proof.wait_ready = real_wait
    # not-ready: an NPC that is restrained before the test; no actors.port
    g = FakeGame()
    g.restr = True
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            Proof(g, Result(), "FalloutNV.esm:104C70", g.log, sleep=g.sleep, now=g.now).run()
        print("selftest: an NPC restrained before the test was not reported as not ready")
        ok = False
    except NotReady:
        print("selftest: an NPC restrained before the test is reported as not ready")

    class Dead:
        def call(self, method: str, params: Optional[dict] = None) -> Any:
            raise RuntimeError("no such method")

    try:
        Proof(Dead(), Result(), "x", FakeLog()).run()
        print("selftest: a missing actors.port was not reported as not ready")
        ok = False
    except NotReady:
        print("selftest: a missing actors.port is reported as not ready")
    # the log tail on a real file: reads only what came after the mark, and restarts from 0 when the file shrank
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "nvcoop.log"
        p.write_text("old line\n", encoding="utf-8")
        t = LogTail(p)
        t.mark()
        with open(p, "a", encoding="utf-8") as f:
            f.write("actors: cosave of the loaded game added 2 ledger entries to undo\n")
        m = COSAVE_LINE.search(t.since())
        if not (m and m.group(1) == "2" and "old line" not in t.since()):
            print("selftest: LogTail did not read only the new lines")
            ok = False
        p.write_text("new\n", encoding="utf-8")
        if t.since().strip() != "new":
            print("selftest: LogTail did not restart from 0 after the log shrank")
            ok = False
        if LogTail(Path(d) / "missing.log").since() != "":
            print("selftest: LogTail of a missing file is not empty")
            ok = False
    if ok:
        print("proof_actor_port_save_check selftest: ALL OK")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--ref", default=None, help="Plugin.esm:HEX of an ordinary living NPC")
    ap.add_argument("--inst-dir", default=None, help="the instance's game directory (the log is <dir>/Data/NVSE/Plugins/nvcoop.log); default: the instance dir of tools/runner")
    ap.add_argument("--log", default=None, help="path of nvcoop.log (overrides --inst-dir)")
    ap.add_argument("--save-name", default=DEFAULT_SAVE, help="name of the save the test writes and loads (default %(default)s)")
    ap.add_argument("--outdir", default="out/actor-port/save-check")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--selftest", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    if a.selftest:
        return selftest()
    if not a.ref:
        print("--ref Plugin.esm:HEX (an ordinary living NPC) is required", file=sys.stderr)
        return 2
    if a.log:
        log_path = Path(a.log)
    else:
        if a.inst_dir:
            base = Path(a.inst_dir)
        else:
            import instances  # noqa: E402
            base = instances.instance_dir(a.instance)
        log_path = base / "Data" / "NVSE" / "Plugins" / "nvcoop.log"
    from nvbridge import NvBridge  # noqa: E402

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    res = Result()
    if not log_path.exists():
        print(f"proof_actor_port_save_check: NOT READY: the instance log {log_path} does not exist (pass --inst-dir or --log)")
        return 3
    try:
        b = NvBridge.connect(instance=a.instance, wait_s=a.wait)
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach the game instance: {e}", file=sys.stderr)
        return 2
    try:
        Proof(b, res, a.ref, LogTail(log_path), a.save_name).run()
    except NotReady as e:
        print(f"proof_actor_port_save_check: NOT READY: {e}")
        return 3
    finally:
        b.close()
    (out / "report.json").write_text(json.dumps({"checks": res.checks, "info": res.info}, indent=2, default=str), encoding="utf-8")
    if res.failures:
        print(f"proof_actor_port_save_check: {len(res.failures)} check(s) FAILED:")
        for f in res.failures:
            print("  -", f)
        return 1
    print(f"proof_actor_port_save_check: all {len(res.checks)} checks passed (report in {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
