// nvcoop.ini [areadigest] for the area desync module. Game-free; the module reads real ini and environment through an injected getter.
//
//   [areadigest]
//   enabled=0          the module does nothing until this is 1 (OFF by default: nothing here has run in a game)
//   intervalMs=2000    digest cadence of every machine                      (clamped 500 .. 30000)
//   settleMs=1000      an item must be quiet this long on both sides before it is healed (200 .. 10000)
//   smoothMetres=2     an actor more than this far off is moved smoothly      (0.5 .. 50)
//   snapMetres=10      more than this far off, or in another cell: snapped    (smooth + 0.5 .. 500)
//   hpTolerance=5      health differences up to this are not healed           (0.5 .. 100)
//   healBudgetPerMin=240   most keys the host corrects per minute             (0 .. 5000; 0 = detect and report only)
//   maxActors=400      most actors one digest covers                          (20 .. 2000)
//   story=1            include the story digest (asks the existing story repair)
//   containers=1 objects=1   include those categories (shadow level, see docs/tools/area-desync.md)
//   actors=1           include the actors (needs [live] actors=1 to have anything to read)
// Environment override: NVCOOP_AREADIGEST_<KEY> (upper case) beats the ini.
#pragma once
#include <functional>
#include <string>
#include "areadigest/areadigest.h"

namespace nvcoop::areadigest {

// section, key, default -> value ("" allowed). Production: environment first, then GetPrivateProfileString.
using IniGetter = std::function<std::string(const char* section, const char* key, const char* def)>;

struct AreaSettings {
    bool enabled = false;
    fnvcoop::areadigest::Config cfg;       // self / isHost are set by the runtime per session
    size_t maxActors = 400;
    bool actors = true, containers = true, objects = true, story = true;
    std::string warnings;                  // one line per value that was out of range and clamped
};

bool parseBool(const std::string& v);                 // 1 true on yes (any case); everything else, "" included, is off
AreaSettings readSettings(const IniGetter& get);
std::string describe(const AreaSettings& s);           // "enabled=1 interval=2000 settle=1000 smooth=2m snap=10m ..."

}  // namespace nvcoop::areadigest
