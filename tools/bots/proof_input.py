"""In-game proof for the bot input driver (plugin/bots). THE LEAD RUNS THIS, ONE GAME AT A TIME; it is never run by tests
against a real game. It proves, with numbers, that forced controls reach the engine and behave like real keys.

    python tools/bots/proof_input.py --instance 0 [--save <name>] [--target <Plugin.esm:HEX>] [--door <Plugin.esm:HEX>]
    python tools/bots/proof_input.py --fake            # same script over the fake bridge (game-free self-test)

Needs nvcoop.ini [dev] bots=1 on the instance. Writes out/T6a/proof_input.json. No argument is required. --target and
--door are Plugin.esm:HEX or a bare hex runtime id (an empty value is refused).
Exit codes: 0 every check passed, 1 a check failed, 2 the proof could not run: a bad command line (refused before the
bridge is opened), no game on the instance, or the game lost during the run (crash or pipe loss).
--dismiss-messages (opt-in, off by default): first clear stray message boxes with Bot.dismiss_messages (button 0 while bot.menu says
"message"), e.g. the DLC notices a fresh save shows after load. They come in waves (6, then 2, then 2 about 16 s later), so it presses
until no box has been shown for --dismiss-quiet-s seconds (default 20), at most --dismiss-max-s (default 120). It adds a
"dismiss_messages" row with the press count and the seconds it waited.
Checks (each prints measured values so a failure says what the game did):
  hook      bot.status reports hooked=true and the control-state query counter rises each frame
  forward   bot.move forward, run:true (run SPEED) for 2 s: the player moves 300..700 units along the facing direction. With the
            game's always-run on, the driver must NOT hold Run (Run held = walk, about 160 units in 2 s); the row shows the move reply
            (alwaysRun, runControl)
  back      Backward moves the opposite way; Left/Right strafe perpendicular (proves the yaw convention and control ids 0..3)
  turn      bot.look yaw/pitch read back through GetAngle
  stop      bot.stop: nothing held, the player stops within 0.5 s
  jump      Z rises > 5 units (control 12, an edge-only control: proves the edge is served exactly once)
  sneak     two taps toggle crouch on then off (edge fires exactly once per tap, or the state would not alternate), read with
            player.IsSneaking (the console has no GetSneaking); a failed read is a FAIL
  fire      with --target: pulses Attack at the target; its health drops (needs an equipped, loaded weapon). Bot.fire aims once, so
            the target must stay still and in line of sight: an NPC that walks away makes the shots miss (SetRestrained 1 holds it)
  activate  with --door: the movement checks leave the player anywhere, so the proof first teleports to the door, faces it and
            walks backward until --door-dist units away (default 100, inside the game's activation reach of about 150; 0 keeps
            the player where it is). Then it looks at the door and presses Activate. PASS when the door state changes, or when
            the player's cell changes: a load door (in game, Doc Mitchell's front door FalloutNV.esm:103E61) moves the player
            to the other cell and its open state stays "closed"
An RPC or bridge error in the fire or activate check gives a FAIL row with the error. It does not stop the proof.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "runner"))
from bots import Bot, FakeBridge, World, proof_args  # noqa: E402
from bots.bot import BridgeError, ConnectionLost, _Fail  # noqa: E402

# No argument is required (--target and --door only add checks). FORMS: every dest that holds a form id.
REQUIRED = {}
FORMS = ("target", "door")

# The sneak state as a console expression. The game's console has IsSneaking. "player.GetSneaking" gave "Unknown variable" (2026-10-21).
SNEAK_EXPR = "player.IsSneaking"
SETTLE_AFTER_JUMP_S = 2.5  # > the ~2 s after a jump in which only one crouch toggle takes effect
DOOR_DIST = 100.0          # units from the door for the activate check (the game's activation reach is about 150)
# Errors of the fire and activate checks that give a FAIL row. ConnectionLost (a BridgeError) is re-raised first: a lost game is exit 2.
# _Fail: a Bot query outside an action (b.pos(ref), b.cell()) raises it when ref.info fails (PR #13 review).
CHECK_ERRORS = (BridgeError, _Fail)


def door_activated(before: str, after: str, cell_before: int, cell_after: int) -> bool:
    """The door state changed, or the player went through a load door (cell change, the open state stays 'closed')."""
    return before != after or cell_before != cell_after


def unreadable_after_ok(cell_before: int, cell_after: int) -> bool:
    """A door state that cannot be read after Activate is 'unloaded' only after a cell change (the old cell may be gone).
    In the same cell a failed read is a FAIL row, not a state change (PR #13 review)."""
    return cell_before != cell_after


def error_text(e: Exception) -> str:
    data = getattr(e, "data", None)
    return f"{type(e).__name__}: {e}" + (f" {data}" if data else "")


def walk_back_from(b, nv, ref, dist: float, timeout_s: float = 4.0) -> float:
    """Teleport to `ref`, face it and walk backward until `dist` units away (the engine's collisions apply). -> the distance."""
    b.goto(ref, mode="teleport")
    b.look(ref=ref)
    nv.call("bot.move", {"forward": -1, "strafe": 0, "run": False})
    b.wait_until(lambda: math.dist(b.pos(), b.pos(ref)) >= dist, timeout_s, 0.05)
    nv.call("bot.stop")
    b.wait(0.3)
    return math.dist(b.pos(), b.pos(ref))


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, **measured):
        self.rows.append({"check": name, "result": "PASS" if ok else "FAIL", **measured})
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {measured}")
        return ok

    def skip(self, name, why):
        self.rows.append({"check": name, "result": "SKIP", "why": why})
        print(f"SKIP  {name}  {why}")


