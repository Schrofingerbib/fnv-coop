"""Shared loaders for the Dead Money questline tools (read-only over out/questdata and the L3/L5 inputs).

Every script in the Dead Money plugin is turned into a `Script` record with a stable human label, so a gap can be
reported as "<label>:<line>". Labels:
  quest/object/effect scripts  -> the SCPT EditorID                       e.g. NVDLC01BombCollarQuestSCRIPT
  stage result scripts         -> "<quest EDID> stage <n>"                (stage scripts have no EditorID of their own)
  topic (INFO) scripts         -> "INFO <local id> begin|end"             (+ topic EDID in `topic`)
  terminal menu item scripts   -> "TERM <EDID> item <n>"
  package scripts              -> "PACK <EDID> <slot>"
Run helpers with `python -I`; this module fixes sys.path itself because -I drops the script directory.
"""
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
QD = os.path.join(REPO, 'out', 'questdata')
PLUGIN = 'DeadMoney.esm'
PREFIX = '01'                                   # load-order prefix (tools/questdata/loadorder.json index 1)
sys.path.insert(0, os.path.join(REPO, 'tools', 'questdata'))
import classify  # noqa: E402  (L3 tokenizer; reused, not rewritten)


def _gz(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


def runtime_id(ref):
    """'DeadMoney.esm:0139DF' -> '010139DF' (hex FormID the console accepts)."""
    plugin, local = ref.split(':')
    order = json.load(open(os.path.join(REPO, 'tools', 'questdata', 'loadorder.json'), encoding='utf-8'))['plugins']
    return '%02X%06X' % (order.index(plugin), int(local, 16))


class Data:
    """Everything the Dead Money tools read, loaded once."""

    def __init__(self):
        self.quests_all = json.load(open(os.path.join(QD, 'quests.json'), encoding='utf-8'))
        self.meta = self.quests_all['meta']
        self.quests = [q for q in self.quests_all['quests'] if q['plugin'] == PLUGIN]
        self.by_edid = {q['edid']: q for q in self.quests}
        self.by_ref = {q['ref']: q for q in self.quests}
        self.all_quests_by_ref = {q['ref']: q for q in self.quests_all['quests']}
        self.globals = self.quests_all['globals']
        self.forms = _gz('forms.json.gz')
        self.refnames = _gz('refnames.json.gz')
        self.scripts = _gz('scripts.json.gz')
        self.terms = _gz('terms.json.gz')
        self.packs = _gz('packs.json.gz')
        self.dials = {d['ref']: d for d in _gz('dials.json.gz')}
        self.risk = json.load(open(os.path.join(QD, 'risk.json'), encoding='utf-8'))['quests']
        self.effects = _gz('effects.json.gz')
        self.native = _gz('effects_native.json.gz')
        with gzip.open(os.path.join(QD, 'infos.jsonl.gz'), 'rt', encoding='utf-8') as f:
            self.infos = [json.loads(l) for l in f if l.strip()]
        self.dm_infos = [i for i in self.infos if i['plugin'] == PLUGIN]
        self._names = None
        self._edids = None

    # ---- names -------------------------------------------------------------------------------------------------
    def edid_of(self, ref):
        for src in (self.forms, self.refnames):
            f = src.get(ref)
            if f and f.get('edid'):
                return f['edid']
        q = self.all_quests_by_ref.get(ref)
        return q['edid'] if q else None

    def edid_index(self):
        """lower-cased EditorID -> ref, over every source a FormID/EditorID may legitimately come from."""
        if self._edids is None:
            idx = {}
            for src in (self.refnames, self.forms):
                for ref, f in src.items():
                    if f.get('edid'):
                        idx.setdefault(f['edid'].lower(), ref)
            for q in self.quests_all['quests']:
                if q.get('edid'):
                    idx.setdefault(q['edid'].lower(), q['ref'])
            for g in self.globals:
                if g.get('edid'):
                    idx.setdefault(g['edid'].lower(), g['ref'])
            for s in self.scripts:
                if s.get('edid'):
                    idx.setdefault(s['edid'].lower(), s['ref'])
            self._edids = idx
        return self._edids

    def known_ref(self, ref):
        return (ref in self.forms or ref in self.refnames or ref in self.all_quests_by_ref
                or any(g['ref'] == ref for g in self.globals))

    def name_of(self, ref):
        f = self.forms.get(ref) or self.refnames.get(ref) or {}
        return f.get('name')


class Script:
    __slots__ = ('label', 'kind', 'quest', 'source', 'refs', 'topic', 'ref', 'block_hint')

    def __init__(self, label, kind, source, quest=None, refs=None, topic=None, ref=None):
        self.label, self.kind, self.source, self.quest = label, kind, source, quest
        self.refs, self.topic, self.ref = refs or [], topic, ref


def universe(d):
    """All Dead Money scripts the questline can execute, labelled. Returns list[Script]."""
    out = []
    quest_script_refs = {q['script'] for q in d.quests if q.get('script')}
    for s in d.scripts:
        if s['plugin'] != PLUGIN:
            continue
        sc = s['script']
        if not sc.get('source'):
            continue
        kind = sc['type'] if sc['type'] in ('quest', 'object', 'effect') else 'object'
        out.append(Script(s['edid'], kind, sc['source'], refs=sc.get('refs'), ref=s['ref']))
    for q in d.quests:
        for st in q['stages']:
            for n, e in enumerate(st['entries']):
                sc = e.get('script')
                if sc and sc.get('source'):
                    out.append(Script('%s stage %d' % (q['edid'], st['index']) + ('' if n == 0 else '#%d' % n), 'stage',
                                      sc['source'], quest=q['edid'], refs=sc.get('refs')))
    for i in d.dm_infos:
        qd = d.all_quests_by_ref.get(i['quest'])
        dial = d.dials.get(i['topic']) or {}
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc.get('source'):
                out.append(Script('INFO %s %s' % (i['ref'].split(':')[1], k), 'info_' + k, sc['source'],
                                  quest=qd['edid'] if qd else None, refs=sc.get('refs'),
                                  topic=dial.get('edid'), ref=i['ref']))
    for t in d.terms:
        if t['ref'].startswith(PLUGIN):
            for n, it in enumerate(t['items']):
                sc = it.get('script')
                if sc and sc.get('source'):
                    out.append(Script('TERM %s item %d' % (t.get('edid') or t['ref'], n), 'term', sc['source'],
                                      refs=sc.get('refs'), ref=t['ref']))
    for p in d.packs:
        if p['ref'].startswith(PLUGIN):
            for k, sc in (p.get('scripts') or {}).items():
                if sc and sc.get('source'):
                    out.append(Script('PACK %s %s' % (p.get('edid') or p['ref'], k), 'pack', sc['source'],
                                      refs=sc.get('refs'), ref=p['ref']))
    return out


# ---- function extraction -----------------------------------------------------------------------------------------
# Short aliases used by the Dead Money scripts (the engine command table lists long names only). `likely` aliases are
# inferred from the GECK short-name convention and the script context; they only affect which gap row a call lands in.
ALIASES = {
    'cios': 'CastImmediateOnSelf', 'rimod': 'RemoveImageSpaceModifier', 'imod': 'ApplyImageSpaceModifier',
    'sifh': 'SetIgnoreFriendlyHits', 'evp': 'EvaluatePackage', 'pms': 'PlayMagicShaderVisuals',
    'sms': 'StopMagicShaderVisuals', 'pcb': 'PurgeCellBuffers', 'kill': 'Kill', 'getlos': 'GetLineOfSight',
    'saa': 'SetActorAlpha', 'do': 'DamageObject', 'sa': 'SetAlert', 'pme': 'PlayMagicEffectVisuals',
    'sme': 'StopMagicEffectVisuals', 'moveto': 'MoveToMarker', 'modav': 'ModActorValue', 'setav': 'SetActorValue',
    'forceav': 'ForceActorValue', 'damageav': 'DamageActorValue', 'restoreav': 'RestoreActorValue',
    'getav': 'GetActorValue', 'getbaseav': 'GetBaseActorValue', 'unlock': 'UnLock',
}
LIKELY_ALIASES = {'saa', 'do'}
SKIP_WORDS = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'return', 'set', 'let', 'to', 'short', 'int', 'long',
              'float', 'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname', 'begin', 'end', 'foreach',
              'loop', 'continue', 'break', 'label', 'goto', 'and', 'or', 'not'}
