"""Gap analysis: every script function in the Legion scope -> Effect type + L5 capture/apply status."""
import collections
import hashlib
import re

import common as C
import fnmap

SEV_ORDER = ['blocker', 'high', 'medium', 'low', 'info']
STAT_RANK = {'n/a': 0, 'supported': 1, 'partial': 2, 'missing': 3}
TRIGGER_BLOCKS = {'ontrigger', 'ontriggerenter', 'ontriggerleave', 'onactivate', 'onadd', 'onequip', 'onunequip', 'ondrop', 'ongive',
                  'onhit', 'onhitwith', 'onstartcombat', 'onsell'}
PLAYERISH = re.compile(r'\b(player|playerref|getactionref|isactionref|activatingactor|getisreference)\b', re.I)


def worst(a, b):
    return a if STAT_RANK[a] >= STAT_RANK[b] else b


def scope_roles():
    return dict(C.SCOPE)


def collect_scripts(data, roles=('core', 'battle', 'dialogue', 'system')):
    """Deduplicated scripts of the in-scope quests (prereq quests excluded)."""
    seen = {}

    def put(s, edid, role):
        key = (hashlib.sha1(s['source'].encode('utf-8', 'replace')).hexdigest(), s['label'])
        if key in seen:
            if edid not in seen[key]['quests']:
                seen[key]['quests'].append(edid)
            return
        s = dict(s)
        s['quests'] = [edid]
        s['role'] = role
        seen[key] = s
    for edid, role in C.SCOPE:
        if role not in roles:
            continue
        for s in C.scripts_of(data, data.by_edid[edid]):
            put(s, edid, role)
    # object scripts that drive the core/battle quests (SetStage/StartQuest/... on them) even when the quest never lists
    # the carrying ref in dep_refs (e.g. the Legate tent OnTrigger script that sets VHDLegionBattle 10)
    drivers = [e for e, r in C.SCOPE if r in ('core', 'battle') and r in roles]
    if drivers:
        # the VEFR reaction quests are started/stopped as bookkeeping by DLC portal scripts: only SetStage counts as "driving" them
        verbs = r'setstage|startquest|stopquest|failquest|completequest'
        names = '|'.join(map(re.escape, [e for e in drivers if not e.startswith('VEFR')]))
        vefr = '|'.join(map(re.escape, [e for e in drivers if e.startswith('VEFR')]))
        rx = re.compile(r'(?i)\b(?:(?:%s)\s+(%s)|(?:setstage)\s+(%s))\b' % (verbs, names, vefr))
        low = {e.lower(): e for e in drivers}
        for ent in data.extra['scpt'].values():
            sc = ent.get('script')
            if not sc or not sc.get('source'):
                continue
            hits = {low[(m.group(1) or m.group(2)).lower()] for m in rx.finditer(sc['source'])}
            for e in hits:
                put({'label': ent['edid'], 'kind': 'dep_trigger', 'script': ent['edid'], 'quest': e, 'source': sc['source']}, e,
                    dict(C.SCOPE)[e])
    return list(seen.values())


