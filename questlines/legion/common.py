"""Shared loaders + scope + script tokenizer for the Legion questline lane (game-free, read-only on out/questdata).

Everything here is derived from out/questdata; nothing is invented. `Data` is the single entry point.
"""
import collections
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
QD = os.path.join(ROOT, 'out', 'questdata')
sys.path.insert(0, os.path.join(ROOT, 'tools', 'questdata'))
import classify  # noqa: E402  (reuse: strip_comment, dep_scripts, load_extra)
from lexicon import L  # noqa: E402

SLUG = 'legion'
PREFIX = {'FalloutNV.esm': '00', 'DeadMoney.esm': '01', 'HonestHearts.esm': '02', 'OldWorldBlues.esm': '03',
          'LonesomeRoad.esm': '04', 'GunRunnersArsenal.esm': '09'}

# ---------------------------------------------------------------- scope
# role: core        = Legion main questline quests (stages swept)
#       battle      = Hoover Dam battle machinery shared by all endings that the Legion ending runs through
#       dialogue    = dialogue containers (0 stages) whose INFOs advance the Legion line / Fortification Hill
#       system      = reaction/fail/timer containers
#       prereq      = owned by another lane (Q-act2 / shared main), listed for prerequisites only; NOT swept here
SCOPE = [
    # core
    ('VMS35', 'core'), ('VHDLegionBattle', 'core'), ('VDeadSea', 'core'),
    ('VEFR01LegionGood2CaesarsFavor', 'core'), ('VEFR00LegionMix2CaesarsHire', 'core'),
    ('VEFR02LegionBad2CaesarsFoe', 'core'),
    # Hoover Dam machinery that the Legion ending executes through
    ('vHDBattleController', 'battle'), ('VLegateShowdown', 'battle'), ('VHDOliverRetreatTimer', 'battle'),
    ('VHDOliverDeathTimer', 'battle'),
    # dialogue containers
    ('VFreeformTheFort', 'dialogue'), ('VDialogueCaesar', 'dialogue'), ('vDialogueTheFortGeneric', 'dialogue'),
    ('vDialogueCaesarsLegionMilitary', 'dialogue'), ('vDialogueCaesarsLegionMilitaryCombat', 'dialogue'),
    ('vDialogueCaesarsLegionSlave', 'dialogue'), ('vDialogueCaesarsLegionCrucified', 'dialogue'),
    ('VDialogueReactivityLegion', 'dialogue'), ('DialogueLegionSniffer', 'dialogue'),
    ('VFreeformHooverDam', 'dialogue'), ('vDialogueHooverDam', 'dialogue'),
    # system
    ('VERLegionVets', 'system'), ('FortEquipmentConfiscationQuest', 'system'), ('vCCmovetothefort', 'system'),
    ('VMQCaesarFail', 'system'),
    # prerequisites owned elsewhere
    ('VMQ02', 'prereq'), ('VMQ03a', 'prereq'), ('VMQ03b', 'prereq'), ('VMQ05', 'prereq'),
    ('VHDHouseBattle', 'prereq'), ('VHDIndependentBattle', 'prereq'),
]
OWNER_OF_PREREQ = 'Q-act2 (shared main Act 2) / other ending lanes'
# stages of core/battle quests that the in-game sweep must NOT run, with the reason
SWEEP_EXCLUDE = {}

ALIASES = {  # short command names used in vanilla scripts -> full names
    'evp': 'evaluatepackage', 'imod': 'imagespacemodifier', 'sayto': 'sayto', 'kill': 'kill', 'moveto': 'movetomarker',
    'rewardxp': 'rewardxp', 'enable': 'enable', 'disable': 'disable', 'ssd': 'setstage', 'ssdone': 'setstage',
    'addscriptpackage': 'addscriptpackage', 'startconversation': 'startconversation',
    'setplayerteammate': 'setplayerteammate', 'setpcfame': 'setpcfame', 'sc': 'startcombat',
    'forceav': 'forceactorvalue', 'modav': 'modactorvalue', 'setav': 'setactorvalue', 'getav': 'getactorvalue',
    'damageav': 'damageactorvalue', 'restoreav': 'restoreactorvalue', 'isdead': 'getdead', 'rewardkarma': 'rewardkarma',
    'playerrewardkarma': 'rewardkarma', 'cios': 'closeinteriorormenu', 'setdestroyed': 'setdestroyed',
    'addreputationexact': 'addreputationexact', 'removeallitems': 'removeallitems', 'player.behemoth': 'behemoth',
}


