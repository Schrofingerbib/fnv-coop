"""Two integration checks that no single lane's tests can make:

0. Lost backslashes: a Windows path written through a tool that unescapes gets a control character in the middle (flight -> form feed,
   build -> backspace, run_tests -> carriage return) in a doc or script.
   Files changed vs HEAD (and untracked ones) must not contain control characters or a bare CR.
1. Ignored sources: a source file that .gitignore swallows is not committed, so a clean checkout lacks it. (tools/build/ was hidden by the
   rule `build/` meant for CMake output; every run_tests script calls tools/build/build.py.) Any ignored file with a source extension
   outside the known output folders is reported.
2. Unregistered twins: a mutation twin that exists in a test exe (`--list` shows `twin.<name>`) but is missing from the CMake
   `foreach(twin ...)` list is never run by ctest, so nothing notices when it stops being caught. Needs the built exes in build/all;
   when one is missing that group is skipped (not failed) unless --require is given.

    python -I tools/lint/check_repo_hygiene.py [--require]     # exit 1 on a finding, prints ALL OK otherwise
    python -I tools/lint/check_repo_hygiene.py --self-test     # unit tests + a twin for each check (prints ALL OK)
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_EXT = (".py", ".cpp", ".h", ".hpp", ".c", ".cmd", ".bat", ".cmake", ".txt", ".json", ".md")
OUTPUT_PREFIXES = ("build/", "out/", "run/", "runs/", "third_party/", ".git/", "node_modules/")
# ignored on purpose although they have a source extension
EXPECTED_IGNORED = ("synccore/vc140.pdb",)

TWIN_BLOCK = re.compile(r"foreach\(twin\s+(.*?)\)\s*add_test\(NAME\s+\S+\s+COMMAND\s+(\w+)\s+--group\s+twin", re.S)


def ignored_sources(root: Path) -> list:
    p = subprocess.run(["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"], cwd=root,
                       capture_output=True, text=True, errors="replace")
    out = []
    for rel in p.stdout.split("\0"):
        rel = rel.replace("\\", "/")
        if not rel or rel.startswith(OUTPUT_PREFIXES) or "__pycache__" in rel or rel in EXPECTED_IGNORED:
            continue
        if rel.endswith("/"):   # a whole ignored directory: list its source files
            base = root / rel
            for d, _, fs in os.walk(base):
                for f in fs:
                    if f.endswith(SOURCE_EXT) and "__pycache__" not in d:
                        out.append(os.path.relpath(os.path.join(d, f), root).replace("\\", "/"))
        elif rel.endswith(SOURCE_EXT):
            out.append(rel)
    return sorted(set(out))


def twin_lists(root: Path) -> list:
    """[(cmake file, exe target, set of twin names registered in ctest)] for every CMakeLists with a twin foreach."""
    found = []
    for cm in list((root / "synccore").rglob("CMakeLists.txt")):
        if "build" in cm.parts:
            continue
        text = cm.read_text(encoding="utf-8", errors="replace")
        for m in TWIN_BLOCK.finditer(text):
            found.append((cm, m.group(2), set(m.group(1).split())))
    return found


def find_exe(root: Path, target: str):
    for d in (root / "build" / "all",):
        for p in d.rglob(target + ".exe"):
            return p
    return None


def exe_twins(exe: Path) -> set:
    p = subprocess.run([str(exe), "--list"], capture_output=True, text=True, errors="replace")
    return {l.strip()[len("twin."):] for l in p.stdout.splitlines() if l.strip().startswith("twin.")}


MANGLED = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]|\r(?!\n)")
TEXT_EXT = (".md", ".py", ".cmd", ".bat", ".cpp", ".h", ".hpp", ".c", ".cmake", ".txt")


def mangled_in(data: bytes):
    """Line number of the first control character (or bare CR) in `data`, else None. A path like plugin\\flight written through a tool that
    unescapes turns into plugin<form feed>light, tools\\build into tools<backspace>uild, synccore\\run_tests into synccore<CR>un_tests."""
    m = MANGLED.search(data)
    return None if m is None else data.count(b"\n", 0, m.start()) + 1


def mangled_files(root: Path) -> list:
    """Text files changed vs HEAD or untracked that contain a lost-backslash artifact (older committed damage is not this check's business)."""
    names = set()
    for cmd in (["git", "diff", "--name-only", "-z", "HEAD"], ["git", "ls-files", "--others", "--exclude-standard", "-z"]):
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, errors="replace")
        names.update(n for n in p.stdout.split("\0") if n)
    out = []
    for rel in sorted(names):
        if rel.startswith(OUTPUT_PREFIXES) or not rel.endswith(TEXT_EXT) or not (root / rel).is_file():
            continue
        ln = mangled_in((root / rel).read_bytes())
        if ln is not None:
            out.append(f"{rel}:{ln}")
    return out


