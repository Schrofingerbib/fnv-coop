# Prior Art: Shared Quest/Story Progression in Single-Player RPG Multiplayer Mods

Scope: what other projects did for quest, dialogue and script sync, what failed, and what we can copy for a Fallout: New Vegas story co-op (xNVSE plugin over NV:MP, or our own netcode).

Evidence key: [CODE] = I read the source. [DOC] = official docs or wiki. [PRESS] = secondary or press reports, lower confidence. Things I could not verify are marked UNVERIFIED.

---

## 1. TES3MP (Morrowind) - the best template

- Repos: https://github.com/TES3MP/openmw-tes3mp (client+server C++, a fork of OpenMW) and https://github.com/TES3MP/CoreScripts (server Lua).
- License: openmw-tes3mp is GPLv3 "with additional allowed terms" (OpenMW-derived). CoreScripts is MIT (checked via GitHub API).
- Architecture [CODE]: dedicated server (`apps/openmw-mp`) plus a modified OpenMW client (`apps/openmw/mwmp`). The server does no game simulation. It receives typed packets, hands them to a Lua callback (`Script::Call<"OnPlayerJournal">`), and the Lua scripts decide whether to store, share or reject them. Server policy is therefore data and scripts, not C++.

### How quest/story state syncs

- **Journal.** The vanilla OpenMW journal code calls `LocalPlayer::sendJournalEntry(quest, index, actor)` and `sendJournalIndex(quest, index)` (`apps/openmw/mwmp/LocalPlayer.cpp`, ~line 1721). The packet is `ID_PLAYER_JOURNAL`, with a list of `JournalItem{type ENTRY|INDEX, quest, index, actorRefId}`.
  - The server processor is `apps/openmw-mp/processors/player/ProcessorPlayerJournal.hpp`. It does nothing except fire the Lua `OnPlayerJournal`.
  - The CoreScripts handler `eventHandler.OnPlayerJournal` (`scripts/eventHandler.lua` ~line 356) does `WorldInstance:SaveJournal(pid)` and then `tes3mp.SendJournalChanges(pid, true, true)`. That means "send to others, skip the originator". If `config.shareJournal` is false it calls `Players[pid]:SaveJournal()` instead.
  - On receivers, `LocalPlayer::addJournalItems()` replays the entries with `MWBase::Journal::addEntry` or `setJournalIndex`. When an `actorRefId` is present it is used so `%name`-style text substitution works.
  - The world journal is persisted in the world JSON (`world.json`, key `journal`) so late joiners get it.
- **Dialogue topics.** `ID_PLAYER_TOPIC` (`ProcessorPlayerTopic.hpp`) is handled by `eventHandler.OnPlayerTopic`. It is shared under `config.shareTopics`, saved to world data and rebroadcast.
- **Factions.** `ID_PLAYER_FACTION` has an action (rank, expulsion or reputation) and a faction id. There are separate toggles per aspect.
- **Global variables.** Vanilla Morrowind scripts mutate globals and script-local variables directly. TES3MP handles this with `ID_CLIENT_SCRIPT_GLOBAL` (`ProcessorClientScriptGlobal.hpp`, `Worldstate::setClientGlobals`, which creates the global if it is missing). Per-script local variables use a similar `clientScriptLocal` path on objects. Which script variables sync is controlled by a whitelist ("client-side script variables must be added to a synchronization whitelist", per the README). The default is not to sync them.
- **Kill counts.** `ID_WORLD_KILL_COUNT` is synced so kill-count quest conditions work across players (`config.shareKills`).
- **Other worldstate packets** (`apps/openmw-mp/processors/worldstate/`): `RecordDynamic`, `WorldMap`, `WorldWeather`, music (`ProcessorMusicPlay`) and video (`config.shareVideos`).
- **Object dialogue choice.** `ID_OBJECT_DIALOGUE_CHOICE` lets the server see and script NPC dialogue (e.g. a custom choice). It is not a general dialogue sync.

### Shared vs per-player config (CoreScripts `scripts/config.lua`, defaults read from master)

| Key | Default |
|---|---|
| `shareJournal` | true |
| `shareFactionRanks` | true |
| `shareFactionExpulsion` | false |
| `shareFactionReputation` | true |
| `shareTopics` | true |
| `shareBounty` | false |
| `shareReputation` | true |
| `shareMapExploration` | false |
| `shareVideos` | true |
| `shareKills` | true |

Every category is a separate boolean and the server can reconfigure it without touching C++.

### What worked and what failed

- **Works:** the "intercept the vanilla mutation call, send a typed delta, replay the same engine call on the receiver" pattern. The README claims most quests work.
- **Weak spots:**
  - Any quest driven by an undeclared script variable or an NPC-local state is not synced unless whitelisted.
  - Whoever triggers dialogue runs the result scripts. The effects that matter (items, journal) are then propagated, but other scripted side effects (animations, AI packages) stay local.
  - Hand-editing world data can produce journal errors (forum reports).
  - World-state reset on rejoin is a server-script responsibility.
