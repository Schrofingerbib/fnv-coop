"""Recompute capture/apply status of script functions from the CURRENT plugin + synccore source.

Static census tables (questlines/systems/census.py `M`, per-lane gaps.json) are hand-maintained and go stale. This
module reads the source instead:
  * Effect types      : members of `using Effect = std::variant<...>` in synccore/include/fnvcoop/effect.h
  * captured(T)       : T is constructed in capture-side sources (plugin/hooks*/ plugin/coop plugin/dialogue; not the
                        JSON codec, not tests)
  * applied(T)        : `std::get_if<T>` / `holds_alternative<T>` / `visit` overload on T in apply-side sources
A script function maps to Effect types through the census `effect` string ("AddItem/RemoveItem",
"ModActorValue(kAvExperience)"). Functions without an Effect type keep their static status unless a `Cmd_<name>`
hook is present in the sources (then at best 'partial').
"""
import glob
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
SRC_GLOBS = ['plugin/hooks*/*.cpp', 'plugin/hooks*/*.h', 'plugin/coop/*.cpp', 'plugin/coop/*.h',
             'plugin/dialogue/*.cpp', 'plugin/dialogue/*.h']
CODEC = ('effect_json.cpp', 'effect_json.h')
APPLY_FILES = re.compile(r'(apply|coop_module|coop_runtime|world_core|world_plan)', re.I)
# function-level truth the Effect-type evidence cannot express (several commands share one Effect type)
FN_EFFECT_OVERRIDE = {'setobjectvar': ['RefScriptVar']}       # object-script locals are RefScriptVar, not SetScriptVar
SPECIFIC_NEG = re.compile(r'not hooked \([^)]*only\)', re.I)    # "NOT hooked (Set/Force/Mod/Damage only)"
STALE_CLAIMS = ('not hooked', 'block_cmd', 'no engine apply', 'n/a', 'no capture', 'not captured', 'no apply',
                'type only', 'unknown')

SUP, PAR, MIS, LOC = 'supported', 'partial', 'missing', 'local'


def effect_types(root=ROOT, text=None):
    """Names inside `using Effect = std::variant<...>;`"""
    if text is None:
        p = os.path.join(root, 'synccore', 'include', 'fnvcoop', 'effect.h')
        text = open(p, encoding='utf-8', errors='replace').read() if os.path.exists(p) else ''
    m = re.search(r'using\s+Effect\s*=\s*std::variant<([^;]*)>\s*;', text, re.S)
    if not m:
        return []
    return [t.strip() for t in m.group(1).replace('\n', ' ').split(',') if t.strip()]


def source_files(root=ROOT):
    files = []
    for g in SRC_GLOBS:
        files += glob.glob(os.path.join(root, g))
    return [f for f in sorted(set(files)) if os.path.basename(f) not in CODEC and '/tests/' not in f.replace('\\', '/')]


def scan_sources(root=ROOT, files=None, types=None):
    """-> {'captured': {T: [files]}, 'applied': {T: [files]}, 'cmd_hooks': set(lowercase names), 'files': n}"""
    types = types if types is not None else effect_types(root)
    files = files if files is not None else source_files(root)
    cap, app, cmds = {}, {}, set()
    for f in files:
        try:
            txt = open(f, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        base = os.path.basename(f)
        is_apply_file = bool(APPLY_FILES.search(base))
        for t in types:
            if re.search(r'(?:get_if|holds_alternative|get)\s*<\s*(?:fnvcoop::)?%s\s*>' % t, txt):
                app.setdefault(t, []).append(base)
            if not is_apply_file or 'record(' in txt:
                for ln in txt.splitlines():
                    if 'get_if' in ln or 'struct ' in ln or 'holds_alternative' in ln:
                        continue
                    if re.search(r'\b(?:fnvcoop::)?%s\s*\{' % t, ln) or re.search(r'record\(\s*(?:fnvcoop::)?%s\b' % t, ln):
                        if base not in cap.setdefault(t, []):
                            cap[t].append(base)
                        break
        for m in re.finditer(r'\bCmd_(\w+)', txt):
            cmds.add(m.group(1).lower())
    return {'captured': cap, 'applied': app, 'cmd_hooks': cmds, 'files': len(files), 'types': list(types)}


def types_in(effect_str, known):
    if not effect_str:
        return []
    toks = re.findall(r'[A-Za-z][A-Za-z0-9_]*', effect_str)
    return [t for t in dict.fromkeys(toks) if t in known]


def live_status(row, src):
    """row: census row (fn, effect, l5_capture, l5_apply, l5_status). -> dict(status, static, effect_types,
    captured, applied, changed, evidence)"""
    static = row.get('l5_status') or row.get('status') or MIS
    fn = row['fn'].lower()
    known = set(src['types'])
    ets = FN_EFFECT_OVERRIDE.get(fn) or types_in(row.get('effect'), known)
    if not ets:                                   # census has no Effect for it but the type may exist now (AddNote...)
        by_low = {t.lower(): t for t in known}
        if fn in by_low:
            ets = [by_low[fn]]
    cap_txt = (row.get('l5_capture') or '').lower()
    app_txt = (row.get('l5_apply') or '').lower()
    hooked = fn in src['cmd_hooks']
    out = {'static': static, 'effect_types': ets, 'captured': None, 'applied': None,
           'evidence': {'cmd_hook': hooked}}
    if static == LOC:
        out.update(status=LOC, changed=False)
        return out
    if not ets:
        status = PAR if (hooked and static == MIS) else static
        out.update(status=status, changed=status != static, captured=hooked or None)
        return out
    cap = all(t in src['captured'] for t in ets) or hooked
    if SPECIFIC_NEG.search(cap_txt) and not hooked:
        cap = False
    app = all(t in src['applied'] for t in ets)
    out['captured'], out['applied'] = cap, app
    out['evidence'].update(captured_in={t: src['captured'].get(t, []) for t in ets},
                           applied_in={t: src['applied'].get(t, []) for t in ets})
    stale = any(s in cap_txt or s in app_txt for s in STALE_CLAIMS)
    if cap and app:
        status = SUP if (static == SUP or stale) else PAR     # a real qualifier in the static text keeps it partial
    elif cap or app:
        status = PAR
    else:
        status = static if static in (PAR, MIS) else MIS
    out.update(status=status, changed=status != static)
    return out
