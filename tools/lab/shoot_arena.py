"""In-game frame grabs of the lab cell (the game's own back buffer through the bridge; never the desktop).

    python -I tools/lab/shoot_arena.py --instance 0 [--frames 96] [--out out/lab/shots]
The copy must run with NVCoopLab.esp installed and [bridge] screenshotD3D9=1. Writes stills plus orbit_NNN.png frames.
"""
import argparse, json, math, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "runner"))
import nvbridge  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from bots.bot import Bot  # noqa: E402


def heading(px, py, cx, cy):
    return math.degrees(math.atan2(cx - px, cy - py)) % 360.0


def place(nv, x, y, z, yaw, pitch):
    nv.exec(f"player.setpos x {x:.0f}")
    nv.exec(f"player.setpos y {y:.0f}")
    nv.exec(f"player.setpos z {z:.0f}")
    nv.exec(f"player.setangle z {yaw:.1f}")
    nv.exec(f"player.setangle x {pitch:.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--save", default="nvcoop_autosave_ready")
    ap.add_argument("--frames", type=int, default=96)
    ap.add_argument("--out", default="out/lab/shots")
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    lay = json.loads((ROOT / "out" / "lab" / "layout.json").read_text(encoding="utf-8"))
    rooms = lay["rooms"]
    fz = lay["cell"].get("floor_z", 7040.0)
    xs = [r["origin"][0] for r in rooms.values()]
    ys = [r["origin"][1] for r in rooms.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    log = {}
    with nvbridge.NvBridge.connect(a.instance, wait_s=240) as nv:
        log["load"] = str(nv.load_save(a.save))[:120]
        time.sleep(4)
        bot = Bot(nv)
        log["dismiss_load"] = str(bot.dismiss_messages(quiet_s=4, max_s=45, max_n=12))[:160]
        nv.exec("coc NVCoopLab")
        time.sleep(10)
        log["dismiss_coc"] = str(bot.dismiss_messages(quiet_s=4, max_s=40, max_n=12))[:160]
        log["menu"] = str(bot.menu_info())[:160]
        for name in ("fight", "vats"):
            for k, ref in rooms[name].get("refs", {}).items():
                try:
                    nv.exec("enable", ref=ref)
                except Exception as e:   # noqa: BLE001
                    log[f"enable {name}.{k}"] = str(e)[:100]
        for cmd in ("tgm", "tcl", "tm"):
            nv.exec(cmd)
        time.sleep(3)
        nv.exec("tai")      # freeze AI so the geckos pose instead of charging the camera

        def shot(name):
            last = ""
            for _ in range(3):      # the first call installs the frame hook and asks for a retry
                try:
                    return str(nv.screenshot(str(out / f"{name}.png")))[:160]
                except Exception as e:   # noqa: BLE001
                    last = "error " + str(e)[:160]
                    time.sleep(0.4)
            return last

        cams = lay.get("decor", {}).get("cameras", {})
        place(nv, cx, cy - 1250, fz + 20, 0.0, 3.0)         # the whole grid from the south, under the ceiling (floor + 256)
        time.sleep(1.5)
        log["overview"] = shot("overview")
        for name in ("spawn", "walk", "fight", "loot", "door", "npc", "vats", "quest"):
            c = cams.get(name)
            if c:
                (px, py, pz), yaw, pitch = c["pos"], c["yaw"], c["pitch"]
            else:                                             # no decor cameras: stand south of the room origin
                ox, oy, _ = rooms[name]["origin"]
                px, py, pz, yaw, pitch = ox, oy - 380, fz + 20, 0.0, 6.0
            place(nv, px, py, pz, yaw, pitch)
            time.sleep(1.2)
            log[name] = shot(name)
        # the clip: a slow dolly past the open fronts of the south row, then of the north row (the corridor between the rows)
        rows = sorted({round(r["origin"][1]) for r in rooms.values()})
        x0, x1 = min(xs) - 300, max(xs) + 300
        per = max(2, a.frames // max(1, len(rows)))
        k = 0
        for ry in rows:
            for i in range(per):
                px = x0 + (x1 - x0) * i / (per - 1)
                place(nv, px, ry - 380, fz + 20, 0.0, 5.0)
                time.sleep(0.12)
                res = shot(f"orbit_{k:03d}")
                k += 1
                if res.startswith("error"):
                    log["orbit_error"] = f"frame {k}: {res}"
                    break
        nv.exec("tai")
    print(json.dumps(log))
    (out / "shots.json").write_text(json.dumps(log, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
