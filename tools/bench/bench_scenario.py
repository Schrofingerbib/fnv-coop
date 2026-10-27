"""Benchmark scenarios: a JSON file lists phases, each phase type is a function registered here.

    {"name": "standard", "steps": [
        {"phase": "warmup", "secs": 15},
        {"phase": "idle", "secs": 30},
        {"phase": "walk", "who": "host", "course": "square", "secs": 45},
        {"phase": "walk", "who": "both", "course": "figure8", "secs": 30},
        {"phase": "area", "secs": 12},
        {"phase": "playbook", "name": "fight"},
        {"phase": "net", "profiles": ["lan", "bad_wifi", "hell"], "secs": 30} ]}

Add a phase type with `@register("name")`; it gets (ctx, step) and may put results in ctx.extras. A step may carry "label" to change the phase name
the sampler records (so per-phase metrics get their own row).
"""
import json, os, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MUTANT = os.environ.get("BENCH_MUTANT", "")   # test twins only
SCENARIO_DIR = HERE / "scenarios"
PHASES = {}


def register(name):
    def deco(fn):
        PHASES[name] = fn
        return fn
    return deco


class Ctx:
    """Everything a phase needs. `k` scales every duration (0.5 = quick run; tests use a tiny value)."""
    def __init__(self, samp, bots, arena, k=1.0, relay=None, outdir=None, log=print, walk_mode="step", phases_mod=None, arena_content=None, real_stage=None):
        self.samp, self.bots, self.arena, self.k = samp, bots, arena, k
        self.relay, self.outdir, self.log, self.walk_mode = relay, outdir, log, walk_mode
        self.phases = phases_mod        # tools/bench/phases.py (the game-facing helpers); None in unit tests
        self.real_stage = real_stage
        self.arena_content = arena_content   # tests pass a fake; None means the real tools/bench/arena_content.py
        self.walk = {"legs": 0, "legs_ok": 0, "why": {}}
        self.extras = {}

    def set_phase(self, name):
        self.samp.phase[0] = name

    def hold(self, secs):
        end = time.time() + secs * self.k
        while time.time() < end:
            time.sleep(min(0.05, max(0.0, end - time.time())))


def load(path_or_name):
    p = Path(path_or_name)
    if not p.is_file():
        p = SCENARIO_DIR / (str(path_or_name) if str(path_or_name).endswith(".json") else f"{path_or_name}.json")
    if not p.is_file():
        raise FileNotFoundError(f"scenario {path_or_name!r} not found (looked in {SCENARIO_DIR})")
    d = json.loads(p.read_text(encoding="utf-8"))
    validate(d)
    return d


def validate(d):
    if not isinstance(d.get("steps"), list) or not d["steps"]:
        raise ValueError("a scenario needs a non-empty 'steps' list")
    for i, s in enumerate(d["steps"]):
        if "phase" not in s:
            raise ValueError(f"step {i} has no 'phase'")
        if s["phase"] not in PHASES:
            raise ValueError(f"step {i}: unknown phase {s['phase']!r}; known: {', '.join(sorted(PHASES))}")


def run(ctx, scenario):
    """Run the steps in order. Returns ctx.extras. A phase that raises is recorded and the run goes on."""
    validate(scenario)
    for i, step in enumerate(scenario["steps"]):
        name = step.get("label") or step["phase"]
        ctx.set_phase(name)
        t0 = time.time()
        try:
            PHASES[step["phase"]](ctx, step)
        except Exception as e:   # noqa: BLE001
            if MUTANT == "stop_on_error":   # twin: the old behaviour, one failing phase ends the whole run
                raise
            ctx.extras.setdefault("phase_errors", []).append({"step": i, "phase": step["phase"], "error": str(e)[:160]})
            ctx.log(f"phase {name} failed: {e}")
        ctx.extras.setdefault("timeline", []).append({"step": i, "phase": name, "seconds": round(time.time() - t0, 2)})
    return ctx.extras


# ------------------------------------------------------------------ phase types
def _count_fail(ctx, why):
    ctx.walk["legs"] += 1
    why = str(why)[:70]
    ctx.walk["why"][why] = ctx.walk["why"].get(why, 0) + 1


