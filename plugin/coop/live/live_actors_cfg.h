// nvcoop.ini [actors] -> actors::engine::Config (the tuning of the real actor port). Game-free, unit-tested. Every value is clamped to a range
// that cannot hurt the frame (a typo such as scanMs=0 must not make the port enumerate every actor every frame).
//
//   [actors]
//   engine=1               false: the port is not installed (the no-op port stays)
//   scanMs=500             how often loaded actors are enumerated                       50 .. 10000
//   maxScanActors=400      upper bound of tracked actors                                16 .. 2000
//   maxLinesPerFrame=24    console lines the port may run per frame                     1 .. 200
//   driveMinMs=33          at most one pose application per actor per this long         0 .. 1000
//   posEpsilonXY=0.25      game units                                                   0 .. 100
//   posEpsilonZ=0.75       game units                                                   0 .. 100
//   yawEpsilonDeg=1.0      degrees                                                      0 .. 90
//   pollDeathMs=100        how often tracked actors are checked for an engine death     16 .. 5000
//   deathGraceMs=1500      a locally dead copy waits this long for the owner's word     0 .. 30000
//   restrainNonOwned=1     AI off for actors this machine does not own
//   stopCombatOnRestrain=1
//   startCombatOnTakeover=1
//   persistLedger=1        write the list of actors this machine restrained
//   maxStandIns=64         0 .. 400
//   swapLeveled=1          swap the body to the owner's leveled pick (unverified in game)
//   saveGraceMs=3000       0 .. 60000
#pragma once
#include <string>

#include "actors/engine/actor_engine_api.h"
#include "live_switches.h"

namespace nvcoop::livewire {

// Starts from `base` (the port's defaults) and overrides what the ini sets. `notes` (optional) receives one line per value that was clamped
// or could not be parsed.
nvcoop::actors::engine::Config readActorsConfig(const IniGetter& get, nvcoop::actors::engine::Config base = {}, std::string* notes = nullptr);

}  // namespace nvcoop::livewire
