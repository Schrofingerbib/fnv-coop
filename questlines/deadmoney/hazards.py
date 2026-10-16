"""Curated Dead Money co-op hazard sites.

Each entry names a script (label as in dmlib.universe) and a regex; build.py resolves the exact line numbers from the ESM
script text and the test fails if a pattern stops matching (so a citation can never drift from the data).

fields: id, system, sev (blocker|high|medium|low), title, script (label), rx (regex on the stripped line), tags,
        why (what breaks with 2-4 players), handling (what the policy / hooks must do)
Brief checklist tags: player-effect spawn companion essential move cinematic menu timer radio ending kill save perk faction
                      items lock collar cloud dialogue
"""

H = []


def h(id, system, sev, title, script, rx, tags, why, handling):
    H.append(dict(id=id, system=system, sev=sev, title=title, script=script, rx=rx, tags=tags.split(), why=why, handling=handling))


# ---- entry to the Villa -------------------------------------------------------------------------------------------------
h('ENTRY-TRIGGER', 'entry', 'blocker', 'Bunker gas trap fires for the host player only',
  'NVDLC01VillaTravelTriggerScript', r'^BEGIN OnTrigger player',
  'cinematic move player',
  'OnTrigger <player> filters on the local player reference. A guest avatar is a ghost NPC in the host world, so the block never runs for a guest: the host is gassed and sent to the Villa, the guest stays in the Mojave bunker.',
  'rule PartyTether on the bunker cell + PartyWarp to DLC01StartMarker; host runs the whole sequence once and the warp moves every party member')
h('ENTRY-CONTROLS', 'entry', 'high', 'Controls locked for the host only during the gas sequence',
  'NVDLC01VillaTravelTriggerScript', r'DisablePlayerControls 1 1 1 1 1 1 1',
  'cinematic player-effect',
  'DisablePlayerControls is captured now (PlayerCharacter::SetControlsDisabled, Recipient default) and the applier has a branch, but the call sits in an OnTrigger <player> block that never runs for a guest, and without a SetPlayerControls rule with scope all the effect locks nobody but the host.',
  'rule SetPlayerControls scope=all with host-effect release; route the captured effect to every party member')
h('ENTRY-STRIP', 'entry', 'blocker', 'Inventory stripped into ONE shared container',
  'NVDLC01VillaTravelTriggerScript', r'player\.RemoveAllItems NVDLC01PlayerEquipmentContainerRef',
  'items player-effect',
  'player.RemoveAllItems is deliberately NOT recorded by the L5 hook (it tracks non-player containers only), and the destination NVDLC01PlayerEquipmentContainerRef is a single container: with N players the equipment of all of them would be mixed and the first player out takes everything back.',
  'per-player equipment container (new rule PerPlayerState) or RemoveAllItems -> RemoveItem per recipient with a stash keyed by player slot; record the player form of RemoveAllItems')
h('ENTRY-RETURN', 'entry', 'blocker', 'Equipment returned from the single container',
  'NVDLC01ReturnEquipmentTriggerSCRIPT', r'NVDLC01PlayerEquipmentContainerRef\.RemoveAllItems Player',
  'items player-effect',
  'The container form is captured (RemoveAllItems{ref}) but the effect carries no destination: replicas run a bare RemoveAllItems and the items vanish instead of returning to the player. The container is also shared, so whoever triggers it first would receive every player\'s stashed gear.',
  'add a destination to RemoveAllItems; return each slot\'s stash to its own player (PerPlayerState)')
h('ENTRY-FAILSTRIP', 'ending', 'blocker', 'Failure ending strips player armour into the same container',
  'NVDLC01MQ03Script', r'Player\.RemoveAllTypedItems NVDLC01PlayerEquipmentContainerRef',
  'items player-effect ending',
  'RemoveAllTypedItems is not hooked at all and the container is shared.',
  'per-player stash; hook the typed bulk remover')
h('ENTRY-TIMER', 'entry', 'high', 'Transition timer moves the host player to the Villa start',
  'NVDLC01VillaTransitionTimerScript', r'player\.MoveTo DLC01StartMarker',
  'move cinematic',
  'A quest-script MoveTo on the host player ref; guests are not moved.',
  'rule PartyWarp markers=[DLC01StartMarker] absent=hold')
h('ENTRY-MQ00', 'entry', 'high', 'Pre-patch safety teleport out of the bunker interior',
  'NVDLC01MQ00Script', r'Player\.Moveto NVDLC01BunkerExitMarker',
  'move',
  'GetInCell reads the host player only; a guest standing in the bunker interior is never moved out.',
  'rule PartyAwareCond GetInCell=any-in')

