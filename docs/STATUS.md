# Status

Developer preview. Every claim here comes from an automated test; nobody tests by hand. Tests drive a real
game only through a JSON-RPC bridge inside the plugin, never with simulated keyboard or mouse input, and use
isolated copies of the game with their own INIs and saves.

## Proven

Game-free suites (`python -I tools/test_all.py`, 14 suites):

| Suite | What it proves |
|---|---|
| canonical nvcoop.dll build | the plugin builds from the tree |
| questdata | ESM parser counts, effects oracle, risk tiers (needs your own game data) |
| synccore | wire format, merge rules, echo guard, epochs and rebase, desync digests, rules engine, rejoin and late join, dialogue channel; simulator with 2 to 4 clients on a lossy link; seeded chaos fuzz; 1-hour 4-player soak |
| hooks | hook prologue bytes, capture, suppression policy, apply paths |
| nvmp | NV:MP 9.4 signatures unique, client patcher, server hook, compat tool (needs your own NV:MP files) |
| snapshot | schema, diff, volatile-value filter, sweep logic |
| dialogue | initiator and spectator state machines, adapter, engine glue compiles |
| coverage | quest graph, critical paths, policy and rule consistency |
| coop runtime | host and client session checks and integration tests |
| runner | bridge client, instance provisioning, in-game gate logic |

Invariants checked in the simulator and in game: no quest script runs on a non-host, no duplicate rewards, no
ghost duplicate NPCs, barriers release exactly once, shared host state equals shared client state.

Proven in the real game:

- **Multiple instances on one PC.** Several copies of the game run side by side, each in its own hardlinked
  folder, by working around the single-instance checks inside our own process.
- **Capture and apply of a hook batch.** The host runs a stage script and the hooks record the effects as one
  transaction. A guest applies it with local quest scripts blocked: the guest re-ran 0 scripts, the effects match,
  and the guest snapshot equals the host snapshot. Kill, resurrect and move were captured exactly and applied
  correctly.
- A real game paired with a simulated partner: host stage to client over ENet, baseline join of 26 effects,
  save/load rebase, barrier release, version handshake rejection.

## Plan

Goodsprings end to end with two game copies first, then the same sweep and two-game tests questline by
questline: prologue, Legion, Yes Man, NCR, House, then side quests and DLC. Coverage must not regress.
