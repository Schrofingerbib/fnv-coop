#include "client_patcher.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>

namespace nvmp::client {

namespace {
std::string Lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return (char)std::tolower(c); });
    return s;
}
std::string Trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && std::isspace((unsigned char)s[a])) ++a;
    while (b > a && std::isspace((unsigned char)s[b - 1])) --b;
    return s.substr(a, b - a);
}
bool Truthy(const std::string& v) {
    std::string l = Lower(Trim(v));
    return l == "1" || l == "true" || l == "yes" || l == "on";
}
std::string Esc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if ((unsigned char)c < 0x20) o += ' ';
        else o += c;
    }
    return o;
}
std::string Hex(uint32_t v) {
    char b[16];
    std::snprintf(b, sizeof b, "0x%X", v);
    return b;
}
}  // namespace

std::vector<std::string> ExpandName(const std::string& n0) {
    const std::string n = Trim(n0);
    if (FindPatch(n) && FindPatch(n)->module == "client.dll") return {n};
    static const std::map<std::string, std::vector<std::string>> kShort = {
        {"quested", {"client.quested_not_localized"}},
        {"questedref", {"client.quested_ref_not_localized"}},
        {"questedrefscan", {"client.quested_ref_scan_include"}},
        {"scriptedref", {"client.scripted_ref_not_localized"}},
        {"ghostdamage", {"client.ghost_damage_locality_byte", "client.ghost_damage_flag31"}},
    };
    auto it = kShort.find(Lower(n));
    return it == kShort.end() ? std::vector<std::string>{} : it->second;
}

Settings ParseSettings(const ConfigGetter& get, std::vector<std::string>* warnings) {
    Settings s;
    auto warn = [&](const std::string& m) { if (warnings) warnings->push_back(m); };
    auto flag = [&](const char* key, bool def) {
        std::string v = get(key, def ? "1" : "0");
        for (const auto& full : ExpandName(key))
            if (Truthy(v)) s.enabled.insert(full);
    };
    flag("quested", true);
    flag("questedRef", false);
    flag("questedRefScan", false);
    flag("scriptedRef", false);
    flag("ghostDamage", false);
    std::string list = get("patches", "");
    if (!Trim(list).empty()) {
        s.enabled.clear();   // an explicit list replaces the flags
        size_t pos = 0;
        while (pos <= list.size()) {
            size_t c = list.find(',', pos);
            std::string item = Trim(list.substr(pos, c == std::string::npos ? std::string::npos : c - pos));
            pos = c == std::string::npos ? list.size() + 1 : c + 1;
            if (item.empty() || Lower(item) == "none") continue;
            auto names = ExpandName(item);
            if (names.empty()) warn("unknown patch in [nvmp] patches=: " + item);
            for (auto& n : names) s.enabled.insert(n);
        }
    }
    if (Lower(Trim(get("enabled", "1"))) == "0" || Lower(Trim(get("enabled", "1"))) == "false") s.enabled.clear();
    s.autoApply = Truthy(get("autoApply", "1"));
    try { s.pollEveryFrames = std::max(1, std::stoi(get("pollFrames", "60"))); } catch (...) { warn("bad pollFrames"); }
    return s;
}

ClientPatcher::ClientPatcher(Settings s, ModuleSource& mods, MemoryWriter& writer, EventSink sink)
    : s_(std::move(s)), mods_(mods), writer_(writer), sink_(std::move(sink)) {
    for (const auto& n : s_.enabled) recs_[n];
}

bool ClientPatcher::FindClient(ModuleView& v) {
    return mods_.Find("client.dll", v) && v.base && v.size > 0x1000;
}

PatchStatus ClientPatcher::DoApply(const PatchDef& d, uint32_t* rvaOut) {
    Rec& r = recs_[d.name];
    PatchState st;
    PatchStatus s = ApplyPatch(d, mod_.base, mod_.size, writer_, st);
    r.status = s;
    if (s == PatchStatus::Applied || s == PatchStatus::AlreadyApplied) {
        r.state = std::move(st);
        r.rva = r.state.rva;
    } else {
        // locate for the report even when we refuse to write
        uint32_t rva = 0;
        CheckPatch(d, mod_.base, mod_.size, &rva);
        r.rva = rva;
    }
    if (rvaOut) *rvaOut = r.rva;
    return s;
}

