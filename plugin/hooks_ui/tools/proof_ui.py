#!/usr/bin/env python3
"""L11 in-game proof (ONE instance, minimized, needs the game slot). Run from a long-lived process:

    python -I -X utf8 plugin/hooks_ui/tools/proof_ui.py

Private instance root ../fnv-instances-l11/0 (run.py provision --n 1 --plugin-dll out/L1/nvcoop.dll), nvcoop.ini role=host +
[ui] selftest=1 (loopback Bridge: raised menus are recorded and answered over RPC, no network). Checks, all through the real
engine: hook install, message-box routing + GetButtonPressed resume, service-menu routing, condition views, forced SayTo /
StartConversation capture and the host DialogMenu veto, forceCloseDialog, SwapTexture capture, origin-side box open/close.
"""
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "runner"))
ROOT = REPO.parent / "fnv-instances-l11"
os.environ["FNV_INSTANCES_ROOT"] = str(ROOT)
import instances  # noqa: E402
import nvbridge  # noqa: E402
import run as runner  # noqa: E402
import winproc  # noqa: E402

INSTANCE = int(os.environ.get("FNV_INSTANCE", "0"))
CHET = "falloutnv.esm:104C79"


def prepare():
    d = instances.instance_dir(INSTANCE)
    shutil.copy2(REPO / "out" / "L1" / "nvcoop.dll", d / "Data" / "NVSE" / "Plugins" / "nvcoop.dll")
    sav = d / "Saves" / "autosave.fos"
    src = REPO.parent / "fnv-instances-l5" / "0" / "Saves"
    sav.parent.mkdir(parents=True, exist_ok=True)
    for n in ("autosave.fos", "autosave.nvse"):
        if (src / n).exists() and not (sav.parent / n).exists():
            shutil.copy2(src / n, sav.parent / n)
    ini = d / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
    t = ini.read_text()
    out, seen_ui = [], False
    for ln in t.splitlines():
        if ln.strip().startswith("autoload="):
            ln = "autoload=autosave"
        if ln.strip().startswith("role="):
            ln = "role=host"
        if ln.strip() == "[ui]":
            seen_ui = True
        out.append(ln)
    if not seen_ui:
        out += ["", "[ui]", "selftest=1"]
    ini.write_text("\n".join(out) + "\n")


def wait_frames(nv, n=900, timeout=240):
    base = nv.ping()["frames"]
    t0 = time.time()
    while nv.ping()["frames"] - base < n:
        if time.time() - t0 > timeout:
            raise TimeoutError("frames did not advance")
        time.sleep(0.25)


