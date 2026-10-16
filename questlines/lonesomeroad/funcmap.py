"""Script function -> synccore Effect type -> L5 capture/apply status, for the Lonesome Road gap analysis.

Sources of truth (read when this table was written, 2026-10-16):
  * synccore/include/fnvcoop/effect.h          Effect variant (33 types)
  * plugin/hooks/engine_hooks.cpp              host capture hooks + guest block-only hooks
  * plugin/hooks/apply_remote.cpp              RemoteApplier::apply (what a replica can really apply)
  * internal lane notes                           "Not done / limits", rounds 2-4

Status vocabulary (field `status`):
  supported       host captures it AND RemoteApplier applies it (exact effect exists)
  partial         the Effect type exists but one half is missing (no host capture, or no apply, or only for some targets)
  missing         no Effect type / no hook: the mutation only ever happens on the machine that ran the script (the host)
  host-only       NPC/AI/animation state owned by the host; guests see it only through NV:MP actor replication (unverified)
  read            no mutation: a query. Player-referencing queries are flagged P-REF and need the PartyAwareCond rule.
  presentation    sound/music/image-space/FX with no state; invisible to guests because only the host runs the script

Severity (field `sev`): critical = can soft-lock or fork the story; high = shared story state forks; medium = per-player
loss or story-tied presentation; low = cosmetic / harmless; info = reads and bookkeeping.
"""

SUP, PAR, MIS, HOST, READ, PRES = 'supported', 'partial', 'missing', 'host-only', 'read', 'presentation'

# hazard tags (brief section 3): player-targeted, spawn, companion, essential, teleport, cell, cinematic, menu, timer,
# radio, ending, killall.  Extra tags used here: guest-activation, world-change, ui-local, formlist.


def E(effect, cap, app, status, sev, hz=(), note=''):
    return {'effect': effect, 'capture': cap, 'apply': app, 'status': status, 'sev': sev, 'hazards': list(hz), 'note': note}


# fn (lowercase) -> entry, or {'player': entry, 'other': entry} when the subject decides.
F = {}

# ---- quest state -------------------------------------------------------------------------------------------------
F['setstage'] = E('SetStage', 'yes', 'yes', SUP, 'info', note='cross-quest SetStage chains are replayed as ordered effects')
F['setobjectivedisplayed'] = E('SetObjective', 'yes', 'yes', SUP, 'info')
F['setobjectivecompleted'] = E('SetObjective', 'yes', 'yes', SUP, 'info')
F['completeallobjectives'] = E('CompleteAllObjectives', 'yes', 'yes', SUP, 'info', note='L5 added capture (Cmd_CompleteAllObjectives hook) and apply after this table was first written; re-verified by run_tests.py')
F['completequest'] = E('CompleteQuest', 'yes', 'yes', SUP, 'info')
F['failquest'] = E('FailQuest', 'yes', 'yes', SUP, 'info')
F['startquest'] = E('StartQuest', 'yes', 'yes', SUP, 'info')
F['stopquest'] = E('StopQuest', 'yes', 'yes', SUP, 'info')
F['forceactivequest'] = E(None, 'no', 'no', MIS, 'low', ('player-targeted',),
                          note='tracked/active quest is a per-player Pip-Boy pointer; no effect type')
F['showmap'] = E(None, 'no', 'no', MIS, 'low', ('player-targeted',),
                 note='reveals a map marker in the calling player\'s Pip-Boy; no effect type (per-player map data)')
F['incrementscriptedchallenge'] = E(None, 'no', 'no', MIS, 'low', ('player-targeted',), 'challenge counters are per-player')
F['addachievement'] = E(None, 'no', 'no', MIS, 'low', ('player-targeted',), 'achievements are per-profile; commented out in the data in the places seen')
F['addformtoformlist'] = E(None, 'no', 'no', MIS, 'medium', ('formlist',),
                           note='runtime FormList edit; quest scripts are skipped on guests (Role::Guest gate) so a guest that never ran '
                                'NVDLC04MQ00SCRIPT lacks every DLC entry in EmbeddedWeapons / perk weapon lists / repair lists')
