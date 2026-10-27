// Game clock authority (host) and follower (client). Game-free.
//
// The host's clock is the only clock. The host sends a Beacon (its five time globals plus the measured rate) every few seconds and at once
// after a jump (script `set GameHour`, a host load, a granted wait / sleep). A client extrapolates the beacon, compares it with its own
// clock and, when they disagree by more than a tolerance, orders a snap (copy the host's globals). Small drift is left alone so the sky does
// not stutter; the clock of a client that sat in a menu (game time pauses in menus) is pulled forward on its next tick.
//
// Why drift is measured as "hour-of-day difference plus a whole number of days": whether GameDaysPassed is continuous or counts whole days
// is not proven in game, so the day count only picks the integer number of days and the fine part comes from GameHour.
//
// Mutation twins 101..109 (FNV_MUT) live in wt_clock.cpp.
#pragma once
#include <cstdint>
#include <optional>

#include "wt_types.h"

namespace nvwt {

// ---- calendar -------------------------------------------------------------------------------------------------------------------
int daysInMonth(int month);   // non-leap table (assumption; the host's beacon is authoritative, so a wrong table only costs a brief mismatch)
bool validTime(const GameTime& t);
GameTime advanceBy(const GameTime& t, double minutes);          // forward or backward; daysPassed moves by minutes / 1440
double deltaMinutes(const GameTime& a, const GameTime& b);      // game minutes from b to a (negative when a is earlier), see header comment

// ---- wire-level beacon ----------------------------------------------------------------------------------------------------------
struct Beacon {
    uint32_t seq = 0;
    GameTime time;
    float rate = 0;        // measured game minutes per real SECOND since the previous beacon (0 while the host's clock was paused)
    float timeScale = 0;   // nominal TimeScale global at the moment of the beacon
    uint32_t skipEpoch = 0;  // number of granted wait / sleep skips so far
    bool jump = false;     // the host's clock was set (load, script, skip): followers snap without tolerance
    bool partyTravel = false;  // the host runs fast travel as a party decision ([worldtime] travel=party): clients must ask before they go
    bool operator==(const Beacon& o) const {
        return seq == o.seq && time == o.time && rate == o.rate && timeScale == o.timeScale && skipEpoch == o.skipEpoch && jump == o.jump &&
               partyTravel == o.partyTravel;
    }
};

// ---- host -----------------------------------------------------------------------------------------------------------------------
class ClockAuthority {
public:
    struct Config {
        uint32_t intervalMs = 5000;       // periodic beacon
        double jumpMinutes = 4.0;         // clock deviates from its own extrapolation by more than this => jump
        uint32_t minGapMs = 200;          // never two beacons closer than this (except a forced one)
        // Owner decision D7: a scripted time skip happens once per PARTY event (the Canyon door's +3 days, Honest Hearts' +14 days), not once per player
        // who triggers the script. A forward leap of the host clock that no granted wait / sleep explains, of at least onceMinMinutes, that has the
        // size of one already seen onceWindowMs ago (within onceToleranceMinutes) is the same event again: the host takes it back.
        bool onceEnabled = true;
        double onceMinMinutes = 720.0;    // 12 game hours; shorter leaps (a script that sets the hour) are not "events"
        uint32_t onceWindowMs = 120000;   // the party-consent timeout: everybody who walks through the door in that time is the same event
        double onceToleranceMinutes = 60.0;
    };
    explicit ClockAuthority(Config c = Config()) : cfg_(c) {}

    // Feed the host's current clock (every frame is fine). Returns a beacon when one is due.
    std::optional<Beacon> tick(uint64_t nowMs, const GameTime& cur, float timeScale);
    // A client joined or asked for one: the next tick sends a beacon at once.
    void forceBeacon() { force_ = true; }
    // The host performed a granted wait / sleep and set its clock to `after`: bumps the epoch and returns the beacon to send now.
    Beacon noteSkip(uint64_t nowMs, const GameTime& after, float timeScale);
    // The host loaded a save (clock is arbitrary now): next tick sends a jump beacon and the rate estimate restarts.
    void noteLoaded() { haveLast_ = false; force_ = true; forceJump_ = true; }

