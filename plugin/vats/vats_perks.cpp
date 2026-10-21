#include "vats/vats_perks.h"

#include <algorithm>

#include "../../tools/mut/fnvcoop_mut.h"

namespace nvcoop::vats {

const PerkInfo* perkTable(size_t* count) {
    static const PerkInfo t[] = {
        {"Grim Reaper's Sprint", PerkTreatment::Glue, "AP refund for VATS kills decided on another machine (VatsLedger); native kills are refunded by the engine"},
        {"Mysterious Stranger", PerkTreatment::Glue, "the Stranger's hits are credited to the player during the player's own playback (attackerFor)"},
        {"Four Leaf Clover", PerkTreatment::Native, "critical chance is rolled by the shooter's own engine; the critical flag and damage travel in the hit"},
        {"Better Criticals", PerkTreatment::Native, "critical damage is part of the damage the shooter's engine computes"},
        {"Ninja", PerkTreatment::Native, "sneak / melee critical chance and damage are computed by the shooter's engine"},
        {"Concentrated Fire", PerkTreatment::Native, "the VATS hit chance bonus works on the local player's own queue"},
        {"Action Boy / Girl", PerkTreatment::Native, "the AP pool is the local player's own"},
        {"Sniper / Commando / Center of Mass and the other hit-chance perks", PerkTreatment::Native, "the hit chance shown and rolled is the local player's"},
    };
    if (count) *count = sizeof t / sizeof t[0];
    return t;
}

void VatsLedger::clear() {
    actions_.clear();
    playback_ = false;
    playbackEndMs_ = 0;
}

void VatsLedger::onActionQueued(uint32_t targetRef, LimbId limb, uint64_t nowMs) {
    if (!targetRef) return;
    Action a;
    a.limb = limb;
    a.atMs = nowMs;
    a.playbackSeq = playbackSeq_;
    actions_[targetRef] = a;            // a victim that was resurrected / re-targeted can count again (a fresh action)
    // forget old actions
    for (auto it = actions_.begin(); it != actions_.end();) {
        if (nowMs > it->second.atMs && nowMs - it->second.atMs > s_.actionTtlMs && !FNV_MUT(871)) it = actions_.erase(it);   // 871: queued actions live forever
        else ++it;
    }
}

void VatsLedger::onModeChanged(Mode from, Mode to, uint64_t nowMs) {
    if (to == Mode::Playback && from != Mode::Playback) {
        playback_ = true;
        ++playbackSeq_;
    } else if (from == Mode::Playback && to != Mode::Playback) {
        playback_ = false;
        playbackEndMs_ = nowMs;
    }
}

bool VatsLedger::playbackRecently(uint64_t nowMs) const {
    if (playback_) return true;
    if (!playbackEndMs_) return false;
    return nowMs >= playbackEndMs_ && nowMs - playbackEndMs_ <= s_.killWindowMs;
}

bool VatsLedger::onActorDied(uint32_t victimRef, bool decidedHere, bool fromNetwork, uint64_t nowMs, VatsKill* out) {
    auto it = actions_.find(victimRef);
    if (it == actions_.end()) return false;
    const Action a = it->second;
    if (!FNV_MUT(872) && !playbackRecently(nowMs)) return false;                      // 872: any death of a queued target counts, even before the playback
    if (!FNV_MUT(873) && a.playbackSeq >= playbackSeq_ && !playback_) return false;    // 873: a playback that began BEFORE the action was queued also counts
    if (!FNV_MUT(874)) actions_.erase(it);                                            // 874: the action is not used up (the same death pays twice)
    if (decidedHere && !fromNetwork && !FNV_MUT(875)) {                               // 875: a death the local engine settled is refunded twice (engine + us)
        ++nativeKills_;
        return false;
    }
    ++kills_;
    if (out) { out->victimRef = victimRef; out->limb = a.limb; }
    return true;
}

uint32_t attackerFor(const AttackerFacts& f) {
    if (!f.attackerRef || !f.localPlayerRef) return f.attackerRef;
    if (f.attackerRef == f.localPlayerRef) return f.attackerRef;
    if (!f.localPlayback && !FNV_MUT(861)) return f.attackerRef;                      // 861: credited outside the player's playback (steals every untracked hit)
    if (f.attackerTracked && !FNV_MUT(862)) return f.attackerRef;                     // 862: a synced NPC's hit is credited to the player
    if (f.attackerIsParty && !FNV_MUT(863)) return f.attackerRef;                     // 863: another player's puppet is credited to this player
    return f.localPlayerRef;
}

int grimReaperRefund(const PerkSettings& s, bool hasPerk, int ap, int maxAp) {
    if (!s.grsEnabled || (!hasPerk && !FNV_MUT(864))) return 0;                       // 864: refunded without the perk
    if (ap < 0 || maxAp <= 0) return 0;                                               // unknown pool: do not guess
    const int missing = std::max(0, maxAp - ap);
    int give = s.grsRestoreAll ? missing : std::min(std::max(0, s.grsApRestore), missing);
    if (FNV_MUT(865)) give = s.grsRestoreAll ? maxAp : std::max(0, s.grsApRestore);   // 865: the refund can push the pool above its maximum
    return give;
}

}  // namespace nvcoop::vats
