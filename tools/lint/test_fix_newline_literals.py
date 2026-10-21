"""Tests for fix_newline_literals.py. Prints ALL OK. The twin (FNVCOOP_MUT=lint_no_join) must make it fail."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fix_newline_literals as L  # noqa: E402

NL = "\\" + "n"  # the two characters backslash, n


def check(name, src, want, want_lines):
    got, lines = L.repair(src)
    if got != want or lines != want_lines:
        print(f"FAIL {name}\n  got   {got!r} {lines}\n  want  {want!r} {want_lines}")
        return False
    return True


def run():
    ok = True
    # 1. one split literal (the C2001 case agents keep producing)
    src = '    std::printf("seed %d: %s\n", a, b);\nint x;\n'
    want = '    std::printf("seed %d: %s' + NL + '", a, b);\nint x;\n'
    ok &= check("split once", src, want, [1])
    # 2. a literal split twice
    src = 'puts("a\nb\nc");\n'
    want = 'puts("a' + NL + 'b' + NL + 'c");\n'
    ok &= check("split twice", src, want, [1, 2])
    # 3. CRLF file keeps its line endings on untouched lines
    src = 'int a;\r\nputs("x\r\n");\r\nint b;\r\n'
    ok &= check("crlf", src, 'int a;\r\nputs("x' + NL + '");\r\nint b;\r\n', [2])
    # 4. things that must NOT be touched
    clean = [
        'puts("fine' + NL + '");\n',
        '// a comment with a " quote\nint y;\n',
        '/* block " comment\n still */ int z;\n',
        'auto r = R"(raw\nstring)";\n',
        "char q = '\"';\nint w;\n",
        "int big = 1'000'000;\nint v;\n",
        '#define M "a" \\\n  "b"\n',
        'puts("escaped quote \\" still");\n',
    ]
    for i, c in enumerate(clean):
        got, lines = L.repair(c)
        if lines or got != c:
            print(f"FAIL clean case {i}: {c!r} -> {got!r} {lines}")
            ok = False
    # 5. --check on a temp file exits 1 and does not modify; fix mode repairs it
    tmpdir = os.path.join(HERE, "..", "..", "out", "lint", "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    p = os.path.join(tmpdir, "broken.cpp")
    with open(p, "w", newline="") as fh:
        fh.write('void f() { std::printf("%d\n", 1); }\n')
    script = os.path.join(HERE, "fix_newline_literals.py")
    env = dict(os.environ)
    r = subprocess.run([sys.executable, "-I", script, "--check", p], capture_output=True, text=True, env=env)
    if r.returncode != 1:
        print(f"FAIL --check exit {r.returncode}: {r.stdout}")
        ok = False
    r = subprocess.run([sys.executable, "-I", script, p], capture_output=True, text=True, env=env)
    with open(p, newline="") as fh:
        fixed = fh.read()
    if fixed != 'void f() { std::printf("%d' + NL + '", 1); }\n':
        print(f"FAIL fix mode wrote {fixed!r}")
        ok = False
    # 6. check_py_compiles: a Python file with a split string fails, a good one passes
    pyc = os.path.join(HERE, "check_py_compiles.py")
    badpy = os.path.join(tmpdir, "broken_mod.py")
    goodpy = os.path.join(tmpdir, "good_mod.py")
    with open(badpy, "w", newline="") as fh:
        fh.write('import re\nRX = re.compile(r"^a[^\n]*\n", re.M)\n')
    with open(goodpy, "w", newline="") as fh:
        fh.write('import re\nRX = re.compile(r"^a[^' + NL + ']*' + NL + '", re.M)\n')
    r = subprocess.run([sys.executable, "-I", pyc, badpy], capture_output=True, text=True, env=env)
    if r.returncode != 1 or "broken_mod.py" not in r.stdout:
        print(f"FAIL check_py_compiles missed a broken file: {r.returncode} {r.stdout}")
        ok = False
    r = subprocess.run([sys.executable, "-I", pyc, goodpy], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"FAIL check_py_compiles rejected a good file: {r.stdout}")
        ok = False
    return ok


if __name__ == "__main__":
    ok = run()
    if "--twin" in sys.argv:
        # run the suite with the fault on: it must fail
        env = dict(os.environ, FNVCOOP_MUT="lint_no_join")
        r = subprocess.run([sys.executable, "-I", os.path.abspath(__file__)], capture_output=True, text=True, env=env)
        if r.returncode == 0:
            print("FAIL twin lint_no_join was not caught")
            ok = False
        else:
            print("twin lint_no_join caught")
    print("ALL OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)
