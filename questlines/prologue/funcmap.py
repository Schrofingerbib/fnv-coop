"""Script function -> synccore Effect type + L5 capture/apply status, for the prologue questline.

Status values (honest, from internal lane notes rounds 1-8, plugin/hooks/{engine_hooks,apply_remote}.cpp and
synccore/include/fnvcoop/effect.h; NOTHING here is proven in the real game for the two-instance case yet):
  supported  an Effect exists, the host hook records it, the guest applier has an engine call for it
  partial    works for some subjects/targets or only indirectly (see note)
  missing    no Effect type, or the Effect type exists but nothing captures / applies it
  n/a        no wire effect needed (local-only UI, host-authoritative AI that NV:MP actor sync covers, or a local script var)

Severity is the co-op impact when capture or apply is not `supported`: blocker > high > medium > low > none.
Hazard tags follow internal questline notes: player-targeted, reward, spawn, companion, essential, move, control, menu,
timer, radio, ending, kill-all, faction, perk, quest-state, ai, ui, inventory, topic, map.
"""

SEVERITY_ORDER = ['blocker', 'high', 'medium', 'low', 'none']


def E(effect, capture, apply, severity='none', hazards=(), note=''):
    return dict(effect=effect, capture=capture, apply=apply, severity=severity, hazards=list(hazards), note=note)


