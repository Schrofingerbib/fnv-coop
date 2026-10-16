"""Shared loaders and the script scanner for the prologue questline (Q-prologue).

Everything here is read-only over out/questdata + out/coverage + docs/quest-policies. No ESM parsing: the
extracted JSON is the only source (FormIDs are never invented, see run_tests.py).
"""
import collections
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
QD = os.path.join(REPO, 'out', 'questdata')
sys.path.insert(0, os.path.join(REPO, 'tools', 'questdata'))
import classify  # noqa: E402
import ctda_funcs  # noqa: E402

COND_FUNCS = {v[0].lower() for v in ctda_funcs.FUNCS.values()}

# ---- scope ---------------------------------------------------------------------------------------------------
# role: core      = the prologue quest itself (VMQ01)
#       leg       = dialogue/support quests that drive VMQ01 (Primm, Nipton, Novac, Boulder City legs)
#       boundary  = start of the next questline; only the lead-in stages up to "reach Benny" are in scope
#       reference = already done by M1 Goodsprings (not swept again here)
#       gate      = only reads VMQ01 state; owned by another lane/companion work (not swept here)
SCOPE = collections.OrderedDict([
    ('VMQ01', 'core'),
    ('PrimmNashDialogue', 'leg'),
    ('nVPrimmDeputyConv', 'leg'),
    ('VES01aVictorInNovac', 'leg'),
    ('VDialogueNovac', 'leg'),
    ('VNipton', 'leg'),
    ('VFreeformBoulderCity', 'leg'),
    ('VMQTops', 'boundary'),
])
REFERENCE = ['VCG00', 'VCG01', 'VCG02', 'VCG03', 'VMS16', 'VFreeformGoodsprings']   # M1 Goodsprings, already done
GATE_ONLY = ['VDialogueBenny', 'VDialogueCraigBoone', 'VDialogueVeronica']
# VMQTops is the Ring-a-Ding-Ding quest (Act 2 shared line). Only the lead-in is ours: stage 5 (House points at Benny) and
# stage 10 (Benny is at the Tops). Everything from stage 15 on is the Benny/Tops scene and belongs to the Ring-a-Ding-Ding line.
BOUNDARY_STAGES = {'VMQTops': [5, 10]}
BOUNDARY_INFO_RE = re.compile(r'(?is)\b(setstage|setobjectivedisplayed|setobjectivecompleted)\s+vmqtops\s+(5|10)\b')

STATEMENT_SKIP = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'return', 'short', 'int', 'long', 'float', 'ref',
                  'reference', 'string_var', 'array_var', 'scn', 'scriptname', 'foreach', 'loop', 'continue', 'break',
                  'label', 'goto', 'begin', 'end'}
TOK = re.compile(r'"[^"]*"|[A-Za-z_][A-Za-z0-9_]*|0[xX][0-9A-Fa-f]+|\d+\.?\d*|\S')

_cache = {}


def _gz(name):
    p = os.path.join(QD, name)
    with gzip.open(p, 'rt', encoding='utf-8') as f:
        return json.load(f)


def data():
    if 'd' in _cache:
        return _cache['d']
    d, infos = classify.load(QD)
    quests = d['quests']
    by_edid = {q['edid']: q for q in quests if q.get('edid')}
    by_ref = {q['ref']: q for q in quests}
    by_formid = {q['formid']: q for q in quests}
    risk = json.load(open(os.path.join(QD, 'risk.json'), encoding='utf-8'))['quests']
    graph = json.load(open(os.path.join(REPO, 'out', 'coverage', 'questgraph.json'), encoding='utf-8'))
    pol_dir = os.path.join(REPO, 'docs', 'quest-policies')
    policies = {p['edid']: p for p in json.load(open(os.path.join(pol_dir, 'policies.json'), encoding='utf-8'))}
    rules = json.load(open(os.path.join(pol_dir, 'rules.json'), encoding='utf-8'))
    eff = _gz('effects.json.gz')
    native = _gz('effects_native.json.gz')
    forms = _gz('forms.json.gz')
    refnames = _gz('refnames.json.gz')
    loadorder = json.load(open(os.path.join(REPO, 'tools', 'questdata', 'loadorder.json'), encoding='utf-8'))
    out = dict(d=d, infos=infos, quests=quests, by_edid=by_edid, by_ref=by_ref, by_formid=by_formid, risk=risk,
               graph=graph, policies=policies, rules=rules, eff=eff, native=native, forms=forms, refnames=refnames,
               loadorder=loadorder, globals={g['edid']: g for g in d['globals'] if g.get('edid')},
               globals_l={g['edid'].lower(): g for g in d['globals'] if g.get('edid')})
    _cache['d'] = out
    return out


