# 05 - NV:MP internals and ecosystem intel

Scope: what the local repos tell us, what the web says, and a recommendation on extending NV:MP versus building our own co-op netcode. Facts are marked [local] (read from the repos), [web] (fetched or searched), or [inferred].

## 1. Local repo findings

### 1.1 x-native lib_net (2020 snapshot, "NVMP-X / NVMP10")

All paths are under `src/x-native/source/lib/lib_net`. The code is about 1,700 lines and is an early rewrite. `app_client` is not included, and the README says the legacy client is "still closed source". The README also says Release builds do not compile because of deliberate hand-coded errors.

**Transport.** ENet (`enet_initialize`, a "connection layer") with `NETWORK_LAYER_MAX_CHANNELS 1` and `NETWORK_LAYER_TIMEOUT 5000`. The default port is 27015. [local]

**Object model.**
- `NetworkObject` has a 32-bit `NetworkObjectIdentifier` and a `NetworkConnectionIdentifier m_Owner`.
- Owner sentinels are `NETWORK_OBJECT_OWNER_SERVER (-1)` and `NETWORK_OBJECT_OWNER_NULL (0)`. The limit is `NETWORK_OBJECT_MAX 512`.
- Locality is derived from ownership: `IsLocal() = m_Owner.IsLocal()`.
- Virtual hooks are `PreJobUpdate`, `MainJobUpdate` (run on a job system), `ShouldUpdate`, `ShouldSynchronise(connection)` and `Synchronise(sync)`.
- Types are registered through an `ObjectManifest` linked list. Each manifest holds `typeID`, a size, `maxInstances`, and creation and deletion function pointers.
- Each manifest carries an instance-index bitmask plus a "shadow" copy of it. The server writes the bitmask to the client every tick, and the client creates or deletes objects by diffing bit changes. This is presence replication by bitmap.
- The only type IDs defined are `NETWORK_OBJECT_ID_ACTOR` and `NETWORK_OBJECT_ID_WORLD_ITEM`. `NETWORK_MAX_ACTORS` and `NETWORK_MAX_WORLD_ITEMS` are both 255.

**Variable model.**
- `NetworkVariableBase` is registered on the owning object and has a compile-time hashed name.
- `NetworkVariableUInt<T, Bits>` and `NetworkVariableInt<T, Bits>` call `sync.Synchronise(value, bits)`. The signed variant writes the sign bit separately.
- There is no delta compression or dirty tracking in this snapshot. Variables are written in full each pass.

**Wire format.**
- `NetworkSynchronisation` is one symmetric bit-packed read/write serializer with modes `SYNC_MODE_READ` and `SYNC_MODE_WRITE`.
- Code writes `Synchronise(value, nbits)`, and the same code path serves both directions. The buffer maximum is 1024 bytes (`NETWORK_SYNCHRONISATION_MAX_SIZE`).
- Identifiers are `[reserved bits | 8-bit synced ID]`. Bit 0x80000000 means "local". The constructor has an obvious bug: it uses `&&` where `&` was intended.
- `NetworkManager::Synchronise` loops over every connection and every object and calls `Synchronise` on each one that passes `ShouldSynchronise`. There is no interest management beyond that hook.
- The newer SDK exposes PVS ("potentially visible set") checks, so the 9.4 server is clearly far more advanced than this snapshot.

**What is useful.**
- The design is a clean, minimal sketch: manifest, bitmask presence, symmetric serializer. It is reusable as design inspiration.
- The code is not a protocol spec for 9.4. It predates it by years and the real client and server code lives in closed binaries.
- The GitHub repo has no declared license (see 3.3).

### 1.2 NVMP-SDK entity model (C# server SDK, mirror of the deleted NVMP/SDK)

This only applies to dedicated scripted servers. The story and co-op server (`nvmp_storyserver.exe`) is not scriptable. [local, matches FINDINGS.md]

