"""Quest co-op policy generator.

Skeleton is generated from risk.json (exact script lines cited as `where:line`), category -> default rule.
Hand-written judgment lives in curated_policies.CURATED and overrides/extends the skeleton.
"""
import json
import os
import re

from model import story_quests, questline_of, tier_of
from curated_policies import CURATED
from rules import machine_rules

# Owner decisions (docs/MASTER-PLAN.md): karma+rep shared, quest items + rewards to every player,
# initiator chooses dialogue, free roam but story moments need the party nearby.
CAT_RULES = {
    'CTRL': ('Forced cutscene / control lock. Tether: stage fires only when all players are in the scene area '
             '(same cell, within tether radius) or have been auto-gathered; lock ALL party members\' controls for the '
             'scene, release on the host\'s release effect (with a timeout failsafe). Initiator drives dialogue.',
             ['SetPlayerControls (per-player lock mask, proposed)', 'PartyTether gate (proposed)']),
    'CTRL-DIALOG': ('Dialogue-driven control lock/unlock. Lock follows the conversation: every player locked while the '
                    'initiator talks; unlock on dialogue end via the effect stream, never by local script.',
                    ['SetPlayerControls (proposed)']),
    'CTRL-RELEASE': ('Control re-enable. Must be an explicit effect so a client that missed the lock still ends '
                     'unlocked; also unlock on reconnect/resync.', ['SetPlayerControls (proposed)']),
    'MOVE': ('Scripted player teleport (MoveTo). Party-warp: every party member is moved to an offset slot around the '
             'target marker in the same txn; absent players are held back at the gate until present (or warped '
             'with the party if the scene is marked mandatory).', ['MoveRef(player, party-warp)', 'PartyTether gate (proposed)']),
    'P-INV': ('Player inventory change. Quest items: duplicated to every player (AddItem routed Recipient=All). '
              'RemoveItem of a quest item removes from every player; barter/consumable removal applies to the '
              'player who owns the item (initiator).', ['AddItem (Recipient=All)', 'RemoveItem (per-owner)']),
    'P-STAT': ('Player stat/perk/skill change. Per-player by default (SPECIAL, perks, skills); karma/reputation are '
               'shared (owner decision) and go through ModKarma/ModReputation.', ['ModActorValue (Recipient=target)',
               'ModKarma / ModReputation (shared)']),
    'S-STAT': ('Karma / reputation / fame change: shared across the party (one Courier). Apply once on host, '
               'broadcast absolute result.', ['ModKarma', 'ModReputation']),
    'REWARD': ('Reward (caps/items/xp/note): every player receives the full reward (owner decision); '
               'dedupe by txn seq so a reconnect never double-pays.', ['AddItem (Recipient=All)']),
    'P-REF': ('Script reads the player reference (distance, cell, equipped, skill, action-ref). Evaluate on the host with '
              'the party collapsed: IsActionRef -> any party member that is the initiator; GetDistance -> min over '
              'party; GetInCell/worldspace -> "all in" for entry gates, "any in" for exit gates; skill checks -> '
              'initiator unless noted.', ['Host-side party-aware condition evaluation (proposed)']),
    'P-COND': ('Dialogue condition on the player. Evaluated for the initiator only (initiator picks the line; '
               'everyone sees line + result).', ['Initiator-scoped condition eval']),
    'CONV': ('Dialogue/speech result. Initiator chooses; line, result script effects and skill-check outcome are '
             'broadcast to all. Others cannot interrupt a running scene.', ['ForcedDialogue / topic effect capture']),
    'SAVE': ('Autosave/quicksave triggers or save-gated logic. Disable client-side saves during the scene; host owns '
             'save points.', ['SaveSuppress (proposed)']),
    'COMPANION': ('SetPlayerTeammate: companion ownership. Default (open decision): owned by the recruiter; '
                  'dismissal/teleport-to-player effects target the owner only.', ['CompanionOwner (proposed)']),
    'TRAVEL': ('Fast-travel enable/disable. Party-wide flag; host authoritative.', ['SetGlobal-like flag effect']),
    'WORLD': ('World object state (enable/disable/lock/unlock/activate). Host runs the script; effect list replays on '
              'clients.', ['EnableRef', 'DisableRef']),
    'ACTOR': ('Actor state (enable/kill/package). Host-owned NPC; clients receive effects.',
              ['EnableRef', 'DisableRef', 'KillActor', 'ResurrectActor']),
    'Q-STATE': ('Quest state. Merge: stage = max, objectives = OR, host authoritative; echo-guarded on clients.',
                ['SetStage', 'SetObjective', 'StartQuest', 'StopQuest']),
    'TIME': ('Game-time / timer logic. Host clock is authoritative.', ['SetGlobal']),
}
SKIP_IN_RULES = {'Q-STATE', 'ACTOR', 'WORLD', 'TIME'}   # baseline, not listed per quest


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def cites(risk, limit=10):
    """Pick the most informative exact script lines: level desc, one per (owner,fn)."""
    fs = sorted(risk['findings'] + risk['stage_findings'], key=lambda f: (-f['level'], f['cat'], f['where']))
    seen, out = set(), []
    for f in fs:
        if f['level'] < 2:
            break
        k = (f['where'], f['fn'], f['cat'])
        if k in seen:
            continue
        seen.add(k)
        out.append({'where': f['where'], 'line': f['line'], 'cat': f['cat'], 'level': f['level'],
                    'text': f['text'].strip()[:140]})
        if len(out) >= limit:
            break
    return out


