// Ledger of the engine state this port changed on REAL game objects, persisted to a small text file so that a save made while the state is in
// place cannot leave it behind:
//   Restrained : actors whose AI the port switched off (SetRestrained 1). A save keeps the flag, a later single-player load would freeze them.
//   StandIn    : clone references the port created (stand-ins and leveled-pick clones). A save keeps them as extra NPCs.
//   Disabled   : real actors the port disabled because a pick clone replaced them. A save keeps them disabled.
//   Essential  : actors given the reference-level essential flag as protection while their base is not essential.
// On the next game start / load / session end the port walks the ledger and undoes every entry whose reference is loaded (an entry whose
// reference is not loaded stays until it is). Game-free: the file is reached through IFileStore.
#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace nvcoop::actors::engine {

enum class LedgerKind : char { Restrained = 'r', StandIn = 's', Disabled = 'd', Essential = 'e' };

class IFileStore {
public:
    virtual ~IFileStore() = default;
    virtual bool read(std::string& out) = 0;          // false = no file
    virtual bool write(const std::string& text) = 0;  // false = could not write
};

class Ledger {
public:
    explicit Ledger(IFileStore* store = nullptr) : store_(store) {}
    void add(LedgerKind k, uint32_t ref);
    void remove(LedgerKind k, uint32_t ref);
    bool has(LedgerKind k, uint32_t ref) const;
    std::vector<uint32_t> all(LedgerKind k) const;
    size_t size() const;
    bool empty() const { return size() == 0; }
    // Writes the file when something changed and `nowMs` is at least minGapMs after the last write (or force). Returns true when written.
    bool flush(uint64_t nowMs, bool force = false);
    // Replaces the content with the file's. Returns the number of entries read.
    size_t load();
    std::string serialize() const;
    // Adds the entries of a serialized ledger (the cosave of a loaded game) to this one; nothing already here is dropped. `resolve` maps a
    // saved ref id to the current one (a changed load order) and returns false to skip the entry. Returns the number of entries added.
    size_t merge(const std::string& text, const std::function<bool(uint32_t&)>& resolve = nullptr);
    static bool parse(const std::string& text, std::map<char, std::set<uint32_t>>& out);
    uint64_t writes() const { return writes_; }
    bool dirty() const { return dirty_; }
    uint32_t minGapMs = 1000;

private:
    IFileStore* store_;
    std::map<char, std::set<uint32_t>> sets_;
    bool dirty_ = false;
    bool everWrote_ = false;
    uint64_t lastWriteMs_ = 0, writes_ = 0;
};

}  // namespace nvcoop::actors::engine
