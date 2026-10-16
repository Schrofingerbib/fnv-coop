# Canonical world-state snapshot (schema v1)

The universal test oracle (docs/00-test-harness-design.md section 2). Produced in-game by
`fnvcoop::snapshot::SnapshotJson()` (L1's `state` method), compared by `plugin/snapshot/tools/snapdiff.py`
and `converge.py`. Machine-readable schema: `plugin/snapshot/snapshot.schema.json`.

## 1. Principles
- **Deterministic**: all object keys sorted, fixed number formatting (reals: 6 decimals, trailing zeros trimmed, `-0` -> `0`, NaN/Inf -> `null`), arrays that are sets are sorted ascending. Two dumps of the same state are byte-identical.
- **FormRef** = `"Plugin.esm:XXXXXX"` (upper-case, 6 hex digits, load-order byte stripped, plugin name resolved through the data handler). Runtime-created refs are `"dyn:FF000123"` (not comparable across machines; avoid in oracles). EDID is stored as a field (`edid`), never as the key, because FormRef is the stable identity and many runtime forms have no EDID (then `""`).
- **Never vacuous**: `meta.counts` declares how many quests/globals were dumped; consumers reject a snapshot whose counts disagree with its content, whose `meta.error` is set, or that has fewer than 50 quests / 10 globals (real FNV: >1000 / >600). Both CLIs exit 2 on such input, even if both inputs are byte-identical empties.
- Authority split follows docs/MASTER-PLAN.md: `shared` must be identical on host and every client; `perPlayer` is per-machine and only compared when asked (or by explicit convergence rules below).

## 2. Top level
```
{ "schemaVersion": 1,
  "meta": {...},
  "shared": {...},        // omitted if SnapshotOptions.shared=false
  "perPlayer": { "<playerId>": {...} } }   // omitted if SnapshotOptions.perPlayer=false
```

## 3. `meta` (ignored by diffs unless `--include-meta`; `timestamp`/`instance` are never diffed)
| field | meaning |
|---|---|
| `gameVersion` | `"1.4.0.525"` |
| `plugins` | load-ordered plugin file names |
| `pluginsHash` | FNV-1a-64 (16 hex) of the newline-joined plugin list; equal hash = FormRefs comparable. `--include-meta` makes a mismatch a difference |
| `timestamp` | ISO-8601 UTC, caller-supplied, optional |
| `instance` | free label ("host", "client1") |
| `counts` | `{quests, globals, reputations}` actually emitted (truncation detector) |
| `error` | set only when the dump failed; guard rejects |

## 4. `shared`
| key | content |
|---|---|
| `quests` | `FormRef -> {edid, name, rawFlags, running, completed, failed, currentStage, completedStages[], objectives{id->{displayed,completed}}, vars{name->number}}` for **every** TESQuest. `completedStages` = stage ids whose `isDone` is set, sorted (set semantics). `vars` = quest script variables (int/float only; keyed by script var name, or `varN`). `rawFlags` is the quest flag byte (authoritative); `running`/`completed`/`failed` are decoded from bits 0/1/2 |
| `globals` | `FormRef -> {edid, type: s|l|f, value}` for every TESGlobal |
| `reputations` | `FormRef -> {edid, fame, infamy}` for every TESReputation (faction reputation is SHARED per owner decision) |
| `karma` | the host-visible player karma AV (karma is SHARED) |
| `factionRanks` | `FormRef -> {edid, rank}` for factions the player belongs to (rank >= 0) |
| `refs` | quest-relevant references: `FormRef -> {exists, edid, enabled, alive, cell}`. Contains (a) `SnapshotOptions.watchRefs` (feed it from `quests.json`: quest-giver NPCs, key objects) and (b) targets of every currently displayed objective. `alive` only for actors; `cell` is the parent cell FormRef or `""` |

## 5. `perPlayer.<playerId>`
`actorValues{Name->{base,current}}` (SPECIAL, skills, Health, limbs, Karma, XP, CarryWeight, ActionPoints, InventoryWeight, RadLevel), `inventory{FormRef->{edid,count}}` (caps excluded), `caps` (int), `perks{FormRef->rank}`, `position{x,y,z,rotZ,cell}`.

## 6. Comparison semantics and tolerances (implemented in `snaplib.py`)
- Dict key order, whitespace, and the order of `completedStages` never matter. Other arrays compare ordered.
- Ints/bools/strings compare exactly. A pair where either side is a non-integer float uses an absolute tolerance, first matching rule wins (override with `--tol 'glob=abs'`):

| path glob | abs tol |
|---|---|
| `shared/globals/*/value`, `shared/reputations/*/*`, `shared/karma` | 1e-3 |
| `shared/quests/*/vars/*` | 1e-4 |
| `perPlayer/*/actorValues/*/*` | 1e-2 |
| `perPlayer/*/position/rotZ` | 5e-2 |
| `perPlayer/*/position/*` | 1.0 world unit |
| anything else | 1e-4 |
- **Volatile globals** whose EDID is `GameYear, GameMonth, GameDay, GameHour, GameDaysPassed, TimeScale` are ignored (per-machine clocks). `snaplib.diff(..., default_ignores=False)` shows them.
- Default sections: `shared` only. `--sections shared,perPlayer` adds per-player data; `--include-meta` adds meta (still never timestamp/instance).
- Exit codes: `0` equivalent, `1` semantic difference, `2` invalid/vacuous input.
- `converge.py host=a.json c1=b.json ...`: every other snapshot's `shared` == reference's. Additionally, whenever `perPlayer` is present: every player's Karma AV must equal `shared.karma` (karma shared), and `--require-items REF,...` asserts each player holds the listed quest items (quest items to everyone).

## 7. Dumper API (`plugin/snapshot/include/fnvcoop_snapshot/snapshot.h`)
`std::string SnapshotJson(const SnapshotOptions&)` - main thread only, never throws; SEH fault -> snapshot with `meta.error` and zero counts (rejected by the guard). Options: `shared`, `perPlayer`, `playerId`, `instance`, `timestamp`, `indent`, `watchRefs`, `questEdidPrefixes` (a filtered dump cannot satisfy the guard; for debugging only).

## 8. Needs in-game engine verification (all marked `VERIFY` in `snapshot_nvse.cpp`)
1. **Quest flag bits**: bit0=running and bit1=completed are from NVSE comments; `failed`=bit2 is an assumption. `rawFlags` is always emitted, so diffs are correct regardless; only the decoded labels may be wrong.
2. **TESReputation fame/infamy** at +0x44/+0x48 as floats (from docs/02-engine-re.md; NVSE declares the class empty). Check against `GetReputation`/console `getrep`.
3. **Objective enumeration**: `lVarOrObjectives` mixes objectives and variables; I classify by `objective->quest == this quest`. Confirm no variable pointer is misread as an objective (would be a crash caught by SEH -> error snapshot).
4. **Quest script vars**: `scriptEventList->GetVariable(idx)` for quests whose event list is not created until the script first runs (should skip cleanly). Confirm names match the GECK script.
5. **Actor value reads**: `avOwner.Fn_03` (current) / `Fn_01` (base) declared with a float return but NVSE comments disagree on EAX vs st0; verify `Karma`, `Speech` against console `player.getav`.
6. **Faction ranks**: merge of base TESNPC list with `ExtraFactionChanges`; verify order after `addtofaction` / `setfactionrank`.
7. **alive**: currently health AV > 0; replace with the engine IsDead vfunc (`Actor::Unk_8B`) once confirmed. Also decide handling of essential/unconscious actors.
8. **enabled**: ref `flags & 0x800` (same test `GetDisabled` uses).
9. **Inventory**: `TESObjectREFR::GetInventoryItems` includes equipped items and merges base container + changes; confirm caps (`0x0F`) count and that quest items are included.
10. **Perks**: `GetPerkRank(perk, false)` vfunc usage and that perk list covers DLC/trait perks.
11. **EDID availability**: runtime strips most EDIDs; quests (`editorName`) and globals (`name`) have them, other forms may give `""` (harmless: FormRef is the identity).
12. **Volatile state not in the snapshot**: AI packages, NPC positions and combat state are intentionally absent (non-deterministic); add targeted `refs` fields only when a test needs them.
13. Dump cost: ~1000 quests + ~700 globals under SEH on the main thread; measure (expect low tens of ms) and consider a quest-dirty cache if `state` is polled in `waitFor` loops.

## 9. Building valid synthetic snapshots (for other lanes' tests)
Easiest: `plugin/snapshot/tools/synthetic.py` (`base_snapshot()`, `make_synthetic(base, {path: value})`, `quest_entry`, `player_entry`, `Add`, `DELETE`; docstring has examples). It keeps `meta.counts` consistent after every mutation. Rules the tools enforce (`snaplib.guard`, used by snapdiff, converge, effect_oracle, check_stage); a violation gives **exit code 2**, never a pass:
1. `schemaVersion == 1`; `meta` object with `gameVersion` and `counts` (`counts.quests`, `counts.globals` required; `reputations` optional).
2. `meta.error` must be absent.
3. `shared` present with all of `quests, globals, reputations, karma, factionRanks, refs` (empty objects are fine for the last three; `karma` a number).
4. At least **50 quests and 10 globals** by default (override: `--min-quests N --min-globals N` on snapdiff/converge/check_stage, or `min_quests=`/`min_globals=` in the Python API; pass them explicitly in small unit fixtures).
5. `meta.counts.quests == len(shared.quests)` and `meta.counts.globals == len(shared.globals)`. Hand-editing a snapshot (adding/removing a quest or global) without updating counts is the usual cause of an unexpected exit 2.
6. Every quest object has all of `edid, rawFlags(0..255), running, completed, failed, currentStage(0..255), completedStages[int], objectives{id: {displayed, completed}}, vars{name: number}`; no extra keys (`additionalProperties:false`). Globals need `value` (number); optional `edid`, `type` in `s|l|f`. Reputations need `fame` and `infamy`. Refs need `exists`; optional `edid, enabled, alive, cell, vars{name: number}`.
7. Keys of quests/globals/reputations/factionRanks/refs/inventory/perks must be FormRefs: `Plugin.esm:XXXXXX` (6 upper-case hex) or `dyn:XXXXXXXX`.
8. When `perPlayer` is compared/required, each player needs `actorValues{N:{base,current}}, inventory, caps, perks, position{x,y,z,cell}`.
Use `python -I plugin/snapshot/tools/snapdiff.py a.json b.json` on a fixture: on exit 2 it prints exactly which rule failed (first 10 schema errors included).

## 10. World-baseline dumper (`baseline_dump.h`, for L10 / L4 "World baseline")
`bool fnvcoop::snapshot::DumpBaselineShadow(fnvcoop::ShadowState* out, uint64_t* contentHash, std::string* err)` (plus `DumpBaselineShadowEx(..., const BaselineDumpOptions&)` for watch refs / volatile globals). Header: `plugin/snapshot/include/fnvcoop_snapshot/baseline_dump.h`; needs `synccore/include` on the include path (header-only use of `fnvcoop/shadow.h`, no synccore library link). Main thread, after a save is loaded; SEH-guarded; returns false + `err` on engine fault, no player/data handler, no plugins or no quests (never a vacuous success). Layer split: `BaselineInput` (plain data) -> `BuildShadowFromBaseline` (game-free, tested against real synccore) <- engine reader `baseline_nvse.cpp`.
Fills: `stages` (EVERY quest, zeros included), `objectives` (status&3 for every objective), `scriptVars` (key = (owner, variable index) for every numeric quest var and every watched ref with an attached script), `globals` (all except per-machine clocks GameYear/Month/Day/Hour/DaysPassed/TimeScale unless `includeVolatileGlobals`), `questRunning` (all quests), `questCompleted`, `allObjectivesDone` (objectives non-empty and all completed), `enabled` (watched refs, true and false), `kills` (watched actors with health <= 0 ONLY), `locks` (watched refs with ExtraLock), `factionRank[(player ref, faction)]` for EVERY faction (-1 = none), `sayOnce` (INFOs with TESTopicInfo.saidOnce set), `ledger[0].karma` and `ledger[0].rep[faction]=(fame,infamy)` (rounded absolute values). Not filled (empty): `placed`, `textures`, `companionOwner`, `tethers`, `slideshowEnded`, `saveSuppress`, barriers (need L5 hook knowledge). `contentHash` == `fnvcoop::contentHashOf(lower-cased plugin names in load order)` (parity test). Watched refs = options + file/env `FNVCOOP_WATCHREFS` (see `tools/watchrefs.py`); refs absent on this machine are simply omitted; runtime-created (`dyn`) refs use plugin name "dyn" and should not be synced.
Needs in-game verification: everything in section 8, plus the player ref id used as the factionRank actor (`pc->refID`, expected 0x14), `TESTopicInfo.saidOnce` semantics, ExtraLock flag bit 0 = locked, health<=0 as dead, and that reading ~800 watched refs + 29k INFOs + all quests stays within a tolerable time budget (one-off at join).

## 11. Volatile keys (data-driven; ONE grammar, ONE loader)
Some state changes by itself (timer vars such as `fStartTimer`, radio quests toggling `running`). Transferring it creates spurious join diffs, so it is left out of baselines, commits and replication. The set is MEASURED, never guessed from names.
**Single policy object:** `fnvcoop::volatileKeys()` (synccore/include/fnvcoop/policy.h, L4). The baseline dumper does NOT have its own list: `DumpBaselineShadowEx` loads the file with `volatileKeys().loadFromText`, resolves variable names to indices with the live script var lists (`resolveNames`, also benefiting the session filter), and filters through `isVolatileVar` / `isVolatileGlobal` / `isVolatile(Effect)` (field entries are queried as StartQuest / SetStage / SetObjective on the quest). Dump filter and session filter therefore cannot disagree. The dumper needs synccore's `policy.cpp` linked (the coop module provides it).
**Grammar** (text, `#` comments; `<form>` = `Plugin.esm:HEX` or `Plugin.esm HEX`):
`var <form> <index|name>` | `owner <form>` (every var of that quest/ref) | `global <form>` | `field <form> running|stage|objectives`. Anything else is an error (there is no whole-quest directive). A named file that is missing/malformed makes the dump FAIL. Env `FNVCOOP_VOLATILE_KEYS` or `BaselineDumpOptions::volatileKeysFile` names the file; no file = only the clock globals are excluded. After each dump `LastBaselineStats().summary` reads `volatile keys: file=... entries=N excluded vars=V fields=F globals=G clock-globals=C unresolved-names=U`.
**Producer:** `plugin/snapshot/tools/volatile_probe.py probe` over repeated `state` dumps taken with no player action (groups = launches): a key is volatile if it differs between consecutive dumps of a launch ("session") or between the first dumps of two launches ("launch"); floats within 1e-4 (vars) / 1e-3 (globals) are ignored. It writes `var <ref> <name>`, `field ...`, `global ...` lines (+ a JSON record with edids and reasons). `plugin/snapshot/tools/probe_run.py` does the whole in-game procedure under the gameslot lease (launch, N dumps, quit, relaunch, probe) and writes `out/snapshot/volatile_keys.{json,txt}`.
**Safety rules** (enforced when probing and by `volatile_probe.py check FILE --scenarios DIR`, exit 1): the 234 story quests of L3 (scenario `kind == "story"`) can never have a `field` entry; if one changes by itself the probe prints a WARNING and does not exclude it (vars and `owner` entries on story quests are allowed). `plugin/snapshot/tools/volatile_manual.json`: `protect` = never exclude, `add` = always exclude (still subject to the story rule).
**Agreement test:** `volatile_cross.py` writes one keys file using every spelling plus ~80 queries with expectations from the Python reference model (`ReferenceKeys`); `tests/volatile_cross_test.cpp` loads the same file through L4's real `VolatileKeys` (+ a name resolver) and must answer identically (83 checks). Mutation twin: with L4's `mutations().ignoreVolatile` switch the answers differ (18 failures) and the run fails. Both run in `run_tests.py`.
The JSON snapshot oracle (sections 1-6) is NOT filtered; compare quiesced states or pass `--ignore`.
**Shipped default (section 11 addendum):** `plugin/snapshot/data/volatile_keys.txt` (+ `volatile_keys.json` with provenance: date, launches, dumps, interval, plugin list/hash, entries, TODOs) is committed and is what the dump uses when nothing else is named. Lookup order in `DumpBaselineShadowEx`: `BaselineDumpOptions::volatileKeysFile` -> env `FNVCOOP_VOLATILE_KEYS` -> `nvcoop_volatile_keys.txt` next to `nvcoop.dll` (deploy a copy of the data file there for installs) -> the in-repo copy baked in at build time (`FNVCOOP_DEFAULT_VOLATILE_KEYS`, dev builds). The stats line says which one was used (`file=... (option|env|default)`); none found = only clock globals filtered. A named (option/env) file that is missing still fails the dump. Tests keep the shipped file parseable, story-safe and consistent with its provenance.
TODO (not done): longer soak (about 5 min idle), one dump after a cell change, re-probe after load-order changes; the first run (2 launches x 3 dumps, 20 s apart, idle autosave) found 6 timer-like vars and is a lower bound.