TOKEN = classify.TOKEN


def command_names():
    """lower-cased name -> canonical name, from the engine command table (L5), the condition table (L3) and ALIASES."""
    table = json.load(open(os.path.join(REPO, 'plugin', 'hooks', 'tools', 're', 'command_table.json'), encoding='utf-8'))
    import ctda_funcs
    names = {k.lower(): k for k in table if k.strip()}
    for v in ctda_funcs.FUNCS.values():
        names.setdefault(v[0].lower(), v[0])
    for a, c in ALIASES.items():
        names[a] = c
    # a few xNVSE/JIP/base commands the Dead Money scripts use that are not in the vanilla table
    for x in ('IncrementScriptedChallenge', 'AddAchievement', 'OpenTeammateContainer', 'ForceRadioStationUpdate',
              'ResetPipboyManager', 'SetBroadcastState', 'StartRadioConversation', 'SetNPCRadio', 'AddCardToPlayer',
              'SetTalkingActivatorActor', 'SetPlayerCanTravel', 'SwapTexture', 'SCAOnActor'):
        names.setdefault(x.lower(), x)
    return names


def local_vars(src):
    out = set()
    for raw in src.splitlines():
        line = classify.strip_comment(raw).strip()
        toks = TOKEN.findall(line)
        if len(toks) >= 2 and toks[0].lower() in ('short', 'int', 'long', 'float', 'ref', 'reference', 'string_var'):
            out.add(toks[1].lower())
    return out


