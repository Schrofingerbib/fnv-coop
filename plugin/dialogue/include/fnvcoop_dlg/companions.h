// Companion ownership + per-owner rewrite of the shared follower quest variables (L8). Game-free, header-only.
//
// Vanilla tracks followers in ONE quest (VNPCFollowers): bHumanoidInParty, bCritterInParty, bPlayerHasFollower,
// nCurrentFollowers and a Hired/Available/Fired triple per companion. With several players those shared values
// make player B think player A's companion is "in my party" (no recruit possible once anyone has a humanoid) and a
// recruit INFO's own `set ...bHumanoidInParty to 1` blocks everybody. Owner decisions (MASTER-PLAN):
//   * a companion is owned by the recruiting player; commands (wait, follow, dismiss, trade, wheel) are owner-only
//   * follower cap is per recruiting player (1 humanoid + 1 critter), party total configurable (default 4)
//
// Mechanism: every machine keeps this registry (filled from CompanionOwner effects; the host also validates
// recruits). Dialogue conditions are evaluated on the initiator's machine, so the engine hook for
// GetQuestVariable/GetScriptVariable calls viewVar(quest, idx, evaluatingSlot, realValue) and gets the value as seen
// by THAT player. The shared quest variable itself keeps the party AGGREGATE (aggregateVar) so host-side quest
// scripts that read it (waiting-companion logic, achievements) keep working.
#pragma once
#include <cstdint>
#include <istream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>
#include "fnvcoop/types.h"
#include "fnvcoop_dlg/mutations.h"

namespace fnvcoop_dlg {

using fnvcoop::FormRef;
using fnvcoop::PlayerId;

enum class CompanionKind : uint8_t { Humanoid = 0, Critter = 1 };

struct CompanionRules {
    uint8_t humanoidCap = 1;   // per recruiting player
    uint8_t critterCap = 1;    // per recruiting player
    uint8_t partyTotal = 4;    // across the whole party
};

enum class RecruitResult : uint8_t {
    Ok = 0,
    AlreadyYours = 1,      // idempotent: this player already owns the companion
    OwnedByOther = 2,
    OwnerCapHumanoid = 3,
    OwnerCapCritter = 4,
    PartyFull = 5,
    Unknown = 6,           // not a registered companion
};

// How a (quest, var index) is rewritten.
enum class VarKind : uint8_t {
    HumanoidInParty, CritterInParty, HasFollower, CurrentFollowers,   // per-owner aggregates
    Hired, Available, Fired,                                         // per companion (needs npc)
};

class CompanionRegistry {
public:
    explicit CompanionRegistry(CompanionRules r = {}) : rules_(r) {}

    const CompanionRules& rules() const { return rules_; }
    void define(const FormRef& npc, CompanionKind kind) { kinds_[npc] = kind; }
    bool isCompanion(const FormRef& npc) const { return kinds_.count(npc) != 0; }

    // ---- ownership
    PlayerId ownerOf(const FormRef& npc) const {
        auto it = owner_.find(npc);
        return it == owner_.end() ? fnvcoop::kNoPlayer : it->second;
    }
    int count(PlayerId slot, int kind = -1) const {
        int n = 0;
        for (auto& kv : owner_) {
            if (kv.second != slot) continue;
            auto k = kinds_.find(kv.first);
            if (kind < 0 || (k != kinds_.end() && int(k->second) == kind)) ++n;
        }
        return n;
    }
    int partyCount() const { return int(owner_.size()); }

