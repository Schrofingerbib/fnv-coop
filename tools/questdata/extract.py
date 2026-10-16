"""Extract QUST/DIAL/INFO/SCPT/GLOB/FACT/REPU from FNV master files into compact JSON.

Usage: python extract.py [--data DIR] [--out DIR] [--plugins A.esm,B.esm]
Reads game files READ-ONLY (mmap). Outputs under --out:
  quests.json        meta + quests + globals + factions + reputations (compact)
  scripts.json.gz    standalone SCPT records
  dials.json.gz      DIAL topics
  infos.jsonl.gz     INFO records, one per line
"""
import argparse
import gzip
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from esm import EsmFile, ParseError  # noqa: E402
from ctda_funcs import FUNCS  # noqa: E402
import forms as forms_mod  # noqa: E402

DEFAULT_DATA = r'C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data'
PLUGINS = ['FalloutNV.esm', 'DeadMoney.esm', 'HonestHearts.esm', 'OldWorldBlues.esm', 'LonesomeRoad.esm',
           'GunRunnersArsenal.esm', 'ClassicPack.esm', 'MercenaryPack.esm', 'TribalPack.esm', 'CaravanPack.esm']
WANT = {'QUST', 'DIAL', 'INFO', 'SCPT', 'GLOB', 'FACT', 'REPU', 'TERM', 'PACK'}

FORM_PT = {'ptForm', 'ptActor', 'ptActorBase', 'ptArmor', 'ptBaseEffect', 'ptBaseObject', 'ptCasino', 'ptCell',
           'ptChallenge', 'ptClass', 'ptEffectItem', 'ptEncounterZone', 'ptFaction', 'ptFormList', 'ptFurniture',
           'ptGlobal', 'ptIdleForm', 'ptInventoryObject', 'ptNote', 'ptOwner', 'ptPackage', 'ptPerk', 'ptQuest',
           'ptRace', 'ptReference', 'ptRegion', 'ptReputation', 'ptVoiceType', 'ptWeapon', 'ptWeather',
           'ptWorldspace'}
OPS = ['==', '!=', '>', '>=', '<', '<=']
RUN_ON = ['subject', 'target', 'reference', 'combat_target', 'linked_ref']
VAR_TYPES = {0: 'float_or_ref', 1: 'int', 2: 'string', 3: 'array', 4: 'ref'}
QUEST_FLAGS = ['start_enabled', 'u2', 'allow_repeated_topics', 'allow_repeated_stages', 'default_delay']
INFO_F1 = ['goodbye', 'random', 'say_once', 'run_immediately', 'info_refusal', 'random_end', 'run_for_rumors',
           'speech_challenge']
INFO_F2 = ['say_once_a_day', 'always_darken', 'unk2', 'unk3', 'low_intelligence', 'high_intelligence']
INFO_TYPES = ['topic', 'conversation', 'combat', 'persuasion', 'detection', 'service', 'misc', 'radio']
SPEECH = ['none', 'very_easy', 'easy', 'average', 'hard', 'very_hard']


def z(b):
    return b.split(b'\0')[0].decode('cp1252')


def u32(b, o=0):
    if o + 4 > len(b):
        raise ParseError('subrecord too short for u32')
    return struct.unpack_from('<I', b, o)[0]


def flags_of(v, names):
    return [n for i, n in enumerate(names) if v >> i & 1]


def parse_ctda(x, ref):
    if len(x) < 20:
        raise ParseError(f'CTDA too short ({len(x)})')
    t = x[0]
    fn = struct.unpack_from('<H', x, 8)[0]
    name, p1t, p2t = FUNCS.get(fn, (f'func_{fn}', None, None))
    cmpv = ref(u32(x, 4)) if t & 4 else struct.unpack_from('<f', x, 4)[0]
    p1, p2 = u32(x, 12), u32(x, 16)
    c = {'op': OPS[min(t >> 5, 5)], 'value': cmpv, 'func': name, 'fn': fn,
         'p1': ref(p1) if p1t in FORM_PT else p1, 'p2': ref(p2) if p2t in FORM_PT else p2}
    if t & 1:
        c['or'] = True
    ro = u32(x, 20) if len(x) >= 24 else (1 if t & 2 else 0)
    c['run_on'] = RUN_ON[ro] if ro < len(RUN_ON) else ro
    if len(x) >= 28:
        r = u32(x, 24)
        if r or ro == 2:
            c['ref'] = ref(r)
    return c


