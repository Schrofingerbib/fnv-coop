"""Multi-bot co-op playbook engine.

A playbook drives 2 to 4 bots at once (one per game instance, each on its own nvbridge pipe).
After every step the engine:
  1. settles (waits until every machine has applied the host's last sequence number),
  2. snapshots every machine and runs snapdiff (host vs each client, "shared" section),
  3. compares the desync counters with their value before the step (they must not rise),
and on any failure it saves everything T1b's analyzer needs to <outdir>/FAIL-<n>-<step>/.

The engine only needs this from a bot (tools/bots/bot.py, T6a):
    bot.nv                     raw NvBridge (or a fake with the same call())
    bot.coop_status() -> dict  the coop.status result
    bot.snapshot(path)         dump the L7 snapshot to a file (fake bots: write json)
plus whatever action methods the playbooks call. Actions return an object with .ok and .reason.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


class StepFailure(Exception):
    """A step did not do what the playbook expected. `reason` is shown to the human."""

    def __init__(self, reason: str, kind: str = "check"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind          # check | action | settle | snapdiff | counters | bridge | exception


class PlaybookAbort(Exception):
    """Raised after a failed step has been recorded; unwinds the playbook."""


@dataclass
class Machine:
    idx: int                      # 0 = host
    name: str                     # "host", "client1", ...
    bot: Any
    instance: int = 0

    @property
    def role(self) -> str:
        return "host" if self.idx == 0 else "client"


@dataclass
class StepRecord:
    name: str
    ok: bool
    reason: str = ""
    kind: str = ""
    seconds: float = 0.0
    snapdiff: str = "skipped"         # equal | different | invalid | skipped
    counters_delta: Dict[str, Dict[str, int]] = field(default_factory=dict)
    fail_dir: str = ""


# ---------------------------------------------------------------------------------------------
# counters: what "desync counters" means. All come from coop.status.
COUNTER_PATHS = {
    # host side
    "digestMismatch": ("desyncs", "digests", "mismatch"),
    "repairs": ("desyncs", "digests", "repairs"),
    "effectsRepaired": ("desyncs", "digests", "effectsRepaired"),
    "badMessages": ("host", "badMessages"),
    # any side
    "applyFailed": ("counters", "applyFailed"),
    # client side
    "staleEpochDropped": ("client", "staleEpochDropped"),
    "rebases": ("client", "rebases"),
    "duplicatesDropped": ("client", "duplicatesDropped"),
}
# These must not rise during a step. Others are informational (duplicates are normal on a lossy link).
REPAIR_COUNTERS = ("digestMismatch", "repairs", "effectsRepaired")
MUST_NOT_RISE = ("digestMismatch", "repairs", "effectsRepaired", "badMessages", "applyFailed", "rebases")


def read_counters(status: dict) -> Dict[str, int]:
    out = {}
    for k, path in COUNTER_PATHS.items():
        v: Any = status
        for p in path:
            v = v.get(p) if isinstance(v, dict) else None
            if v is None:
                break
        out[k] = int(v) if isinstance(v, (int, float)) else 0
    return out


def last_seq(status: dict) -> int:
    return int(status.get("lastSeq", 0) or 0)


# ---------------------------------------------------------------------------------------------
class Comparer:
    """Takes a snapshot of each machine and diffs two of them. Subclasses: LiveComparer, FakeComparer."""

    def snapshot(self, m: Machine, path: str) -> Any:
        raise NotImplementedError

    def diff(self, a: Any, b: Any, ignore: Sequence[str]) -> Tuple[int, List[str]]:
        """Returns (code, lines): 0 equal, 1 different, 2 invalid input."""
        raise NotImplementedError


class LiveComparer(Comparer):
    """Real snapshots (bridge `state`) compared with plugin/snapshot/tools/snapdiff.py."""

    def __init__(self, repo_root: str, sections=("shared",), min_quests=None, min_globals=None):
        import sys
        tdir = os.path.join(repo_root, "plugin", "snapshot", "tools")
        if tdir not in sys.path:
            sys.path.insert(0, tdir)
        import snaplib
        import snapdiff
        self.snaplib, self.snapdiff = snaplib, snapdiff
        self.sections = tuple(sections)
        self.min_quests = snaplib.DEFAULT_MIN_QUESTS if min_quests is None else min_quests
        self.min_globals = snaplib.DEFAULT_MIN_GLOBALS if min_globals is None else min_globals

    def snapshot(self, m: Machine, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        m.bot.snapshot(path)
        return self.snaplib.load(path)

    def diff(self, a, b, ignore):
        code, problems, diffs = self.snapdiff.compare(a, b, self.sections, False, None, tuple(ignore),
                                                      self.min_quests, self.min_globals)
        lines = list(problems) + [str(d) for d in diffs]
        return code, lines


# ---------------------------------------------------------------------------------------------
class Ctx:
    """What a playbook sees. Create with Ctx(machines, comparer, outdir, opts)."""

    def __init__(self, machines: List[Machine], comparer: Optional[Comparer], outdir: str,
                 refs: Optional[dict] = None, opts: Optional[dict] = None, playbook: str = "",
                 log: Optional[Callable[[str], None]] = None):
        self.machines = machines
        self.comparer = comparer
        self.outdir = outdir
        self.refs = dict(refs or {})
        self.opts = {"settle_s": 20.0, "settle_poll_s": 0.1, "compare": True, "strict_counters": True,
                     "snapdiff_ignore": [], "save_snapshots": True, "chaos": None}
        self.opts.update(opts or {})
        self.playbook = playbook
        self.records: List[StepRecord] = []
        self._log = log or (lambda s: None)
        self._fail_n = 0
        self._counter_base: Dict[str, Dict[str, int]] = {}
        self._step_n = 0
        self.notes: List[str] = []
        self.anim = None                # AnimWatch when the playbook has T7 animation checks switched on
        self.absent: set = set()        # names of machines that left the session (excluded from settle/compare/queries)
        self._t_start = time.monotonic()
        self._repairs_at_start = 0

    # ---- who ----
    @property
    def host(self) -> Machine:
        return self.machines[0]

    @property
    def clients(self) -> List[Machine]:
        return self.machines[1:]

    @property
    def present(self) -> List[Machine]:
        return [m for m in self.machines if m.name not in self.absent]

    def log(self, s: str) -> None:
        self._log(f"[{self.playbook}] {s}")

    # ---- helpers for playbook authors ----
    def ref(self, key: str) -> str:
        if key not in self.refs:
            raise StepFailure(f"scenario has no ref named {key!r} (have: {sorted(self.refs)})", "check")
        return self.refs[key]

    def must(self, result: Any, what: str) -> Any:
        """Action results never raise; turn a failed one into a step failure with its reason."""
        ok = getattr(result, "ok", result is True)
        if not ok:
            reason = getattr(result, "reason", "") or "no reason given"
            raise StepFailure(f"{what}: {reason}", "action")
        return result

    def expect(self, cond: Any, msg: str) -> None:
        if not cond:
            raise StepFailure(msg, "check")

    @contextlib.contextmanager
    def walking(self, machine: "Machine"):
        """Tell the animation checker that this machine's player walks during the block (its puppets must move: 'stuck' detector)."""
        if self.anim is None:
            yield
        else:
            with self.anim.walking(machine.name):
                yield

    @contextlib.contextmanager
    def teleporting(self, grace_ms: float = 700.0):
        """A deliberate teleport / fast travel happens in the block. Position jumps (rubberband) and landing artefacts of every
        player's character and puppet are ignored for the block plus `grace_ms`; nothing else is: a puppet that slides, holds
        the wrong weapon or loses its animation after the jump still fails."""
        if self.anim is None:
            yield
        else:
            with self.anim.teleporting(grace_ms):
                yield

    def equip_all(self) -> None:
        """Everyone equips refs['weapon']. Without that ref the scenario is trusted to have armed the players."""
        w = self.refs.get("weapon")
        if not w:
            return
        for m in self.machines:
            self.must(m.bot.equip(w), f"{m.name} equips {w}")

    def on_all(self, fn: Callable[[Machine], Any], machines: Optional[Sequence[Machine]] = None) -> Dict[str, Any]:
        """Run fn(machine) on every machine at the same time (threads; each bot has its own pipe)."""
        ms = list(machines if machines is not None else self.present)
        out: Dict[str, Any] = {}
        err: Dict[str, BaseException] = {}

        def run(m: Machine):
            try:
                out[m.name] = fn(m)
            except BaseException as e:  # noqa: BLE001  re-raised below on the calling thread
                err[m.name] = e

        ts = [threading.Thread(target=run, args=(m,), daemon=True) for m in ms]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        if err:
            name, e = next(iter(err.items()))
            if isinstance(e, StepFailure):
                raise e
            raise e
        return out

    def same_everywhere(self, label: str, query: Callable[[Machine], Any],
                        machines: Optional[Sequence[Machine]] = None) -> Any:
        """Ask every machine the same question and require identical answers."""
        ms = list(machines if machines is not None else self.present)
        vals = {m.name: query(m) for m in ms}
        distinct = {json.dumps(v, sort_keys=True, default=str) for v in vals.values()}
        if len(distinct) != 1:
            raise StepFailure(f"{label} differs between machines: {vals}", "check")
        return vals[ms[0].name]

    def optional(self, label: str, fn: Callable[[], Any], default: Any = None) -> Any:
        """Run a check that needs a bridge method that may not exist yet. If it is missing, note it and return `default`."""
        from .probe import MissingBridge
        try:
            return fn()
        except MissingBridge as e:
            self.notes.append(f"{label} skipped: {e}")
            return default

    def wait_until(self, label: str, pred: Callable[[], bool], timeout_s: float = 10.0, poll_s: float = 0.1) -> None:
        end = time.monotonic() + timeout_s
        while True:
            self._tick()
            if pred():
                return
            if time.monotonic() >= end:
                raise StepFailure(f"timed out after {timeout_s:g}s waiting for: {label}", "check")
            time.sleep(0 if self.opts.get("fast") else poll_s)

    def pause(self, seconds: float) -> None:
        """Let the game run for a while (fake worlds: a few ticks instead of real time)."""
        if self.opts.get("fast"):
            n = max(3, int(seconds / 0.05)) if self.anim is not None else 3     # animation checks need virtual time to pass
            for _ in range(n):
                self._tick()
            return
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._tick()
            time.sleep(0.1)

    def _tick(self) -> None:
        """Poll statuses once (this also drives fake worlds forward)."""
        for m in self.machines:
            m.bot.coop_status()

    # ---- settle, compare, counters ----
    def statuses(self) -> Dict[str, dict]:
        return {m.name: m.bot.coop_status() for m in self.present}

    def settle(self, timeout_s: Optional[float] = None) -> None:
        """Wait until every client has applied the host's last sequence number and stays equal for 2 polls."""
        timeout_s = self.opts["settle_s"] if timeout_s is None else timeout_s
        end = time.monotonic() + timeout_s
        stable = 0
        last = {}
        while True:
            st = self.statuses()
            host_seq = last_seq(st[self.host.name])
            seqs = {m.name: last_seq(st[m.name]) for m in self.present}
            synced = all(seqs[m.name] == host_seq for m in self.present[1:])
            host_ok = st[self.host.name].get("host", {}).get("allSynced", True)
            if synced and host_ok and seqs == last:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
            last = seqs
            if time.monotonic() >= end:
                raise StepFailure(f"machines did not converge within {timeout_s:g}s: lastSeq per machine {seqs}, "
                                  f"host allSynced={host_ok}", "settle")
            time.sleep(self.opts["settle_poll_s"])

    def _read_all_counters(self) -> Dict[str, Dict[str, int]]:
        return {n: read_counters(s) for n, s in self.statuses().items()}

    def quiesce(self) -> None:
        """Impaired link rule: transient digest mismatches are fine while traffic is in flight, but once nothing new is sent
        for `quiesce_s` every machine must have converged and NO repair may happen during the quiet window."""
        q = float(self.opts["quiesce_s"])
        self.pause(q)
        self.settle()
        base = self._read_all_counters()
        self.pause(q)
        self.settle()
        now = self._read_all_counters()
        bad = []
        for n in now:
            for k in REPAIR_COUNTERS:
                if now[n][k] > base[n][k]:
                    bad.append(f"{n}.{k} +{now[n][k] - base[n][k]}")
        if bad:
            raise StepFailure(f"repairs during the {q:g}s quiescence window (must be 0): " + "; ".join(bad), "quiescence")

    def check_repair_budget(self) -> None:
        """Total repairs per minute over the run must stay within the profile's budget (default 0: start strict)."""
        budget = self.opts.get("repair_budget_per_min")
        if budget is None:
            return
        total = sum(c["repairs"] for c in self._read_all_counters().values()) - self._repairs_at_start
        minutes = max((time.monotonic() - self._t_start) / 60.0, 1.0)
        rate = total / minutes
        if rate > budget:
            raise StepFailure(f"repairs per minute {rate:.2f} exceed the {self.opts.get('chaos')} budget {budget} "
                              f"({total} repairs in {minutes:.1f} min)", "budget")

    def mark_counters(self) -> None:
        self._counter_base = self._read_all_counters()

    def check_counters(self, allow_repairs: bool = False, allow_rebase: bool = False) -> Dict[str, Dict[str, int]]:
        now = self._read_all_counters()
        delta = {n: {k: now[n][k] - self._counter_base.get(n, now[n])[k] for k in now[n]} for n in now}
        bad = []
        for n, d in delta.items():
            for k in MUST_NOT_RISE:
                if allow_rebase and k == "rebases":
                    continue
                if (allow_repairs or not self.opts["strict_counters"]) and k in REPAIR_COUNTERS:
                    continue            # a repaired desync on an impaired link is recorded, not failed
                if d[k] > 0:
                    bad.append(f"{n}.{k} rose by {d[k]}")
        self._counter_base = now
        if bad:
            raise StepFailure("desync counters rose: " + "; ".join(bad), "counters")
        return delta

    def compare_snapshots(self, tag: str) -> str:
        """Host vs each client. Returns 'equal' or raises StepFailure."""
        if not self.comparer or not self.opts["compare"]:
            return "skipped"
        snapdir = os.path.join(self.outdir, "snap", re.sub(r"[^A-Za-z0-9_.-]+", "_", tag))
        snaps = {}
        for m in self.present:
            snaps[m.name] = self.comparer.snapshot(m, os.path.join(snapdir, m.name + ".json"))
        problems = []
        for c in self.present[1:]:
            code, lines = self.comparer.diff(snaps[self.host.name], snaps[c.name], self.opts["snapdiff_ignore"])
            if code == 2:
                raise StepFailure(f"snapdiff refused the snapshots ({c.name}): " + "; ".join(lines[:3]), "snapdiff")
            if code == 1:
                problems.append(f"host vs {c.name}: {len(lines)} difference(s), first: " + "; ".join(lines[:3]))
        if problems:
            raise StepFailure("snapdiff found differences. " + " | ".join(problems), "snapdiff")
        return "equal"

    # ---- steps ----
    @contextlib.contextmanager
    def step(self, name: str, *, settle: bool = True, compare: bool = True, allow_repairs: bool = False,
             allow_rebase: bool = False):
        """with ctx.step("both shoot"): ...  On exit: settle, snapdiff, counter check. Failure aborts the playbook."""
        self._step_n += 1
        t0 = time.monotonic()
        rec = StepRecord(name=name, ok=False)
        label = f"{self._step_n:02d}-{name}"
        self.log(f"step {label}")
        try:
            if not self._counter_base:
                self.mark_counters()
                self._repairs_at_start = sum(c["repairs"] for c in self._counter_base.values())
            yield self
            if settle:
                self.settle()
                if self.opts.get("quiesce_s"):
                    self.quiesce()
            if compare:
                rec.snapdiff = {"equal": "equal", "skipped": "skipped"}[self.compare_snapshots(label)]
            rec.counters_delta = self.check_counters(allow_repairs, allow_rebase)
            self.check_repair_budget()
            rec.ok = True
        except StepFailure as e:
            rec.reason, rec.kind = e.reason, e.kind
            if e.kind == "snapdiff":
                rec.snapdiff = "different"
        except PlaybookAbort:
            raise
        except Exception as e:  # noqa: BLE001  a bridge/bot crash is also a failed step with a trace
            rec.reason, rec.kind = f"{type(e).__name__}: {e}", "exception" if not _is_bridge(e) else "bridge"
            self.notes.append(traceback.format_exc())
        finally:
            rec.seconds = round(time.monotonic() - t0, 3)
            if not rec.ok:
                rec.fail_dir = self.save_failure(label, rec)
            self.records.append(rec)
        if not rec.ok:
            self.log(f"FAIL {label}: {rec.reason}")
            raise PlaybookAbort(rec.reason)

    # ---- failure artifacts (inputs for T1b) ----
    def save_failure(self, label: str, rec: StepRecord) -> str:
        self._fail_n += 1
        d = os.path.join(self.outdir, f"FAIL-{self._fail_n}-{re.sub(r'[^A-Za-z0-9_.-]+', '_', label)}")
        os.makedirs(d, exist_ok=True)
        manifest: Dict[str, Any] = {"playbook": self.playbook, "step": label, "reason": rec.reason, "kind": rec.kind,
                                    "machines": [], "chaos": self.opts.get("chaos")}
        for m in self.machines:
            info: Dict[str, Any] = {"name": m.name, "role": m.role, "instance": m.instance}
            try:
                info["status"] = m.bot.coop_status()
            except Exception as e:  # noqa: BLE001
                info["status_error"] = str(e)
            try:
                info["events"] = m.bot.nv.call("events", {"since": 0}, timeout_s=5)
            except Exception as e:  # noqa: BLE001
                info["events_error"] = str(e)
            try:   # T1a's recorder; the method may not exist yet
                info["flight_dump"] = m.bot.nv.call("flight.dump", {"reason": f"{self.playbook}:{label}"}, timeout_s=15)
            except Exception as e:  # noqa: BLE001
                info["flight_dump_error"] = str(e)
            try:
                if self.comparer and self.opts["save_snapshots"]:
                    sp = os.path.join(d, f"{m.name}.snapshot.json")
                    m.bot.snapshot(sp)
                    info["snapshot"] = sp
            except Exception as e:  # noqa: BLE001
                info["snapshot_error"] = str(e)
            manifest["machines"].append(info)
        manifest["steps_so_far"] = [r.__dict__ for r in self.records] + [rec.__dict__]
        manifest["notes"] = self.notes[-3:]
        with open(os.path.join(d, "failure.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1, default=str)
        return d


def _is_bridge(e: BaseException) -> bool:
    return type(e).__name__ in ("ConnectionLost", "GameCrashed", "RpcError", "WaitTimeout", "ProtocolError")


# ---------------------------------------------------------------------------------------------
class Playbook:
    """Subclass, set the class attributes, implement run(ctx)."""

    name = ""
    summary = ""
    min_players = 2
    max_players = 4
    scenario = ""                   # T4b scenario name to start from
    needs_scenario_refs: Tuple[str, ...] = ()   # ref keys the scenario must provide
    snapdiff_ignore: Tuple[str, ...] = ()
    allow_repairs = False
    anim = False                    # True: T7 animation/pose assertions run at the end (needs [dev] animprobe=1)
    anim_npcs = True                # also assert the NPC counters (compared > 0 ...)

    def run(self, ctx: Ctx) -> None:
        raise NotImplementedError


@dataclass
class RunResult:
    playbook: str
    ok: bool
    steps: List[StepRecord]
    outdir: str
    reason: str = ""

    def to_json(self) -> dict:
        return {"playbook": self.playbook, "ok": self.ok, "reason": self.reason, "outdir": self.outdir,
                "steps": [s.__dict__ for s in self.steps]}


def run_playbook(pb: Playbook, machines: List[Machine], comparer: Optional[Comparer], outdir: str,
                 refs: Optional[dict] = None, opts: Optional[dict] = None,
                 log: Optional[Callable[[str], None]] = None) -> RunResult:
    n = len(machines)
    if not (pb.min_players <= n <= pb.max_players):
        return RunResult(pb.name, False, [], outdir, f"{pb.name} needs {pb.min_players} to {pb.max_players} players, got {n}")
    missing = [k for k in pb.needs_scenario_refs if k not in (refs or {})]
    if missing:
        return RunResult(pb.name, False, [], outdir, f"scenario is missing refs {missing}")
    os.makedirs(outdir, exist_ok=True)
    o = dict(opts or {})
    ign = list(o.get("snapdiff_ignore", [])) + list(pb.snapdiff_ignore)
    o["snapdiff_ignore"] = ign
    ctx = Ctx(machines, comparer, outdir, refs, o, pb.name, log)
    reason = ""
    watch = None
    if pb.anim and o.get("anim", True):
        from .animwatch import AnimWatch
        watch = ctx.anim = AnimWatch(ctx, hz=o.get("anim_hz", 20.0), outdir=os.path.join(outdir, "anim"), npcs=pb.anim_npcs)
        watch.start()
    try:
        pb.run(ctx)
        if watch is not None:
            with ctx.step("animation and pose check", settle=False, compare=False):
                fails = watch.failures()
                with open(os.path.join(outdir, "anim_report.json"), "w", encoding="utf-8") as f:
                    json.dump(getattr(watch, "last_report", {}), f, indent=1, default=str)
                if fails:
                    raise StepFailure("animation check: " + "; ".join(fails), "anim")
        ok = all(r.ok for r in ctx.records) and bool(ctx.records)
        if not ctx.records:
            reason = "playbook ran no steps"
    except PlaybookAbort as e:
        ok, reason = False, str(e)
    except StepFailure as e:   # raised outside a step block
        rec = StepRecord("(outside step)", False, e.reason, e.kind)
        rec.fail_dir = ctx.save_failure("outside-step", rec)
        ctx.records.append(rec)
        ok, reason = False, e.reason
    except Exception as e:  # noqa: BLE001
        rec = StepRecord("(outside step)", False, f"{type(e).__name__}: {e}", "exception")
        rec.fail_dir = ctx.save_failure("outside-step", rec)
        ctx.records.append(rec)
        ok, reason = False, rec.reason
    if watch is not None:
        watch.stop()
    res = RunResult(pb.name, ok, ctx.records, outdir, reason)
    with open(os.path.join(outdir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res.to_json(), f, indent=1, default=str)
    return res
