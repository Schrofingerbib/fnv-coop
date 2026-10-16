# fnv-coop wire protocol v3

Status: matches `synccore/` (kWireVersion = 3, kProtocolVersion = 3; v3 added the FailQuest effect, tag 32, and the questFailed set at the end of the ShadowState encoding). Source of truth is the code:
`synccore/include/fnvcoop/{wire.h,protocol.h,effect.h,transport_util.h}`, `synccore/src/{wire.cpp,protocol.cpp,shadow.cpp}`.
Round-trip, fuzz and golden tests live in `synccore/tests/` (run `synccore\run_tests.bat`).

This document is for people writing another lane, a tool, or a compatible implementation. It describes bytes on the wire and the
rules the two session state machines (`HostSession`, `ClientSession`) follow. It does not describe game hooks.

## 1. Layers

```
game hooks (L5)  ->  effects  ->  HostSession / ClientSession (synccore)  ->  ITransport  ->  UDP (ENet)
```

* A **transport** (`ITransport`) moves opaque byte datagrams between peers. Reliable ordered by default; ENet channel 0 is
  reliable-ordered, channel 1 is unreliable (only used when a caller asks for it explicitly). Datagrams are limited to
  `kMaxMessageBytes` = 8 MiB; real traffic stays far below the MTU-sized fragments ENet handles itself.
* The **session layer** is reliable on top of any lossy transport: it retransmits, de-duplicates and re-orders on its own, so
  the protocol also works over an unreliable channel (the simulator uses 0.5% loss, 0.2% duplication, corruption, reordering).
* Topology is a star: one host, up to `maxPlayers` (<= 8, `kMaxSlots`) clients. Slot 0 is the host player (`kHostPlayer`).

## 2. Channel registry (first byte)

`MuxTransport` (`transport_util.h`) dispatches on the first byte of every datagram so several protocols share one connection:

| First byte | Channel | Owner |
|---|---|---|
| `0xFC` | story / session envelope (everything in this document) | L4 synccore |
| `0xD1` | dialogue adapter | L8 `plugin/dialogue` |

New channels must pick an unused first byte that is not 0xFC and is not 0xA1 (see 3.2), and be registered here and in
`transport_util.h`. Datagrams with an unknown first byte are dropped by the mux. `AppData` (type 8) is an alternative for small
payloads that should ride the story channel; it is best effort.

## 3. Envelope

### 3.1 Normal messages

```
offset 0  0xFC          magic0
       1  0x4E          magic1 ('N')
       2  kWireVersion  (2)
       3  type          message type id, 1..17 (section 5)
       4  [string table][body]
 end-4    crc32 LE      CRC-32 (IEEE, reflected, init/final 0xFFFFFFFF) over bytes 0 .. end-5
```

Minimum size is 9 bytes. Decode failures, in check order: `TooShort`, `Malformed` (> 8 MiB), `BadMagic`, `BadVersion`, `BadCrc`,
`Malformed` (body invalid / unknown type), `Trailing` (bytes left over after the body). A receiver silently drops anything that
does not decode `Ok`; the sender's retransmission covers the loss.

### 3.2 Handshake messages (Hello = 1, Reject = 3)

Identical layout, but byte 2 is the version-independent value `kHandshakeEnvelope` = 0xA1 instead of `kWireVersion`. This lets two
incompatible builds still exchange Hello/Reject and explain why they cannot play together. For these two types:

* the body must never change in an incompatible way: only append new fields at the end;
* decoders must tolerate trailing bytes (a newer build may append fields), so `Trailing` is not an error for them;
* the first fields (`session`, and `reason` for Reject) stay in place forever.

### 3.3 Primitive encodings (class `Archive`, one symmetric reader/writer)

