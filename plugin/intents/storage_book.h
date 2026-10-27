// Per-player gear stashes on shared storage containers (CAMPAIGN-GAPS G35). The Fort's confiscation, the Dead Money entry strip, the Gomorrah / Tops /
// Ultra-Luxe friskings and the Honest Hearts gear boxes keep every player's gear in ONE container reference that the scripts name by id
// (NVCCPlayerStorageREF, FortPlayerEquipmentContainerRef ...). With two players the container mixes the gear and the restore hands it all to whoever
// triggers it. There is no spare container form to redirect to (container_redirect.h needs one per slot), so the shared container stays the only
// one and the HOST swaps its contents around every guest interaction:
//
//   a guest intent runs      : the container holds exactly that guest's stash (what it put there earlier), whatever else was in it is parked
//   the intent has finished  : what the container holds now is that guest's stash again, the parked contents are put back
//
// The scripts therefore see "their" gear and nobody else's, with no change to the scripts. The host's own player (slot 0) is not swapped: the container's
// real contents are the host's. Data, not code: nvcoop_intents.json, key "stashes":
//
//   {"stashes":[{"name":"NVCCPlayerStorageREF","target":"falloutnv.esm:139B6B","what":"Fort gear"}]}
//
// Game-free. The engine side (reading and replacing a container's contents without capturing it) is IIntentEngine::readItems / replaceItems.
#pragma once
#include <map>
#include <string>
#include <vector>

#include "intents/intent_engine.h"

namespace nvcoop::intents {

class StorageBook {
public:
    struct Entry {
        FormRef container;
        std::string name;   // the EditorID, informational (logs, status)
        std::string what;   // informational
    };

    // Replaces the list of shared containers (the ledger is kept). On error the old list stays.
    bool load(const std::string& jsonText, std::string* err);
    bool isStash(const FormRef& c) const;
    const std::vector<Entry>& entries() const { return entries_; }
    bool empty() const { return entries_.empty(); }

    // ---- the ledger ----
    // `slot`'s stash of `c` (empty when it has none). take() removes it from the ledger, put() stores it (an empty vector forgets the slot).
    std::vector<StoredItem> take(const FormRef& c, PlayerId slot);
    void put(const FormRef& c, PlayerId slot, std::vector<StoredItem> items);
    const std::vector<StoredItem>* peek(const FormRef& c, PlayerId slot) const;
    void forgetSlot(PlayerId slot);   // a player who left for good: only called when asked (a rejoin keeps its gear)
    void clear() { stash_.clear(); dirty_ = true; }
    size_t stashes() const;                 // (container, slot) pairs with something in them
    int64_t totalItems(PlayerId slot) const;

    // ---- persistence (the host's save / reload) ----
    std::string stateJson() const;
    bool loadState(const std::string& jsonText, std::string* err);
    bool dirty() const { return dirty_; }
    void markClean() { dirty_ = false; }

private:
    std::vector<Entry> entries_;
    std::map<FormRef, std::map<PlayerId, std::vector<StoredItem>>> stash_;
    bool dirty_ = false;
};

// Merges duplicate lines and drops zero or negative counts: the canonical form of a contents list.
std::vector<StoredItem> normalizeItems(std::vector<StoredItem> items);

}  // namespace nvcoop::intents