def hexid(ref):
    """'FalloutNV.esm:842DD' -> '000842DD' (runtime FormID using the shared load order)."""
    D = data()
    plugin, local = ref.rsplit(':', 1)
    idx = D['loadorder']['plugins'].index(plugin)
    return '%08X' % ((idx << 24) | int(local, 16))


def short_hex(ref):
    return '%X' % int(hexid(ref), 16)


# ---- script collection -----------------------------------------------------------------------------------------
def scope_quests():
    D = data()
    return [D['by_edid'][e] for e in SCOPE]


def in_scope_infos(edid):
    """INFOs of a scope quest that are in scope (all for core/leg; stage-5/10 lead-in only for the boundary quest)."""
    D = data()
    q = D['by_edid'][edid]
    out = []
    for i in D['infos'].get(q['ref'], []):
        if SCOPE[edid] == 'boundary':
            src = ' '.join((i.get(k) or {}).get('source', '') for k in ('begin', 'end'))
            if not BOUNDARY_INFO_RE.search(src):
                continue
        out.append(i)
    return out


def in_scope_stages(edid):
    q = data()['by_edid'][edid]
    if SCOPE[edid] == 'boundary':
        return [s for s in q['stages'] if s['index'] in BOUNDARY_STAGES[edid]]
    return list(q['stages'])


def collect_scripts():
    """-> list of {quest, role, kind, label, stage, info, block_src, refs} for every script in the questline."""
    out = []
    D = data()
    for edid, role in SCOPE.items():
        q = D['by_edid'][edid]
        sd = q.get('script_data')
        if sd and sd.get('source') and role != 'boundary':
            out.append(dict(quest=edid, role=role, kind='quest_script', label='quest_script', stage=None, info=None,
                            source=sd['source'], refs=sd.get('refs', []), script_edid=sd.get('edid')))
        for s in in_scope_stages(edid):
            for n, en in enumerate(s['entries']):
                sc = en.get('script')
                if sc and sc.get('source'):
                    out.append(dict(quest=edid, role=role, kind='stage', label='stage %d' % s['index'], stage=s['index'],
                                    info=None, source=sc['source'], refs=sc.get('refs', []), script_edid=None, entry=n))
        for i in in_scope_infos(edid):
            for k in ('begin', 'end'):
                sc = i.get(k)
                if sc and sc.get('source'):
                    out.append(dict(quest=edid, role=role, kind='info', label='info %s %s' % (i['ref'].split(':')[1], k),
                                    stage=None, info=i['ref'], source=sc['source'], refs=sc.get('refs', []),
                                    script_edid=None, topic=i.get('topic')))
    return out


# ---- driver object scripts ---------------------------------------------------------------------------------------
# Object/NPC/trigger scripts (not stage, quest or topic scripts) that WRITE the questline's quests. They are where a lot of
# the real story logic lives (looting Manny's note, the Tops trigger that completes VMQ01, Nipton captives dying).
DRIVER_NAMES = ['VMQ01', 'nVPrimmDeputyConv', 'VNipton', 'VES01aVictorInNovac', 'PrimmNashDialogue', 'VDialogueNovac',
                'VFreeformBoulderCity']
