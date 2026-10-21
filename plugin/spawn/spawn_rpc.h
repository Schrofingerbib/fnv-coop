// coop.spawn: what this machine believes about spawn sites (FULL-SYNC-PLAN 3.3). Game-free; the module calls spawnRpc() with the running SpawnSync.
//   {}                      -> {"self":0,"host":true,"link":true,"sites":12,"pending":0,"waiting":0,"candidates":0,"digest":"00ab...","stats":{...},"tracker":{...},"registry":{...}}
//   {"list":true,"max":50}  -> {"sites":[{"site":..,"state":"Spawned","owner":1,"epoch":3,"gen":2,"mine":false,"interior":false,"members":2,"dead":0}, ...],"total":12}
//   {"site":"0x1234abcd"}   -> one site (decimal also accepted): {"known":true,...} / {"known":false,"site":...}
//   no sync                 -> {"unsupported":true,"reason":"..."}
// The proof script (plugin/spawn/tools/spawn_proof.py) asserts on these numbers. Once things settle, every machine answers the same
// (state, owner, epoch, gen, members) for a site, and `digest` is the same everywhere.
#pragma once
#include <string>
#include "spawn/spawn_sync.h"

namespace nvcoop::spawn {

std::string siteJson(const SiteInfo& s);
std::string statsJson(const Stats& s);
// Returns false (with a message in `out`) only for bad parameters; "not running" is a normal answer.
bool spawnRpc(const SpawnSync* sync, const std::string& paramsJson, std::string& out);

}  // namespace nvcoop::spawn
