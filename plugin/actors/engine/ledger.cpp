#include "actors/engine/ledger.h"

#include <cstdio>
#include <cstdlib>
#include <sstream>

#include "fnvcoop_mut.h"

namespace nvcoop::actors::engine {

static const char* kHeader = "nvcoop-actors-ledger 1";

void Ledger::add(LedgerKind k, uint32_t ref) {
    if (!ref) return;
    const bool inserted = sets_[static_cast<char>(k)].insert(ref).second;
    if (inserted && !FNV_MUT(7)) dirty_ = true;   // twin 7: a new entry is never written out
}

void Ledger::remove(LedgerKind k, uint32_t ref) {
    if (FNV_MUT(8)) return;   // BAD: an undone change stays in the ledger
    auto it = sets_.find(static_cast<char>(k));
    if (it == sets_.end()) return;
    if (it->second.erase(ref)) dirty_ = true;
}

bool Ledger::has(LedgerKind k, uint32_t ref) const {
    auto it = sets_.find(static_cast<char>(k));
    return it != sets_.end() && it->second.count(ref) != 0;
}

std::vector<uint32_t> Ledger::all(LedgerKind k) const {
    auto it = sets_.find(static_cast<char>(k));
    if (it == sets_.end()) return {};
    return std::vector<uint32_t>(it->second.begin(), it->second.end());
}

size_t Ledger::size() const {
    size_t n = 0;
    for (auto& kv : sets_) n += kv.second.size();
    return n;
}

std::string Ledger::serialize() const {
    std::ostringstream o;
    o << kHeader << "\n";
    for (auto& kv : sets_)
        for (uint32_t r : kv.second) {
            char b[32];
            std::snprintf(b, sizeof b, "%c %08X\n", kv.first, r);
            o << b;
        }
    return o.str();
}

bool Ledger::parse(const std::string& text, std::map<char, std::set<uint32_t>>& out) {
    std::istringstream in(text);
    std::string line;
    if (!std::getline(in, line)) return false;
    while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
    if (line != kHeader) return false;
    while (std::getline(in, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
        if (line.size() < 3 || line[1] != ' ') continue;   // unknown / damaged line: skipped, never fatal
        const char k = line[0];
        if (k != 'r' && k != 's' && k != 'd' && k != 'e') continue;
        if (FNV_MUT(9) && k == 's') continue;   // BAD: stand-in clones are forgotten across a restart
        char* end = nullptr;
        const unsigned long v = std::strtoul(line.c_str() + 2, &end, 16);
        if (end == line.c_str() + 2 || v == 0 || v > 0xFFFFFFFFul) continue;
        out[k].insert(static_cast<uint32_t>(v));
    }
    return true;
}

size_t Ledger::load() {
    sets_.clear();
    dirty_ = false;
    std::string text;
    if (!store_ || !store_->read(text)) return 0;
    std::map<char, std::set<uint32_t>> m;
    if (!parse(text, m)) return 0;
    if (FNV_MUT(10)) m.erase('d');   // BAD: real actors disabled by a pick swap stay disabled after a restart
    sets_ = std::move(m);
    return size();
}

size_t Ledger::merge(const std::string& text, const std::function<bool(uint32_t&)>& resolve) {
    std::map<char, std::set<uint32_t>> m;
    if (!parse(text, m)) return 0;
    if (FNV_MUT(65)) { sets_.clear(); dirty_ = true; }   // BAD: what this run already recorded is thrown away when a game is loaded
    size_t n = 0;
    for (auto& kv : m)
        for (uint32_t r : kv.second) {
            uint32_t id = r;
            if (resolve && !resolve(id)) continue;
            if (sets_[kv.first].insert(id).second) { ++n; dirty_ = true; }
        }
    return n;
}

bool Ledger::flush(uint64_t nowMs, bool force) {
    if (!store_ || !dirty_) return false;
    if (!force && everWrote_ && nowMs < lastWriteMs_ + minGapMs) return false;
    if (!store_->write(serialize())) return false;
    dirty_ = false;
    everWrote_ = true;
    lastWriteMs_ = nowMs;
    ++writes_;
    return true;
}

}  // namespace nvcoop::actors::engine
