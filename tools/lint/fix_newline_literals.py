"""Find and repair C/C++ string literals that a real line break split in two.

Agents writing through a shell (or a tool that unescapes) turn "...\\n" into "...<newline>", which MSVC reports as
C2001 "newline in constant". This joins such a line with the next one and puts the two-character escape back.

Usage:
  python -I tools/lint/fix_newline_literals.py [--check] [--changed] [PATH ...]
    --check    report only, exit 1 if anything is broken (for test_all / pre-build)
    --changed  scan files changed vs git HEAD (plus untracked) instead of PATHs
  With no PATH and no --changed, scans synccore/ plugin/ tools/ .

Raw strings (R"(...)") and comments are skipped. Character literals are handled.
"""
import os
import subprocess
import sys

EXTS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".inl")
SKIP_DIRS = {"build", "out", "third_party", ".git", "node_modules"}

# Test hook: FNVCOOP_MUT=lint_no_join makes repair() report but not join (the twin test must notice).
MUT = os.environ.get("FNVCOOP_MUT", "")


def _line_ends_in_string(line, state):
    """Scan one line. state = (in_block_comment, open_raw_delimiter or None), carried across lines.
    Returns (ends_inside_plain_string, state_after)."""
    in_block_comment, raw = state
    i, n = 0, len(line)
    quote = None
    if raw:
        k = line.find(raw)
        if k < 0:
            return False, (in_block_comment, raw)   # still inside a multi-line raw string
        i = k + len(raw)
        raw = None
    while i < n:
        c = line[i]
        if in_block_comment:
            if line.startswith("*/", i):
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if line.startswith("//", i):
            return False, (False, None)
        if line.startswith("/*", i):
            in_block_comment = True
            i += 2
            continue
        if c == "R" and line.startswith('R"', i) and (i == 0 or not (line[i - 1].isalnum() or line[i - 1] == "_")):
            # raw string: R"delim( ... )delim" ; multi-line raw strings are legal and are skipped to their end
            j = line.find("(", i + 2)
            if j < 0:
                return False, (in_block_comment, None)
            delim = ")" + line[i + 2:j] + '"'
            k = line.find(delim, j + 1)
            if k < 0:
                return False, (in_block_comment, delim)
            i = k + len(delim)
            continue
        if c in "\"'":
            # a digit separator like 1'000 is not a char literal
            if c == "'" and i > 0 and line[i - 1].isalnum() and i + 1 < n and line[i + 1].isalnum():
                i += 1
                continue
            quote = c
        i += 1
    ends_in_string = quote == '"' and not line.endswith("\\")
    return ends_in_string, (in_block_comment, None)


def repair(text):
    """Return (fixed_text, list of 1-based line numbers that were split)."""
    lines = text.split("\n")
    out, broken = [], []
    state = (False, None)
    i = 0
    while i < len(lines):
        cur = lines[i]
        cr = cur.endswith("\r")
        body = cur[:-1] if cr else cur
        ends, state_next = _line_ends_in_string(body, state)
        if ends and i + 1 < len(lines):
            broken.append(i + 1)
            if MUT == "lint_no_join":
                out.append(cur)
                state = state_next
                i += 1
                continue
            nxt = lines[i + 1]
            joined = body + "\\n" + nxt
            lines[i + 1] = joined  # re-scan the joined line (a literal can be split more than once)
            i += 1
            continue
        out.append(cur)
        state = state_next
        i += 1
    return "\n".join(out), broken


def _changed_files(root):
    files = set()
    for args in (["git", "diff", "--name-only", "HEAD"], ["git", "ls-files", "--others", "--exclude-standard"]):
        try:
            res = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
        except OSError:
            continue
        files.update(l.strip() for l in res.stdout.splitlines() if l.strip())
    return [os.path.join(root, f) for f in sorted(files) if f.endswith(EXTS) and os.path.isfile(os.path.join(root, f))]


def _walk(paths):
    for p in paths:
        if os.path.isfile(p):
            if p.endswith(EXTS):
                yield p
            continue
        for d, dirs, fs in os.walk(p):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in fs:
                if f.endswith(EXTS):
                    yield os.path.join(d, f)


def main(argv):
    check = "--check" in argv
    changed = "--changed" in argv
    paths = [a for a in argv if not a.startswith("--")]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if changed:
        files = _changed_files(root)
    else:
        files = list(_walk(paths or [os.path.join(root, d) for d in ("synccore", "plugin", "tools")]))
    bad = 0
    for f in files:
        with open(f, "rb") as fh:
            raw = fh.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        fixed, broken = repair(text)
        if not broken:
            continue
        bad += len(broken)
        rel = os.path.relpath(f, root)
        for ln in broken:
            print(f"{rel}:{ln}: string literal split by a line break" + ("" if check else " (fixed)"))
        if not check and fixed != text:
            with open(f, "wb") as fh:
                fh.write(fixed.encode("utf-8"))
    if bad == 0:
        print(f"ALL OK ({len(files)} files)")
        return 0
    return 1 if check else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
