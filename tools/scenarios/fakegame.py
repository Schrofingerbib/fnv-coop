"""In-memory stand-in for the bridge of 1..N game instances sharing one world (test and dry-run use only).

It models just enough of the console / eval / call surface for the shipped scenarios: quest stages, kills, locks, item counts,
spawns, positions, health, loot pickup, teammates. Flags inject the bug classes the checks must catch
(exec_ref_ignored: the host checks exec's ref, then runs the line with no calling reference).

The console behaves like the game's (in game, 2026-10-21): "<hex>.Func" fails with Script command "<hex>.Func" not found,
"player.Func" works, and a line for another reference runs as exec(cmd, ref=<hex>) / eval(expr, ref=<hex>).
"""
import re

HEX = r"([0-9A-Fa-f]{8})"


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


class World:
    def __init__(self, bases=None, markers=None, **flags):
        self.flags = flags             # desync_guest, double_spawn, dup_loot, no_handoff
        self.bases = bases or {}       # activatable ref hex -> item base hex
        self.markers = markers or {}   # marker hex -> (x, y)
        self.nplayers = 2
        self.loads = []
        self.saves = {}
        self.reset_world()
        self.calls = []                # (player, kind, text)

    def reset_world(self):
        self.stage, self.stage_done = {}, {}
        self.dead, self.disabled, self.locked, self.taken = set(), set(), {}, set()
        self.items, self.health, self.team, self.spawned = {}, {}, set(), {}
        self.pos, self.owner, self.left = {}, 0, set()

    def where(self, who, i):
        if who == "player":
            who = "player%d" % i
        if who not in self.pos:
            self.pos[who] = self.markers.get(who, (hash(who) % 90000 + 20000, 0))
        return self.pos[who]