    RecruitResult canRecruit(const FormRef& npc, PlayerId slot) const {
        auto k = kinds_.find(npc);
        if (k == kinds_.end()) return RecruitResult::Unknown;
        PlayerId o = ownerOf(npc);
        if (o == slot) return RecruitResult::AlreadyYours;
        if (o != fnvcoop::kNoPlayer) return RecruitResult::OwnedByOther;
        if (mutations().capIsPartyWide) {  // MUTANT: the old "2 companions total" rule
            if (count(0, 0) + count(1, 0) + count(2, 0) + count(3, 0) >= rules_.humanoidCap && k->second == CompanionKind::Humanoid)
                return RecruitResult::OwnerCapHumanoid;
        } else {
            if (k->second == CompanionKind::Humanoid && count(slot, 0) >= rules_.humanoidCap) return RecruitResult::OwnerCapHumanoid;
            if (k->second == CompanionKind::Critter && count(slot, 1) >= rules_.critterCap) return RecruitResult::OwnerCapCritter;
        }
        if (partyCount() >= rules_.partyTotal) return RecruitResult::PartyFull;
        return RecruitResult::Ok;
    }
    RecruitResult recruit(const FormRef& npc, PlayerId slot) {
        RecruitResult r = canRecruit(npc, slot);
        if (r == RecruitResult::Ok) owner_[npc] = slot;
        return r;
    }
    // Only the owner may dismiss (kNoPlayer = host/system override, e.g. companion death).
    bool dismiss(const FormRef& npc, PlayerId by) {
        auto it = owner_.find(npc);
        if (it == owner_.end()) return false;
        if (by != fnvcoop::kNoPlayer && by != it->second && !mutations().anyoneDismisses) return false;
        owner_.erase(it);
        return true;
    }
    // Replicated state (CompanionOwner effect): force without cap checks. owner == kNoPlayer clears.
    void setOwner(const FormRef& npc, PlayerId owner) {
        if (owner == fnvcoop::kNoPlayer) owner_.erase(npc); else owner_[npc] = owner;
    }
    // The owner left the session: their companions are released (they walk home; vanilla "fired" state).
    std::vector<FormRef> releaseOwner(PlayerId slot) {
        std::vector<FormRef> freed;
        if (mutations().keepCompanionsOfLeaver) return freed;
        for (auto it = owner_.begin(); it != owner_.end();) {
            if (it->second == slot) { freed.push_back(it->first); it = owner_.erase(it); } else ++it;
        }
        return freed;
    }

    // ---- variable rules
    void addVarRule(const FormRef& quest, uint32_t idx, VarKind kind, const FormRef& npc = FormRef()) {
        rules_by_[{quest, idx}] = {kind, npc};
    }
    bool hasRule(const FormRef& quest, uint32_t idx) const { return rules_by_.count({quest, idx}) != 0; }
    size_t ruleCount() const { return rules_by_.size(); }

    // The value of the variable as seen by `slot`. `real` is the shared (aggregate) value the engine holds.
    int32_t viewVar(const FormRef& quest, uint32_t idx, PlayerId slot, int32_t real) const {
        auto it = rules_by_.find({quest, idx});
        if (it == rules_by_.end() || mutations().noOwnerRewrite) return real;
        const Rule& r = it->second;
        switch (r.kind) {
            case VarKind::HumanoidInParty:
                return (count(slot, 0) >= rules_.humanoidCap || partyFull()) ? 1 : 0;   // party total reached: recruiting closed
            case VarKind::CritterInParty:
                return (count(slot, 1) >= rules_.critterCap || partyFull()) ? 1 : 0;
            case VarKind::HasFollower: return count(slot) > 0 ? 1 : 0;
            case VarKind::CurrentFollowers: return count(slot);
            case VarKind::Hired: return ownerOf(r.npc) == slot ? 1 : 0;
            case VarKind::Available: {
                PlayerId o = ownerOf(r.npc);
                if (o != fnvcoop::kNoPlayer && o != slot) return 0;   // already travelling with another courier
                return real;
            }
            case VarKind::Fired: return real;
        }
        return real;
    }
    // What the shared variable should hold: the party aggregate (any owner has one / total).
    int32_t aggregateVar(const FormRef& quest, uint32_t idx, int32_t real) const {
        auto it = rules_by_.find({quest, idx});
        if (it == rules_by_.end()) return real;
        const Rule& r = it->second;
        switch (r.kind) {
            case VarKind::HumanoidInParty: return anyKind(0) ? 1 : 0;
            case VarKind::CritterInParty: return anyKind(1) ? 1 : 0;
            case VarKind::HasFollower: return partyCount() > 0 ? 1 : 0;
            case VarKind::CurrentFollowers: return partyCount();
            case VarKind::Hired: return ownerOf(r.npc) != fnvcoop::kNoPlayer ? 1 : 0;
            case VarKind::Available: case VarKind::Fired: return real;
        }
        return real;
    }
    struct VarFix { FormRef quest; uint32_t idx; int32_t value; };
    // After CompanionOwner effects were applied: the SetScriptVar corrections that bring the shared variables back
    // in line with the registry (the recruit/dismiss INFO's own writes are per-origin, the registry is the truth).
    std::vector<VarFix> reconcile(const std::map<std::pair<FormRef, uint32_t>, int32_t>& realValues) const {
        std::vector<VarFix> out;
        for (auto& kv : rules_by_) {
            auto rv = realValues.find(kv.first);
            int32_t real = rv == realValues.end() ? 0 : rv->second;
            if (kv.second.kind == VarKind::Available || kv.second.kind == VarKind::Fired) continue;
            int32_t want = aggregateVar(kv.first.first, kv.first.second, real);
            if (rv == realValues.end() || want != real) out.push_back({kv.first.first, kv.first.second, want});
        }
        return out;
    }

private:
    struct Rule { VarKind kind; FormRef npc; };
    bool partyFull() const { return partyCount() >= rules_.partyTotal; }
    bool anyKind(int k) const {
        for (auto& kv : owner_) { auto it = kinds_.find(kv.first); if (it != kinds_.end() && int(it->second) == k) return true; }
        return false;
    }

