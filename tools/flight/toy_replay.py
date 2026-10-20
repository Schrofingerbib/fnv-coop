"""A tiny pure-Python stand-in for synccore's `fnvcoop_replay`, used only to test the shrinker and the contract.

It follows the same contract: argv[1] = fixture JSON; one JSON line on stdout; exit 0 converged, 1 diverged, 2 bad fixture.
Toy rules (bugs that need specific steps, so shrinking has something real to find):
  * a deliver whose datagram starts with 0xEE "arms" the target slot;
  * a later deliver starting with 0xFF to an armed slot is a stale echo: failure "stale echo applied on slot N";
  * an inject step with kind "dup" causes failure "duplicate actor on slot N";
  * the check only runs at a quiesce step, and with two failures present the stale echo is reported first.
"""
import json
import sys


def run(fx):
    if fx.get("version") != 1 or not isinstance(fx.get("steps"), list):
        raise ValueError("bad fixture")
    armed = set()
    stale = []
    dup = []
    run_steps = 0
    for st in fx["steps"]:
        op = st.get("op")
        run_steps += 1
        if op == "deliver":
            b = bytes.fromhex(st.get("hex", ""))
            if not b:
                continue
            to = st["to"]
            if b[0] == 0xEE:
                armed.add(to)
            elif b[0] == 0xFF and to in armed:
                stale.append(to)
        elif op == "inject" and st.get("kind") == "dup":
            dup.append(st.get("slot", 1))
        elif op == "quiesce":
            viol = ["stale echo applied on slot %d" % s for s in stale] + ["duplicate actor on slot %d" % s for s in dup]
            first = {"category": "actors", "key": "echo"} if viol else None
            return {"diverged": bool(viol), "violations": viol, "first": first, "stepsRun": run_steps}
    return {"diverged": False, "violations": [], "first": None, "stepsRun": run_steps}


def main():
    args = [a for a in sys.argv[1:] if a != "--replay"]
    simfuzz = "--replay" in sys.argv    # mimics `fnvcoop_simfuzz --replay scenario.json`: ops list, VIOLATION line
    try:
        with open(args[0]) as f:
            fx = json.load(f)
        if simfuzz:
            fx = {"version": 1, "steps": fx["ops"]}
        res = run(fx)
    except (OSError, ValueError, KeyError, IndexError):
        print(json.dumps({"error": "bad fixture"}))
        return 2
    if simfuzz:
        for v in res["violations"]:
            print("VIOLATION " + v.replace("stale echo applied on", "stale_echo_applied").replace("duplicate actor on", "duplicate_actor"))
        return 1 if res["diverged"] else 0
    print(json.dumps(res))
    return 1 if res["diverged"] else 0


if __name__ == "__main__":
    sys.exit(main())