def classify_use(u, kind):
    """-> (status, entry) or None when the use is not a co-op concern."""
    cmd = u['cmd']
    if u['role'] in ('setvar', 'setglobal', 'setlocal'):
        ent = fnmap.FN.get(cmd)
    elif u['role'] in ('cond', 'expr') and cmd in fnmap.READ_AS_GAP:
        ent = fnmap.FN.get(cmd)       # reads of local UI state (message box button) are a menu gap, not a world read
    elif u['role'] in ('cond', 'expr'):
        if cmd in fnmap.SAFE_READ:
            return None
        if cmd in fnmap.PREF_READ:
            playerish = u['player'] or cmd in fnmap.INHERENT_PLAYER or \
                (cmd in ('getincell', 'getinworldspace', 'isininterior') and not u['subject'])
            if not playerish:
                return None
            red = fnmap.PREF_READ[cmd]
            in_story = kind in ('quest_script', 'stage', 'info_begin', 'info_end')
            return 'missing', {'effect': 'PartyAwareCond:' + red, 'capture': 'n/a', 'apply': 'missing',
                               'tag': 'condition-read', 'sev': 'high' if in_story else 'medium',
                               'note': 'player-ref read inside script logic; rules engine has reduceCondition() but no engine hook feeds it '
                                       '(Condition::Evaluate located, not hooked), so the script sees only the host player',
                               'basis': 'synccore/rules.h reduceCondition; internal lane notes round 2 item 7', 'npc': None}
        return None
    else:
        ent = fnmap.FN.get(cmd)
    if ent is None:
        return 'unclassified', {'effect': None, 'capture': 'unknown', 'apply': 'unknown', 'tag': 'unclassified', 'sev': 'low',
                                'note': 'command not in fnmap; needs a human decision', 'basis': '', 'npc': None}
    e = dict(ent)
    npc = ent.get('npc')
    if cmd in ('addtofaction', 'removefromfaction', 'removeperk', 'addperk', 'setfactionrank') and fnmap.COMPANION_RE.search(u['text']):
        e.update({'capture': 'missing', 'apply': 'missing', 'sev': 'high', 'tag': 'companion',
                  'note': 'companion bookkeeping (follower/teammate faction or companion perk): ownership must follow the recruiter; '
                          + ent['note']})
        return 'missing', e
    if npc and u['subject'] and u['subject'] not in C.PLAYER and not u['player']:
        e.update({'capture': npc['capture'], 'apply': npc['apply'], 'sev': npc['sev'], 'tag': npc.get('tag', e['tag'])})
    elif npc and not u['player'] and u['subject'] is None:
        # implicit subject = the script owner / INFO speaker (an NPC or object), not the player
        e.update({'capture': npc['capture'], 'apply': npc['apply'], 'sev': npc['sev'], 'tag': npc.get('tag', e['tag'])})
    plr = ent.get('plr')
    if plr and (u['player'] or u['subject'] in C.PLAYER):
        e.update({'capture': plr['capture'], 'apply': plr['apply'], 'sev': plr['sev'], 'tag': plr.get('tag', e['tag'])})
    st = worst(e['capture'] if e['capture'] in STAT_RANK else 'missing', e['apply'] if e['apply'] in STAT_RANK else 'missing')
    return st, e


def analyse(data):
    kq = {q['edid'].lower() for q in data.quests if q.get('edid')}
    kg = set(data.glob_by_edid)
    scripts = collect_scripts(data)
    sites = []
    census = collections.Counter()
    for s in scripts:
        uses = C.tokenize_script(s['source'], kq, kg, data.cmd_table)
        for u in uses:
            census[u['cmd']] += 1
            r = classify_use(u, s['kind'])
            if r is None:
                continue
            st, e = r
            sites.append({'cmd': u['cmd'], 'role': u['role'], 'status': st, 'effect': e['effect'], 'capture': e['capture'],
                          'apply': e['apply'], 'tag': e['tag'], 'sev': e['sev'], 'where': s['label'], 'kind': s['kind'],
                          'script': s.get('script'), 'line': u['line'], 'quest': s['quests'][0], 'quests': s['quests'],
                          'block': u['block'], 'subject': u['subject'], 'player': u['player'],
                          'conditional': u['conditional'], 'text': u['text']})
        # guest-originated triggers (object scripts reacting to the player)
        if s['kind'].startswith('dep_'):
            for n, raw in enumerate(s['source'].replace('\r', '').split('\n'), 1):
                m = re.match(r'\s*begin\s+(\w+)', raw, re.I)
                if m and m.group(1).lower() in TRIGGER_BLOCKS:
                    body = _block_body(s['source'], n)
                    if PLAYERISH.search(body):
                        sites.append({'cmd': m.group(1).lower(), 'role': 'block', 'status': 'missing',
                                      'effect': 'guest-originated trigger (host script sees only the host player)',
                                      'capture': 'missing', 'apply': 'n/a', 'tag': 'guest-trigger', 'sev': 'high',
                                      'where': s['label'], 'kind': s['kind'], 'script': s.get('script'), 'line': n,
                                      'quest': s['quests'][0], 'quests': s['quests'], 'block': m.group(1), 'subject': None,
                                      'player': True, 'conditional': False,
                                      'text': raw.strip()[:140]})
    return scripts, sites, census


