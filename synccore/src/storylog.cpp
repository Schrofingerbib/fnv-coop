#include <algorithm>
#include "fnvcoop/storylog.h"
#include "fnvcoop/wire.h"

namespace fnvcoop {

uint64_t StoryLog::append(EffectTxn txn) {
    txn.seq = head_.seq + 1;
    head_.applyTxn(txn);
    uint64_t s = txn.seq;
    if (s % kCheckpointEvery == 0) {   // cheap-ish (one copy per 128 txns); makes stateAt() O(checkpoint spacing) instead of O(log)
        checkpoints_.emplace_back(s, std::make_shared<const ShadowState>(head_));
        if (checkpoints_.size() > kMaxCheckpoints) checkpoints_.erase(checkpoints_.begin());
    }
    txns_.push_back(std::move(txn));
    return s;
}

bool StoryLog::setInitialState(const ShadowState& s) {
    if (!txns_.empty() || head_.seq != 0 || base_.seq != 0) return false;
    base_ = s;
    base_.seq = 0;
    head_ = base_;
    checkpoints_.clear();
    return true;
}

bool StoryLog::stateAt(uint64_t seq, ShadowState& out) const {
    if (seq < base_.seq || seq > head_.seq) return false;
    const ShadowState* start = &base_;
    uint64_t from = base_.seq;
    for (auto it = checkpoints_.rbegin(); it != checkpoints_.rend(); ++it)
        if (it->first <= seq && it->first >= base_.seq) { start = it->second.get(); from = it->first; break; }
    out = *start;
    for (uint64_t s = from + 1; s <= seq; ++s) out.applyTxn(txns_[static_cast<size_t>(s - base_.seq - 1)]);
    return true;
}

const EffectTxn* StoryLog::get(uint64_t seq) const {
    if (seq < firstSeq() || seq > head_.seq) return nullptr;
    return &txns_[static_cast<size_t>(seq - firstSeq())];
}

std::vector<EffectTxn> StoryLog::range(uint64_t afterSeq, size_t maxCount) const {
    std::vector<EffectTxn> r;
    uint64_t s = afterSeq + 1;
    if (s < firstSeq()) return r;
    for (; s <= head_.seq && r.size() < maxCount; ++s) r.push_back(txns_[static_cast<size_t>(s - firstSeq())]);
    return r;
}

void StoryLog::compact(size_t keep) {
    while (txns_.size() > keep) {
        base_.applyTxn(txns_.front());
        txns_.pop_front();
    }
    checkpoints_.erase(std::remove_if(checkpoints_.begin(), checkpoints_.end(), [&](const auto& c) { return c.first < base_.seq; }), checkpoints_.end());
}

std::vector<uint8_t> StoryLog::save() const {
    Archive a = Archive::writer();
    uint8_t ver = kWireVersion;
    a.u8(ver);
    ShadowState b = base_;
    io(a, b);
    size_t n = txns_.size();
    a.count(n, ~size_t(0) >> 1);
    for (auto t : txns_) io(a, t);
    return a.finish();
}

bool StoryLog::load(const uint8_t* data, size_t size) {
    Archive a = Archive::reader(data, size);
    uint8_t ver = 0;
    a.u8(ver);
    if (!a.ok() || ver != kWireVersion) return false;
    StoryLog fresh;
    io(a, fresh.base_);
    size_t n = 0;
    if (!a.count(n, ~size_t(0) >> 1)) return false;
    fresh.head_ = fresh.base_;
    for (size_t i = 0; i < n && a.ok(); ++i) {
        EffectTxn t;
        io(a, t);
        if (!a.ok() || t.seq != fresh.head_.seq + 1) return false;
        fresh.head_.applyTxn(t);
        fresh.txns_.push_back(std::move(t));
    }
    if (!a.ok() || !a.atEnd()) return false;
    *this = std::move(fresh);
    return true;
}

}  // namespace fnvcoop
