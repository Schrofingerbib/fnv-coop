#!/usr/bin/env python3
"""The loot service must be in the live hub (sweep 2026-10-22: loot_proof "loot sync is enabled and in the session (saw (True, False))" on every copy).

plugin/loot/loot_glue.h promised "ONE LINE in live_glue.cpp" that nobody wrote, so the module said "enabled" while the live hub never
opened a session on it. The hub is also built before the loot module's DeferredInit, so the one line has two traps:
  * `hub.add(&nvcoop::loot::service(), nvcoop::loot::enabled())` evaluates its arguments in an unspecified order: enabled() may be read before
    service() has read the ini (false), so the service goes into the hub switched off;
  * service() must only CREATE the service (ini, engine, sync); installing the xNVSE handlers waits for DeferredInit, when the NVSE interface exists.

    python -I tools/lint/check_loot_wiring.py            # check the repo (exit 1 on a problem)
    python -I tools/lint/check_loot_wiring.py --self-test  # the checker's own tests and twins (prints ALL OK)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
GLUE = ROOT / "plugin" / "coop" / "live" / "live_glue.cpp"
MODULE = ROOT / "plugin" / "loot" / "loot_module.cpp"


def _code(src: str) -> str:
    return re.sub(r"//[^\n]*", "", src)


def glue_problems(src: str) -> List[str]:
    src = _code(src)
    blocks = [b for b in re.findall(r"#ifdef NVCOOP_WITH_LOOT\b(.*?)#endif", src, re.S) if "loot::service()" in b or "hub.add" in b]
    if not blocks:
        return ["live_glue.cpp has no #ifdef NVCOOP_WITH_LOOT block that registers the service: it is never added to the live hub"]
    body = blocks[0]
    bad: List[str] = []
    if "hub.add(" not in body:
        bad.append("the NVCOOP_WITH_LOOT block does not call hub.add(...)")
    s, e = body.find("loot::service()"), body.find("loot::enabled()")
    if s < 0 or e < 0:
        bad.append("the block must use nvcoop::loot::service() and nvcoop::loot::enabled()")
    elif s > e:
        bad.append("nvcoop::loot::service() must be called BEFORE nvcoop::loot::enabled() (enabled() is false until the service read the ini)")
    add = re.search(r"hub\.add\(([^;]*)\)\s*\)", body) or re.search(r"hub\.add\(([^;]*)\)", body)
    if add and "service()" in add.group(1) and "enabled()" in add.group(1):
        bad.append("service() and enabled() in one hub.add(...) call: the evaluation order of the arguments is unspecified")
    if '#include "loot/loot_glue.h"' not in src:
        bad.append('live_glue.cpp does not include "loot/loot_glue.h"')
    return bad


def module_problems(src: str) -> List[str]:
    src = _code(src)
    bad: List[str] = []
    m = re.search(r"LootSync& service\(\) \{(.*?)\n\}", src, re.S)
    if not m:
        return ["loot_module.cpp: LootSync& service() not found"]
    if re.search(r"\bInit\(\)", m.group(1)):
        bad.append("service() calls Init(): the handlers would be installed before the NVSE interface exists and DeferredInit would skip them; call Create()")
    if "Create()" not in m.group(1):
        bad.append("service() must create the service (Create()) when it is asked before DeferredInit")
    c = re.search(r"void Create\(\) \{(.*?)\n\}", src, re.S)
    if c and "InstallHandlers" in c.group(1):
        bad.append("Create() installs the xNVSE handlers: that belongs in Init() (DeferredInit)")
    return bad


def problems() -> List[str]:
    return glue_problems(GLUE.read_text(encoding="utf-8")) + module_problems(MODULE.read_text(encoding="utf-8"))


GOOD_GLUE = '''#include "loot/loot_glue.h"
#ifdef NVCOOP_WITH_LOOT
    {
        nvcoop::loot::LootSync& lootService = nvcoop::loot::service();
        const bool lootOn = nvcoop::loot::enabled();
        if (!g.hub.add(&lootService, lootOn)) nvcoop::Log("x");
    }
#endif
'''
GOOD_MODULE = '''void Create() {
    g_created = true;
}
LootSync& service() {
    Create();
    return *g_sync;
}
'''


def self_test() -> int:
    ok = True

    def expect(name: str, got: List[str], want_bad: bool) -> None:
        nonlocal ok
        if bool(got) != want_bad:
            print(f"FAIL {name}: {got}")
            ok = False

    expect("good glue", glue_problems(GOOD_GLUE), False)
    expect("good module", module_problems(GOOD_MODULE), False)
    # twins: each one is a real defect the checker must refuse
    expect("twin: no block", glue_problems(GOOD_GLUE.replace("NVCOOP_WITH_LOOT", "NVCOOP_WITH_OTHER")), True)
    expect("twin: no hub.add", glue_problems(GOOD_GLUE.replace("g.hub.add(", "g.hub.addx(")), True)
    expect("twin: enabled before service", glue_problems(GOOD_GLUE.replace(
        "nvcoop::loot::LootSync& lootService = nvcoop::loot::service();\n        const bool lootOn = nvcoop::loot::enabled();",
        "const bool lootOn = nvcoop::loot::enabled();\n        nvcoop::loot::LootSync& lootService = nvcoop::loot::service();")), True)
    expect("twin: one call, unspecified order", glue_problems(GOOD_GLUE.replace(
        "        nvcoop::loot::LootSync& lootService = nvcoop::loot::service();\n        const bool lootOn = nvcoop::loot::enabled();\n"
        "        if (!g.hub.add(&lootService, lootOn))", "        if (!g.hub.add(&nvcoop::loot::service(), nvcoop::loot::enabled()))")), True)
    expect("twin: no include", glue_problems(GOOD_GLUE.replace('#include "loot/loot_glue.h"\n', "")), True)
    expect("twin: service() runs Init", module_problems(GOOD_MODULE.replace("    Create();\n    return", "    Init();\n    return")), True)
    expect("twin: Create installs handlers", module_problems(GOOD_MODULE.replace("    g_created = true;", "    InstallHandlers();")), True)
    if ok:
        print("ALL OK")
        return 0
    return 1


def main(argv: List[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    bad = problems()
    for b in bad:
        print(b)
    print("loot wiring: OK" if not bad else f"loot wiring: {len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