SCRIPT_SUBS = ('SCDA', 'SCTX', 'SLSD', 'SCVR', 'SCRO', 'SCRV')


def parse_script(subs, i, ref):
    """subs[i] must be SCHR. Returns (script dict, next index)."""
    h = subs[i][1]
    if len(h) != 20:
        raise ParseError(f'SCHR size {len(h)}')
    sc = {'type': {0: 'object', 1: 'quest', 0x100: 'effect'}.get(struct.unpack_from('<H', h, 16)[0], 'other'),
          'compiled_size': u32(h, 8), 'source': '', 'vars': [], 'refs': [], 'rvars': []}
    j = i + 1
    while j < len(subs) and subs[j][0] in SCRIPT_SUBS:
        s, d = subs[j]
        if s == 'SCTX':
            sc['source'] = d.rstrip(b'\0').decode('cp1252')
        elif s == 'SLSD':
            sc['vars'].append({'idx': u32(d), 'type': VAR_TYPES.get(d[16] if len(d) > 16 else 0, '?'), 'name': None})
        elif s == 'SCVR':
            if sc['vars']:
                sc['vars'][-1]['name'] = z(d)
        elif s == 'SCRO':
            sc['refs'].append(ref(u32(d)))
        elif s == 'SCRV':
            sc['rvars'].append(u32(d))
        j += 1
    return sc, j


def slim(sc):
    """Drop truly empty scripts (no source, nothing compiled)."""
    return sc if sc and (sc['source'].strip() or sc['compiled_size']) else None


def parse_qust(rec, ref):
    subs = rec.subrecords()
    q = {'formid': f'{rec.formid:08X}', 'conditions': [], 'stages': [], 'objectives': []}
    stage = entry = obj = None
    i = 0
    while i < len(subs):
        s, x = subs[i]
        if s == 'EDID':
            q['edid'] = z(x)
        elif s == 'FULL':
            q['name'] = z(x)
        elif s == 'SCRI':
            q['script'] = ref(u32(x))
        elif s == 'DATA':
            q['flags'] = flags_of(x[0], QUEST_FLAGS)
            q['priority'] = x[1] if len(x) > 1 else 0
            q['delay'] = struct.unpack_from('<f', x, 4)[0] if len(x) >= 8 else None
        elif s == 'INDX':
            stage = {'index': struct.unpack('<h', x[:2])[0], 'entries': []}
            q['stages'].append(stage)
            entry = obj = None
        elif s == 'QSDT':
            if stage is None:
                raise ParseError(f'QUST {rec.formid:08X}: QSDT before INDX')
            entry = {'flags': flags_of(x[0], ['complete_quest', 'fail_quest']), 'conditions': []}
            stage['entries'].append(entry)
        elif s == 'CNAM' and entry is not None:
            entry['text'] = z(x)
        elif s == 'SCHR' and entry is not None and obj is None:
            sc, i = parse_script(subs, i, ref)
            entry['script'] = slim(sc)
            continue
        elif s == 'NAM0' and entry is not None:
            entry['next_quest'] = ref(u32(x))
        elif s == 'QOBJ':
            obj = {'index': struct.unpack('<i', x[:4])[0], 'targets': []}
            q['objectives'].append(obj)
            stage = entry = None
        elif s == 'NNAM' and obj is not None:
            obj['text'] = z(x)
        elif s == 'QSTA' and obj is not None:
            obj['targets'].append({'ref': ref(u32(x)), 'conditions': []})
        elif s in ('CTDA', 'CTDT'):
            c = parse_ctda(x, ref)
            if entry is not None:
                entry['conditions'].append(c)
            elif obj is not None and obj['targets']:
                obj['targets'][-1]['conditions'].append(c)
            else:
                q['conditions'].append(c)
        i += 1
    return q


