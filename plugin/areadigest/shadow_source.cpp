#include "areadigest/shadow_source.h"

#include <algorithm>
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"

namespace nvcoop::areadigest {

using namespace fnvcoop::areadigest;
using fnvcoop::FormRef;
using fnvcoop::hash64;

Key keyOfRef(const FormRef& r) {
    std::string p = r.plugin;
    for (char& c : p) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    uint64_t h = hash64(p.data(), p.size());
    h = hash64(&r.localId, sizeof r.localId, h);
    return h ? h : 1;
}

namespace {

uint32_t fold(uint64_t h) { return static_cast<uint32_t>(h ^ (h >> 32)); }

}  // namespace

ShadowRefSource::ShadowRefSource(Cat cat, WorldGetter world, CellOfRef cellOf, const Config& cfg)
    : cat_(cat), world_(std::move(world)), cellOf_(std::move(cellOf)), tr_(cfg) {}

void ShadowRefSource::build(std::vector<Item>& out) const {
    const fnvcoop::ShadowState* w = world_ ? world_() : nullptr;
    if (!w) return;
    auto cellFor = [&](const FormRef& r) {
        CellId c = 0;
        if (cellOf_) cellOf_(r, c);
        return c;
    };
    if (cat_ == Cat::Containers) {
        for (const auto& kv : w->refInv) {
            const fnvcoop::RefInv& inv = kv.second;
            uint64_t h = hash64(&inv.cleared, sizeof inv.cleared);
            if (!FNV_MUT(73)) {   // twin 73: the contents are not part of the state (a missing item is invisible)
                for (const auto& d : inv.delta) {
                    h = hash64(&d.second, sizeof d.second, h ^ keyOfRef(d.first));
                }
            }
            Item it;
            it.key = keyOfRef(kv.first);
            it.cat = Cat::Containers;
            it.cell = cellFor(kv.first);
            it.alive = true;
            it.state = fold(h);
            out.push_back(it);
        }
    } else if (cat_ == Cat::Objects) {
        std::map<FormRef, Item> byRef;
        auto itemFor = [&](const FormRef& r) -> Item& {
            auto f = byRef.find(r);
            if (f == byRef.end()) {
                Item it;
                it.key = keyOfRef(r);
                it.cat = Cat::Objects;
                it.cell = cellFor(r);
                f = byRef.emplace(r, it).first;
            }
            return f->second;
        };
        for (const auto& kv : w->objects) {
            Item& it = itemFor(kv.first);
            const uint8_t bits = static_cast<uint8_t>((kv.second.open ? 1 : 0) | (kv.second.activated ? 4 : 0));
            it.state ^= fold(hash64(&bits, 1, 0x0b1ec7ull));
            if (kv.second.destroyed && !FNV_MUT(74)) it.alive = false;   // twin 74: destroyed is ignored
        }
        for (const auto& kv : w->locks) {
            Item& it = itemFor(kv.first);
            if (FNV_MUT(72)) continue;                                      // twin 72: lock changes are invisible
            const int32_t lv[2] = {kv.second.first ? 1 : 0, kv.second.second};
            it.state ^= fold(hash64(lv, sizeof lv, 0x10c4ull));
        }
        for (auto& kv : byRef) out.push_back(kv.second);
    }
}

void ShadowRefSource::collect(uint64_t nowMs, std::vector<Item>& items, std::vector<CellId>& loadedCells) {
    if (!world_ || !world_()) return;
    std::vector<Item> mine;
    build(mine);
    tr_.update(mine, nowMs);
    loadedCells.push_back(0);   // the shadow knows every reference: cell 0 ("everywhere") is always loaded
    for (Item& it : mine) {
        loadedCells.push_back(it.cell);
        items.push_back(it);
    }
}

bool ShadowRefSource::find(Key key, Item& out, uint64_t nowMs) {
    std::vector<Item> mine;
    build(mine);
    for (Item& it : mine) {
        if (it.key != key) continue;
        out = it;
        tr_.refresh(out, nowMs);
        return true;
    }
    return false;
}

bool ShadowStorySource::local(StoryDigest& out) {
    World w;
    if (!world || !world(w) || !w.state) return false;
    out = storyDigestOf(*w.state, w.seq, w.epoch);
    return true;
}

}  // namespace nvcoop::areadigest
