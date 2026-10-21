// Share policy (owner decisions locked 2026-10-15) and commit-time normalization.
#pragma once
#include <cstddef>
#include <functional>
#include <set>
#include <string>
#include "fnvcoop/effect.h"
#include "fnvcoop/mutations.h"

namespace fnvcoop {

struct SharePolicy {
    bool journal = true;            // SetStage / SetObjective / Start/StopQuest are world-shared
    bool globals = true;            // SetGlobal shared (host wins)
    bool kills = true;              // KillActor / ResurrectActor shared
    bool karma = true;              // party = one Courier: karma delta goes to every player
    bool reputation = true;         // faction reputation delta goes to every player
    bool questRewardsToAll = true;  // quest/dialogue-result items, caps, XP go to every player in full
    // What a CLIENT may do with its own save games while in a session (progress is the host's; a client save only
    // carries that player's own state): Rejoin = loading is allowed and the client reconciles to the host's world,
    // Block = the engine layer must refuse client loads (clientLoadAllowed()).
    enum class ClientLoad : uint8_t { Rejoin = 0, Block = 1 };
    ClientLoad clientLoad = ClientLoad::Rejoin;
    uint8_t maxPlayers = 4;         // slots 0..maxPlayers-1; "All" expands to these

    static SharePolicy defaults() { return {}; }
    uint8_t allMask() const { return static_cast<uint8_t>(maxPlayers >= 8 ? 0xFFu : (1u << maxPlayers) - 1u); }
    bool operator==(const SharePolicy& o) const {
        return journal == o.journal && globals == o.globals && kills == o.kills && karma == o.karma &&
               reputation == o.reputation && questRewardsToAll == o.questRewardsToAll && maxPlayers == o.maxPlayers &&
               clientLoad == o.clientLoad;
    }
};

// True for causes whose item/AV effects count as quest rewards.
bool isQuestRewardCause(CauseKind k);

// Volatile keys: variables/globals that change constantly or are purely local (timers, radio state, frame counters).
// They are NEVER baselined and NEVER replicated (dropped by normalizeTxn, skipped by baselineEntries and
// StateReplicator). One process-wide list so every lane (L5/L7/L10/dialogue) shares it; populate once at startup on
// BOTH host and clients (the lists must match, or the baseline sees client-only/host-only entries).
class VolatileKeys {
public:
    void addVar(const FormRef& owner, uint16_t index) { vars_.insert({owner, index}); }
    void addOwner(const FormRef& owner) { owners_.insert(owner); }   // every var of this quest/ref
    void addGlobal(const FormRef& global) { globals_.insert(global); }
    void addField(const FormRef& quest, uint8_t field) { fields_.insert({quest, field}); }
    void addFaction(const FormRef& faction) { factions_.insert(faction); }   // ranks in it are per-machine (DLC player factions)
    void clear() { vars_.clear(); owners_.clear(); globals_.clear(); names_.clear(); declaredNames_.clear(); fromName_.clear(); fields_.clear(); factions_.clear(); }
    uint64_t contentHash() const;   // order-independent hash of every entry (handshake)
    size_t size() const { return vars_.size() + owners_.size() + globals_.size() + names_.size() + fields_.size() + factions_.size(); }
    static constexpr uint8_t kFieldRunning = 1, kFieldStage = 2, kFieldObjectives = 3;
    // Named-variable entries ("var <form> <name>") become index entries once the engine can map names to indexes.
    // Returns how many were resolved; unresolved ones stay pending (see unresolvedNames()).
    size_t resolveNames(const std::function<bool(const FormRef& owner, const std::string& name, uint16_t& index)>& resolver);
    size_t unresolvedNames() const { return names_.size(); }
    // Only SetScriptVar and SetGlobal can be volatile.
    bool isVolatile(const Effect& e) const;
    bool isVolatileVar(const FormRef& owner, uint16_t index) const;
    bool isVolatileGlobal(const FormRef& g) const;
    // Text format (shared with L7's out/snapshot/volatile_keys.txt), '#' comments, <form> = `Plugin.esm:HEX` or `Plugin.esm HEX`:
    //   var <form> <index|name>   owner <form>   global <form>   field <form> running|stage|objectives   faction <form>
    // Whole-quest exclusion for story quests is the producer's job (L7's tool enforces it); `owner` here is for non-story owners.
    bool loadFromText(const std::string& text, std::string* err = nullptr);
private:
    std::set<std::pair<FormRef, uint16_t>> vars_;
    std::set<FormRef> owners_, globals_;
    std::set<std::pair<FormRef, std::string>> names_;         // named vars still waiting for resolveNames()
    std::set<std::pair<FormRef, std::string>> declaredNames_; // every named declaration ever loaded (stable handshake hash)
    std::set<std::pair<FormRef, uint16_t>> fromName_;         // vars_ entries that came from resolving a name
    std::set<std::pair<FormRef, uint8_t>> fields_;
    std::set<FormRef> factions_;
};
VolatileKeys& volatileKeys();

constexpr size_t kNormalizeInvalid = static_cast<size_t>(-1);

// Rewrites a txn in place: drops effects for categories the policy does not share, and resolves every
// Recipient (Default/Origin/All) into an explicit Mask. Returns number of effects dropped,
// or kNormalizeInvalid if the txn origin is invalid (nothing should be committed).
size_t normalizeTxn(EffectTxn& txn, const SharePolicy& policy);

// Slot mask for a recipient (All = every slot; Default/Origin = origin only). Normalized txns only contain Mask.
uint8_t recipientMask(const Recipient& r, PlayerId origin);

// ---- per-reward identity (restart-rejoin without duplicates) ----
// A reward is an additive per-player effect of a committed txn (items and actor values; karma / reputation are synced as absolutes).
// Its id is derived from where it was committed, so the host, the receiving client and the client's save sidecar all compute the same
// value without any extra wire field: (world epoch, txn seq, effect index). A new epoch can never collide with an older one.
inline uint64_t rewardId(uint32_t epoch, uint64_t seq, uint32_t index) {
    return (uint64_t(epoch & 0xFFFFu) << 48) | ((seq & 0xFFFFFFFFull) << 16) | (index & 0xFFFFu);
}
inline bool isRewardEffect(const Effect& e) {
    return std::holds_alternative<AddItem>(e) || std::holds_alternative<RemoveItem>(e) || std::holds_alternative<ModActorValue>(e);
}
struct RewardGrant {
    uint64_t id = 0;
    Effect effect;   // addressed to exactly one slot
};
// fn(slot, id, effect) for every reward effect of a normalized (committed) txn, once per recipient slot.
template <class F>
void forEachReward(const EffectTxn& t, uint32_t epoch, F&& fn) {
    for (size_t i = 0; i < t.effects.size(); ++i) {
        const Effect& e = t.effects[i];
        if (!isRewardEffect(e)) continue;
        const Recipient* to = nullptr;
        if (auto* a = std::get_if<AddItem>(&e)) to = &a->to;
        else if (auto* r = std::get_if<RemoveItem>(&e)) to = &r->to;
        else if (auto* m = std::get_if<ModActorValue>(&e)) to = &m->to;
        const uint8_t mask = recipientMask(*to, t.origin);
        for (unsigned s = 0; s < kMaxSlots; ++s) {
            if (!(mask >> s & 1u)) continue;
            Effect one = e;
            if (auto* a = std::get_if<AddItem>(&one)) a->to = Recipient::slot(PlayerId(s));
            else if (auto* r = std::get_if<RemoveItem>(&one)) r->to = Recipient::slot(PlayerId(s));
            else if (auto* m = std::get_if<ModActorValue>(&one)) m->to = Recipient::slot(PlayerId(s));
            fn(PlayerId(s), rewardId(epoch, t.seq, uint32_t(i)), one);
        }
    }
}

}  // namespace fnvcoop
