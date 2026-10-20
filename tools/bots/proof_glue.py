"""In-game proof for the engine-glue bridge methods (bot.menu / bot.dialog / bot.say / bot.inventory / bot.vats / bot.consent /
test.fasttravel / dev.countRefs / dev.terminal). THE LEAD RUNS THIS, one game at a time; tests only run it over the fake bridge.

    python tools/bots/proof_glue.py --instance 0 --npc <ref> --info <INFO form> [--item <form>] [--target <ref>]
                                    [--marker <ref>] [--base <form>] [--other-info <INFO form>]
    python tools/bots/proof_glue.py --fake

Needs nvcoop.ini [dev] bots=1 and the player next to the NPC (a living NPC with a plain greeting, e.g. Sunny Smiles).
--npc and --info are required without --fake. Every form argument is Plugin.esm:HEX or a bare hex runtime id (a 0x prefix
and leading zeros are allowed, an empty value is refused).
--info and --other-info are compared as numbers, as (plugin, local id), never as text (proof_args.form_key). A bare id is a
runtime id whose top byte is the load index, and the game's plugin list gives its plugin. Plugin.esm:HEX with 8 digits is
accepted only when its top byte is the load index of that plugin. The low 24 bits are compared only when the plugin is known
to match.
Exit codes: 0 every check passed, 1 a check failed, 2 the proof could not run: a bad command line (refused before the
bridge is opened), no game on the instance, the game lost during the run (crash or pipe loss), or --info / --other-info
that the load order cannot resolve or that name the same INFO (refused before the proof drives anything).
Each check prints measured numbers. Every engine call behind these methods is unverified at runtime, so the first failure is
expected information, not a surprise: the printed 'reply' says which gate refused.
  menu       none -> dialogue after talking (kind and id), dialogReady flips true when the line has finished
  dialog     the offered INFO list contains --info; with --other-info (an INFO this NPC does not offer now, e.g. a line of
             another NPC) the list must also NOT contain it, so a bot.dialog that lists every INFO cannot pass
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
from bots import Bot, FakeBridge, World, proof_args  # noqa: E402

# Required without --fake: dest -> what the user must give. FORMS: every dest that holds a form id.
REQUIRED = {"npc": "the ref of the NPC to talk to (Plugin.esm:HEX)",
            "info": "an INFO form that the NPC offers now (Plugin.esm:HEX)"}
FORMS = ("npc", "info", "other_info", "item", "target", "terminal", "marker", "base")


def choice_key(c, plugins):
    """The form key (proof_args.form_key) of one bot.dialog choice: its 'info' name, else its numeric 'formId'.
    None when neither resolves in the load order `plugins`."""
    for v in (c.get("info"), c.get("formId")):
        if v is None or v == "":
            continue
        try:
            return proof_args.form_key(v, plugins)
        except ValueError:
            continue
    return None


def offers(choices, want, plugins):
    """True if the bot.dialog choices contain the INFO with form key `want`. Both sides are compared as
    (plugin, local id) numbers, so '0x801', '0801', '00000801' and 'FalloutNV.esm:801' all name the same INFO
    and FalloutNV.esm:1800 never matches FalloutNV.esm:800."""
    return any(choice_key(c, plugins) == want for c in choices)


def dialog_keys(a, plugins):
    """(--info key, --other-info key or None) with the load order `plugins`, resolved once before the proof drives
    anything. Raises proof_args.CannotRun when one of them does not resolve or when both name the same INFO."""
    keys = {}
    for dest in ("info", "other_info"):
        v = getattr(a, dest)
        if v is None:
            keys[dest] = None
            continue
        try:
            keys[dest] = proof_args.form_key(v, plugins)
        except ValueError as e:
            raise proof_args.CannotRun(f"argument {proof_args.flag(dest)}: {e}") from None
    if keys["other_info"] is not None and keys["other_info"] == keys["info"]:
        raise proof_args.CannotRun(f"argument --other-info {a.other_info!r} names the same INFO as --info {a.info!r} in this "
                                   f"load order ({keys['info'][0]}:{keys['info'][1]:X}): give an INFO that this NPC does not offer now")
    return keys["info"], keys["other_info"]


def run(nv, a):
    rows = []

    def check(name, ok, **m):
        rows.append({"check": name, "result": "PASS" if ok else "FAIL", **m})
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {m}")

    def skip(name, why):
        rows.append({"check": name, "result": "SKIP", "why": why})
        print(f"SKIP  {name}  {why}")

    plugins = list(nv.plugins())
    want, other_key = dialog_keys(a, plugins)   # CannotRun here: nothing has been driven yet
    b = Bot(nv, "glue")
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
    if other_key is not None:
        other = offers(ch, other_key, plugins)
        check("dialog", offers(ch, want, plugins) and not other, choices=ch, other_info=a.other_info, other_offered=other)
    else:
        check("dialog", offers(ch, want, plugins), choices=ch)
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


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--npc", help="NPC to talk to (required without --fake)")
    ap.add_argument("--info", help="INFO the NPC offers now (required without --fake)")
    ap.add_argument("--other-info", help="INFO the NPC does NOT offer now: the dialog check fails if bot.dialog lists it")
    ap.add_argument("--item"); ap.add_argument("--target")
    ap.add_argument("--terminal"); ap.add_argument("--entry", type=int, default=0); ap.add_argument("--marker"); ap.add_argument("--base"); ap.add_argument("--fake", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "out" / "T6a" / "proof_glue.json"))
    return ap


def check_args(a):
    """Up-front checks for a game run (not --fake). An empty list means the proof can run.
    The same-INFO refusal here is the part that needs no load order (proof_args.provably_same); dialog_keys does the rest."""
    bad = proof_args.problems(a, REQUIRED, FORMS)
    if not bad and a.other_info is not None and proof_args.provably_same(a.other_info, a.info):
        bad.append("argument --other-info names the same INFO as --info: give an INFO that this NPC does not offer now")
    return bad


# --fake: the arguments the self-test uses (0x801 is a line of another actor, so the NPC 0x602 does not offer it).
FAKE_ARGS = {"npc": "FalloutNV.esm:602", "info": "FalloutNV.esm:800", "other_info": "FalloutNV.esm:801",
             "item": "FalloutNV.esm:500", "terminal": "FalloutNV.esm:605", "target": "FalloutNV.esm:603",
             "marker": "FalloutNV.esm:600", "base": "FalloutNV.esm:603"}


def fake_bridge(mutant=None):
    """The game-free world that FAKE_ARGS describe, behind a FakeBridge (with `mutant`, one injected defect)."""
    w = World()
    w.add_actor(0x602, (100, 0, 0), topics=[0x800])
    w.add_actor(0x603, (500, 0, 0), topics=[0x801])
    w.add_actor(0x604, (600, 0, 0)).base_id = 0x603
    w.add_container(0x600, (900, 0, 0))
    nv = FakeBridge(w, mutant=mutant)
    nv.me.items[0x500] = 4
    nv.call = _wrap_fake(nv)
    w.add_terminal(0x605, (0, 300, 0)).topics_text = ["Read log", "Exit"]
    return nv


def _run_on(bridge, a):
    with bridge as nv:
        return run(nv, a)


def main(argv=None, connect=None):
    """`connect(instance)` opens the bridge (default: the real pipe); tests pass a fake one.
    Returns the exit code (0, 1 or 2, see the module doc). It never raises SystemExit: a bad command line returns 2."""
    ap = parser()
    a, code = proof_args.parse(ap, argv)
    if a is None:
        return code
    if a.fake:
        nv = fake_bridge()
        for k, v in FAKE_ARGS.items():
            setattr(a, k, v)
        rows = proof_args.guarded("the fake bridge", lambda: run(nv, a))
    else:
        bad = check_args(a)
        if bad:
            return proof_args.refuse(ap, bad)   # before the bridge is opened
        bridge = proof_args.open_bridge(connect or proof_args.connect_real, a.instance)
        if bridge is None:
            return 2
        rows = proof_args.guarded(f"instance {a.instance}", lambda: _run_on(bridge, a))
    if rows is None:
        return 2
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