| Name | Encoding |
|---|---|
| `u8/u16/u32/u64` | fixed width, little endian |
| `varu`, `varu32` | LEB128 unsigned varint (max 10 bytes for 64 bit; 32-bit variants reject values > 2^32-1) |
| `vari`, `vari32` | zigzag then LEB128 |
| `f32` | IEEE-754 binary32 LE bits; **non-finite values are an encode and decode error** |
| `f64` | binary64 LE bits; same finite rule |
| `boolean` | one byte, 0 or 1 (anything else is malformed) |
| `str` | varint length, raw bytes (no terminator); cap 255 unless stated |
| `bytes` | varint length, raw bytes; cap stated per field |
| `count` | varint element count; rejected if above the field's cap **or above the bytes remaining** (every element is at least 1 byte) |

After any failure the archive is poisoned: reads return zeros, writes are no-ops, and the message fails as a whole. There is no
partial decode.

### 3.4 String table

`[string table][body]`: the table is `varu n` followed by `n` strings (`str`). Inside the body, a `FormRef`'s plugin name is a varint
index into this table. Writers intern each distinct plugin name once per message (max 4096 entries, names <= 255 bytes); readers
reject out-of-range indexes. This is why `FormRef`s are cheap: `Plugin.esm` is sent once per message, then 1 byte each.

## 4. Shared structures

All integers below are the varint/fixed forms of 3.3 in the order listed.

* **FormRef**: `pluginRef` (string table index), `varu32 localId`. Never a raw load-order FormID. Plugin names should be
  lower-cased (`FormRef::make`); comparison is byte-exact.
* **Recipient**: `u8 mode` (0 Default, 1 Origin, 2 All, 3 Mask), then `u8 mask` only when mode == 3 (bit i = slot i).
* **Cause**: `u8 kind` (0 Unknown, 1 QuestResult, 2 DialogueResult, 3 Console, 4 Loot, 5 Combat, 6 Script, 7 Intent; >= 8 rejected),
  `FormRef source`, `vari32 aux`.
* **SharePolicy**: `u8 bits` (bit0 journal, 1 globals, 2 kills, 3 karma, 4 reputation, 5 questRewardsToAll, 6 clientLoad=Block;
  bit7 must be 0), `u8 maxPlayers` (1..8).
* **EffectTxn**: `varu seq`, `u8 origin` (< 8), `u8 flags` (bit0 = origin already applied locally), `varu32 originEpoch`,
  `varu32 originIntent`, `Cause`, `count effects (<= 1024)`, then each `Effect`.
* **Effect**: `u8 tag` then the fields of that alternative. The tag is the index in the `Effect` variant (section 6). An unknown
  tag fails the whole message, so **adding or reordering effects is an incompatible change** (section 8).
* **ShadowState** (Welcome snapshot): `varu seq`, then the sections stages, objectives, globals, enabled, kills, questRunning, ...
  each as `count (<= kMaxEntries)` + entries (see `io(Archive&, ShadowState&)` in `shadow.cpp`; the order there is normative).

## 5. Messages

Type id = (index in `Message` variant) + 1. Direction: C = client to host, H = host to client. `session` is a per-connect id chosen
by the client in Hello and echoed in every later message so stale datagrams from an old connection are ignored.

