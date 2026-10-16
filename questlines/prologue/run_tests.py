"""Tests + mutation twin for the prologue questline.

    python -I questlines/prologue/run_tests.py        -> prints ALL OK and exits 0

Every validator is a pure function over the generated JSON that returns a list of error strings. The real data must produce no
errors; every mutant (a deliberately broken copy) must produce at least one, otherwise the test itself is broken.
"""
import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build  # noqa: E402
import funcmap  # noqa: E402
import gaps as gaps_mod  # noqa: E402
import model  # noqa: E402
import policy  # noqa: E402
import sweep as sweep_mod  # noqa: E402

REPO = model.REPO
FILES = ['inventory.json', 'sweep.json', 'gaps.json', 'gaps.md', 'dialogue_specs.json', 'dialogue.md', 'rules.patch.json',
         'policy_review.md']
CMD_RE = re.compile(r'^(startquest|setstage) ([0-9A-F]{8})(?: (\d+))?$')
SETVAR_RE = re.compile(r'^set (\w+)\.(\w+) to (-?\d+)$')
STATUS = {'supported', 'partial', 'missing', 'n/a'}


def load(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return json.load(f)


class Ctx:
    """Resolvers over the extracted game data (quests.json, forms.json.gz, refnames.json.gz)."""

    def __init__(self):
        D = model.data()
        self.D = D
        self.q_by_edid = D['by_edid']
        self.q_by_hex = {model.hexid(q['ref']): q for q in D['quests']}
        names = {}
        for q in D['quests']:
            if q.get('edid'):
                names[q['edid'].lower()] = ('quests.json', q['ref'])
        for src, tag in ((D['forms'], 'forms.json.gz'), (D['refnames'], 'refnames.json.gz')):
            for ref, v in src.items():
                if v.get('edid'):
                    names.setdefault(v['edid'].lower(), (tag, ref))
        self.names = names
        self.refs = set(D['forms']) | set(D['refnames']) | {q['ref'] for q in D['quests']}
        ef = open(os.path.join(REPO, 'synccore', 'include', 'fnvcoop', 'effect.h'), encoding='utf-8').read()
        m = re.search(r'using Effect = std::variant<([^>]+)>', ef)
        self.effects = {x.strip() for x in m.group(1).split(',')}
        self.oracle_only = {'CompleteQuest', 'FailQuest', 'CompleteAllObjectives', 'SetFactionRank', 'AddPerk'}
        rules_py = open(os.path.join(REPO, 'tools', 'coverage', 'rules.py'), encoding='utf-8').read()
        doc = rules_py.split('Kinds (runtime vocabulary', 1)[1].split('"""', 1)[0]
        self.rule_kinds = set(re.findall(r'^  ([A-Z]\w+)\s+\S', doc, re.M))
        self.script_src = {}
        for s in model.collect_scripts() + model.driver_scripts():
            self.script_src[gaps_mod.script_label(s)] = s['source']

    def resolves(self, edid):
        return edid.lower() in self.names


def v_inventory(inv, cx):
    err = []
    D = cx.D
    if [q['edid'] for q in inv['quests']] != list(model.SCOPE):
        err.append('inventory quests != SCOPE')
    for q in inv['quests']:
        gq = cx.q_by_edid.get(q['edid'])
        if gq is None:
            err.append('%s not in quests.json' % q['edid'])
            continue
        if q['formid'] != model.hexid(gq['ref']) or q['ref'] != gq['ref']:
            err.append('%s formid mismatch' % q['edid'])
        if sorted(s['index'] for s in q['stages']) != sorted({s['index'] for s in gq['stages']}):
            err.append('%s stage list differs from quests.json' % q['edid'])
        if sorted(o['index'] for o in q['objectives']) != sorted(o['index'] for o in gq['objectives']):
            err.append('%s objective list differs from quests.json' % q['edid'])
        allsrc = model.all_script_sources()
        for s in q['stages']:
            for w in s.get('set_by', []):
                src = allsrc.get(w['script'])
                if src is None or not (1 <= w['line'] <= len(src.splitlines())) or 'setstage' not in src.splitlines()[w['line'] - 1].lower():
                    err.append('%s stage %s: writer %s:%s is not a SetStage line' % (q['edid'], s['index'], w['script'], w['line']))
            if s['sweep'] not in ('swept', 'excluded'):
                err.append('%s stage %s has no sweep status' % (q['edid'], s['index']))
            if s['sweep'] == 'excluded' and not s['excluded_reason']:
                err.append('%s stage %s excluded without a reason' % (q['edid'], s['index']))
        if q['dialogue']['topics_total'] != len(gq['topics']):
            err.append('%s topic count differs' % q['edid'])
        for e in q['prerequisites']['driven_by'] + q['prerequisites']['reads_state_of'] + q['prerequisites']['drives_or_gates']:
            if e['quest'] not in cx.q_by_edid:
                err.append('%s prerequisite quest %s unknown' % (q['edid'], e['quest']))
        if q['risk']['tier'] != D['risk'][gq['ref']]['tier']:
            err.append('%s tier mismatch' % q['edid'])
    for r in inv['reference_done'] + inv['gate_only']:
        if r['edid'] not in cx.q_by_edid:
            err.append('reference %s unknown' % r['edid'])
    s = inv['summary']
    if s['stages'] != sum(len(q['stages']) for q in inv['quests']) or s['stages_swept'] + s['stages_excluded'] != s['stages']:
        err.append('inventory summary inconsistent')
    return err


def v_sweep(sw, inv, cx):
    err = []
    inv_by = {q['edid']: q for q in inv['quests']}
    seen = set()
    for q in sw['quests']:
        gq = cx.q_by_edid.get(q['edid'])
        if gq is None or q['ref'] != gq['ref'] or q['formid'] != model.hexid(gq['ref']):
            err.append('sweep quest %s does not resolve' % q['edid'])
            continue
        valid = {s['index'] for s in gq['stages']}
        sw_idx = [s['index'] for s in q['stages']]
        ex_idx = [s['index'] for s in q['excluded_stages']]
        if set(sw_idx) & set(ex_idx):
            err.append('%s: stage both swept and excluded' % q['edid'])
        if set(sw_idx) | set(ex_idx) != valid:
            err.append('%s: stages not covered/excluded: %s' % (q['edid'], sorted(valid - set(sw_idx) - set(ex_idx))))
        if (set(sw_idx) | set(ex_idx)) - valid:
            err.append('%s: sweep mentions unknown stages %s' % (q['edid'], sorted((set(sw_idx) | set(ex_idx)) - valid)))
        for x in q['excluded_stages']:
            if not x.get('reason'):
                err.append('%s stage %s excluded without reason' % (q['edid'], x['index']))
        for ist in inv_by[q['edid']]['stages']:
            want = 'swept' if ist['index'] in sw_idx else 'excluded'
            if ist['sweep'] != want:
                err.append('%s stage %s: inventory says %s, sweep says %s' % (q['edid'], ist['index'], ist['sweep'], want))
        cmds = list(q['setup'])
        for s in q['stages']:
            if s['console'] != 'setstage %s %d' % (q['formid'], s['index']):
                err.append('%s stage %s console line wrong' % (q['edid'], s['index']))
            cmds += s['setup']
            for v in s.get('variants', []):
                cmds += v['setup']
        for c in cmds:
            m = CMD_RE.match(c)
            if m:
                tq = cx.q_by_hex.get(m.group(2))
                if tq is None:
                    err.append('%s: command %r FormID is not a quest in quests.json' % (q['edid'], c))
                elif m.group(1) == 'setstage' and int(m.group(3)) not in {s['index'] for s in tq['stages']}:
                    err.append('%s: command %r stage not defined in %s' % (q['edid'], c, tq['edid']))
                continue
            m = SETVAR_RE.match(c)
            if m:
                tq = cx.q_by_edid.get(m.group(1))
                src = ((tq or {}).get('script_data') or {}).get('source', '')
                if tq is None or not re.search(r'(?im)^\s*(short|int|long|float)\s+%s\b' % re.escape(m.group(2)), src):
                    err.append('%s: command %r quest/variable does not exist' % (q['edid'], c))
                continue
            err.append('%s: unknown command form %r' % (q['edid'], c))
        for s in q['stages']:
            for c in s['setup']:
                m = CMD_RE.match(c)
                if m and m.group(1) == 'setstage' and int(m.group(3)) == s['index']:
                    err.append('%s: stage %s setup sets the stage under test' % (q['edid'], s['index']))
        seen.add(q['edid'])
    if seen != set(model.SCOPE):
        err.append('sweep quests != SCOPE')
    for r in sw['reset_quests']:
        if r not in cx.q_by_hex:
            err.append('reset quest %s unknown' % r)
    for need in ('edid', 'ref'):
        if any(need not in q for q in sw['quests']):
            err.append('m1 format key %s missing' % need)
    if any('index' not in s for q in sw['quests'] for s in q['stages']):
        err.append('m1 format: stages[].index missing')
    # scripted INFOs: exist, are in-scope, and have a coverage statement
    infos = {}
    for qn in model.SCOPE:
        for i in model.in_scope_infos(qn):
            if i.get('begin') or i.get('end'):
                infos[i['ref']] = qn
    got = {r['info'] for r in sw['infos']}
    if got != set(infos):
        err.append('sweep infos != scripted in-scope infos (%d vs %d)' % (len(got), len(infos)))
    for r in sw['infos']:
        if not r.get('covered_by'):
            err.append('info %s has no coverage statement' % r['info'])
    return err


def v_gaps(g, cx):
    err = []
    for x in g['by_function']:
        if x['effect'] is not None and x['effect'] not in cx.effects:
            err.append('%s: effect type %s is not in synccore effect.h' % (x['fn'], x['effect']))
        if x['capture'] not in STATUS | {'unknown'} or x['apply'] not in STATUS | {'unknown'}:
            err.append('%s: bad status' % x['fn'])
        if x['severity'] not in funcmap.SEVERITY_ORDER + ['unknown']:
            err.append('%s: bad severity' % x['fn'])
        if x['count'] != len(x['locations']):
            err.append('%s: count != locations' % x['fn'])
        for loc in x['locations']:
            src = cx.script_src.get(loc['script'])
            if src is None:
                err.append('%s: script %r not in the questline' % (x['fn'], loc['script']))
                continue
            lines = src.splitlines()
            if not (1 <= loc['line'] <= len(lines)):
                err.append('%s: %s:%d outside the script' % (x['fn'], loc['script'], loc['line']))
                continue
            text = model.classify.strip_comment(lines[loc['line'] - 1]).strip().rstrip(';').strip()[:140]
            if text != loc['text']:
                err.append('%s: %s:%d text does not match the script (%r vs %r)' % (x['fn'], loc['script'], loc['line'], loc['text'], text))
    s = g['summary']
    if sum(x['count'] for x in g['by_function']) != s['occurrences'] or s['occurrences'] != g['effects_found']:
        err.append('gap summary occurrences inconsistent')
    if sum(x['count'] for x in g['unsupported']) != s['unsupported_occurrences']:
        err.append('unsupported occurrence total inconsistent')
    if any(x['severity'] == 'unknown' for x in g['by_function']):
        err.append('unclassified functions: %s' % [x['fn'] for x in g['by_function'] if x['severity'] == 'unknown'])
    # every unsupported group states a reason
    if any(not x['note'] for x in g['unsupported']):
        err.append('unsupported group without a note')
    # the BRIEF checklist is complete and its locations are real
    tags = {c['tag'] for c in g['hazard_checklist']}
    for need in ('player-targeted-reward', 'spawn', 'companion', 'essential', 'move', 'control', 'menu', 'timer', 'radio', 'ending',
                 'kill-all'):
        if need not in tags:
            err.append('hazard checklist missing %s' % need)
    for c in g['hazard_checklist']:
        for loc in c['locations']:
            if loc['script'] not in cx.script_src:
                err.append('checklist %s: unknown script %s' % (c['tag'], loc['script']))
    # claim used by policy_review.md: no script moves the PLAYER
    for c in g['hazard_checklist']:
        if c['tag'] == 'move' and any(re.match(r'(?i)\s*player(ref)?\.', loc['text']) for loc in c['locations']):
            err.append('a script moves the player: policy_review.md claim "no player MoveTo" is false')
        if c['tag'] == 'control' and c['count']:
            err.append('control-lock hits found: policy_review.md claim "no control locks" is false')
    return err


def _edids_in(o):
    """Every string in a patch params tree that looks like an EditorID (not a keyword)."""
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ('markers', 'items', 'npcs', 'cells'):
                out += [x for x in v]
            elif k == 'forced':
                for f in v:
                    out += [f['npc'], f['trigger']]
            else:
                out += _edids_in(v)
    elif isinstance(o, list):
        for x in o:
            out += _edids_in(x)
    return out


def v_patch(p, cx):
    err = []
    D = cx.D
    kinds = set(p['kinds_existing'])
    if kinds != cx.rule_kinds or len(kinds) != 16:
        err.append('kinds_existing != the 16 kinds of tools/coverage/rules.py (%s)' % sorted(cx.rule_kinds ^ kinds))
    new = {k['kind'] for k in p['new_kinds']}
    for k in p['new_kinds']:
        for need in ('why', 'engine_work', 'params_schema'):
            if not k.get(need):
                err.append('new kind %s lacks %s' % (k['kind'], need))
    ext = {(e['kind'], e['param'].split('.')[0]) for e in p['param_extensions']}
    known_params = {}
    for qd in D['rules']['quests'].values():
        for r in qd['rules']:
            known_params.setdefault(r['kind'], set()).update(r['params'].keys())
    new_quests = {o['quest'] for o in p['ops'] if o['op'] == 'add_quest'}
    ids = [o['id'] for o in p['ops']]
    if len(ids) != len(set(ids)):
        err.append('duplicate op ids')
    for o in p['ops']:
        if o['quest'] not in cx.q_by_edid:
            err.append('%s: quest %s does not exist' % (o['id'], o['quest']))
            continue
        if o['kind'] not in kinds | new:
            err.append('%s: kind %s is neither one of the 16 nor a declared new kind' % (o['id'], o['kind']))
        rq = D['rules']['quests'].get(o['quest'])
        if o['op'] == 'merge_params':
            if not rq or not any(r['kind'] == o['kind'] for r in rq['rules']):
                err.append('%s: merge_params but %s has no %s rule' % (o['id'], o['quest'], o['kind']))
        elif o['op'] == 'add_quest':
            if rq is not None:
                err.append('%s: add_quest but %s already has an entry' % (o['id'], o['quest']))
            if not o.get('tier') or not o.get('questline'):
                err.append('%s: add_quest needs tier + questline' % o['id'])
        elif o['op'] == 'add_rule':
            if rq is None and o['quest'] not in new_quests:
                err.append('%s: add_rule on %s which has no entry and no add_quest' % (o['id'], o['quest']))
            if rq is not None and any(r['kind'] == o['kind'] for r in rq['rules']):
                err.append('%s: add_rule but %s already has %s (use merge_params)' % (o['id'], o['quest'], o['kind']))
        else:
            err.append('%s: unknown op %s' % (o['id'], o['op']))
        for pk in o['params']:
            if o['kind'] in kinds and pk not in known_params.get(o['kind'], set()) and (o['kind'], pk) not in ext:
                err.append('%s: param %s.%s is neither used in rules.json nor declared in param_extensions' % (o['id'], o['kind'], pk))
        for e in _edids_in(o['params']):
            if not cx.resolves(e):
                err.append('%s: EditorID %s does not resolve in quests.json / forms.json.gz / refnames.json.gz' % (o['id'], e))
        if o['kind'] == 'ExclusiveStages':
            valid = {s['index'] for s in cx.q_by_edid[o['quest']]['stages']}
            bad = [s for s in o['params']['stages'] if s not in valid]
            if bad:
                err.append('%s: stages %s not defined in %s' % (o['id'], bad, o['quest']))
        if o['status'] == 'proposed' and o['kind'] != 'InitiatorDialogue' and not o['evidence'] and not o['why'].startswith('completes'):
            err.append('%s: proposal without evidence' % o['id'])
    for q in p['open_owner_questions']:
        if not q['question']:
            err.append('open question without text')
    return err


def v_specs(doc, cx):
    err = []
    infos = {}
    import gzip
    for line in gzip.open(os.path.join(model.QD, 'infos.jsonl.gz'), 'rt', encoding='utf-8'):
        o = json.loads(line)
        infos[o['ref']] = o
    for sc in doc['scenarios']:
        if not sc['policy_checks'] or sc['kind'] != 'dialogue':
            err.append('%s: bad header' % sc['id'])
        for qn in sc['quests']:
            if qn not in cx.q_by_edid:
                err.append('%s: quest %s unknown' % (sc['id'], qn))
        acts = [s['action'] for s in sc['steps']]
        if 'talk' not in acts or 'pick' not in acts:
            err.append('%s: needs a talk and a pick' % sc['id'])
        for st in sc['steps']:
            if st['action'] == 'setup':
                for slot, cmds in st['players'].items():
                    if slot not in sc['players']:
                        err.append('%s: bad slot %s' % (sc['id'], slot))
                    for c in cmds:
                        m = CMD_RE.match(c)
                        if m and m.group(2) not in cx.q_by_hex:
                            err.append('%s: %r FormID is not a quest' % (sc['id'], c))
            elif st['action'] == 'talk':
                if st['npc_ref'] not in cx.refs:
                    err.append('%s: npc_ref %s does not resolve' % (sc['id'], st['npc_ref']))
                if model.edid_of(st['npc_ref']) != st['npc']:
                    err.append('%s: npc EditorID/ref mismatch' % sc['id'])
            elif st['action'] == 'pick':
                i = infos.get(st['info'])
                if i is None:
                    err.append('%s: INFO %s unknown' % (sc['id'], st['info']))
                    continue
                if i['topic'] != st['topic']:
                    err.append('%s: INFO %s is not in topic %s' % (sc['id'], st['info'], st['topic']))
                if not (i['responses'] and i['responses'][0]['text'].startswith(st['line'])):
                    err.append('%s: line %r is not the start of INFO %s' % (sc['id'], st['line'], st['info']))
                for qn, n in st['expect'].get('stage', {}).items():
                    q = cx.q_by_edid.get(qn)
                    if q is None or n not in {s['index'] for s in q['stages']}:
                        err.append('%s: expected stage %s %s does not exist' % (sc['id'], qn, n))
    return err


def v_files(rendered):
    err = []
    for name in FILES:
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            err.append('%s missing (run build.py)' % name)
            continue
        with open(p, encoding='utf-8', newline='') as f:
            if f.read() != rendered[name]:
                err.append('%s is stale: re-run questlines/prologue/build.py' % name)
    return err


def v_text(texts, patch):
    """Every INFO id and proposal id quoted in the prose files exists."""
    import gzip
    err = []
    infos = set()
    for line in gzip.open(os.path.join(model.QD, 'infos.jsonl.gz'), 'rt', encoding='utf-8'):
        infos.add(json.loads(line)['ref'].split(':')[1])
    ids = {o['id'] for o in patch['ops']}
    for name in ('dialogue.md', 'policy_review.md'):
        txt = texts[name]
        for grp in re.findall(r'INFOs? ((?:[0-9A-F]{6}[ /,]*)+)', txt):
            for i in re.findall(r'[0-9A-F]{6}', grp):
                if i not in infos:
                    err.append('%s quotes unknown INFO %s' % (name, i))
        for m in re.findall(r'\bP(\d\d)\b', txt):
            if 'P' + m not in ids:
                err.append('%s quotes unknown proposal P%s' % (name, m))
    return err


def v_doc():
    """internal questline notes must quote the same counts as the generated data."""
    p = os.path.join(REPO, 'docs', 'questlines', 'prologue.md')
    if not os.path.exists(p):
        return ['internal questline notes missing']
    text = open(p, encoding='utf-8').read()
    inv, sw, g = load('inventory.json'), load('sweep.json'), load('gaps.json')
    want = ('Counts: %d quests, %d stages (%d swept, %d excluded), %d scripted INFOs, %d scripts / %d statements scanned, '
            '%d unsupported functions (%d occurrences)' % (
                inv['summary']['quests'], inv['summary']['stages'], sw['summary']['stages_swept'], sw['summary']['stages_excluded'],
                sw['summary']['scripted_infos'], g['scripts_scanned'], g['statements_scanned'], g['summary']['unsupported_functions'],
                g['summary']['unsupported_occurrences']))
    return [] if want in text else ['status doc does not contain the line: ' + want]


# ---- mutants ---------------------------------------------------------------------------------------------------------
def mutants(inv, sw, g, patch, specs, rendered):
    out = []

    def m(name, validator, obj_name, fn):
        out.append((name, validator, obj_name, fn))

    def drop_stage(o):
        o['quests'][0]['stages'].pop()
    m('sweep: a swept stage silently dropped', 'sweep', 'sw', drop_stage)

    def bad_hex(o):
        o['quests'][0]['stages'][0]['setup'][0] = 'startquest 000842DE'
    m('sweep: FormID that is not a quest', 'sweep', 'sw', bad_hex)

    def bad_stage(o):
        o['quests'][0]['stages'][1]['setup'].append('setstage 000842DD 999')
    m('sweep: setstage to an undefined stage', 'sweep', 'sw', bad_stage)

    def no_reason(o):
        o['quests'][-1]['excluded_stages'][0]['reason'] = ''
    m('sweep: exclusion without reason', 'sweep', 'sw', no_reason)

    def bad_var(o):
        for s in o['quests'][0]['stages']:
            for v in s.get('variants', []):
                v['setup'] = ['set VMQ01.bNoSuchVar to 1']
    m('sweep: quest variable that does not exist', 'sweep', 'sw', bad_var)

    def drop_info(o):
        o['infos'].pop()
    m('sweep: scripted INFO not listed', 'sweep', 'sw', drop_info)

    def line_off(o):
        o['by_function'][0]['locations'][0]['line'] += 1
    m('gaps: location line number off by one', 'gaps', 'g', line_off)

    def bad_effect(o):
        for x in o['by_function']:
            if x['effect'] == 'SetStage':
                x['effect'] = 'SetStages'
    m('gaps: effect type not in effect.h', 'gaps', 'g', bad_effect)

    def bad_status(o):
        o['by_function'][0]['capture'] = 'maybe'
    m('gaps: invalid status word', 'gaps', 'g', bad_status)

    def lose_hit(o):
        o['by_function'][0]['locations'].pop()
    m('gaps: a hit silently lost', 'gaps', 'g', lose_hit)

    def fake_move(o):
        for c in o['hazard_checklist']:
            if c['tag'] == 'move':
                c['locations'].append(dict(c['locations'][0], text='player.MoveTo SomeMarker'))
    m('gaps: a player MoveTo appears (policy claim false)', 'gaps', 'g', fake_move)

    def bogus_kind(o):
        o['ops'][0]['kind'] = 'Bogus'
    m('patch: unknown rule kind', 'patch', 'patch', bogus_kind)

    def bad_item(o):
        o['ops'][1]['params']['items'].append('NoSuchNoteEdid')
    m('patch: EditorID that does not resolve', 'patch', 'patch', bad_item)

    def merge_missing(o):
        for x in o['ops']:
            if x['op'] == 'merge_params':
                x['quest'] = 'VMS16'
                x['kind'] = 'CompanionOwner'
                break
    m('patch: merge_params into a rule that is not there', 'patch', 'patch', merge_missing)

    def bad_excl(o):
        for x in o['ops']:
            if x['kind'] == 'ExclusiveStages':
                x['params']['stages'] = [110, 999]
                break
    m('patch: ExclusiveStages with an undefined stage', 'patch', 'patch', bad_excl)

    def no_evidence(o):
        o['ops'][0]['evidence'] = []
    m('patch: proposal without evidence', 'patch', 'patch', no_evidence)

    def bad_line(o):
        o['scenarios'][0]['steps'][-1]['line'] = 'Completely different words'
    m('specs: line text that is not in the INFO', 'specs', 'specs', bad_line)

    def bad_info(o):
        o['scenarios'][0]['steps'][-1]['info'] = 'FalloutNV.esm:FFFFFF'
    m('specs: INFO that does not exist', 'specs', 'specs', bad_info)

    def bad_expect(o):
        o['scenarios'][0]['steps'][-1]['expect'].setdefault('stage', {})['VMQ01'] = 77
    m('specs: expected stage that does not exist', 'specs', 'specs', bad_expect)

    def fake_writer(o):
        o['quests'][0]['stages'][0]['set_by'] = [dict(script='VMQ01 stage 10', line=1, text='x')]
    m('inventory: stage writer that is not a SetStage line', 'inv', 'inv', fake_writer)

    def drop_obj(o):
        o['quests'][0]['objectives'].pop()
    m('inventory: objective missing', 'inv', 'inv', drop_obj)

    def drop_stage_inv(o):
        o['quests'][0]['stages'].pop(0)
    m('inventory: stage missing', 'inv', 'inv', drop_stage_inv)

    def bad_info_text(o):
        o['dialogue.md'] = o['dialogue.md'] + ' see INFO FFFFFF here'
    m('text: prose quotes an INFO that does not exist', 'text', 'rendered', bad_info_text)

    def bad_pid(o):
        o['policy_review.md'] = o['policy_review.md'] + ' see P99 here'
    m('text: prose quotes a proposal that does not exist', 'text', 'rendered', bad_pid)

    def stale(o):
        o['inventory.json'] = o['inventory.json'] + ' '
    m('files: generated file differs from disk', 'files', 'rendered', stale)
    return out


def main():
    cx = Ctx()
    rendered = build.render_all()
    inv, sw, g = load('inventory.json'), load('sweep.json'), load('gaps.json')
    patch, specs = load('rules.patch.json'), load('dialogue_specs.json')
    objs = {'inv': inv, 'sw': sw, 'g': g, 'patch': patch, 'specs': specs, 'rendered': rendered}
    validators = {
        'inventory': lambda: v_inventory(inv, cx), 'sweep': lambda: v_sweep(sw, inv, cx), 'gaps': lambda: v_gaps(g, cx),
        'patch': lambda: v_patch(patch, cx), 'specs': lambda: v_specs(specs, cx), 'files': lambda: v_files(rendered),
        'text': lambda: v_text(rendered, patch),
        'doc': v_doc,
    }
    failed = 0
    for name, fn in validators.items():
        errs = fn()
        print('%-10s %s' % (name, 'ok' if not errs else 'FAIL'))
        for e in errs[:25]:
            print('    ' + e)
        failed += bool(errs)
    # sanity on the data the claims rest on
    s = sw['summary']
    print('           %d quests, %d stages (%d swept, %d excluded), %d scripted INFOs, %d scripts, %d unsupported functions' % (
        s['quests'], s['stages_total'], s['stages_swept'], s['stages_excluded'], s['scripted_infos'], g['scripts_scanned'],
        g['summary']['unsupported_functions']))
    # mutation twin
    vmap = {'inv': lambda o: v_inventory(o, cx), 'sweep': lambda o: v_sweep(o, inv, cx), 'gaps': lambda o: v_gaps(o, cx),
            'patch': lambda o: v_patch(o, cx), 'specs': lambda o: v_specs(o, cx), 'files': v_files,
            'text': lambda o: v_text(o, patch)}
    caught = 0
    muts = mutants(inv, sw, g, patch, specs, rendered)
    for name, vname, oname, fn in muts:
        o = copy.deepcopy(objs[oname])
        fn(o)
        errs = vmap[vname](o)
        if errs:
            caught += 1
        else:
            failed += 1
            print('MUTANT SURVIVED: %s' % name)
    print('mutants    %d/%d caught' % (caught, len(muts)))
    if failed:
        print('FAILED (%d)' % failed)
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
