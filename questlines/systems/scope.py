"""Scope + classification for the `systems` questline.

Scope (see internal questline notes for the rationale):
  (c) every quest whose out/coverage/questgraph.json "line" is one of MY_LINES (the unassigned buckets), and
  (b) companion quests/systems on ANY line (COMPANIONS + COMPANION_SYSTEM below; non-own-line ones are flagged `overlap`).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qlib  # noqa: E402

MY_LINES = ('Systems & misc', 'Side: Misc / events', "DLC: Gun Runners' Arsenal")

# ---------------------------------------------------------------- companions (curated, every EDID is test-resolved)
# ref: engine refs of the companion (ED-E has three upgrade bodies); perk: the PLAYER perk granted on recruit
# (found by scanning INFO result scripts; see companions.json for the evidence); personal: stage-bearing personal quests.
COMPANIONS = {
    'Boone': dict(npc='CraigBoone', refs=['CraigBooneREF'], perk='Spotting', dialogue=['VDialogueCraigBoone'],
                  personal=['VMS39', 'VMS53'], timers=[], flags='bBooneHired', home='BooneHomeMarkerREF',
                  kind='humanoid'),
    'Cass': dict(npc='RoseofSharonCassidy', refs=['RoseofSharonCassidyREF'], perk='WhiskeyRose',
                 dialogue=['VDialogueCass', 'VFollowerDialogueRose'], personal=['VCassCompanion'],
                 timers=['VCassTimer'], flags='bCassHired', home='CassHomeMarkerREF', kind='humanoid'),
    'Veronica': dict(npc='Veronica', refs=['VeronicaREF'], perk='ScribeAssistant', dialogue=['VDialogueVeronica'],
                     personal=['VMS49'], timers=['VDialogueVeronicaTimer', 'VReactionVeronica'],
                     flags='bVeronicaHired', home='VeronicaHomeMarkerREF', kind='humanoid'),
    'Lily': dict(npc='Lily', refs=['LilyREF'], perk='StealthGirl', dialogue=['vDialogueLily'], personal=['VMS41'],
                 timers=['Lilysmedicinetimer', 'LilysPsychoticBreaks'], flags='bLilyHired',
                 home='LilyJacobstownMarker', kind='humanoid'),
    'ED-E': dict(npc=None, refs=['EDE1REF', 'EDE2REF', 'EDE3REF'], perk='EnhancedSensors',
                 dialogue=['vDialogueEDE'], personal=['vDialogueEDE'], timers=[], flags='bEDEHired',
                 home='EDEHomeMarker', kind='critter'),
    'Raul': dict(npc=None, refs=['RaulREF'], perk='FullMaintenance', dialogue=['VDialogueRaul'], personal=[],
                 timers=[], flags='RaulHired', home=None, kind='humanoid'),
    'Arcade': dict(npc=None, refs=['ArcadeREF'], perk='BetterHealing', dialogue=['VDialogueArcadeGannon'],
                   personal=['VMS54'], timers=['ArcadeRetrieveArmorTimer'], flags='ArcadeHired',
                   home='ArcadeHomeMarker', kind='humanoid'),
    'Rex': dict(npc='Rex', refs=['RexREF'], perk='SearchAndMark', dialogue=['VDialogueRex'], personal=['VMS24'],
                timers=[], flags='RexHired', home=None, kind='critter'),
}
# hire/fire/limit machinery and non-core follower quests (DLC ones belong to the DLC lines: overlap flagged)
COMPANION_SYSTEM = ['VNPCFollowers', 'Followers', 'FollowersHireRL3', 'P04CompanionFireQuest',
                    'NVDLC01NewVegasFollowerFireQuest', 'NVDLC01Followers', 'NVDLC02FollowersQuest',
                    'NVDLC02FollowersBanter', 'NVDLC04EDEFinaleQuest', 'NVDLC04DialogueEDE',
                    'NVDLC03DialogueArcade', 'NVDLC01ArcadeGoodbye', 'VES01VictorGSMyBodyguard']

DEBUG = {'Patch04Quest00': 'EditorID/name "Patch04Debug" (DLC-uninstall cleanup; not start_enabled)',
         'VDialogueTEST': 'name "Test Dialogue"',
         'vDialogueTestaclesDebug': 'name "Debug Dialogue"'}

ENDING = {'VEnding', 'VEndingBinks', 'VMQEndStates', 'VMQEndStatesTheStrip', 'VMQEndStatesFreeside', 'FadeToCreditsTimer',
          'VLegateShowdown', 'VHDKimballSpeech', 'VStoryStateQuest', 'SecuritronVaultExplodeQuest'}

# curated corrections found by reading the scripts (each is test-checked to exist in quests.json)
OVERRIDES = {
    'ControlRumble': ('controller', 'per-player', 'gamepad rumble sequence: local to the controller of the player who triggered it'),
    'PerkWildWasteland': ('perk_effect', 'shared', 'enables a world ref while the player has the Wild Wasteland perk; world ref is shared, so reduce HasPerk over the party (any)'),
    'FriendOfTheNightQuest': ('perk_effect', 'per-player', 'applies/removes night-vision image-space per player from Player.HasPerk'),
    'MeatOfChampions': ('perk_effect', 'per-player', 'counts champions the player ate and grants MeatOfChampionsPerk to that player'),
    'Generic': ('perk_effect', 'per-player', 'generic perk/addiction/XP handler keyed off the player (HasMagicEffect, perks)'),
    'FerociousLoyaltyQuest': ('companion_system', 'per-player', 'companion buff when the OWNER player health < 50% (Player.GetHealthPercentage); evaluate per owner, apply to that owner companions'),
    'VDoctors': ('quest_like', 'per-player', 'one shared quest stepped by every doctor (setstage 10/20) for a treatment scene: restrain + lock controls + fade + restore AV on the patient; per-patient state must not be one shared stage (reported by Q-strip, questlines/strip/policy_review.md F4)'),
    'Doctors': ('quest_like', 'per-player', 'doctor treatment stages (same template as VDoctors): per-patient scene (reported by Q-strip)'),
    'VDLCPackQuest': ('dlc_setup', 'per-player', 'IsDLCInstalled + item grants are per machine/player'),
    'NVDLC05MQ00': ('dlc_setup', 'per-player', 'Gun Runners Arsenal setup: formlist/merchant container ops are local, unsaved data; must run on every machine'),
    'VNightTimeQuest': ('story_support', 'host-only', 'sets global VNight from the clock; global replicates from the host'),
    'AutoDocTimerQuest': ('timer', 'per-player', 'fade-to-black + EnablePlayerControls for the player in the AutoDoc scene (PerPlayerScene); only the Caesar conversation start is shared'),
    'ImplantTimerQuest': ('timer', 'per-player', 'fade-to-black + EnablePlayerControls for the implanted player (PerPlayerScene)'),
}

TYPES = ['controller', 'perk_effect', 'dlc_setup', 'debug', 'companion_personal', 'companion_system', 'companion_dialogue', 'companion_timer', 'radio',
         'encounter', 'achievement', 'timer', 'ending', 'scripted_scene', 'casino', 'chargen', 'dialogue_freeform',
         'dialogue_generic', 'story_support', 'quest_like', 'other']
POLICIES = ('host-only', 'shared', 'per-player', 'exclude')

FN_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def companion_membership():
    """edid -> (companion name or 'system', role)"""
    m = {}
    for name, c in COMPANIONS.items():
        for e in c['personal']:
            m[e] = (name, 'personal')
        for e in c['dialogue']:
            m.setdefault(e, (name, 'dialogue'))
        for e in c['timers']:
            m[e] = (name, 'timer')
    for e in COMPANION_SYSTEM:
        m.setdefault(e, ('system', 'system'))
    return m


def line_of():
    return {n['id']: n for n in qlib.questgraph()['nodes']}


def owned_scripts(q, infos_by_q):
    """(kind, location, source) for the quest's own script, stage result scripts and INFO begin/end scripts."""
    out = []
    sd = q.get('script_data')
    if sd and sd.get('source'):
        out.append(('quest', 'quest script', sd['source']))
    for st in q['stages']:
        for n, e in enumerate(st['entries']):
            sc = e.get('script')
            if sc and sc.get('source'):
                out.append(('stage', 'stage %s' % st['index'] + ('.%d' % n if n else ''), sc['source']))
    seen = {}
    for i in infos_by_q.get(q['ref'], []):
        n = seen[i['ref']] = seen.get(i['ref'], 0) + 1
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc.get('source'):
                out.append(('info', 'info %s %s' % (i['ref'] + ('#%d' % n if n > 1 else ''), k), sc['source']))
    return out