def runtime_id(ref):
    """'FalloutNV.esm:1252D6' -> '001252D6' (runtime FormID with load-order prefix)."""
    plug, loc = ref.split(':')
    return '%s%06X' % (PREFIX[plug], int(loc, 16))


def local_hex(ref):
    return ref.split(':')[1].upper().zfill(6)


class Data:
    def __init__(self, qd=QD):
        self.qd = qd
        d = json.load(open(os.path.join(qd, 'quests.json'), encoding='utf-8'))
        self.quests = d['quests']
        self.globals = d['globals']
        self.factions = d.get('factions', [])
        self.by_edid = {q['edid']: q for q in self.quests if q.get('edid')}
        self.by_ref = {q['ref']: q for q in self.quests}
        self.glob_by_edid = {g['edid'].lower(): g for g in self.globals if g.get('edid')}
        self.glob_by_ref = {g['ref']: g for g in self.globals}
        self.infos = collections.defaultdict(list)
        self.info_by_ref = {}
        with gzip.open(os.path.join(qd, 'infos.jsonl.gz'), 'rb') as f:
            for ln in f:
                i = json.loads(ln)
                self.info_by_ref[i['ref']] = i
                if i.get('quest'):
                    self.infos[i['quest']].append(i)
        self.dials = {x['ref']: x for x in self._gz('dials.json.gz')}
        self.forms = self._gz('forms.json.gz')
        self.refnames = self._gz('refnames.json.gz')
        self.risk = json.load(open(os.path.join(qd, 'risk.json'), encoding='utf-8'))['quests']
        self.eff = self._gz('effects.json.gz')
        self.eff_native = self._gz('effects_native.json.gz')
        self.extra = classify.load_extra(qd)
        self.scpt_by_id = {id(v['script']): v for v in self.extra['scpt'].values() if 'script' in v}
        self.graph = json.load(open(os.path.join(ROOT, 'out', 'coverage', 'questgraph.json'), encoding='utf-8'))
        self.rules = json.load(open(os.path.join(ROOT, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))
        self.policies = json.load(open(os.path.join(ROOT, 'docs', 'quest-policies', 'policies.json'), encoding='utf-8'))
        self.loadorder = json.load(open(os.path.join(ROOT, 'tools', 'questdata', 'loadorder.json'), encoding='utf-8'))
        self.cmd_table = {k.lower() for k in json.load(open(os.path.join(ROOT, 'plugin', 'hooks', 'tools', 're',
                                                                          'command_table.json'), encoding='utf-8'))}

    def _gz(self, name):
        with gzip.open(os.path.join(self.qd, name), 'rt', encoding='utf-8') as f:
            return json.load(f)

    # ---- name resolution (every emitted id must pass through here)
    def resolve(self, ref):
        """ref 'Plugin.esm:HEX' -> dict(sig, edid, name) or None."""
        if ref in self.by_ref:
            q = self.by_ref[ref]
            return {'sig': 'QUST', 'edid': q['edid'], 'name': q.get('name')}
        if ref in self.glob_by_ref:
            g = self.glob_by_ref[ref]
            return {'sig': 'GLOB', 'edid': g.get('edid'), 'name': None}
        for src in (self.forms, self.refnames):
            v = src.get(ref)
            if v:
                return {'sig': v.get('sig'), 'edid': v.get('edid'), 'name': v.get('name')}
        if ref in self.dials:
            v = self.dials[ref]
            return {'sig': 'DIAL', 'edid': v.get('edid'), 'name': v.get('name')}
        if ref in self.info_by_ref:
            return {'sig': 'INFO', 'edid': None, 'name': None}
        return None

    def nm(self, ref):
        if not isinstance(ref, str):
            return ref
        r = self.resolve(ref)
        return (r['edid'] or r['name'] or ref) if r else ref

    def cond_text(self, c):
        return '%s(%s,%s)%s%s[%s]' % (c['func'], self.nm(c.get('p1')), self.nm(c.get('p2')), c['op'], c['value'],
                                       c.get('run_on'))


# ---------------------------------------------------------------- script collection
def scripts_of(data, q, with_dep=True):
    """All scripts of quest q -> list of dicts {label, kind, script_edid, source, line_offset}.
    label is the exact location prefix used in gaps ("<script edid or quest> <where>")."""
    out = []
    qe = q['edid']
    sd = q.get('script_data')
    if sd and sd.get('source'):
        out.append({'label': sd['edid'], 'kind': 'quest_script', 'script': sd['edid'], 'quest': qe, 'source': sd['source']})
    for s in sorted(q['stages'], key=lambda s: s['index']):
        for ei, e in enumerate(s['entries']):
            if e.get('script') and e['script'].get('source'):
                out.append({'label': '%s stage %d' % (qe, s['index']), 'kind': 'stage', 'script': None, 'quest': qe,
                            'stage': s['index'], 'source': e['script']['source']})
    for i in data.infos.get(q['ref'], []):
        dl = data.dials.get(i['topic'], {})
        for k in ('begin', 'end'):
            if i.get(k) and i[k].get('source'):
                out.append({'label': 'INFO %s %s' % (local_hex(i['ref']), k), 'kind': 'info_' + k, 'script': None,
                            'quest': qe, 'info': i['ref'], 'topic': dl.get('edid'), 'source': i[k]['source']})
    if with_dep:
        seen = set()
        for dr in q.get('dep_refs', ()):
            for label, sc in classify.dep_scripts(dr, data.extra):
                if id(sc) in seen:
                    continue
                seen.add(id(sc))
                ent = data.scpt_by_id.get(id(sc))
                edid = ent['edid'] if ent else None
                out.append({'label': edid or label, 'kind': 'dep_' + label.split(' ')[0], 'script': edid, 'quest': qe,
                            'dep_ref': dr, 'dep_label': label, 'source': sc.get('source') or ''})
    return out


# ---------------------------------------------------------------- tokenizer
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
DECLS = {'short', 'long', 'int', 'float', 'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname'}
FLOW = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'return', 'begin', 'end', 'foreach', 'loop', 'continue',
        'break', 'label', 'goto'}