F['additemtoleveledlist'] = E(None, 'no', 'no', MIS, 'low', ('formlist',), 'runtime leveled-list edit, same guest gap as AddFormToFormList')
F['addtopic'] = E(None, 'no', 'no', MIS, 'low', note='topic list state is per conversation partner on the initiator')
F['set questvar'] = E('SetScriptVar', 'yes', 'yes', SUP, 'info', note='quest variables: L5 diffs every quest ScriptEventList around the outermost scope')
F['set objvar'] = E('SetScriptVar', 'no', 'yes', PAR, 'high', ('object-var',),
                    note='ref/object-script variable: RemoteApplier can write it, but L5 cannot capture it (no hook, unbounded set). '
                         'Object scripts also run on guests, so each machine keeps its own copy of the variable')
F['set global'] = E('SetGlobal', 'yes', 'yes', SUP, 'info')

# ---- world state -------------------------------------------------------------------------------------------------
F['enable'] = E('EnableRef', 'yes', 'yes', SUP, 'info', note='state change only; refs unresolvable on a replica come back FormMissing')
F['disable'] = E('DisableRef', 'yes', 'yes', SUP, 'info')
F['markfordelete'] = E(None, 'no', 'no', MIS, 'low', note='ref deletion; disable is captured, deletion is not')
F['setdestroyed'] = E(None, 'no', 'no', MIS, 'medium', ('world-change',),
                      note='destroyed flag on a placed ref (launch console, terminals): no effect type')
F['unlock'] = E('SetLock', 'no', 'no', PAR, 'high', ('world-change',),
                note='SetLock exists; host capture is block-only (Cmd_UnLock is hooked to drop it on guests, not to record it); no apply branch')
F['lock'] = E('SetLock', 'no', 'no', PAR, 'high', ('world-change',), note='same as Unlock')
F['setopenstate'] = E(None, 'no', 'no', MIS, 'medium', ('world-change',), 'door/container open state: no effect type')
F['playgroup'] = E(None, 'no', 'no', PRES, 'low', note='animation group on a ref (rocket, console lever, klaxon); state-bearing only for the final scene')
F['playidle'] = E(None, 'no', 'no', HOST, 'low')
F['placeatme'] = E(None, 'no', 'no', MIS, 'medium', ('spawn',),
                   note='spawn: no effect type and no hook. In this DLC every seen argument is an explosion/rumble/FX base form, never an actor; '
                        'severity is raised where the FX is a story beat (missile launch, ED-E death)')
F['setpos'] = E('MoveRef', 'yes', 'yes', SUP, 'low', ('teleport',), 'only meaningful on persistent refs; explosions are fresh refs with no wire identity')
F['moveto'] = {'player': E('MoveRef', 'yes', 'yes', PAR, 'critical', ('teleport', 'cell'),
                           note='capture+apply exist, but a lone MoveRef moves one player: needs the PartyWarp rule (kind exists in rules.json; synccore '
                                'RulesEngine lists PartyWarp as unsupported: needs engine refs)'),
               'other': E('MoveRef', 'yes', 'yes', SUP, 'low', ('teleport',))}
F['movetomarkerwithfade'] = F['moveto']
F['purgecellbuffers'] = E(None, 'no', 'no', READ, 'info', note='local engine housekeeping before a cell change')
F['autosave'] = E(None, 'no', 'no', MIS, 'medium', ('save',),
                  note='SaveSuppress effect + rule exist, but no hook on Autosave/PlayerNoSavePackage; a guest autosave in a scripted scene is '
                       'not blocked')
F['disablenavmesh'] = E(None, 'no', 'no', HOST, 'low', note='nav data matters only where the AI runs (host)')
F['enablenavmesh'] = F['disablenavmesh']
F['entertrigger'] = E(None, 'no', 'no', HOST, 'low', ('player-targeted',), note='manual OnTriggerEnter Player: runs on the host with the host player')