def function_uses(src, names):
    """Yield dicts {line, fn, canon, subject, block, text, conditional, in_cond} for every command/function token."""
    lv = local_vars(src)
    block = None
    depth = 0
    for n, raw in enumerate(src.splitlines(), 1):
        line = classify.strip_comment(raw).strip()
        if not line:
            continue
        toks = TOKEN.findall(line)
        low = [t.lower() for t in toks]
        if low[0] == 'begin' and len(low) > 1:
            block = toks[1]
            depth = 0
            continue
        if low[0] == 'end' and len(low) == 1:
            block = None
            continue
        if low[0] in ('short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname'):
            continue
        is_cond_line = low[0] in ('if', 'elseif', 'while')
        if low[0] in ('endif', 'endwhile', 'loop'):
            depth = max(0, depth - 1)
            continue
        conditional = depth > 0
        for i, t in enumerate(low):
            if t in SKIP_WORDS or t in lv or t not in names:
                continue
            prev = low[i - 1] if i else ''
            if low[0] == 'set' and i == 1:      # the assignment target is a variable, never a call
                continue
            subj = low[i - 2] if i >= 2 and prev == '.' else None
            yield {'line': n, 'fn': t, 'canon': names[t], 'subject': subj, 'block': block, 'text': line[:160],
                   'conditional': conditional or is_cond_line, 'in_cond': is_cond_line or (low[0] == 'set' and i >= 3)}
        if is_cond_line and low[0] in ('if', 'while'):
            depth += 1


def unknown_calls(src, names):
    """Statement-leading calls (`Ref.Func ...` or bare `Func ...`) whose function is in no table. Must stay empty."""
    lv = local_vars(src)
    out = []
    for n, raw in enumerate(src.splitlines(), 1):
        line = classify.strip_comment(raw).strip()
        if not line:
            continue
        toks = TOKEN.findall(line)
        low = [t.lower() for t in toks]
        if low[0] in SKIP_WORDS or low[0] in ('short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var'):
            continue
        if len(low) >= 3 and low[1] == '.':
            fn = low[2]
        elif len(low) >= 1 and low[0] not in names and (len(low) == 1 or low[1] != '.'):
            fn = low[0]
        else:
            continue
        if fn in names or fn in lv or not re.match(r'[a-z_]\w*$', fn):
            continue
        out.append((n, fn, line[:100]))
    return out
