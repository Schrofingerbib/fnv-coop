// What the world-time session needs from the game, as an interface so the whole thing runs against a fake in tests. The production
// implementation (wt_glue.cpp) uses the TESGlobal values for the clock and the pending-travel pins of plugin/bots/bots_addrs.h; every
// engine address behind it is pinned or marked TODO-RE there.
#pragma once
#include <string>
#include <vector>

#include "wt_types.h"

namespace nvwt {

class IWorldTimeEngine {
public:
    virtual ~IWorldTimeEngine() = default;
    // The five clock globals. False when they cannot be read / written (no game loaded yet, forms missing).
    virtual bool readClock(GameTime* out) = 0;
    virtual bool writeClock(const GameTime& t) = 0;
    virtual float timeScale() = 0;
    // A short message in the HUD ("Not while someone is in a fight.").
    virtual void notify(const std::string& text) = 0;
    // Queue a genuine fast travel to `marker` on THIS machine (the way the map menu does it). False when the marker is unknown here.
    virtual bool startTravel(const FormRef& marker) = 0;
};

}  // namespace nvwt