# keyed by lowercase command name. 'subject' variants are resolved in classify_statement().
FUNCS = {
    'setstage': E('SetStage', 'supported', 'supported', hazards=['quest-state'],
                  note='hook on TESQuest::SetStage entry; guest SetStage runs with the script gate'),
    'setobjectivedisplayed': E('SetObjective', 'supported', 'supported', hazards=['quest-state']),
    'setobjectivecompleted': E('SetObjective', 'supported', 'supported', hazards=['quest-state']),
    'completeallobjectives': E('CompleteAllObjectives', 'partial', 'supported', 'low', ['quest-state'],
                               'objective SetState hook should record each objective as SetObjective; the CompleteAllObjectives '
                               'effect itself is never emitted (unverified in game)'),
    'completequest': E('CompleteQuest', 'supported', 'supported', hazards=['quest-state']),
    'failquest': E('FailQuest', 'supported', 'supported', hazards=['quest-state']),
    'startquest': E('StartQuest', 'supported', 'supported', hazards=['quest-state']),
    'stopquest': E('StopQuest', 'supported', 'supported', hazards=['quest-state']),
    'setglobalvalue': E('SetGlobal', 'supported', 'supported', hazards=['quest-state'],
                        note='captured by diffing all TESGlobals around the outermost scope'),
    'setquestvar': E('SetScriptVar', 'supported', 'supported', hazards=['quest-state'],
                     note='quest-owned variables only: diff of every quest ScriptEventList around the scope'),
    'setrefvar': E('SetScriptVar', 'missing', 'supported', 'medium', ['quest-state'],
                   note='ref/NPC-owned script variables (Set Ref.var) are NOT captured (L5 round 2, item 11); the applier can '
                        'write them (ExtraScript event list) once something emits the effect'),
    'setlocal': E(None, 'n/a', 'n/a', note='local variable of the running script; not shared state'),
    'enable': E('EnableRef', 'supported', 'supported', 'low', ['quest-state'],
                'records only on a state CHANGE (SetDisabled); an already-enabled ref emits nothing'),
    'disable': E('DisableRef', 'supported', 'supported', 'low', ['quest-state'], 'same state-change-only caveat as enable'),
    'resurrect': E('ResurrectActor', 'supported', 'partial', 'low', ['quest-state'],
                   'capture proven live on Chet; apply goes through a console line (prid + Resurrect)'),
    'kill': E('KillActor', 'supported', 'partial', 'low', ['kill-all'], 'apply via console line'),
    'killactor': E('KillActor', 'supported', 'partial', 'low', ['kill-all'], 'apply via console line'),
    'moveto': E('MoveRef', 'supported', 'partial', 'high', ['move'],
                'captured from MoveToMarker params; apply is a console line and cross-cell needs a marker ref. The '
                'partyWarp flag (all players move together) is never set by the hook'),
    'movetomarker': E('MoveRef', 'supported', 'partial', 'high', ['move'], 'see moveto'),
    'movetofade': E('MoveRef', 'supported', 'partial', 'medium', ['move'],
                    'MoveToMarkerWithFade (0x5CD0E0) is hooked and captured like MoveToMarker'),
    'additem': E('AddItem', 'supported', 'supported', hazards=['reward', 'inventory'],
                 note='player target only: container/NPC AddItem is not captured (L5 limits). Recipient routing (RewardAll / '
                      'QuestItemDup) is a host policy decision, hook emits Recipient::dflt()'),
    'removeitem': E('RemoveItem', 'supported', 'supported', 'low', ['inventory', 'player-targeted'],
                    'player target only; policy must choose RemoveItemPerOwner (hand-in) vs remove-from-all (quest item)'),
    'rewardxp': E('ModActorValue', 'supported', 'supported', hazards=['reward'], note='XP pseudo-AV 0x10000; RewardAll'),
    'rewardkarma': E('ModKarma', 'supported', 'supported', hazards=['faction'], note='shared karma (owner decision)'),
    'addreputation': E('ModReputation', 'supported', 'supported', hazards=['faction'], note='shared reputation (owner decision)'),
    'setav': E('ModActorValue', 'partial', 'supported', 'medium', ['player-targeted'],
               'hook diffs player-target AV changes only; an NPC subject (SetAV on a ref) is not captured'),
    'addperk': E('SetPerk', 'missing', 'missing', 'high', ['perk', 'player-targeted'],
                 'SetPerk effect type exists; AddPerk is only a block-only suppression hook on guests (not recorded, not applied)'),
    'removeperk': E('SetPerk', 'missing', 'missing', 'high', ['perk', 'player-targeted'], 'see addperk'),
    'addtofaction': E('SetFactionRank', 'missing', 'missing', 'high', ['faction'],
                      'owner decision: faction membership/rank is shared. SetFactionRank effect type exists; the hook is '
                      'suppression-only (BLOCK_CMD) and never records; the applier returns Unsupported'),
    'removefromfaction': E('SetFactionRank', 'missing', 'missing', 'high', ['faction'], 'see addtofaction'),
    'setfactionrank': E('SetFactionRank', 'missing', 'missing', 'high', ['faction'], 'see addtofaction'),
    'setpcenemyoffaction': E(None, 'missing', 'missing', 'medium', ['faction'],
                             'player-vs-faction hostility: no Effect type and no hook; shared by the party'),
    'setally': E(None, 'missing', 'missing', 'medium', ['faction', 'ai'], 'faction relation; no Effect type, suppressed on guests only'),
    'setenemy': E(None, 'missing', 'missing', 'medium', ['faction', 'ai'], 'faction relation; no Effect type'),
    'startcombat': E(None, 'n/a', 'n/a', 'low', ['ai'],
                     'host-run (guest copy suppressed); combat itself rides on NV:MP actor sync, not on a story effect'),
    'stopcombat': E(None, 'n/a', 'n/a', 'low', ['ai'], 'see startcombat'),
    'addscriptpackage': E(None, 'missing', 'missing', 'medium', ['ai'],
                          'AI package state of an NPC is not an Effect; correct only if the NPC is host-owned and NV:MP syncs '
                          'its behaviour. Hook is suppression-only on guests'),
    'removescriptpackage': E(None, 'missing', 'missing', 'medium', ['ai'], 'see addscriptpackage'),
    'resetai': E(None, 'n/a', 'n/a', 'low', ['ai'], 'host-local AI re-evaluation'),
    'evp': E(None, 'n/a', 'n/a', 'low', ['ai'], 'EvaluatePackage: host-local AI re-evaluation'),
    'setrestrained': E(None, 'missing', 'missing', 'medium', ['ai'], 'NPC restrained flag (captive/hostage); no Effect type'),
    'ignorecrime': E(None, 'missing', 'missing', 'low', ['ai'], 'crime-system flag on an NPC; no Effect type'),
    'setownership': E(None, 'missing', 'missing', 'low', ['quest-state'], 'ownership of a ref changes theft rules; no Effect type'),
    'markfordelete': E(None, 'missing', 'missing', 'low', ['quest-state'],
                       'permanent ref removal; closest Effect is DisableRef (not emitted for this command)'),
    'showmap': E(None, 'missing', 'missing', 'medium', ['map', 'ui'],
                 'reveals a map marker on the Pip-Boy map (fast-travel unlock + marker). No Effect type; per-player vs shared '
                 'discovered-locations is undecided. ShowMap also pops the map UI'),
    'showmessage': E(None, 'missing', 'missing', 'low', ['menu', 'ui'],
                     'MessageBox / HUD message; local UI. Needs an event so every player sees story messages (not state)'),
    'showbartermenu': E(None, 'partial', 'partial', 'low', ['menu'],
                        'service sub-session of the dialogue lane (docs/dialogue-design.md 7.2): initiator trades, others spectate'),
    'openteammatecontainer': E(None, 'n/a', 'n/a', 'low', ['menu', 'companion'], 'opens the companion inventory UI of the initiator'),
    'addnote': E(None, 'missing', 'missing', 'high', ['reward', 'inventory', 'player-targeted'],
                 'Pip-Boy note (NOTE form): no Effect type. Quest scripts poll GetHasNote, so a missed AddNote on a guest '
                 'can stall a quest'),
    'removenote': E(None, 'missing', 'missing', 'low', ['inventory', 'player-targeted'], 'no Effect type'),
    'addtopic': E(None, 'missing', 'missing', 'medium', ['topic'],
                  'unlocks a dialogue topic on the player; no Effect type (topic knowledge share flag is undecided)'),
    'sayto': E('ForcedDialogue', 'missing', 'missing', 'medium', ['ui', 'ai'],
               'ForcedDialogue effect type exists; Cmd_SayTo (0x5C9100) is located but not hooked'),
    'setactorvalue': E('ModActorValue', 'partial', 'supported', 'medium', ['player-targeted'],
                       'alias of setav; a script-self / NPC subject is not captured'),
    'damageactorvalue': E('ModActorValue', 'partial', 'supported', 'medium', ['ai'],
                          'DamageAV hook diffs player-target changes only; crippling an NPC limb condition is not captured'),
    'activate': E(None, 'missing', 'missing', 'medium', ['ai', 'quest-state'],
                  'script-driven Activate of a ref (runs its OnActivate chain); only a suppression hook exists on guests'),
    'sendassaultalarm': E(None, 'missing', 'missing', 'medium', ['faction', 'ai'],
                          'raises the crime alarm against the player for a faction; no Effect type, party-shared hostility is undecided'),
    'removeme': E(None, 'missing', 'missing', 'low', ['inventory'],
                  'the script deletes its own object (e.g. the looted note object); no Effect type'),
    'say': E(None, 'missing', 'missing', 'low', ['ui', 'ai'], 'NPC voice line / bark; spectator playback belongs to the dialogue lane'),
    'addachievement': E(None, 'n/a', 'n/a', 'none', ['ui'], 'Steam achievement of the local player; intentionally local'),
    'applyimagespacemodifier': E(None, 'n/a', 'n/a', 'low', ['ui'], 'screen effect on the local camera'),
    'playbink': E(None, 'missing', 'missing', 'medium', ['control', 'ui'], 'movie; local'),
}

