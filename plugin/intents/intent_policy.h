// Rules of use, game-free: what a guest forwards and how the local engine call goes on (guest side), who acts and how far they may be (host
// side), and the [intents] ini reader.
#pragma once
#include <functional>

#include "intents/intent_engine.h"
#include "intents/intent_types.h"

namespace nvcoop::intents {

// ---- guest side ----------------------------------------------------------------------------------------------------------------------
struct GuestDecision {
    bool forward = false;         // send an intent
    Kind kind = Kind::Activate;
    bool suppressLocal = false;   // the engine call is skipped (the host decides); false = it goes on (menus and animations stay local)
    bool viaDialogue = false;     // DialogueStart: ask the 0xD1 dialogue adapter instead of the intents channel
    const char* why = "";         // for status / tests
};

// The local player activated `target` (nvhooks::setActivateObserver).
GuestDecision decideActivate(const Config& cfg, const TargetInfo& target);
// The engine is about to raise script event `mask` on `target` for the local player (nvhooks::setEventObserver).
GuestDecision decideEvent(const Config& cfg, uint32_t mask, const TargetInfo& target);

// ---- host side -----------------------------------------------------------------------------------------------------------------------
float reachOf(const Config& cfg, Kind k);                       // game units; <= 0 = unlimited
bool kindEnabled(const Config& cfg, Kind k);
// May the host's own player (refID 0x14) stand in for a guest that has no puppet, for this kind on this class of target?
bool standInAllowed(const Config& cfg, Kind k, TargetClass cls);

// ---- ini -----------------------------------------------------------------------------------------------------------------------------
// section [intents]; key lookup is case-insensitive. Values: 1/true/on/yes are on, 0/false/off/no are off; anything else is an error.
using IniGetter = std::function<std::string(const char* key, const char* def)>;
bool readConfig(const IniGetter& get, Config* out, std::string* err);
// Convenience for tests and tools: parse an ini text, then readConfig over its [intents] section.
bool parseConfig(const std::string& iniText, Config* out, std::string* err);

}  // namespace nvcoop::intents
