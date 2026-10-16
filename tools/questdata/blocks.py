"""Block-level effects of standalone scripts (quest scripts + object/NPC/effect scripts) and the
guest-suppression coverage report.

Quest/object scripts are `Begin <BlockType> ... End` blocks that the engine runs continuously (GameMode, MenuMode) or on
events (OnDeath, OnActivate, OnTrigger, OnLoad, ...), on EVERY machine. Any block that mutates shared state would also do
so on a guest unless suppressed. Output:
  - `blocks` list inside effects.json.gz (rich format, per block type)
  - guest_suppression_report.md
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify  # noqa: E402
import effects  # noqa: E402
from lexicon import L  # noqa: E402

# effect types L5's guest gate can block (L5 list); everything else that mutates shared state needs another strategy
L5_BLOCKABLE = {'SetStage', 'SetObjective', 'StartQuest', 'StopQuest', 'ModReputation', 'ModKarma', 'EnableRef',
                'DisableRef', 'AddItem', 'RemoveItem', 'ModActorValue', 'KillActor', 'ResurrectActor', 'MoveRef'}
UNBLOCKABLE = {'SetGlobal', 'SetQuestVar'}          # direct variable writes: not interceptable by call hooks
UNLISTED = {'CompleteQuest', 'FailQuest', 'SetFactionRank', 'AddPerk', 'RemoveFromFaction'}  # modeled, not on L5's list
MUTATOR_CATS = {'ACTOR', 'WORLD', 'S-STAT', 'P-STAT', 'REWARD', 'P-INV', 'TRAVEL', 'COMPANION', 'SAVE', 'MOVE'}
CONTINUOUS = {'gamemode', 'menumode', 'scripteffectupdate'}
QUEST_TOUCH = {'SetStage', 'SetObjective', 'StartQuest', 'StopQuest', 'CompleteQuest', 'FailQuest', 'SetGlobal',
               'SetQuestVar'}


def split_blocks(src):
    """-> [(block_type, args, body_source, first_body_line)] for each top-level Begin..End block."""
    out, cur, typ, args, start = [], [], None, [], 0
    for n, raw in enumerate(src.splitlines(), 1):
        line = classify.strip_comment(raw).strip()
        toks = line.split()
        if not toks:
            continue
        if typ is None:
            if toks[0].lower() == 'begin' and len(toks) > 1:
                typ, args, cur, start = toks[1], toks[2:], [], n + 1
            continue
        if len(toks) == 1 and toks[0].lower() == 'end':
            out.append((typ, args, '\n'.join(cur), start))
            typ = None
            continue
        cur.append(raw)
    return out


def script_refs(scpts):
    return {r for s in scpts if s.get('script') for r in s['script']['refs'] if r}


def analyse_scripts(scpts, quest_script_refs, refinfo, globals_by_name):
    blocks = []
    for s in scpts:
        sc = s.get('script')
        if not sc or not sc.get('source'):
            continue
        is_quest = s['ref'] in quest_script_refs
        owner = 'quest' if is_quest else sc['type']  # 'quest' | 'object' | 'effect'
        names = effects.name_map(sc, refinfo)
        local_vars = {v['name'].lower() for v in sc['vars'] if v.get('name')} if is_quest else set()
        for typ, args, body, first in split_blocks(sc['source']):
            ctx = effects.Ctx(names, globals_by_name, local_vars)
            eff, unm = effects.analyse(body, ctx)
            if not eff and not unm:
                continue
            blocks.append({'script': s['ref'], 'edid': s.get('edid'), 'owner': owner, 'block': typ,
                           'args': args, 'line': first, 'effects': eff, 'unmodeled': unm})
    return blocks


def _kind(fn_or_type):
    t = fn_or_type
    if t in L5_BLOCKABLE:
        return 'blockable'
    if t in UNBLOCKABLE:
        return 'unblockable'
    if t in UNLISTED:
        return 'unlisted'
    return None


def summarise(blocks):
    """Per block: effect-type counter plus a classification."""
    rows = []
    for b in blocks:
        c = collections.Counter(e['type'] for e in b['effects'])
        for u in b['unmodeled']:
            if u['fn'] == 'SetQuestVar':
                c['SetQuestVar'] += 1
            elif L.get(u['fn'].lower(), ('',))[0] in MUTATOR_CATS:
                c['~' + u['fn'].lower()] += 1       # unmodeled shared mutator
        kinds = set()
        for t in c:
            k = 'unlisted' if t.startswith('~') else _kind(t)
            if k:
                kinds.add(k)
        rows.append((b, c, kinds))
    return rows


def report_md(blocks, n_scripts):
    rows = summarise(blocks)
    mut = [r for r in rows if r[2]]
    md = ['# Guest-suppression coverage report', '',
          f'{n_scripts} standalone scripts (quest + object/NPC + magic-effect) analysed; {len(blocks)} blocks have at least one '
          f'effect or unmodeled call, {len(mut)} of them mutate shared state and would do so on a guest unless suppressed.',
          '', 'Classes: **blockable** = effect types on L5\'s gate list (' + ', '.join(sorted(L5_BLOCKABLE)) + '); '
          '**unblockable** = direct `set Quest.var` / quest-script local var / global writes (no call to hook); '
          '**unlisted** = modeled or unmodeled mutators not on L5\'s list (CompleteQuest, FailQuest, SetFactionRank, AddPerk, '
          'RemoveFromFaction, AddScriptPackage, StartCombat, Lock/Unlock, ...).', '']
    by_type = collections.defaultdict(lambda: collections.Counter())
    for b, c, kinds in mut:
        t = b['block'].lower()
        by_type[t]['blocks'] += 1
        for k in ('blockable', 'unblockable', 'unlisted'):
            if k in kinds:
                by_type[t][k] += 1
        if kinds == {'unblockable'}:
            by_type[t]['only_unblockable'] += 1
    md += ['## By block type (blocks that mutate shared state)', '',
           '| Block | blocks | has blockable | has unblockable (var/global) | has unlisted | ONLY unblockable | continuous |',
           '|---|---|---|---|---|---|---|']
    for t, c in sorted(by_type.items(), key=lambda kv: -kv[1]['blocks']):
        md.append(f'| {t} | {c["blocks"]} | {c["blockable"]} | {c["unblockable"]} | {c["unlisted"]} | '
                  f'{c["only_unblockable"]} | {"yes" if t in CONTINUOUS else ""} |')
    md += ['', '## Effect occurrences by block type', '']
    eff_types = sorted({t for _, c, _ in mut for t in c})
    md += ['| Block | ' + ' | '.join(eff_types) + ' |', '|---|' + '---|' * len(eff_types)]
    mat = collections.defaultdict(collections.Counter)
    for b, c, _ in mut:
        mat[b['block'].lower()].update(c)
    for t, c in sorted(mat.items(), key=lambda kv: -sum(kv[1].values())):
        md.append(f'| {t} | ' + ' | '.join(str(c[e]) if c[e] else '' for e in eff_types) + ' |')
    md += ['', '## By script owner', '', '| Owner | blocks mutating | with blockable | with unblockable | with unlisted |',
           '|---|---|---|---|---|']
    for ow in ('quest', 'object', 'effect'):
        rr = [r for r in mut if r[0]['owner'] == ow]
        md.append(f'| {ow} | {len(rr)} | {sum(1 for r in rr if "blockable" in r[2])} | '
                  f'{sum(1 for r in rr if "unblockable" in r[2])} | {sum(1 for r in rr if "unlisted" in r[2])} |')

    # highlight: what L5 cannot block
    unb = [r for r in mut if 'unblockable' in r[2]]
    cont = [r for r in unb if r[0]['block'].lower() in CONTINUOUS]
    md += ['', '## Cannot be blocked by L5 (variable / global writes)', '',
           f'{len(unb)} blocks write quest variables or globals directly; {len(cont)} of them are in continuous blocks '
           f'(GameMode/MenuMode, i.e. running every frame on every machine) and '
           f'{sum(1 for r in unb if r[2] == {"unblockable"})} contain nothing else. '
           'These need a different strategy: host-authoritative variable sync that overwrites the guest\'s values '
           '(plus suppressing the guest-side script where it is the sole writer).', '']
    wr = collections.Counter()
    for b, c, _ in unb:
        wr[(b['owner'], b['edid'])] += c['SetQuestVar'] + c['SetGlobal']
    md += ['Top scripts by direct var/global writes in blocks:', '', '| Script | owner | writes |', '|---|---|---|']
    for (ow, ed), n in wr.most_common(25):
        md.append(f'| {ed} | {ow} | {n} |')
    gl = collections.Counter()
    for b, _, _ in mut:
        for e in b['effects']:
            if e['type'] == 'SetGlobal' and e.get('global'):
                gl[e['global']] += 1
    md += ['', f'Globals written from blocks: {len(gl)} distinct; top: ' +
           ', '.join(f'{g} x{n}' for g, n in gl.most_common(8)), '']

    # unlisted modeled/unmodeled mutators
    ul = collections.Counter()
    for b, c, _ in mut:
        for t, n in c.items():
            if t.startswith('~') or t in UNLISTED:
                ul[t.lstrip('~')] += n
    md += ['## Mutators outside L5\'s list (occurrences in blocks)', '', '| Function / effect | occurrences |', '|---|---|']
    for t, n in ul.most_common(25):
        md.append(f'| {t} | {n} |')

    # OnDeath
    od = [r for r in rows if r[0]['block'].lower() == 'ondeath']
    od_q = [r for r in od if any(t in QUEST_TOUCH for t in r[1])]
    od_cond = [r for r in od_q if all(e['conditional'] for e in r[0]['effects'] if e['type'] in QUEST_TOUCH) and
               all(u['conditional'] for u in r[0]['unmodeled'] if u['fn'] == 'SetQuestVar')]
    md += ['', '## OnDeath blocks touching quests (ghost-kill double-fire risk)', '',
           f'{len([b for b in blocks if b["block"].lower() == "ondeath"])} OnDeath blocks with any effect/unmodeled call; '
           f'**{len(od_q)}** touch quest state (stage / objective / quest start-stop / complete-fail / global / quest var) '
           f'in {len({r[0]["script"] for r in od_q})} scripts; {len(od_cond)} of those only inside conditionals.', '',
           '| Script | owner | quest-touching effects |', '|---|---|---|']
    for b, c, _ in sorted(od_q, key=lambda r: -sum(v for t, v in r[1].items() if t in QUEST_TOUCH))[:40]:
        md.append(f'| {b["edid"]} | {b["owner"]} | ' + ', '.join(f'{t} x{n}' for t, n in c.items() if t in QUEST_TOUCH) + ' |')
    return '\n'.join(md) + '\n'