# ---- bomb collar --------------------------------------------------------------------------------------------------------
h('COLLAR-STATE', 'collar', 'blocker', 'One collar state machine for the whole session',
  'NVDLC01BombCollarQuestSCRIPT', r'^int iNumRadii',
  'collar player-effect',
  'iNumRadii / fCollarTimer / bBoom / rDeadFollower are single quest variables. Players walk into different speaker radii, so a shared counter either never reaches zero (nobody can ever be "in the clear") or kills the wrong player.',
  'new rule PerPlayerState(scope=collar): one collar record per slot, timers run on the host per slot, detonation targets that slot only')
h('COLLAR-KILL-TIMER', 'collar', 'blocker', 'Collar timeout kills "player" = the host',
  'NVDLC01BombCollarQuestSCRIPT', r'^player\.kill player 1$',
  'collar kill player-effect',
  'player.kill acts on the host player ref. When a guest stands near a speaker the host dies instead (or nobody does).',
  'KillActor with Recipient=origin slot; downed state per DLC policy instead of permadeath of the host')
h('COLLAR-EXPLOSION', 'collar', 'high', 'Collar explosion spawned at the player',
  'NVDLC01BombCollarQuestSCRIPT', r'^player\.PlaceAtMe NVDLC01BombCollarExplosion',
  'spawn collar',
  'PlaceAtMe is captured as SpawnRef, but RemoteApplier has no SpawnRef branch: guests would see a corpse without the blast (and gibs/decapitation are applied by the explosion).',
  'add a SpawnRef apply branch or run the explosion as a host-spawned ref that NV:MP replicates')
h('COLLAR-FOLLOWER', 'collar', 'blocker', 'Companion detonation reads the single current follower',
  'NVDLC01BombCollarQuestSCRIPT', r'NVDLC01Followers\.rCurrentFollower',
  'collar companion',
  'rCurrentFollower is ONE reference for the whole game. Two players with two companions: only one is killed on timeout; the other keeps a live collar.',
  'per-player companion slot; kill every companion whose owner slot is detonated')
h('COLLAR-DEADFOLLOWER', 'collar', 'blocker', 'Companion death detonates every collar',
  'NVDLC01BombCollarQuestSCRIPT', r'^if \(rDeadFollower && bBoom == 0\)',
  'collar companion kill',
  'Death of any companion starts a countdown that kills the player AND the other companions. In co-op the countdown must be per owner slot, and iDiedInCasino GetInSameCell tests only the host.',
  'PerPlayerState + owner-slot routing: a companion\'s death starts the countdown for its owner only')
h('COLLAR-SPEAKER-COUNT', 'collar', 'blocker', 'Speakers increment the shared radius counter from GetDistance player',
  'NVDLC01RadioSpeakerSCRIPT', r'set NVDLC01BombCollarQuest\.iNumRadii to \(NVDLC01BombCollarQuest\.iNumRadii \+ 1\)',
  'collar radio',
  'Each speaker measures only the host player (GetDistance player). A guest in the radius is not counted; two players in the same radius count once each only for the host.',
  'host-side party-aware GetDistance = per-slot; PerPlayerState counters per slot')
h('COLLAR-SPEAKER-DESTROY', 'collar', 'medium', 'Speaker destruction: the radius bookkeeping runs where the speaker was shot',
  'NVDLC01RadioSpeakerSCRIPT', r'^SetDestroyed 1$',
  'radio world',
  'SetDestroyed is captured as ObjectState and applied by console, so the destroyed flag replicates. What stays machine-local is the OnDestructionStageChange block (iNumRadii - 1, RewardXP 10) that runs on the machine that shot the speaker, and the radius counting itself.',
  'make the radius counter per slot (PerPlayerState) and route the shot XP to the shooter')
h('COLLAR-ACTIVATE-RADIO', 'collar', 'medium', 'Radio switched off only for IsActionRef Player',
  'NVDLC01RadioSpeakerSCRIPT', r'^if \(IsActionRef Player\)',
  'radio',
  'A guest turning off a radio never matches IsActionRef Player.',
  'rule PartyAwareCond IsActionRef=initiator')
h('COLLAR-HIT-COMPANION', 'collar', 'blocker', 'Hitting a companion kills the attacker (OnHit Player)',
  'NVDLC01DogScript', r'^Player\.KillActor Player 1',
  'collar kill companion',
  'BEGIN OnHit Player only fires for the host player. A guest who attacks Dog/Dean/Christine is not punished and the host would be if NV:MP routed the hit.',
  'route OnHit attacker = origin slot; KillActor Recipient=that slot')
