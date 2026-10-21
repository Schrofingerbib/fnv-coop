#include "wt_objects.h"

#include <iterator>

#include "fnvcoop_mut.h"

namespace nvwt {

using fnvcoop::kObjDestroyed;
using fnvcoop::kObjLock;
using fnvcoop::kObjOpen;
using fnvcoop::ObjectState;

uint8_t trackedMask(ObjKind k, int openState) {
    uint8_t m = 0;
    switch (k) {
        case ObjKind::Door: m = kObjLock | kObjOpen | kObjDestroyed; break;
        case ObjKind::Container: m = kObjLock | kObjDestroyed; break;
        case ObjKind::Terminal: m = kObjLock; break;
        case ObjKind::Activator: m = kObjDestroyed; break;
        case ObjKind::Other: m = kObjDestroyed; break;
    }
    if ((k == ObjKind::Activator || k == ObjKind::Other) && openState != 0) m |= kObjOpen;
    if (openState == 0) m = static_cast<uint8_t>(m & ~kObjOpen);   // the engine gave no open state: nothing to compare
    return m;
}

void ObjectWatcher::trim() {
    if (book_.size() <= cfg_.maxEntries) return;
    const size_t target = cfg_.maxEntries - cfg_.maxEntries / 10;
    for (auto it = book_.begin(); it != book_.end() && book_.size() > target;)
        it = (it->second.known == 0) ? book_.erase(it) : std::next(it);
    for (auto it = book_.begin(); it != book_.end() && book_.size() > target;) it = book_.erase(it);
}

void ObjectWatcher::onAuthoritative(const ObjectState& s) {
    if (s.mask == 0) return;
    if (role_ == Role::Host && FNV_MUT(134)) return;   // MUTANT 134: a script-captured change is not entered, so the poll sends it a second time
    auto it = book_.find(s.ref);
    const bool existed = it != book_.end();
    Entry& e = existed ? it->second : book_[s.ref];
    bool changed = false;
    if (s.mask & kObjOpen) {
        changed |= existed && e.open != s.open;
        e.open = s.open;
        e.havePending = false;
        e.known |= kObjOpen;
    }
    if (s.mask & kObjLock) {
        const int32_t lv = s.locked ? s.level : 0;
        changed |= existed && (e.locked != s.locked || e.level != lv);
        e.locked = s.locked;
        e.level = lv;
        e.known |= kObjLock;
    }
    if (s.mask & kObjDestroyed) {
        changed |= existed && e.destroyed != s.destroyed;
        e.destroyed = s.destroyed;
        e.known |= kObjDestroyed;
    }
    if (role_ == Role::Host && changed) ++stats_.echoSuppressed;
    if (!existed) trim();
}

std::vector<ObjectState> ObjectWatcher::observeHost(const ObjObs& o, uint64_t nowMs) {
    std::vector<ObjectState> out;
    if (role_ != Role::Host) return out;
    auto it = book_.find(o.ref);
    if (it == book_.end()) {
        Entry e;
        e.open = o.openState == 1;
        e.locked = o.locked;
        e.level = o.locked ? o.lockLevel : 0;
        e.destroyed = o.destroyed;
        book_[o.ref] = e;   // first sight: the loaded save's state, not a change
        ++stats_.firstSight;
        trim();
        return out;
    }
    Entry& e = it->second;
    if (e.havePending && nowMs - e.lastSeen > cfg_.staleMs && !FNV_MUT(137)) e.havePending = false;   // MUTANT 137: a change seen once, long ago, is confirmed by the next sighting
    e.lastSeen = nowMs;
    const uint8_t tm = trackedMask(o.kind, o.openState);
    uint8_t mask = 0;
    if (tm & kObjLock) {
        const int32_t lv = o.locked ? o.lockLevel : 0;
        if (o.locked != e.locked || lv != e.level) mask |= kObjLock;
    }
    if ((tm & kObjDestroyed) && o.destroyed && !e.destroyed) mask |= kObjDestroyed;
    bool wantOpen = e.open;
    if (tm & kObjOpen) {
        const bool stable = FNV_MUT(131) ? (o.openState >= 1 && o.openState <= 4) : (o.openState == 1 || o.openState == 3);   // MUTANT 131: "opening" and "closing" already count
        if (stable) {
            wantOpen = FNV_MUT(131) ? (o.openState == 1 || o.openState == 2) : (o.openState == 1);
            if (wantOpen != e.open) {
                if (!e.havePending || e.pendingOpen != wantOpen) {
                    e.havePending = true;
                    e.pendingOpen = wantOpen;
                    e.pendingSince = nowMs;
                }
                if (nowMs - e.pendingSince >= cfg_.settleMs || FNV_MUT(132)) mask |= kObjOpen;   // MUTANT 132: no settle time, a flapping door floods the wire
            } else {
                e.havePending = false;
            }
        }
    }
    if (mask == 0) return out;
    if (e.lastEmit != 0 && nowMs - e.lastEmit < cfg_.minEmitGapMs && !FNV_MUT(133)) return out;   // MUTANT 133: no per-object rate limit
    ObjectState s;
    s.ref = o.ref;
    s.mask = mask;
    if (mask & kObjOpen) {
        s.open = wantOpen;
        e.open = wantOpen;
        e.havePending = false;
    }
    if (mask & kObjLock) {
        s.locked = o.locked;
        s.level = o.locked ? o.lockLevel : 0;
        if (!FNV_MUT(135)) {   // MUTANT 135: no pre-image, a rebase after a host load cannot restore the lock
            s.hasBefore = true;
            s.beforeLocked = e.locked;
            s.beforeLevel = e.level;
        }
        e.locked = s.locked;
        e.level = s.level;
    }
    if (mask & kObjDestroyed) {
        s.destroyed = true;
        e.destroyed = true;
    }
    e.lastEmit = nowMs;
    ++stats_.emitted;
    out.push_back(s);
    return out;
}

std::optional<ObjectState> ObjectWatcher::observeGuest(const ObjObs& o, uint64_t nowMs) {
    if (role_ != Role::Client) return std::nullopt;
    auto it = book_.find(o.ref);
    if (it == book_.end()) return std::nullopt;   // the host never said anything about this object: nothing to heal toward
    Entry& e = it->second;
    if (e.known == 0) return std::nullopt;
    const uint8_t tm = trackedMask(o.kind, o.openState);
    uint8_t mask = 0;
    if ((e.known & kObjLock) && (tm & kObjLock)) {
        if (o.locked != e.locked || (e.locked && o.lockLevel != e.level)) mask |= kObjLock;
    }
    if ((e.known & kObjDestroyed) && (tm & kObjDestroyed) && e.destroyed && !o.destroyed) mask |= kObjDestroyed;
    if ((e.known & kObjOpen) && (tm & kObjOpen) && (o.openState == 1 || o.openState == 3) && (o.openState == 1) != e.open) mask |= kObjOpen;
    if (FNV_MUT(136)) mask &= static_cast<uint8_t>(~kObjLock);   // MUTANT 136: a lock that disagrees is never healed
    if (mask == 0) {
        e.mismatchSince = 0;
        return std::nullopt;
    }
    if (e.mismatchSince == 0) e.mismatchSince = nowMs;
    if (nowMs - e.mismatchSince < cfg_.healGraceMs && !FNV_MUT(138)) return std::nullopt;   // MUTANT 138: heals at once and fights the activate round trip
    if (e.lastHeal != 0 && nowMs - e.lastHeal < cfg_.healGapMs && !FNV_MUT(139)) return std::nullopt;   // MUTANT 139: a heal that does not take is retried every frame
    e.lastHeal = nowMs;
    e.mismatchSince = 0;
    ObjectState s;
    s.ref = o.ref;
    s.mask = mask;
    if (mask & kObjOpen) s.open = e.open;
    if (mask & kObjLock) {
        s.locked = e.locked;
        s.level = e.locked ? e.level : 0;
    }
    if (mask & kObjDestroyed) s.destroyed = true;
    ++stats_.healed;
    return s;
}

std::vector<ObjectState> ObjectWatcher::authoritative() const {
    std::vector<ObjectState> out;
    for (const auto& kv : book_) {
        const Entry& e = kv.second;
        if (e.known == 0) continue;
        ObjectState s;
        s.ref = kv.first;
        s.mask = e.known & (kObjOpen | kObjLock | kObjDestroyed);
        s.open = e.open;
        s.locked = e.locked;
        s.level = e.locked ? e.level : 0;
        s.destroyed = e.destroyed;
        out.push_back(s);
    }
    return out;
}

}  // namespace nvwt