# ---- actors / AI -------------------------------------------------------------------------------------------------
F['setessential'] = E(None, 'no', 'no', HOST, 'high', ('essential',),
                      note='essential/ghost flags are host actor state; guests learn HP outcomes via NV:MP only (unverified)')
F['setghost'] = E(None, 'no', 'no', HOST, 'medium', ('essential',))
F['setrestrained'] = E(None, 'no', 'no', HOST, 'low')
F['setunconscious'] = E(None, 'no', 'no', HOST, 'low')
F['setcombatstyle'] = E(None, 'no', 'no', HOST, 'low')
F['setenemy'] = E(None, 'no', 'no', HOST, 'medium', note='faction relationship change (Ulysses turns hostile)')
F['setally'] = E(None, 'no', 'no', HOST, 'medium')
F['addscriptpackage'] = E(None, 'no', 'no', HOST, 'low', note='block-only on guests; host AI package is authoritative')
F['removescriptpackage'] = F['addscriptpackage']
F['evaluatepackage'] = E(None, 'no', 'no', HOST, 'info')
F['resetai'] = E(None, 'no', 'no', HOST, 'info')
F['startcombat'] = E(None, 'no', 'no', HOST, 'medium', note='block-only on guests; host owns combat starts')
F['stopcombat'] = F['startcombat']
F['setignorefriendlyhits'] = E(None, 'no', 'no', HOST, 'info')
F['ignorecrime'] = E(None, 'no', 'no', HOST, 'info')
F['killactor'] = E('KillActor', 'yes', 'yes', SUP, 'medium', ('killall',), 'ED-E sacrifice uses KillActor; proven in game for generic actors')
F['restoreactorvalue'] = {'player': E(None, 'no', 'no', MIS, 'medium', ('player-targeted',),
                                      note='ModActorValue covers ModAV/SetAV/ForceAV/DamageAV only; RestoreAV is not hooked'),
                          'other': E(None, 'no', 'no', HOST, 'info')}
F['resethealth'] = {'player': E(None, 'no', 'no', MIS, 'medium', ('player-targeted',), 'full heal of the calling player; no hook'),
                    'other': E(None, 'no', 'no', HOST, 'info')}
F['setactorvalue'] = E('ModActorValue', 'yes', 'yes', SUP, 'medium', ('player-targeted',), 'delta computed from before/after')
F['fireweapon'] = E(None, 'no', 'no', PRES, 'low')
F['useweapon'] = E(None, 'no', 'no', PRES, 'low', note='ED-E fires the unlock zapper in the finale; visual')
F['castimmediateonself'] = {'player': E(None, 'no', 'no', MIS, 'medium', ('player-targeted',),
                                        note='spell cast on the calling player (slow-mo, rest-up restore): no effect type'),
                            'other': E(None, 'no', 'no', PRES, 'low')}
F['dispel'] = E(None, 'no', 'no', PRES, 'low')
F['dispelallspells'] = {'player': E(None, 'no', 'no', MIS, 'low', ('player-targeted',)), 'other': E(None, 'no', 'no', PRES, 'low')}
F['addspell'] = E(None, 'no', 'no', MIS, 'low', ('player-targeted',))
F['removespell'] = E(None, 'no', 'no', MIS, 'low', ('player-targeted',))
F['modweaponhealthperc'] = E(None, 'no', 'no', MIS, 'low', ('player-targeted',))
F['setscale'] = E(None, 'no', 'no', PRES, 'info')

# ---- player-targeted (owner decisions: rewards -> all, karma/rep/faction -> shared, quest items -> all) ---------------
F['rewardxp'] = E('ModActorValue', 'yes', 'yes', SUP, 'info', ('player-targeted',), 'XP pseudo-AV 0x10000; RewardAll rule routes the full amount to every player')
F['additem'] = {'player': E('AddItem', 'yes', 'yes', SUP, 'info', ('player-targeted',), 'RewardAll / QuestItemDup route it to every player'),
                'other': E('AddItem', 'no', 'no', MIS, 'medium', ('world-change',),
                           note='item added to a container or NPC: AddItem has no target field (docs/engine-addresses.md section 5 proposal)')}
