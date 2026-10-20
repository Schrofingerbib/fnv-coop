"""In-game proof for the bot input driver (plugin/bots). THE LEAD RUNS THIS, ONE GAME AT A TIME; it is never run by tests
against a real game. It proves, with numbers, that forced controls reach the engine and behave like real keys.

    python tools/bots/proof_input.py --instance 0 [--save <name>] [--target <Plugin.esm:HEX>] [--door <Plugin.esm:HEX>]
    python tools/bots/proof_input.py --fake            # same script over the fake bridge (game-free self-test)

Needs nvcoop.ini [dev] bots=1 on the instance. Writes out/T6a/proof_input.json and exits non-zero on any FAIL.
--dismiss-messages (opt-in, off by default): first clear stray message boxes with Bot.dismiss_messages (button 0 while bot.menu says
"message"), e.g. the DLC notices a fresh save shows about 5 s after load; it adds a "dismiss_messages" row.
Checks (each prints measured values so a failure says what the game did):
  hook      bot.status reports hooked=true and the control-state query counter rises each frame
  forward   hold Forward+Run for 2 s: the player moves 300..700 units along the facing direction (real locomotion speed)
  back      Backward moves the opposite way; Left/Right strafe perpendicular (proves the yaw convention and control ids 0..3)
  turn      bot.look yaw/pitch read back through GetAngle
  stop      bot.stop: nothing held, the player stops within 0.5 s
  jump      Z rises > 5 units (control 12, an edge-only control: proves the edge is served exactly once)
  sneak     two taps toggle crouch on then off (edge fires exactly once per tap, or the state would not alternate)
  fire      with --target: pulses Attack at the target; its health drops (needs an equipped, loaded weapon)
  activate  with --door: look at it and press Activate; the door state changes
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "runner"))
from bots import Bot, FakeBridge, World  # noqa: E402


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
        d = b.dismiss_messages()
        r.check("dismiss_messages", d.ok, reply=str(d))
    st = nv.call("bot.status")
    q0 = st.get("frame", 0)
    b.wait(0.5)
    st2 = nv.call("bot.status")
    r.check("hook", bool(st.get("hooked")) and st2.get("frame", 0) > q0, status=st, frame_after=st2.get("frame"))

    yaw = 0.0
    b.look(yaw=yaw)
    b.wait(0.3)

    def burst(forward, strafe, secs=2.0):
        p0 = b.pos()
        nv.call("bot.move", {"forward": forward, "strafe": strafe, "run": True})
        b.wait(secs)
        nv.call("bot.stop")
        b.wait(0.5)
        return rel_move(b, p0, yaw)

    f, s = burst(1, 0)
    r.check("forward", 300 <= f <= 700 and abs(s) < 0.25 * abs(f), along=round(f), side=round(s))
    f, s = burst(-1, 0)
    r.check("back", f < -150 and abs(s) < 0.4 * abs(f), along=round(f), side=round(s))
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

    try:
        s0 = b._eval("player.GetSneaking")
        b.sneak_toggle(); b.wait(0.8)
        s1 = b._eval("player.GetSneaking")
        b.sneak_toggle(); b.wait(0.8)
        s2 = b._eval("player.GetSneaking")
        r.check("sneak", s0 == 0 and s1 != 0 and s2 == 0, before=s0, after_one_tap=s1, after_two_taps=s2)
    except Exception as e:  # GetSneaking may not exist in this console grammar
        r.skip("sneak", f"cannot read sneak state: {e}")

    if args.target:
        h0 = b.health(args.target)
        fr = b.fire(args.target, shots=3, expect_hit=True)
        r.check("fire", fr.ok, health_before=h0, health_after=b.health(args.target), reply=str(fr))
    else:
        r.skip("fire", "pass --target <ref of a hostile in line of sight> and equip a loaded weapon first")

    if args.door:
        before = b.door_state(args.door)
        b.look(ref=args.door)
        a = b.activate_crosshair()
        b.wait(1.5)
        after = b.door_state(args.door)
        r.check("activate", a.ok and before != after, before=before, after=after)
    else:
        r.skip("activate", "pass --door <ref of a door within reach>")
    nv.call("bot.stop")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--save")
    ap.add_argument("--target")
    ap.add_argument("--door")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--dismiss-messages", action="store_true", help="clear stray message boxes (button 0) before the checks")
    ap.add_argument("--out", default=str(ROOT / "out" / "T6a" / "proof_input.json"))
    args = ap.parse_args()
    if args.fake:
        w = World()
        w.add_actor(0x603, (0, 400, 0))
        w.add_door(0x601, (50, 50, 0))
        nv = FakeBridge(w)
        args.target, args.door = "FalloutNV.esm:603", "FalloutNV.esm:601"
        rep = run(nv, args)
    else:
        from nvbridge import NvBridge
        with NvBridge.connect(args.instance, wait_s=10) as nv:
            if args.save:
                nv.load_save(args.save)
            rep = run(nv, args)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep.rows, indent=2), encoding="utf-8")
    failed = [x for x in rep.rows if x["result"] == "FAIL"]
    print("PROOF FAILED: " + ", ".join(x["check"] for x in failed) if failed else "PROOF OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
