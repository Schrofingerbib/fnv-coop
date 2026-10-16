"""Shared loaders + scope for the NCR questline lane (Q-ncr).

Read-only over out/questdata, out/coverage and docs/quest-policies. Everything here is deterministic.
Import from sibling scripts with: sys.path.insert(0, dirname(__file__)) (python -I does not add the script dir).
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
import loadorder  # noqa: E402

SLUG = 'ncr'

# ---- scope -----------------------------------------------------------------------------------------------------
# core    = the NCR main line itself (docs/quest-policies/main-ncr.md) + the critical-path prerequisites that the
#           coverage graph derived (VMS45, VMQNCRFail, VMS21) + Hoover Dam battle controller. Every stage is swept.
# support = stage-bearing quests that a core stage script drives through a HARD edge (SetStage / StartQuest); swept
#           because their effects are part of the core stage's transaction.
# Quests that only gate/feed a core quest and have stages of their own (VMS46, VMS23, VMS29, ...) are `touched`
# (inventoried, stages listed in sweep.json `excluded` with a reason). Stage-less dialogue/script containers are `system`.
CORE = ['VMS45', 'VMQNCRFail', 'VMS21', 'VMQ05', 'vHDBattleController', 'VMQ06',
        'VEFR00NCRMixed2LimitedAccess', 'VEFR01NCRGood2EmergencyRadio', 'VEFR02NCRBad2MostWanted']
SUPPORT = ['VMS21a']
# Stage-less containers that carry the NCR line's dialogue/scripts (topics, result scripts, timers). Chosen from the
# coverage graph (edges into/out of CORE with kind stage|start|var|gate) and checked by hand against the quest text.
SYSTEM = ['VFreeformHooverDam', 'vDialogueHooverDam', 'VHDKimballSpeech', 'VHDAssassinationDialog',
          'vNCREmergencyRadio', 'VEnding', 'VMQEndStates', 'VMQEndStatesTheStrip', 'VMQEndStatesFreeside',
          'VMS21aSupport', 'VHDOliverRetreatTimer', 'VHDOliverDeathTimer', 'vDialogueGomorrah',
          'vDialogueGomorrahGeneric', 'vFreeformTheStreet02', 'VDialogueHiddenValley', 'VLegateShowdown',
          'vDialogueGreatKhansGeneric', 'vDialogueNCRMilitary', 'vDialogueNCRCivilian']
# Quests owned by other lanes that the NCR line needs (named in the lane brief / graph critical path):
CROSS_LANE = {'VMQ03a': 'You\'ll Know It When It Happens (NCR-only Act 2 step, Q-act2)',
              'VMQ04': 'Bright and Shiny (Q-act2)', 'VMQTops': 'Ring-a-Ding-Ding! (Q-act2)'}
CROSS_PREFIX = ('VCG', 'VMQ0', 'VMQTops')

STATUS_RANK = {'supported': 0, 'n/a': 0, 'partial': 1, 'missing': 2}


def readgz(name):
    with gzip.open(os.path.join(QD, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


class Data:
    """Everything loaded once."""

    def __init__(self):
        self.d, infos = classify.load(QD)
        # an INFO overridden by a later plugin (e.g. GunRunnersArsenal.esm re-issuing FalloutNV.esm:15FD06) appears twice in
        # infos.jsonl; with the full load order only the last record is live, so keep that one (first position, last content)
        self.infos = {}
        self.info_overrides = 0
        for qref, lst in infos.items():
            keep = {}
            for i in lst:
                if i['ref'] in keep:
                    self.info_overrides += 1
                keep[i['ref']] = i
            self.infos[qref] = list(keep.values())
        self.quests = [q for q in self.d['quests']]
        self.by_edid = {q['edid']: q for q in self.quests if q.get('edid') and not q.get('override')}
        self.by_ref = {q['ref']: q for q in self.quests if not q.get('override')}
        self.risk = json.load(open(os.path.join(QD, 'risk.json'), encoding='utf-8'))['quests']
        self.graph = json.load(open(os.path.join(REPO, 'out', 'coverage', 'questgraph.json'), encoding='utf-8'))
        self.native = readgz('effects_native.json.gz')
        self.eff = readgz('effects.json.gz')
        self.forms = readgz('forms.json.gz')
        self.refnames = readgz('refnames.json.gz')
        self.scpt_list = readgz('scripts.json.gz')
        self.dials = readgz('dials.json.gz')
        self.rules = json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))
        self.policies = {p['edid']: p for p in json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'policies.json'), encoding='utf-8'))}
        self.hostowned = json.load(open(os.path.join(REPO, 'out', 'coverage', 'hostowned.json'), encoding='utf-8'))
        self._edid2ref = None
        self._extra = None

    def extra(self):
        if self._extra is None:
            self._extra = classify.load_extra(QD)
        return self._extra

    # ---- EDID / ref resolution -------------------------------------------------------------------------------
    def edid_map(self):
        """lower(edid) -> set(ref) over quests, forms.json.gz, refnames.json.gz, scripts.json.gz (script EDIDs) and globals."""
        if self._edid2ref is None:
            m = collections.defaultdict(set)
            for q in self.quests:
                if q.get('edid'):
                    m[q['edid'].lower()].add(q['ref'])
            for src in (self.forms, self.refnames):
                for ref, v in src.items():
                    if v.get('edid'):
                        m[v['edid'].lower()].add(ref)
            for g in self.d.get('globals', []):
                if g.get('edid'):
                    m[g['edid'].lower()].add(g['ref'])
            for s in self.scpt_list:
                if s.get('edid'):
                    m[s['edid'].lower()].add(s['ref'])
            self._edid2ref = m
        return self._edid2ref

    def resolve(self, edid):
        """EDID -> ref or None (None when unknown or ambiguous between different refs)."""
        s = self.edid_map().get(edid.lower())
        if not s or len(s) != 1:
            return None
        return next(iter(s))

    def ref_info(self, ref):
        if ref in self.by_ref:
            q = self.by_ref[ref]
            return {'ref': ref, 'sig': 'QUST', 'edid': q.get('edid'), 'name': q.get('name')}
        v = self.forms.get(ref) or self.refnames.get(ref)
        if not v:
            return {'ref': ref}
        o = {'ref': ref, 'sig': v.get('sig'), 'edid': v.get('edid')}
        if v.get('name'):
            o['name'] = v['name']
        return o

    def ref_exists(self, ref):
        if ref in self.by_ref or ref in self.forms or ref in self.refnames:
            return True
        return any(g.get('ref') == ref for g in self.d.get('globals', []))

    # ---- scripts ---------------------------------------------------------------------------------------------
    def quest_scripts(self, q):
        """Yield (where, script_dict, script_edid_or_None) for the quest script, stage scripts and INFO result scripts."""
        sd = q.get('script_data')
        if sd and sd.get('source'):
            yield 'quest_script', sd, sd.get('edid')
        for s in sorted(q['stages'], key=lambda s: s['index']):
            for n, e in enumerate(s['entries']):
                if e.get('script') and e['script'].get('source'):
                    yield (f'stage {s["index"]}' + (f'.{n}' if n else '')), e['script'], None
        for i in self.infos.get(q['ref'], []):
            for k in ('begin', 'end'):
                if i.get(k) and i[k].get('source'):
                    yield f'info {i["ref"].split(":")[1]} {k}', i[k], None


def hexid(ref):
    return loadorder.console_id(ref)


def qkey(q):
    return q['edid']


def strip(line):
    return classify.strip_comment(line).strip()


def stage_texts(q):
    out = {}
    for s in q['stages']:
        for e in s['entries']:
            if e.get('text'):
                out[s['index']] = e['text']
                break
    return out


def jdump(obj, path, indent=1):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False, sort_keys=False)
        f.write('\n')