def rel_move(b, before, yaw_deg):
    """Displacement since `before`, split into (along facing, to the right of facing)."""
    now = b.pos()
    dx, dy = now.x - before.x, now.y - before.y
    yaw = math.radians(yaw_deg)
    fwd = dx * math.sin(yaw) + dy * math.cos(yaw)
    right = dx * math.cos(yaw) - dy * math.sin(yaw)
    return fwd, right


def run(nv, args) -> Report:
    r = Report()
    b = Bot(nv, "proof")
    if getattr(args, "dismiss_messages", False):
        d = b.dismiss_messages(quiet_s=getattr(args, "dismiss_quiet_s", 20.0), max_s=getattr(args, "dismiss_max_s", 120.0))
        r.check("dismiss_messages", d.ok, pressed=d.data.get("pressed"), waited_s=d.data.get("waited_s"), reply=str(d))
    st = nv.call("bot.status")
    q0 = st.get("frame", 0)
    b.wait(0.5)
    st2 = nv.call("bot.status")
    r.check("hook", bool(st.get("hooked")) and st2.get("frame", 0) > q0, status=st, frame_after=st2.get("frame"))

    yaw = 0.0
    b.look(yaw=yaw)
    b.wait(0.3)

    moves = []

    def burst(forward, strafe, secs=2.0):
        p0 = b.pos()
        moves.append(nv.call("bot.move", {"forward": forward, "strafe": strafe, "run": True}))
        b.wait(secs)
        nv.call("bot.stop")
        b.wait(0.5)
        return rel_move(b, p0, yaw)

    f, s = burst(1, 0)
    r.check("forward", 300 <= f <= 700 and abs(s) < 0.25 * abs(f), along=round(f), side=round(s), move=moves[-1])
    f, s = burst(-1, 0)
    r.check("back", f < -150 and abs(s) < 0.4 * abs(f), along=round(f), side=round(s), move=moves[-1])
    f, s = burst(0, 1)
    r.check("strafe_right", s > 150 and abs(f) < 0.4 * abs(s), along=round(f), side=round(s))
    f, s = burst(0, -1)
    r.check("strafe_left", s < -150 and abs(f) < 0.4 * abs(s), along=round(f), side=round(s))

    res = b.look(yaw=135, pitch=10)
    gz, gx = b._eval("player.GetAngle Z"), b._eval("player.GetAngle X")
    r.check("turn", res.ok and abs(((gz - 135 + 180) % 360) - 180) < 3 and abs(gx - 10) < 3, z=gz, x=gx, reply=str(res))
    b.look(yaw=0, pitch=0)

    nv.call("bot.move", {"forward": 1, "strafe": 0, "run": True})
    b.wait(1.0)
    stop = b.stop()
    p0 = b.pos()
    b.wait(0.5)
    r.check("stop", stop.ok and math.dist(p0, b.pos()) < 40, reply=str(stop), drift=round(math.dist(p0, b.pos())))

    j = b.jump()
    r.check("jump", j.ok, reply=str(j))
    # Land and settle first: within about 2 s of a jump the engine applies only one crouch toggle (first in-game run,
    # 2026-10-21: the second tap 1.1 s later left the player crouched; two seconds after the jump both taps worked).
    b.wait(SETTLE_AFTER_JUMP_S)

    try:
        s0 = b._eval(SNEAK_EXPR)
        b.sneak_toggle(); b.wait(0.8)
        s1 = b._eval(SNEAK_EXPR)
        b.sneak_toggle(); b.wait(0.8)
        s2 = b._eval(SNEAK_EXPR)
        r.check("sneak", s0 == 0 and s1 != 0 and s2 == 0, before=s0, after_one_tap=s1, after_two_taps=s2)
    except ConnectionLost:   # the game is gone (GameCrashed included): proof_args.guarded makes it exit 2, not a sneak FAIL
        raise
    except Exception as e:  # the console has IsSneaking, so a failed read is a defect, not a reason to skip
        r.check("sneak", False, error=f"cannot read {SNEAK_EXPR}: {e}")

    if args.target:
        try:
            h0 = b.health(args.target)
            fr = b.fire(args.target, shots=3, expect_hit=True)
            r.check("fire", fr.ok, health_before=h0, health_after=b.health(args.target), reply=str(fr))
        except ConnectionLost:
            raise
        except CHECK_ERRORS as e:
            r.check("fire", False, error=error_text(e))
    else:
        r.skip("fire", "pass --target <ref of a hostile in line of sight> and equip a loaded weapon first")

    if args.door:
        try:
            dist = getattr(args, "door_dist", DOOR_DIST)
            reached = walk_back_from(b, nv, args.door, dist) if dist > 0 else None
            before, cell0 = b.door_state(args.door), b.cell()
            b.look(ref=args.door)
            a = b.activate_crosshair()
            b.wait(1.5)
            cell1 = b.cell()
            try:
                after = b.door_state(args.door)
            except ConnectionLost:
                raise
            except CHECK_ERRORS:
                if not unreadable_after_ok(cell0, cell1):
                    raise              # same cell: the outer handler writes a FAIL row
                after = "unloaded"     # the player went through a load door and the old cell may be unloaded
            r.check("activate", a.ok and door_activated(before, after, cell0, cell1), before=before, after=after,
                    cell_before=cell0, cell_after=cell1, door_dist=None if reached is None else round(reached))
        except ConnectionLost:
            raise
        except CHECK_ERRORS as e:
            r.check("activate", False, error=error_text(e))
    else:
        r.skip("activate", "pass --door <ref of a door>")
    nv.call("bot.stop")
    return r


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--save")
    ap.add_argument("--target")
    ap.add_argument("--door")
    ap.add_argument("--door-dist", type=float, default=DOOR_DIST,
                    help="walk back to this many units from the door before the activate check (0: do not move the player)")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--dismiss-messages", action="store_true", help="clear stray message boxes (button 0) before the checks")
    ap.add_argument("--dismiss-quiet-s", type=float, default=20.0, help="done when no box has been shown for this many seconds")
    ap.add_argument("--dismiss-max-s", type=float, default=120.0, help="give up (FAIL) after this many seconds")
    ap.add_argument("--out", default=str(ROOT / "out" / "T6a" / "proof_input.json"))
    return ap