def infos_by_quest():
    d = {}
    for i in qlib.infos():
        d.setdefault(i['quest'], []).append(i)
    return d


def signals(q, infos_by_q):
    """Cheap script signals used by the classifier and the policy text."""
    fns = {}
    src_all = []
    n_result = 0
    for kind, loc, src in owned_scripts(q, infos_by_q):
        src_all.append(src)
        if kind == 'info':
            n_result += 1
        for _n, fn, subj, args, _line in qlib.statements(src):
            fns[fn.lower()] = fns.get(fn.lower(), 0) + 1
    text = '\n'.join(src_all)
    return {
        'fns': fns,
        'result_scripts': n_result,
        'uses_player': bool(re.search(r'\bplayer(ref)?\b', text, re.I)),
        'achievement': bool(re.search(r'\b(AddAchievement|IncrementScriptedChallenge)\b', text, re.I)),
        'gamemode': bool(re.search(r'begin\s+gamemode', text, re.I)),
    }


def classify(q, node, comp, sig):
    """-> (type, policy, reason). Order matters: first match wins."""
    e = q['edid']
    nm = q.get('name') or ''
    n_st, n_inf = len(q['stages']), len(q['infos'])
    if e in DEBUG:
        return 'debug', 'exclude', 'owner decision: debug quests excluded (%s)' % DEBUG[e]
    if comp:
        who, role = comp
        t = {'personal': 'companion_personal', 'system': 'companion_system', 'dialogue': 'companion_dialogue',
             'timer': 'companion_timer'}[role]
        return t, 'shared', 'companion state is party-visible; recruiter owns the companion (CompanionOwner) [%s]' % who
    if e == 'AchievementQuest' or sig['achievement'] or re.search(r'achievement|challenge', e, re.I):
        return 'achievement', 'per-player', 'tracks the local player (level/stat) and calls AddAchievement; each machine earns its own'
    if re.search(r'radio|jukebox|holotape|^BH0\d|ChipHistory', e + ' ' + nm, re.I) and n_st == 0:
        return 'radio', 'per-player', 'radio/audio playback is local to each player (station selection is a Pip-Boy UI state)'
    if e in ENDING:
        return 'ending', 'shared', 'ending/world-state sequence: host runs it once for the whole party (SlideshowEnd / endstate)'
    if re.search(r'casino|winnings', e, re.I):
        return 'casino', 'per-player', 'winnings level is a per-player stat; script rewards Player.AddItem'
    if e in ('VCG04', 'CG00', 'CG02', 'VCG01Test'):
        return 'chargen', 'per-player', 'character-creation/revision content: each player creates their own character'
    if re.match(r'^(VER|ER)[A-Z0-9]', e) or re.match(r'^VES\d', e) and n_st == 0 or 'ncounter' in nm \
            or e in ('VOuterVegasFiends', 'VES00TemplateScript'):
        return 'encounter', 'host-only', 'random/special encounter spawner: host spawns, NV:MP syncs actors (L6)'
    if re.search(r'timer|restarter|enabler$', e, re.I) or re.search(r'timer', nm, re.I):
        return 'timer', 'host-only', 'time-driven helper: runs on the host only; consequences ship as effects'
    if re.search(r'SS\d$|FX$|Scripted Scene|Sequence', e + ' ' + nm):
        return 'scripted_scene', 'host-only', 'scripted scene/FX sequence: host-run; party rules (tether/controls) apply'
    if re.search(r'dialogue|freeform|dialog|bark|generic|nobark|greet|chatter|friendlyfire|^Dialogue', e, re.I) \
            and n_st == 0:
        if n_inf == 0 and not sig['gamemode']:
            return 'dialogue_generic', 'host-only', 'dialogue shell without INFOs'
        if sig['result_scripts'] > 0 and n_inf:
            return 'dialogue_freeform', 'shared', 'freeform dialogue with result scripts: initiator picks, result ships as a txn'
        return 'dialogue_generic', 'host-only', 'ambient/line-only dialogue (no result scripts): voiced by the host NPC'
    if n_st > 0:
        return 'quest_like', 'shared', 'has stages/objectives: normal shared quest state'
    if re.search(r'support|control|reaction|reputation|faction|lighting|security|squad|sniffer|disguise|conversation|'
                 r'confiscation|snowglobe|transition|move|explore|euclid|chamber|dust|autodoc|implant|couple|hit', e, re.I):
        return 'story_support', 'host-only', 'engine/world helper script: host-only, effects replicate'
    if n_inf and sig['result_scripts']:
        return 'dialogue_freeform', 'shared', 'INFO result scripts present: shared dialogue results'
    return 'other', 'host-only', 'no stages/INFOs of note: host-only helper'