DRIVER_BOUNDARY_ALLOW = ['VMQTopsSecuritronMsgTrigASCRIPT', 'VMQTopsSecuritronMsgTrigBSCRIPT', 'VTopsBennyIntroTriggerSCRIPT']
# object scripts that match but belong to the Ring-a-Ding-Ding / Act 2 line (reason kept for inventory.json)
DRIVER_EXCLUDE = {'VPlatinumChipSCRIPT': 'picking up the Platinum Chip completes VMQTops (stage 80): Ring-a-Ding-Ding line, not the lead-in'}
DRIVER_WRITE_RE = re.compile(
    r'(?im)\b(setstage|setobjectivedisplayed|setobjectivecompleted|startquest|stopquest|completequest|failquest|'
    r'completeallobjectives)\s+(%s)\b|\bset\s+(%s)\.\w+\s+to' % ('|'.join(DRIVER_NAMES), '|'.join(DRIVER_NAMES)))


def driver_scripts():
    """-> list of script dicts (same shape as collect_scripts) for object scripts that write the questline's quests."""
    if 'drivers' in _cache:
        return _cache['drivers']
    out = []
    for sc in _gz('scripts.json.gz'):
        body = sc['script']
        if body.get('type') != 'object' or not body.get('source'):
            continue
        if sc['edid'] in DRIVER_EXCLUDE:
            continue
        if sc['edid'] in DRIVER_BOUNDARY_ALLOW or DRIVER_WRITE_RE.search(body['source']):
            out.append(dict(quest=None, role='driver', kind='object', label='object %s' % sc['edid'], stage=None, info=None,
                            source=body['source'], refs=body.get('refs', []), script_edid=sc['edid'], ref=sc['ref']))
    out.sort(key=lambda x: x['script_edid'])
    _cache['drivers'] = out
    return out


# ---- scanner ---------------------------------------------------------------------------------------------------
def scan_source(src, quest_names, global_names):
    """Yield one record per statement-level command or assignment.

    {line, text, kind: 'cmd'|'setvar'|'setglobal'|'setlocal', fn (lowercase), subject, args, conditional, block}
    Condition expressions (if/elseif) are scanned for calls as kind 'cond' with fn = the function names found.
    """
    recs = []
    depth = 0
    block = None
    for n, raw in enumerate(src.splitlines(), 1):
        line = classify.strip_comment(raw).strip().rstrip(';').strip()
        if not line:
            continue
        toks = TOK.findall(line)
        low = [t.lower() for t in toks]
        t0 = low[0]
        if t0 == 'begin' and len(low) > 1:
            block = toks[1]
            continue
        if t0 == 'end' and len(low) == 1:
            block = None
            continue
        if t0 in ('short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname'):
            continue
        if t0 in ('if', 'elseif', 'while'):
            if t0 != 'elseif':
                depth += 1
            fns = [low[i] for i in range(1, len(low)) if low[i] in COND_FUNCS]
            recs.append(dict(line=n, text=line[:140], kind='cond', fn=None, calls=fns, subject=None, args=low[1:],
                             conditional=True, block=block))
            continue
        if t0 in ('else',):
            continue
        if t0 in ('endif', 'endwhile', 'loop'):
            depth = max(0, depth - 1)
            continue
        if t0 == 'return':
            continue
        cond = depth > 0
        if t0 == 'set' and len(low) >= 4:
            lhs = toks[1]
            # tokens: set A . B to expr   or   set A to expr
            if len(low) >= 6 and low[2] == '.' and low[4] == 'to':
                recs.append(dict(line=n, text=line[:140], kind='setvar', fn='setquestvar', subject=lhs, args=[toks[3]] + toks[5:],
                                 var=toks[3], conditional=cond, block=block))
            elif low[2] == 'to':
                if low[1] in global_names:
                    recs.append(dict(line=n, text=line[:140], kind='setglobal', fn='setglobalvalue', subject=lhs, args=toks[3:],
                                     conditional=cond, block=block))
                else:
                    recs.append(dict(line=n, text=line[:140], kind='setlocal', fn='setlocal', subject=lhs, args=toks[3:],
                                     conditional=cond, block=block))
            continue
        if t0 in STATEMENT_SKIP:
            continue
        # [subject.]Command args
        subject, cmd, rest = None, toks[0], toks[1:]
        if len(toks) > 2 and toks[1] == '.':
            subject, cmd, rest = toks[0], toks[2], toks[3:]
        recs.append(dict(line=n, text=line[:140], kind='cmd', fn=cmd.lower(), fn_orig=cmd, subject=subject, args=rest,
                         conditional=cond, block=block))
    return recs