F['additemns'] = F['additem']
F['removeitem'] = {'player': E('RemoveItem', 'yes', 'yes', SUP, 'info', ('player-targeted',), 'hand-in semantics via RemoveItemPerOwner'),
                   'other': E('RemoveItem', 'no', 'no', MIS, 'medium', ('world-change',), 'container/NPC target: same gap as AddItem')}
F['additemhealthpercent'] = E(None, 'no', 'no', MIS, 'medium', ('player-targeted',),
                              note='not the hooked Cmd_AddItem; the loot-chest endgame armour (Duster variants) is added this way')
F['removeallitems'] = E(None, 'no', 'no', MIS, 'medium', ('companion',), note='companion inventory hand-back (ED-E); no hook, no effect type')
F['equipitem'] = E(None, 'no', 'no', HOST, 'low')
F['addnote'] = E(None, 'no', 'no', MIS, 'medium', ('player-targeted',), 'Pip-Boy note; progress gates elsewhere read GetHasNote on the host player')
F['addperk'] = {'player': E('SetPerk', 'no', 'no', PAR, 'high', ('player-targeted',),
                            note='SetPerk exists; Cmd_AddPerk is block-only (dropped on guests) and not recorded on the host; no apply branch'),
                'other': E('SetPerk', 'no', 'no', PAR, 'medium', ('companion',), 'NPC perk (ED-E companion suite): same gap')}
F['removeperk'] = F['addperk']
F['addtofaction'] = E('SetFactionRank', 'no', 'no', PAR, 'high', note='shared membership per owner decision; capture block-only, no apply branch')
F['removefromfaction'] = F['addtofaction']
F['addreputation'] = E('ModReputation', 'yes', 'yes', SUP, 'info', ('player-targeted',), 'shared')
F['addreputationexact'] = F['addreputation']
F['rewardkarma'] = E('ModKarma', 'yes', 'yes', SUP, 'info', ('player-targeted',), 'shared')
F['addspecialpoints'] = E(None, 'no', 'no', MIS, 'medium', ('player-targeted',), 'per-player S.P.E.C.I.A.L. points; ModActorValue does not cover it')
F['addtagskills'] = E(None, 'no', 'no', MIS, 'medium', ('player-targeted',), 'per-player tag skill points')
F['pipboyradiooff'] = E(None, 'no', 'no', MIS, 'low', ('radio',), 'turns the calling player\'s Pip-Boy radio off')
F['setinchargen'] = E(None, 'no', 'no', MIS, 'high', ('cinematic', 'ending'),
                      note='global "chargen" flag used as a level-up/UI suppressor during the launch and ending; per machine, no effect type')

# ---- companion ---------------------------------------------------------------------------------------------------
F['setplayerteammate'] = E('CompanionOwner', 'no', 'no', MIS, 'critical', ('companion',),
                           note='no hook and no apply branch; ED-E recruit/dismiss/finale all go through it. CompanionOwner rule has no engine half')
F['openteammatecontainer'] = E(None, 'no', 'no', MIS, 'medium', ('companion', 'menu'), 'UI on the calling machine')

# ---- control / cinematic -----------------------------------------------------------------------------------------
F['disableplayercontrols'] = E('SetPlayerControls', 'yes', 'no', PAR, 'high', ('cinematic',),
                               note='captured since L5 hooked PlayerCharacter::SetControlsDisabled (engine_hooks.cpp h_Controls); RemoteApplier has no '
                                    'branch, the lock is applied by coop_module (SetPlayerControlsLocked) from the rules engine: path not exercised for this DLC')
F['enableplayercontrols'] = F['disableplayercontrols']

# ---- menus / dialogue --------------------------------------------------------------------------------------------
F['showmessage'] = E(None, 'no', 'no', MIS, 'medium', ('menu',),
                     note='modal message box on the machine running the script (host); no effect type. With buttons it is a choice UI '
                          '(see GetButtonPressed)')