    CompanionRules rules_;
    std::map<FormRef, CompanionKind> kinds_;
    std::map<FormRef, PlayerId> owner_;
    std::map<std::pair<FormRef, uint32_t>, Rule> rules_by_;
};

// VNPCFollowers (FalloutNV.esm:0B16D0) variable indices, verified against its quest script (tools/check_follower_vars.py).
namespace vnpc {
constexpr uint32_t kHumanoidInParty = 47, kCritterInParty = 48, kHasFollower = 18, kCurrentFollowers = 19;
struct Companion { const char* name; uint32_t hired, fired, available; CompanionKind kind; };
constexpr Companion kCompanions[] = {
    {"Boone", 6, 7, 8, CompanionKind::Humanoid},     {"Cass", 21, 22, 23, CompanionKind::Humanoid},
    {"Veronica", 27, 28, 29, CompanionKind::Humanoid}, {"Lily", 33, 34, 35, CompanionKind::Humanoid},
    {"EDE", 30, 31, 32, CompanionKind::Critter},     {"Raul", 9, 11, 10, CompanionKind::Humanoid},
    {"Arcade", 12, 14, 13, CompanionKind::Humanoid}, {"Rex", 15, 17, 16, CompanionKind::Critter},
};
}  // namespace vnpc

// Registers the VNPCFollowers rules; npcByName maps Boone/Cass/... to the companion NPC (base or ref) FormRef.
inline void registerVanillaFollowers(CompanionRegistry& reg, const FormRef& quest, const std::map<std::string, FormRef>& npcByName) {
    reg.addVarRule(quest, vnpc::kHumanoidInParty, VarKind::HumanoidInParty);
    reg.addVarRule(quest, vnpc::kCritterInParty, VarKind::CritterInParty);
    reg.addVarRule(quest, vnpc::kHasFollower, VarKind::HasFollower);
    reg.addVarRule(quest, vnpc::kCurrentFollowers, VarKind::CurrentFollowers);
    for (auto& c : vnpc::kCompanions) {
        auto it = npcByName.find(c.name);
        if (it == npcByName.end()) continue;
        reg.define(it->second, c.kind);
        reg.addVarRule(quest, c.hired, VarKind::Hired, it->second);
        reg.addVarRule(quest, c.fired, VarKind::Fired, it->second);
        reg.addVarRule(quest, c.available, VarKind::Available, it->second);
    }
}

// Owner-only topic list (generated by tools/gen_companion_topics.py): lines "ownerOnly <plugin>:<HEXID> <name>".
inline std::set<FormRef> loadOwnerOnlyTopics(std::istream& in) {
    std::set<FormRef> s;
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream ls(line);
        std::string kind, ref;
        if (!(ls >> kind >> ref) || kind != "ownerOnly") continue;
        size_t c = ref.rfind(':');
        if (c == std::string::npos) continue;
        std::string plugin = ref.substr(0, c);
        for (auto& ch : plugin) ch = char(tolower(static_cast<unsigned char>(ch)));
        s.insert(FormRef(plugin, uint32_t(std::stoul(ref.substr(c + 1), nullptr, 16))));
    }
    return s;
}

}  // namespace fnvcoop_dlg