- **Reusable:** the packet design (delta, `sendToOthers`, `skipAttachedPlayer`), the per-category share flags, the persisted world JSON, and the global-var whitelist idea. Code reuse is only conceptual (OpenMW vs NVSE), but the config surface and semantics can be copied wholesale.

---

## 2. Skyrim Together Reborn / TiltedEvolution

- Repo: https://github.com/tiltedphoques/TiltedEvolution. License is GPLv3 with the project's own header ("Tilted Online is free software", GPL v3 or later). GitHub shows NOASSERTION because of the custom header.
- Architecture [CODE]: C++ client DLL plus a dedicated server using entt ECS and a custom serializer (`Code/encoding`).

### Quest sync design (the most directly comparable open implementation)

- Scope: quests are synced inside a party only (`GameServer::SendToParty`). It is not global server state.
- Client hooks (`Code/client/Services/Generic/QuestService.cpp`): registers engine event sinks `questStartStopEvent` and `questStageEvent`. A comment notes that `TESQuestStageItemDoneEvent` fires too late, so they use `TESQuestStageEvent`.
- Wire: `RequestQuestUpdate{Id(GameId=modId+baseId), Stage, Status(Started|StageUpdate|Stopped), ClientQuestType}` goes client to server. `NotifyQuestUpdate` goes server to the rest of the party.
- Server (`Code/server/Services/QuestService.cpp`) keeps a per-player `QuestLog` (`Entries: {GameId, uint16 Stage}`), so it has an authoritative copy for late joiners.
- Receiver: `pQuest->ScriptSetStage(stage)` and `SetActive(true)`.
- **Anti-echo guard:** `ScopedQuestOverride` is an RAII flag set while applying remote updates. Hooks return early if it is set, so applying a remote stage does not re-broadcast it. This is the key pattern for us.
- FormID mapping uses `ModSystem::GetServerModId` and `GetGameId`, because load orders differ per client. This is relevant to NV (modIndex differs between machines).
- Exclusions: `IsNonSyncableQuest` skips quests without stages and a hardcoded list (werewolf transformation, vampire transformation, a barrow lever scene, the skill-experience quest). These are cases where syncing causes feedback loops or per-player state.
- **Misc and None quest types are off by default** (`Gameplay:bEnableMiscQuestSync`, "(Experimental)", default false). These are the scripted background quests that carry per-player state. Syncing them broke things.
- Remote stage updates call `ScriptSetStage`, which runs the stage's result script. It does not apply a state snapshot. So the side effects (item grants, spawns) happen on every client, with duplicate-item risks. The changelog has "duplicating quest items" and "dont sync quest items" fixes, then a later "sync quest items" commit.
- **Dialogue** is only cosmetic: `DialogueRequest{ServerId, SoundFilename}` and `SubtitleRequest{ServerId, Text, TopicFormId}` replay the voice line and subtitle to nearby players. The conversation logic runs on the speaking player only.
- **What they gave up** (project/press statements):
  - Dev posts say "some quests will break" and call it unfixable.
  - There are no stated per-quest rules. The team also stated it is not compatible with most other mods.
  - Fallout Together (Fallout 4, same team) was never finished. In May 2024 the dev said they would only continue if others helped. The code was open-sourced and nobody took it over. [PRESS, Reddit] I found no quest-sync design notes for it.
  - Starfield Together: stopped December 2023 with ~70% of the Skyrim Together code ported, and the modder said the code would go to the Discord. No quest-sync evidence. [PRESS]
- **Reusable:** the whole message set and the ScopedOverride pattern, which are directly portable in concept. The same-stage replay approach is a natural fit for xNVSE `SetStage` (it exists in NVSE). The mod-ID mapping scheme is too.

---

## 3. VaultMP (Foxtacles/vaultmp, Fallout 3 / NV port)

- Repo: https://github.com/Foxtacles/vaultmp. Archived. License file is nonstandard (GitHub shows NOASSERTION). "This project is no longer being maintained."
- Design [CODE/DOC]: external process plus injected DLL talking over a Pipe (`Pipe.cpp`, `Interface.cpp`) to the game's console/script interpreter. A RakNet network layer syncs object/actor values. Server-side scripting API (C++/Pawn) defines gameplay rules. Stated goal is a roaming open world "to enable players to roam the open world", not story co-op.
- Quests: a grep of `Game.cpp` and `Player.cpp` for quest, setstage and similar found nothing, so it has no quest sync. Gameplay rules were left to server scripts. NV was handled via a "Vault-Tec Multiplayer NV" Nexus page; I did not verify its quest behavior.
- Lessons: the "drive the engine through its own script interpreter" route was used for everything and was slow and brittle (Pipe latency). It is a precedent for Fallout engine hooks, but not for quests. Prefer in-process plugin hooks (xNVSE) over console-command injection.

