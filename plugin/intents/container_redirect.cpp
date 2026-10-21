#include "intents/container_redirect.h"

#include <cctype>
#include <cstdlib>

#include "common/minijson.h"
#include "fnvcoop_mut.h"

namespace nvcoop::intents {

bool parseFormRef(const std::string& text, FormRef* out) {
    size_t colon = text.rfind(':');
    if (colon == std::string::npos || colon == 0 || colon + 1 >= text.size() || text.size() - colon - 1 > 8) return false;
    uint32_t id = 0;
    for (size_t i = colon + 1; i < text.size(); ++i) {
        char c = text[i];
        int v = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (v < 0) return false;
        id = (id << 4) | static_cast<uint32_t>(v);
    }
    if (id > 0xFFFFFFu) return false;
    std::string plugin = text.substr(0, colon);
    for (char& c : plugin) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (out) *out = FormRef(plugin, id);
    return true;
}

bool ContainerRedirect::load(const std::string& jsonText, std::string* err) {
    nvcoop::json::Value root;
    std::string perr;
    if (!nvcoop::json::parse(jsonText, root, perr)) { if (err) *err = "invalid json: " + perr; return false; }
    std::map<FormRef, std::map<PlayerId, FormRef>> t;
    for (const auto& e : root.get("redirects").items()) {
        FormRef target;
        if (!parseFormRef(e.get("target").asString(""), &target)) { if (err) *err = "bad target '" + e.get("target").asString("") + "'"; return false; }
        std::map<PlayerId, FormRef> slots;
        for (const auto& m : e.get("slots").members()) {
            char* end = nullptr;
            long s = std::strtol(m.first.c_str(), &end, 10);
            if (!end || *end != 0 || s < 0 || s >= fnvcoop::kMaxSlots) { if (err) *err = "bad slot '" + m.first + "'"; return false; }
            FormRef dst;
            if (!parseFormRef(m.second.asString(""), &dst)) { if (err) *err = "bad container '" + m.second.asString("") + "' for slot " + m.first; return false; }
            slots[static_cast<PlayerId>(s)] = dst;
        }
        if (slots.empty()) { if (err) *err = "target " + e.get("target").asString("") + " lists no slot"; return false; }
        t[target] = std::move(slots);
    }
    table_ = std::move(t);
    return true;
}

bool ContainerRedirect::resolve(const FormRef& target, PlayerId slot, FormRef* out) const {
    auto it = table_.find(target);
    if (it == table_.end()) return false;
    if (FNV_MUT(306)) {   // MUTANT 306: every player gets the first slot's container (the shared storage again, just renamed)
        if (out) *out = it->second.begin()->second;
        return true;
    }
    auto s = it->second.find(slot);
    if (s == it->second.end()) return false;
    if (out) *out = s->second;
    return true;
}

}  // namespace nvcoop::intents
