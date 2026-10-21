#include "equip_corrector.h"

#include <algorithm>
#include <tuple>

#include "avatar_faults.h"

namespace nvcoop::avatar {

namespace {
bool SameSet(const std::vector<EquipEntry>& a, const std::vector<EquipEntry>& b) {
    if (a.size() != b.size()) return false;
    const uint8_t modMask = avatarFaults().modsIgnoredInSet ? 0 : kWeaponModMask;
    std::set<std::tuple<FormId, bool, uint8_t>> sa, sb;
    for (const auto& e : a) sa.insert(std::make_tuple(e.form, e.weapon, static_cast<uint8_t>(e.mods & modMask)));
    for (const auto& e : b) sb.insert(std::make_tuple(e.form, e.weapon, static_cast<uint8_t>(e.mods & modMask)));
    return sa == sb;
}
}  // namespace

void EquipCorrector::Reset() {
    nextAt_ = 0;
    converged_ = false;
    weaponsReady_ = want_.empty();
    last_ = Pending{};
    fails_.clear();
    undone_.clear();
    gaveUp_.clear();
    modsConverged_ = true;
    modFails_.clear();
    modGaveUp_.clear();
    modAsked_.clear();
}

void EquipCorrector::SetDesired(const std::vector<EquipEntry>& want) {
    if (SameSet(want, want_)) return;
    want_ = want;
    nextAt_ = 0;
    converged_ = false;
    weaponsReady_ = false;
    fails_.clear();
    undone_.clear();
    gaveUp_.clear();
    last_ = Pending{};
    modsConverged_ = true;
    modFails_.clear();
    modGaveUp_.clear();
    modAsked_.clear();
}

int EquipCorrector::Tick(IPuppetEngine& e, RefId ref, uint32_t now, Stats& stats) {
    const AvatarFaults& F = avatarFaults();
    if (static_cast<int32_t>(now - nextAt_) < 0) return 0;

    std::vector<FormId> cur;
    if (!e.GetEquipped(ref, cur)) {
        nextAt_ = now + cfg_->equipPollMsConverged;
        return 0;
    }
    std::set<FormId> have(cur.begin(), cur.end());
    std::set<FormId> want;
    for (const auto& w : want_) want.insert(w.form);

    // Did the engine undo our previous write? (Equip not worn, or Unequip worn again.)
    if (last_.kind != Kind::None) {
        bool worn = have.count(last_.item) != 0;
        bool undone = (last_.kind == Kind::Equip && !worn) || (last_.kind == Kind::Unequip && worn);
        if (undone) {
            auto& v = undone_[last_.item];
            v.push_back(now);
            v.erase(std::remove_if(v.begin(), v.end(), [&](uint32_t t) { return now - t > cfg_->equipFlapWindowMs; }), v.end());
        }
        last_ = Pending{};
    }

    // Items to take off: everything worn that is not wanted.
    std::vector<FormId> extras;
    for (FormId f : have)
        if (!want.count(f)) extras.push_back(f);

    // Items to put on: non-weapons first, then weapons. Given-up items are skipped.
    std::vector<FormId> missing;
    bool anyGaveUpWanted = false;
    for (int pass = 0; pass < 2; ++pass)
        for (const auto& w : want_) {
            if (w.weapon != (pass == 1) || have.count(w.form)) continue;
            if (gaveUp_.count(w.form)) {
                anyGaveUpWanted = true;
                continue;
            }
            missing.push_back(w.form);
        }

    weaponsReady_ = true;
    for (const auto& w : want_)
        if (w.weapon && !have.count(w.form)) weaponsReady_ = false;

    if (extras.empty() && missing.empty()) {
        // every wanted item is worn: now the weapon modifications, one slot per write
        modsConverged_ = true;
        for (const auto& w : want_) {
            const uint8_t wantMods = static_cast<uint8_t>(w.mods & kWeaponModMask);
            if (!w.weapon || !wantMods || !have.count(w.form) || F.noWeaponMods) continue;
            const int haveMods = e.WeaponMods(ref, w.form);   // -1 = the engine cannot tell
            for (int slot = 1; slot <= 3; ++slot) {
                const uint8_t bit = static_cast<uint8_t>(1u << (slot - 1));
                if (!(wantMods & bit)) continue;
                const ModKey key{w.form, slot};
                if (haveMods >= 0 && (haveMods & bit)) continue;                       // already on
                if (modGaveUp_.count(key)) continue;
                if (haveMods < 0 && modAsked_.count(key)) continue;               // cannot verify: one request per slot is enough
                modsConverged_ = false;
                converged_ = false;
                if (e.EnableWeaponMod(ref, w.form, slot)) {
                    ++stats.modOps;
                    modAsked_.insert(key);
                    modFails_[key] = 0;
                    nextAt_ = now + cfg_->equipOpIntervalMs;
                } else {
                    ++stats.modFailures;
                    unsigned n = ++modFails_[key];
                    if (n >= cfg_->equipMaxFails && !F.modsRetryForever) {
                        modGaveUp_.insert(key);
                        ++stats.modGaveUp;
                    }
                    unsigned d = cfg_->equipOpIntervalMs;
                    if (!F.modsRetryForever)
                        for (unsigned i = 1; i < n && d < cfg_->equipBackoffMaxMs; ++i) d *= 2;
                    nextAt_ = now + std::min(d, cfg_->equipBackoffMaxMs);
                }
                return 1;
            }
        }
        converged_ = !anyGaveUpWanted;
        nextAt_ = now + cfg_->equipPollMsConverged;
        return 0;
    }
    converged_ = false;

    auto backoff = [&](FormId item) {
        unsigned f = fails_[item];
        if (F.noEquipBackoff) return cfg_->equipOpIntervalMs;
        uint32_t d = cfg_->equipOpIntervalMs;
        for (unsigned i = 1; i < f && d < cfg_->equipBackoffMaxMs; ++i) d *= 2;
        return std::min(d, cfg_->equipBackoffMaxMs);
    };
    auto fail = [&](FormId item) {
        ++stats.equipFailures;
        unsigned n = ++fails_[item];
        if (!F.noEquipBackoff && n >= cfg_->equipMaxFails && gaveUp_.insert(item).second) ++stats.equipGaveUp;
        nextAt_ = now + backoff(item);
    };
    auto ok = [&](FormId item, Kind k) {
        ++stats.equipOps;
        fails_[item] = 0;
        last_ = Pending{k, item};
        nextAt_ = now + cfg_->equipOpIntervalMs;
    };

    // 1. take off extras (or remove them for good when the engine keeps putting them back)
    if (!extras.empty()) {
        FormId item = extras.front();
        bool flapping = !F.noEquipFlapGuard && undone_[item].size() >= cfg_->equipFlapLimit;
        if (flapping) {
            int n = std::max(1, e.ItemCount(ref, item));
            if (e.RemoveItem(ref, item, n)) {
                ++stats.equipFlapRemovals;
                undone_[item].clear();
                ok(item, Kind::None);
                last_ = Pending{};
            } else {
                fail(item);
            }
        } else if (e.UnequipItem(ref, item)) {
            ok(item, Kind::Unequip);
        } else {
            fail(item);
        }
        return 1;
    }

    // 2. put on what is missing, owning it first
    FormId item = missing.front();
    // flapping wanted item (the engine strips it again and again): give up on it instead of fighting forever
    if (!F.noEquipFlapGuard && undone_[item].size() >= cfg_->equipFlapLimit) {
        if (gaveUp_.insert(item).second) ++stats.equipGaveUp;
        nextAt_ = now + cfg_->equipOpIntervalMs;
        return 0;
    }
    if (!F.noEquipAddFirst && e.ItemCount(ref, item) <= 0) {
        if (e.AddItem(ref, item, 1)) {
            ++stats.equipOps;
            fails_[item] = 0;
            nextAt_ = now + cfg_->equipOpIntervalMs;
        } else {
            fail(item);
        }
        return 1;
    }
    if (e.EquipItem(ref, item)) ok(item, Kind::Equip);
    else fail(item);
    return 1;
}

}  // namespace nvcoop::avatar
