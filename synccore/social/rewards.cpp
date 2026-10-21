#include "social/rewards.h"

#include <algorithm>
#include <cmath>

#include "../../tools/mut/fnvcoop_mut.h"

namespace fnvcoop::social {

namespace {

uint8_t bit(PlayerId p) { return p < kMaxSlots ? static_cast<uint8_t>(1u << p) : 0; }

void ioEntity(Archive& a, cc::Entity& e) {
    uint8_t k = static_cast<uint8_t>(e.kind);
    a.u8(k);
    if (a.reading()) {
        if (k > static_cast<uint8_t>(cc::EntityKind::Actor)) { a.fail(); return; }
        e.kind = static_cast<cc::EntityKind>(k);
    }
    if (e.kind == cc::EntityKind::None) { if (a.reading()) e.id = 0; return; }
    a.varu32(e.id);
}

}  // namespace

const char* toString(KarmaClass k) {
    static const char* n[] = {"Unknown", "VeryEvil", "Evil", "Neutral", "Good", "VeryGood"};
    return static_cast<size_t>(k) < sizeof n / sizeof *n ? n[static_cast<size_t>(k)] : "?";
}

bool operator==(const SocialSettings& a, const SocialSettings& b) {
    if (a.rev != b.rev || a.xpMode != b.xpMode || a.scope != b.scope || a.nearRadius != b.nearRadius || a.shareKarma != b.shareKarma ||
        a.karmaOnKills != b.karmaOnKills || a.crime != b.crime || a.friendlyFire != b.friendlyFire || a.foreignCompanions != b.foreignCompanions ||
        !(a.caps == b.caps))
        return false;
    for (size_t i = 0; i < static_cast<size_t>(KarmaClass::Count_); ++i) if (a.karma.delta[i] != b.karma.delta[i]) return false;
    return true;
}

void io(Archive& a, SocialSettings& s) {
    a.varu32(s.rev);
    uint8_t mode = static_cast<uint8_t>(s.xpMode), scope = static_cast<uint8_t>(s.scope), foreign = static_cast<uint8_t>(s.foreignCompanions);
    a.u8(mode); a.u8(scope); a.u8(foreign);
    a.f32(s.nearRadius);
    uint8_t flags = static_cast<uint8_t>((s.shareKarma ? 1 : 0) | (s.karmaOnKills ? 2 : 0) | (s.crime ? 4 : 0) | (s.friendlyFire ? 8 : 0));
    a.u8(flags);
    a.u8(s.caps.humanoidPerOwner); a.u8(s.caps.critterPerOwner); a.u8(s.caps.partyTotal);
    for (size_t i = 0; i < static_cast<size_t>(KarmaClass::Count_); ++i) a.vari32(s.karma.delta[i]);
    if (a.reading()) {
        if (mode > 1 || scope > 2 || foreign > 1 || (flags & 0xF0) || !std::isfinite(s.nearRadius) || s.nearRadius < 0 || s.caps.partyTotal > kMaxSlots * 4) { a.fail(); return; }
        s.xpMode = static_cast<cc::XpMode>(mode); s.scope = static_cast<XpScope>(scope); s.foreignCompanions = static_cast<ForeignPolicy>(foreign);
        s.shareKarma = flags & 1; s.karmaOnKills = flags & 2; s.crime = flags & 4; s.friendlyFire = flags & 8;
    }
}

void io(Archive& a, KillReport& r) {
    a.u64(r.deathId);
    ioEntity(a, r.victim.who);
    uint8_t kc = static_cast<uint8_t>(r.victim.karma);
    a.u8(kc);
    io(a, r.victim.faction);
    a.vari32(r.victim.xp);
    a.boolean(r.victim.hostileToParty);
    a.boolean(r.victim.named);
    ioEntity(a, r.killer);
    a.u8(r.killerRecruiter);
    size_t n = r.contributors.size();
    if (!a.count(n, kMaxSlots)) return;
    if (a.reading()) r.contributors.assign(n, 0);
    for (auto& p : r.contributors) a.u8(p);
    a.u8(r.nativeMask);
    a.varu32(r.cell);
    a.f32(r.where.x); a.f32(r.where.y); a.f32(r.where.z);
    a.u8(r.reporter);
    if (a.reading()) {
        if (kc >= static_cast<uint8_t>(KarmaClass::Count_) || r.victim.xp < 0 || r.reporter >= kMaxSlots) { a.fail(); return; }
        for (PlayerId p : r.contributors) if (p >= kMaxSlots) { a.fail(); return; }
        r.victim.karma = static_cast<KarmaClass>(kc);
    }
}

std::vector<PlayerId> presentPlayers(XpScope scope, const std::vector<PlayerPlace>& players, uint32_t cell, const cc::Vec3& where, float nearRadius) {
    std::set<PlayerId> s;
    for (const PlayerPlace& p : players) {
        if (p.slot >= kMaxSlots || !p.connected) continue;
        bool in = true;
        if (scope == XpScope::SameCell && !FNV_MUT(868)) in = p.cell == cell;                                                    // BAD (868): SameCell counts everybody
        if (scope == XpScope::Near && !FNV_MUT(869)) in = p.cell == cell && cc::distance(p.pos, where) <= nearRadius;              // BAD (869): Near counts everybody
        if (in) s.insert(p.slot);
    }
    return std::vector<PlayerId>(s.begin(), s.end());
}

bool RewardLedger::remember(uint64_t id) {
    if (!ids_.insert(id).second) return false;
    order_.push_back(id);
    while (order_.size() > cap_) { ids_.erase(order_.front()); order_.erase(order_.begin()); }
    return true;
}

KillRewards RewardLedger::settle(const KillReport& k, const SocialSettings& s, const std::vector<PlayerId>& present, uint8_t connectedMask,
                                 const cc::CombatRules& rules) {
    KillRewards r;
    if (ids_.count(k.deathId) && !FNV_MUT(861)) return r;   // BAD (861): a replayed or doubly reported death pays twice
    remember(k.deathId);

    PlayerId responsible = kNoPlayer;
    if (k.killer.isPlayer() && k.killer.id < kMaxSlots) responsible = static_cast<PlayerId>(k.killer.id);
    else if (k.killerRecruiter < kMaxSlots) responsible = k.killerRecruiter;
    r.responsible = responsible;

    // ---- XP
    cc::KillRecord kr;
    kr.deathId = k.deathId; kr.victim = k.victim.who; kr.killer = k.killer; kr.killerRecruiter = k.killerRecruiter; kr.xp = k.victim.xp;
    kr.contributors = k.contributors;
    cc::XpMode mode = FNV_MUT(870) ? cc::XpMode::KillingBlow : s.xpMode;   // BAD (870): the host's party-share setting is ignored
    if (k.victim.who.isPlayer()) kr.xp = 0;                                // a player's death is not a kill that pays
    for (const cc::XpAward& a : cc::attributeKillXp(kr, mode, present, rules)) {
        if (a.slot >= kMaxSlots || a.xp <= 0) continue;
        if ((k.nativeMask & bit(a.slot)) && !FNV_MUT(862)) { r.xpNative += a.xp; continue; }   // BAD (862): the engine already paid it; paying again doubles it
        r.xp.push_back(XpGrant{a.slot, a.xp});
    }

    // ---- karma
    if (s.karmaOnKills && responsible != kNoPlayer && k.victim.karma != KarmaClass::Unknown && !k.victim.who.isPlayer()) {
        int32_t d = s.karma.delta[static_cast<size_t>(k.victim.karma)];
        if (k.victim.hostileToParty && d < 0 && !FNV_MUT(863)) d = 0;   // BAD (863): defending yourself costs karma
        if (d != 0) {
            uint8_t mask = s.shareKarma ? connectedMask : bit(responsible);
            if (FNV_MUT(864)) mask = bit(responsible);                   // BAD (864): karma is never shared
            if (!FNV_MUT(865)) mask = static_cast<uint8_t>(mask & ~k.nativeMask);   // BAD (865): a slot whose engine already moved its karma gets it again
            r.karma = d;
            r.karmaTo = mask;
        }
    }
    r.settled = !r.xp.empty() || (r.karma != 0 && r.karmaTo != 0);
    return r;
}

std::vector<Effect> rewardEffects(const KillRewards& r, uint8_t localMask) {
    std::vector<Effect> out;
    for (const XpGrant& g : r.xp) {
        if ((localMask & bit(g.slot)) && !FNV_MUT(866)) continue;   // BAD (866): the host's own XP is also committed, so the host is paid twice
        ModActorValue m;
        m.av = kAvExperience; m.delta = g.xp; m.to = Recipient::slot(g.slot);
        out.push_back(m);
    }
    const uint8_t mask = FNV_MUT(866) ? r.karmaTo : static_cast<uint8_t>(r.karmaTo & ~localMask);
    if (r.karma != 0 && mask != 0) {
        ModKarma k;
        k.delta = r.karma; k.to = Recipient::slots(mask);
        out.push_back(k);
    }
    return out;
}

std::vector<Effect> reputationEffects(const std::vector<RepChange>& rep, PlayerId criminal, bool shared, uint8_t connectedMask, uint8_t nativeMask, uint8_t localMask) {
    std::vector<Effect> out;
    uint8_t mask = shared ? connectedMask : bit(criminal);
    if (FNV_MUT(867)) mask = bit(criminal);   // BAD (867): reputation is never shared
    mask = static_cast<uint8_t>(mask & ~nativeMask & ~localMask);
    if (mask == 0) return out;
    for (const RepChange& c : rep) {
        if (c.fame == 0 && c.infamy == 0) continue;
        ModReputation m;
        m.faction = c.faction; m.fame = c.fame; m.infamy = c.infamy; m.to = Recipient::slots(mask);
        out.push_back(m);
    }
    return out;
}

}  // namespace fnvcoop::social