**Interfaces.**
- `INetReference` is a networked TESObjectREFR. Properties include `FormID`, `ModFile`, `FormType`, `RefID` (settable only at creation) and `SimType`.
- Placement properties are `Position`, `Rotation`, `Worldspace`, `Interior` and `VirtualWorldID`.
- Ownership and lifecycle: `PlayerOwner`, `PVSCheck`, `IsInPVS`, `Destroy`, `SaveState` and `ReloadState`. State is also encodable via `Encode`, `Decode` and JSON.
- Other properties include `Script`, `InterpMode`, attachments, `PlaySound`, `Morph` and `Activatable`. Events are `Activated`, `ActivatedOtherReference` and `Damaged`.
- `INetActor : INetReference` adds `Health`, `Level`, inventory methods, `SetActorValue`/`GetActorValue`, `PlayIdle`, `AddPackage`, targets, `FireWeapon`, `Kill`, `Resurrect`, `KnockedState`, `MovementFlags` and the `Died` and `Attack` events.
- Other interfaces: `INetCharacter`, `INetPlayer`, `INetWorldSpace`, `INetZone` and `INetLabel`.

**SimType.** The doc comment says the SDK default is `Static`. The `NetReferenceSimulationType` enum exists, but I did not enumerate its values. Next step: grep that enum.

**Ownership model.** Each ref has one simulating owner, a "local" copy on the owning client and "ghost" copies elsewhere. The `PlayerOwner` setter and the changelog (below) confirm ownership transfers on door activation and attachment.

**Encounters.** `INetCharacterEncounterConfig` and `NetCharacterEncounterTypes {EncounterActors, EncounterReference, EncounterDoor}` are per-player switches. The docs say that "players will sync to the server specific gameplay objects they encounter. The server is never fully aware of the state around a player" unless clients send encounter messages.

This confirms the architecture. The server is not authoritative over the world. Clients discover actors, upload them, and the server arbitrates owners. This is a client-authoritative per-reference model, and it is exactly where quest NPCs get excluded (FINDINGS.md, FUN_100cc9e0).

**Absent from the SDK.** There is no quest, global variable or script variable replication API in the files I listed. A grep for "quest" in the SDK would confirm this; I did not do it, so treat "none" as likely rather than proven.

### 1.3 NVMP-Companion (community patches, targets NV:MP 8.1, not 9.4)

The repo has two parts: `nvmp_companion` (an NVSE plugin) and `nvmp_server_hook` (a DLL injected into `nvmp_storyserver.exe`).

**How offsets are found.**
- Fixed RVAs, with no signature scanning at all.
- The code takes `GetModuleHandleA("client.dll")` (falling back to `"nvmp\\client.dll"`), adds a hardcoded RVA, then `memcmp`s the expected bytes before patching.
- Patching goes through `VirtualProtect`, a write and a restore.
- The server uses `GetModuleHandle(nullptr)` plus RVA, with a prologue check `55 8B EC 6A FF`.
- A version bump therefore breaks every patch silently. The expected-byte check prevents crashes, but the patch is skipped.

| Target | RVA | Purpose / patch |
|---|---|---|
| client.dll damage receiver | `0x0009CFC9` | `0F 85` (JNE) to `90 E9` (NOP + JMP). Skips the `[victim+0x1AE] != 0` locality check, so ghosts can be damaged. |
| client.dll overlay text | `0x0009AA3C` | 7-byte `lea eax,[ebp-0x1D4]; push eax` replaced by `mov eax,&modeText; push eax; nop`. Cosmetic. |
| client.dll globals | `GLOBAL_ENCOUNTER_PTR_RVA`, `GLOBAL_CHAR_MGR_RVA` | Constants in `nvmp_state.h` (not read). Used by `encounter_toggle.cpp`, which is a runtime toggle of encounter behaviour (not read in detail). |
| storyserver attack handler | `0x001098E8` | `74` (JE) to `EB` (JMP). Ownership check bypass. |
| storyserver `ShouldSynchronise` | `0x00105FE0` | 5-byte detour into a VirtualAlloc'd hand-assembled stub. Blocks sync per player for `[SP]`/`[HB]` names. |
| storyserver GameNetCharacter manager | `0x002F9CC4` (global ptr) | List head at `mgr+0x54`, node `+0x00` next, node `+0x0C` netref. |
| storyserver ResolveTarget | `0x000FDF00` | Target context to player netref. |
| FNV engine | `0x011DEA3C` | `g_thePlayer`. |

