"""What L5 (plugin/hooks) and the coop layer actually capture and apply, read from the CURRENT source tree.

The hook and effect vocabularies are moving targets (L4/L5 added ObjectState, SpawnRef, RequestSave, SetPlayerControls capture ... while this lane was being
written), so gaps.json derives capture/apply status from the source instead of hard-coding it. Parsing is deliberately simple (regex over comment-stripped
C++) and is itself tested against synthetic snippets, with a mutation twin, in run_tests.py.

  HOOKS         names X of `HK(X, ...)` entries in engine_hooks.cpp kEntries (+ the guest-only Activate entry)
  PRODUCERS     Effect types that engine_hooks.cpp passes to `g_hub->record(...)` (directly or through a local variable of that type)
  BLOCK_ONLY    commands wrapped by BLOCK_CMD(X): dropped on guests, host passes through WITHOUT recording
  APPLY_ENGINE  Effect types with a branch in RemoteApplier::apply (apply_remote.cpp)
  APPLY_COOP    Effect types realized by the coop layer (coop_module.cpp EngineWorld::apply): session-level effects
  RULES_PRODUCERS Effect types the synccore rules engine constructs (rules.cpp): session-level effects, not script captures
  AMBIENT_RECORDED  True only if CaptureHub records mutations made outside a capture scope in the production build
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qlib  # noqa: E402

HOOKS_DIR = os.path.join(qlib.ROOT, 'plugin', 'hooks')
FILES = dict(engine=os.path.join(HOOKS_DIR, 'engine_hooks.cpp'), apply=os.path.join(HOOKS_DIR, 'apply_remote.cpp'),
             hub=os.path.join(HOOKS_DIR, 'capture_hub.cpp'), rules=os.path.join(qlib.ROOT, 'synccore', 'src', 'rules.cpp'), coop=os.path.join(qlib.ROOT, 'plugin', 'coop', 'coop_module.cpp'),
             effect_h=os.path.join(qlib.ROOT, 'synccore', 'include', 'fnvcoop', 'effect.h'))


def strip_cpp(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def effect_types(effect_h_text):
    m = re.search(r'using Effect = std::variant<([^>]*)>', effect_h_text, re.S)
    return [x.strip() for x in m.group(1).split(',')]


def parse(engine, apply_, hub, coop, etypes, rules=''):
    """Pure function over source texts (so the tests can feed synthetic snippets)."""
    engine, apply_, hub, coop, rules = (strip_cpp(t) for t in (engine, apply_, hub, coop, rules))
    hooks = set(re.findall(r'\bHK\(\s*([A-Za-z0-9_]+)\s*,', engine))
    hooks |= set(re.findall(r'\{\s*&addr::([A-Za-z0-9_]+)\s*,', engine))
    block_only = set(re.findall(r'\bBLOCK_CMD\(\s*([A-Za-z0-9_]+)\s*\)', engine))
    producers = set()
    for t in etypes:
        if re.search(rf'record\(\s*{t}\s*[{{(]', engine):
            producers.add(t)
            continue
        for var in re.findall(rf'\b{t}\s+(\w+)\s*(?:;|\{{|=)', engine):
            if re.search(rf'record\(\s*{var}\s*\)', engine):
                producers.add(t)
                break
    if re.search(r'recordScriptVar\(', engine) and 'SetScriptVar' in etypes:
        producers.add('SetScriptVar')   # quest-var diff goes through CaptureHub::recordScriptVar -> SetScriptVar
    hooks -= {'spec'}                   # macro parameter names in the #define lines
    block_only -= {'name'}
    apply_engine = {t for t in etypes if re.search(rf'get_if<\s*{t}\s*>', apply_)}
    apply_coop = {t for t in etypes if re.search(rf'get_if<\s*(?:fnvcoop::)?{t}\s*>', coop)}
    m = re.search(r'#else\s*constexpr bool kMutEmitPerScope\s*=\s*\w+;\s*constexpr bool kMutRecordAmbient\s*=\s*(\w+);', hub)
    ambient = (m.group(1) == 'true') if m else None
    rules_producers = {t for t in etypes if re.search(rf'(?<![A-Za-z0-9_]){t}\s*\{{', rules)}
    return dict(hooks=hooks, block_only=block_only, producers=producers, apply_engine=apply_engine, apply_coop=apply_coop,
                ambient_recorded=ambient, rules_producers=rules_producers)


def load():
    txt = {k: open(p, encoding='utf-8').read() for k, p in FILES.items()}
    et = effect_types(txt['effect_h'])
    f = parse(txt['engine'], txt['apply'], txt['hub'], txt['coop'], et, txt['rules'])
    f['effect_types'] = et
    return f