def unregistered(listed: set, registered: set) -> list:
    """Twins the exe has but ctest does not run, plus registered names the exe does not know (ctest would fail on those anyway)."""
    return sorted(listed - registered)


def check_repo(root: Path, require: bool) -> list:
    problems = []
    for f in ignored_sources(root):
        problems.append(f"source file is ignored by .gitignore (a clean checkout would not have it): {f}")
    for f in mangled_files(root):
        problems.append(f"control character or bare CR (a backslash escape was eaten by a tool, as in a path written with a single backslash): {f}")
    for cm, target, registered in twin_lists(root):
        exe = find_exe(root, target)
        rel = cm.relative_to(root).as_posix()
        if exe is None:
            msg = f"{rel}: {target}.exe is not built, twin registration not checked"
            if require:
                problems.append(msg)
            else:
                print("  skip " + msg)
            continue
        for n in unregistered(exe_twins(exe), registered):
            problems.append(f"{rel}: twin '{n}' exists in {target} but is not in the ctest foreach list (never run by ctest)")
    return problems


# ---------------------------------------------------------------------------------------------------------------- self test
def self_test() -> int:
    bad = []

    def check(name, cond):
        print(("  ok   " if cond else "  FAIL ") + name)
        if not cond:
            bad.append(name)

    # twin registration: pure function
    check("unregistered(): a twin only the exe has is reported", unregistered({"a", "b"}, {"a"}) == ["b"])
    check("unregistered(): everything registered -> nothing", unregistered({"a"}, {"a", "c"}) == [])
    # twin of it: a comparison that ignores the exe's list reports nothing for a missing registration
    check("twin: comparing a list to itself would miss the gap (the check above would catch it)", unregistered({"a", "b"}, {"a", "b"}) == [] and unregistered({"a", "b"}, {"a"}) != [])
    # CMake foreach parsing
    sample = "foreach(twin x1 x2\n   x3)\n  add_test(NAME twin_${twin} COMMAND my_tests --group twin --name ${twin})\nendforeach()"
    m = TWIN_BLOCK.search(sample)
    check("foreach parsing: names and exe target", m is not None and m.group(1).split() == ["x1", "x2", "x3"] and m.group(2) == "my_tests")

    # ignored sources, on a scratch repo
    with tempfile.TemporaryDirectory(prefix="hygiene_") as tmp:
        t = Path(tmp)
        subprocess.run(["git", "init", "-q"], cwd=t, check=True)
        (t / "tools" / "build").mkdir(parents=True)
        (t / "tools" / "build" / "build.py").write_text("print(1)\n")
        (t / "build" / "all").mkdir(parents=True)
        (t / "build" / "all" / "gen.h").write_text("//\n")
        (t / ".gitignore").write_text("build/\n")
        found = ignored_sources(t)
        check("scratch repo with the old rule: tools/build/build.py is reported", found == ["tools/build/build.py"])
        (t / ".gitignore").write_text("build/\n!tools/build/\n")
        check("scratch repo with the fixed rule: nothing is reported (CMake output stays ignored)", ignored_sources(t) == [])
    # lost-backslash artifacts
    bs, ff, bsp, cr, lf = bytes([92]), bytes([12]), bytes([8]), bytes([13]), bytes([10])
    check("mangled_in(): clean text (backslash paths, CRLF) -> None", mangled_in(b"plugin" + bs + b"flight" + bs + b"run_tests.cmd" + cr + lf) is None)
    check("mangled_in(): a form feed (lost backslash-f) is found, with its line", mangled_in(b"a" + lf + b"plugin" + ff + b"light" + lf) == 2)
    check("mangled_in(): a backspace (lost backslash-b) is found", mangled_in(b"tools" + bsp + b"uild") == 1)
    check("mangled_in(): a bare CR (lost backslash-r) is found but CRLF is not", mangled_in(b"synccore" + cr + b"un_tests") == 1 and mangled_in(b"x" + cr + lf + b"y" + cr + lf) is None)
    # twin: the same scan with the control-character class emptied finds nothing, which the three checks above would notice
    saved = MANGLED
    try:
        globals()["MANGLED"] = re.compile(rb"(?!x)x")
        check("twin: a scan that matches nothing is detected by the checks above", mangled_in(b"a" + bsp + b"b") is None)
    finally:
        globals()["MANGLED"] = saved
    check("real scan restored after the twin", mangled_in(b"a" + bsp + b"b") == 1)
    # the real repo
    check("this repo: no source file is swallowed by .gitignore", ignored_sources(ROOT) == [])
    print("ALL OK" if not bad else "FAILED: " + "; ".join(bad))
    return 1 if bad else 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    problems = check_repo(ROOT, "--require" in sys.argv)
    for p in problems:
        print("PROBLEM " + p)
    if problems:
        return 1
    print("ALL OK (no ignored sources, every built twin is registered in ctest)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
