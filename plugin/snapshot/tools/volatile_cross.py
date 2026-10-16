#!/usr/bin/env python3
"""Generate the cross-check fixture: ONE volatile-keys file + queries with expectations computed by the Python
reference model. tests/volatile_cross_test.cpp loads the same file through L4's fnvcoop::VolatileKeys and must
give identical answers, so the probe's output, the dump filter and the session filter cannot disagree.

  volatile_cross.py DIR      writes DIR/keys.txt and DIR/queries.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import volatile_probe as vp  # noqa: E402

KEYS = """# every accepted spelling
var FalloutNV.esm:104C2A fStartTimer          # name, resolved by the engine
var falloutnv.esm 104c2b 7                    # index, 'Plugin HEX' two-token form
var DeadMoney.esm:000010 3
owner FalloutNV.esm:300000                    # every var of this owner
owner HonestHearts.esm 000042
global FalloutNV.esm:0A0001
global FalloutNV.esm 0A0002
field FalloutNV.esm:300001 running
field DeadMoney.esm:000020 stage
field FalloutNV.esm:300002 objectives
var FalloutNV.esm:104C99 fNeverResolved       # unknown name: stays pending
"""

NAMES = {("falloutnv.esm:104C2A", "fstarttimer"): 4,
         ("falloutnv.esm:104C2A", "fother"): 5}


def build():
    ref = vp.ReferenceKeys(KEYS, NAMES)
    refs = ["falloutnv.esm:104C2A", "falloutnv.esm:104C2B", "deadmoney.esm:000010", "falloutnv.esm:300000",
            "honesthearts.esm:000042", "falloutnv.esm:300001", "falloutnv.esm:104C99", "falloutnv.esm:777777"]
    q = []
    for n, (r, nm) in enumerate(sorted(NAMES)):
        q.append("name %s %s %d" % (r, nm, NAMES[(r, nm)]))
    for r in refs:
        for idx in (0, 3, 4, 5, 7):
            q.append("qvar %s %d %d" % (r, idx, ref.is_var(r, idx)))
    for r in refs + ["falloutnv.esm:0A0001", "falloutnv.esm:0A0002", "falloutnv.esm:0A0003"]:
        q.append("qglobal %s %d" % (r, ref.is_global(r)))
    for r in refs + ["deadmoney.esm:000020", "falloutnv.esm:300002"]:
        for f in vp.FIELDS:
            q.append("qfield %s %s %d" % (r, f, ref.is_field(r, f)))
    q.append("qsize %d" % ref.size())
    q.append("qunresolved %d" % ref.unresolved)
    return KEYS, "\n".join(q) + "\n"


def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    keys, queries = build()
    with open(os.path.join(d, "keys.txt"), "w", newline="\n") as f:
        f.write(keys)
    with open(os.path.join(d, "queries.txt"), "w", newline="\n") as f:
        f.write(queries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