def _walk_one(ctx, bot, course, deadline):
    """Walk the course until the deadline. Any error (the game still loading, a bridge hiccup) counts as a failed leg and the loop goes on:
    a walker thread must never die silently and leave the rest of the phase unmeasured."""
    pts, k = None, 0
    while time.time() < deadline:
        try:
            if pts is None:
                pts = ctx.arena.course(course, around=[float(v) for v in bot.pos()])
            px, py, pz = pts[k % len(pts)]
            r = bot.goto_pos(px, py, pz, timeout_s=max(2.0, min(25.0, deadline - time.time())), mode=ctx.walk_mode)
            ctx.walk["legs"] += 1
            ctx.walk["legs_ok"] += 1 if r.ok else 0
            if not r.ok:
                why = str(getattr(r, "reason", "") or "")[:70]
                ctx.walk["why"][why] = ctx.walk["why"].get(why, 0) + 1
            k += 1
        except Exception as e:   # noqa: BLE001
            if MUTANT == "walker_dies":   # twin: the old behaviour, the walker thread dies on the first error
                raise
            _count_fail(ctx, "error: " + str(e))
            time.sleep(0.5)


@register("warmup")
@register("idle")
@register("wait")
def _wait(ctx, step):
    ctx.hold(float(step.get("secs", 10)))


@register("walk")
def _walk(ctx, step):
    who = step.get("who", "both")
    course = step.get("course", "square")
    end = time.time() + float(step.get("secs", 30)) * ctx.k
    sel = {"host": [0], "client": [1], "both": [0, 1]}[who]
    ts = [threading.Thread(target=_walk_one, args=(ctx, ctx.bots[i], course, end), daemon=True) for i in sel]
    [t.start() for t in ts]
    [t.join(timeout=max(1.0, end - time.time() + 30)) for t in ts]
    ctx.hold(max(0.0, (end - time.time()) / max(ctx.k, 1e-9)))


@register("arena")
def _arena(ctx, step):
    ctx.extras["arena"] = ctx.phases.goto_arena(ctx.arena, ctx.bots, ctx.log)


@register("probe")
def _probe(ctx, step):
    ctx.extras["input_probe"] = ctx.phases.input_probe(ctx.bots, ctx.log)


@register("area")
def _area(ctx, step):
    ctx.extras["area"] = ctx.phases.area_change(ctx.samp, ctx.bots, ctx.k, ctx.log)


@register("playbook")
def _playbook(ctx, step):
    name = step["name"]
    label = step.get("label") or {"loot_race": "loot"}.get(name, name)
    ctx.extras[f"playbook_{label}"] = ctx.phases.run_playbook(name, Path(ctx.outdir) / f"pb_{label}", ctx.log)


@register("arena_fight")
def _arena_fight(ctx, step):
    """An enemy ring built at runtime in the arena; PASS/FAIL checks stored where the fight playbook's result goes (play.fight_pass_pct)."""
    ac = ctx.arena_content or __import__("arena_content")
    ctx.extras["playbook_fight"] = ac.arena_fight(ctx.bots, ctx.arena, ctx.log, n=int(step.get("enemies", ac.N_ENEMIES)))


@register("arena_loot")
def _arena_loot(ctx, step):
    ac = ctx.arena_content or __import__("arena_content")
    ctx.extras["playbook_loot"] = ac.arena_loot(ctx.bots, ctx.arena, ctx.log, n=int(step.get("items", ac.N_LOOT)))


@register("real_fight")
def _real_fight(ctx, step):
    """Fight on the four REAL placed actors at the Goodsprings gang spot (same ref id on every machine): numbers for kill latency and NPC position error."""
    rs = ctx.real_stage or __import__("real_stage")
    rs.stage(ctx.bots, ctx.log)
    ctx.extras["playbook_fight"] = rs.real_fight(ctx.bots, ctx.log)


@register("real_loot")
def _real_loot(ctx, step):
    """Both players take the corpse loot of the ganger killed in real_fight at the same moment."""
    rs = ctx.real_stage or __import__("real_stage")
    ctx.extras["playbook_loot"] = rs.real_loot(ctx.bots, ctx.log)


@register("net")
def _net(ctx, step):
    if ctx.relay is None:
        ctx.log("net phase skipped: no relay (run with --net)")
        ctx.extras.setdefault("skipped", []).append("net")
        return
    for prof in step.get("profiles", ctx.phases.NET_PROFILES if ctx.phases else ["lan"]):
        ctx.relay.profile(prof)
        ctx.set_phase(f"net_{prof}")
        _walk(ctx, {"who": "both", "course": step.get("course", "square"), "secs": step.get("secs", 35)})
    ctx.relay.profile("lan")
    ctx.set_phase("net_recover")
    ctx.hold(float(step.get("recover_secs", 25)))
