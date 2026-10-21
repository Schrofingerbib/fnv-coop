"""Game-free tests for the split-screen Python tools (layout, monitors, window placement, plan, launcher), with mutation twins.

    python -I tools/splitscreen/tests/test_tools.py            # real run: every test must pass
    python -I tools/splitscreen/tests/test_tools.py --twins    # one run per twin (split_mut switch): every twin must make at least one test fail

Prints "ALL OK" on success. Scratch files go under out/splitscreen/tmp.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parents[1]
for sub in (TOOLS, REPO / "tools" / "runner", REPO / "tools" / "pool", REPO / "tools"):
    sys.path.insert(0, str(sub))

import check_layout  # noqa: E402
import launch  # noqa: E402
import monitors  # noqa: E402
import plan as planmod  # noqa: E402
import smoke_playbook  # noqa: E402
import split_mut  # noqa: E402
import winplace  # noqa: E402
from layout import Rect, cells, fit, mode_for, too_small  # noqa: E402

SCRATCH = REPO / "out" / "splitscreen" / "tmp"
TESTS = []

# split_mut switches, each must make a test fail
TWINS = ["layout_ignore_gap", "layout_swap_rows", "fit_no_center", "keep_caption", "no_restore", "activate_on_place", "trust_first_place",
         "allow_shared_pad", "no_ghost", "clients_first", "kill_any_pid", "no_size_check", "no_aspect_check", "no_bounds_check",
         "no_overlap_check", "accept_any_physical"]


def test(fn):
    TESTS.append(fn)
    return fn


def ok(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "check failed")


def tmpdir() -> Path:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=SCRATCH))


# ------------------------------------------------------------------------------------------------------ layout
@test
def layout_tiles_exactly():
    area = Rect(0, 0, 1921, 1081)
    for mode, n in (("2lr", 2), ("2tb", 2), ("3", 3), ("4", 4)):
        cs = cells(mode, area)
        ok(len(cs) == n, mode)
        for i in range(n):
            ok(cs[i].inside(area), f"{mode} cell {i} leaves the area")
            for j in range(i + 1, n):
                ok(not cs[i].overlaps(cs[j]), f"{mode} cells {i},{j} overlap")
    a, b = cells("2lr", area)
    ok((a.w, b.w, b.x) == (960, 961, 960), "odd pixel goes to the second cell, no pixel lost")
    ok(a.w + b.w == 1921)
    q = cells("4", area)
    ok((q[0].h, q[2].h) == (540, 541), "top row 540, bottom row 541")
    ok(sum(c.w * c.h for c in q) == 1921 * 1081, "the quad covers every pixel")
    ok(cells("3", area) == q[:3], "3 players use the first three quarters")


@test
def layout_offset_and_gap():
    area = Rect(1920, 100, 1920, 1080)       # a second monitor
    a, b = cells("2lr", area, gap=10)
    ok(a == Rect(1920, 100, 955, 1080) and b == Rect(2885, 100, 955, 1080), f"{a} {b}")
    ok(b.x - a.right == 10, "gap")
    q = cells("4", area, gap=4)
    ok(q[1].x - q[0].right == 4 and q[2].y - q[0].bottom == 4, "quad gap")
    t, u = cells("2tb", area)
    ok(t == Rect(1920, 100, 1920, 540) and u == Rect(1920, 640, 1920, 540))


@test
def layout_modes_and_errors():
    ok(mode_for(2, "lr") == "2lr" and mode_for(2, "tb") == "2tb" and mode_for(3) == "3" and mode_for(4) == "4")
    for bad in ((1, "lr"), (5, "lr"), (2, "x")):
        try:
            mode_for(*bad)
            ok(False, f"{bad} accepted")
        except ValueError:
            pass
    try:
        cells("9", Rect(0, 0, 10, 10))
        ok(False)
    except ValueError:
        pass
    ok(too_small(Rect(0, 0, 639, 360)) and too_small(Rect(0, 0, 640, 359)) and not too_small(Rect(0, 0, 640, 360)))


@test
def layout_fit():
    ok(fit(Rect(0, 0, 960, 1080), "16:9") == Rect(0, 270, 960, 540), "centred vertically")
    ok(fit(Rect(0, 0, 1920, 540), "16:9") == Rect(480, 0, 960, 540), "centred horizontally")
    ok(fit(Rect(100, 50, 1000, 1000), "fill") == Rect(100, 50, 1000, 1000))
    r = fit(Rect(0, 0, 1001, 1001), "4:3")
    ok(r.w % 2 == 0 and r.h % 2 == 0 and r.w * 3 == r.h * 4 or abs(r.w * 3 - r.h * 4) <= 8, str(r))
    try:
        fit(Rect(0, 0, 10, 10), "wide")
        ok(False)
    except ValueError:
        pass


# ------------------------------------------------------------------------------------------------------ monitors
@test
def monitors_choose():
    ms = [monitors.Monitor(1, Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1040), True), monitors.Monitor(2, Rect(1920, 0, 2560, 1440), Rect(1920, 0, 2560, 1440), False)]
    ok(monitors.choose(ms, None).index == 1 and monitors.choose(ms, "primary").index == 1)
    ok(monitors.choose(ms, "2").rect.w == 2560)
    for bad in ("3", "x"):
        try:
            monitors.choose(ms, bad)
            ok(False)
        except ValueError:
            pass
    try:
        monitors.choose([], None)
        ok(False)
    except ValueError:
        pass


# ------------------------------------------------------------------------------------------------------ window placement
class FakeApi(winplace.WindowApi):
    def __init__(self):
        self.win = {}      # hwnd -> dict(pid, rect, style, ex, iconic, visible)
        self.calls = []
        self.fg = 0

    def add(self, hwnd, pid, rect, style=0x00CF0000 | 0x10000000, ex=0x100, iconic=False):
        self.win[hwnd] = dict(pid=pid, rect=rect, style=style, ex=ex, iconic=iconic)

    def windows_of_pid(self, pid):
        return [h for h, w in self.win.items() if w["pid"] == pid]

    def style(self, h): return self.win[h]["style"]
    def exstyle(self, h): return self.win[h]["ex"]

    def set_style(self, h, style, ex):
        self.win[h]["style"], self.win[h]["ex"] = style, ex
        self.calls.append(("style", h))

    def rect(self, h): return self.win[h]["rect"]

    def client(self, h):
        r = self.win[h]["rect"]
        if winplace.is_borderless(self.win[h]["style"]):
            return r.w, r.h
        return r.w - 16, r.h - 39

    def iconic(self, h): return self.win[h]["iconic"]

    def show_noactivate(self, h):
        self.win[h]["iconic"] = False
        self.calls.append(("show", h))

    def set_pos(self, h, r, flags):
        self.win[h]["rect"] = r
        self.calls.append(("pos", h, flags))
        return True

    def foreground(self): return self.fg


@test
def winplace_places_borderless_without_activating():
    api = FakeApi()
    api.add(7, 100, Rect(50, 50, 800, 600), iconic=True)
    cell = Rect(960, 0, 960, 540)
    r = winplace.place(api, 7, cell)
    ok(r == cell and winplace.placed_ok(api, 7, cell), "on the cell")
    ok(winplace.is_borderless(api.style(7)) and api.exstyle(7) & winplace.WS_EX_CLIENTEDGE == 0)
    ok(not api.iconic(7), "restored")
    flags = [c[2] for c in api.calls if c[0] == "pos"][0]
    ok(flags & winplace.SWP_NOACTIVATE, "SetWindowPos must not activate")
    ok(flags & winplace.SWP_FRAMECHANGED and flags & winplace.SWP_NOZORDER, "frame change applied, z-order untouched")


@test
def winplace_picks_the_main_window_of_the_right_process():
    api = FakeApi()
    api.add(1, 100, Rect(0, 0, 300, 200))
    api.add(2, 100, Rect(0, 0, 1280, 720))        # the game window: the largest
    api.add(3, 200, Rect(0, 0, 4000, 4000))       # somebody else's
    ok(winplace.game_window(api, 100) == 2)
    ok(winplace.game_window(api, 300) is None)


@test
def keeper_places_and_corrects_drift():
    api = FakeApi()
    api.add(1, 100, Rect(10, 10, 800, 600))
    api.add(2, 200, Rect(30, 30, 800, 600))
    c1, c2 = Rect(0, 0, 960, 540), Rect(960, 0, 960, 540)
    logs = []
    k = winplace.Keeper(api, {100: c1, 200: c2}, logs.append)
    ok(k.step() == {100: True, 200: True})
    ok(api.rect(1) == c1 and api.rect(2) == c2 and k.moves == 2)
    ok(k.step() == {100: True, 200: True} and k.moves == 2, "nothing to do when in place: no extra moves")
    api.win[2]["rect"] = Rect(500, 500, 960, 540)            # the engine moved it
    ok(k.step()[200] and api.rect(2) == c2 and k.moves == 3, "drift corrected")
    api.win[1]["iconic"] = True                               # minimized by hand
    ok(k.step()[100] and not api.iconic(1))
    del api.win[1]                                            # the game recreated its window later
    ok(k.step() == {100: False, 200: True})
    api.add(5, 100, Rect(0, 0, 640, 480))
    ok(k.step()[100] and api.rect(5) == c1)


# ------------------------------------------------------------------------------------------------------ plan
def mkplan(players=2, orient="lr", pads=None, **kw):
    pads = pads if pads is not None else [None] + list(range(players - 1))
    return planmod.build_plan(players, orient, Rect(0, 0, 1920, 1080), 1, pads, **kw)


@test
def plan_pad_assignment():
    ok(planmod.assign_pads(2, [0, 1]) == [None, 0], "player one keeps the keyboard")
    ok(planmod.assign_pads(4, [0, 1, 2]) == [None, 0, 1, 2])
    ok(planmod.assign_pads(3, [1, 3]) == [None, 1, 3], "pads are taken in order, whichever indexes are connected")
    ok(planmod.assign_pads(2, [2, 3], p1_pad=True) == [2, 3])
    ok(planmod.assign_pads(3, [0], allow_missing=True) == [None, 0, None])
    try:
        planmod.assign_pads(3, [0])
        ok(False, "a shortage must be an error")
    except planmod.PlanError as e:
        ok("controller" in str(e))
    ok(planmod.parse_pad_spec("none,0,1", 3) == [None, 0, 1])
    ok(planmod.parse_pad_spec("1, none", 2) == [1, None])
    for bad in ("0,0", "none", "none,5", "none,x,1"):
        try:
            planmod.parse_pad_spec(bad, 2 if bad.count(",") == 1 else 3)
            ok(False, bad)
        except planmod.PlanError:
            pass


@test
def plan_seats_and_flags():
    p = mkplan(3)
    s0, s1, s2 = p.seats
    ok((s0.role, s1.role, s2.role) == ("host", "client", "client"))
    ok(s0.kbm and not s0.ghost and s0.pad is None and s0.audio == "play", "player one: keyboard, real XInput state, sound")
    ok(not s1.kbm and s1.ghost and s1.pad == 0 and s1.audio == "mute", "other seats: pad, ghost, muted")
    ok(s2.pad == 1 and [s.instance for s in p.seats] == [40, 41, 42])
    p = mkplan(2, pads=[0, None])
    ok(not p.seats[0].kbm and p.seats[0].ghost, "player one on a pad: ghost too")
    ok(not p.seats[1].kbm and p.seats[1].ghost and p.seats[1].pad is None, "an idle seat listens to nothing")
    p = mkplan(2, audio="off")
    ok(p.seats[0].audio == "off" and p.seats[1].audio == "off")


@test
def plan_rejects_small_cells():
    try:
        planmod.build_plan(4, "lr", Rect(0, 0, 1024, 576), 1, [None, 0, 1, 2])
        ok(False, "512x288 cells must be refused")
    except planmod.PlanError as e:
        ok("too small" in str(e))
    p = planmod.build_plan(4, "lr", Rect(0, 0, 1024, 576), 1, [None, 0, 1, 2], force_small=True)
    ok(p.warnings)
    ok(planmod.ram_verdict(20.0, 4) is None and planmod.ram_verdict(6.0, 4) and planmod.ram_verdict(6.5, 2) is None)


@test
def plan_nvcoop_ini_and_json():
    p = mkplan(2, port=28000, dev_inject=True)
    h, c = (planmod.nvcoop_ini_extra(p, s) for s in p.seats)
    ok(h["bridge"]["minimized"] == "0" and h["bridge"]["focusGuard"] == "1", "visible windows keep the focus guard")
    ok(h["coop"]["listen"] == "127.0.0.1" and "listen" not in c["coop"], "only the host listens")
    ok(h["coop"]["port"] == c["coop"]["port"] == "28000" and c["coop"]["host"] == "127.0.0.1")
    ok(h["coop"]["token"] != c["coop"]["token"])
    ok(h["splitscreen"] == {"enable": "1", "player": "0", "players": "2", "pad": "none", "kbm": "1", "ghostPad": "0", "audio": "play",
                            "focusFake": "1", "devInject": "1"}, str(h["splitscreen"]))
    ok(c["splitscreen"]["pad"] == "0" and c["splitscreen"]["kbm"] == "0" and c["splitscreen"]["ghostPad"] == "1" and c["splitscreen"]["audio"] == "mute")
    ok("devInject" not in planmod.nvcoop_ini_extra(mkplan(2), mkplan(2).seats[0])["splitscreen"], "devInject only when asked")
    d = p.as_dict()
    ok(d["seats"][1]["cell"] == {"x": 960, "y": 0, "w": 960, "h": 1080})
    # the ini text the plugin reads is accepted by instances.merge_ini (ANSI checks) and parses to what the C++ expects
    import instances
    cfg = instances.merge_ini({}, c)
    ok(cfg["splitscreen"]["pad"] == "0")


@test
def plan_ini_edits_apply_safely():
    import instances
    import lowcost
    p = mkplan(2, fps_cap=45, audio="off")
    s = p.seats[1]
    e = planmod.ini_edits(p, s)
    pr = dict(((a, b), c) for a, b, c in e["FalloutPrefs.ini"])
    ok(pr[("Display", "iSize W")] == "960" and pr[("Display", "iSize H")] == "1080" and pr[("Display", "iLocation X")] == "960")
    ok(pr[("Display", "bFull Screen")] == "0" and pr[("Display", "iPresentInterval")] == "0")
    fo = dict(((a, b), c) for a, b, c in e["Fallout.ini"])
    ok(fo[("General", "bAlwaysActive")] == "1" and fo[("General", "iFPSClamp")] == "45" and fo[("Audio", "bEnableAudio")] == "0")
    ok(("Audio", "bEnableAudio") not in dict(((a, b), c) for a, b, c in planmod.ini_edits(mkplan(2), mkplan(2).seats[1])["Fallout.ini"]), "audio on: no change")
    d = tmpdir()
    try:
        (d / "FalloutPrefs.ini").write_text("[Display]\r\nbFull Screen=1\r\niSize W=640\r\n[Other]\r\nkeep=1\r\n", encoding="latin-1", newline="")
        (d / "Fallout.ini").write_text("[General]\r\nbAlwaysActive=0\r\n", encoding="latin-1", newline="")
        planmod.apply_ini_edits(d, e)
        t = (d / "FalloutPrefs.ini").read_bytes().decode("latin-1")
        ok(instances.ini_get(t, "Display", "iSize W") == "960" and instances.ini_get(t, "Display", "bFull Screen") == "0")
        ok(instances.ini_get(t, "Other", "keep") == "1", "unrelated keys survive")
        ok("\r\n" in t and "\n" not in t.replace("\r\n", ""), "CRLF kept")
        ok(instances.ini_get((d / "Fallout.ini").read_text(encoding="latin-1"), "General", "bAlwaysActive") == "1")
        # a hardlinked INI (a link into the game dir) is refused, and the original is not written
        orig = d / "orig.ini"
        orig.write_text("[Display]\r\niSize W=1\r\n", encoding="latin-1")
        os.remove(d / "FalloutPrefs.ini")
        os.link(orig, d / "FalloutPrefs.ini")
        try:
            planmod.apply_ini_edits(d, e)
            ok(False, "hardlinked INI must be refused")
        except lowcost.UnsafeTarget:
            pass
        ok("iSize W=1" in orig.read_text(encoding="latin-1"), "the linked original was not touched")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------ launcher
class FakeBackend(launch.Backend):
    def __init__(self, root, ram=32.0, foreign=None, fail_seat=None, nongame=()):
        self.root_, self.ram, self.foreign, self.fail_seat, self.nongame = root, ram, foreign or [], fail_seat, set(nongame)
        self.events, self.provisioned, self.killed, self.alive = [], [], [], set()
        self.a = SimpleNamespace()
        self.next_pid = 5000

    def root(self): return self.root_
    def free_ram_gb(self): return self.ram
    def foreign_game_pids(self, ours): return list(self.foreign)

    def provision(self, plan, seat):
        self.events.append(("provision", seat.index))
        self.provisioned.append((seat.index, planmod.nvcoop_ini_extra(plan, seat), planmod.ini_edits(plan, seat)))
        return {}

    def spawn(self, seat):
        self.events.append(("spawn", seat.index))
        self.current = seat.index

    def bridge_pid(self, seat, wait_s):
        if seat.index == self.fail_seat:
            return None
        self.next_pid += 1
        self.alive.add(self.next_pid)
        self.events.append(("up", seat.index, self.next_pid))
        return {"pid": self.next_pid}

    def pid_alive(self, pid): return pid in self.alive
    def is_game(self, pid): return pid not in self.nongame
    def kill(self, pid):
        self.killed.append(pid)
        self.alive.discard(pid)


def args(**kw):
    base = dict(no_launch=False, no_keep=True, boot_timeout=1.0, stagger=0.0, place_timeout=5.0, force=False, allow_foreign=False,
                keep_interval=0.0, settle_s=0.0)
    base.update(kw)
    return SimpleNamespace(**base)


def placed_api(be, plan):
    api = FakeApi()
    for e in be.events:
        if e[0] == "up":
            api.add(e[1] + 1, e[2], Rect(5, 5, 800, 600))
    return api


class LazyApi(FakeApi):
    """Creates each fake window only when the process is asked about, after the spawn (the real game makes its window late)."""
    def __init__(self, be):
        super().__init__()
        self.be = be

    def windows_of_pid(self, pid):
        if pid in self.be.alive and not any(w["pid"] == pid for w in self.win.values()):
            self.add(pid, pid, Rect(7, 7, 800, 600))
        return super().windows_of_pid(pid)


@test
def launcher_up_orders_and_places():
    d = tmpdir()
    try:
        be = FakeBackend(d)
        plan = mkplan(3)
        api = LazyApi(be)
        logs = []
        rc = launch.up(args(), plan, be, api, logs.append, sleep=lambda s: None)
        ok(rc == 0, f"rc {rc}: {logs[-3:]}")
        ups = [e for e in be.events if e[0] == "up"]
        ok([e[1] for e in ups] == [0, 1, 2], "host first, then the clients in order")
        spawn_i = [i for i, e in enumerate(be.events) if e[0] == "spawn"]
        up_i = [i for i, e in enumerate(be.events) if e[0] == "up"]
        ok(spawn_i[1] > up_i[0] and spawn_i[2] > up_i[1], "a client starts only after the previous copy is up")
        ok([p[0] for p in be.provisioned] == [0, 1, 2])
        ok(be.provisioned[1][1]["splitscreen"]["pad"] == "0" and be.provisioned[1][2]["FalloutPrefs.ini"][1][2] == "960")
        # every window ended up on its own cell
        for s, e in zip(plan.seats, ups):
            ok(api.rect(e[2]) == s.cell, f"seat {s.index} window {api.rect(e[2])} != {s.cell}")
        st = launch.load_state(d)
        ok(sorted(st["pids"].values()) == sorted(e[2] for e in ups), "pids saved for down")
        ok(any("READY" in l for l in logs) and any("controller 1" in l for l in logs), "tester steps printed")
        # down closes exactly those, only if they are still game processes
        be.nongame = {ups[1][2]}
        launch.down(be, logs.append)
        ok(sorted(be.killed) == sorted([ups[0][2], ups[2][2]]), f"killed {be.killed}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@test
def launcher_refuses_and_cleans_up():
    d = tmpdir()
    try:
        plan = mkplan(2)
        logs = []
        be = FakeBackend(d, foreign=[999])
        ok(launch.up(args(), plan, be, LazyApi(be), logs.append, sleep=lambda s: None) == 2 and not be.events, "the owner's own game is left alone")
        ok(launch.up(args(allow_foreign=True), plan, be, LazyApi(be), logs.append, sleep=lambda s: None) == 0)
        be = FakeBackend(d, ram=3.0)
        ok(launch.up(args(), plan, be, LazyApi(be), logs.append, sleep=lambda s: None) == 2 and not be.events, "not enough memory: nothing started")
        be = FakeBackend(d, fail_seat=1)
        rc = launch.up(args(), plan, be, LazyApi(be), logs.append, sleep=lambda s: None)
        ok(rc == 3 and len(be.killed) == 1, f"a copy that does not come up stops the rest: rc {rc} killed {be.killed}")
        be = FakeBackend(d, fail_seat=1, nongame=range(5001, 5100))
        launch.up(args(), plan, be, LazyApi(be), logs.append, sleep=lambda s: None)
        ok(not be.killed, "a pid that is not a game process is never killed")
        be = FakeBackend(d)
        ok(launch.up(args(no_launch=True), plan, be, LazyApi(be), logs.append, sleep=lambda s: None) == 0 and not any(e[0] == "spawn" for e in be.events), "--no-launch only provisions")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@test
def launcher_cli_plan_matches():
    ap = launch.build_parser()
    a = ap.parse_args(["plan", "--players", "4", "--fit", "16:9"])
    ms = [monitors.Monitor(1, Rect(0, 0, 2560, 1440), Rect(0, 0, 2560, 1400), True)]
    p = launch.make_plan(a, ms, [0, 1, 2, 3])
    ok(p.seats[3].cell == Rect(1280, 720, 1280, 720) and [s.pad for s in p.seats] == [None, 0, 1, 2])
    a = ap.parse_args(["plan", "--players", "2", "--split", "tb", "--workarea", "--pads", "none,2"])
    p = launch.make_plan(a, ms, [])
    ok(p.seats[1].pad == 2 and p.seats[1].cell == Rect(0, 700, 2560, 700) and p.mode == "2tb")
    # --dev-inject: synthetic pads 0..3 exist on every copy, so no seat is idle without real controllers (in game 2026-10-21 the
    # seats stayed idle and the smoke playbook's pad step failed)
    a = ap.parse_args(["plan", "--players", "2", "--dev-inject"])
    ok([s.pad for s in launch.make_plan(a, ms, []).seats] == [None, 0], "--dev-inject seats take synthetic pads")
    ok([s.pad for s in launch.make_plan(a, ms, [2]).seats] == [None, 2], "--dev-inject keeps a detected controller (PR #24 review)")
    a = ap.parse_args(["plan", "--players", "2", "--allow-missing-pads"])
    ok([s.pad for s in launch.make_plan(a, ms, []).seats] == [None, None], "without --dev-inject a seat with no controller stays idle")
    a = ap.parse_args(["plan", "--players", "3"])
    try:
        launch.make_plan(a, ms, [0])
        ok(False, "too few pads")
    except planmod.PlanError:
        pass


@test
def check_layout_selftest_passes():
    ok(check_layout.selftest() == 0)


@test
def smoke_playbook_selftest_passes():
    ok(smoke_playbook.selftest() == 0)


# ------------------------------------------------------------------------------------------------------ runner
def run_all(quiet: bool = False) -> int:
    failed = 0
    for fn in TESTS:
        try:
            if quiet:
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    fn()
            else:
                fn()
        except Exception as e:                   # noqa: BLE001
            failed += 1
            if not quiet:
                print(f"FAIL {fn.__name__}: {e}")
                if not isinstance(e, AssertionError):
                    traceback.print_exc()
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--twins", action="store_true")
    a = ap.parse_args()
    if not a.twins:
        failed = run_all()
        print(f"split-screen tools: {len(TESTS)} tests, {failed} failed" + (" - ALL OK" if not failed else ""))
        return 1 if failed else 0
    bad = []
    for t in TWINS:
        with split_mut.use(t):
            if run_all(quiet=True) == 0:
                bad.append(t)
    if bad:
        print("SURVIVED twins: " + ", ".join(bad))
        return 1
    print(f"split-screen tools: all {len(TWINS)} twins caught - ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
