"""The failure packet: one markdown file under 3 KB that a fixer reads before anything else (docs/PROOF-PLAN.md section 2).

    build(ctx) -> text      fit(text) enforces the size limit; write(dir, ctx) saves packet.md next to the full log excerpts

ctx keys: verdict (the verdict dict), failure (failure.json dict or None), inspect (inspect_live list or None), inspect_problems (list),
moved (all moved counters), from_failure (one from_failure report or None), crash (dict or None), warn_lines ({instance: [lines]}), home (path to hide).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

LIMIT = 3000        # bytes, a bit under 3 KB so a reader's "under 3 KB" holds with the trailing newline
MUTANT = os.environ.get("PROOF_MUTANT", "")   # test-only: packet_oversize skips the limit
WARN_RE = re.compile(r"warn|error|fail|refus|mismatch|crash|exception|fault|unsupported|cannot|could not|rejected", re.I)


def warn_lines(text: str, n: int = 40) -> List[str]:
    return [l.rstrip() for l in text.splitlines() if WARN_RE.search(l)][-n:]


def scrub(s: str, home: str = "") -> str:
    """No local user paths in anything that could be pasted into a public place."""
    s = re.sub(r"(?i)[A-Z]:[/\\]Users[/\\][^/\\\s]+", "~", s)
    if home:
        s = s.replace(home, "~")
    return s


def _cut(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def sections(ctx: Dict[str, Any]) -> List[List[Any]]:
    """[[title, body, priority]] ; a lower priority number is kept first when the packet must shrink."""
    v = ctx["verdict"]
    out: List[List[Any]] = []
    head = [f"# {v['test']} run {v['run']}/{v['of']}: {v['verdict']} {v.get('class') or ''}".rstrip(),
            f"reason: {_cut(v.get('reason', ''), 200)}", f"check: {_cut(v.get('check') or '-', 80)}   repro: {v.get('repro') or '-'}",
            f"rerun: {v.get('rerun', '')}"]
    out.append(["", "\n".join(head), 0])
    f = ctx.get("failure")
    if f:
        lines = [f"playbook {f.get('playbook')} step {_cut(f.get('step', ''), 70)} kind {f.get('kind')}"]
        for m in f.get("machines", [])[:3]:
            st = m.get("status") or {}
            dg = (st.get("desyncs") or {}).get("digests") or {}
            lines.append(f"{m.get('name')}: role {m.get('role')} digests ok {dg.get('ok', '?')} mismatch {dg.get('mismatch', '?')} "
                         f"repairs {dg.get('repairs', '?')}")
        out.append(["failure.json", "\n".join(lines), 1])
    insp = ctx.get("inspect")
    probs = ctx.get("inspect_problems") or []
    if insp is not None or probs:
        body = "problems: " + ("; ".join(_cut(p, 90) for p in probs[:5]) if probs else "none")
        for r in (insp or [])[:3]:
            if not r.get("reachable", True):
                body += f"\ninstance {r.get('name')}: unreachable ({_cut(r.get('error', ''), 60)})"
        out.append(["inspect_live (before teardown)", body, 1])
    mv = ctx.get("moved") or {}
    if mv:
        top = sorted(mv.items(), key=lambda kv: -abs(kv[1]))[:12]
        out.append(["counters moved", ", ".join(f"{k} {d:+g}" for k, d in top), 1])
    ff = ctx.get("from_failure")
    if ff:
        body = [f"verdict {ff.get('verdict')}  check: {_cut(ff.get('agreement', ''), 100)}"]
        p = ff.get("primary")
        if p:
            body.append(f"{p.get('class')} {p.get('key')} at {p.get('at_ms')} ms, suspects {p.get('suspects')}")
        if ff.get("case"):
            body.append(f"shrunk case: {ff['case']}")
        out.append(["from_failure", "\n".join(body), 1])
    cr = ctx.get("crash")
    if cr:
        out.append(["crash", _cut(" ".join(f"{k}={v}" for k, v in cr.items()), 400), 0])
    wl = ctx.get("warn_lines") or {}
    for inst, lines in wl.items():
        body = "\n".join(_cut(l, 110) for l in lines[-40:])
        out.append([f"nvcoop.log WARN/ERR, instance {inst} (last {min(len(lines), 40)}; all in warn_inst{inst}.txt)", body, 5])
    return out


def render(secs: List[List[Any]]) -> str:
    parts = []
    for title, body, _p in secs:
        if body:
            parts.append((f"## {title}\n" if title else "") + body)
    return "\n\n".join(parts) + "\n"


def fit(secs: List[List[Any]], limit: int = LIMIT) -> str:
    """Render, then shrink: drop trailing log lines first, then whole low-priority sections, never the head."""
    text = render(secs)
    if MUTANT == "packet_oversize":
        return text
    guard = 0
    while len(text.encode("utf-8")) > limit and guard < 2000:
        guard += 1
        cand = [s for s in secs if s[2] > 0 and s[1]]
        if not cand:
            break
        s = max(cand, key=lambda x: (x[2], len(x[1])))
        ls = s[1].split("\n")
        if len(ls) > 1:
            s[1] = "\n".join(ls[1:]) if s[2] >= 5 else "\n".join(ls[:-1])      # logs: oldest line goes; others: newest detail goes
        else:
            s[1] = _cut(s[1], max(40, len(s[1]) // 2)) if len(s[1]) > 80 else ""
        text = render(secs)
    b = text.encode("utf-8")
    if len(b) > limit:                                   # last resort: hard cut at a character boundary
        text = b[: limit - 20].decode("utf-8", "ignore") + "\n[cut]\n"
    return text


def build(ctx: Dict[str, Any]) -> str:
    return scrub(fit(sections(ctx)), ctx.get("home", ""))


def write(dest: Path, ctx: Dict[str, Any]) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for inst, lines in (ctx.get("warn_lines") or {}).items():
        (dest / f"warn_inst{inst}.txt").write_text(scrub("\n".join(lines) + "\n", ctx.get("home", "")), encoding="utf-8")
    p = dest / "packet.md"
    p.write_text(build(ctx), encoding="utf-8")
    return p