# commands the scanner met that are not in FUNCS are reported as unclassified (severity 'unknown') rather than guessed.


def classify_statement(rec, quest_names):
    """-> dict(effect, capture, apply, severity, hazards, note, fn_key) for one scanner record, or None for plain reads.

    Resolves the subject-dependent cases: player vs NPC for AddItem/RemoveItem/SetAV, quest-owned vs ref-owned variables.
    """
    kind, fn = rec['kind'], rec['fn']
    if kind == 'cond':
        return None
    if kind == 'setvar':
        owner = (rec.get('subject') or '').lower()
        key = 'setquestvar' if owner in quest_names else 'setrefvar'
        e = dict(FUNCS[key])
        e['fn_key'] = key
        return e
    key = fn
    if kind == 'setglobal':
        key = 'setglobalvalue'
    if kind == 'setlocal':
        key = 'setlocal'
    base = FUNCS.get(key)
    if base is None:
        return dict(effect=None, capture='unknown', apply='unknown', severity='unknown', hazards=[],
                    note='not in the function table: unclassified, needs a human look', fn_key=key)
    e = dict(base)
    e['fn_key'] = key
    subj = (rec.get('subject') or '').lower()
    on_player = subj in ('player', 'playerref') or (not subj and key in ('rewardxp', 'rewardkarma', 'addreputation',
                                                                            'addperk', 'removeperk', 'addnote', 'removenote'))
    if key in ('additem', 'removeitem') and not on_player:
        e.update(capture='missing', apply='missing', severity='medium',
                 note='NPC/container inventory target: not captured by the hook and the applier only edits the player')
        e['hazards'] = list(e['hazards']) + ['npc-inventory']
    if key == 'setav' and on_player:
        e.update(capture='supported', severity='none', note='player-target AV delta: captured by before/after diff')
    if key in ('moveto', 'movetomarker', 'movetofade'):
        if on_player:
            e['hazards'] = e['hazards'] + ['player-targeted', 'cell-transition']
            e['note'] += ' | PLAYER subject: must become a party-warp (L10 rule PartyWarp)'
        else:
            e.update(severity='medium')
    return e


def worst(a, b):
    order = SEVERITY_ORDER + ['unknown']
    return a if order.index(a) <= order.index(b) else b