---

## 4. NV:MP (nv-mp.com)

- Closed source, so no code. Sources: https://nuwiki.nv-mp.com/wiki/Main_Page, a Steam community guide (note that the guide carries a "removed for violating guidelines" notice, so treat it as an author's claim), the TweakTown/Co-Optimus 2016 articles.
- Statements found:
  - 2016 dev quotes: "The host's game -- inventory, quests, etc -- is constantly synchronized with everyone else's. Quests are clientside, they still take place in the world but one player in a location will synchronise entities/NPCs to other players around them." Failure mode they described: kill a quest NPC and others who enter the cell fail the quest. A refresh of the host was suggested to reset broken quests. [PRESS]
  - Current Steam guide FAQ: "Is there quest sync? No ... The Devs are working on it now." Progress is tied to each player's save, and death returns you to your last save. NPCs are "Ghosts"/"Simulated" (cell-owner simulation).
  - Co-op: up to 10 friends, all players need identical mod lists/DLCs, offline mode. On the public server (RADD/ATK) mods with .esp/.esm/.dll are blocked by a whitelist. Server scripting mods are excluded. The wiki's "Software Development Kit" and "Server Hosting" sections are empty ("Coming Soon").
  - No public roadmap or plugin API for quests. I could not find an SDK doc.
- Implication: building on NV:MP means our xNVSE plugin must coexist with its cell-owner NPC simulation. That is the likeliest source of quest-state conflicts (see pitfalls). Whether NV:MP exposes events is UNVERIFIED and is for the other research tracks.

---

## 5. Other projects

| Project | License / status | Quest/story approach | Notes |
|---|---|---|---|
| Elden Ring Seamless Co-op (LukeYui) | Closed source, Nexus | Progress events completed by a party member advance the guest's own world too; bosses do not eject co-op partners; Site of Grace rest resets world state for all to avoid desync. Fast travel by vote. [PRESS] | Pattern: event flags (boolean) are trivially mergeable. Vanilla gives progress to host only. |
| CyberMP (Cyberpunk 2077) | Closed beta | No quest co-op. Free-roam, races, deathmatch only. [PRESS] | Confirms how hard it is for a scripted-quest game. |
| Witcher Online | Closed source | Syncs positions; party system (v4.0) syncs time/weather and leader's dialogue choices, plus cutscenes. Quest progress not synced; players are told to keep their saves in lockstep. [PRESS, conflicting] | "Party leader chooses dialogue for everyone" is a cheap workable design. |
| OblivionOnline / OblivionMP | OBSE-based older project; OblivionMP (Remastered) announced | OblivionOnline: unstable, no quest info. OblivionMP lists planned custom scripts/quests. [PRESS] | Nothing concrete found. |
| Starfield Together / Fallout Together | Open-sourced remnants | See section 2. | No quest design found. |

---

## 6. Academic / GDC-style writing