**Net-reference layout (8.1).**
- `+0x04` is the class-name pointer.
- `+0xF4` is the flags byte; bit 0x01 means in-PVS.
- `+0x108` is the life state.
- `+0x1AE` is the locality byte (0 ghost, 1 local).
- FINDINGS.md says the same `+0x1AE` offset appears in 9.4 `FUN_10056040`, so struct layouts are fairly stable across versions even though RVAs move.

**Takeaways.**
- Per-player `[SP]`/`[HB]` hybrid modes work by blocking sync server-side, and a Nexus page describes the same modes (Multiplayer syncs ghost NPCs, Hybrid does not).
- The patch set only toggles existing behaviour and adds no new sync capability.
- The server hook is also useful as a template for hooking `nvmp_storyserver.exe`.

## 2. Web research

Several nuwiki pages 404'd and Hosting_CO-OP was not reachable. SDK docs at `nvmp.github.io/SDK` returned 404. The SDK docs source is in the local mirror under `NVMP-SDK/Documentation`. The wiki, Reddit and Steam searches returned little. Dev statements are thin.

**Changelog highlights** ([nuwiki Changelog](https://nuwiki.nv-mp.com/wiki/Changelog)). The latest entry is NV:MP 9.0 dated 18/04/2026, per the fetch. Our local binary is 9.4, so the page lags, or the fetch summary was wrong; recheck.
- 4.9 (11/02/2023): host time and weather are synced by default.
- 5.25 and 5.5: attaching an object transfers ownership; destructible objects owned by another machine take no damage until they become local.
- 5.42 to 5.45: activating a door changes ownership.
- 5.55 (07/09/2023): kill tracking added; false ownership dispatch fixed.
- 5.57 (12/09/2023): "Fixed encountered references not syncing to the server, which reset NPC appearance data"; the network code now waits for new owners to sync.
- 6.01 (24/11/2023): CO:OP players are enrolled in a global party system.
- 23/04/2024: crash fix for a local actor activating an unsynced reference.
- Nothing in the changelog mentions quest, stage or objective synchronisation.

**Dev statements.**
- A secondhand report (GameSpot, quoting a dev handle "Nvmp_corporal") says NPCs in a region are synced to a host, "but quests won't". Each player keeps their own quest log.
- Quest NPCs can fail to appear if another player already killed them in the cell. The workaround is to refresh or change hosts.
- Source: [GameSpot](https://www.gamespot.com/articles/fallout-new-vegas-is-getting-a-multiplayer-mod/1100-6434268/), plus [co-optimus](https://co-optimus.com/article/13884/fallout-new-vegas-co-op-mod-needs-beta-testers.html). These are 2016 statements.
- The binary RE in FINDINGS.md says this is still how 9.4 behaves.

**Hosting TTW page.** "The starting vault NPCs are NOT synced, But once you get outside then all NPCs and enemies will be!" ([Hosting TTW](https://nuwiki.nv-mp.com/wiki/Hosting_TTW)). This contradicts the "quest NPCs are localized" finding only if vault NPCs are quest-flagged, which they are. It is consistent with the 0x400 IsQuestItem flag observation.

**Server options.**
- I found no public docs for `AllowNPCGhosts` or `EnablePartySystem` beyond the Companion's PATCHES.md ("Party blips and location overlay").
- A web search for `AllowNPCGhosts` returned nothing relevant.
- `AllowNPCGhosts` is only visible in the 9.4 server.cfg. Hypothesis [inferred]: it controls whether non-owned NPC copies (ghosts) are instantiated on clients at all. A binary check of its readers would settle it.

**Community artefacts.**
- [NVMP org on GitHub](https://github.com/NVMP): ClientDistribution (raw client ZIP, 26 stars), Sunburst-PluginDistribution, ClientLauncher, ClientInstaller (archived), PublicServerMods, IssueTracking. No licences displayed.
- x-native is public at `dev-oren/x-native` (our clone is from ecaii; same project). It has 29 commits, 1 fork, and no license shown.
- [MPUnofficialPatch](https://github.com/MrJordanL/MPUnofficialPatch): an ESM-based unofficial patch. It disables auto-dialogue for some NPCs and removes a Brahmin companion because it "would possibly bug out if there was NPC sync". It confirms that story content needs hand-patching to work in NV:MP.
- [NVMP COOP Companion and Server Patch (Nexus 97189)](https://www.nexusmods.com/newvegas/mods/97189): the Companion's Nexus page (403 on fetch, so only the search snippet is known).
- No forks or leaks of the closed client.dll or storyserver source turned up for `NetEncounterManager`, `nvmp_storyserver` or `lib_net networksynchronisation`. Searches returned nothing relevant.
- I did not search Reddit directly; results came only through general web search.

## 3. Strategy assessment

### 3.1 Why quests do not sync in NV:MP (summary)

1. Encounter logic (FUN_100cc9e0) localizes any quest-flagged, follower or runtime-created actor.
2. The ownership model has one simulating owner per ref.
3. Nothing replicates quest stages, global variables or script state.

A co-op that finishes the story needs all three fixed: shared quest state, shared persistent-NPC state (alive/dead, location, dialogue progress), and shared world-state flags.

### 3.2 Strategy A: extend NV:MP (runtime patches + our own xNVSE plugin + side-channel networking)

**Pros.**
- Gets us the hard parts for free: player and NPC position and animation sync, combat, inventory, PVS, ENet transport, and the existing server and launcher infrastructure.
- Friends already know the install flow.
- Our quest layer could be a small plugin: read quest stages and globals through xNVSE, ship them over a side channel (or over the server hook), and apply them on the other clients.
- The Companion repo proves the injection approach works (NVSE plugin plus server DLL).

**Cons.**
- Maintenance is the main risk. Every NV:MP update changes RVAs. The Companion pins to 8.1 with fixed offsets and no signature scanning, and we are already on 9.4. We would need signature scanning from day one.
- The core policy conflicts with what we need. Quest NPCs are deliberately excluded from sync. Flipping that means patching `FUN_100cc9e0`, and then the ownership model must cope with quest-script-driven NPCs (scripts run on every client or on only one?). If two clients each run quest scripts on the same NPC, we get divergence. NV:MP never had to solve this.
- The server is closed and has no scripting in story mode. A side channel needs its own server process. We would effectively run two netcodes and keep them in step.
- Quality ceiling is bounded by a client-authoritative per-reference design that was built for MMO-style hubs (see the changelog: homes, RADD, arena). Scripted cutscenes, AI packages and dialogue state are hard to make consistent.
- Anti-cheat and trust: none, but that is fine for friends.

**Legal and licensing.**
- client.dll and the server are proprietary and closed. The wiki and GitHub show no license for them.
- Runtime patching is what the community Companion already does publicly (Nexus), so it appears tolerated, but I found no explicit permission.
- Redistribution of NV:MP binaries or patched copies is off the table; ship only patches that apply at runtime.
- The open repos (x-native, SDK mirror, launcher) carry no visible license text, so default copyright applies: we may read them, but should not copy code.

### 3.3 Strategy B: own netcode (2-8 players, host-authoritative, xNVSE)

**Pros.**
- Full control of the semantics we actually need: the host runs all quest scripts and AI, and clients are mostly puppets plus local player input. This avoids the split-brain problem in 3.2.
- No dependency on closed binaries or version drift. FNV 1.4.0.525 is frozen, and xNVSE is actively maintained.
- Replicating quest stages, globals, persistent-ref state and save-game-like state is a first-class design goal rather than a bolt-on.
- A small player count removes the scalability machinery (PVS, bitmask presence, sharding).
- Legal position is clean: no NV:MP code or binaries are touched. Design ideas from x-native (manifest, bit-packed symmetric serializer) are not copyrightable expression. Reimplement rather than copy.

**Cons.**
- Much more work. We must build from scratch: player avatar spawning and animation sync, combat and damage, inventory, doors and cells, dialogue, VATS-free combat, save and load, reconnect. NV:MP spent years on these.
- Hard engine problems remain: remote players as actors need cells loaded around them, and host and client may be in different cells (the game only simulates the player's loaded area). A host-authoritative model needs the host to simulate cells the clients occupy, which FNV does not do. This is the central difficulty of any FNV co-op and is why NV:MP chose client authority.
- Matches NV:MP's hard-won stability only after long testing.

**Quality ceiling.** Higher in principle for story fidelity, but only if the "different cells" problem is handled, for example by keeping players together (shared-cell rule, teleport-follow, or a "party leader owns the world" rule, as in many co-op mods and in NV:MP's own hybrid ideas).

### 3.4 Comparison

| Factor | A: extend NV:MP | B: own netcode |
|---|---|---|
| Time to first playable | Short (weeks) for basics, quest layer open-ended | Long (months) for basics |
| Quest and story fidelity ceiling | Limited by client-authority model and closed code | High, if the cell problem is solved |
| Breakage on NV:MP updates | High (RVAs; need sig scanning) | None |
| Legal exposure | Moderate (patching closed binary; tolerated by precedent only) | Low |
| Reuse of NV:MP community | High | None |
| Friends' setup burden | NV:MP install plus our plugin | Our plugin only |

### 3.5 Recommendation

Do not choose purely A or B. Use a staged approach, B-leaning:

1. Short term (spike, days): A-lite. Prove the quest layer in isolation using NV:MP as the transport for players only. In Hybrid-like mode (NPC sync off), run a side-channel xNVSE plugin that mirrors quest stages and globals from a host. This tests the actual hypothesis (do mirrored quest states give a playable story?) cheaply. Use signature scanning rather than fixed RVAs.
2. Decide at the end of the spike. If mirrored quest state plus "everyone stays together" is enough, stay on A. If NPC divergence (dead or moved quest NPCs, scripted scenes) is the pain point, move to B. The host-authoritative design from 3.3 needs the party-together rule anyway.
3. In either case, reuse the x-native design ideas (manifest, symmetric bit serializer, ENet) for the side channel. ENet is MIT-licensed; check it before use.

My judgement is that B gives the best final quality, while A gives the fastest evidence. I would not invest in deep patches to client.dll beyond what the spike needs.

## 4. Open items / next steps

- Grep the SDK for quest, global and script-variable APIs, and enumerate `NetReferenceSimulationType` values.
- Decompile readers of `AllowNPCGhosts` and `EnablePartySystem` in 9.4 (server and client).
- Check 9.4 `FUN_100cc9e0` caller context to see whether the quested check can be bypassed with a single patch (FINDINGS.md item).
- Read `encounter_toggle.cpp` fully; it may already toggle the encounter global at runtime, which would be the cheapest quest-NPC sync experiment.
- Reconcile the 9.0 versus 9.4 version discrepancy against the changelog page.
- Try the NV:MP Discord and GitHub IssueTracking repo for dev statements on quest sync (not reachable from here).
- Confirm licence terms on nv-mp.com (the About and Disclaimers pages 404'd under the guessed URLs).
