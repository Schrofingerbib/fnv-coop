#include "guest_local_run.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>

#include "common/minijson.h"

namespace nvhooks {

bool parseGuestLocalRun(const std::string& jsonText, GuestLocalRunList* out, std::string* err) {
    nvcoop::json::Value root;
    std::string perr;
    if (!nvcoop::json::parse(jsonText, root, perr)) {
        if (err) *err = perr;
        return false;
    }
    const nvcoop::json::Value* arr = root.find("scripts");
    if (!arr || arr->type() != nvcoop::json::Value::Array) {
        if (err) *err = "missing \"scripts\" array";
        return false;
    }
    GuestLocalRunList list;
    for (const auto& e : arr->items()) {
        std::string ref = e.get("script").asString("");
        size_t colon = ref.rfind(':');
        if (colon == std::string::npos || colon == 0 || colon + 1 >= ref.size()) { ++list.skipped; continue; }
        char* end = nullptr;
        unsigned long id = std::strtoul(ref.c_str() + colon + 1, &end, 16);
        if (*end != '\0' || id == 0) { ++list.skipped; continue; }
        std::string plugin = ref.substr(0, colon);
        std::transform(plugin.begin(), plugin.end(), plugin.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        list.scripts.push_back(fnvcoop::FormRef(plugin, static_cast<uint32_t>(id)));
        list.quests.push_back(e.get("quest").asString(""));
        list.reasons.push_back(e.get("reason").asString(""));
    }
    if (out) *out = std::move(list);
    return true;
}

}  // namespace nvhooks