def check_args(args) -> list:
    """Up-front checks for a game run (not --fake). An empty list means the proof can run."""
    bad = proof_args.problems(args, REQUIRED, FORMS)
    dist = getattr(args, "door_dist", DOOR_DIST)
    if not (dist >= 0 and math.isfinite(dist)):
        bad.append(f"argument --door-dist: {dist!r} must be 0 or more (0: do not move the player)")
    return bad


def fake_bridge(mutant=None) -> FakeBridge:
    """The game-free world of the --fake self-test (target 0x603, door 0x601), behind a FakeBridge."""
    w = World()
    w.add_actor(0x603, (0, 400, 0))
    w.add_door(0x601, (50, 50, 0))
    return FakeBridge(w, mutant=mutant)


def _run_on(bridge, args) -> Report:
    with bridge as nv:
        if args.save:
            nv.load_save(args.save)
        return run(nv, args)


def main(argv=None, connect=None) -> int:
    """`connect(instance)` opens the bridge (default: the real pipe); tests pass a fake one.
    Returns the exit code (0, 1 or 2, see the module doc). It never raises SystemExit: a bad command line returns 2."""
    ap = parser()
    args, code = proof_args.parse(ap, argv)
    if args is None:
        return code
    if args.fake:
        nv = fake_bridge()
        args.target, args.door = "FalloutNV.esm:603", "FalloutNV.esm:601"
        rep = proof_args.guarded("the fake bridge", lambda: run(nv, args))
    else:
        bad = check_args(args)
        if bad:
            return proof_args.refuse(ap, bad)   # before the bridge is opened
        bridge = proof_args.open_bridge(connect or proof_args.connect_real, args.instance)
        if bridge is None:
            return 2
        rep = proof_args.guarded(f"instance {args.instance}", lambda: _run_on(bridge, args))
    if rep is None:
        return 2
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep.rows, indent=2), encoding="utf-8")
    failed = [x for x in rep.rows if x["result"] == "FAIL"]
    print("PROOF FAILED: " + ", ".join(x["check"] for x in failed) if failed else "PROOF OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