def parse_info(rec, ref):
    subs = rec.subrecords()
    f = {'formid': f'{rec.formid:08X}', 'topic': ref(rec.parent) if rec.parent else None, 'conditions': [],
         'responses': [], 'add_topics': [], 'link_to': [], 'link_from': [], 'follow_up': [], 'begin': None, 'end': None}
    seen_next = False
    i = 0
    while i < len(subs):
        s, x = subs[i]
        if s == 'DATA':
            f['type'] = INFO_TYPES[x[0]] if x[0] < 8 else x[0]
            f['next_speaker'] = x[1] if len(x) > 1 else 0
            f['flags'] = flags_of(x[2], INFO_F1) if len(x) > 2 else []
            f['flags'] += flags_of(x[3], INFO_F2) if len(x) > 3 else []
        elif s == 'QSTI':
            f['quest'] = ref(u32(x))
        elif s == 'PNAM':
            f['prev_info'] = ref(u32(x))
        elif s == 'NAME':
            f['add_topics'].append(ref(u32(x)))
        elif s == 'TCLT':
            f['link_to'].append(ref(u32(x)))
        elif s == 'TCLF':
            f['link_from'].append(ref(u32(x)))
        elif s == 'TCFU':
            f['follow_up'].append(ref(u32(x)))
        elif s == 'TRDT':
            f['responses'].append({'emotion': u32(x), 'value': struct.unpack_from('<i', x, 4)[0] if len(x) >= 8 else 0})
        elif s == 'NAM1':
            if not f['responses']:
                f['responses'].append({})
            f['responses'][-1]['text'] = z(x)
        elif s == 'NAM2' and f['responses']:
            f['responses'][-1]['notes'] = z(x)
        elif s in ('CTDA', 'CTDT'):
            f['conditions'].append(parse_ctda(x, ref))
        elif s == 'NEXT':
            seen_next = True
        elif s == 'SCHR':
            sc, i = parse_script(subs, i, ref)
            f['end' if seen_next else 'begin'] = slim(sc)
            continue
        elif s == 'ANAM':
            f['speaker'] = ref(u32(x))
        elif s == 'RNAM':
            f['prompt'] = z(x)
        elif s == 'DNAM':
            v = u32(x)
            f['speech_challenge'] = SPEECH[v] if v < 6 else v
        elif s == 'KNAM':
            f['skill_perk'] = ref(u32(x))
        i += 1
    return f


def parse_dial(rec, ref):
    d = {'formid': f'{rec.formid:08X}', 'quests': []}
    for s, x in rec.subrecords():
        if s == 'EDID':
            d['edid'] = z(x)
        elif s == 'QSTI':
            d['quests'].append(ref(u32(x)))
        elif s == 'FULL':
            d['name'] = z(x)
        elif s == 'PNAM':
            d['priority'] = struct.unpack_from('<f', x)[0]
        elif s == 'DATA':
            d['type'] = INFO_TYPES[x[0]] if x[0] < 8 else x[0]
            d['flags'] = flags_of(x[1], ['rumors', 'top_level']) if len(x) > 1 else []
    return d


def parse_scpt(rec, ref):
    s_ = {'formid': f'{rec.formid:08X}'}
    subs = rec.subrecords()
    for i, (s, x) in enumerate(subs):
        if s == 'EDID':
            s_['edid'] = z(x)
        elif s == 'SCHR':
            s_['script'], _ = parse_script(subs, i, ref)
            break
    return s_


def parse_simple(rec, ref):
    o = {'formid': f'{rec.formid:08X}'}
    for s, x in rec.subrecords():
        if s == 'EDID':
            o['edid'] = z(x)
        elif s == 'FULL':
            o['name'] = z(x)
        elif s == 'FNAM' and rec.sig == 'GLOB':
            o['type'] = chr(x[0])
        elif s == 'FLTV':
            o['value'] = struct.unpack_from('<f', x)[0]
        elif s == 'DATA' and rec.sig == 'REPU':
            o['value'] = struct.unpack_from('<f', x)[0]
        elif s == 'WMI1':
            o['reputation'] = ref(u32(x))
    return o


