"""One command for the bot action layer: python -I tools/bots/run_tests.py  -> prints ALL OK on success."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    p = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", str(HERE / "tests"), "-v"],
                       capture_output=True, text=True, errors="replace", cwd=HERE.parent.parent)
    out = p.stdout + p.stderr
    print(out[-3000:] if p.returncode else out.strip().splitlines()[-4:].__str__())
    if p.returncode:
        print("FAILED")
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
