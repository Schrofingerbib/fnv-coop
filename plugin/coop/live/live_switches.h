// nvcoop.ini switches of the live layer (enemy / NPC / avatar sync). Game-free and unit-tested; the module reads real ini and environment
// through an injected getter.
//
//   [live]
//   actors=0      NPC ownership + the real engine port (plugin/actors, actors/engine, actors/anim)
//   avatars=0     player puppets + the avatar snapshot channel (plugin/avatar, avatar/engine)
//   combat=0      hit / death / projectile glue (plugin/combat)           needs actors=1 (hits resolve through the victim's owner)
//   spawns=0      spawn-site arbitration and net ids (plugin/spawn)       needs actors=1 (a spawned actor is an owned actor)
//   social=0      companions, crime, kill rewards (plugin/companions)     needs actors=1 (companions are owned actors). The module itself is switched by
//                 [companions] enabled=1 and its frame is driven by coop_module.cpp (the one call site); this switch gives it the avatar layer's puppet
//                 map and a line in the live status. It never starts the module.
//
// Every switch is OFF by default: nothing of the live layer runs in a game until it has been proven there. Environment override for
// automated runs: NVCOOP_LIVE_ACTORS / _AVATARS / _COMBAT / _SPAWNS / _SOCIAL (1 or 0) beats the ini.
//   [actors]      tuning of the real actor port (scanMs, maxScanActors, ...): see live_actors_cfg.h
#pragma once
#include <functional>
#include <string>

namespace nvcoop::livewire {

// section, key, default -> value ("" allowed). Production: environment first, then GetPrivateProfileString.
using IniGetter = std::function<std::string(const char* section, const char* key, const char* def)>;

struct LiveSwitches {
    bool actors = false;
    bool avatars = false;
    bool combat = false;
    bool spawns = false;
    bool social = false;
    // Switches as requested vs as they will run: a dependency that is missing turns the dependent switch off, with a reason.
    std::string warnings;   // one line per forced-off switch, "" when none
    bool any() const { return actors || avatars || combat || spawns || social; }
};

// "1", "true", "on", "yes" (any case, surrounding blanks ignored) are on; everything else, including "", is off.
bool parseSwitchValue(const std::string& v);

// Reads [live] through the getter, then applies the dependency rules (combat / spawns need actors).
LiveSwitches readLiveSwitches(const IniGetter& get);
// The dependency rules alone (what the tests pin).
LiveSwitches resolveSwitches(LiveSwitches requested);

// Compact text for logs and coop.live status: "actors=1 avatars=0 combat=1 spawns=0" (+ " social=1" only when it is on, so a tool that matches the four-lane line keeps working).
std::string describeSwitches(const LiveSwitches& s);

}  // namespace nvcoop::livewire