PLAYER = {'player', 'playerref'}


def tokenize_script(src, known_quests, known_globals, cmd_table):
    """-> list of uses {line, cmd, role, subject, player, conditional, block, text}.
    role: stmt (statement command), cond (function inside if/elseif/while), expr (function inside a set expression),
          setvar (quest/ref variable write), setglobal (global write)."""
    uses = []
    depth = 0
    block = None
    for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
        line = classify.strip_comment(raw).strip()
        if not line:
            continue
        low = line.lower()
        toks = IDENT.findall(low)
        if not toks:
            continue
        t0 = toks[0]
        if t0 == 'begin' and len(toks) > 1:
            block = IDENT.findall(line)[1]
            continue
        if t0 == 'end' and len(toks) == 1:
            block = None
            continue
        if t0 in DECLS:
            continue

        def add(cmd, role, subject=None, player=False):
            uses.append({'line': n, 'cmd': ALIASES.get(cmd, cmd), 'role': role, 'subject': subject, 'player': player,
                         'conditional': depth > 0, 'block': block, 'text': line[:140]})

        def scan_expr(text, role, skip=()):
            ts = [(m.start(), m.group(0)) for m in IDENT.finditer(text)]
            for k, (pos, t) in enumerate(ts):
                if t in FLOW or t in DECLS or t in skip:
                    continue
                a = ALIASES.get(t, t)
                if a in cmd_table or t in L or a in L:
                    prev = text[:pos].rstrip()
                    subj = None
                    if prev.endswith('.'):
                        m = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\.$', prev)
                        subj = m.group(1) if m else None
                    rest = [x[1] for x in ts[k + 1:k + 4]]
                    pl = (subj in PLAYER) or any(x in PLAYER for x in rest[:1])
                    add(t, role, subj, pl)

        if t0 in ('if', 'elseif', 'while'):
            if t0 == 'elseif':
                pass
            else:
                depth += 1
            scan_expr(low[len(t0):], 'cond')
            continue
        if t0 in ('else',):
            continue
        if t0 in ('endif', 'endwhile', 'loop'):
            depth = max(0, depth - 1)
            continue
        if t0 == 'return':
            continue
        if t0 in ('set', 'let'):
            m = re.match(r'\s*(?:set|let)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s+to\s+(.*)', low)
            if m:
                lhs, expr = m.group(1), m.group(2)
                if '.' in lhs:
                    owner, var = lhs.split('.', 1)
                    add('set quest var' if owner in known_quests else 'set ref var', 'setvar', owner)
                elif lhs in known_globals:
                    add('set global', 'setglobal', None)
                else:
                    add('set local', 'setlocal', None)
                scan_expr(expr, 'expr')
            continue
        # statement command, optionally "subject.cmd args"
        m = re.match(r'\s*(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)', low)
        if not m:
            continue
        subj, cmd = m.group(1), m.group(2)
        pl = (subj in PLAYER)
        rest = IDENT.findall(low[m.end():])
        if rest and rest[0] in PLAYER:
            pl = True
        if subj and subj not in PLAYER and subj in known_quests and cmd in ('setstage', 'getstage'):
            pass
        add(cmd, 'stmt', subj, pl)
        # functions nested later in args (rare) are ignored on purpose: statement-level only
    return uses