h('COLLAR-ELEVATOR-RESET', 'collar', 'high', 'Vault elevator zeroes the shared radius counter',
  'NVDLC01VaultElevatorScript', r'Set NVDLC01BombCollarQuest\.iNumRadii to 0',
  'collar ending',
  'One player\'s escape clears the collar state of players still inside the vault.',
  'clear only the escaping slot')

# ---- toxic cloud --------------------------------------------------------------------------------------------------------
h('CLOUD-GLOBAL', 'cloud', 'blocker', 'Villa-wide toxic damage hits the host player only',
  'NVDLC01GlobalToxicDamageSCRIPT', r'PlayerREF\.DamageActorValue Health 3',
  'cloud player-effect',
  'A quest script (host-only) damages PlayerREF every tick. Guests take no cloud damage; if the host AV change were broadcast to all (default ModActorValue recipient) every player would take every player\'s tick.',
  'PerPlayerState(scope=cloud): each slot ticks its own damage by its own worldspace/interior test; DamageActorValue Recipient=origin')
h('CLOUD-LOCAL', 'cloud', 'high', 'Cloud triggers damage PlayerREF in a quest script',
  'NVDLC01ToxicCloudSCRIPT', r'PlayerREF\.DamageActorValue Health iHealthDamage',
  'cloud player-effect',
  'iToxicCloudsActive is a shared counter fed by trigger volumes.',
  'per-slot counters (PerPlayerState)')

# ---- companions ---------------------------------------------------------------------------------------------------------
h('COMP-HIRE', 'companion', 'blocker', 'Hiring a companion runs on GetActionRef with the host player perks',
  'NVDLC01HireSCRIPT', r'NewFollower\.SetPlayerTeammate 1',
  'companion player-effect',
  'SetPlayerTeammate has no hook or apply path (CompanionOwner is unused). The owner must be the recruiter (CompanionOwner) and each hire grants a player perk to `player`.',
  'rule CompanionOwner owner=recruiter; hook Cmd_SetPlayerTeammate; route the perk to the recruiter')
h('COMP-SLOT', 'companion', 'blocker', 'Single follower slot for the whole DLC',
  'NVDLC01HireSCRIPT', r'set NVDLC01Followers\.rCurrentFollower to NewFollower',
  'companion',
  'rCurrentFollower / bHaveCompanion allow one companion; a second recruit by another player either overwrites the first or is refused. The collar logic and fire script read the same slot.',
  'per-owner companion slots (PerPlayerState); decide whether a party may hold up to N Dead Money companions at once (owner decision needed)')
h('COMP-PERK', 'companion', 'medium', 'Recruit perks are added to "player"',
  'NVDLC01HireSCRIPT', r'player\.AddPerk NVDLC01UncleanLiving',
  'perk player-effect companion',
  'AddPerk is captured as SetPerk{Recipient default} and applied on the local player of every machine that receives it; without recipient routing every party member gets Unclean Living / Signal Interference / In My Footsteps, not only the recruiter (Signal Interference lengthens the collar timer of its owner).',
  'SetPerk with Recipient=recruiter')
h('COMP-FIRE', 'companion', 'medium', 'Firing a companion removes the perk from "player"',
  'NVDLC01FireSCRIPT', r'player\.removeperk NVDLC01UncleanLiving',
  'perk player-effect companion', 'mirror of COMP-HIRE (RemovePerk)', 'SetPerk rank 0 for the owner slot only')

# ---- gala event ---------------------------------------------------------------------------------------------------------
h('GALA-AUTOSAVE', 'gala', 'medium', 'Gala completion autosaves',
  'NVDLC01MQ02 stage 50', r'^Autosave$', 'save',
  'Autosave is captured as RequestSave but RemoteApplier has no branch: whether guests save is a session policy. An autosave while one player is mid-warp or downed would anchor the host\'s save at a bad moment.',
  'rule SaveSuppress; host owns save points')
h('GALA-MOVE-COMP', 'gala', 'high', 'Gala completion warps all three companions',
  'NVDLC01MQ02 stage 50', r'^NVDLC01DeanRef\.MoveTo NVDLC01DeanTheaterMarker', 'move companion',
  'MoveRef on NPCs is captured; the destination must also respect which companions are currently owned by which player.',
  'MoveRef partyWarp=false for NPCs (supported); keep owner binding')
h('GALA-GAUNTLET', 'gala', 'medium', 'Gauntlet speakers armed by Activate player',
  'NVDLC01MQ02Script', r'^GalaGauntletSpeaker01\.Activate player 1', 'world radio',
  'Script Activate is a block-only hook (no effect): clients do not see the speaker animation; SetDestroyed on the speakers is captured as ObjectState.',
  'capture Activate (or the resulting ObjectState)')