| Id | Name | Dir | Body (in order) |
|---|---|---|---|
| 1 | Hello | C | `varu32 session`, `u64 token` (stable per-install identity -> slot), `varu lastSeq`, `u8 flags` (bit0 wantBaseline), `u64 contentHash`, `varu32 lastEpoch`, HandshakeInfo |
| 2 | Welcome | H | `varu32 session`, `u8 slot` (< 8), `varu headSeq`, SharePolicy, `boolean hasSnapshot` [ShadowState], `varu32 intentEpoch`, `varu32 lastIntent`, `varu32 baselineId` (!= 0: baseline transfer follows), `u8 baselineAbs` (bit0 karma, bit1 reputation synced by baseline), `varu32 worldEpoch` |
| 3 | Reject | H | `varu32 session`, `u8 reason`, `str text` (player-facing, <= 255) |
| 4 | TxnBatch | H | `varu32 session`, `varu32 worldEpoch` (0 = unspecified), `count txns (<= 256)`, EffectTxn each |
| 5 | Ack | C | `varu32 session`, `varu upTo` (highest contiguous seq applied), `varu32 worldEpoch` |
| 6 | Intent | C | `varu32 session`, `varu32 epoch`, `varu32 id`, `u8 kind`, Cause, `count effects (<= 1024)` + Effects, FormRef `ref` only if kind != AppliedEffects |
| 7 | IntentAck | H | `varu32 session`, `varu32 epoch`, `varu32 id` |
| 8 | AppData | both | `varu32 session`, `varu32 channel` (<= 0xFFFF), `u8 from` (slot), `bytes payload` (<= 64 KiB) |
| 9 | BaselineSummaryChunk | C | `varu32 session,id,index,total` (total >= 1, index < total), `count entries (<= 4096)` of (`u64 key`, `u32 hash`), `boolean hasAbs`, `vari karma`, `count rep (<= 4096)` of (FormRef faction, `vari fame`, `vari infamy`). Abs fields are only meaningful in chunk 0 but always present |
| 10 | BaselineDiffChunk | H | `varu32 session,id,index,total`, `count effects (<= 1024)` + Effects, `count revertKeys (<= 4096)` of `u64`, `varu32 kept` |
| 11 | BaselineAck | both | `varu32 session`, `varu32 id`, `u8 phase` (0 host acks summary chunks, 1 client acks diff chunks; > 1 rejected), `varu32 next` (first missing chunk index) |
| 12 | Rebase | H | `varu32 session`, `varu32 epoch` |
| 13 | SyncDigest | C | `varu32 session`, `varu32 epoch`, `varu seq`, `varu32 digestId`, `count shards` (must equal 128) of `u32` |
| 14 | ShardRequest | H | `varu32 session`, `varu32 digestId`, `u64 maskLo`, `u64 maskHi` (bit i = shard i) |
| 15 | ShardEntries | C | `varu32 session`, `varu32 digestId`, `boolean truncated`, `count entries (<= 1000)` + Effects |
| 16 | SyncRepair | H | `varu32 session`, `varu32 epoch`, `varu32 digestId`, `count effects (<= 1024)` + Effects, `count revertKeys (<= 4096)` of `u64` |
| 17 | DigestRequest | H | `varu32 session`, `varu32 epoch` |

Hello's HandshakeInfo: `varu32 protocol` (<= 0xFFFF), `varu32 buildNumber`, `u64 buildHash`, `u64 rulesHash`, `u64 volatileHash`,
`str buildName` (<= 64), `count plugins (<= 1024)` of `str` (<= 255). Zero/empty fields mean "not set" and are not checked.

IntentKind: 0 AppliedEffects (client already applied; host records), 1 ActivateRef, 2 TriggerEnter, 3 PickUp, 4 ScenePass,
5 ReportCell. Kinds 1..5 are **requests**: the guest did not execute anything, the host runs the real action and commits the
resulting effects. Intent `ref` is the target form (or quest for ScenePass, cell for ReportCell).

Reject reasons: 1 full, 2 content (plugin hash differs while wantBaseline), 3 no baseline, 4 protocol, 5 build, 6 rules,
7 volatile list, 8 mod list. `text` is always filled so a client that does not know a reason id can still show it.

Limits summary: `kMaxEffectsPerTxn` 1024, `kMaxTxnsPerBatch` 256, `kMaxStringLen` 255, `kMaxMessageBytes` 8 MiB, `kMaxAppPayload`
64 KiB, `kMaxShardEntries` 1000, `kSyncShards` 128, baseline chunk sizes `kBaselineSummaryPerChunk` 256 /
`kBaselineEffectsPerChunk` 128 / `kBaselineRevertsPerChunk` 512 (senders); table 4096 strings.
Receivers enforce the caps above, which are looser than the senders' chunk sizes, so chunk sizes can be tuned without a version bump.

## 6. Effect tags