    // The last tick took a repeated scripted skip back: write `*to` into the game clock (no beacon is due, clients never saw the leap).
    bool takeUndo(GameTime* to) {
        if (!undoPending_) return false;
        undoPending_ = false;
        if (to) *to = undoTo_;
        return true;
    }
    uint32_t repeatsUndone() const { return repeatsUndone_; }
    void setPartyTravel(bool on) { partyTravel_ = on; }
    uint32_t skipEpoch() const { return epoch_; }
    uint32_t beaconsSent() const { return seq_; }

private:
    Beacon make(uint64_t nowMs, const GameTime& cur, float timeScale, bool jump);
    Config cfg_;
    bool haveLast_ = false, force_ = false, forceJump_ = false;
    GameTime last_;
    uint64_t lastMs_ = 0, lastBeaconMs_ = 0;
    GameTime lastBeaconTime_;
    bool haveBeacon_ = false;
    bool partyTravel_ = false;
    uint32_t seq_ = 0, epoch_ = 0;
    // scripted skip events (D7)
    bool haveEvent_ = false, undoPending_ = false;
    uint64_t eventMs_ = 0;
    double eventSize_ = 0;
    GameTime undoTo_;
    uint32_t repeatsUndone_ = 0;
};

// ---- client ---------------------------------------------------------------------------------------------------------------------
struct SnapOrder {
    GameTime to;
    bool hard = false;       // jump beacon or a skip: no tolerance was involved
    bool advance = false;    // local AdvanceTime applied before any beacon covered it
    double driftMinutes = 0; // local minus expected, before the order
};

class ClockFollower {
public:
    struct Config {
        double toleranceMinutes = 2.5;     // |drift| at or below this is left alone
        uint32_t snapGapMs = 1000;         // soft snaps at most this often
        uint32_t maxBeaconAgeMs = 20000;   // a beacon older than this is not extrapolated (host quiet or lost): no snaps
        double maxExtrapolateSec = 20.0;
        uint32_t skipHoldMs = 8000;        // after a local AdvanceTime, wait this long for the beacon that carries the new epoch
    };
    explicit ClockFollower(Config c = Config()) : cfg_(c) {}

    void onBeacon(const Beacon& b, uint64_t nowMs);
    // The AdvanceTime effect (wait / sleep granted by the host) reached this machine.
    void onAdvanceTime(uint32_t minutes, uint64_t nowMs);
    // Every frame (or every few): `local` = this machine's clock. An order means "write these globals now"; call noteApplied after.
    std::optional<SnapOrder> tick(uint64_t nowMs, const GameTime& local);
    void noteApplied(uint64_t nowMs) { lastSnapMs_ = nowMs; haveSnap_ = true; }

    bool haveBeacon() const { return haveBeacon_; }
    const Beacon& beacon() const { return beacon_; }
    GameTime expectedAt(uint64_t nowMs) const;   // the host's clock now, extrapolated
    double lastDrift() const { return lastDrift_; }
    uint32_t snaps() const { return snaps_; }
    uint32_t epochSeen() const { return epochSeen_; }

private:
    Config cfg_;
    Beacon beacon_;
    bool haveBeacon_ = false, haveSnap_ = false, pendingJump_ = false;
    uint64_t beaconMs_ = 0, lastSnapMs_ = 0;
    double pendingAdvance_ = 0;
    bool holding_ = false;
    uint64_t holdSinceMs_ = 0;
    uint32_t epochSeen_ = 0, awaitEpoch_ = 0, effectsSeen_ = 0;   // effectsSeen_: AdvanceTime effects heard (compared with the beacon's epoch)
    double lastDrift_ = 0;
    uint32_t snaps_ = 0;
};

}  // namespace nvwt