def build_policies(data):
    """-> list of policy dicts for every T3/T4 story quest, ordered by (tier desc, score desc)."""
    out = []
    for q in story_quests(data):
        r = data['risk'][q['ref']]
        if r['tier'] not in ('T3', 'T4'):
            continue
        cats = [c for c, lv in sorted(r['cat_max_level'].items(), key=lambda kv: -kv[1])
                if c in CAT_RULES and c not in SKIP_IN_RULES and lv >= 1]
        rules, hooks = [], []
        for c in cats:
            rules.append({'cat': c, 'rule': CAT_RULES[c][0]})
            for h in CAT_RULES[c][1]:
                if h not in hooks:
                    hooks.append(h)
        cur = CURATED.get(q['edid'])
        out.append({'edid': q['edid'], 'name': q.get('name'), 'plugin': q['plugin'], 'tier': r['tier'],
                    'score': r['score'], 'questline': questline_of(data, q), 'cats': r['cat_max_level'],
                    'cites': cites(r), 'rules': rules, 'hooks': hooks, 'curated': cur,
                    'machine_rules': machine_rules(q['edid'], r, cur)})
    out.sort(key=lambda p: (-int(p['tier'][1]), -p['score'], p['edid']))
    return out


def render_md(line, pols):
    L = [f'# Co-op policies: {line}', '',
         '_Generated by `tools/coverage/build.py` from `out/questdata/risk.json`. Cited lines are exact '
         '(`script:line`; `dep obj X` = script of an object/NPC the quest depends on). Entries marked '
         '**curated** carry hand-written judgment; the rest are the category skeleton._', '']
    for p in pols:
        flag = ' - curated' if p['curated'] else ''
        L += [f"## {p['edid']} - {p['name'] or '(unnamed)'}  [{p['tier']}{flag}]", '',
              f"Plugin {p['plugin']}, risk score {p['score']}. Categories: " +
              ', '.join(f"{c}:{lv}" for c, lv in sorted(p['cats'].items(), key=lambda kv: -kv[1])
                        if c not in SKIP_IN_RULES), '']
        c = p['curated']
        if c:
            L += [f"**Scene.** {c['scene']}", '', f"**Co-op rule.** {c['rule']}", '']
            if c.get('tether'):
                L += [f"**Tether.** {c['tether']}", '']
            L += [f"**Engine hooks / effects.** {c['hooks']}", '']
            if c.get('test'):
                L += [f"**Test hook.** {c['test']}", '']
        L += ['**Risky lines:**', '']
        for ci in p['cites']:
            L.append(f"- L{ci['level']} {ci['cat']} `{ci['where']}:{ci['line']}` `{ci['text']}`")
        if not p['cites']:
            L.append('- (no level>=2 line; tier driven by transitive dependency)')
        L.append('')
        L += ['**Machine rules:** ' + ', '.join(r['kind'] + ('' if r['origin'] == 'curated' else '~')
                                                 for r in p['machine_rules']) + '  (`~` = derived from categories)', '']
        if not c:
            L += ['**Default rules (skeleton):**', '']
            for r in p['rules'][:6]:
                L.append(f"- *{r['cat']}*: {r['rule']}")
            L += ['', '**Hooks needed:** ' + (', '.join(p['hooks']) or 'none beyond baseline'), '']
    return '\n'.join(L)


def write_policies(data, outdir):
    os.makedirs(outdir, exist_ok=True)
    pols = build_policies(data)
    by_line = {}
    for p in pols:
        by_line.setdefault(p['questline'], []).append(p)
    index = ['# Quest policies (T3/T4 story quests)', '',
             f'{len(pols)} quests, {sum(1 for p in pols if p["curated"])} with hand-written policy. '
             'Regenerate: `python tools/coverage/build.py`.', '',
             'Owner decisions applied everywhere: karma+rep shared; quest items and rewards to every player; '
             'initiator picks dialogue; free roam but story moments tether the party.', '',
             '| Questline | Quests | Curated | File |', '|---|---|---|---|']
    for line, ps in sorted(by_line.items()):
        fn = slug(line) + '.md'
        open(os.path.join(outdir, fn), 'w', encoding='utf-8').write(render_md(line, ps))
        index.append(f"| {line} | {len(ps)} | {sum(1 for p in ps if p['curated'])} | [{fn}]({fn}) |")
    open(os.path.join(outdir, 'README.md'), 'w', encoding='utf-8').write('\n'.join(index) + '\n')
    json.dump(pols, open(os.path.join(outdir, 'policies.json'), 'w', encoding='utf-8'), indent=1)
    json.dump({'schema': 1, 'kinds_doc': 'tools/coverage/rules.py',
               'quests': {p['edid']: {'tier': p['tier'], 'questline': p['questline'],
                                      'curated': bool(p['curated']), 'rules': p['machine_rules']} for p in pols}},
              open(os.path.join(outdir, 'rules.base.json'), 'w', encoding='utf-8'), indent=1)
    return pols
