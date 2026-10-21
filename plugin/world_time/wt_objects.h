// World-object state watcher. Game-free.
//
// L5 already captures object changes made by SCRIPT COMMANDS (Lock, UnLock, SetOpenState, SetDestroyed) as ObjectState effects. What no
// hook sees is the NATIVE path: a player activating a door, picking a lock, a trap going off, an NPC walking through a door. This class
// closes that gap by diffing what the engine reports (GetOpenState / GetLocked / GetLockLevel / GetDestroyed, read by the glue) against a
// book of the last known state:
//   host   : a change the book did not hear about becomes an ObjectState effect (with the lock pre-image); first sight is silent.
//   client : the book holds what the HOST said (onAuthoritative); an object that disagrees with it for longer than the grace period is
//            healed (the glue re-applies the host's state). Fields the host never mentioned are never touched.
// Doors report transitional open states (2 opening, 4 closing): only the settled states 1 (open) and 3 (closed) count.
// Mutation twins 130..145 live in wt_objects.cpp.
#pragma once
#include <map>
#include <optional>
#include <vector>

#include "fnvcoop/effect.h"
#include "wt_types.h"

namespace nvwt {

enum class ObjKind : uint8_t { Door, Container, Terminal, Activator, Other };

struct ObjObs {
    FormRef ref;
    ObjKind kind = ObjKind::Other;
    int openState = 0;            // engine GetOpenState: 1 open, 2 opening, 3 closed, 4 closing; 0 = the object has no open state
    bool locked = false;
    int32_t lockLevel = 0;
    bool destroyed = false;
};

// Which fields a kind can carry (ObjectState mask bits).
uint8_t trackedMask(ObjKind k, int openState);

class ObjectWatcher {
public:
    struct Config {
        uint32_t settleMs = 0;          // a changed open state must stay the same this long before it is sent (0 = send on the first settled sighting)
        uint32_t staleMs = 1000;        // a pending change that was last seen longer ago than this starts over
        uint32_t minEmitGapMs = 150;    // per object
        uint32_t healGraceMs = 1500;    // client: how long a mismatch may last before it is healed (the activate round trip is in there)
        uint32_t healGapMs = 1500;      // client: at most one heal per object this often
        size_t maxEntries = 30000;
    };
    ObjectWatcher(Role role, Config c = Config()) : role_(role), cfg_(c) {}

    // Told by the capture path (host: script-captured ObjectState; client: ObjectState received from the host).
    void onAuthoritative(const fnvcoop::ObjectState& s);
    // Host: one observation; the result is what to record (0 or 1 effect).
    std::vector<fnvcoop::ObjectState> observeHost(const ObjObs& o, uint64_t nowMs);
    // Client: one observation; a value means "re-apply this state to the object now".
    std::optional<fnvcoop::ObjectState> observeGuest(const ObjObs& o, uint64_t nowMs);

    // Everything the host has told this machine, as ObjectState effects (diagnostics, rejoin tests).
    std::vector<fnvcoop::ObjectState> authoritative() const;
    void forget(const FormRef& r) { book_.erase(r); }
    void clear() { book_.clear(); }
    size_t size() const { return book_.size(); }

    struct Stats { uint32_t firstSight = 0, emitted = 0, healed = 0, divergentUntold = 0, echoSuppressed = 0; };
    const Stats& stats() const { return stats_; }

private:
    struct Entry {
        bool open = false, locked = false, destroyed = false;
        int32_t level = 0;
        uint8_t known = 0;               // mask bits the host has spoken about (authoritative)
        bool pendingOpen = false;
        bool havePending = false;
        uint64_t pendingSince = 0, lastEmit = 0, lastSeen = 0, mismatchSince = 0, lastHeal = 0;
    };
    void trim();
    Role role_;
    Config cfg_;
    std::map<FormRef, Entry> book_;
    Stats stats_;
};

}  // namespace nvwt
