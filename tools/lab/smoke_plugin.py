"""One copy: does the game load NVCoopLab.esp, and does `coc NVCoopLab` reach the lab cell? Prints one JSON line.

    python -I tools/lab/smoke_plugin.py --instance 0 [--out out/lab/smoke]
The copy must already be running (run.py up) with the plugin installed (tools/lab/esp/install_lab.py).
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "runner"))
import nvbridge  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--save", default="nvcoop_autosave_ready")
    ap.add_argument("--out", default="out/lab/smoke")
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    r = {"instance": a.instance}
    with nvbridge.NvBridge.connect(a.instance, wait_s=240) as nv:
        mods = nv.plugins()
        r["loaded"] = "NVCoopLab.esp" in [m for m in mods]
        r["mods_tail"] = mods[-4:]
        r["load"] = str(nv.load_save(a.save))[:200]
        time.sleep(5)
        for cmd in ("coc NVCoopLab", "coc GreenRoom"):
            r[cmd] = str(nv.exec(cmd))[:200]
            time.sleep(12)
            try:
                z = nv.eval("player.GetPos z")
                x = nv.eval("player.GetPos x")
                y = nv.eval("player.GetPos y")
            except Exception as e:   # noqa: BLE001
                x = y = z = None
                r[cmd + " err"] = str(e)[:200]
            r[cmd + " pos"] = [x, y, z]
            try:
                r[cmd + " shot"] = str(nv.screenshot(str(out / ("shot_" + cmd.split()[1] + ".png"))))[:200]
            except Exception as e:   # noqa: BLE001
                r[cmd + " shot"] = "error " + str(e)[:120]
            if z is not None and abs(z - 7040) < 400:
                r["reached_by"] = cmd
                break
    print(json.dumps(r))
    (out / "smoke.json").write_text(json.dumps(r, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
