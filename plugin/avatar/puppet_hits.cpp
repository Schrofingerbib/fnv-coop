#include "puppet_hits.h"

#include <cstdio>

#include "avatar_faults.h"

namespace nvcoop::avatar {

HitOutcome PuppetHitRouter::OnHit(const PuppetHit& h) {
    const AvatarFaults& F = avatarFaults();
    HitOutcome o;
    ++stats_.hits;
    Slot slot = kNoSlot;
    if (!slotOfPuppet(h.victimRef, slot)) {
        ++stats_.notPuppet;
        return o;   // Proceed
    }
    o.victimSlot = slot;
    if (!(h.healthDmg > 0) && !(h.limbDmg > 0) && !F.hitsForwardEmpty) {
        ++stats_.ignored;
        o.route = HitRoute::Ignored;
        return o;
    }
    o.action = HitAction::Clamp;
    if (!port_) {
        ++stats_.noPort;
        o.route = HitRoute::NoPort;
    } else {
        HitAction act = HitAction::Clamp;
        if (port_->Forward(slot, h, act)) {
            ++stats_.forwarded;
            o.route = HitRoute::Forwarded;
            o.action = act;
        } else {
            ++stats_.refused;
            o.route = HitRoute::PortRefused;
        }
    }
    if (F.puppetHitProceeds) o.action = HitAction::Proceed;   // broken twin: the local puppet takes the damage itself
    if (o.action == HitAction::Clamp) ++stats_.clamped;
    if (o.action == HitAction::Neutralize) ++stats_.neutralized;
    return o;
}

std::string PuppetHitRouter::StatusJson() const {
    char b[300];
    std::snprintf(b, sizeof b,
                  "{\"hits\":%llu,\"notPuppet\":%llu,\"ignored\":%llu,\"forwarded\":%llu,\"noPort\":%llu,\"refused\":%llu,\"clamped\":%llu,\"neutralized\":%llu}",
                  (unsigned long long)stats_.hits, (unsigned long long)stats_.notPuppet, (unsigned long long)stats_.ignored,
                  (unsigned long long)stats_.forwarded, (unsigned long long)stats_.noPort, (unsigned long long)stats_.refused,
                  (unsigned long long)stats_.clamped, (unsigned long long)stats_.neutralized);
    return b;
}

}  // namespace nvcoop::avatar
