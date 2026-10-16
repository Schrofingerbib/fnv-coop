#include "fnvcoop/replicator.h"
#include <algorithm>

namespace fnvcoop {

uint64_t StateReplicator::hashOf(const Key& k) {
    uint64_t h = k.isGlobal ? 0x9E3779B97F4A7C15ull : 1469598103934665603ull;
    for (char c : k.form.plugin) { h ^= static_cast<uint8_t>(c); h *= 1099511628211ull; }
    h ^= k.form.localId; h *= 1099511628211ull;
    h ^= k.index; h *= 1099511628211ull;
    return h;
}

void StateReplicator::put(const Key& k, double v) {
    ++stats_.writes;
    uint64_t slot = hashOf(k);
    for (;; ++slot) {
        auto it = pending_.find(slot);
        if (it == pending_.end()) break;
        if (it->second.key.isGlobal == k.isGlobal && it->second.key.index == k.index && it->second.key.form == k.form) {
            it->second.value = v;
            ++stats_.coalesced;
            return;
        }
    }
    pending_.emplace(slot, Entry{k, v});
    order_.push_back(slot);
}

void StateReplicator::setScriptVar(const FormRef& owner, uint16_t index, double value) {
    if (volatileKeys().isVolatileVar(owner, index)) { ++stats_.volatileDropped; return; }
    Key k;
    k.form = owner;
    k.index = index;
    put(k, value);
}

void StateReplicator::setGlobal(const FormRef& global, float value) {
    if (volatileKeys().isVolatileGlobal(global)) { ++stats_.volatileDropped; return; }
    Key k;
    k.isGlobal = true;
    k.form = global;
    put(k, double(value));
}

uint64_t StateReplicator::flush(uint64_t nowMs) {
    if (!started_) { started_ = true; lastRefill_ = nowMs; lastFlush_ = nowMs - std::min<uint64_t>(nowMs, opt_.minIntervalMs); }
    tokens_ = std::min(double(opt_.maxEffectsPerFlush) * 2,
                       tokens_ + double(opt_.maxEffectsPerSec) * double(nowMs - lastRefill_) / 1000.0);
    lastRefill_ = nowMs;
    if (pending_.empty() || nowMs < lastFlush_ + opt_.minIntervalMs) return 0;

    const ShadowState& known = host_.state();
    std::vector<Effect> eff;
    while (!order_.empty() && eff.size() < opt_.maxEffectsPerFlush) {
        const uint64_t slot = order_.front();
        auto it = pending_.find(slot);
        const Key& k = it->second.key;
        const double value = it->second.value;
        bool same = false;
        if (k.isGlobal) {
            auto g = known.globals.find(k.form);
            same = g != known.globals.end() && double(g->second) == value;
        } else {
            auto v = known.scriptVars.find({k.form, k.index});
            same = v != known.scriptVars.end() && v->second == value;
        }
        if (same) {
            ++stats_.skippedUnchanged;
        } else {
            if (tokens_ < 1.0) { ++stats_.deferredByBudget; break; }
            tokens_ -= 1.0;
            if (k.isGlobal) eff.push_back(SetGlobal{k.form, static_cast<float>(value)});
            else eff.push_back(SetScriptVar{k.form, k.index, value});
        }
        order_.pop_front();
        pending_.erase(it);
    }
    lastFlush_ = nowMs;
    if (eff.empty()) return 0;
    Cause c;
    c.kind = CauseKind::Script;
    uint64_t seq = host_.commit(kHostPlayer, c, std::move(eff));
    if (seq) {
        ++stats_.txns;
        stats_.effectsCommitted += host_.log().get(seq)->effects.size();
    }
    return seq;
}

}  // namespace fnvcoop
