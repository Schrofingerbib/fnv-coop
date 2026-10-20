"""API for playbooks: load_scenario(name), apply(bridges, scenario), refs(scenario)."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import scenlib as _s  # noqa: E402
import runner as _r  # noqa: E402


def load_scenario(name):
    """name or path -> scenario dict (validated; raises ValueError with the error list)."""
    p = Path(name)
    p = p if p.suffix == ".json" else _s.SCEN_DIR / (name + ".json")
    sc = _s.load_scenario(p)
    errs = _s.validate(sc, name=p.stem)
    if errs:
        raise ValueError("%s invalid: %s" % (p.name, "; ".join(errs)))
    return sc


def refs(scenario):
    """alias -> {"ref", "edid", "sig", "hex"} (hex = runtime FormID for console use)."""
    res = _s.Resolver(scenario)
    return {a: dict(d, hex=res.hex("$" + a)) for a, d in scenario["forms"].items()}


def apply(bridges, scenario, store=None, keys=None, force_reload=False):
    """Jump the given bridges (host first) into the scenario's start state (reload only if needed). Returns the jump dict
    {reload, reasons, seconds, setup, reset}; reload=True means the save was loaded. Each of `keys` must name the game
    process behind its bridge (runner.StateStore.lease_keys(L) for a pool lease). Without keys, the state is private to
    this call, so a persistent `store` never lends it the state of an earlier game."""
    return _r.Runner(bridges, store, keys).jump(scenario, force_reload)


def run(bridges, scenario, **kw):
    return _r.Runner(bridges, kw.get("store"), kw.get("keys"), capabilities=kw.get("capabilities")).run(scenario)