def parse_term(rec, ref):
    t = {'formid': f'{rec.formid:08X}', 'items': []}
    subs = rec.subrecords()
    item = None
    i = 0
    while i < len(subs):
        s, x = subs[i]
        if s == 'EDID':
            t['edid'] = z(x)
        elif s == 'FULL':
            t['name'] = z(x)
        elif s == 'SCRI':
            t['script_ref'] = ref(u32(x))
        elif s == 'ITXT':
            item = {'text': z(x), 'script': None, 'conditions': []}
            t['items'].append(item)
        elif s == 'RNAM' and item is not None:
            item['result_text'] = z(x)
        elif s == 'TNAM' and item is not None:
            item['submenu'] = ref(u32(x))
        elif s == 'INAM' and item is not None:
            item['note'] = ref(u32(x))
        elif s == 'SCHR' and item is not None:
            sc, i = parse_script(subs, i, ref)
            item['script'] = slim(sc)
            continue
        elif s in ('CTDA', 'CTDT') and item is not None:
            item['conditions'].append(parse_ctda(x, ref))
        i += 1
    return t


def parse_pack(rec, ref):
    p = {'formid': f'{rec.formid:08X}', 'scripts': {}}
    subs = rec.subrecords()
    cur = None
    i = 0
    while i < len(subs):
        s, x = subs[i]
        if s == 'EDID':
            p['edid'] = z(x)
        elif s in ('POBA', 'POEA', 'POCA'):
            cur = {'POBA': 'begin', 'POEA': 'end', 'POCA': 'change'}[s]
        elif s == 'SCHR' and cur:
            sc, i = parse_script(subs, i, ref)
            sc = slim(sc)
            if sc:
                p['scripts'][cur] = sc
            continue
        i += 1
    return p


PARSERS = {'QUST': parse_qust, 'INFO': parse_info, 'DIAL': parse_dial, 'SCPT': parse_scpt,
           'GLOB': parse_simple, 'FACT': parse_simple, 'REPU': parse_simple,
           'TERM': parse_term, 'PACK': parse_pack}


def extract_plugin(path):
    """Parse one plugin. Returns (name, masters, {sig: [objs]}, stats)."""
    e = EsmFile.open(path)
    try:
        ref = e.resolver()
        out = {k: [] for k in PARSERS}
        stats = {k: {'records': 0, 'new': 0, 'override': 0, 'deleted': 0} for k in PARSERS}
        for rec in e.records(WANT):
            st = stats[rec.sig]
            st['records'] += 1
            if rec.deleted:
                st['deleted'] += 1
                continue
            own = (rec.formid >> 24) >= len(e.masters)
            st['new' if own else 'override'] += 1
            o = PARSERS[rec.sig](rec, ref)
            o['ref'] = ref(rec.formid)
            o['plugin'] = e.name
            if not own:
                o['override'] = True
            out[rec.sig].append(o)
        return e.name, e.masters, out, stats
    finally:
        e.close()


def script_counts(out):
    stage_sc = [en['script'] for q in out['QUST'] for s in q['stages'] for en in s['entries'] if en.get('script')]
    return {'SCPT': len(out['SCPT']), 'TERM': len(out['TERM']), 'PACK': len(out['PACK']),
            'pack_scripts': sum(len(p['scripts']) for p in out['PACK']),
            'term_item_scripts': sum(1 for t in out['TERM'] for i in t['items'] if i['script']),
            'quest_script_refs': sum(1 for q in out['QUST'] if q.get('script')),
            'stage_scripts': len(stage_sc),
            'stage_scripts_with_source': sum(1 for s in stage_sc if s['source'].strip()),
            'info_scripts': sum(1 for f in out['INFO'] for k in ('begin', 'end') if f[k])}


