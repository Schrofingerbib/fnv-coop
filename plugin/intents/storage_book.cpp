#include "intents/storage_book.h"

#include <algorithm>
#include <cstdio>

#include "common/minijson.h"
#include "fnvcoop_mut.h"
#include "intents/container_redirect.h"

namespace nvcoop::intents {

namespace {

std::string refText(const FormRef& r) {
    char buf[16];
    std::snprintf(buf, sizeof buf, ":%X", static_cast<unsigned>(r.localId));
    return r.plugin + buf;
}

}  // namespace

std::vector<StoredItem> normalizeItems(std::vector<StoredItem> items) {
    std::map<FormRef, int64_t> sum;
    for (const StoredItem& i : items) sum[i.item] += i.count;
    std::vector<StoredItem> out;
    for (const auto& kv : sum)
        if (kv.second > 0) out.push_back({kv.first, static_cast<int32_t>(std::min<int64_t>(kv.second, 0x7FFFFFFF))});
    return out;
}

bool StorageBook::load(const std::string& jsonText, std::string* err) {
    nvcoop::json::Value root;
    std::string perr;
    if (!nvcoop::json::parse(jsonText, root, perr)) { if (err) *err = "invalid json: " + perr; return false; }
    std::vector<Entry> list;
    for (const auto& e : root.get("stashes").items()) {
        Entry en;
        if (!parseFormRef(e.get("target").asString(""), &en.container)) { if (err) *err = "stash: bad target '" + e.get("target").asString("") + "'"; return false; }
        for (const Entry& o : list)
            if (o.container == en.container) { if (err) *err = "stash: " + e.get("target").asString("") + " is listed twice"; return false; }
        en.name = e.get("name").asString("");
        en.what = e.get("what").asString("");
        list.push_back(std::move(en));
    }
    entries_ = std::move(list);
    return true;
}

bool StorageBook::isStash(const FormRef& c) const {
    for (const Entry& e : entries_)
        if (e.container == c) return true;
    return false;
}

std::vector<StoredItem> StorageBook::take(const FormRef& c, PlayerId slot) {
    auto it = stash_.find(c);
    if (it == stash_.end()) return {};
    auto s = it->second.find(slot);
    if (s == it->second.end()) return {};
    std::vector<StoredItem> v = std::move(s->second);
    it->second.erase(s);
    if (it->second.empty()) stash_.erase(it);
    dirty_ = true;
    return v;
}

void StorageBook::put(const FormRef& c, PlayerId slot, std::vector<StoredItem> items) {
    items = normalizeItems(std::move(items));
    if (FNV_MUT(401)) slot = kHostPlayer;   // MUTANT 401: every guest's gear is filed under one slot (the shared container again)
    if (items.empty()) {
        auto it = stash_.find(c);
        if (it != stash_.end() && it->second.erase(slot)) {
            dirty_ = true;
            if (it->second.empty()) stash_.erase(it);
        }
        return;
    }
    stash_[c][slot] = std::move(items);
    dirty_ = true;
}

const std::vector<StoredItem>* StorageBook::peek(const FormRef& c, PlayerId slot) const {
    auto it = stash_.find(c);
    if (it == stash_.end()) return nullptr;
    auto s = it->second.find(slot);
    return s == it->second.end() ? nullptr : &s->second;
}

void StorageBook::forgetSlot(PlayerId slot) {
    for (auto it = stash_.begin(); it != stash_.end();) {
        if (it->second.erase(slot)) dirty_ = true;
        if (it->second.empty()) it = stash_.erase(it);
        else ++it;
    }
}

size_t StorageBook::stashes() const {
    size_t n = 0;
    for (const auto& kv : stash_) n += kv.second.size();
    return n;
}

int64_t StorageBook::totalItems(PlayerId slot) const {
    int64_t n = 0;
    for (const auto& kv : stash_) {
        auto s = kv.second.find(slot);
        if (s == kv.second.end()) continue;
        for (const StoredItem& i : s->second) n += i.count;
    }
    return n;
}

std::string StorageBook::stateJson() const {
    nvcoop::json::Value arr = nvcoop::json::Value::array();
    for (const auto& kv : stash_)
        for (const auto& s : kv.second) {
            nvcoop::json::Value items = nvcoop::json::Value::array();
            for (const StoredItem& i : s.second) {
                nvcoop::json::Value pair = nvcoop::json::Value::array();
                pair.push(refText(i.item));
                pair.push(static_cast<int64_t>(i.count));
                items.push(std::move(pair));
            }
            nvcoop::json::Value o = nvcoop::json::Value::object();
            o.set("container", refText(kv.first)).set("slot", static_cast<int64_t>(s.first)).set("items", std::move(items));
            arr.push(std::move(o));
        }
    nvcoop::json::Value root = nvcoop::json::Value::object();
    root.set("version", 1).set("stash", std::move(arr));
    return root.dump();
}

bool StorageBook::loadState(const std::string& jsonText, std::string* err) {
    nvcoop::json::Value root;
    std::string perr;
    if (!nvcoop::json::parse(jsonText, root, perr)) { if (err) *err = "invalid json: " + perr; return false; }
    if (root.get("version").asInt(0) != 1) { if (err) *err = "unknown stash state version"; return false; }
    std::map<FormRef, std::map<PlayerId, std::vector<StoredItem>>> t;
    for (const auto& e : root.get("stash").items()) {
        FormRef c;
        if (!parseFormRef(e.get("container").asString(""), &c)) { if (err) *err = "stash state: bad container"; return false; }
        int64_t slot = e.get("slot").asInt(-1);
        if (slot < 0 || slot >= fnvcoop::kMaxSlots) { if (err) *err = "stash state: bad slot"; return false; }
        std::vector<StoredItem> items;
        for (const auto& p : e.get("items").items()) {
            StoredItem i;
            if (p.items().size() != 2 || !parseFormRef(p.items()[0].asString(""), &i.item)) { if (err) *err = "stash state: bad item"; return false; }
            i.count = static_cast<int32_t>(p.items()[1].asInt(0));
            items.push_back(i);
        }
        items = normalizeItems(std::move(items));
        if (!items.empty()) t[c][static_cast<PlayerId>(slot)] = std::move(items);
    }
    stash_ = std::move(t);
    dirty_ = false;
    return true;
}

}  // namespace nvcoop::intents