def _block_body(src, start_line):
    lines = src.replace('\r', '').split('\n')
    out = [lines[start_line - 1]]
    for raw in lines[start_line:]:
        if re.match(r'\s*end\s*(;.*)?$', raw, re.I):
            break
        out.append(raw)
    return '\n'.join(out)


def hazards(data, scripts, sites):
    """Brief checklist -> counts + sample locations."""
    h = collections.OrderedDict()

    def add(name, pred, note=''):
        hits = [s for s in sites if pred(s)]
        h[name] = {'count': len(hits), 'note': note,
                   'sites': ['%s:%d %s' % (s['where'], s['line'], s['text'][:70]) for s in hits[:12]]}
    add('player-targeted effects (rewards/perks/AV/karma/rep)', lambda s: s['tag'] in ('reward', 'player-target', 'perk', 'karma-rep') and
        (s['player'] or s['cmd'] in ('rewardxp', 'rewardkarma', 'addreputation', 'setreputation', 'addreputationexact', 'addperk', 'removeperk', 'addnote')))
    add('spawns (PlaceAtMe)', lambda s: s['tag'] == 'spawn')
    add('companions (SetPlayerTeammate / followers)', lambda s: s['tag'] == 'companion' or s['cmd'] == 'setplayerteammate')
    add('essential/ghost flags', lambda s: s['tag'] == 'essential')
    add('MoveTo / teleports (player subject)', lambda s: s['tag'] == 'move' and (s['player'] or s['subject'] in C.PLAYER))
    add('MoveTo / teleports (any subject)', lambda s: s['tag'] == 'move')
    add('cinematics / player-control locks', lambda s: s['tag'] == 'cinematic')
    add('menus (ShowMessage/barter/repair/button reads)', lambda s: s['tag'] == 'menu')
    add('fast-travel locks', lambda s: s['tag'] == 'travel')
    add('radio', lambda s: s['tag'] == 'radio')
    add('save control', lambda s: s['tag'] == 'save')
    add('faction hostility (SetEnemy/SetAlly)', lambda s: s['tag'] == 'faction-hostility')
    add('faction membership', lambda s: s['tag'] == 'faction')
    add('guest-originated triggers', lambda s: s['tag'] == 'guest-trigger')
    add('player-ref reads in script logic', lambda s: s['tag'] == 'condition-read')
    # texty hazards: timers, endings, kill-everyone
    timers, endings, killall = [], [], []
    for sc in scripts:
        for n, raw in enumerate(sc['source'].replace('\r', '').split('\n'), 1):
            line = C.classify.strip_comment(raw)
            if re.search(r'\b(GameDaysPassed|GameHour)\b', line, re.I) and re.search(r'\b(set|if|elseif)\b', line, re.I):
                timers.append('%s:%d %s' % (sc['label'], n, line.strip()[:70]))
            if re.search(r'\b(StartQuest\s+VEnding|EndGame|SetInChargen|AddAchievement\s+39)\b', line, re.I):
                endings.append('%s:%d %s' % (sc['label'], n, line.strip()[:70]))
        dead = len(re.findall(r'\bGetDead\s*==\s*1', sc['source'], re.I)) + len(re.findall(r'\.GetDead\s*==\s*1', sc['source'], re.I))
        if dead >= 3:
            killall.append('%s (%d GetDead==1 checks)' % (sc['label'], dead))
    h['timers (GameDaysPassed/GameHour logic)'] = {'count': len(timers), 'note': '', 'sites': timers[:12]}
    h['endings / slideshows'] = {'count': len(endings), 'note': '', 'sites': endings[:12]}
    h['kill-everyone completion logic'] = {'count': len(killall), 'note': '', 'sites': killall[:12]}
    return h