def write_json(path, obj, gz=False):
    data = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if gz:
        with gzip.open(path, 'wb', compresslevel=6) as f:
            f.write(data)
    else:
        with open(path, 'wb') as f:
            f.write(data)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=DEFAULT_DATA)
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'out', 'questdata'))
    ap.add_argument('--plugins', default=','.join(PLUGINS))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    merged = {k: [] for k in PARSERS}
    meta = {'plugins': {}}
    for name in a.plugins.split(','):
        p = os.path.join(a.data, name)
        if not os.path.exists(p):
            print('skip (missing)', name)
            continue
        t = time.time()
        nm, masters, out, stats = extract_plugin(p)
        sc = script_counts(out)
        meta['plugins'][nm] = {'masters': masters, 'stats': stats, 'scripts': sc,
                               'stages': sum(len(q['stages']) for q in out['QUST'])}
        print(f'{nm}: {time.time() - t:.1f}s  QUST new/ovr {stats["QUST"]["new"]}/{stats["QUST"]["override"]}  '
              f'stages {meta["plugins"][nm]["stages"]}  INFO {stats["INFO"]["records"]}  DIAL {stats["DIAL"]["records"]}  '
              f'scripts {sc}', flush=True)
        for k in merged:
            merged[k].extend(out[k])
    # link INFO/DIAL to quests and quest scripts
    qidx = {q['ref']: q for q in merged['QUST'] if not q.get('override')}
    for q in qidx.values():
        q['infos'], q['topics'] = [], []
    for f in merged['INFO']:
        q = qidx.get(f.get('quest'))
        if q is not None:
            q['infos'].append(f['ref'])
    for d in merged['DIAL']:
        for qr in d['quests']:
            q = qidx.get(qr)
            if q is not None:
                q['topics'].append(d['ref'])
    scpt = {s['ref']: s for s in merged['SCPT']}
    for q in qidx.values():
        sr = q.get('script')
        if sr and sr in scpt and 'script' in scpt[sr]:
            q['script_data'] = {'edid': scpt[sr].get('edid'), **scpt[sr]['script']}
    # objective-target / script-referenced forms (for risk roll-up of NPC/terminal/package scripts)
    for q in qidx.values():
        deps = {t['ref'] for o in q['objectives'] for t in o['targets'] if t['ref']}
        scs = ([q['script_data']] if q.get('script_data') else []) +               [e['script'] for st in q['stages'] for e in st['entries'] if e.get('script')]
        for sc in scs:
            deps.update(r for r in sc['refs'] if r)
        deps.discard(q['ref'])
        q['dep_refs'] = sorted(deps)
    allrefs = {r for q in qidx.values() for r in q['dep_refs']}
    paths = [os.path.join(a.data, n) for n in a.plugins.split(',') if os.path.exists(os.path.join(a.data, n))]
    forms = forms_mod.lookup(paths, allrefs)
    forms.update(forms_mod.lookup(paths, {f['base'] for f in forms.values() if f.get('base')} - set(forms)))
    meta['totals'] = {
        'quests': len(qidx), 'quest_overrides': sum(1 for q in merged['QUST'] if q.get('override')),
        'stages': sum(len(q['stages']) for q in merged['QUST']),
        'stage_log_entries': sum(len(s['entries']) for q in merged['QUST'] for s in q['stages']),
        'objectives': sum(len(q['objectives']) for q in merged['QUST']),
        'infos': len(merged['INFO']), 'dials': len(merged['DIAL']), 'scpt': len(merged['SCPT']),
        'terms': len(merged['TERM']), 'packs': len(merged['PACK']), 'forms_resolved': len(forms),
        'globals': len(merged['GLOB']), 'factions': len(merged['FACT']), 'reputations': len(merged['REPU'])}
    print('TOTALS', json.dumps(meta['totals']))
    write_json(os.path.join(a.out, 'quests.json'), {
        'meta': meta, 'quests': list(qidx.values()), 'quest_overrides': [q for q in merged['QUST'] if q.get('override')],
        'globals': merged['GLOB'], 'factions': merged['FACT'], 'reputations': merged['REPU']})
    write_json(os.path.join(a.out, 'scripts.json.gz'), merged['SCPT'], gz=True)
    write_json(os.path.join(a.out, 'dials.json.gz'), merged['DIAL'], gz=True)
    write_json(os.path.join(a.out, 'terms.json.gz'), merged['TERM'], gz=True)
    write_json(os.path.join(a.out, 'packs.json.gz'), merged['PACK'], gz=True)
    write_json(os.path.join(a.out, 'forms.json.gz'), forms, gz=True)
    with gzip.open(os.path.join(a.out, 'infos.jsonl.gz'), 'wb', compresslevel=6) as f:
        for i in merged['INFO']:
            f.write(json.dumps(i, separators=(',', ':'), ensure_ascii=False).encode('utf-8') + b'\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