def build_scope():
    """-> list of record dicts, one per in-scope quest (sorted by EDID)."""
    Q = qlib.quest_by_edid()
    node = line_of()
    ibq = infos_by_quest()
    comp = companion_membership()
    graph = qlib.questgraph()
    in_edges = {}
    for ed in graph['edges']:
        if ed.get('strength') == 'hard':
            in_edges.setdefault(ed['dst'], set()).add(ed['src'])
    risk = qlib.risk()
    rules = qlib.rules()['quests']
    dials = {}
    recs = []
    for e, q in sorted(Q.items()):
        n = node[e]
        c = comp.get(e)
        own = n['line'] in MY_LINES
        if not own and not c:
            continue
        sig = signals(q, ibq)
        t, pol, why = classify(q, n, c, sig)
        if e in OVERRIDES:
            t, pol, why = OVERRIDES[e]
        # a quest whose variables other quests read hard is story-visible even if it is a "helper"
        readers = sorted(x for x in in_edges.get(e, ()) if x != e)
        if pol == 'host-only' and readers and t not in ('encounter',) and e not in OVERRIDES:
            pol, why = 'shared', why + '; its vars are read by quests %s' % ','.join(readers[:5])
        r = risk[q['ref']]
        recs.append({
            'edid': e, 'ref': q['ref'], 'name': q.get('name'), 'plugin': q['plugin'], 'graph_line': n['line'],
            'own_line': own, 'overlap': (not own),
            'companion': ({'who': c[0], 'role': c[1]} if c else None),
            'type': t, 'policy': pol, 'policy_reason': why, 'tier': r['tier'], 'score': r['score'],
            'stages': [s['index'] for s in q['stages']], 'objectives': [o['index'] for o in q['objectives']],
            'infos': len(q['infos']), 'topics': len(q['topics']), 'result_scripts': sig['result_scripts'],
            'start_enabled': 'start_enabled' in q['flags'], 'flags': q['flags'],
            'hard_readers': readers, 'in_rules_json': e in rules,
        })
    return recs
