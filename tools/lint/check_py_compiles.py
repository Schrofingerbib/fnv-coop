"""Fail fast when any Python file in the repo does not even parse (e.g. a "\\n" in a string turned into a real line break).

Usage: python -I tools/lint/check_py_compiles.py [PATH ...]     (default: tools/ plugin/ synccore/)
Prints ALL OK, or one line per broken file and exits 1. Writes no .pyc files.
"""
import os
import sys

SKIP_DIRS = {"build", "out", "third_party", ".git", "node_modules", "__pycache__"}


def broken_files(paths):
    bad = []
    count = 0
    for top in paths:
        walk = [(os.path.dirname(top), [], [os.path.basename(top)])] if os.path.isfile(top) else os.walk(top)
        for d, dirs, files in walk:
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(d, f)
                count += 1
                try:
                    with open(p, "rb") as fh:
                        compile(fh.read(), p, "exec", dont_inherit=True)
                except SyntaxError as e:
                    bad.append(f"{p}:{e.lineno}: {e.msg}")
                except ValueError as e:  # null bytes
                    bad.append(f"{p}: {e}")
    return bad, count


def main(argv):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    paths = argv or [os.path.join(root, d) for d in ("tools", "plugin", "synccore")]
    bad, count = broken_files(paths)
    for b in bad:
        print(os.path.relpath(b, root) if b.startswith(root) else b)
    if bad:
        return 1
    print(f"ALL OK ({count} Python files parse)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