# ---- readable names / conditions --------------------------------------------------------------------------------
AV_NAMES = {5: 'Strength', 6: 'Perception', 7: 'Endurance', 8: 'Charisma', 9: 'Intelligence', 10: 'Agility', 11: 'Luck',
            24: 'XP', 32: 'Barter', 33: 'Big Guns', 34: 'Energy Weapons', 35: 'Explosives', 36: 'Lockpick', 37: 'Medicine',
            38: 'Melee Weapons', 39: 'Repair', 40: 'Science', 41: 'Guns', 42: 'Sneak', 43: 'Speech', 44: 'Survival',
            45: 'Unarmed'}
SKILL_AVS = {32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45}


def edid_of(ref):
    """FormRef -> EditorID using quests.json then forms.json.gz then refnames.json.gz (None when unknown)."""
    D = data()
    if ref in D['by_ref']:
        return D['by_ref'][ref].get('edid')
    for src in (D['forms'], D['refnames']):
        v = src.get(ref)
        if v:
            return v.get('edid')
    return None


def name_of(ref):
    D = data()
    v = D['forms'].get(ref)
    if v and v.get('name'):
        return v['name']
    if ref in D['by_ref']:
        return D['by_ref'][ref].get('name')
    return None


def cond_text(c):
    """One-line readable form of a CTDA condition entry."""
    fn = c['func']
    p1 = c.get('p1')
    if fn in ('GetActorValue', 'GetBaseActorValue') and isinstance(p1, int):
        p1 = AV_NAMES.get(p1, p1)
    elif isinstance(p1, str) and ':' in p1:
        p1 = edid_of(p1) or p1
    p2 = c.get('p2')
    args = [str(x) for x in (p1, p2) if x not in (None, 0, '')]
    run = '' if c.get('run_on') == 'subject' else ' [on %s]' % c.get('run_on')
    return '%s%s %s %s%s' % (fn, '(%s)' % ', '.join(args) if args else '', c['op'], c['value'], run)


# ---- every script in the game, labelled (used to find who writes a stage) -------------------------------------------------
def all_script_sources():
    """-> {label: source} for every quest/stage/INFO/object script of the whole game. Labels: `<QuestEDID> stage N`,
    `<QuestEDID> info <local id> begin|end`, `<QuestEDID> quest_script`, `object <ScriptEDID>`."""
    if 'allsrc' in _cache:
        return _cache['allsrc']
    D = data()
    out = {}
    for q in D['quests']:
        e = q.get('edid') or q['ref']
        sd = q.get('script_data')
        if sd and sd.get('source'):
            out['%s quest_script' % e] = sd['source']
        for s in q['stages']:
            for n, en in enumerate(s['entries']):
                sc = en.get('script')
                if sc and sc.get('source'):
                    out['%s stage %d' % (e, s['index']) + ('' if n == 0 else ' #%d' % n)] = sc['source']
        for i in D['infos'].get(q['ref'], []):
            for k in ('begin', 'end'):
                sc = i.get(k)
                if sc and sc.get('source'):
                    out['%s info %s %s' % (e, i['ref'].split(':')[1], k)] = sc['source']
    for sc in _gz('scripts.json.gz'):
        if sc['script'].get('type') == 'object' and sc['script'].get('source'):
            out['object %s' % sc['edid']] = sc['script']['source']
    _cache['allsrc'] = out
    return out