void ClientPatcher::ApplyAllEnabled() {
    int ok = 0, bad = 0;
#ifdef CLIENT_PATCHER_MUTANT_ALL
    // mutation twin: ignores the configuration and applies every client patch
    std::set<std::string> names;
    for (const auto& d : PatchTable()) if (d.module == "client.dll" && !d.replacement.empty()) names.insert(d.name);
    for (const auto& name : names) {
#else
    for (const auto& name : s_.enabled) {
#endif
        const PatchDef* d = FindPatch(name);
        if (!d) { Emit("nvmp.patch", "{\"name\":\"" + Esc(name) + "\",\"status\":\"unknown-patch\"}"); ++bad; continue; }
        uint32_t rva = 0;
        PatchStatus s = DoApply(*d, &rva);
        bool good = s == PatchStatus::Applied || s == PatchStatus::AlreadyApplied;
        good ? ++ok : ++bad;
        Emit("nvmp.patch", "{\"name\":\"" + Esc(name) + "\",\"status\":\"" + ToString(s) + "\",\"rva\":\"" + Hex(rva) + "\",\"ok\":" +
                               (good ? "true" : "false") + "}");
    }
    Emit("nvmp.patches.done", "{\"applied\":" + std::to_string(ok) + ",\"failed\":" + std::to_string(bad) + "}");
}

void ClientPatcher::OnFrame() {
    ++frame_;
    if (phase_ != Phase::Waiting) return;
    if (!s_.autoApply) { phase_ = Phase::Done; return; }
    const bool poll = (frame_ % (uint64_t)s_.pollEveryFrames) == 1 || notified_.exchange(false);
    if (!poll) return;
    if (!FindClient(mod_)) {
        if (frame_ > (uint64_t)s_.giveUpAfterFrames) {
            phase_ = Phase::GaveUp;
            Emit("nvmp.client.absent", "{\"frames\":" + std::to_string(frame_) + "}");
        }
        return;
    }
    if (!announced_) {
        announced_ = true;
        Emit("nvmp.client.detected", "{\"base\":\"" + Hex((uint32_t)(uintptr_t)mod_.base) + "\",\"size\":" + std::to_string(mod_.size) +
                                         ",\"frame\":" + std::to_string(frame_) + "}");
    }
    ApplyAllEnabled();
    phase_ = Phase::Done;
}

PatchStatus ClientPatcher::Apply(const std::string& name) {
    const PatchDef* d = FindPatch(name);
    if (!d || d->module != "client.dll" || d->replacement.empty()) return PatchStatus::BadDefinition;
    if (!mod_.base && !FindClient(mod_)) return PatchStatus::ModuleNotFound;
    uint32_t rva = 0;
    PatchStatus s = DoApply(*d, &rva);
    Emit("nvmp.patch", "{\"name\":\"" + Esc(name) + "\",\"status\":\"" + ToString(s) + "\",\"rva\":\"" + Hex(rva) + "\",\"manual\":true}");
    return s;
}

PatchStatus ClientPatcher::Revert(const std::string& name) {
    auto it = recs_.find(name);
    if (it == recs_.end()) return PatchStatus::NotApplied;
    PatchStatus s = RevertPatch(it->second.state, writer_);
    if (s == PatchStatus::Reverted) it->second.status = s;
    Emit("nvmp.patch", "{\"name\":\"" + Esc(name) + "\",\"status\":\"" + ToString(s) + "\",\"manual\":true}");
    return s;
}

int ClientPatcher::RevertAll() {
    int bad = 0;
    for (auto& [name, rec] : recs_) {
        if (!rec.state.applied) continue;
        PatchStatus s = RevertPatch(rec.state, writer_);
        if (s == PatchStatus::Reverted) rec.status = s;
        else ++bad;
    }
    return bad;
}

std::string ClientPatcher::StatusJson() const {
    const char* ph = phase_ == Phase::Waiting ? "waiting" : phase_ == Phase::Done ? "done" : "gave-up";
    std::string j = std::string("{\"phase\":\"") + ph + "\",\"frames\":" + std::to_string(frame_) + ",\"module\":";
    if (mod_.base) j += "{\"base\":\"" + Hex((uint32_t)(uintptr_t)mod_.base) + "\",\"size\":" + std::to_string(mod_.size) + "}";
    else j += "null";
    j += ",\"patches\":[";
    bool first = true;
    for (const auto& d : PatchTable()) {
        if (d.module != "client.dll") continue;
        if (!first) j += ",";
        first = false;
        auto it = recs_.find(d.name);
        const bool en = s_.enabled.count(d.name) != 0;
        j += "{\"name\":\"" + d.name + "\",\"enabled\":" + (en ? "true" : "false");
        if (it != recs_.end()) {
            j += std::string(",\"status\":\"") + ToString(it->second.status) + "\",\"rva\":\"" + Hex(it->second.rva) + "\",\"applied\":" +
                 (it->second.state.applied ? "true" : "false");
        }
        j += "}";
    }
    return j + "]}";
}

}  // namespace nvmp::client