h('GALA-DOG-ITEMS', 'gala', 'high', 'Dog Gala: ghost harvester remains counted on the host player only',
  'NVDLC01MQ02aSCRIPT', r'Player\.GetItemCount NVDLC01GhostHarvesterRemains >= 2', 'items player-effect',
  'The quest script counts the host player\'s inventory. A guest carrying the two slabs never advances stage 40.',
  'PartyAwareCond GetItemCount = party sum (quest-item rule: duplicated items count once per slot)')
h('GALA-ELEVATOR', 'gala', 'high', 'Gala02 elevator teleports the activating player',
  'NVDLC01Gala02ElevatorDoorSCRIPT', r'Player\.MoveTo NVDLC01Gala02ElevatorTopsidePlayerDestinationMarker', 'move',
  'Only the activator moves; the party is split between the Clinic floors.', 'PartyWarp absent=hold')

# ---- vault / ending -----------------------------------------------------------------------------------------------------
h('END-FAILURE', 'ending', 'blocker', 'Failure ending runs from a quest script for "player"',
  'NVDLC01MQ03Script', r'^player\.MoveTo NVDLC01SlideshowMarker', 'ending move cinematic',
  'Locks controls and teleports the host player to the slideshow marker; guests are neither locked nor moved, and StopQuest ends the DLC under them.',
  'rule SlideshowEnd + PartyWarp + SetPlayerControls scope=all; run once on the host')
h('END-ELEVATOR', 'ending', 'blocker', 'Vault elevator completes MQ03 for everyone, moves one player',
  'NVDLC01VaultElevatorScript', r'^player\.MoveTo NVDLC01EndVillaMarker', 'ending move',
  'SetStage MQ03 100 fires when the first player activates the elevator; `player.MoveTo` moves only that player, companions are disabled for all, and the vault door is locked behind them.',
  'Barrier all-players-pass on the elevator; then PartyWarp the whole party')
h('END-GATE', 'ending', 'blocker', 'Exit gate: GetButtonPressed + slideshow for the activating player',
  'NVDLC01SMGateMojaveSCRIPT', r'^player\.MoveTo NVDLC01SlideshowMarker', 'ending move menu cinematic',
  'The "travel to the Mojave?" message box is answered by whoever stands at the gate on the host; the ending starts for one player, radios are re-enabled for everyone, and DisablePlayerControls locks that player only.',
  'InitiatorDialogue for the box (initiator answers), then SlideshowEnd + PartyWarp for all')
h('END-LOCK', 'ending', 'high', 'Slideshow locks controls',
  'NVDLC01SMGateMojaveSCRIPT', r'^DisablePlayerControls 1 1 1 1 1 1 1', 'ending cinematic',
  'DisablePlayerControls is captured now, but the slideshow is started from an OnActivate by one player; lock scope and release have to come from the SetPlayerControls rule for all players (see END-GATE).',
  'SetPlayerControls scope=all release=host-effect')
h('END-SLIDE', 'ending', 'high', 'Slideshow trigger fires for "player" and sets chargen mode',
  'NVDLC01SlideshowTriggerSCRIPT', r'^SetInChargen 1', 'ending cinematic player-effect',
  'SetInChargen is a local flag; the narrator SayTo player is host-only.', 'SlideshowEnd runs once; ForcedDialogue to all')
h('END-RADIO', 'ending', 'medium', 'Mojave radio re-enabled at DLC end',
  'NVDLC01SMGateMojaveSCRIPT', r'^RNVTARef\.Enable', 'radio ending',
  'World ref toggle is supported; the radio stop/start calls are not.', 'EnableRef supported; add radio effects')
h('END-CHIPS', 'ending', 'medium', 'Reward chips delivered to a drop box on a 3-day timer',
  'NVDLC01ChipRewardSCRIPT', r'NVDLC01ElijahsDropboxREF\.additem NVDLC01SierraMadreChip 100', 'items timer',
  'AddItem on a container is captured and applied now (RefAddItem); the remaining problem is ONE shared drop box on a GameDaysPassed timer, so the first player to open it takes the chips.',
  'one drop box per slot, or AddItem to every player (RewardAll)')

# ---- holograms / world --------------------------------------------------------------------------------------------------
h('HOLO-LOS', 'holograms', 'medium', 'Hologram security reads GetLOS player',
  'NVDLC01ETNGGGSwitch01SCRIPT', r'Player\.GetLOS GGGhost01', 'player-effect',
  'Line-of-sight is evaluated for the host player.', 'PartyAwareCond GetLineOfSight=any')
