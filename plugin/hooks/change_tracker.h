// Remembers the last value reported per key; update() says whether a new value must be reported.
// Used by the host state poll (quest script vars, globals) so unchanged values are never re-sent.
#pragma once
#include <cstdint>
#include <unordered_map>

namespace nvhooks {

class ChangeTracker {
public:
    // true on first sight of `key` or when the value differs from the last reported one.
    bool update(uint64_t key, double value);
    void clear() { last_.clear(); }
    size_t size() const { return last_.size(); }

    static uint64_t varKey(uint32_t ownerRefID, uint16_t index) { return (uint64_t(ownerRefID) << 16) | index; }
    static uint64_t globalKey(uint32_t refID) { return (uint64_t(1) << 63) | refID; }

private:
    std::unordered_map<uint64_t, double> last_;
};

}  // namespace nvhooks
