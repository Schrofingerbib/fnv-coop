"""Shared loaders and full (uncapped) finding collector for the factions questline.

Read-only over out/questdata. Never touches the game install.
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(REPO, 'out', 'questdata')
sys.path.insert(0, os.path.join(REPO, 'tools', 'questdata'))
import classify  # noqa: E402

# group -> quests. The group is a lane label (policy file / data evidence), not an in-game faction claim.
GROUPS = {
    'nellis_golf_ncr': ['VMS56', 'VFreeformCampGolf', 'NellisSunshineBoogie', 'vFreeformForlornHope03', 'VMS52', 'VMS55',
                        'VMS53', 'VMS54', 'VMS55a', 'VMS50', 'VMS51'],
    'mojave_other_policy': ['VMS49', 'VMS39'],
    'nellis_boomers': ['VNellisInfestation', 'VNellisJack', 'VFreeformNellis'],
    'brotherhood_of_steel': ['VMS55b', 'VDialogueBrotherhoodOfSteelFaction', 'VDialogueHiddenValley', 'DialogueBoSSniffer'],
    'enclave_remnants': ['VDialogueRemnants'],
    'powder_gangers': ['VES04FactReactPrimmArea01PowderGangers', 'vDialoguePowderGangerGeneric',
                       'vDialogueGoodspringsPowderGanger', 'VFreeformNCRCF', 'vDialogueNCRCFGeneric',
                       'VFreeformQuarryJunction'],
    'great_khans': ['vDialogueGreatKhansGeneric', 'vCrucifiedFaction', 'VMS46'],
    'followers_of_the_apocalypse': ['vDialogueFollowersApocalypse'],
    'companion_system': ['VNPCFollowers', 'Followers', 'FollowersHireRL3'],
    'mojave_other': ['VMS57', 'VMS43', 'VMS43a', 'VMS44', 'VMS37', 'vMojaveGhost', 'VMSRSGlobalCommBreakdown',
                     'vDialogueRangerStationEcho', 'vDialogueRangerStationDelta', 'vDialogueRangerStationBravo',
                     'VES34Vault', 'VT74', 'VMS12', 'VES05NightkinTumbleweedRancher', 'vFreeformForlornHope02'],
}
QUESTS = [q for g in GROUPS.values() for q in g]
GROUP_OF = {q: g for g, qs in GROUPS.items() for q in qs}

# One-line themes, each taken from the objective text / referenced NPCs in the quest data (see inventory notes).
THEME = {
    'VMS56': 'NCR First Recon: help Corporal Betsy, message to Dr Usanagi (New Vegas Medical Clinic)',
    'VFreeformCampGolf': 'Camp Golf Misfits: improve readiness / fake reports (Jack, Sgt McCredie)',
    'NellisSunshineBoogie': 'Nellis AFB: repair solar arrays (Boomer rep)',
    'vFreeformForlornHope03': 'Camp Forlorn Hope: Legion ears for Sexton (NCR rep)',
    'VMS52': 'Ranger Stations radio security upgrade (Sgt Reyes; Alpha..Foxtrot)',
    'VMS55': 'Brotherhood of Steel at Hidden Valley: Still in the Dark (Elder McNamara, Ramos, Hardin)',
    'VMS53': 'Boone companion quest: kidnapped wife, Dino Dee-lite, Novac',
    'VMS54': 'Arcade Gannon companion quest: reunite the Remnants',
    'VMS55a': 'BoS induction path: kill the Van Graffs for Hardin',
    'VMS50': 'Jacobstown: mercenaries harassing the lodge (Marcus)',
    'VMS51': 'Alice McLafferty: disable the bottle cap press (NCR rep)',
    'VMS49': 'Veronica companion quest: Hidden Valley elder, Vault 22 (Brotherhood)',
    'VMS39': 'Boone companion quest: Bitter Springs, Coyote Tail Ridge',
    'VNellisInfestation': 'Nellis AFB: giant ants / generators (Raquel)',
    'VNellisJack': 'Nellis AFB: Young Hearts (Jack and Janet)',
    'VFreeformNellis': 'Nellis AFB general dialogue (Boomer rep/karma lines)',
    'VMS55b': 'BoS induction path: install device in Black Mountain console for McNamara',
    'VDialogueBrotherhoodOfSteelFaction': 'BoS faction dialogue container',
    'VDialogueHiddenValley': 'Hidden Valley conversations (McNamara, Ramos, Hardin, intercom)',
    'DialogueBoSSniffer': 'BoS sniffer dialogue container',
    'VDialogueRemnants': 'Enclave Remnants dialogue (Arcade, Orion Moreno, Judah, Daisy, Henry)',
    'VES04FactReactPrimmArea01PowderGangers': 'Primm-area Powder Ganger faction reaction',
    'vDialoguePowderGangerGeneric': 'Generic Powder Ganger dialogue',
    'vDialogueGoodspringsPowderGanger': 'Goodsprings Powder Ganger dialogue (Goodsprings lane owns the story quests)',
    'VFreeformNCRCF': 'NCR Correctional Facility dialogue (Powder Ganger / NCR rep)',
    'vDialogueNCRCFGeneric': 'Generic NCRCF dialogue',
    'VFreeformQuarryJunction': 'Quarry Junction dialogue container',
    'vDialogueGreatKhansGeneric': 'Great Khans generic dialogue (armorer barter)',
    'vCrucifiedFaction': 'Crucified faction container (Aba Daba Honeymoon)',
    'VMS46': 'Aba Daba Honeymoon: free Anders, drugs for Motor-Runner / Crimson Caravan, Khan rep',
    'vDialogueFollowersApocalypse': 'Followers of the Apocalypse dialogue',
    'VNPCFollowers': 'Companion recruitment system quest (Boone, Arcade, Veronica, ED-E, ...)',
    'Followers': 'Quest to handle followers (system)',
    'FollowersHireRL3': 'Hiring RL-3 (system)',
    'VMS57': 'Black Mountain: end Tabitha (Neil)',
    'VMS43': 'Camp Searchlight: Edwards / ghouls (Raul)',
    'VMS43a': 'Camp Searchlight: Edwards, Astor, radscorpions',
    'VMS44': 'Camp Searchlight / Nipton: missing radiation suits (Logan)',
    'VMS37': 'Bitter Springs: who is harassing the camp (NCR rep, karma)',
    'vMojaveGhost': 'Mojave Outpost: Ranger Ghost recon of Nipton',
    'VMSRSGlobalCommBreakdown': 'Ranger Stations radio encryption (Return To Sender, Reyes)',
    'vDialogueRangerStationEcho': 'Ranger Station Echo dialogue',
    'vDialogueRangerStationDelta': 'Ranger Station Delta dialogue',
    'vDialogueRangerStationBravo': 'Ranger Station Bravo dialogue',
    'VES34Vault': 'Vault 34 / Sharecropper Farms: Hard Luck Blues',
    'VT74': 'Vault 74 exploration',
    'VMS12': 'Three-Card Bounty: heads of Violet, Cook-Cook, Driver Nephi (First Recon)',
    'VES05NightkinTumbleweedRancher': 'Nightkin Tumbleweed Rancher encounter',
    'vFreeformForlornHope02': 'Camp Forlorn Hope: Stocking Up',
}

# Quests the data/policy files put elsewhere; listed for cross reference only (no sweep, no patch).
XREF = {
    'vMojave': 'policy file: Side: Misc / events',
    'VNelson': 'policy file: Side: Misc / events',
    'vDialogueSL': 'policy file: Side: Misc / events',
    'vDialogueEDE': 'policy file: Side: Misc / events',
    'NVDLC02MQ01': 'White Legs content lives in Honest Hearts (Q-honesthearts)',
    'VMS45': 'Great Khans Oh My Papa: Freeside / Strip lane',
    'VMS42': 'Great Khans Why Can\'t We Be Friends: Freeside / Strip lane',
    'VMS20': 'Boulder City Showdown (Great Khans): Boulder City lane',
    'VMS21a': 'Bye Bye Love (Great Khans): Boulder City lane',
    'VMS15': 'Volare! (Boomers): Novac lane',
    'VMS23': 'Things That Go Boom (Boomers): Novac lane',
}


def load():
    d, infos = classify.load(OUT)
    extra = classify.load_extra(OUT)
    by_edid = {q['edid']: q for q in d['quests'] if q.get('edid')}
    by_ref = {q['ref']: q for q in d['quests']}
    risk = json.load(open(os.path.join(OUT, 'risk.json'), encoding='utf-8'))['quests']
    kq = {q['edid'].lower() for q in d['quests'] if q.get('edid')}
    kg = {g['edid'].lower() for g in d['globals'] if g.get('edid')}
    return dict(d=d, infos=infos, extra=extra, by_edid=by_edid, by_ref=by_ref, risk=risk, kq=kq, kg=kg)


def gz(name):
    with gzip.open(os.path.join(OUT, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


def load_order():
    return json.load(open(os.path.join(REPO, 'tools', 'questdata', 'loadorder.json'), encoding='utf-8'))['plugins']


def fid(ref, order=None):
    """'FalloutNV.esm:144519' -> runtime hex FormID (load-order prefix + local id), 8 hex digits."""
    order = order or load_order()
    plugin, local = ref.split(':')
    return '%02X%06X' % (order.index(plugin), int(local, 16) & 0xFFFFFF)


def scripts_of(ctx, q):
    """Yield (where, script_edid, source, dialogue_ctx, line_base) for every script that belongs to the quest."""
    extra = ctx['extra']
    r = q['ref']
    sd = q.get('script_data')
    if sd and sd.get('source'):
        yield 'quest_script', sd.get('edid'), sd['source'], None
    for s in q['stages']:
        for e in s['entries']:
            sc = e.get('script')
            if sc and sc.get('source'):
                yield 'stage %d' % s['index'], None, sc['source'], None
    for i in ctx['infos'].get(r, ()):
        iid = i['ref'].split(':')[1]
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc.get('source'):
                yield 'info %s %s' % (iid, k), None, sc['source'], 'dialogue'
    touched = extra.get('touched', {}).get(r) if extra.get('touched') else None
    touched = None if touched is None else set(touched) | {t['ref'] for o in q['objectives'] for t in o['targets']}
    seen = set()
    for dr in q.get('dep_refs', ()):
        if touched is not None and dr not in touched:
            continue
        for label, sc in classify.dep_scripts(dr, extra):
            if id(sc) in seen:
                continue
            seen.add(id(sc))
            if sc and sc.get('source'):
                yield 'dep %s' % label, sc.get('edid'), sc['source'], 'terminal' if label.startswith('term') else None


def collect(ctx, q):
    """Every classify finding for the quest (uncapped) with where / script edid. Mirrors classify.rollup."""
    kq, kg = ctx['kq'], ctx['kg']
    r = q['ref']
    out = []

    def add(fs, where, script_edid=None):
        for f in fs:
            f = dict(f)
            f['where'] = where
            f['script'] = script_edid
            out.append(f)
    for where, sedid, src, dctx in scripts_of(ctx, q):
        fs = classify.classify_source(src, kq, kg, dctx)
        add(fs, where, sedid)
    for s in q['stages']:
        for e in s['entries']:
            if e.get('next_quest'):
                add([classify._f(0, 'next_quest', 'Q-STATE', 1, 'next_quest', False, None)], 'stage %d' % s['index'])
    for i in ctx['infos'].get(r, ()):
        iid = i['ref'].split(':')[1]
        add(classify.classify_conditions(i['conditions'], 'info'), 'info %s cond' % iid)
        if 'speech_challenge' in i.get('flags', []) or i.get('speech_challenge') not in (None, 'none'):
            add([classify._f(0, 'speech challenge', 'CONV', 3, 'speech_challenge', True, None)], 'info %s' % iid)
        if i.get('add_topics'):
            add([classify._f(0, 'add topics', 'Q-STATE', 1, 'addtopic', False, None)], 'info %s' % iid)
    add(classify.classify_conditions(q['conditions'], 'quest'), 'quest cond')
    return out
