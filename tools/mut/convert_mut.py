"""One-shot helper: rewrite compile-time mutation twins (#if MACRO == n ... #endif) into runtime ones (if (FNV_MUT(n)) { ... }).

    python -I tools/mut/convert_mut.py --macro NVWORLD_MUT file1.cpp file2.cpp ...

Rewrites:   #if M == n     ->  if (FNV_MUT(n)) {
            #if M != n     ->  if (!FNV_MUT(n)) {
            #else          ->  } else {        (only when it belongs to a rewritten #if)
            #endif         ->  }
Block bodies are indented one level. Anything else (#ifdef, #if _WIN32, nested non-mutation #if) is left alone. The result is
only valid where the old block held whole statements; blocks that declare a variable used later, case labels, class members or
initialisers must be fixed by hand (the compiler points at them). Prints one line per file so the diff can be reviewed.
"""
import re
import sys

NL = "\n"


def convert(text: str, macro: str):
    pat_if = re.compile(r"^(\s*)#\s*if\s+" + re.escape(macro) + r"\s*(==|!=)\s*(\d+)\s*(//.*)?$")
    lines = text.split(NL)
    out = []
    stack = []  # entries: None for any other conditional, else a dict describing a rewritten block
    n_changed = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = pat_if.match(line)
        if m:
            op, n, cm = m.group(2), m.group(3), m.group(4) or ""
            neg = "!" if op == "!=" else ""
            # indentation of the block = indentation of its first real body line
            ind = m.group(1)
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.lstrip().startswith("#"):
                    ind = re.match(r"\s*", nxt).group(0)
                    break
            out.append(f"{ind}if ({neg}FNV_MUT({n})) {{{(' ' + cm) if cm else ''}")
            stack.append({"ind": ind})
            n_changed += 1
            continue
        if re.match(r"#\s*(if|ifdef|ifndef)\b", stripped):
            stack.append(None)
            out.append(line)
            continue
        if re.match(r"#\s*(else|elif)\b", stripped):
            if stack and stack[-1]:
                if stripped.startswith("#elif"):
                    raise SystemExit("unsupported #elif on a mutation block: " + line)
                out.append(f"{stack[-1]['ind']}}} else {{")
            else:
                out.append(line)
            continue
        if re.match(r"#\s*endif\b", stripped):
            top = stack.pop() if stack else None
            if top:
                rest = stripped[len("#endif"):].strip()
                out.append(f"{top['ind']}}}{(' ' + rest) if rest else ''}")
            else:
                out.append(line)
            continue
        depth = sum(1 for e in stack if e)  # how many rewritten blocks enclose this line
        if depth and stripped and not stripped.startswith("#"):
            out.append("    " * depth + line)
        else:
            out.append(line)
    return NL.join(out), n_changed


def main():
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "--macro":
        print(__doc__)
        return 2
    macro = args[1]
    for path in args[2:]:
        with open(path, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", NL)
        new, n = convert(text, macro)
        if n:
            if crlf:
                new = new.replace(NL, "\r\n")
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
        print(f"{path}: {n} block(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
