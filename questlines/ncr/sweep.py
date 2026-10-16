"""sweep.json builder: the L7 capture-sweep config for the NCR questline (m1_goodsprings.json format + setup lists).

python -I questlines/ncr/sweep.py      -> questlines/ncr/sweep.json   (needs inventory.json and gaps.json first; run build_all.py)

Format = out/questdata/m1_goodsprings.json (quests[].edid/ref/stages[].index ... which plugin/snapshot/tools/goodsprings_sweep.py
load_plan() reads) plus, per quest:
  console_id   hex FormID the console accepts (EditorIDs do not resolve)
  setup        ordered console commands to run ONCE before the quest's stages (after the sweep's test.quest.reset); each
               {cmd, why, required}. `required:false` = realism only.
  reset        hex FormIDs the sweep should pass to test.quest.reset for this quest (the quest + every quest its scripts write to)
  stage_setup  {stage: {branches:[{line, text, cmds:[...], manual:[...]}]}}: extra commands that make a conditional branch of
               THAT stage's result script fire (so the oracle's `conditional` effects get exercised). Applied right before
               `setstage <console_id> <stage>`; run the stage once without them (unconditional effects) and once with each branch.
  chain_setup  narrative-order predecessors on the NCR critical path as `setstage` commands (informational, not required)
  risky        per stage: why it should run last / on a disposable instance
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

CHAIN = ['VMQTops', 'VMQ03a', 'VMQ04', 'VMS45', 'VMQ05', 'VMQNCRFail', 'VMS21', 'vHDBattleController']
OPS = r'(==|!=|>=|<=|>|<)'
RE_STAGE = re.compile(r'(?i)\b(getstagedone|getstage)\s+(\w+)(?:\s+(\d+))?')
RE_OBJ = re.compile(r'(?i)\b(getobjectivedisplayed|getobjectivecompleted)\s+(\w+)\s+(\d+)\s*' + OPS + r'?\s*(-?\d+)?')
RE_QSTATE = re.compile(r'(?i)\b(getquestcompleted|getqc|getquestrunning|getqr)\s+(\w+)\s*' + OPS + r'?\s*(-?\d+)?')
RE_VAR = re.compile(r'(?i)\b(\w+)\.(\w+)\s*' + OPS + r'\s*(-?\d+(?:\.\d+)?)\b')
RE_ITEM = re.compile(r'(?i)\bplayer(?:ref)?\.getitemcount\s+(\w+)\s*' + OPS + r'\s*(-?\d+)')
RE_MANUAL = re.compile(r'(?i)\b(getincell|getdistance|getinworldspace|getdead|getdisabled|getisid|getlocked|isactionref|getactionref|'
                       r'getplayerteammate|getequipped|getinfaction|getav|getactorvalue|getreputationthreshold|getrandompercent|getgamehour|getdayspassed)\b')
RE_WRITE_Q = re.compile(r'(?i)\b(setstage|startquest|stopquest|completequest|failquest|setobjectivedisplayed|setobjectivecompleted|resetquest)\s+(\w+)')
RE_WRITE_VAR = re.compile(r'(?i)^\s*set\s+(\w+)\.(\w+)\s+to\b')


def _default_state(fnname, line):
    """True for `X.GetDead == 0`-style tests: the world is already in that state."""
    if fnname.lower() not in ('getdead', 'getdisabled', 'getlocked'):
        return False
    pat = re.compile(r'(?i)' + fnname + r'\s*(==\s*0)')
    return pat.search(line) is not None


def satisfy(op, val):
    """smallest integer value that makes `x op val` true (None when the default state already satisfies it / not useful)."""
    v = float(val)
    if op in ('==', None):
        return v
    if op == '>=':
        return v
    if op == '>':
        return v + 1
    if op == '!=':
        return v + 1
    return None   # < and <= are normally the default state


def fmt(v):
    return str(int(v)) if float(v).is_integer() else str(v)


class Sweep:
    def __init__(self, D):
        self.D = D
        self.qnames = {q['edid'] for q in D.quests if q.get('edid')}
        self.qvars = {}
        for q in D.quests:
            if q.get('edid') and not q.get('override') and q.get('script_data'):
                self.qvars.setdefault(q['edid'].lower(), {v['name'].lower(): v['name'] for v in q['script_data'].get('vars', [])})

    def qhex(self, edid):
        q = self.D.by_edid.get(edid) or self.D.by_edid.get(self._canon(edid))
        return common.hexid(q['ref']) if q else None

    def stages(self, edid):
        q = self.D.by_edid.get(edid) or self.D.by_edid.get(self._canon(edid))
        return sorted(st['index'] for st in q['stages']) if q else []

    def _canon(self, name):
        for k in self.D.by_edid:
            if k.lower() == name.lower():
                return k
        return name

    def atoms(self, line):
        """Setup commands + manual hints for ONE `if`/`elseif` line."""
        cmds, manual = [], []
        for fn, qn, n in RE_STAGE.findall(line):
            qid = self.qhex(qn)
            if not qid:
                continue
            have = self.stages(qn)
            if fn.lower() == 'getstagedone' and n:
                if int(n) in have:
                    cmds.append(f'setstage {qid} {n}')
            elif fn.lower() == 'getstage':
                m = re.search(r'(?i)\bgetstage\s+' + re.escape(qn) + r'\s*' + OPS + r'\s*(-?\d+)', line)
                if m:
                    op, val = m.group(1), int(m.group(2))
                    ok = [x for x in have if (x == val if op == '==' else x >= val if op == '>=' else x > val if op == '>' else x != val if op == '!=' else False) and x > 0]
                    if ok:
                        cmds.append(f'setstage {qid} {min(ok)}')
        for fn, qn, n, op, val in RE_OBJ.findall(line):
            qid = self.qhex(qn)
            if not qid:
                continue
            want_one = (val == '' or (op in ('==', '>=', '>', None, '') and val not in ('0',)) or (op == '!=' and val == '0'))
            if want_one:
                if fn.lower() == 'getobjectivecompleted':
                    cmds.append(f'setobjectivedisplayed {qid} {n} 1')
                    cmds.append(f'setobjectivecompleted {qid} {n} 1')
                else:
                    cmds.append(f'setobjectivedisplayed {qid} {n} 1')
        for fn, qn, op, val in RE_QSTATE.findall(line):
            qid = self.qhex(qn)
            want_true = (not op) or (op == '==' and val != '0') or (op == '!=' and val == '0') or (op in ('>', '>=') and val == '0' and op == '>')
            if not qid or not want_true:
                continue
            cmds.append(f'completequest {qid}' if fn.lower() in ('getquestcompleted', 'getqc') else f'startquest {qid}')
        for qn, var, op, val in RE_VAR.findall(line):
            vars_ = self.qvars.get(qn.lower())
            if not vars_ or var.lower() not in vars_:
                continue
            v = satisfy(op, val)
            if v is None:
                continue
            cmds.append(f'set {self.qhex(qn)}.{vars_[var.lower()]} to {fmt(v)}')
        for item, op, val in RE_ITEM.findall(line):
            ref = self.D.resolve(item)
            if ref and op in ('>=', '>', '==') and float(val) > 0:
                cmds.append(f'player.additem {common.hexid(ref)} {fmt(satisfy(op, val))}')
            elif not ref:
                manual.append(f'item {item} does not resolve to a FormID')
        for fnname in RE_MANUAL.findall(line):
            if _default_state(fnname, line):
                continue   # the default state already satisfies it
            manual.append(fnname.lower())
        out, seen = [], set()
        for c in cmds:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out, sorted(set(manual))

    def branches(self, source):
        out = []
        for n, raw in enumerate(source.replace('\r', '').split('\n'), 1):
            line = common.strip(raw)
            if not re.match(r'(?i)^(if|elseif)\b', line):
                continue
            cmds, manual = self.atoms(line)
            if cmds or manual:
                out.append({'line': n, 'text': line[:150], 'cmds': cmds, 'manual': manual})
        return out

    def reset_set(self, q):
        names = {q['edid']}
        for where, sc, _ in self.D.quest_scripts(q):
            if where.startswith('info '):
                continue
            for line in sc['source'].replace('\r', '').split('\n'):
                line = common.strip(line)
                for _, qn in RE_WRITE_Q.findall(line):
                    if qn in self.qnames:
                        names.add(qn)
                    elif self._canon(qn) in self.qnames:
                        names.add(self._canon(qn))
                m = RE_WRITE_VAR.match(line)
                if m and self._canon(m.group(1)) in self.qnames:
                    names.add(self._canon(m.group(1)))
        return sorted(common.hexid(self.D.by_edid[n]['ref']) for n in names if n in self.D.by_edid)

    def complete_stage(self, edid):
        q = self.D.by_edid[edid]
        done = [s['index'] for s in q['stages'] if any('complete_quest' in e['flags'] for e in s['entries'])]
        if done:
            return min(done)
        ok = [s['index'] for s in q['stages'] if not any('fail_quest' in e['flags'] for e in s['entries'])]
        return max(ok) if ok else None


# stages that are destructive or start a world-wide scripted sequence; the sweep must run them last and from a reloaded save
CURATED_RISKY = {
    ('VMQ05', 100): 'SetStage vHDBattleController 10 starts the Hoover Dam battle (about 60 ref enables/disables, EnableFastTravel 0, SetEnemy PlayerFaction vs Legion)',
    ('VMQ05', 200): 'quest failure after VMQNCRFail: Moore and Crocker are disabled by the caller, later NCR stages cannot be reached on the same save',
    ('VMQNCRFail', 5): 'permanent: disables Colonel Moore / Ambassador Crocker / VMS23 messenger and fails VMQ05, VMS23, VMS29 (conditional effects)',
    ('vHDBattleController', 10): 'starts the battle: SetEnemy PlayerFaction Legion, SetAlly PlayerFaction NCR, ~60 ref enables/disables, fast travel off',
    ('vHDBattleController', 20): 'kills up to 25 Legate-camp actors (kill-everyone cleanup) when the Legate attacked',
    ('vHDBattleController', 30): 'game over: DisablePlayerControls, vStoryState 61, StartQuest VEnding (ending slideshow, SwapTexture)',
    ('VMS45', 60): 'quest fail: scripts call it from NPC death handlers; run last on a clean save',
}


def risky_reasons(stage_events, edid, stage):
    out = []
    if (edid, stage) in CURATED_RISKY:
        out.append(CURATED_RISKY[(edid, stage)])
    for e in stage_events:
        if e['severity'] == 'blocker' and e['quest'] == edid and e['where'] == f'stage {stage}':
            out.append(f'{e["fn"]} (blocker)')
        if 'kill-everyone' in e['hazards'] and e['quest'] == edid and e['where'] == f'stage {stage}':
            out.append('kill-everyone cleanup')
    return sorted(set(out))


def build(D=None, inv=None, gaps=None):
    D = D or common.Data()
    inv = inv or json.load(open(os.path.join(common.HERE, 'inventory.json'), encoding='utf-8'))
    gaps = gaps or json.load(open(os.path.join(common.HERE, 'gaps.json'), encoding='utf-8'))
    sw = Sweep(D)
    # per-stage hazard lookup straight from gaps.json locations
    stage_events = []
    for g in gaps['gaps']:
        for l in g['locations']:
            stage_events.append({'quest': l['quest'], 'where': l['where'], 'fn': g['fn'], 'severity': g['severity'], 'hazards': g['hazards']})
    swept_roles = ('core', 'support')
    quests, excluded = [], []
    chain_done = {}
    for e in CHAIN:
        if e in D.by_edid:
            chain_done[e] = sw.complete_stage(e)
    for iq in inv['quests']:
        edid = iq['edid']
        q = D.by_edid[edid]
        if iq['role'] in swept_roles:
            rq = D.risk[q['ref']]
            native = D.native['quests'].get(edid, {}).get('stages', {})
            stages, stage_setup, risky = [], {}, {}
            for s in sorted(q['stages'], key=lambda s: s['index']):
                idx = s['index']
                srcs = [e['script']['source'] for e in s['entries'] if e.get('script') and e['script'].get('source')]
                br = []
                for src in srcs:
                    br += sw.branches(src)
                if br:
                    stage_setup[str(idx)] = {'branches': br}
                nat = native.get(str(idx), {})
                why = risky_reasons(stage_events, edid, idx)
                if why:
                    risky[str(idx)] = why
                stages.append({
                    'index': idx, 'log': next((e.get('text') for e in s['entries'] if e.get('text')), None),
                    'flags': sorted({f for e in s['entries'] for f in e['flags']}),
                    'has_script': bool(srcs),
                    'findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text'])
                                 for f in rq['stage_findings'] if f['where'] == f'stage {idx}'],
                    'oracle': {'unconditional': len(nat.get('unconditional', [])), 'conditional': len(nat.get('conditional', [])),
                               'types': sorted({x['type'] for k in ('unconditional', 'conditional') for x in nat.get(k, [])})},
                })
            setup = []
            if 'start_enabled' not in (q.get('flags') or []):
                setup.append({'cmd': f'startquest {iq["console_id"]}', 'why': 'quest is not start-enabled; stages only advance a running quest', 'required': True})
            qbr = sw.branches(q['script_data']['source']) if q.get('script_data') and q['script_data'].get('source') else []
            chain = []
            if edid in CHAIN:
                for prev in CHAIN[:CHAIN.index(edid)]:
                    st = chain_done.get(prev)
                    if st is not None:
                        chain.append({'cmd': f'setstage {sw.qhex(prev)} {st}', 'why': f'{prev}: narrative predecessor on the NCR critical path (completion stage); informational',
                                      'required': False})
            quests.append({
                'edid': edid, 'ref': iq['ref'], 'name': iq['name'], 'role': 'core' if iq['role'] == 'core' else 'support',
                'console_id': iq['console_id'], 'tier': iq['tier'], 'tier_direct': iq['tier_direct'], 'cats': rq['cats'],
                'priority': q.get('priority'), 'flags': q.get('flags'), 'script': q.get('script'),
                'stages': stages,
                'objectives': [{'index': o['index'], 'text': o['text'], 'targets': o['targets']} for o in iq['objectives']],
                'setup': setup, 'quest_script_branches': qbr, 'stage_setup': stage_setup, 'chain_setup': chain,
                'reset': sw.reset_set(q), 'risky': risky,
                'setstage_other_quests': sorted({f'{m.group(2)} {m.group(3)}' for _, sc, _ in D.quest_scripts(q) for m in re.finditer(r'(?i)\b(setstage)\s+(\w+)\s+(\d+)', sc['source']) if m.group(2).lower() != edid.lower()}),
                'dialogue': {'infos': iq['dialogue']['infos'], 'topics': iq['dialogue']['topics']},
            })
        elif iq['role'] in ('cross_lane', 'touched') and q['stages']:
            reason = ('owned by another questline lane (Act 2 / shared main line): not swept here' if iq['role'] == 'cross_lane'
                      else 'one-hop neighbour (side quest, other main line or shared dialogue) that has its own questline owner; not part of the NCR main line')
            excluded.append({'quest': edid, 'ref': iq['ref'], 'stages': [s['index'] for s in iq['stages']], 'role': iq['role'], 'reason': reason})

    out = {
        'milestone': 'NCR main questline (through the Hoover Dam ending)', 'slug': common.SLUG,
        'core': {e: D.by_edid[e]['name'] for e in common.CORE},
        'support': {e: D.by_edid[e]['name'] for e in common.SUPPORT},
        'system_quests': [x for x in common.SYSTEM if x in D.by_edid],
        'format_note': 'm1_goodsprings.json-compatible: quests[].edid/ref/stages[].index/tier_direct are what goodsprings_sweep.load_plan() reads. '
                       'Extra keys (console_id, setup, reset, stage_setup, chain_setup, risky, oracle) are for the L7 NCR sweep driver.',
        'procedure': ['1. load the checkpoint save and run test.quest.reset with quest.reset (hex FormIDs) - the sweep driver does this per stage',
                      '2. run quest.setup in order (required entries first; startquest for quests that are not start-enabled)',
                      '3. unconditional pass: console `setstage <console_id> <stage>` and capture (compare with out/questdata/effects_native.json.gz)',
                      '4. conditional pass (only for stages listed in stage_setup): reload, reset, setup, run the cmds of one branch, then setstage again; quest_script_branches are the same idea for GameMode scripts',
                      '5. chain_setup is narrative order only (never required); risky stages go last, on a disposable save'],
        'order_last': ['vHDBattleController', 'VMQNCRFail', 'VMQ05'],   # cinematic / destructive quests last (same convention as ORDER_LAST in goodsprings_sweep.py)
        'excluded': excluded,
        'summary': {
            'quests': len(quests), 'stages': sum(len(q['stages']) for q in quests), 'objectives': sum(len(q['objectives']) for q in quests),
            'infos': sum(q['dialogue']['infos'] for q in quests),
            'risky_stages': sum(len(q['risky']) for q in quests),
            'stages_with_branches': sum(len(q['stage_setup']) for q in quests),
            'excluded_quests': len(excluded), 'excluded_stages': sum(len(x['stages']) for x in excluded),
        },
        'quests': quests,
    }
    return out


def main():
    out = build()
    p = os.path.join(common.HERE, 'sweep.json')
    common.jdump(out, p, indent=1)
    s = out['summary']
    print(f'sweep: {s["quests"]} quests, {s["stages"]} stages, {s["objectives"]} objectives, {s["stages_with_branches"]} stages with branch setups, '
          f'{s["risky_stages"]} risky stages, excluded {s["excluded_quests"]} quests / {s["excluded_stages"]} stages -> {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
