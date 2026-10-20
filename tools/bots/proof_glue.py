"""In-game proof for the engine-glue bridge methods (bot.menu / bot.dialog / bot.say / bot.inventory / bot.vats / bot.consent /
test.fasttravel / dev.countRefs / dev.terminal). THE LEAD RUNS THIS, one game at a time; tests only run it over the fake bridge.

    python tools/bots/proof_glue.py --instance 0 --npc <ref> --info <INFO form> [--item <form>] [--target <ref>]
                                    [--marker <ref>] [--base <form>] [--other-info <INFO form>]
    python tools/bots/proof_glue.py --fake

Needs nvcoop.ini [dev] bots=1 and the player next to the NPC (a living NPC with a plain greeting, e.g. Sunny Smiles).
--dismiss-messages (opt-in, off by default): first clear stray message boxes with Bot.dismiss_messages. It presses until no box has
been shown for --dismiss-quiet-s seconds (default 20, the boxes come in waves), at most --dismiss-max-s (default 120). It adds a
"dismiss_messages" row with the press count and the seconds it waited.
Each check prints measured numbers. Every engine call behind these methods is unverified at runtime, so the first failure is
expected information, not a surprise: the printed 'reply' says which gate refused.
  menu       none -> dialogue after talking (kind and id), dialogReady flips true when the line has finished
  dialog     the offered INFO list contains --info
  say        bot.say --info: the NPC answers (dialogState leaves 2 and returns to 2 / the dialogue closes on a goodbye)
  inventory  bot.inventory totals equal the console's GetItemCount for --item (catches delta-vs-total mistakes)
  vats       with --target: bot.vats enters VATS, selects the target, plays it: phase done, target health reported
  consent    raise a box with ui.open, answer it with bot.consent, ui.menus/ui.reply sink sees the answer
  fasttravel with --marker: test.fasttravel moves the player next to it
  countrefs  with --base: dev.countRefs equals the number of matching refs from world.actors
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "runner"))
from bots import Bot, FakeBridge, World  # noqa: E402


def run(nv, a):
    rows = []

    def check(name, ok, **m):
        rows.append({"check": name, "result": "PASS" if ok else "FAIL", **m})
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {m}")

    def skip(name, why):
        rows.append({"check": name, "result": "SKIP", "why": why})
        print(f"SKIP  {name}  {why}")

    b = Bot(nv, "glue")
    if getattr(a, "dismiss_messages", False):
        d = b.dismiss_messages(quiet_s=getattr(a, "dismiss_quiet_s", 20.0), max_s=getattr(a, "dismiss_max_s", 120.0))
        check("dismiss_messages", d.ok, pressed=d.data.get("pressed"), waited_s=d.data.get("waited_s"), reply=str(d))
    m0 = b.menu_info()
    check("menu_before", m0.get("kind") in ("none", "hud"), reply=m0)
    t = b.talk(a.npc)
    for _ in range(40):
        m1 = b.menu_info()
        if m1.get("dialogReady"):
            break
        b.wait(0.25)
    check("menu_dialogue", t.ok and m1.get("kind") == "dialogue" and m1.get("dialogReady"), reply=m1, talk=str(t))
    ch = b.dialog_choices()
    check("dialog", any(c["info"].lower().endswith(a.info.split(":")[-1].lower()) for c in ch), choices=ch)
    s = b.choose_topic(a.info)
    b.wait(1.0)
    m2 = b.menu_info()
    check("say", s.ok, reply=str(s), after=m2)
    b.end_dialogue()

    if a.item:
        inv = b.inventory(items=[a.item])
        console = b.inventory_count(a.item)
        check("inventory", list(inv.values()) == [console], engine=inv, console=console)
    else:
        skip("inventory", "pass --item <form the player carries>")
    if a.target:
        v = b.vats_target(a.target)
        check("vats", v.ok, reply=str(v), menu_after=b.menu_info().get("kind"))
    else:
        skip("vats", "pass --target <hostile in view> with a weapon equipped and AP")
    nv.call("ui.open", {"id": 4242, "text": "bot consent probe", "buttons": ["Ready", "Not yet"]})
    b.wait(0.5)
    c = b.consent("ready")
    check("consent", c.ok, reply=str(c))
    if a.terminal:
        u = b.use_terminal(a.terminal, a.entry)
        check("terminal", u.ok and any(x["index"] == a.entry for x in b.terminal_used(a.terminal)), reply=str(u))
    else:
        skip("terminal", "pass --terminal <terminal ref in reach> [--entry N]; the engine result (door opens, log shown) must also be checked by eye/state")
    if a.marker:
        f = b.fast_travel(a.marker)
        check("fasttravel", f.ok, reply=str(f), genuine=f.data.get("genuine"))
    else:
        skip("fasttravel", "pass --marker <map marker ref>")
    if a.item:
        have = b.inventory_count(a.item)
        if have >= 1:
            d = b.drop(a.item, 1)
            check("drop_ref", d.ok and bool(d.data.get("ref")), reply=str(d), source=d.data.get("source"))
        else:
            skip("drop_ref", "the player has none of --item to drop")
    if a.base:
        n = b.count_refs(a.base)
        listed = nv.call("world.actors", {"max": 500, "type": 0x3B}).get("actors", [])
        expect = sum(1 for x in listed if x.get("base") == int(a.base.split(":")[-1], 16))
        check("countrefs", n == expect, dev_countRefs=n, world_actors=expect)
    else:
        skip("countrefs", "pass --base <actor base form present in the cell>")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--npc"); ap.add_argument("--info"); ap.add_argument("--item"); ap.add_argument("--target")
    ap.add_argument("--terminal"); ap.add_argument("--entry", type=int, default=0); ap.add_argument("--marker"); ap.add_argument("--base"); ap.add_argument("--fake", action="store_true")
    ap.add_argument("--dismiss-messages", action="store_true", help="clear stray message boxes (button 0) before the checks")
    ap.add_argument("--dismiss-quiet-s", type=float, default=20.0, help="done when no box has been shown for this many seconds")
    ap.add_argument("--dismiss-max-s", type=float, default=120.0, help="give up (FAIL) after this many seconds")
    ap.add_argument("--out", default=str(ROOT / "out" / "T6a" / "proof_glue.json"))
    a = ap.parse_args()
    if a.fake:
        w = World()
        w.add_actor(0x602, (100, 0, 0), topics=[0x800])
        w.add_actor(0x603, (500, 0, 0))
        w.add_actor(0x604, (600, 0, 0)).base_id = 0x603
        w.add_container(0x600, (900, 0, 0))
        nv = FakeBridge(w)
        nv.me.items[0x500] = 4
        nv.call = _wrap_fake(nv)
        a.npc, a.info, a.item = "FalloutNV.esm:602", "FalloutNV.esm:800", "FalloutNV.esm:500"
        w.add_terminal(0x605, (0, 300, 0)).topics_text = ["Read log", "Exit"]
        a.terminal = "FalloutNV.esm:605"
        a.target, a.marker, a.base = "FalloutNV.esm:603", "FalloutNV.esm:600", "FalloutNV.esm:603"
        rows = run(nv, a)
    else:
        from nvbridge import NvBridge
        with NvBridge.connect(a.instance, wait_s=10) as nv:
            rows = run(nv, a)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    bad = [r["check"] for r in rows if r["result"] == "FAIL"]
    print("PROOF FAILED: " + ", ".join(bad) if bad else "PROOF OK")
    return 1 if bad else 0


def _wrap_fake(nv):
    """The fake has no ui.open / world.actors; add the two the proof uses."""
    orig = nv.call

    def call(method, params=None, timeout_s=35.0):
        if method == "ui.open":
            nv.box = params["id"]
            return {}
        if method == "world.actors":
            return {"actors": [{"id": t.id, "base": t.base_id or t.id} for t in nv.world.things.values() if t.kind == "actor"]}
        return orig(method, params, timeout_s)
    return call


if __name__ == "__main__":
    sys.exit(main())