class FakeNv:
    def __init__(self, world, i, clock):
        self.w, self.i, self.clock = world, i, clock
        self.lease = "fake"

    def _log(self, kind, text):
        self.w.calls.append((self.i, kind, text))

    # -- console. Like the game (2026-10-21): "player.X" works, "<hex>.X" fails with Script command "<hex>.X" not found,
    # and a line for another reference names it in exec's ref (the console's calling reference).
    def _who(self, text, ref):
        """-> (who, verb, rest words) of a console line / expression, or None when it names no reference."""
        if ref is not None and not self.w.flags.get("exec_ref_ignored"):   # twin flag: the host drops the ref
            if not re.fullmatch(HEX, ref):
                raise RuntimeError("exec: bad ref %r (the scenario runner passes runtime hex ids)" % ref)
            m = re.match(r"^(\w+)\s*(.*)$", text)
            return (ref.upper(), m.group(1).lower(), m.group(2).split()) if m else None
        m = re.match(r"^player\.(\w+)\s*(.*)$", text, re.I)
        return ("player", m.group(1).lower(), m.group(2).split()) if m else None

    @staticmethod
    def _hex_prefixed(text):
        return re.match(r"^%s\.\w" % HEX, text) is not None

    def exec(self, cmd, ref=None):
        w, i = self.w, self.i
        self._log("exec", cmd if ref is None else "%s @%s" % (cmd, ref))
        if self._hex_prefixed(cmd):
            return {"ok": False, "output": 'Script command "%s" not found' % cmd.split()[0]}
        m = re.match(r"^setstage %s (\d+)$" % HEX, cmd, re.I)
        if m:
            w.stage[m.group(1).upper()] = int(m.group(2))
            w.stage_done.setdefault(m.group(1).upper(), set()).add(int(m.group(2)))
            return {"ok": True, "output": ""}
        parsed = self._who(cmd, ref)
        if not parsed:
            return {"ok": True, "output": "Could not parse"} if cmd.startswith("bogus") else {"ok": True, "output": ""}
        who, verb, rest = parsed
        key = "player%d" % i if who == "player" else who
        if verb == "kill":
            w.dead.add(who)
        elif verb == "resurrect":
            w.dead.discard(who)
        elif verb == "enable":
            w.disabled.discard(who)
        elif verb == "disable":
            w.disabled.add(who)
        elif verb == "lock":
            w.locked[who] = int(rest[0])
        elif verb == "unlock":
            w.locked.pop(who, None)
        elif verb == "additem":
            w.items[(key, rest[0].upper())] = w.items.get((key, rest[0].upper()), 0) + int(rest[1])
        elif verb == "removeitem":
            w.items[(key, rest[0].upper())] = max(0, w.items.get((key, rest[0].upper()), 0) - int(rest[1]))
        elif verb == "placeatme":
            n = int(rest[1]) * (2 if w.flags.get("double_spawn") else 1)
            w.spawned[(who, rest[0].upper())] = w.spawned.get((who, rest[0].upper()), 0) + n
        elif verb == "moveto":
            dest = rest[0]
            tgt = "player%d" % i if dest == "player" else dest.upper()
            w.pos[key] = w.where(tgt, i)
            if who == "player" and i == 0 and w.flags.get("novac") == dest.upper() and not w.flags.get("no_handoff"):
                w.owner = 1
        elif verb == "activate":
            if who in w.bases and (who not in w.taken or w.flags.get("dup_loot")):
                w.taken.add(who)
                base, me = w.bases[who], "player%d" % i
                w.items[(me, base)] = w.items.get((me, base), 0) + 1
        elif verb == "setplayerteammate":
            w.team.add(who)
        elif verb == "setav" and rest[0].lower() == "health":
            w.health[who] = float(rest[1])
        return {"ok": True, "output": ""}

    # -- eval
    def eval(self, expr, ref=None):
        w, i = self.w, self.i
        self._log("eval", expr if ref is None else "%s @%s" % (expr, ref))
        if self._hex_prefixed(expr):       # the old "<hex>.Func": the game's console cannot compile it (nvbridge.eval raises)
            raise RuntimeError('cannot evaluate %r: Script command "%s" not found' % (expr, expr.split()[0]))
        guest_blind = w.flags.get("desync_guest") and i == 1
        m = re.match(r"^GetStage %s$" % HEX, expr)
        if m:
            return 0.0 if guest_blind else float(w.stage.get(m.group(1).upper(), 0))
        m = re.match(r"^GetStageDone %s (\d+)$" % HEX, expr)
        if m:
            return 0.0 if guest_blind else float(int(m.group(2)) in w.stage_done.get(m.group(1).upper(), set()))
        parsed = self._who(expr, ref)
        if not parsed:
            raise RuntimeError("cannot evaluate %r: no calling reference" % expr)
        who, fn, rest = parsed
        key = "player%d" % i if who == "player" else who
        if guest_blind and fn in ("getdead", "getlocked", "getav", "getitemcount", "getnumrefs", "getplayerteammate"):
            return 0.0
        if fn == "getdead":
            return float(who in w.dead)
        if fn == "getdisabled":
            return float(who in w.disabled)
        if fn == "getlocked":
            return float(w.locked.get(who, 0) > 0)
        if fn == "getitemcount":
            return float(w.items.get((key, rest[0].upper()), 0))
        if fn == "getplayerteammate":
            return float(who in w.team)
        if fn == "getav":
            return float(w.health.get(who, 200.0))
        if fn == "getnumrefs":
            return float(w.spawned.get((who, rest[0].upper()), 0))
        if fn == "getdistance":
            a = w.where(key, i)
            b = w.where("player%d" % i if rest[0] == "player" else rest[0].upper(), i)
            return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)
        raise RuntimeError("fake cannot eval %r" % expr)

    # -- bridge calls
    def call(self, method, params=None):
        w, i = self.w, self.i
        params = params or {}
        self._log("call", method)
        if method == "test.quest.reset":
            for q in params["quests"]:
                w.stage.pop(q.upper(), None)
                w.stage_done.pop(q.upper(), None)
            return {"reset": len(params["quests"])}
        if method == "test.quest.setvar":
            return {"ok": True}
        if method == "bot.vats":
            t = params["ref"].upper()
            w.health[t] = w.health.get(t, 200.0) - 40
            return {"ok": True}
        if method == "test.fasttravel":
            for p in range(w.nplayers):
                w.pos["player%d" % p] = w.where(params["marker"].upper(), p)
            return {"ok": True}
        if method == "bot.say":      # sunny conversation: the end script sets VCG02 to 25
            w.stage[w.flags["vcg02"]] = 25
            w.stage_done.setdefault(w.flags["vcg02"], set()).add(25)
            return {"said": True}
        if method == "bot.menu.press":
            w.pending_confirm = params["button"] == 1
            return {"pressed": True}
        if method == "bot.consent":
            if getattr(w, "pending_confirm", False) and params["answer"] == "ready":
                w.stage[w.flags["vmq05"]] = 100
            return {"answered": True}
        if method == "dev.countRefs":
            return {"count": sum(n for (m, b), n in w.spawned.items() if b == params["base"].upper())}
        if method == "coop.owner":
            return {"owner": w.owner, "epoch": 1}
        if method == "coop.leave":
            w.left.add(i)
            return {"ok": True}
        if method == "coop.join":
            w.left.discard(i)
            return {"ok": True}
        raise RuntimeError("fake has no method %s" % method)

    def wait_stage(self, q, stage, timeout_ms=0):
        return {"satisfied": True}

    def load_save(self, name, timeout_ms=0):
        self._log("load", name)
        self.w.loads.append((self.i, name))
        self.w.reset_world()
        snap = self.w.saves.get(name)
        if snap:
            self.w.stage, self.w.stage_done = dict(snap[0]), {k: set(v) for k, v in snap[1].items()}
        return {"loaded": True}

    def save_game(self, name, timeout_ms=0, overwrite=False):
        self._log("save", name)
        self.w.saves[name] = (dict(self.w.stage), {k: set(v) for k, v in self.w.stage_done.items()})
        return {"saved": True}


def make(n=2, **flags):
    w = World(**flags)
    clock = Clock()
    return w, clock, [FakeNv(w, i, clock) for i in range(n)]