F['getbuttonpressed'] = E(None, 'n/a', 'n/a', READ, 'high', ('menu', 'ui-local'),
                          note='reads the message-box answer of the LOCAL machine; a guest\'s answer never reaches the host script')
F['say'] = E('ForcedDialogue', 'no', 'no', PAR, 'medium', ('ending',), note='ForcedDialogue exists; no capture hook for Say/SayTo (Cmd_Say 0x5C8A50 located only)')
F['sayto'] = F['say']
F['startconversation'] = E('ForcedDialogue', 'no', 'no', PAR, 'high', ('menu',),
                           note='forced greeting (Ulysses at the temple): needs the initiator rule; no hook')
F['showrecipemenu'] = E(None, 'no', 'no', MIS, 'medium', ('menu', 'player-targeted'), 'service menu opened for Player (= host in a host-run result script)')
F['showrepairmenu'] = F['showrecipemenu']
F['swaptexture'] = E('SwapTexture', 'no', 'no', PAR, 'high', ('ending',), note='SwapTexture exists (the slideshow screen); no capture, no apply branch')
F['playsound'] = E(None, 'no', 'no', PRES, 'low')
F['playsound3d'] = F['playsound']
F['playmusic'] = E(None, 'no', 'no', PRES, 'low')
F['applyimagespacemodifier'] = E(None, 'no', 'no', PRES, 'low', ('cinematic',))
F['removeimagespacemodifier'] = E(None, 'no', 'no', PRES, 'info')
F['activate'] = E(None, 'no', 'no', MIS, 'critical', ('guest-activation',),
                  note='Activate inside an OnActivate block: the default activation. Cmd_Activate is block-only on guests; guest-originated '
                       'activation intent is "designed, NOT implemented" (internal lane notes round 4)')
F['pcb'] = E(None, 'no', 'no', READ, 'info', note='semantics unknown to this analysis (appears as a bare line in two stage scripts); treated as inert')

# ---- reads (condition-style functions). Player-referencing ones are P-REF and need PartyAwareCond ----------------------
for _n in ('getstage getstagedone getobjectivecompleted getobjectivedisplayed getquestrunning getdead getdisabled '
           'getsecondspassed getdeadcount istalking getrestrained getcurrentaipackage getlinkedref getself isincombat '
           'isincriticalstage gethealthpercentage hasmagiceffect isspelltarget getactionref menumode').split():
    F[_n] = E(None, 'n/a', 'n/a', READ, 'info')
F['getsecondspassed'] = E(None, 'n/a', 'n/a', READ, 'info', ('timer',), 'timers run on the host only; guests skip quest scripts')
F['menumode'] = E(None, 'n/a', 'n/a', READ, 'medium', ('menu', 'ui-local'), 'MenuMode block: fires on the machine whose menu opened')
for _n in ('isactionref getdistance getincell getinworldspace issneaking gettalkedtopc getpos getlevel getactorvalue getitemcount '
           'getequipped gethasnote hasperk iskiller ispcsleeping isininterior ishardcore').split():
    F[_n] = E(None, 'n/a', 'n/a', READ, 'high', ('p-ref',),
              note='reads the local/host player; needs the PartyAwareCond reduction (initiator / any-party-member / min-over-party). '
                   'Host-side evaluation is "proposed" in docs/quest-policies and unimplemented in L5 (round 2 item 5)')
F['getlevel'] = E(None, 'n/a', 'n/a', READ, 'low', ('p-ref',), 'level is per player; reads host player')
F['ishardcore'] = E(None, 'n/a', 'n/a', READ, 'low', ('p-ref',), 'hardcore mode is a per-machine setting')
F['isininterior'] = E(None, 'n/a', 'n/a', READ, 'low', ('p-ref',))
F['ispcsleeping'] = E(None, 'n/a', 'n/a', READ, 'low', ('p-ref',))
F['getequipped'] = E(None, 'n/a', 'n/a', READ, 'medium', ('p-ref',), 'inventory fact of the host player')
F['getitemcount'] = E(None, 'n/a', 'n/a', READ, 'medium', ('p-ref',), 'inventory fact; subject decides whose')
F['hasperk'] = E(None, 'n/a', 'n/a', READ, 'medium', ('p-ref',), 'perk fact of the host player')
F['gethasnote'] = E(None, 'n/a', 'n/a', READ, 'high', ('p-ref',),
                    note='quest progress gated on a Pip-Boy note the host player holds (launch codes in NVDLC04MQ01a)')
