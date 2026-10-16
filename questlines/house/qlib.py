"""Shared data layer for the Mr. House questline (slug `house`).

Reads the read-only quest data under out/questdata/ and reuses tools/questdata loaders (classify, loadorder, lexicon,
ctda_funcs). Nothing here touches the game install, the ESM files or the INIs.

Python 3.11, run as `python -I`: -I drops the script directory from sys.path, so every entry point inserts HERE itself.
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
TOOLS = os.path.join(ROOT, 'tools', 'questdata')
sys.path.insert(0, TOOLS)
import classify  # noqa: E402
import ctda_funcs  # noqa: E402
import lexicon  # noqa: E402
import loadorder  # noqa: E402

SLUG = 'house'

# ----------------------------------------------------------------------------------------------------------------
# Questline membership
# ----------------------------------------------------------------------------------------------------------------
# core: the Mr. House main questline proper (docs/quest-policies/main-house.md) + the Hoover Dam House battle. Every stage swept.
CORE = ['VMQHouse', 'VMQHouse1', 'VMQHouse2', 'VMQHouse3', 'VMQHouse4', 'VMQHouse5', 'VMQHouse6', 'VMQHouse7',
        'VMQHouse8', 'VMQHouseLockdown', 'VMQHouseFail', 'VHDHouseBattle']
# system: stage-bearing support quest owned here (every INFO result script of it advances a core quest).
SYSTEM = {
    'VDialogueMrHouse': 'Mr. House dialogue container: every stage of VMQHouse1..8 is advanced from its INFO result scripts',
}
# shared: infrastructure that the House path runs through but another lane owns (rules.json / the other lanes' scope files:
# Q-ncr owns vHDBattleController + the ending quests; Q-strip owns VDialogueLucky38Penthouse). Analysed here for gaps
# and hazards because the House ending cannot finish without it; stages are NOT swept here (see sweep.json `excluded`).
SHARED = {
    'vHDBattleController': 'Eureka! Hoover Dam battle controller (owner Q-ncr); VHDHouseBattle stage 10 sets its state, it sets VHDHouseBattle 50',
    'VEnding': 'Game Ending Narration (owner Q-ncr): slideshow driver, NarratorScript SayToDone chain + SwapTexture',
    'VEndingBinks': 'Vegas Ending Binks helper (systems/ending)',
    'VMQEndStates': 'World Changes Post-Endgame template (VStoryState 63 = House victory)',
    'VMQEndStatesTheStrip': 'End States for The Strip (owner Q-strip)',
    'VMQEndStatesFreeside': 'Freeside Post-Endgame Content Quest (owner Q-strip)',
    'VFreeformHooverDam': 'Hoover Dam conversations (owner Q-ncr): topic results set VHDHouseBattle 50/90/100',
    'vDialogueHooverDam': 'Hoover Dam generic dialogue (owner Q-ncr)',
    'VHDKimballSpeech': 'President Kimball speech (owner Q-ncr); VMQHouse6 objective 71 on cancel',
    'VHDAssassinationDialog': 'Kimball assassination dialogue (owner Q-ncr); VMQHouse6 objectives 70/72/74',
    'VDialogueLucky38Penthouse': 'Lucky 38 penthouse dialogue (owner Q-strip)',
    'VDialogueLucky38Entrance': 'Lucky 38 entrance dialogue (owner Q-act2); VMQHouse2 stage 100 and VMQHouseLockdown drive it',
    'VLegateShowdown': 'Legate showdown controller (owner Q-legion/Q-ncr); sets VHDHouseBattle 50 from its control script',
}
# prereq: upstream main-story quests owned by Q-act2 / Q-prologue. Used only to build `setup`; never swept here.
PREREQ = ['VMQ01', 'VMQTops', 'VMQ02', 'VMQ03a', 'VMQ03b', 'VMQ04']

PLAYER_NAMES = {'player', 'playerref'}


def role_of(edid):
    if edid in CORE:
        return 'core'
    if edid in SYSTEM:
        return 'system'
    if edid in SHARED:
        return 'shared'
    if edid in PREREQ:
        return 'prereq'
    return 'other'


# ----------------------------------------------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------------------------------------------
def _gz(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


class Data:
    """Everything read-only, loaded once."""

    def __init__(self):
        self.quests_doc = json.load(open(os.path.join(QD, 'quests.json'), encoding='utf-8'))
        self.quests = self.quests_doc['quests']
        self.q_by_edid = {q['edid']: q for q in self.quests if q.get('edid')}
        self.q_by_ref = {q['ref']: q for q in self.quests}
        self.forms = _gz('forms.json.gz')
        self.refnames = _gz('refnames.json.gz')
        self.scripts = _gz('scripts.json.gz')
        self.s_by_ref = {s['ref']: s for s in self.scripts}
        self.s_by_edid = {s['edid'].lower(): s for s in self.scripts if s.get('edid')}
        self.dials = _gz('dials.json.gz')
        self.d_by_ref = {d['ref']: d for d in self.dials}
        self.terms = _gz('terms.json.gz')
        self.packs = _gz('packs.json.gz')
        self.native = _gz('effects_native.json.gz')
        self.risk = json.load(open(os.path.join(QD, 'risk.json'), encoding='utf-8'))['quests']
        self.plugins = loadorder.load()
        self._infos = None
        self._sources = None
        self._edids = None

    # ---- infos
    @property
    def infos_by_quest(self):
        if self._infos is None:
            m = collections.defaultdict(list)
            with gzip.open(os.path.join(QD, 'infos.jsonl.gz'), 'rt', encoding='utf-8') as f:
                for ln in f:
                    i = json.loads(ln)
                    if i.get('quest'):
                        m[i['quest']].append(i)
            self._infos = m
        return self._infos

    # ---- name resolution. Sources in priority order: quests.json, forms.json.gz, refnames.json.gz
    def _build_names(self):
        by = {}      # lower edid -> (ref, source)
        refsrc = {}  # ref -> (edid, source, sig)
        d = self.quests_doc
        for q in d['quests']:
            if q.get('edid'):
                refsrc.setdefault(q['ref'], (q['edid'], 'quests.json', 'QUST'))
        for key, sig in (('globals', 'GLOB'), ('factions', 'FACT'), ('reputations', 'REPU')):
            for g in d.get(key, []):
                if g.get('edid'):
                    refsrc.setdefault(g['ref'], (g['edid'], 'quests.json', sig))
        for ref, f in self.forms.items():
            if f.get('edid'):
                refsrc.setdefault(ref, (f['edid'], 'forms.json.gz', f.get('sig')))
        for ref, f in self.refnames.items():
            if f.get('edid'):
                refsrc.setdefault(ref, (f['edid'], 'refnames.json.gz', f.get('sig')))
        for ref, dl in self.d_by_ref.items():
            if dl.get('edid'):
                refsrc.setdefault(ref, (dl['edid'], 'dials.json.gz', 'DIAL'))
        order = {'quests.json': 0, 'forms.json.gz': 1, 'refnames.json.gz': 2, 'dials.json.gz': 3}
        for ref, (e, src, sig) in sorted(refsrc.items(), key=lambda kv: order[kv[1][1]]):
            by.setdefault(e.lower(), (ref, src))
        self._edids = by
        self._refsrc = refsrc

    def edid_to_ref(self, edid):
        if self._edids is None:
            self._build_names()
        r = self._edids.get(edid.lower())
        return r[0] if r else None

    def edid_source(self, edid):
        if self._edids is None:
            self._build_names()
        r = self._edids.get(edid.lower())
        return r[1] if r else None

    def ref_info(self, ref):
        """(edid, source, sig) for a 'Plugin.esm:LOCALID' ref or None."""
        if self._edids is None:
            self._build_names()
        return self._refsrc.get(ref)

    def ref_edid(self, ref):
        i = self.ref_info(ref)
        return i[0] if i else None

    def resolves(self, ref):
        return self.ref_info(ref) is not None

    def hexid(self, edid_or_ref):
        """Console FormID (hex) for an EditorID or ref. Raises KeyError when it does not resolve."""
        ref = edid_or_ref if ':' in edid_or_ref else self.edid_to_ref(edid_or_ref)
        if not ref or not self.resolves(ref):
            raise KeyError(f'unresolved: {edid_or_ref}')
        return loadorder.console_id(ref, self.plugins)

    # ---- global values (initial)
    def global_by_edid(self, edid):
        for g in self.quests_doc['globals']:
            if g.get('edid', '').lower() == edid.lower():
                return g
        return None

    # ---- extra for classify.dep_scripts
    def classify_extra(self):
        return {'touched': _gz('effects.json.gz')['quest_touched'], 'forms': self.forms,
                'scpt': {x['ref']: x for x in self.scripts}, 'terms': {x['ref']: x for x in self.terms},
                'packs': {x['ref']: x for x in self.packs}}

    def known_quests(self):
        return frozenset(q['edid'] for q in self.quests if q.get('edid'))

    def known_globals(self):
        return frozenset(g['edid'] for g in self.quests_doc['globals'] if g.get('edid'))


_DATA = None


def data():
    global _DATA
    if _DATA is None:
        _DATA = Data()
    return _DATA


# ----------------------------------------------------------------------------------------------------------------
# Script vocabulary + scanner
# ----------------------------------------------------------------------------------------------------------------
TOKEN = re.compile(r'"[^"]*"|0[xX][0-9A-Fa-f]+|[A-Za-z_][A-Za-z0-9_]*|\.|\S')
KEYWORDS = {'if', 'elseif', 'else', 'endif', 'while', 'endwhile', 'begin', 'end', 'return', 'set', 'to', 'let', 'scn',
            'scriptname', 'short', 'long', 'int', 'float', 'ref', 'reference', 'array_var', 'string_var', 'and', 'or',
            'not', 'foreach', 'loop', 'continue', 'break', 'call', 'eval', 'in'}
# short forms the compiler/console accepts that the command table lists under the long name
ALIASES = {'swaptexture': 'swaptexture', 'cios': 'cios', 'setcs': 'setcs', 'imod': 'imod', 'sifh': 'setignorefriendlyhits', 'evp': 'evaluatepackage', 'pcb': 'purgecellbuffers', 'sms': 'setmenumodestate',
           'pms': 'playmusic', 'rewardxp': 'rewardxp', 'sv': 'setactorvalue', 'fav': 'forceactorvalue',
           'mav': 'modactorvalue', 'dav': 'damageactorvalue', 'rav': 'restoreactorvalue', 'gav': 'getactorvalue',
           'getav': 'getactorvalue', 'setav': 'setactorvalue', 'forceav': 'forceactorvalue', 'modav': 'modactorvalue',
           'damageav': 'damageactorvalue', 'restoreav': 'restoreactorvalue', 'getbaseav': 'getbaseactorvalue',
           'getdisabled': 'getdisabled', 'moveto': 'movetomarker', 'sw': 'setweather', 'fw': 'forceweather',
           'getisid': 'getisid'}


def _vocab():
    cmd = json.load(open(os.path.join(ROOT, 'plugin', 'hooks', 'tools', 're', 'command_table.json'), encoding='utf-8'))
    v = {k.lower() for k in cmd}
    v |= {f[0].lower() for f in ctda_funcs.FUNCS.values()}
    v |= set(lexicon.L)
    v |= set(ALIASES)
    return v


VOCAB = _vocab()
# vocabulary words that are far more often a variable/ref name or an English noun than a call in this corpus
VOCAB_DENY = {'time', 'button', 'waiting'}
CLOCKS = {'gamedayspassed', 'gamehour', 'timescale'}


def canon(fn):
    """Canonical lower-case function name (alias resolved)."""
    f = fn.lower()
    if f == 'moveto':
        return 'moveto'
    return ALIASES.get(f, f) if f not in ('getav', 'setav') else f


def strip_comment(line):
    return classify.strip_comment(line)


def scan_source(src, known_quests=frozenset(), known_globals=frozenset()):
    """Statement-level scan of one script. Returns a list of call records:
      {line, fn (lower), subject (lower or None), player (bool), cond (inside if/while), block, text, kind}
    kind = 'call' | 'setvar' | 'setglobal'. Line numbers are 1-based within the script source (scn line = 1), the same
    base the risk/policy files cite."""
    out = []
    depth = 0
    block = None
    aliases = set(PLAYER_NAMES)
    for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
        line = strip_comment(raw).strip()
        if not line:
            continue
        toks = TOKEN.findall(line)
        low = [t.lower() for t in toks]
        head = low[0]
        if head == 'begin' and len(low) > 1:
            block = toks[1]
            depth = 0
            continue
        if head == 'end' and len(low) == 1:
            block = None
            depth = 0
            continue
        if head in ('short', 'long', 'int', 'float', 'ref', 'reference', 'string_var', 'array_var', 'scn', 'scriptname'):
            continue
        in_cond = depth > 0
        if head in ('if', 'while'):
            depth += 1
        elif head == 'endif' or head == 'endwhile':
            depth = max(0, depth - 1)
            continue
        # aliasing of player refs
        if head == 'set' and len(low) >= 4 and low[2] == 'to' and low[3] in aliases | {'0x14'}:
            aliases.add(low[1])
        if head == 'set' and len(low) >= 4:
            if low[2] == '.' and low[1] in {k.lower() for k in known_quests}:
                out.append(dict(line=n, fn='set questvar', subject=toks[1], player=False, cond=in_cond or head in ('if',),
                                block=block, text=line[:200], kind='setvar'))
            elif low[2] == 'to' and low[1] in {g.lower() for g in known_globals}:
                out.append(dict(line=n, fn='set global', subject=toks[1], player=False, cond=in_cond,
                                block=block, text=line[:200], kind='setglobal'))
            elif low[2] == '.' and len(low) > 4:  # ref.var assignment (object script variable)
                out.append(dict(line=n, fn='set refvar', subject=toks[1], player=low[1] in aliases, cond=in_cond,
                                block=block, text=line[:200], kind='setvar'))
        for i, t in enumerate(low):
            if t in CLOCKS and not (head == 'set' and i == 1):
                out.append(dict(line=n, fn='read clock', subject=None, player=False, cond=in_cond or head in ('if', 'elseif'),
                                block=block, text=line[:200], kind='clock'))
                continue
            if t in KEYWORDS or t not in VOCAB or t in VOCAB_DENY:
                continue
            if i >= 1 and low[i - 1] == '.' and i >= 2:
                subj = low[i - 2]
            else:
                subj = None
            # a bare word that is a quest/global/ref name is not a call
            if subj is None and i >= 1 and low[i - 1] == '.':
                continue
            player = subj in aliases if subj else False
            out.append(dict(line=n, fn=canon(t), subject=subj, player=player, cond=in_cond or head in ('elseif',),
                            block=block, text=line[:200], kind='call'))
    return out


# ----------------------------------------------------------------------------------------------------------------
# Script universe for a questline
# ----------------------------------------------------------------------------------------------------------------
SETSTAGE_RE = re.compile(r'(?i)\b(setstage|startquest|stopquest|completequest|failquest)\s+(\w+)\s*(\d+)?')


def quest_scripts(q, infos):
    """Yield dicts {where, kind, source, refs} for the quest script, stage scripts and INFO scripts of a quest."""
    sd = q.get('script_data')
    if sd and sd.get('source'):
        yield dict(where='quest_script', kind='quest', source=sd['source'], refs=sd.get('refs', []),
                   script_ref=q.get('script'), script_edid=sd.get('edid'))
    for s in sorted(q['stages'], key=lambda s: s['index']):
        for ei, e in enumerate(s['entries']):
            sc = e.get('script')
            if sc and sc.get('source') is not None:
                yield dict(where=f'stage {s["index"]}', kind='stage', source=sc['source'], refs=sc.get('refs', []),
                           stage=s['index'], entry=ei, script_ref=None, script_edid=None)
    for i in infos:
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc.get('source') is not None:
                yield dict(where=f'info {i["ref"].split(":")[1]} {k}', kind=f'info-{k}', source=sc['source'],
                           refs=sc.get('refs', []), info=i['ref'], script_ref=None, script_edid=None)


def dep_script_list(d, q, extra):
    """The same dependency scripts classify.rollup folds into the quest's risk (object / terminal / package scripts of
    everything the quest depends on) -- restricted, like rollup, to refs the quest actually touches."""
    touched = extra['touched'].get(q['ref'])
    touched = None if touched is None else set(touched) | {t['ref'] for o in q['objectives'] for t in o['targets']}
    seen = set()
    for dr in q.get('dep_refs', ()):
        if touched is not None and dr not in touched:
            continue
        for label, sc in classify.dep_scripts(dr, extra):
            if id(sc) in seen:
                continue
            seen.add(id(sc))
            yield label, sc, dr


def find_driver_scripts(d, quest_edids):
    """Standalone scripts (objects, NPCs, quests) that SetStage/Start/Stop/Complete/Fail one of the quests."""
    names = {n.lower() for n in quest_edids}
    out = {}
    for s in d.scripts:
        src = (s.get('script') or {}).get('source') or ''
        for ln in src.replace('\r', '').split('\n'):
            m = SETSTAGE_RE.search(strip_comment(ln))
            if m and m.group(2).lower() in names:
                out[s['ref']] = s
                break
    return out


def console_cmd_stage(d, edid, stage):
    return f'setstage {d.hexid(edid)} {stage}'