I found no GDC talk or paper on this exact topic. Closest useful items:
- ISLE portable multiplayer world-state assessment (https://git.mattkc.com/isledecomp/isle-portable/raw/commit/12a63c105c14f5ac11701a019983f5be8f949d4c/docs/multiplayer-world-state-assessment.md): the hard part is "introducing authority into a currently symmetric system" and intercepting direct-mutation code at a chokepoint. Recommends a server-assigned host with migration plus a full snapshot, and sending the type of change instead of the resulting value. Third-party write-up but a good checklist.
- General retrofit advice (Godot guides, DevCom 2023 coherence talk): separate input from simulation, authority on one side, late-join snapshots.
- Host-authoritative scripting and script deduplication are not covered by anything I found. This means we must design it ourselves.

---

## 7. Ranked ideas to adopt for New Vegas

1. **Typed delta packets at the engine chokepoints, replayed through the native call, with an anti-echo guard.** Hook quest stage set, objective complete/display, and (via xNVSE) game-variable writes. Send `{questFormId(normalized), stage, kind}`. Apply by calling the same engine setter under a `ScopedQuestOverride` flag. [STR, TES3MP, both confirmed in code]
2. **Server-held canonical quest log for late join, reconnect and host migration.** Persist `{quest, stage, objectives, flags}` like TES3MP's world JSON and TiltedEvolution's `QuestLog`. Send the snapshot on join. [both]
3. **Per-category share flags, defaulted per the TES3MP table** (journal/stages, topics-said, faction rep, kills, karma/reputation, globals), plus an allow/deny list of quests. This makes "full co-op" vs "my own story" a server setting. [TES3MP]
4. **Whitelist-driven sync of script variables / game globals.** Do not sync all of them. Start with the vanilla globals and the specific quest script variables for a handful of main-story quests. [TES3MP]
5. **Single authority for who runs quest and dialogue result scripts.** Recommended: the dialogue initiator (or the cell owner) runs them and the rest receive only effects (stage, items, flags, NPC state). Avoids duplicate effects. Tilted's replay-everywhere model caused item duplication. [Tilted changelog]
6. **FormID normalization by (plugin name, local id)** instead of raw load-order index, because load orders differ. [Tilted `ModSystem`]
7. **Party-leader/vote dialogue and cutscene gating.** Leader picks dialogue; others see subtitles/voice (Witcher Online, Tilted SubtitleRequest); voting for fast travel and cutscene skip (Seamless Co-op). Cheap and robust.
8. **Merge rules by type.** Booleans/flags: OR. Monotonic stages: max, with an explicit exception list for quests that can go back. Kill/death flags: union. (Seamless Co-op merges event flags.)
9. **Exclusion table plus a debug dump of quests with type/priority** so quests can be audited against the sync rules. [Tilted `kNonSyncableQuestIds`, `DebugDumpQuests`]

## 8. Pitfalls to avoid

- **Echo loops:** applying a remote stage fires your own hook. Always guard. Also beware recursive stage scripts that set other stages.
- **Result scripts run N times:** replaying `SetStage` on every client re-runs stage scripts (AddItem, spawn, Enable/Disable). Decide per quest whether to replay the script or only set the stage silently. Quest-item duplication was a repeated Tilted bug.
- **Hidden/background quest scripts** (the Misc/None type in Skyrim) carry per-player state. Tilted ships them off by default and "experimental". Expect the NV equivalent (e.g. quests that poll the player each frame) to need a deny list.
- **Cell-owner NPC simulation (NV:MP) vs quest NPC death.** The 2016 NV:MP failure: someone kills a quest NPC and others fail on entering the cell. We need quest-critical NPC state (dead, disabled, location, AI package) to be synced from an authoritative source, not left to whoever simulates.
- **Per-player engine state not covered by globals:** companion lists, player faction rank, karma, Pip-Boy note ordering, active quest target (marker), and the current dialogue topic. Decide what each means with two players.
- **Different load orders and form IDs.** Do not put raw FormIDs on the wire.
- **Unsynced script variables silently de-sync story:** TES3MP's solution is a whitelist plus tests quest by quest. Budget for per-quest verification of the main story, not a generic fix.
- **Dialogue is the hard part and is not fully solved by anyone.** Nobody found syncs full branching conversation; they synchronize outcomes (journal/topic) or follow the leader. Don't plan on lock-step dialogue UI.
- **Cutscenes/forced controls** (e.g. the Doc Mitchell opening, Benny's shooting): the player controller is taken by a script. Needs an explicit "all players in scene" rule or skip.
- **Do not depend on quest data from press claims about NV:MP.** Their quest sync status, hooks and SDK are not public, and the SDK docs are placeholders. Verify with the reverse-engineering tracks before committing to "layer on NV:MP".
- **Host migration:** TES3MP and ISLE both avoid it by having a dedicated/server-assigned authority. Plan the same.

## Sources

- TES3MP: https://github.com/TES3MP/openmw-tes3mp , https://github.com/TES3MP/CoreScripts
- TiltedEvolution: https://github.com/tiltedphoques/TiltedEvolution (Code/client/Services/Generic/QuestService.cpp, Code/server/Services/QuestService.cpp, Code/encoding/Structs/QuestLog.h, Code/client/Games/Overrides.h, CHANGELOG.md)
- VaultMP: https://github.com/Foxtacles/vaultmp
- NV:MP: https://nuwiki.nv-mp.com/wiki/Main_Page , https://nuwiki.nv-mp.com/wiki/Ring_A_Ding_Ding:_Home , https://steamcommunity.com/sharedfiles/filedetails/?id=3292884725 , https://www.tweaktown.com/news/50015/new-mod-brings-co-op-fallout-vegas/index.html
- Witcher Online: https://www.nexusmods.com/witcher3/mods/11590 , https://www.digitalcitizen.life/the-witcher-3-online-mod-adds-party-system-and-cutscene-sync-for-co-op-play/
- Seamless Co-op coverage: https://www.ggrecon.com/articles/elden-ring-mod-finally-fixes-co-op-mode/
- CyberMP coverage: https://www.dsogaming.com/videotrailer-news/modders-are-working-on-a-multiplayer-mod-for-cyberpunk-2077-and-heres-your-first-look-at-it/
- Starfield Together: https://starfieldportal.com/article/skyrim-together-modders-cease-work-on-starfield-together
- ISLE assessment: https://git.mattkc.com/isledecomp/isle-portable/raw/commit/12a63c105c14f5ac11701a019983f5be8f949d4c/docs/multiplayer-world-state-assessment.md
