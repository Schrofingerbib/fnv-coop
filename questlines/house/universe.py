"""Script universe of the Mr. House questline: every script whose statements can run as part of the House path.

lane 'house'  : scripts of CORE/SYSTEM quests (quest script, stage result scripts, INFO begin/end scripts), the object/NPC/terminal/
                package scripts they depend on (classify.dep_scripts rules), scripts they reference (one hop), and standalone scripts
                that SetStage/Start/Stop/Complete/Fail one of those quests ("driver" scripts: MrHouseSCRIPT, the Lucky 38 elevator
                scripts, VHD* battle scripts ...).
lane 'shared' : the same for SHARED quests (owned by other lanes) minus anything already seen in the house lane.
Each record is deduplicated by identity so a script counted for the house lane is not counted again for the shared lane.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qlib  # noqa: E402

# Scripts of the ending machinery that hang off refs the quest scripts do not reference by SCRO (found by reading
# VEndingScript / NarratorScript / EndingTriggerScript; confirmed present in scripts.json.gz by the tests).
EXTRA_SHARED_SCRIPTS = ['NarratorScript', 'EndingTriggerScript', 'VEndingBinksSCRIPT']


def obj_scripts_for(ref, extra):
    """(label, script_ref, script_dict) for the scripts attached to a placed ref / its base (object, terminal, package)."""
    forms, scpt = extra['forms'], extra['scpt']
    terms, packs = extra['terms'], extra['packs']
    if (forms.get(ref) or {}).get('sig') == 'QUST':
        return  # another quest: its scripts belong to that quest's own lane
    cands = [ref]
    f = forms.get(ref)
    if f and f.get('base'):
        cands.append(f['base'])
    for c in cands:
        fc = forms.get(c) or {}
        nm = fc.get('edid') or c
        if c in terms:
            for it in terms[c]['items']:
                if it.get('script'):
                    yield f'term {nm}', f'term:{c}:{it.get("text", "")[:24]}', it['script']
        if c in packs:
            for k, sc in packs[c]['scripts'].items():
                yield f'pack {nm} {k}', f'pack:{c}:{k}', sc
        sref = fc.get('script')
        if sref and sref in scpt and 'script' in scpt[sref]:
            yield f'obj {nm}', sref, scpt[sref]['script']
        if c in terms and terms[c].get('script_ref') in scpt:
            yield f'term {nm} object', terms[c]['script_ref'], scpt[terms[c]['script_ref']]['script']


def display_name(d, label, sref, kind):
    """The script EDID when we know it, else the label (matches the `dep obj X` style used by risk.json)."""
    s = d.s_by_ref.get(sref)
    return s['edid'] if s and s.get('edid') else label


def build(d=None):
    d = d or qlib.data()
    extra = d.classify_extra()
    out, seen = [], set()

    def add(rec):
        if rec['id'] in seen:
            return
        seen.add(rec['id'])
        out.append(rec)

    order = [(e, 'house') for e in qlib.CORE + list(qlib.SYSTEM)] + [(e, 'shared') for e in qlib.SHARED]
    for edid, lane in order:
        q = d.q_by_edid[edid]
        infos = d.infos_by_quest.get(q['ref'], [])
        itopic = {i['ref']: i for i in infos}
        for s in qlib.quest_scripts(q, infos):
            if s['kind'] == 'quest':
                name = s.get('script_edid') or f'{edid} quest script'
                disp = name
            elif s['kind'] == 'stage':
                disp = f'{edid} stage {s["stage"]}'
            else:
                i = itopic[s['info']]
                disp = f'{edid} info {s["info"].split(":")[1]} {s["kind"].split("-")[1]}'
            # a quest script is also a standalone SCPT record: key it by script ref so it is counted once
            rid = f'obj|{s["script_ref"]}' if s['kind'] == 'quest' and s.get('script_ref') else f'{edid}|{s["where"]}'
            rec = dict(id=rid, lane=lane, quest=edid, kind=s['kind'], where=s['where'], script=disp,
                       script_ref=s.get('script_ref'), source=s['source'], refs=s['refs'], via='own')
            if s['kind'].startswith('info'):
                i = itopic[s['info']]
                t = d.d_by_ref.get(i.get('topic'))
                rec['info'] = s['info']
                rec['topic'] = (t or {}).get('edid')
                rec['prompt'] = (i.get('prompt') or '')[:80]
                rec['text'] = ((i.get('responses') or [{}])[0].get('text') or '')[:80]
            add(rec)
        # dependency scripts (same set classify.rollup folds into the quest's risk)
        touched = extra['touched'].get(q['ref'])
        touched = None if touched is None else set(touched) | {t['ref'] for o in q['objectives'] for t in o['targets']}
        for dr in q.get('dep_refs', ()):
            if touched is not None and dr not in touched:
                continue
            for label, sref, sc in obj_scripts_for(dr, extra):
                add(dict(id=f'obj|{sref}', lane=lane, quest=edid, kind='object', where=label, script=display_name(d, label, sref, 'obj'),
                         script_ref=sref, source=sc['source'], refs=sc.get('refs', []), via=f'dep_ref {dr}'))
        # one hop through the refs the quest's own scripts name (house lane only: e.g. Lucky38MrHouseREF -> MrHouseSCRIPT)
        if lane == 'house':
            for rec in [r for r in out if r['quest'] == edid and r['via'] == 'own']:
                for r in rec['refs']:
                    for label, sref, sc in obj_scripts_for(r, extra):
                        add(dict(id=f'obj|{sref}', lane=lane, quest=edid, kind='object', where=label,
                                 script=display_name(d, label, sref, 'obj'), script_ref=sref, source=sc['source'],
                                 refs=sc.get('refs', []), via=f'script ref {r}'))
    # drivers: standalone scripts that set a House quest's stage
    for edid in qlib.CORE + list(qlib.SYSTEM):
        for sref, s in qlib.find_driver_scripts(d, [edid]).items():
            add(dict(id=f'obj|{sref}', lane='house', quest=edid, kind='driver', where='driver', script=s['edid'], script_ref=sref,
                     source=s['script']['source'], refs=s['script'].get('refs', []), via=f'drives {edid}'))
    for nm in EXTRA_SHARED_SCRIPTS:
        s = d.s_by_edid.get(nm.lower())
        if s:
            add(dict(id=f'obj|{s["ref"]}', lane='shared', quest='VEnding', kind='object', where='ending', script=s['edid'],
                     script_ref=s['ref'], source=s['script']['source'], refs=s['script'].get('refs', []), via='ending infrastructure'))
    return out
