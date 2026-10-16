"""Q-strip scope + data access: which quests/scripts belong to the Freeside / Strip questline.

Everything is read from out/questdata (read-only). Nothing in here invents a FormID: every EditorID in the group tables
is resolved against quests.json / scripts.json.gz / forms.json.gz by `resolve_scope()` and by run_tests.py.
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

SLUG = 'strip'

# ---- quest groups ---------------------------------------------------------------------------------------------
# core: the 19 quests docs/quest-policies/side-freeside-strip.md assigns to this questline (rules.json questline
# "Side: Freeside / Strip"); a test cross-checks this list against rules.json.
CORE = ['VMS45', 'VMS30', 'VMS29', 'VMS35', 'VMS42', 'VMS36', 'VMS24', 'VMS47', 'vfreeformlucky38', 'VFSHighTimes',
        'VMS31', 'VMS32', 'VMS38', 'VMS25', 'VFreeformTheStreet01', 'VMS41', 'vCCEyeforaneye', 'VMS33', 'VNellisJack']
# extended: story quests with stages that belong to this lane by content (Tops talent pool, Atomic Wrangler,
# Gomorrah/Omertas) but that no policy file assigns to a questline (or assigns to Boulder City although the
# gameplay is Gomorrah/Omertas: VMS21/VMS21a).
EXTENDED = {
    'VMSTopsTalentPool': 'Talent Pool: Tops (Chairmen) entertainers',
    'VFSDebtCollector': 'Debt Collector: Freeside (Atomic Wrangler) bounties',
    'VFSAtomicPimp': 'Wang Dang Atomic Tango: Atomic Wrangler escorts',
    'VMS21': 'How Little We Know: Gomorrah / Omertas racket',
    'VMS21a': 'Bye Bye Love: Gomorrah / Omertas, Joana',
    'vCCLeftMyHeart': 'Left My Heart: Frank Weathers (sibling of vCCEyeforaneye)',
    'VMS38a': 'Support scripting for Bleed Me Dry',
    'VMS38b': 'The Thorn Mayhem (Bleed Me Dry support)',
}
# system: dialogue containers, casino winnings/ban quests, scripted-scene and security quests. group -> reason
SYSTEM = {
    # casino gambling / bans / chips
    'vCasinoCompsVikkiAndVance': 'casino', 'vCasinoCompsAtomicWrangler': 'casino', 'vCasinoCompsGomorrah': 'casino',
    'VCasinoCompsUltraLuxe': 'casino', 'vCasinoCompsTheTops': 'casino', 'vDialogueCasinoCashier': 'casino',
    'VChipHistory': 'casino', 'VTopsKickedOut': 'casino', 'VTopsSecurity': 'casino', 'VUltraLuxeSecurity': 'casino',
    'VMSTopsPerformances': 'casino', 'VDialogueTops': 'casino', 'vDialogueTheTops': 'casino',
    'VDialogueWhiteGloveSociety': 'casino', 'vDialogueGomorrah': 'casino', 'vDialogueGomorrahGeneric': 'casino',
    # Strip / Lucky 38 street scripting
    'vDialogueTheStrip': 'strip', 'VStreetReputation': 'strip', 'VStreetSS2': 'strip', 'VStreetSS3': 'strip',
    'VStreetSS4': 'strip', 'VStreetSS5': 'strip', 'VStreetSS6': 'strip', 'VStreetSS7': 'strip',
    'VStreetCombat': 'strip', 'VStreetLighting': 'strip', 'vFreeformTheStreet02': 'strip',
    'VMQEndStatesTheStrip': 'strip', 'StripRadioQuest': 'strip', 'TopsRadioQuest': 'strip',
    'VDialogueLucky38Suite': 'strip', 'VDialogueLucky38Penthouse': 'strip',
    # Freeside / Kings / Followers
    'vDialogueFreeside': 'freeside', 'VFreeformFreeside': 'freeside', 'VFreeformFreeside2': 'freeside',
    'vDialogueFreesideDrunk': 'freeside', 'vDialogueKings': 'freeside', 'vDialogueFollowersApocalypse': 'freeside',
    'vDialogueMedicalClinic': 'freeside', 'VMQEndStatesFreeside': 'freeside', 'VDialogueArcadeGannon': 'freeside',
    # companion support of VMS41 (Lily)
    'vDialogueLily': 'freeside', 'Lilysmedicinetimer': 'freeside', 'LilysPsychoticBreaks': 'freeside',
}
# casino / security / gate object scripts that no quest dep_ref reaches. SCPT editor IDs (resolved in scripts.json.gz).
CASINO_OBJ_SCRIPTS = [
    # Atomic Wrangler
    'SlotMachineWranglerScript', 'BlackjackTableAtomicWranglerScript', 'RouletteTableAtomicWranglerScript',
    # Vikki and Vance
    'vVikkiVanceSlotScript', 'vVikkiAndVanceBlackjackSCRIPT', 'VikkiAndVanceRouletteTableSCRIPT',
    # Gomorrah (Omertas)
    'vGomorrahSlot01Script', 'vGomorrahBlackjackSCRIPT', 'GomorrahRouletteTableSCRIPT',
    'GomorrahCasinoEnterTriggerScript', 'GomorrahHotelDoorScript', 'GomorrahExitTriggerScript', 'OmertaBouncerScript',
    'VMS33OmertaThugTriggerScript', 'VMS21aOmertaAmbushThugSCRIPT', 'VMS21GomorrahMPTriggerScript',
    # Ultra-Luxe (White Glove Society)
    'BlackjackTableUltraLuxeScript', 'RouletteTableUltraLuxeScript', 'UltraLuxeBlackjackTable01StoolScript',
    'VUltraLuxeSecuritySCRIPT', 'VUltraLuxeInteriorDoorSCRIPT', 'VUltraLuxeExteriorDoorSCRIPT',
    'VUltraLuxeFriskingTriggerScript', 'VUltraLuxeFriskingHoldoutTriggerScript', 'VUltraLuxeWeaponsLockerSCRIPT',
    'VMS18WhiteGloveBaseSCRIPT', 'VMS18WhiteGloveSocietyFactionOutfitWarningScript', 'WhiteGloveSocietySnifferSCRIPT',
    'VMS18RouletteDealerSCRIPT',
    # The Tops (Chairmen)
    'SlotMachineTheTopsScript', 'BlackjackTableTheTopsScript', 'RouletteTableTheTopsScript',
    'TheTopsBlackjackTable01StoolScript', 'vTopsBlackjackDealer01Script', 'TheTopsSmallScript',
    'TopsExteriorDoorSCRIPT', 'TopsInteriorDoorScript', 'TopsFriskingTriggerScript', 'TopsFriskingHoldoutTriggerScript',
    'TopsReturnWeaponsTriggerSCRIPT', 'VTopsSecuritySCRIPT', 'VTopsGreeterSCRIPT', 'VTopsKickedOutSCRIPT',
    'VTopsKickedOutSideSCRIPT', 'VTopsWeaponsLockerSCRIPT', 'VTopsPresidentialDoorScript', 'TopsCombatSCRIPT',
    # generic dealers, cashier, chips, challenges tied to the games
    'NVRouletteDealerScript', 'NVRouletteDealerScript2', 'NVRouletteTableScript', 'RouletteDealerMarkerScript',
    'vDialogueCasinoCashierSCRIPT', 'ChallengeBlackjackSCRIPT', 'ChallengeSlotsSCRIPT', 'ChallengeRouletteSCRIPT',
    'L38walltermchipSCRIPT', 'Lucky38CasinoTrigger01SCRIPT', 'CasinoBaseLightScript',
    # Freeside gate and Mick & Ralph's / Atomic Wrangler
    'VFSRalphSCRIPT', 'VFSMickandRalphsIntDoorSCRIPT', 'VFSSilverRushGamblerCustomerScript',
]

# quests of other lanes this questline reads/writes (cross-lane references, not analysed here)
CROSS_LANE = {
    'VMS29a': 'Main: Shared (Ring-a-Ding-Ding and Act 2) -> Q-act2 (Birds of a Feather, Van Graffs / Silver Rush)',
    'VMS18': 'Main: Shared -> Q-act2 (Beyond the Beef, White Glove Society / Ultra-Luxe)',
    'VMQTops': 'Main: Shared -> Q-act2 (Ring-a-Ding-Ding, the Tops)',
    'VDialogueVegasNorth': 'Main: Shared -> Q-act2 (Someone to Watch Over Me)',
    'VDialogueLucky38Entrance': 'Main: Shared -> Q-act2 (Lucky 38 entrance)',
    'VMQHouse': 'Main: House -> Q-house',
}

SEV_ORDER = ['blocker', 'high', 'medium', 'low', 'info']


def ref_hex(ref):
    return ref.rsplit(':', 1)[1].upper()


def console_hex(ref):
    return loadorder.console_id(ref)


class Data:
    """Lazy read-only view of out/questdata + coverage/policy files."""

    def __init__(self, qd=QD, repo=REPO):
        self.qd, self.repo = qd, repo
        self.d, self.infos_by_quest = classify.load(qd)
        self.extra = classify.load_extra(qd)
        self.quests = [q for q in self.d['quests']]
        self.by_edid = {q['edid']: q for q in self.quests if q.get('edid')}
        self.by_ref = {q['ref']: q for q in self.quests}
        self.forms = self.extra['forms']
        self.scpt = self.extra['scpt']
        self.scpt_by_edid = {}
        for ref, x in self.scpt.items():
            if x.get('edid'):
                self.scpt_by_edid[x['edid'].lower()] = x
        self.all_infos = {}
        for qref, lst in self.infos_by_quest.items():
            for i in lst:
                self.all_infos[i['ref']] = i
        self.globals = {g['ref']: g for g in self.d['globals']}
        self.globals_by_edid = {g['edid'].lower(): g for g in self.d['globals'] if g.get('edid')}
        self._risk = None
        self._rules = None
        self._policies = None
        self._native = None
        self._effects = None
        self._graph = None
        self._dials = None
        self._scenarios = {}

    def _json(self, *parts, gz=False):
        p = os.path.join(self.repo, *parts)
        if gz:
            with gzip.open(p, 'rt', encoding='utf-8') as f:
                return json.load(f)
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    @property
    def risk(self):
        if self._risk is None:
            self._risk = self._json('out', 'questdata', 'risk.json')['quests']
        return self._risk

    @property
    def rules(self):
        if self._rules is None:
            self._rules = self._json('docs', 'quest-policies', 'rules.json')
        return self._rules

    @property
    def policies(self):
        if self._policies is None:
            self._policies = {p['edid']: p for p in self._json('docs', 'quest-policies', 'policies.json')}
        return self._policies

    @property
    def native(self):
        if self._native is None:
            self._native = self._json('out', 'questdata', 'effects_native.json.gz', gz=True)
        return self._native

    @property
    def effects(self):
        if self._effects is None:
            self._effects = self._json('out', 'questdata', 'effects.json.gz', gz=True)
        return self._effects

    @property
    def graph(self):
        if self._graph is None:
            self._graph = self._json('out', 'coverage', 'questgraph.json')
        return self._graph

    @property
    def dials(self):
        if self._dials is None:
            self._dials = {x['ref']: x for x in self._json('out', 'questdata', 'dials.json.gz', gz=True)}
        return self._dials

    def scenario(self, edid):
        if edid not in self._scenarios:
            p = os.path.join(self.qd, 'scenarios', edid + '.json')
            self._scenarios[edid] = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None
        return self._scenarios[edid]

    # -------------------------------------------------------------------------------------------------------
    def members(self):
        """[(edid, group, detail)] in report order."""
        out = [(e, 'core', None) for e in CORE]
        out += [(e, 'extended', why) for e, why in EXTENDED.items()]
        out += [(e, 'system', why) for e, why in SYSTEM.items()]
        return out

    def resolve_scope(self):
        """Return list of problems (unresolved EditorIDs); empty = every name in the scope tables is real."""
        bad = []
        for e, g, _ in self.members():
            if e not in self.by_edid:
                bad.append(f'quest {e} ({g}) not in quests.json')
        for s in CASINO_OBJ_SCRIPTS:
            if s.lower() not in self.scpt_by_edid:
                bad.append(f'script {s} not in scripts.json.gz')
        for e in CROSS_LANE:
            if e not in self.by_edid:
                bad.append(f'cross-lane quest {e} not in quests.json')
        return bad

    def info_label(self, i, key):
        return f'info {ref_hex(i["ref"])} {key}'

    # script enumeration ------------------------------------------------------------------------------------
    def scripts_of_quest(self, edid):
        """Own scripts of a quest: [dict(kind,label,edid,source,refs,quest,stage?,info?)]. Deduped by label."""
        q = self.by_edid[edid]
        out = []
        sd = q.get('script_data')
        if sd and sd.get('source'):
            out.append(dict(kind='quest_script', label=f'quest_script {edid}', edid=sd.get('edid') or edid + 'Script',
                            source=sd['source'], refs=sd.get('refs', []), quest=edid))
        for s in sorted(q['stages'], key=lambda s: s['index']):
            for n, e in enumerate(s['entries']):
                sc = e.get('script')
                if sc and sc.get('source'):
                    out.append(dict(kind='stage', label=f'stage {edid}:{s["index"]}', edid=f'{edid}:stage{s["index"]}',
                                    source=sc['source'], refs=sc.get('refs', []), quest=edid, stage=s['index']))
        for i in self.infos_by_quest.get(q['ref'], []):
            for k in ('begin', 'end'):
                sc = i.get(k)
                if sc and sc.get('source'):
                    out.append(dict(kind='info', label=self.info_label(i, k), edid=f'info:{ref_hex(i["ref"])}',
                                    source=sc['source'], refs=sc.get('refs', []), quest=edid, info=i['ref'], block=k))
        return out

    def dep_scripts_of_quest(self, edid):
        """Object/NPC/terminal/package scripts the quest depends on and touches (same filter as the risk tiers)."""
        q = self.by_edid[edid]
        touched = self.extra['touched'].get(q['ref']) if self.extra.get('touched') else None
        touched = None if touched is None else set(touched) | {t['ref'] for o in q['objectives'] for t in o['targets']}
        out, seen = [], set()
        sid = {id(x['script']): x.get('edid') for x in self.scpt.values()}
        for dr in q.get('dep_refs', ()):
            if touched is not None and dr not in touched:
                continue
            for label, sc in classify.dep_scripts(dr, self.extra):
                if id(sc) in seen or not sc.get('source'):
                    continue
                seen.add(id(sc))
                nm = sid.get(id(sc)) or (label.split(' ', 1)[1] if ' ' in label else label)
                out.append(dict(kind='dep', label=f'dep {label}', edid=nm, source=sc['source'],
                                refs=sc.get('refs', []), quest=edid))
        return out

    def related_infos(self, edid):
        """INFOs living in OTHER quests (dialogue containers) that read or advance this quest (memoised)."""
        memo = self.__dict__.setdefault('_related', {})
        if edid not in memo:
            memo[edid] = self._related_infos(edid)
        return memo[edid]

    def _related_infos(self, edid):
        q = self.by_edid[edid]
        qref = q['ref']
        pat = re.compile(r'\b(setstage|getstage|getstagedone|setobjective\w+|completequest|failquest|startquest|'
                         r'stopquest|completeallobjectives)\s+' + re.escape(edid) + r'\b|\b' + re.escape(edid) + r'\.\w+',
                         re.I)
        out = []
        for i in self.all_infos.values():
            if i.get('quest') == qref:
                continue
            hit = None
            for c in i.get('conditions', []):
                if c['func'] in classify.QUEST_DEP_FUNCS and c.get('p1') == qref:
                    hit = 'condition'
                    break
            if not hit:
                for k in ('begin', 'end'):
                    sc = i.get(k)
                    if sc and sc.get('source') and (qref in sc.get('refs', []) and pat.search(sc['source'])):
                        hit = k
                        break
            if hit:
                out.append((i, hit))
        return out

    def casino_obj_scripts(self):
        out = []
        for s in CASINO_OBJ_SCRIPTS:
            x = self.scpt_by_edid.get(s.lower())
            if x and x['script'].get('source'):
                out.append(dict(kind='casino_obj', label=f'obj {x["edid"]}', edid=x['edid'], source=x['script']['source'],
                                refs=x['script'].get('refs', []), quest=None))
        return out


def quest_console_ids(data, edid):
    q = data.by_edid[edid]
    return q['ref'], console_hex(q['ref'])