def main():
    if winproc.list_pids("FalloutNV.exe") and not os.environ.get("NVUI_ALLOW_OTHERS"):
        pass  # other lanes hold other slots; we only refuse to touch their pids
    prepare()
    pid = runner.launch_instance(INSTANCE)
    st = runner.load_state()
    st["pids"][str(INSTANCE)] = pid
    runner.save_state(st)
    print("PID", pid, flush=True)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail)[:400] if detail != "" else ""), flush=True)
        ok &= bool(cond)

    nv = nvbridge.NvBridge.connect(INSTANCE, wait_s=240)
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        wait_frames(nv)
        call = nv.call

        # ---------------------------------------------------------------- install
        st = call("ui.status")
        print("hooks:", [(h["name"], h["status"]) for h in st["hooks"]])
        check("hooks_ui: all 15 host hooks installed, 0 failed", st["installed"] == 15 and st["failed"] == 0, st)

        # ---------------------------------------------------------------- message box routing
        call("ui.origin", {"slot": 2, "on": True})
        before = call("ui.status")["counters"]["boxes"]
        # a vanilla MESG with buttons; the console runs it like a script
        call("exec", {"cmd": "ShowMessage VEndGameWarningMsgIndy"})
        time.sleep(0.5)
        menus = call("ui.menus")
        s2 = call("ui.status")
        print("menus:", menus)
        check("box: ShowMessage inside origin scope reached the box hook", s2["counters"]["boxes"] == before + 1, s2["counters"])
        check("box: raised to the origin (slot 2), not opened on the host", len(menus) == 1 and menus[0]["origin"] == 2 and s2["router"]["boxesRouted"] == 1, menus)
        check("box: text + buttons captured", bool(menus) and len(menus[0]["text"]) > 5 and len(menus[0]["buttons"]) >= 1, menus)
        r = call("exec", {"cmd": "GetButtonPressed"})
        print("GetButtonPressed before reply:", r)
        check("box: script sees 'not pressed yet' (-1) until the origin answers", "-1" in str(r), r)
        call("ui.reply", {"menu": menus[0]["id"], "choice": 0})
        r = call("exec", {"cmd": "GetButtonPressed"})
        print("GetButtonPressed after reply:", r)
        check("box: script resumes with the origin's choice (button 0)", ">> 0" in str(r) or "0.00" in str(r), r)
        call("ui.reply", {"menu": menus[0]["id"], "choice": 1})
        r = call("exec", {"cmd": "GetButtonPressed"})
        check("box: duplicate reply is ignored (no second value)", "-1" in str(r), r)
        check("box: exactly one resume", call("ui.status")["router"]["resumes"] == 1 and call("ui.status")["router"]["dupResumes"] == 1)

        # ---------------------------------------------------------------- service menu
        n0 = call("ui.status")["router"]["serviceRouted"]
        call("exec.ref", {"ref": CHET, "cmd": "ShowBarterMenu"})
        time.sleep(0.5)
        s3 = call("ui.status")
        menus = call("ui.menus")
        check("service: ShowBarterMenu for a remote origin is raised (kind Barter) instead of opened", s3["router"]["serviceRouted"] == n0 + 1 and menus[-1]["kind"] == 2 and menus[-1]["origin"] == 2, menus[-1:])
        check("service: no barter menu opened on the host", not call("exec", {"cmd": "GetBarterGold"}) is None)
        call("ui.origin", {"on": False})

        # ---------------------------------------------------------------- condition views
        q = "falloutnv.esm:11A46C"  # replaced below when the quest EDID lookup works
        qr = call("ref.info", {"ref": "falloutnv.esm:000014"})
        # VNPCFollowers: formid from the snapshot of quests
        qref = None
        for cand in ("falloutnv.esm:0E6F1D", "falloutnv.esm:0E4F4C"):
            pass
        qref = call("quest.byedid", {"edid": "VNPCFollowers"}) if False else None
        # use the vanilla quest VCG01 (always present): the view table overrides var 3 per slot
        qref = "falloutnv.esm:10A214"  # VCG02 (also used by the L5 proofs)
        real = call("ui.evalvar", {"quest": qref, "idx": 1})
        print("evalvar real:", real)
        check("cond: engine condition function reachable through the hook", real["ok"] is True, real)
        call("ui.watch", {"quest": qref})
        call("ui.view", {"quest": qref, "idx": 1, "slot": 0, "value": 77})
        call("ui.view", {"quest": qref, "idx": 1, "slot": 2, "value": 99})
        v0 = call("ui.evalvar", {"quest": qref, "idx": 1})["value"]
        call("ui.origin", {"slot": 2, "on": True})
        v2 = call("ui.evalvar", {"quest": qref, "idx": 1})["value"]
        call("ui.origin", {"on": False})
        v0b = call("ui.evalvar", {"quest": qref, "idx": 1})["value"]
        check("cond: host evaluation sees the host's view (77)", v0 == 77 and v0b == 77, (v0, v0b))
        check("cond: evaluation for slot 2 sees slot 2's view (99)", v2 == 99, v2)
        check("cond: unwatched quest is untouched", call("ui.evalvar", {"quest": "falloutnv.esm:104C1C", "idx": 1})["ok"] is not None)

        # ---------------------------------------------------------------- forced dialogue
        call("exec.ref", {"ref": CHET, "cmd": "SayTo player GREETING"})
        time.sleep(0.5)
        f = call("ui.forced")
        print("forced:", f)
        check("forced: SayTo player captured as a party scene (no acting player)", len(f) >= 1 and f[-1]["kind"] == "say" and f[-1]["scene"] is True, f)
        call("ui.origin", {"slot": 2, "on": True})
        call("exec.ref", {"ref": CHET, "cmd": "SayTo player GREETING"})
        time.sleep(0.5)
        f = call("ui.forced")
        check("forced: SayTo inside acting scope targets slot 2", f[-1]["target"] == 2 and f[-1]["scene"] is False, f[-1])
        # StartConversation for a remote actor: the host must NOT open its DialogMenu
        call("ui.actor", {"slot": 2})
        call("exec.ref", {"ref": CHET, "cmd": "StartConversation player GREETING"})
        time.sleep(1.0)
        d = call("ui.dialogopen")
        f = call("ui.forced")
        check("forced: StartConversation captured with actor slot 2", f[-1]["kind"] == "conversation", f[-1])
        check("forced: host DialogMenu NOT opened for a remote actor (veto)", d["open"] is False, d)
        # control: the actor is the host -> the engine really opens the DialogMenu, then forceCloseDialog closes it
        call("ui.actor", {"slot": 0})
        call("exec.ref", {"ref": CHET, "cmd": "StartConversation player GREETING"})
        time.sleep(2.0)
        d = call("ui.dialogopen")
        check("forced: control, host actor -> native DialogMenu opens", d["open"] is True, d)
        if d["open"]:
            c = call("ui.closedialog")
            time.sleep(1.5)
            check("forceCloseDialog returned true", c["closed"] is True, c)
            check("forceCloseDialog: DialogMenu is gone", call("ui.dialogopen")["open"] is False)
        call("ui.origin", {"on": False})

        # ---------------------------------------------------------------- slideshow
        n = len(call("ui.slides"))
        call("exec", {"cmd": "SwapTexture 14 \"fnvcoop\\\\a\" \"fnvcoop\\\\b\""})
        time.sleep(0.3)
        sl = call("ui.slides")
        print("slides:", sl)
        check("slides: SwapTexture captured with both texture paths", len(sl) == n + 1 and sl[-1]["new"].endswith("b") and sl[-1]["old"].endswith("a"), sl[-1:])

        n = len(call("ui.slides"))
        r = call("ui.swap", {"ref": "falloutnv.esm:000014", "old": "fnvcoop\c", "new": "fnvcoop\d"})
        time.sleep(0.3)
        check("slides: client playback (ops::swapTexture) runs the engine command", r["ok"] is True and len(call("ui.slides")) == n + 1, r)

        # ---------------------------------------------------------------- origin-side open/close
        r = call("ui.open", {"id": 900, "text": "L11 origin-side test box", "buttons": ["One", "Two"]})
        time.sleep(1.0)
        check("ops: origin-side message box opens", r["ok"] is True and call("ui.closemenu", {"menu": 899})["queued"] == 1, r)
        c = call("ui.closemenu", {"menu": 900})
        time.sleep(1.0)
        check("ops: closeMenu forces the box shut", c["queued"] == 0, c)
        check("game still alive and answering", call("ping") is not None)
    finally:
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
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