h('HOLO-SNEAK', 'holograms', 'medium', 'Pickpocket-blocking hologram checks Player.IsSneaking',
  'NVDLC01HologramNoPickPocketScript', r'^If Player\.IsSneaking', 'player-effect',
  'Evaluated for the host player only.', 'initiator-evaluated')
h('COMP-DEAN-SNEAK', 'companion', 'medium', 'Dean script: IsActionRef Player && Player.IsSneaking',
  'NVDLC01DeanScript', r'IsActionRef Player == 1\) && \(Player\.IsSneaking\)', 'companion player-effect',
  'Activation by a guest is not seen as Player.', 'PartyAwareCond IsActionRef=initiator, IsSneaking=initiator')
h('COMP-CHRISTINE-SNEAK', 'companion', 'medium', 'Christine script: IsActionRef Player && Player.IsSneaking',
  'NVDLC01ChristineSCRIPT', r'IsActionRef Player == 1\) && \(Player\.IsSneaking\)', 'companion player-effect',
  'Activation by a guest is not seen as Player.', 'PartyAwareCond IsActionRef=initiator, IsSneaking=initiator')
h('END-CREDITS-KILL', 'ending', 'blocker', 'Failure ending kills the host player to force an autosave reload',
  'NVDLC01FadeToCreditsTimerSCRIPT', r'^Player\.KillActor$', 'ending kill cinematic save',
  'The failure slideshow ends by killing PlayerREF so the engine reloads the last autosave. Guests are not killed, the host reloads a save alone, and the session desyncs from the world of the guests.',
  'rule SlideshowEnd: end the session for all players (coordinated reload of the host save / return to the lobby) instead of killing one player')
h('GALA-SUITES-AUTOSAVE', 'gala', 'medium', 'Intercom, casino knockout and vault terminal autosave at story moments',
  'NVDLC01SuitesElevatorIntercomScpt', r'^Autosave$', 'save',
  'Autosave is captured as RequestSave without an applier branch; more call sites besides the Gala: CasinoKnockoutTrigger, Gala02ElevatorDoor, VaultMainTerminal, ETGateTerminal01.',
  'rule SaveSuppress on every scene; the host owns save points')
h('VAULT-TIMER', 'collar', 'blocker', 'Vault escape: a 70 s collar timer that kills "Player"',
  'NVDLC01VaultCollarBeepScript', r'^Player\.Kill Player 1', 'collar kill timer player-effect',
  'Object-script timer in the vault (objective 50 "escape before your collar explodes"): fTimer, bCollarOn and the fireworks follow VaultEscapeSoundMarker.MoveTo Player. One timer, one victim (the host); a guest can stand still in the vault forever.',
  'PerPlayerState(scope=collar) with a timer per slot; spawn effects around each slot')
h('VAULT-KILL-TRIGGER', 'collar', 'high', 'Vault kill trigger',
  'NVDLC01VaultKillTriggerScript', r'^Player\.Kill$', 'kill player-effect',
  'OnTrigger with GetActionRef == Player: kills the host player and fades the host screen; a guest crossing the volume is not the Player ref.',
  'PartyAwareCond GetActionRef=initiator; KillActor Recipient=origin slot')
h('SUITES-TIMER', 'collar', 'blocker', 'Executive Suites: Christine collar timeout kills "player"',
  'NVDLC01SuitesSetupSCRIPT', r'^player\.kill player 1', 'collar kill timer player-effect',
  'fCollarTimer counts to 16 s after Christine dies (MQ03c objective 30); host-only victim, host-only warning messages (ShowMessage) and rumble.',
  'PerPlayerState(scope=collar) + ShowMessage to the affected slot')
h('KITCHEN-GAS', 'dog', 'blocker', 'Kitchen gas traps kill "player"',
  'NVDLC01KitchenGasTrapSCRIPT', r'^player\.kill player 0', 'collar kill spawn player-effect',
  'Three traps in the kitchen explode and kill the host player ref when Dog spots the player; guests inside the kitchen are never targeted and never counted as spotted.',
  'PartyAwareCond: spotted = any slot; KillActor per slot; stealth mission needs a Barrier so every slot is "in position"')
h('KITCHEN-DOG-KILL', 'dog', 'blocker', 'Dog death in the kitchen kills the host player and fails MQ03',
  'NVDLC01DogScript', r'^player\.kill player 0$', 'kill collar player-effect',
  'player.kill acts on the host; SetStage MQ03 255 fails the questline for everyone.',
  'collar rule per slot; failure only when every collared slot is dead')
