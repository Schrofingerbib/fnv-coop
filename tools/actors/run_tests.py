"""One command for tools/actors: the offline self-test of proof_actor_port.py, plus a twin of the proof itself (a proof that cannot fail must
be reported by its own self-test).   python -I tools/actors/run_tests.py   -> prints ALL OK
The same self-test + twins run for the two scripted hand-step replacements: proof_actor_port_two_copy.py (two-copy session) and
proof_actor_port_save_check.py (ACLG cosave save check). They share proof_actor_port.Result, so the "checks never fail" twin patches all three.
"""
import contextlib
import importlib.util
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load():
    spec = importlib.util.spec_from_file_location("proof_actor_port", HERE / "proof_actor_port.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["proof_actor_port"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_named(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def scripted_proof(name):
    """Self-test of a scripted in-game proof + its two script-level twins. proof_actor_port must already be in sys.modules (load())."""
    mod = load_named(name)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.selftest()
    out = buf.getvalue()
    if rc != 0 or "ALL OK" not in out:
        print(out)
        print(f"tools/actors: {name}: the self-test failed")
        return False
    print(out.strip().splitlines()[-1])
    base = sys.modules["proof_actor_port"]
    real_check = base.Result.check

    def always_pass(self, n, ok, detail=""):
        return real_check(self, n, True, detail)

    base.Result.check = always_pass
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.selftest()
    finally:
        base.Result.check = real_check
    if rc == 0:
        print(f"tools/actors: {name}: TWIN SURVIVED: a proof that cannot fail passed its own self-test")
        return False
    print(f"tools/actors: {name}: twin 'checks never fail' caught by the self-test")
    real_run = mod.Proof.run
    mod.Proof.run = lambda self: self.r
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.selftest()
    finally:
        mod.Proof.run = real_run
    if rc == 0:
        print(f"tools/actors: {name}: TWIN SURVIVED: a proof that checks nothing passed its own self-test")
        return False
    print(f"tools/actors: {name}: twin 'checks nothing' caught by the self-test")
    return True


def main() -> int:
    mod = load()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.selftest()
    out = buf.getvalue()
    if rc != 0 or "ALL OK" not in out:
        print(out)
        print("tools/actors: the proof's self-test failed")
        return 1
    print(out.strip().splitlines()[-1])

    # twin: a proof whose checks always pass must NOT survive its own self-test
    real_check = mod.Result.check

    def always_pass(self, name, ok, detail=""):
        return real_check(self, name, True, detail)

    mod.Result.check = always_pass
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.selftest()
    finally:
        mod.Result.check = real_check
    if rc == 0:
        print("tools/actors: TWIN SURVIVED: a proof that cannot fail passed its own self-test")
        return 1
    print("tools/actors: twin 'checks never fail' caught by the self-test")

    # twin: a fake that hides every fault must make the self-test's 'twin caught' bookkeeping fail
    real_run = mod.Proof.run

    def hollow_run(self):
        self.wiring()
        return self.r

    mod.Proof.run = hollow_run
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.selftest()
    finally:
        mod.Proof.run = real_run
    if rc == 0:
        print("tools/actors: TWIN SURVIVED: a proof that checks nothing passed its own self-test")
        return 1
    print("tools/actors: twin 'checks nothing' caught by the self-test")
    for fname in ("proof_actor_port_two_copy", "proof_actor_port_save_check"):
        if not scripted_proof(fname):
            return 1
    print("tools/actors: ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