F['issneaking'] = E(None, 'n/a', 'n/a', READ, 'low', ('p-ref',))
F['gettalkedtopc'] = E(None, 'n/a', 'n/a', READ, 'low', ('p-ref',))

# commands that only appear inside this DLC's own dialogue/UI plumbing and need no entry beyond reads
IGNORE = {'return', 'if', 'elseif', 'else', 'endif', 'begin', 'end', 'set', 'to', 'scn', 'scriptname'}

# Functions in the command tables that this DLC does not use must not appear in gaps; anything used but absent from F is
# reported as `unmapped` (never silently dropped).


# ---- second pass: functions the first analysis run reported as unmapped --------------------------------------------------
F['set localvar'] = E(None, 'n/a', 'n/a', HOST, 'info', ('object-var',),
                      note='script-local variable of an object/NPC script (timers, do-once flags). Object scripts run on every machine, so each '
                           'keeps its own copy; only the host copy drives story outcomes')
F['setav'] = F['setactorvalue']
F['damageav'] = F['setactorvalue']
F['damageactorvalue'] = F['setactorvalue']
F['restoreav'] = F['restoreactorvalue']
F['kill'] = E('KillActor', 'yes', 'yes', SUP, 'medium', ('killall',), 'Kill with a killer argument; same hook as KillActor')
F['resurrect'] = E('ResurrectActor', 'yes', 'yes', SUP, 'low')
F['enablefasttravel'] = E('SetFastTravelLock', 'no', 'no', PAR, 'high', ('cell',),
                          note='SetFastTravelLock exists (party-wide, host authoritative); Cmd_EnableFastTravel located, not hooked')
F['forceweather'] = E(None, 'no', 'no', MIS, 'low', note='global weather override; host-only visual')
F['setweather'] = F['forceweather']
F['releaseweatheroverride'] = F['forceweather']
F['showbartermenu'] = E(None, 'no', 'no', MIS, 'medium', ('menu',), 'service menu on the calling machine')
F['showwarning'] = E(None, 'no', 'no', MIS, 'low', ('menu',))
F['showsleepwaitmenu'] = E(None, 'no', 'no', MIS, 'low', ('menu',))
F['removeme'] = E(None, 'no', 'no', MIS, 'low', note='ref self-delete')
F['pushactoraway'] = E(None, 'no', 'no', HOST, 'info')
F['setallvisible'] = E(None, 'no', 'no', HOST, 'info')
F['setangle'] = E(None, 'no', 'no', HOST, 'info')
F['setactoralpha'] = E(None, 'no', 'no', PRES, 'info')
F['stopmagicshadervisuals'] = E(None, 'no', 'no', PRES, 'info')
F['playmagicshadervisuals'] = E(None, 'no', 'no', PRES, 'info')
for _n in ('getinfaction getcombattarget getrandompercent getanimaction isidleplaying isimagespaceactive getlocked getisghost '
           'isxbox isps3 getiscreaturetype getunconscious getreputationthreshold getangle').split():
    F[_n] = E(None, 'n/a', 'n/a', READ, 'info')
F['getissex'] = E(None, 'n/a', 'n/a', READ, 'low', ('p-ref',), 'sex of the host player (ending slide variants)')
F['getreputationthreshold'] = E(None, 'n/a', 'n/a', READ, 'info', note='shared reputation: identical everywhere')

# entries whose capture/apply status depends on the call subject (player vs other ref): the L5 consistency test skips the
# "claims NO capture/apply" direction for them, because the same Effect type IS captured for the other subject.
F['set objvar']['sub'] = True
F['additem']['other']['sub'] = True
F['removeitem']['other']['sub'] = True