Tag = variant index. Field list after the tag (types per 3.3, FormRef per section 4, `to` = Recipient):

| Tag | Effect | Fields |
|---|---|---|
| 0 | SetStage | quest, varu32 stage |
| 1 | SetObjective | quest, varu32 objective, u8 flags (bit0 displayed, bit1 completed; merge = OR) |
| 2 | SetGlobal | global, f32 value |
| 3 | EnableRef | ref |
| 4 | DisableRef | ref |
| 5 | AddItem | item, vari32 count, to |
| 6 | RemoveItem | item, vari32 count, to |
| 7 | ModActorValue | varu32 av (0x10000 = experience), vari32 delta, to |
| 8 | ModReputation | faction, vari32 fame, vari32 infamy, to |
| 9 | ModKarma | vari32 delta, to |
| 10 | StartQuest | quest |
| 11 | StopQuest | quest |
| 12 | MoveRef | ref, cell, f32 x, f32 y, f32 z, boolean partyWarp |
| 13 | KillActor | actor |
| 14 | ResurrectActor | actor |
| 15 | SetScriptVar | owner, varu32 index (<= 0xFFFF), f64 value |
| 16 | SetPlayerControls | varu32 controls, boolean enable, to |
| 17 | PartyTether | cell, boolean on |
| 18 | SlideshowEnd | (none) |
| 19 | SaveSuppress | boolean on |
| 20 | CompanionOwner | npc, u8 owner (< 8) |
| 21 | ForcedDialogue | npc, topic, u8 initiator (< 8) |
| 22 | BarrierArrive | barrier, u8 required (slot mask) |
| 23 | BarrierRelease | barrier |
| 24 | SayOnceFlag | info |
| 25 | CompleteQuest | quest |
| 26 | CompleteAllObjectives | quest |
| 27 | SetLock | ref, boolean locked, vari32 level |
| 28 | SetFactionRank | actor, faction, vari32 rank (< 0 = removed) |
| 29 | SetPerk | perk, vari32 rank (0 = removed), to |
| 30 | SwapTexture | ref, texture, u8 slot |
| 31 | SetFastTravelLock | scope, boolean on |
| 32 | FailQuest | quest (sticky; implies completed: shadow state sets both questFailed and questCompleted) |

Integer deltas on purpose (XP, caps, karma, rep): replicas stay bit-identical. Floats appear only for globals, positions and
script variables, and must be finite. `ForcedDialogue` is an event (not state); replays carry `catchUp` so sinks can tell.

## 7. Session behaviour

### 7.1 Ordering and delivery

* The host assigns a dense, monotonically increasing `seq` to each committed EffectTxn (`StoryLog`). Clients apply strictly in
  seq order, buffer gaps, ignore duplicates, and send `Ack{upTo}` (cumulative).
* The host retransmits unacked txns with exponential backoff, independent of live commits (a live send never refreshes the
  retransmit timer; otherwise steady commits starve retransmits). A client that is far behind (> 32 txns) gets no live sends,
  only ack-clocked catch-up: each Ack that advances immediately triggers the next batch.
* Client **intents** are durable and exactly-once: numbered `id` within an `epoch`; the host executes each id once, in order,
  acknowledges with `IntentAck`, and the client resends unacked intents on a timer and after reconnect (`Welcome.lastIntent`
  tells it where the host stopped). The `epoch` changes when the client's local world is replaced (load/rejoin) so old ids are
  not confused with new ones.
* `worldEpoch` identifies a host world lineage. It changes when the host loads an older save. Txns/acks from another epoch are
  stale and dropped.

### 7.2 Join

1. Client sends Hello (new `session`). Host validates: protocol, build, rules hash, volatile hash, mod list, free slot, and
   plugin `contentHash` when wantBaseline. Failure -> Reject (reason + text), nothing else is sent.
2. Host sends Welcome. `hasSnapshot` = the client must replace its shadow with the snapshot (fresh join / far behind /
   log compacted past `lastSeq`); otherwise the client simply resumes from `lastSeq` and receives TxnBatch catch-up.
