"""Line-level scanner for GECK scripts: statement commands, condition reads, quest-var / global writes.

Output per script: list of Stmt dicts
  {line, text, kind, subject, fn, args, conditional, block}
kind: 'cmd'   a statement-level command (an effect or UI call), `Subject.Fn args` or `Fn args`
      'read'  a function used inside an if/elseif/while condition or on the right-hand side of `set`
      'setq'  `set Quest.var to ...` (quest/ref script variable write)
      'setg'  `set Global to ...` (not a local, not dotted)
      'setl'  `set localvar to ...`
`conditional` = inside if/elseif/else/while or after a conditional `return`.
"""
import re

TOKEN = re.compile(r'"[^"]*"|0[xX][0-9A-Fa-f]+|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\.|\S')
KEYWORDS = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'return', 'set', 'let', 'short', 'int', 'long', 'float',
            'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname', 'begin', 'end', 'foreach', 'loop',
            'continue', 'break', 'label', 'goto'}
DECL = {'short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname'}
LOGIC = {'and', 'or', 'not', 'to'}
PLAYER_SUBJECTS = {'player', 'playerref'}


def strip_comment(line):
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
        if ch == ';' and not q:
            break
        out.append(ch)
    return ''.join(out)


def _is_name(t):
    return bool(t) and (t[0].isalpha() or t[0] == '_')


def _reads_in(toks, n, text, cond, block, known_reads, out):
    """Collect read-function uses from an expression token list."""
    i = 0
    while i < len(toks):
        t = toks[i]
        tl = t.lower()
        subj = None
        if _is_name(t) and i + 2 < len(toks) and toks[i + 1] == '.' and _is_name(toks[i + 2]):
            subj, fn = t, toks[i + 2]
            i += 2
        else:
            fn = t
        fl = fn.lower()
        if _is_name(fn) and (fl in known_reads or (fl.startswith(('get', 'is', 'has')) and fl not in LOGIC)):
            args = []
            j = i + 1
            while j < len(toks) and len(args) < 4 and toks[j] not in ('(', ')', '&', '|', '=', '<', '>', '!') \
                    and toks[j].lower() not in LOGIC:
                if _is_name(toks[j]) or toks[j][0].isdigit() or toks[j].startswith('0x'):
                    args.append(toks[j])
                    j += 1
                else:
                    break
            out.append(dict(line=n, text=text[:160], kind='read', subject=subj, fn=fl, args=args, conditional=cond,
                            block=block))
        i += 1


def scan(src, known_reads=frozenset()):
    stmts = []
    depth = 0
    cond_return = False
    block = None
    for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
        text = strip_comment(raw).strip()
        if not text:
            continue
        toks = TOKEN.findall(text)
        if not toks:
            continue
        t0 = toks[0].lower()
        if t0 == 'begin' and len(toks) > 1:
            block = toks[1]
            depth = 0
            cond_return = False
            continue
        if t0 == 'end' and len(toks) == 1:
            block = None
            continue
        if t0 in DECL:
            continue
        if t0 in ('if', 'while'):
            _reads_in(toks[1:], n, text, True, block, known_reads, stmts)
            depth += 1
            continue
        if t0 == 'elseif':
            _reads_in(toks[1:], n, text, True, block, known_reads, stmts)
            continue
        if t0 in ('else',):
            continue
        if t0 in ('endif', 'endwhile', 'loop'):
            depth = max(0, depth - 1)
            continue
        if t0 == 'return':
            if depth > 0:
                cond_return = True
            continue
        if t0 in KEYWORDS and t0 not in ('set', 'let'):
            continue
        cond = depth > 0 or cond_return
        if t0 in ('set', 'let'):
            # set <lhs> to <expr>   (lhs may be Quest.var)
            if 'to' in [t.lower() for t in toks]:
                k = [t.lower() for t in toks].index('to')
                lhs = toks[1:k]
                expr = toks[k + 1:]
            else:
                lhs, expr = toks[1:2], toks[2:]
            if len(lhs) >= 3 and lhs[1] == '.':
                stmts.append(dict(line=n, text=text[:160], kind='setq', subject=lhs[0], fn=lhs[2].lower(),
                                  args=[' '.join(expr)[:60]], conditional=cond, block=block))
            elif lhs:
                stmts.append(dict(line=n, text=text[:160], kind='setl_or_g', subject=None, fn=lhs[0].lower(),
                                  args=[' '.join(expr)[:60]], conditional=cond, block=block))
            _reads_in(expr, n, text, cond, block, known_reads, stmts)
            continue
        # statement command: [Subject .] Fn args
        subj = None
        i = 0
        if _is_name(toks[0]) and len(toks) > 2 and toks[1] == '.' and _is_name(toks[2]):
            subj, i = toks[0], 2
        fn = toks[i]
        if not _is_name(fn):
            continue
        args = [t for t in toks[i + 1:] if t not in ('(', ')')][:6]
        stmts.append(dict(line=n, text=text[:160], kind='cmd', subject=subj, fn=fn.lower(), args=args, conditional=cond,
                          block=block))
        # reads on the same line (e.g. `player.additem x (getitemcount y)`) are rare; skip
    return stmts