3. If `baselineId != 0` (the client's world came from its own save): summary phase, then diff phase (7.3).

### 7.3 World baseline transfer (join with wantBaseline)

* Phase 0: client sends `BaselineSummaryChunk` (index/total) listing `(key, valueHash)` for every tracked world entry; the host
  replies `BaselineAck{phase 0, next}`; the client resends from `next`.
* Phase 1: host computes the diff versus its authoritative state and streams `BaselineDiffChunk`s (effects to force-set plus
  `revertKeys` the client has but the host does not); the client applies them with force semantics and replies
  `BaselineAck{phase 1, next}`.
* Hashes canonicalize script-variable and global values to float32 then round to 1e-4 so benign float noise between machines
  does not produce spurious diffs. Both ends must use the same canonicalization (it is part of the protocol).
* Chunks are idempotent by index; a restarted client uses a new `id`.

### 7.4 Rebase

When the host loads an older save (or a client reconnects holding a different epoch) it sends `Rebase{epoch}`. The client
re-dumps its world and rejoins with a baseline under a new session. Sticky one-way flags (e.g. CompleteQuest) cannot be un-set
by a rewind; this is a documented limitation.

### 7.5 Desync detection and repair

The client periodically (and on `DigestRequest`, after barriers/scenes) sends `SyncDigest`: 128 shard hashes (8 entry classes x 16
key buckets) over its REAL engine world at an applied `seq`. The host compares against the same seq; for mismatching shards it
sends `ShardRequest` (128-bit mask), the client answers `ShardEntries` (<= 1000 per message, `truncated` set if it hit the cap),
and the host sends `SyncRepair`: force-set effects and revert keys, skipping keys touched since the digest. Repairs are only
applied if the `epoch` matches.

### 7.6 AppData

Opaque, best effort, for adapters that want to ride the story channel (dialogue uses its own mux channel instead). `from` is the
sender slot as claimed by the sender; the host overwrites it for relayed traffic.

## 8. Versioning rules

1. `kWireVersion` is in every normal envelope; a receiver drops other versions (`BadVersion`). Bump it on **any** incompatible
   byte-level change: new/reordered/removed Effect tags or Message ids, changed field order or types, changed caps that a
   conforming sender could exceed, changed CRC/string-table rules.
2. `kProtocolVersion` is in Hello's HandshakeInfo and is checked by the host (Reject 4 with a readable text). Bump it together with
   kWireVersion, and also when behaviour changes without bytes changing (merge rules, baseline canonicalization, retransmit
   semantics a peer relies on).
3. **Hello and Reject must stay decodable by every past and future build**: version byte 0xA1, fields only appended, receivers
   ignore trailing bytes, type ids 1 and 3 never reused.
4. To add an effect type without breaking old builds there is no negotiation: append at the end of the variant (new tag), bump both
   versions. Old builds then Reject the new build with a clear message instead of misparsing.
5. Hash inputs that gate play (rules.json text hash, volatile-key list content hash, plugin list) are compared at join; changing
   `rules.json` therefore forces everyone to the same copy by design (Reject 6/7/8).
6. Every change must come with: updated tests (round trip + fuzz corpus), a mutation twin where applicable, and an update to this
   file. Receivers must treat all input as hostile: bounds-check counts, reject non-finite floats, never trust `slot`/`from`.

## 9. Test and tooling pointers

* `synccore\run_tests.bat [x64]` builds and runs ~45 ctest entries (wire round trip, truncation/bit-flip fuzz, session scenarios
  under loss/dup/corruption, mutation twins, ENet loopback).
* `build\L4\fnvcoop_soak.exe --seconds 3600 --players 4 --seed N [--threaded]` runs the Goodsprings traffic-shape soak
  (see `internal lane notes`).
* Diagnostics: `HostSession::explainJoinEntry`, `ClientSession::explainSummaryEntry` name the world entry behind a baseline or
  digest mismatch.

## 10. Dialogue channel (first byte 0xD1)

Owner: L8 (`plugin/dialogue/include/fnvcoop_dlg/`, header-only); L4 supplies the transport, the mux (section 2) and the session
rules below. Dialogue packets ride the same connection as the story envelope and are demultiplexed on the first byte.

**Delivery requirement:** reliable and ordered per peer pair (ENet channel 0). Duplicates are tolerated (the host is idempotent per
`(conv, step)`); loss and reordering are not. This is why dialogue does not use the story session's own retransmit machinery.

### 10.1 Envelope

```
0xD1  ver(=1)  type  body  crc32 LE (over everything before it)
```

Different from the story envelope: no magic1, no string table (strings and FormRef plugin names are inline), the version byte is the
dialogue protocol version (`kVersion` in `messages.h`, currently 1), and a decoder rejects trailing bytes. Varints and zigzag are as in
3.3. Limits: string <= 1024 bytes, lines <= 32, topics <= 64, facts <= 128. `FormRef` = `str plugin`, `varu32 localId`.

### 10.2 Messages (type id = index + 1)

| Id | Name | Dir | Body |
|---|---|---|---|
| 1 | StartReq | C->H | `varu reqId`, FormRef npc |
| 2 | Offer | both | `varu conv`, `varu step`, `count topics` of (FormRef topic, `u8 check`, `u8 flags`). C->H: the initiator's list. H->C: the relayed copy for spectators |
| 3 | Pick | C->H | `varu conv`, `varu step`, FormRef topic, FormRef info, `vari choice`, `u8 goodbye`, `count lines` of (`str text`, `str voice`, `u8 emotion`, `u8 emotionValue`), `count facts` of (`u8 kind`, FormRef what, `vari value`) |
| 4 | LineDone | C->H | `varu conv`, `varu step` |
| 5 | EndReq | C->H | `varu conv`, `u8 reason` (CloseReason 1..8) |
| 6 | Heartbeat | C->H | `varu conv` |
| 7 | StartDenied | H->C | `varu reqId`, `u8 reason` (1 Busy, 2 AlreadyTalking, 3 NpcDead, 4 NpcInCombat, 5 Unknown, 6 TooMany), `u8 owner` slot or 0xFF |
| 8 | Opened | H->C | `varu conv`, `varu reqId`, FormRef npc, `u8 initiator` |
| 9 | Lines | H->C | `varu conv`, `varu step`, FormRef topic, FormRef info, `u8 goodbye`, `count lines` as in Pick |
| 10 | Result | H->C | `varu conv`, `varu step`, `u8 phase` (0 Begin, 1 End), `varu txnSeq` |
| 11 | Control | H->C | `varu conv`, `u8 lockMask`, `u8 locked` |
| 12 | Closed | H->C | `varu conv`, `u8 reason` (1 Goodbye, 2 InitiatorLeft, 3 Disconnect, 4 NpcDead, 5 Combat, 6 Timeout, 7 HostShutdown, 8 ProtocolError) |

`conv` ids are assigned by the host (never 0). `step` counts the initiator's picks within a conversation, from 1.

### 10.3 Rules

* **Initiator lock.** The host grants at most one conversation per NPC and per player. A second StartReq gets StartDenied (Busy with the
  holder's slot, or AlreadyTalking). A retransmitted StartReq (same `reqId`, same npc) re-sends Opened. Only the initiator's messages for
  a conv are accepted (anything else counts as `rejected`). The host never evaluates conditions: the initiator resolves topic/INFO
  selection and reports the `facts` it observed; the host validates ordering.
* **Line broadcast.** On Pick the host runs the Begin result script, sends `Lines` to every connected slot except the initiator, and
  `Result{Begin}` to everyone. `Offer` (the next topic list) is relayed to spectators the same way.
* **Choice and steps.** Pick must carry `step == current + 1` while the conversation awaits a pick; a duplicate Pick (same step) re-sends the
  Result and never re-runs the script. LineDone runs the End script exactly once, sends `Result{End}`, and closes the conversation with
  `Goodbye` when the Pick had the goodbye flag.
* **Result-txn link.** `Result.txnSeq` is the story-log seq of the EffectTxn the result script committed (0 = none). That txn travels on the
  story channel, so it can arrive after the Result. The initiator must not build the next topic list (its conditions read quest state)
  until its story session has applied `max(Begin.txnSeq, End.txnSeq)`. `ClientDialogue::setAppliedProvider` /
  `DialogueNode::Config::appliedSeq` supply the applied seq; the gate falls back to `gateTimeoutMs` (default 1500 ms from LineDone) and the
  TopicsReady event then has `timedOut` set.
* **Initiator disconnect.** When the transport reports the initiator gone the plugin calls `DialogueNode::playerLeft(slot)` (alongside
  `HostSession::peerDisconnected`). The host closes every conversation of that slot with `Closed{Disconnect}`: a begun line's End script
  still runs once (quest state is never half-applied), the NPC lock is freed, the control-lock mask is released (`Control{locked=false}`
  to the initiator while it is connected) and all remaining slots, including the host's own client side, get `Closed` so their menus and
  overlays close. Silence for `leaseMs` (default 300 s; Heartbeat or any initiator message refreshes it) closes with `Timeout`; NPC
  death/combat close with `NpcDead`/`Combat`.
* **Late join.** `DialogueNode::playerJoined(slot)` sends the joiner `Opened` for each running conversation, then the latest `Lines` and,
  while the initiator is choosing, the latest `Offer`. Call it when the story session reports the slot joined.
* **Rejoin.** A returning initiator never received the Closed. When its story session (re)joins it must call `DialogueNode::resync()`, which
  clears the stale Initiator/Spectator role and emits `CloseMenu` (`wasInitiator` set if the real menu is open) so the engine glue
  closes the menu and re-enables player controls.

## 11. Rejoin and late-join rules (story channel)

Pinned by the integrated tests `synccore/tests/test_rejoin*.cpp`:

* A plain reconnect (same process, `ClientSession::connect` with a larger `session`) resumes from `lastSeq`; txns it missed are replayed
  once, never twice. If the host world epoch changed meanwhile (the host loaded a save) the host sends `Rebase` and the client re-baselines
  via its baseline provider; txns of the old epoch are fenced off by `worldEpoch` and never replayed into the new epoch.
* A baseline join (restart with the client's own save, `Hello.flags` bit0) receives the shared world as a diff plus the per-slot ledger
  (items/XP earned so far) as live effects. A restarted client whose save already contains those rewards receives them again: per-player
  rewards are not part of the world diff. Known limitation; clients that can tell should treat ledger effects delivered with `fromSnapshot`
  as "grant if missing".
* Barrier: `BarrierArrive` merges `required` (the slots joined at that arrival) and `arrived`; release happens once. A player who joins
  while a barrier is open must pass it too, or leave: leavers (`peerDisconnected`) stop counting immediately and `barrierTimeoutMs` is the
  last resort. Late joiners receive the barrier state through the normal snapshot/baseline.

## 12. Guest-originated interactions and the acting-player context

Problem: script blocks that react to "the player" (`OnActivate` + `IsActionRef player`, `OnTrigger/OnTriggerEnter player`, `OnAdd player`,
`OnHit` by player, pickups, and every `GetDistance player` / `GetItemCount player` / `GetAV player` inside them) are bound to the host's
player ref (0x14). A guest's avatar is only a ghost on the host (NV:MP), so those blocks never fire for a guest and, when they do fire for the
host, they read the host's inventory and position. Quest and object scripts run only on the host (guests are gated), so the host must run
the block on the guest's behalf. One design for all kinds:

**Guest side (detect, never apply).**
* ActivateRef / PickUp: the activation observer (`nvhooks::setActivateObserver`, ini `forward_activate=1`) runs on the guest. The local UI half
  (container menu, door animation) proceeds; the script half is already neutered by the guest gate. The runtime sends
  `Intent{kind=ActivateRef|PickUp, ref=target}` (protocol 5 above; durable, in order, exactly once, refused while `syncing`).
* TriggerEnter: sent for trigger volumes the host cannot observe itself (the guest is in a cell the host has not loaded, or the ghost is not
  yet there). Hook point = the guest's event dispatch (L5, `MarkScriptEvent` region): when the engine would fire a player-filtered block of a
  trigger/volume for the local player, send `Intent{TriggerEnter, ref=trigger ref}` instead. Hit / OnAdd are covered by Activate/PickUp and by the
  host seeing the ghost; they get their own kinds only if a gap list shows blocks that neither path reaches.
* Nothing is applied locally from the intent; effects come back as a normal host txn (origin = the guest slot).

**Host side (run the block as the guest).**
1. `HostSession::onAction(slot, kind, ref)` calls the runtime's `ActionExecutor`. The runtime sets `actingSlot() = slot` and calls the
   *acting hook* `(slot, true)`; afterwards `(slot, false)`. Inside that window the engine layer must:
   * open a `CaptureHub` scope (`Cause{Intent, source=ref, aux=kind}`); the runtime forces the txn origin to `slot`, so player-targeted effects
     (`AddItem`, `ModActorValue`, karma, reputation) are routed by the normal recipient rules (origin / rewards-to-all per policy/rules.json);
   * resolve the actor of the interaction and every `player` reference read by the block to the guest: `IsActionRef player`, `GetDistance
     player`, `GetItemCount player`, `GetActorValue player`, `GetInSameCell player`, `player.<cmd>`.
2. The *acting player* is the guest's ghost refID (`Runtime` `ghostOf(slot)`, provided by L6). The engine layer (L5) implements the resolution
   at ONE choke point: while an acting scope is alive, the script engine's resolution of form 0x14 (the `player` SCRO/SCRV reference and the
   `Condition::Evaluate` "run on player" subject) returns the acting ghost; the event-filter comparison "actor == player" treats the ghost as the
   player. Outside an acting scope nothing changes (host gameplay is untouched).
3. The block is started by re-issuing the interaction as the guest: `<target>.Activate <ghostRef> 1` (console path through
   `nvhooks::execConsoleLineOn`, calling reference = target). Pickups are `Activate` of the item ref; trigger enters are dispatched through the
   same event path once L5 provides a safe entry (`TriggerEnter` is logged as unsupported until then).
4. Stand-in (what is implemented now): when no ghost refID is known, the executor runs the interaction as the host's own player (0x14) with the
   same capture scope and origin attribution. `IsActionRef player` passes and the stage/var effects are correct and shared; the limitation is
   that the host's player really receives the item and `GetDistance player` measures the host. It is fully replaced by step 2 once the ghost
   refID exists.

**Invariants / tests.** (a) the executor window is the only time `actingSlot()` is set (tested: `testActions`, acting hook on/off pair, seen
inside); (b) txn origin == guest slot and rewards reach the requester only for origin-routed causes (tested); (c) a rejected / unknown target
leaves no scope open and logs `coop.guestAction issued:false`; (d) the same interaction submitted twice is executed twice (intent ids are
exactly-once per submission, not per target - the script decides whether it is repeatable).

**Coordinator decisions recorded here.** Message-box transitions (Fort -> Dam) become a party barrier (`Barrier` rule + `PartyWarp`), then every
player is warped; shared player storage containers (`NVCCPlayerStorageREF`, companion containers) become per-player (host keeps one container
per slot, intent target resolved by slot).

**Open items.** L5: acting-player resolution at the script engine choke point + the guest dispatch hook for `TriggerEnter`; L6: ghost refID per
slot (`Runtime::setGhostResolver`); L4: rules for per-player containers; L10: per-slot container redirection in the executor.
